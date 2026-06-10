from __future__ import annotations

from datetime import datetime
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
import ctypes

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from usb9_lcd.gui.fan_curve import FanCurveEditor
from usb9_lcd.gui.fan_curve_model import (
    FAN_CURVE_CUSTOM_PRESET,
    FAN_CURVE_PRESETS,
    FAN_CURVE_SENSOR_SOURCES,
    apply_fan_curve_policy,
    fan_curve_preset_points,
    normalize_fan_curve_preset,
    normalize_fan_curve_sensor_source,
    sanitize_fan_curve_points,
)
from usb9_lcd.gui.settings import GuiSettings, save_settings
from usb9_lcd.monitoring.models import FanTelemetry, SystemTelemetry
from usb9_lcd.monitoring.service import collect_system_telemetry
from usb9_lcd.monitoring.windows import WindowsFanChannel, collect_windows_fan_channels, set_windows_fan_control_percent
from usb9_lcd.service.permissions import (
    PermissionRequest,
    build_pwm_write_request,
    grant_permission_request,
)


@dataclass(frozen=True)
class GenericFanChannel:
    name: str
    rpm: int | None = None
    percent: float | None = None
    pwm_path: Path | None = None
    pwm_enable_path: Path | None = None
    windows_control_id: str = ""
    role: str = ""
    control_available: bool = False
    control_reason: str = ""


@dataclass(frozen=True)
class GenericFanSnapshot:
    platform_name: str
    telemetry: SystemTelemetry
    channels: list[GenericFanChannel]
    control_available: bool
    control_reason: str
    diagnostic_details: str = ""


@dataclass(frozen=True)
class LinuxFanProbeDiagnostics:
    summary: str
    details: str


_LINUX_FAN_MODULE_CANDIDATES = ("nct6683", "nct6775", "asus_ec_sensors", "it87", "asus_wmi")
_LINUX_FAN_PROBE_MODULES = (
    ("nct6683", "force=1"),
    ("nct6775", ""),
    ("asus_ec_sensors", ""),
    ("it87", ""),
)
_FAN_ROLE_LABELS = {
    "cpu": "CPU 风扇",
    "pump": "水泵/AIO",
    "case": "机箱风扇",
    "gpu": "GPU 风扇",
    "unknown": "未标定",
}
_FAN_ROLE_CHOICES = (
    ("自动识别", ""),
    ("CPU 风扇", "cpu"),
    ("水泵/AIO", "pump"),
    ("机箱风扇", "case"),
    ("GPU 风扇", "gpu"),
    ("未标定", "unknown"),
)


def _platform_label() -> str:
    if sys.platform.startswith("win"):
        return "Windows"
    if sys.platform.startswith("linux"):
        return "Linux"
    if sys.platform == "darwin":
        return "macOS"
    return sys.platform


def _is_windows_admin() -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8", errors="replace").strip())
    except (OSError, ValueError):
        return None


def _fan_label(hwmon: Path, index: str) -> str:
    label_path = hwmon / f"fan{index}_label"
    label = label_path.read_text(encoding="utf-8", errors="replace").strip() if label_path.exists() else ""
    if label:
        return label
    name_path = hwmon / "name"
    chip = name_path.read_text(encoding="utf-8", errors="replace").strip() if name_path.exists() else hwmon.name
    return f"{chip} fan{index}"


def _normalize_fan_role(role: object) -> str:
    value = str(role or "").strip().lower()
    aliases = {
        "cpu": "cpu",
        "cpu fan": "cpu",
        "pump": "pump",
        "aio": "pump",
        "aio pump": "pump",
        "water": "pump",
        "case": "case",
        "chassis": "case",
        "sys": "case",
        "system": "case",
        "gpu": "gpu",
        "nvidia": "gpu",
        "radeon": "gpu",
        "unknown": "unknown",
    }
    return aliases.get(value, value if value in _FAN_ROLE_LABELS else "")


def _infer_fan_role(name: str) -> str:
    text = name.lower()
    if any(marker in text for marker in ("gpu", "nvidia", "geforce", "radeon", "arc")):
        return "gpu"
    if any(marker in text for marker in ("pump", "aio", "w_pump", "water")):
        return "pump"
    if any(marker in text for marker in ("cpu", "processor")):
        return "cpu"
    if any(marker in text for marker in ("chassis", "cha_fan", "case", "sys_fan", "system fan")):
        return "case"
    return ""


def _mainboard_fan_index(name: str) -> str:
    match = re.search(r"\bfan\s*#?(\d+)\b", name, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _fan_channel_identity(channel: GenericFanChannel) -> str:
    if channel.windows_control_id:
        return channel.windows_control_id
    if channel.pwm_path is not None:
        return str(channel.pwm_path)
    return channel.name


def _fan_display_name(channel: GenericFanChannel) -> str:
    role = _normalize_fan_role(channel.role) or _infer_fan_role(channel.name)
    if role and role != "unknown":
        return f"{_FAN_ROLE_LABELS[role]} · {channel.name}"
    fan_index = _mainboard_fan_index(channel.name)
    if fan_index:
        return f"主板 Fan{fan_index}（未标定） · {channel.name}"
    if role == "unknown":
        return f"未标定 · {channel.name}"
    return channel.name


def _linux_pwm_channel(hwmon: Path, index: str, rpm: int | None = None) -> GenericFanChannel:
    pwm_path = hwmon / f"pwm{index}"
    pwm_enable_path = hwmon / f"pwm{index}_enable"
    label = _fan_label(hwmon, index)
    raw_pwm = _read_int(pwm_path) if pwm_path.exists() else None
    percent = None if raw_pwm is None else max(0.0, min(100.0, raw_pwm * 100.0 / 255.0))
    pwm_writable = pwm_path.exists() and os.access(pwm_path, os.W_OK)
    pwm_enable_writable = not pwm_enable_path.exists() or os.access(pwm_enable_path, os.W_OK)
    writable = pwm_writable and pwm_enable_writable
    if writable:
        reason = "PWM writable"
    elif pwm_path.exists() and not pwm_writable:
        reason = "PWM exists but is not writable; run with permissions or configure udev/system service"
    elif pwm_enable_path.exists() and not pwm_enable_writable:
        reason = "PWM enable exists but is not writable; run with permissions or configure udev/system service"
    else:
        reason = "No matching PWM output exposed for this fan input"
    return GenericFanChannel(
        name=label,
        rpm=rpm,
        percent=percent,
        pwm_path=pwm_path if pwm_path.exists() else None,
        pwm_enable_path=pwm_enable_path if pwm_enable_path.exists() else None,
        role=_infer_fan_role(label),
        control_available=writable,
        control_reason=reason,
    )


def _scan_linux_hwmon_channels(hwmon_root: Path = Path("/sys/class/hwmon")) -> list[GenericFanChannel]:
    channels: list[GenericFanChannel] = []
    if not hwmon_root.exists():
        return channels

    for hwmon in sorted(hwmon_root.glob("hwmon*")):
        seen_indices: set[str] = set()
        for fan_input in sorted(hwmon.glob("fan*_input")):
            match = re.fullmatch(r"fan(\d+)_input", fan_input.name)
            if not match:
                continue
            index = match.group(1)
            seen_indices.add(index)
            rpm = _read_int(fan_input)
            channels.append(_linux_pwm_channel(hwmon, index, rpm=rpm))
        for pwm_path in sorted(hwmon.glob("pwm[0-9]*")):
            match = re.fullmatch(r"pwm(\d+)", pwm_path.name)
            if not match:
                continue
            index = match.group(1)
            if index in seen_indices:
                continue
            channels.append(_linux_pwm_channel(hwmon, index))
    return channels


def _read_linux_hwmon_names(hwmon_root: Path = Path("/sys/class/hwmon")) -> list[str]:
    if not hwmon_root.exists():
        return []
    names: list[str] = []
    for hwmon in sorted(hwmon_root.glob("hwmon*")):
        name_path = hwmon / "name"
        name = name_path.read_text(encoding="utf-8", errors="replace").strip() if name_path.exists() else ""
        names.append(name or hwmon.name)
    return names


def _linux_hwmon_matching_files(hwmon_root: Path, pattern: str, *, fullmatch: str | None = None) -> list[str]:
    if not hwmon_root.exists():
        return []
    files: list[str] = []
    for hwmon in sorted(hwmon_root.glob("hwmon*")):
        for candidate in sorted(hwmon.glob(pattern)):
            if fullmatch and not re.fullmatch(fullmatch, candidate.name):
                continue
            files.append(str(candidate))
    return files


def _system_has_linux_fan_pwm_files(hwmon_root: Path = Path("/sys/class/hwmon")) -> bool:
    return bool(
        _linux_hwmon_matching_files(hwmon_root, "fan*_input", fullmatch=r"fan\d+_input")
        or _linux_hwmon_matching_files(hwmon_root, "pwm[0-9]*", fullmatch=r"pwm\d+")
    )


def _available_linux_modules(candidates: tuple[str, ...] = _LINUX_FAN_MODULE_CANDIDATES) -> list[str]:
    if shutil.which("modinfo") is None:
        return []
    available: list[str] = []
    for module in candidates:
        result = subprocess.run(
            ["modinfo", module],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            available.append(module)
    return available


def _loaded_linux_modules(candidates: tuple[str, ...] = _LINUX_FAN_MODULE_CANDIDATES) -> list[str]:
    try:
        loaded = {line.split()[0] for line in Path("/proc/modules").read_text(encoding="utf-8").splitlines() if line}
    except OSError:
        return []
    return [module for module in candidates if module in loaded]


def _join_or_none(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def _fan_hwmon_probe_shell() -> str:
    lines = [
        "set +e",
        "echo 'lumen-hub: probing Linux fan hwmon drivers'",
    ]
    for module, args in _LINUX_FAN_PROBE_MODULES:
        modprobe_args = f"{module} {args}".strip()
        lines.extend(
            [
                f"if modinfo {module} >/dev/null 2>&1; then",
                f"  modprobe {modprobe_args} >/dev/null 2>&1",
                f"  echo '{modprobe_args}='$?",
                "else",
                f"  echo '{module}=missing'",
                "fi",
            ]
        )
    lines.append("udevadm settle >/dev/null 2>&1 || true")
    return "\n".join(lines)


def _snapshot_with_probe_message(snapshot: GenericFanSnapshot, probe_message: str) -> GenericFanSnapshot:
    if not probe_message:
        return snapshot
    diagnostic_details = snapshot.diagnostic_details
    probe_details = f"启动驱动加载:\n{probe_message}"
    if diagnostic_details:
        diagnostic_details = f"{diagnostic_details}\n\n{probe_details}"
    else:
        diagnostic_details = probe_details
    return replace(snapshot, diagnostic_details=diagnostic_details)


def _snapshot_with_permission_message(snapshot: GenericFanSnapshot, permission_message: str) -> GenericFanSnapshot:
    if not permission_message:
        return snapshot
    diagnostic_details = snapshot.diagnostic_details
    permission_details = f"PWM 权限授权:\n{permission_message}"
    if diagnostic_details:
        diagnostic_details = f"{diagnostic_details}\n\n{permission_details}"
    else:
        diagnostic_details = permission_details
    return replace(snapshot, diagnostic_details=diagnostic_details)


def _linux_fan_probe_diagnostics(hwmon_root: Path = Path("/sys/class/hwmon")) -> LinuxFanProbeDiagnostics:
    hwmon_names = _read_linux_hwmon_names(hwmon_root)
    fan_files = _linux_hwmon_matching_files(hwmon_root, "fan*_input", fullmatch=r"fan\d+_input")
    pwm_files = _linux_hwmon_matching_files(hwmon_root, "pwm[0-9]*", fullmatch=r"pwm\d+")
    available_modules = _available_linux_modules()
    loaded_modules = _loaded_linux_modules()
    sensors_command = shutil.which("sensors")

    if fan_files or pwm_files:
        summary = "Linux 找到了 fan/pwm 文件，但没有匹配出可用普通风扇通道"
    elif hwmon_names:
        summary = "Linux 当前没有暴露 fan*_input 或 pwm* 风扇节点"
    else:
        summary = "Linux 当前没有暴露 /sys/class/hwmon 传感器目录"

    lines = [
        "Linux 风扇诊断:",
        f"- hwmon 芯片: {_join_or_none(hwmon_names)}",
        f"- fan*_input: {_join_or_none(fan_files[:12])}",
        f"- pwm*: {_join_or_none(pwm_files[:12])}",
        f"- 已加载候选驱动: {_join_or_none(loaded_modules)}",
        f"- 当前内核可用候选驱动: {_join_or_none(available_modules)}",
        f"- lm-sensors 命令: {'installed' if sensors_command else 'not installed'}",
        "",
        "建议验证步骤:",
        "1. 软件启动和点击“扫描/刷新”时会自动尝试加载本机已有的候选 hwmon 驱动。",
        "2. 如果自动加载后仍没有节点，再安装并识别传感器: sudo apt install lm-sensors && sudo sensors-detect",
        "3. ASUS / Nuvoton 主板也可手动逐个测试候选驱动，测试后回到本页点“扫描/刷新”。",
    ]
    if "nct6683" in available_modules:
        lines.append("   sudo modprobe nct6683 force=1")
    if "nct6775" in available_modules:
        lines.append("   sudo modprobe nct6775")
    if "asus_ec_sensors" in available_modules:
        lines.append("   sudo modprobe asus_ec_sensors")
    lines.extend(
        [
            "4. 如果出现新的 /sys/class/hwmon/.../fan*_input，GUI 会显示转速。",
            "5. 如果只有 fan*_input 没有可写 pwm*，GUI 只能读转速，不能调速。",
        ]
    )
    return LinuxFanProbeDiagnostics(summary=summary, details="\n".join(lines))


def _channels_from_telemetry(fans: list[FanTelemetry]) -> list[GenericFanChannel]:
    return [
        GenericFanChannel(
            name=fan.name,
            rpm=fan.rpm,
            percent=fan.percent,
            role=_infer_fan_role(fan.name),
            control_available=False,
            control_reason=fan.error or "Read-only sensor; no ordinary Windows fan-control backend is configured",
        )
        for fan in fans
    ]


def collect_generic_fan_snapshot(
    telemetry_collector: Callable[[], SystemTelemetry] = collect_system_telemetry,
) -> GenericFanSnapshot:
    telemetry = telemetry_collector()
    platform_name = _platform_label()
    if sys.platform.startswith("linux"):
        channels = _scan_linux_hwmon_channels()
        if not channels:
            channels = _channels_from_telemetry(telemetry.fans)
        writable_count = sum(1 for channel in channels if channel.control_available)
        if writable_count:
            reason = f"{writable_count} writable PWM channel(s) detected"
        elif channels:
            reason = "Fan sensors detected, but no writable PWM channel is available"
        else:
            diagnostics = _linux_fan_probe_diagnostics()
            reason = diagnostics.summary
            return GenericFanSnapshot(
                platform_name,
                telemetry,
                channels,
                False,
                reason,
                diagnostics.details,
            )
        return GenericFanSnapshot(platform_name, telemetry, channels, bool(writable_count), reason)

    if sys.platform.startswith("win"):
        channels = _channels_from_windows_backend()
        admin_note = (
            "Current GUI is running as administrator; if motherboard fans are still missing, this board is not exposed by the current Windows sensor backend"
            if _is_windows_admin()
            else "Current GUI is not running as administrator; motherboard fan sensors often require elevated access to the Super I/O or EC driver"
        )
        writable_count = sum(1 for channel in channels if channel.control_available)
        if writable_count:
            reason = f"{writable_count} writable motherboard/controller fan channel(s) detected"
        elif channels:
            reason = f"No writable motherboard/controller fan channel is available. {admin_note}."
        else:
            reason = f"No Windows fan sensors detected. {admin_note}."
        return GenericFanSnapshot(
            platform_name,
            telemetry,
            channels,
            bool(writable_count),
            reason,
        )

    channels = _channels_from_telemetry(telemetry.fans)
    return GenericFanSnapshot(platform_name, telemetry, channels, False, "Fan control is not implemented for this platform yet")


def _channels_from_windows_backend() -> list[GenericFanChannel]:
    return [
        GenericFanChannel(
            name=channel.name,
            rpm=channel.rpm,
            percent=channel.percent,
            windows_control_id=channel.control_id,
            role=_infer_fan_role(channel.name),
            control_available=channel.control_available,
            control_reason=channel.control_reason,
        )
        for channel in collect_windows_fan_channels()
    ]


def _format_channel(channel: GenericFanChannel) -> str:
    parts = [_fan_display_name(channel)]
    if channel.rpm is not None:
        parts.append(f"{channel.rpm} RPM")
    if channel.percent is not None:
        parts.append(f"{channel.percent:.0f}%")
    parts.append("control: yes" if channel.control_available else f"control: no ({channel.control_reason})")
    return " / ".join(parts)


def _cpu_temperature_label(snapshot: GenericFanSnapshot) -> str:
    cpu = snapshot.telemetry.cpu
    temp = cpu.package_temperature_c
    details: list[str] = []
    if cpu.utilization_percent is not None:
        details.append(f"负载 {cpu.utilization_percent:.0f}%")
    if cpu.power_w is not None:
        details.append(f"功耗 {cpu.power_w:.0f}W")
    if temp is None:
        if details:
            return "\n".join(["CPU --", " · ".join(details)])
        return cpu.error or "CPU --"
    if temp < 55:
        state = "凉爽"
    elif temp < 75:
        state = "正常"
    elif temp < 88:
        state = "偏热"
    else:
        state = "过热"
    parts = [f"{temp:.0f}°C", state, *details]
    return "\n".join([parts[0], " · ".join(parts[1:])])


def _cpu_temperature_color(snapshot: GenericFanSnapshot) -> str:
    temp = snapshot.telemetry.cpu.package_temperature_c
    if temp is None:
        return "#f2f3f0"
    if temp < 55:
        return "#70e000"
    if temp < 75:
        return "#ffd166"
    if temp < 88:
        return "#ff9f1c"
    return "#ff4d4d"


def _format_rpm(value: int | None) -> str:
    if value is None:
        return "-- RPM"
    if value <= 0:
        return "0 RPM"
    return f"{value} RPM"


def _format_pwm_percent(value: float | None) -> str:
    if value is None:
        return "PWM --"
    return f"PWM {value:.0f}%"


def _fan_live_summary(snapshot: GenericFanSnapshot) -> str:
    if not snapshot.channels:
        return "未发现风扇传感器"
    rpm_values = [channel.rpm for channel in snapshot.channels if channel.rpm is not None]
    active = [rpm for rpm in rpm_values if rpm > 0]
    writable = sum(1 for channel in snapshot.channels if channel.control_available)
    parts = [f"{len(snapshot.channels)} 通道", f"{len(active)} 有转速"]
    if active:
        parts.append(f"最高 {max(active)} RPM")
        parts.append(f"平均 {round(sum(active) / len(active))} RPM")
    parts.append(f"{writable} 可控" if writable else "只读")
    return " · ".join(parts)


def _fan_live_lines(snapshot: GenericFanSnapshot, *, limit: int = 12) -> list[str]:
    lines = [_fan_live_summary(snapshot)]
    for index, channel in enumerate(snapshot.channels[:limit], start=1):
        state = "可控" if channel.control_available else "只读"
        lines.append(
            f"{index:02d}. {_fan_display_name(channel)}: "
            f"{_format_rpm(channel.rpm)} · {_format_pwm_percent(channel.percent)} · {state}"
        )
    remaining = len(snapshot.channels) - limit
    if remaining > 0:
        lines.append(f"... 还有 {remaining} 个通道")
    return lines


def _snapshot_update_time(snapshot: GenericFanSnapshot) -> str:
    captured_at = snapshot.telemetry.captured_at
    if isinstance(captured_at, datetime):
        return captured_at.strftime("%H:%M:%S")
    return "--:--:--"


def _snapshot_details(snapshot: GenericFanSnapshot) -> str:
    lines = [f"Platform: {snapshot.platform_name}", f"Control capability: {snapshot.control_reason}"]
    cpu = snapshot.telemetry.cpu
    if cpu.package_temperature_c is not None or cpu.utilization_percent is not None or cpu.power_w is not None:
        cpu_parts: list[str] = []
        if cpu.package_temperature_c is not None:
            cpu_parts.append(f"{cpu.package_temperature_c:.0f}C")
        if cpu.utilization_percent is not None:
            cpu_parts.append(f"{cpu.utilization_percent:.0f}% load")
        if cpu.power_w is not None:
            cpu_parts.append(f"{cpu.power_w:.0f}W")
        lines.append(f"CPU: {' / '.join(cpu_parts)}")
    else:
        lines.append(f"CPU: {cpu.error or 'unavailable'}")
    lines.append("")
    if snapshot.channels:
        lines.append("Fan channels:")
        lines.extend(f"  - {_format_channel(channel)}" for channel in snapshot.channels)
    else:
        lines.append("Fan channels: none detected")
    if snapshot.diagnostic_details:
        lines.append("")
        lines.append(snapshot.diagnostic_details)
    return "\n".join(lines)


class FanControlHostPage(QWidget):
    status_changed = Signal(str)
    scan_finished = Signal(object, object)

    def __init__(
        self,
        *,
        auto_load: bool = True,
        auto_grant_pwm_permissions: bool = False,
        auto_enable_pwm_control: bool = False,
        auto_probe_hwmon_drivers: bool = False,
        settings: GuiSettings | None = None,
        settings_saver: Callable[[GuiSettings], None] = save_settings,
        snapshot_collector: Callable[[], GenericFanSnapshot] = collect_generic_fan_snapshot,
        permission_helper: object | None = None,
        **_ignored: object,
    ) -> None:
        super().__init__()
        self.settings = settings or GuiSettings()
        self._settings_saver = settings_saver
        self._snapshot_collector = snapshot_collector
        self.permission_helper = permission_helper
        self._snapshot: GenericFanSnapshot | None = None
        self.monitor = None
        self._loaded = False
        self._scan_active = False
        self._curve_applying = False
        self._last_curve_temperature_c: float | None = None
        self._last_curve_percent: int | None = None
        self._updating_curve_preset = False
        self._auto_grant_pwm_permissions = auto_grant_pwm_permissions
        self._auto_enable_pwm_control = auto_enable_pwm_control
        self._auto_probe_hwmon_drivers = auto_probe_hwmon_drivers
        self._driver_probe_attempted = False
        self._driver_probe_message = ""
        self._permission_grant_attempted = False
        self._permission_grant_message = ""
        self._curve_timer = QTimer(self)
        self._curve_timer.timeout.connect(self._curve_tick)
        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(self._live_refresh_tick)
        self._apply_curve_after_scan = False
        self.scan_finished.connect(self._on_scan_finished)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(14)

        header = QLabel("普通风扇")
        header.setObjectName("PageTitle")
        subtitle = QLabel("读取主板/系统风扇传感器；仅在发现安全可写 PWM 后才允许手动控制。")
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(header)
        layout.addWidget(subtitle)

        self.status_label = QLabel("未扫描")
        self.status_label.setObjectName("SectionLabel")
        layout.addWidget(self.status_label)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.cpu_value = QLabel("CPU --")
        self.sensor_value = QLabel("风扇传感器未扫描")
        self.live_value = QLabel("实时刷新未启动")
        self.control_value = QLabel("控制能力未知")
        grid.addWidget(self._metric_card("CPU", self.cpu_value), 0, 0)
        grid.addWidget(self._metric_card("风扇实时转速", self.sensor_value), 0, 1)
        grid.addWidget(self._metric_card("实时状态", self.live_value), 1, 0)
        grid.addWidget(self._metric_card("控制能力", self.control_value), 1, 1)
        layout.addLayout(grid)

        control_card = QFrame()
        control_card.setObjectName("MetricCard")
        control_layout = QVBoxLayout(control_card)
        control_title = QLabel("手动 PWM")
        control_title.setObjectName("SectionLabel")
        self.pwm_label = QLabel("目标 PWM: 40%")
        self.pwm_slider = QSlider(Qt.Orientation.Horizontal)
        self.pwm_slider.setRange(20, 100)
        self.pwm_slider.setValue(40)
        self.pwm_slider.valueChanged.connect(lambda value: self.pwm_label.setText(f"目标 PWM: {value}%"))
        self.enable_manual = QCheckBox("写入前切到手动 PWM 模式")
        self.enable_manual.setChecked(True)
        self.enable_manual.setToolTip("大多数 Linux hwmon 风扇需要 pwm_enable=1，直接写 pwm 数值才会生效。")
        self.apply_button = QPushButton("应用到普通风扇")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply_pwm)
        control_layout.addWidget(control_title)
        control_layout.addWidget(self.pwm_label)
        control_layout.addWidget(self.pwm_slider)
        control_layout.addWidget(self.enable_manual)
        control_layout.addWidget(self.apply_button)
        layout.addWidget(control_card)

        role_card = QFrame()
        role_card.setObjectName("MetricCard")
        role_layout = QGridLayout(role_card)
        role_title = QLabel("通道角色标定")
        role_title.setObjectName("SectionLabel")
        self.role_channel_combo = QComboBox()
        self.role_channel_combo.currentIndexChanged.connect(self._selected_role_channel_changed)
        self.role_combo = QComboBox()
        for label, role in _FAN_ROLE_CHOICES:
            self.role_combo.addItem(label, role)
        self.role_save_button = QPushButton("保存角色")
        self.role_save_button.clicked.connect(self._save_selected_channel_role)
        role_layout.addWidget(role_title, 0, 0, 1, 4)
        role_layout.addWidget(QLabel("通道"), 1, 0)
        role_layout.addWidget(self.role_channel_combo, 1, 1)
        role_layout.addWidget(QLabel("角色"), 1, 2)
        role_layout.addWidget(self.role_combo, 1, 3)
        role_layout.addWidget(self.role_save_button, 2, 3)
        layout.addWidget(role_card)

        curve_card = QFrame()
        curve_card.setObjectName("MetricCard")
        curve_layout = QVBoxLayout(curve_card)
        curve_title = QLabel("风扇曲线")
        curve_title.setObjectName("SectionLabel")
        curve_hint = QLabel("CPU 温度到 PWM 输出的曲线；拖动控制点，双击空白处增加点，右键点位删除。")
        curve_hint.setObjectName("FieldHint")
        curve_hint.setWordWrap(True)
        self.curve_editor = FanCurveEditor()
        self.curve_editor.set_points(self.settings.host_fan.curve_points)
        self.curve_editor.curve_changed.connect(self._curve_changed)
        self.curve_summary = QLabel("")
        self.curve_summary.setObjectName("FieldHint")
        self.curve_summary.setWordWrap(True)
        curve_options = QHBoxLayout()
        self.curve_preset_combo = QComboBox()
        for preset, (label, _points) in FAN_CURVE_PRESETS.items():
            self.curve_preset_combo.addItem(label, preset)
        self.curve_preset_combo.addItem("自定义", FAN_CURVE_CUSTOM_PRESET)
        self._set_curve_preset_combo(self.settings.host_fan.curve_preset)
        self.curve_preset_combo.currentIndexChanged.connect(self._curve_preset_changed)
        self.curve_enable = QCheckBox("启用曲线控制")
        self.curve_enable.setChecked(self.settings.host_fan.curve_enabled)
        self.curve_enable.toggled.connect(self._curve_enabled_changed)
        self.curve_interval = QSpinBox()
        self.curve_interval.setRange(1, 60)
        self.curve_interval.setSuffix(" s")
        self.curve_interval.setValue(self.settings.host_fan.curve_interval_seconds)
        self.curve_interval.valueChanged.connect(self._curve_interval_changed)
        self.curve_sensor_combo = QComboBox()
        for source, label in FAN_CURVE_SENSOR_SOURCES.items():
            self.curve_sensor_combo.addItem(label, source)
        self._set_curve_sensor_combo(self.settings.host_fan.curve_sensor_source)
        self.curve_sensor_combo.currentIndexChanged.connect(self._curve_policy_changed)
        self.curve_hysteresis = QSpinBox()
        self.curve_hysteresis.setRange(0, 20)
        self.curve_hysteresis.setSuffix(" °C")
        self.curve_hysteresis.setValue(self.settings.host_fan.curve_hysteresis_c)
        self.curve_hysteresis.valueChanged.connect(self._curve_policy_changed)
        self.curve_minimum = QSpinBox()
        self.curve_minimum.setRange(0, 100)
        self.curve_minimum.setSuffix("%")
        self.curve_minimum.setValue(self.settings.host_fan.curve_minimum_percent)
        self.curve_minimum.valueChanged.connect(self._curve_policy_changed)
        self.curve_fallback = QSpinBox()
        self.curve_fallback.setRange(0, 100)
        self.curve_fallback.setSuffix("%")
        self.curve_fallback.setValue(self.settings.host_fan.curve_fallback_percent)
        self.curve_fallback.valueChanged.connect(self._curve_policy_changed)
        self.curve_apply_button = QPushButton("按曲线写入一次")
        self.curve_apply_button.setEnabled(False)
        self.curve_apply_button.clicked.connect(self._apply_curve_now)
        self.curve_save_button = QPushButton("保存曲线")
        self.curve_save_button.clicked.connect(self._save_curve_now)
        curve_options.addWidget(QLabel("预设"))
        curve_options.addWidget(self.curve_preset_combo)
        curve_options.addWidget(self.curve_enable)
        curve_options.addWidget(QLabel("刷新间隔"))
        curve_options.addWidget(self.curve_interval)
        curve_options.addStretch(1)
        curve_options.addWidget(self.curve_save_button)
        curve_options.addWidget(self.curve_apply_button)
        curve_layout.addWidget(curve_title)
        curve_layout.addWidget(curve_hint)
        curve_layout.addWidget(self.curve_editor)
        curve_layout.addWidget(self.curve_summary)
        curve_policy_options = QHBoxLayout()
        curve_policy_options.addWidget(QLabel("传感器"))
        curve_policy_options.addWidget(self.curve_sensor_combo)
        curve_policy_options.addWidget(QLabel("迟滞"))
        curve_policy_options.addWidget(self.curve_hysteresis)
        curve_policy_options.addWidget(QLabel("最低"))
        curve_policy_options.addWidget(self.curve_minimum)
        curve_policy_options.addWidget(QLabel("丢失回退"))
        curve_policy_options.addWidget(self.curve_fallback)
        curve_policy_options.addStretch(1)
        curve_layout.addLayout(curve_policy_options)
        curve_layout.addLayout(curve_options)
        layout.addWidget(curve_card)
        self._update_curve_summary()

        actions = QHBoxLayout()
        self.scan_button = QPushButton("扫描/刷新")
        self.load_button = self.scan_button
        self.scan_button.setToolTip("Linux 下会先尝试加载主板 hwmon 风扇驱动，再刷新传感器。")
        self.scan_button.clicked.connect(lambda: self.reload_fan_control(interactive_driver_probe=True))
        self.permission_button = QPushButton("授权 PWM 权限")
        self.permission_button.setToolTip("给当前用户授权写入已发现的 /sys/class/hwmon/.../pwm* 文件。")
        self.permission_button.setEnabled(False)
        self.permission_button.setVisible(sys.platform.startswith("linux"))
        self.permission_button.clicked.connect(self.request_pwm_permission_grant)
        self.help_button = QPushButton("为什么不能控制？")
        self.help_button.clicked.connect(self._explain_control_limit)
        self.admin_button = QPushButton("管理员重启")
        self.admin_button.clicked.connect(self._restart_as_admin)
        self.admin_button.setVisible(sys.platform.startswith("win") and not _is_windows_admin())
        self.live_refresh = QCheckBox("实时刷新")
        self.live_refresh.setChecked(True)
        self.live_refresh.setToolTip("只刷新 CPU 温度和风扇 RPM/PWM 显示，不写入 PWM。")
        self.live_refresh.toggled.connect(self._live_refresh_changed)
        self.live_interval = QSpinBox()
        self.live_interval.setRange(1, 30)
        self.live_interval.setSuffix(" s")
        self.live_interval.setValue(2)
        self.live_interval.setToolTip("普通风扇传感器刷新间隔。")
        self.live_interval.valueChanged.connect(self._live_interval_changed)
        actions.addWidget(self.scan_button)
        actions.addWidget(self.permission_button)
        actions.addWidget(self.help_button)
        actions.addWidget(self.admin_button)
        actions.addWidget(self.live_refresh)
        actions.addWidget(QLabel("显示间隔"))
        actions.addWidget(self.live_interval)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(220)
        layout.addWidget(self.details, 1)

        if auto_load:
            self.load_fan_control(interactive_driver_probe=False)
        else:
            self._set_status("普通风扇页已就绪")
        self._sync_curve_controls()
        self._sync_live_timer()

    def home_status_text(self) -> str:
        if self._snapshot is None:
            return "未扫描"
        if not self._snapshot.channels:
            return "未发现普通风扇"
        first = self._snapshot.channels[0]
        rpm = "--" if first.rpm is None else f"{first.rpm}RPM"
        writable = sum(1 for channel in self._snapshot.channels if channel.control_available)
        if writable:
            return f"{len(self._snapshot.channels)} 个传感器 / {writable} 可控"
        return f"{len(self._snapshot.channels)} 个传感器 / {rpm}"

    def load_fan_control(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.reload_fan_control(*args, **kwargs)

    def reload_fan_control(
        self,
        *args,
        interactive_driver_probe: bool = False,
        apply_curve_after_scan: bool = False,
        **kwargs,
    ) -> None:  # noqa: ARG002, ANN002, ANN003
        if self._scan_active:
            if apply_curve_after_scan:
                self._apply_curve_after_scan = True
            self._set_status("普通风扇正在扫描中...")
            return
        self._apply_curve_after_scan = bool(apply_curve_after_scan)
        self._scan_active = True
        self.scan_button.setEnabled(False)
        self._set_status("正在扫描普通风扇...")

        def worker() -> None:
            try:
                snapshot = self._collect_snapshot_after_optional_probe(interactive_driver_probe=interactive_driver_probe)
                self.scan_finished.emit(snapshot, None)
            except Exception as exc:  # noqa: BLE001
                self.scan_finished.emit(None, exc)

        threading.Thread(target=worker, name="fan-scan-worker", daemon=True).start()

    def request_hwmon_driver_probe(self) -> None:
        self.reload_fan_control(interactive_driver_probe=True)

    def _collect_snapshot_after_optional_probe(self, *, interactive_driver_probe: bool) -> GenericFanSnapshot:
        probe_message = ""
        if self._should_probe_linux_hwmon_drivers(interactive_driver_probe=interactive_driver_probe):
            self._driver_probe_attempted = True
            _, probe_message = self._load_fan_hwmon_drivers(interactive=interactive_driver_probe)
            self._driver_probe_message = probe_message
        snapshot = self._snapshot_collector()
        permission_message = ""
        if self._should_grant_pwm_permissions(snapshot, interactive_driver_probe=interactive_driver_probe):
            self._permission_grant_attempted = True
            _, permission_message = self._grant_pwm_permissions(snapshot, interactive=interactive_driver_probe)
            self._permission_grant_message = permission_message
            snapshot = self._snapshot_collector()
        if probe_message:
            snapshot = _snapshot_with_probe_message(snapshot, probe_message)
        if permission_message:
            snapshot = _snapshot_with_permission_message(snapshot, permission_message)
        return snapshot

    def _should_probe_linux_hwmon_drivers(self, *, interactive_driver_probe: bool) -> bool:
        if not sys.platform.startswith("linux"):
            return False
        if os.environ.get("LUMEN_HUB_SKIP_FAN_DRIVER_PROBE") == "1":
            return False
        if self._system_has_fan_pwm_files():
            return False
        return interactive_driver_probe or (self._auto_probe_hwmon_drivers and not self._driver_probe_attempted)

    def _system_has_fan_pwm_files(self) -> bool:
        return _system_has_linux_fan_pwm_files()

    def _should_grant_pwm_permissions(self, snapshot: GenericFanSnapshot, *, interactive_driver_probe: bool) -> bool:
        if not sys.platform.startswith("linux"):
            return False
        if os.environ.get("LUMEN_HUB_SKIP_FAN_PERMISSION_GRANT") == "1":
            return False
        if snapshot.control_available:
            return False
        if not self._pwm_permission_paths(snapshot):
            return False
        return interactive_driver_probe or (self._auto_grant_pwm_permissions and not self._permission_grant_attempted)

    def _pwm_permission_paths(self, snapshot: GenericFanSnapshot | None = None) -> list[Path]:
        target = snapshot or self._snapshot
        if target is None:
            return []
        paths: list[Path] = []
        seen: set[str] = set()
        for channel in target.channels:
            for path in (channel.pwm_path, channel.pwm_enable_path):
                if path is None or not path.exists():
                    continue
                key = str(path)
                if key in seen:
                    continue
                if not os.access(path, os.W_OK):
                    paths.append(path)
                    seen.add(key)
        return paths

    def _pwm_permission_shell(self, paths: list[Path]) -> str:
        return build_pwm_write_request(paths).shell_command

    def _grant_pwm_permissions(self, snapshot: GenericFanSnapshot, *, interactive: bool = False) -> tuple[bool, str]:
        paths = self._pwm_permission_paths(snapshot)
        if not paths:
            return False, "没有找到需要授权的 pwm* 或 pwm*_enable 文件。"
        result = self._run_permission_request(
            build_pwm_write_request(paths),
            timeout=30,
            interactive=interactive,
        )
        if result.ok:
            return True, result.message or f"已授权 {len(paths)} 个 PWM 文件。"
        return False, result.message

    def _run_permission_request(
        self,
        request: PermissionRequest,
        *,
        timeout: int,
        interactive: bool,
    ):
        return grant_permission_request(
            request,
            helper=self.permission_helper,
            fallback_runner=self._run_privileged_shell_compat,
            timeout=timeout,
            interactive=interactive,
        )

    def request_pwm_permission_grant(self) -> None:
        self.reload_fan_control(interactive_driver_probe=True)

    def _load_fan_hwmon_drivers(self, *, interactive: bool = False) -> tuple[bool, str]:
        if not sys.platform.startswith("linux"):
            return False, "当前平台不是 Linux，已跳过 hwmon 驱动加载。"
        commands = _fan_hwmon_probe_shell()
        ok, output = self._run_privileged_shell_compat(
            commands,
            timeout=30,
            interactive=interactive,
            action_label="加载主板风扇驱动",
        )
        if self._system_has_fan_pwm_files():
            message = output or "驱动加载命令已执行。"
            return True, f"{message}\n驱动加载后已发现 fan/pwm 节点。"
        if ok:
            message = output or "驱动加载命令已执行。"
            return False, f"{message}\n驱动加载执行完毕，但仍未暴露 fan/pwm 节点。"
        return False, output

    def _run_privileged_shell_compat(
        self,
        commands: str,
        *,
        timeout: int,
        interactive: bool,
        action_label: str,
    ) -> tuple[bool, str]:
        try:
            return self._run_privileged_shell(
                commands,
                timeout=timeout,
                interactive=interactive,
                action_label=action_label,
            )
        except TypeError as exc:
            if "action_label" not in str(exc):
                raise
            return self._run_privileged_shell(commands, timeout=timeout, interactive=interactive)

    def _run_privileged_shell(
        self,
        commands: str,
        *,
        timeout: int,
        interactive: bool = False,
        action_label: str = "加载主板风扇驱动",
    ) -> tuple[bool, str]:
        shell = shutil.which("sh") or "/bin/sh"
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            command = [shell, "-c", commands]
            label = "root"
        elif interactive:
            pkexec = shutil.which("pkexec")
            if not pkexec:
                return False, f"{action_label}失败：系统没有 pkexec，无法弹出授权窗口。"
            command = [pkexec, shell, "-c", commands]
            label = "pkexec"
        else:
            sudo = shutil.which("sudo")
            if not sudo:
                return False, f"{action_label}已跳过：系统没有 sudo，无法非交互执行。"
            command = [sudo, "-n", shell, "-c", commands]
            label = "sudo -n"
        try:
            result = subprocess.run(
                command,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, f"{label} {action_label}超时。"
        except OSError as exc:
            return False, f"{label} {action_label}失败: {exc}"

        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
        if result.returncode == 0:
            return True, output or "ok"
        if not interactive and label == "sudo -n":
            return (
                False,
                f"{action_label}已跳过：当前会话没有免密 sudo；点击“扫描/刷新”可弹出系统授权。",
            )
        if interactive and label == "pkexec":
            return False, output or f"系统授权被取消或{action_label}失败。"
        return False, output or f"{label} {action_label}失败，退出码 {result.returncode}。"

    def _on_scan_finished(self, snapshot: object, error: object) -> None:
        self._scan_active = False
        self.scan_button.setEnabled(True)
        if isinstance(error, Exception):
            self._apply_curve_after_scan = False
            self.details.setPlainText(f"普通风扇扫描失败:\n{error}")
            self._set_status("普通风扇扫描失败")
            self._sync_live_timer()
            return
        if isinstance(snapshot, GenericFanSnapshot):
            self._snapshot = snapshot
            self._loaded = True
            self._render_snapshot()
            self._set_status(self.home_status_text())

    def release(self) -> None:
        self._live_timer.stop()
        self._curve_timer.stop()
        return None

    def _render_snapshot(self) -> None:
        if self._snapshot is None:
            return
        snapshot = self._snapshot_with_saved_roles(self._snapshot)
        self._snapshot = snapshot
        self.cpu_value.setText(_cpu_temperature_label(snapshot))
        self.cpu_value.setStyleSheet(f"color: {_cpu_temperature_color(snapshot)};")
        self.sensor_value.setText("\n".join(_fan_live_lines(snapshot)))

        self.control_value.setText(snapshot.control_reason)
        self.apply_button.setEnabled(snapshot.control_available)
        self._populate_role_controls(snapshot)
        self._sync_permission_controls()
        self._sync_curve_controls()
        self._update_live_status(snapshot)
        self._sync_live_timer()
        self.details.setPlainText(_snapshot_details(snapshot))
        should_apply_curve = self._apply_curve_after_scan
        self._apply_curve_after_scan = False
        if should_apply_curve and self.curve_enable.isChecked() and snapshot.control_available and not self._curve_applying:
            self._apply_curve_to_snapshot(snapshot, source="曲线自动")

    def _snapshot_with_saved_roles(self, snapshot: GenericFanSnapshot) -> GenericFanSnapshot:
        roles = self.settings.host_fan.channel_roles
        channels: list[GenericFanChannel] = []
        changed = False
        for channel in snapshot.channels:
            role = _normalize_fan_role(roles.get(_fan_channel_identity(channel), "")) or _infer_fan_role(channel.name)
            if role and role != channel.role:
                channels.append(replace(channel, role=role))
                changed = True
            elif not role and channel.role:
                channels.append(replace(channel, role=""))
                changed = True
            else:
                channels.append(channel)
        return replace(snapshot, channels=channels) if changed else snapshot

    def _populate_role_controls(self, snapshot: GenericFanSnapshot) -> None:
        blocked = self.role_channel_combo.blockSignals(True)
        try:
            current_identity = str(self.role_channel_combo.currentData() or "")
            self.role_channel_combo.clear()
            for channel in snapshot.channels:
                identity = _fan_channel_identity(channel)
                self.role_channel_combo.addItem(_fan_display_name(channel), identity)
            if current_identity:
                index = self.role_channel_combo.findData(current_identity)
                if index >= 0:
                    self.role_channel_combo.setCurrentIndex(index)
        finally:
            self.role_channel_combo.blockSignals(blocked)
        has_channels = bool(snapshot.channels)
        self.role_channel_combo.setEnabled(has_channels)
        self.role_combo.setEnabled(has_channels)
        self.role_save_button.setEnabled(has_channels)
        self._selected_role_channel_changed()

    def _selected_role_channel_changed(self) -> None:
        identity = str(self.role_channel_combo.currentData() or "")
        role = _normalize_fan_role(self.settings.host_fan.channel_roles.get(identity, ""))
        index = self.role_combo.findData(role)
        if index < 0:
            index = self.role_combo.findData("")
        if index >= 0:
            self.role_combo.setCurrentIndex(index)

    def _save_selected_channel_role(self) -> None:
        identity = str(self.role_channel_combo.currentData() or "")
        if not identity:
            return
        role = _normalize_fan_role(self.role_combo.currentData())
        if role:
            self.settings.host_fan.channel_roles[identity] = role
        else:
            self.settings.host_fan.channel_roles.pop(identity, None)
        if self._save_host_fan_settings():
            self._set_status("风扇通道角色已保存")
        if self._snapshot is not None:
            self._snapshot = self._snapshot_with_saved_roles(self._snapshot)
            self.sensor_value.setText("\n".join(_fan_live_lines(self._snapshot)))
            self.details.setPlainText(_snapshot_details(self._snapshot))
            self._populate_role_controls(self._snapshot)

    def _apply_pwm(self) -> None:
        if self._snapshot is None or not self._snapshot.control_available:
            self._explain_control_limit()
            return
        percent_value = self.pwm_slider.value()
        written, errors = self._write_pwm_percent_to_channels(percent_value)
        if errors:
            self.details.append("\n写入失败:\n" + "\n".join(errors))
            self._set_status("普通风扇 PWM 写入失败")
        else:
            self.details.append(f"\n已写入 PWM {self.pwm_slider.value()}%: " + ", ".join(written))
            self._set_status(f"已应用 PWM {self.pwm_slider.value()}%")
        self.reload_fan_control(interactive_driver_probe=False)

    def _write_pwm_percent_to_channels(self, percent_value: int) -> tuple[list[str], list[str]]:
        if self._snapshot is None:
            return [], ["普通风扇尚未扫描"]
        written: list[str] = []
        errors: list[str] = []
        for channel in self._snapshot.channels:
            if not channel.control_available:
                continue
            try:
                if channel.windows_control_id:
                    set_windows_fan_control_percent(channel.windows_control_id, percent_value)
                elif channel.pwm_path is not None:
                    self._write_linux_pwm(channel, percent_value)
                else:
                    continue
                written.append(channel.name)
            except OSError as exc:
                errors.append(f"{channel.name}: {exc}")
            except Exception as exc:  # noqa: BLE001 - hardware write errors should stay in the UI.
                errors.append(f"{channel.name}: {exc}")
        if not written and not errors:
            errors.append("没有可写 PWM 通道")
        return written, errors

    def _curve_changed(self, points: object | None = None) -> None:
        curve_points = sanitize_fan_curve_points(points if points is not None else self.curve_editor.points())
        self._reset_curve_policy_memory()
        self.settings.host_fan.curve_points = curve_points
        self.settings.host_fan.curve_preset = FAN_CURVE_CUSTOM_PRESET
        self._set_curve_preset_combo(FAN_CURVE_CUSTOM_PRESET)
        self.curve_editor.set_points(curve_points)
        saved = self._save_host_fan_settings()
        self._update_curve_summary()
        if not saved:
            return
        if self.curve_enable.isChecked():
            self._set_status("风扇曲线已更新，下一轮刷新时生效")
        else:
            self._set_status("风扇曲线已保存")

    def _set_curve_preset_combo(self, preset: object) -> None:
        if not hasattr(self, "curve_preset_combo"):
            return
        key = normalize_fan_curve_preset(preset)
        index = self.curve_preset_combo.findData(key)
        if index < 0:
            index = self.curve_preset_combo.findData("normal")
        self._updating_curve_preset = True
        try:
            self.curve_preset_combo.setCurrentIndex(max(0, index))
        finally:
            self._updating_curve_preset = False

    def _set_curve_sensor_combo(self, sensor_source: object) -> None:
        if not hasattr(self, "curve_sensor_combo"):
            return
        source = normalize_fan_curve_sensor_source(sensor_source)
        index = self.curve_sensor_combo.findData(source)
        self.curve_sensor_combo.setCurrentIndex(max(0, index))

    def _curve_preset_changed(self) -> None:
        if self._updating_curve_preset:
            return
        preset = normalize_fan_curve_preset(self.curve_preset_combo.currentData())
        self.settings.host_fan.curve_preset = preset
        if preset != FAN_CURVE_CUSTOM_PRESET:
            self._reset_curve_policy_memory()
            points = fan_curve_preset_points(preset)
            self.settings.host_fan.curve_points = points
            self.curve_editor.set_points(points)
        else:
            self.settings.host_fan.curve_points = sanitize_fan_curve_points(self.curve_editor.points())
        self._save_host_fan_settings()
        self._update_curve_summary()
        if self.curve_enable.isChecked() and self._snapshot is not None and self._snapshot.control_available:
            self._apply_curve_to_snapshot(self._snapshot, source="曲线预设")
        else:
            self._set_status(f"已选择风扇曲线预设：{self.curve_preset_combo.currentText()}")

    def _curve_interval_changed(self, value: int) -> None:
        self.settings.host_fan.curve_interval_seconds = max(1, min(60, int(value)))
        self._save_host_fan_settings()
        self._sync_curve_timer()

    def _curve_policy_changed(self) -> None:
        if not hasattr(self, "curve_sensor_combo"):
            return
        self._reset_curve_policy_memory()
        self.settings.host_fan.curve_sensor_source = normalize_fan_curve_sensor_source(
            self.curve_sensor_combo.currentData()
        )
        self.settings.host_fan.curve_hysteresis_c = max(0, min(20, int(self.curve_hysteresis.value())))
        self.settings.host_fan.curve_minimum_percent = max(0, min(100, int(self.curve_minimum.value())))
        self.settings.host_fan.curve_fallback_percent = max(0, min(100, int(self.curve_fallback.value())))
        self._save_host_fan_settings()
        self._update_curve_summary()
        self._set_status("风扇曲线策略已保存")

    def _reset_curve_policy_memory(self) -> None:
        self._last_curve_temperature_c = None
        self._last_curve_percent = None

    def _curve_enabled_changed(self, enabled: bool) -> None:
        self.settings.host_fan.curve_enabled = bool(enabled)
        self._save_host_fan_settings()
        self._sync_curve_timer()
        if enabled:
            if self._snapshot is not None and self._snapshot.control_available:
                self._apply_curve_to_snapshot(self._snapshot, source="曲线启用")
            else:
                self._set_status("风扇曲线已启用，等待可写 PWM 通道")
        else:
            self._set_status("风扇曲线控制已暂停")

    def _save_curve_now(self) -> None:
        curve_points = sanitize_fan_curve_points(self.curve_editor.points())
        preset = normalize_fan_curve_preset(self.curve_preset_combo.currentData())
        if preset == FAN_CURVE_CUSTOM_PRESET or curve_points != fan_curve_preset_points(preset):
            preset = FAN_CURVE_CUSTOM_PRESET
        self.settings.host_fan.curve_points = curve_points
        self.settings.host_fan.curve_preset = preset
        self._set_curve_preset_combo(preset)
        self.curve_editor.set_points(curve_points)
        self._update_curve_summary()
        if self._save_host_fan_settings():
            self._set_status("风扇曲线已保存")

    def _save_host_fan_settings(self) -> bool:
        try:
            self._settings_saver(self.settings)
        except OSError as exc:
            self.details.append(f"\n风扇曲线设置保存失败: {exc}")
            self._set_status("风扇曲线设置保存失败")
            return False
        return True

    def _update_curve_summary(self) -> None:
        points = sanitize_fan_curve_points(self.curve_editor.points())
        summary = "，".join(f"{temp}°C→{percent}%" for temp, percent in points)
        preset = self.curve_preset_combo.currentText() if hasattr(self, "curve_preset_combo") else "当前"
        sensor = self.curve_sensor_combo.currentText() if hasattr(self, "curve_sensor_combo") else "CPU"
        hysteresis = self.curve_hysteresis.value() if hasattr(self, "curve_hysteresis") else 0
        minimum = self.curve_minimum.value() if hasattr(self, "curve_minimum") else 0
        fallback = self.curve_fallback.value() if hasattr(self, "curve_fallback") else 100
        self.curve_summary.setText(
            f"{preset}曲线：{summary}\n策略：传感器 {sensor}，迟滞 {hysteresis}°C，最低 {minimum}%，丢失回退 {fallback}%"
        )

    def _sync_curve_controls(self) -> None:
        has_control = bool(self._snapshot and self._snapshot.control_available)
        self.curve_apply_button.setEnabled(has_control)
        self._sync_curve_timer()

    def _sync_live_timer(self) -> None:
        if not hasattr(self, "live_refresh"):
            return
        interval_ms = max(1, int(self.live_interval.value())) * 1000
        self._live_timer.setInterval(interval_ms)
        should_run = bool(self.live_refresh.isChecked() and self._snapshot is not None)
        if should_run and not self._live_timer.isActive():
            self._live_timer.start()
        elif not should_run and self._live_timer.isActive():
            self._live_timer.stop()

    def _live_refresh_changed(self, enabled: bool) -> None:
        self._sync_live_timer()
        if self._snapshot is not None:
            self._update_live_status(self._snapshot)
        self._set_status("普通风扇实时刷新已开启" if enabled else "普通风扇实时刷新已暂停")

    def _live_interval_changed(self, _value: int) -> None:
        self._sync_live_timer()
        if self._snapshot is not None:
            self._update_live_status(self._snapshot)

    def _live_refresh_tick(self) -> None:
        if self._scan_active:
            return
        self.reload_fan_control(interactive_driver_probe=False, apply_curve_after_scan=False)

    def _update_live_status(self, snapshot: GenericFanSnapshot) -> None:
        if not hasattr(self, "live_value"):
            return
        mode = f"自动刷新 {self.live_interval.value()}s" if self.live_refresh.isChecked() else "自动刷新已暂停"
        self.live_value.setText(f"更新 {_snapshot_update_time(snapshot)}\n{mode} · 只读显示")

    def _sync_permission_controls(self) -> None:
        if not hasattr(self, "permission_button"):
            return
        has_permission_targets = bool(self._snapshot and self._pwm_permission_paths(self._snapshot))
        self.permission_button.setEnabled(has_permission_targets)

    def _sync_curve_timer(self) -> None:
        interval_ms = max(1, int(self.curve_interval.value())) * 1000
        self._curve_timer.setInterval(interval_ms)
        should_run = bool(
            self.curve_enable.isChecked()
            and self._snapshot is not None
            and self._snapshot.control_available
        )
        if should_run and not self._curve_timer.isActive():
            self._curve_timer.start()
        elif not should_run and self._curve_timer.isActive():
            self._curve_timer.stop()

    def _curve_tick(self) -> None:
        if self._scan_active:
            self._apply_curve_after_scan = True
            return
        self.reload_fan_control(interactive_driver_probe=False, apply_curve_after_scan=True)

    def _apply_curve_now(self) -> None:
        if self._snapshot is None or not self._snapshot.control_available:
            self._explain_control_limit()
            return
        self._apply_curve_to_snapshot(self._snapshot, source="曲线手动")

    def _apply_curve_to_snapshot(self, snapshot: GenericFanSnapshot, *, source: str) -> None:
        policy = apply_fan_curve_policy(
            self.curve_editor.points(),
            cpu_temperature_c=snapshot.telemetry.cpu.package_temperature_c,
            gpu_temperature_c=snapshot.telemetry.gpu.temperature_c,
            sensor_source=self.settings.host_fan.curve_sensor_source,
            previous_temperature_c=self._last_curve_temperature_c,
            previous_percent=self._last_curve_percent,
            hysteresis_c=self.settings.host_fan.curve_hysteresis_c,
            fallback_percent=self.settings.host_fan.curve_fallback_percent,
            minimum_percent=self.settings.host_fan.curve_minimum_percent,
        )
        percent = policy.percent
        previous_snapshot = self._snapshot
        self._snapshot = snapshot
        self._curve_applying = True
        try:
            written, errors = self._write_pwm_percent_to_channels(percent)
        finally:
            self._curve_applying = False
            self._snapshot = previous_snapshot if previous_snapshot is not None else snapshot
        temp_text = "--" if policy.temperature_c is None else f"{policy.temperature_c:.0f}C"
        reason_suffix = "（迟滞保持）" if policy.held_by_hysteresis else ""
        if policy.reason == "sensor-missing":
            reason_suffix = "（传感器丢失安全回退）"
        if errors:
            self.details.append(
                f"\n{source}写入失败：{policy.sensor_label} {temp_text} -> PWM {percent}%{reason_suffix}\n"
                + "\n".join(errors)
            )
            self._set_status("风扇曲线 PWM 写入失败")
            return
        self._last_curve_temperature_c = policy.temperature_c
        self._last_curve_percent = percent
        self.details.append(
            f"\n{source}：{policy.sensor_label} {temp_text} -> PWM {percent}%{reason_suffix}: " + ", ".join(written)
        )
        self._set_status(f"风扇曲线已写入 PWM {percent}%")

    def _write_linux_pwm(self, channel: GenericFanChannel, percent_value: int) -> None:
        if channel.pwm_path is None:
            raise ValueError("Linux PWM channel has no pwm path")
        if self.enable_manual.isChecked() and channel.pwm_enable_path is not None:
            current_mode = _read_int(channel.pwm_enable_path)
            if current_mode != 1:
                channel.pwm_enable_path.write_text("1\n", encoding="utf-8")
        raw_value = round(max(0, min(100, percent_value)) * 255 / 100)
        channel.pwm_path.write_text(f"{raw_value}\n", encoding="utf-8")

    def _explain_control_limit(self) -> None:
        reason = self._snapshot.control_reason if self._snapshot else "尚未扫描普通风扇"
        diagnostics = f"\n\n{self._snapshot.diagnostic_details}" if self._snapshot and self._snapshot.diagnostic_details else ""
        self.details.append(
            "\n控制限制说明:\n"
            f"{reason}\n"
            "Windows 普通主板风扇没有统一系统 API，通常只能通过 LibreHardwareMonitor/OpenHardwareMonitor 读取。"
            "Linux 下如果 hwmon 暴露 pwm* 但不可写，请点击“授权 PWM 权限”；授权后本页会自动重扫并启用手动 PWM/曲线控制。"
            f"{diagnostics}"
        )
        self._set_status("已显示普通风扇控制限制")

    def _restart_as_admin(self) -> None:
        if not sys.platform.startswith("win"):
            self._set_status("当前平台不需要 Windows 管理员重启")
            return
        try:
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    (
                        "$argsList=@('-m','usb9_lcd.gui.app'); "
                        f"Start-Process -FilePath '{sys.executable}' "
                        "-ArgumentList $argsList -WorkingDirectory (Get-Location) -Verb RunAs"
                    ),
                ],
                check=False,
                cwd=Path.cwd(),
            )
        except Exception as exc:  # noqa: BLE001
            self.details.append(f"\n管理员重启失败:\n{exc}")
            self._set_status("管理员重启失败")
            return
        self.details.append("\n已请求管理员权限重启。请在 UAC 弹窗中点“是”，然后关闭当前非管理员窗口。")
        self._set_status("已请求管理员重启")

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_changed.emit(text)

    def _metric_card(self, title: str, value: QLabel) -> QFrame:
        card = QFrame()
        card.setObjectName("FanStatusCard")
        card.setMinimumHeight(112)
        layout = QVBoxLayout(card)
        title_label = QLabel(title)
        title_label.setObjectName("FanCardMeta")
        value.setObjectName("FanSummaryValue")
        value.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(value)
        return card
