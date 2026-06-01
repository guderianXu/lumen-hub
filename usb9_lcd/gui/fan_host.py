from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import ctypes

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from usb9_lcd.monitoring.models import FanTelemetry, SystemTelemetry
from usb9_lcd.monitoring.service import collect_system_telemetry
from usb9_lcd.monitoring.windows import WindowsFanChannel, collect_windows_fan_channels, set_windows_fan_control_percent


@dataclass(frozen=True)
class GenericFanChannel:
    name: str
    rpm: int | None = None
    percent: float | None = None
    pwm_path: Path | None = None
    pwm_enable_path: Path | None = None
    windows_control_id: str = ""
    control_available: bool = False
    control_reason: str = ""


@dataclass(frozen=True)
class GenericFanSnapshot:
    platform_name: str
    telemetry: SystemTelemetry
    channels: list[GenericFanChannel]
    control_available: bool
    control_reason: str


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


def _scan_linux_hwmon_channels() -> list[GenericFanChannel]:
    channels: list[GenericFanChannel] = []
    hwmon_root = Path("/sys/class/hwmon")
    if not hwmon_root.exists():
        return channels

    for hwmon in sorted(hwmon_root.glob("hwmon*")):
        for fan_input in sorted(hwmon.glob("fan*_input")):
            match = re.fullmatch(r"fan(\d+)_input", fan_input.name)
            if not match:
                continue
            index = match.group(1)
            rpm = _read_int(fan_input)
            pwm_path = hwmon / f"pwm{index}"
            pwm_enable_path = hwmon / f"pwm{index}_enable"
            raw_pwm = _read_int(pwm_path) if pwm_path.exists() else None
            percent = None if raw_pwm is None else max(0.0, min(100.0, raw_pwm * 100.0 / 255.0))
            writable = pwm_path.exists() and os.access(pwm_path, os.W_OK)
            if writable:
                reason = "PWM writable"
            elif pwm_path.exists():
                reason = "PWM exists but is not writable; run with permissions or configure udev/system service"
            else:
                reason = "No matching PWM output exposed for this fan input"
            channels.append(
                GenericFanChannel(
                    name=_fan_label(hwmon, index),
                    rpm=rpm,
                    percent=percent,
                    pwm_path=pwm_path if pwm_path.exists() else None,
                    pwm_enable_path=pwm_enable_path if pwm_enable_path.exists() else None,
                    control_available=writable,
                    control_reason=reason,
                )
            )
    return channels


def _channels_from_telemetry(fans: list[FanTelemetry]) -> list[GenericFanChannel]:
    return [
        GenericFanChannel(
            name=fan.name,
            rpm=fan.rpm,
            percent=fan.percent,
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
            reason = "No hwmon fan sensors detected"
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
            control_available=channel.control_available,
            control_reason=channel.control_reason,
        )
        for channel in collect_windows_fan_channels()
    ]


def _format_channel(channel: GenericFanChannel) -> str:
    parts = [channel.name]
    if channel.rpm is not None:
        parts.append(f"{channel.rpm} RPM")
    if channel.percent is not None:
        parts.append(f"{channel.percent:.0f}%")
    parts.append("control: yes" if channel.control_available else f"control: no ({channel.control_reason})")
    return " / ".join(parts)


def _snapshot_details(snapshot: GenericFanSnapshot) -> str:
    lines = [f"Platform: {snapshot.platform_name}", f"Control capability: {snapshot.control_reason}"]
    cpu = snapshot.telemetry.cpu
    if cpu.package_temperature_c is not None or cpu.utilization_percent is not None:
        cpu_parts: list[str] = []
        if cpu.package_temperature_c is not None:
            cpu_parts.append(f"{cpu.package_temperature_c:.0f}C")
        if cpu.utilization_percent is not None:
            cpu_parts.append(f"{cpu.utilization_percent:.0f}% load")
        lines.append(f"CPU: {' / '.join(cpu_parts)}")
    else:
        lines.append(f"CPU: {cpu.error or 'unavailable'}")
    lines.append("")
    if snapshot.channels:
        lines.append("Fan channels:")
        lines.extend(f"  - {_format_channel(channel)}" for channel in snapshot.channels)
    else:
        lines.append("Fan channels: none detected")
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
        snapshot_collector: Callable[[], GenericFanSnapshot] = collect_generic_fan_snapshot,
        **_ignored: object,
    ) -> None:
        super().__init__()
        self._snapshot_collector = snapshot_collector
        self._snapshot: GenericFanSnapshot | None = None
        self.monitor = None
        self._loaded = False
        self._scan_active = False
        self._auto_grant_pwm_permissions = auto_grant_pwm_permissions
        self._auto_enable_pwm_control = auto_enable_pwm_control
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
        self.control_value = QLabel("控制能力未知")
        grid.addWidget(self._metric_card("CPU", self.cpu_value), 0, 0)
        grid.addWidget(self._metric_card("风扇传感器", self.sensor_value), 0, 1)
        grid.addWidget(self._metric_card("控制能力", self.control_value), 1, 0, 1, 2)
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

        actions = QHBoxLayout()
        self.scan_button = QPushButton("扫描/刷新")
        self.load_button = self.scan_button
        self.scan_button.clicked.connect(lambda: self.reload_fan_control(interactive_driver_probe=True))
        self.help_button = QPushButton("为什么不能控制？")
        self.help_button.clicked.connect(self._explain_control_limit)
        self.admin_button = QPushButton("管理员重启")
        self.admin_button.clicked.connect(self._restart_as_admin)
        self.admin_button.setVisible(sys.platform.startswith("win") and not _is_windows_admin())
        actions.addWidget(self.scan_button)
        actions.addWidget(self.help_button)
        actions.addWidget(self.admin_button)
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

    def reload_fan_control(self, *args, **kwargs) -> None:  # noqa: ARG002, ANN002, ANN003
        if self._scan_active:
            self._set_status("普通风扇正在扫描中...")
            return
        self._scan_active = True
        self.scan_button.setEnabled(False)
        self._set_status("正在扫描普通风扇...")

        def worker() -> None:
            try:
                snapshot = self._snapshot_collector()
                self.scan_finished.emit(snapshot, None)
            except Exception as exc:  # noqa: BLE001
                self.scan_finished.emit(None, exc)

        threading.Thread(target=worker, name="fan-scan-worker", daemon=True).start()

    def _on_scan_finished(self, snapshot: object, error: object) -> None:
        self._scan_active = False
        self.scan_button.setEnabled(True)
        if isinstance(error, Exception):
            self.details.setPlainText(f"普通风扇扫描失败:\n{error}")
            self._set_status("普通风扇扫描失败")
            return
        if isinstance(snapshot, GenericFanSnapshot):
            self._snapshot = snapshot
            self._loaded = True
            self._render_snapshot()
            self._set_status(self.home_status_text())

    def release(self) -> None:
        return None

    def _render_snapshot(self) -> None:
        if self._snapshot is None:
            return
        snapshot = self._snapshot
        cpu_parts: list[str] = []
        if snapshot.telemetry.cpu.package_temperature_c is not None:
            cpu_parts.append(f"{snapshot.telemetry.cpu.package_temperature_c:.0f}C")
        if snapshot.telemetry.cpu.utilization_percent is not None:
            cpu_parts.append(f"{snapshot.telemetry.cpu.utilization_percent:.0f}% load")
        self.cpu_value.setText(" / ".join(cpu_parts) if cpu_parts else snapshot.telemetry.cpu.error or "CPU --")

        if snapshot.channels:
            self.sensor_value.setText("\n".join(_format_channel(channel) for channel in snapshot.channels[:3]))
        else:
            self.sensor_value.setText("未发现风扇传感器")

        self.control_value.setText(snapshot.control_reason)
        self.apply_button.setEnabled(snapshot.control_available)
        self.details.setPlainText(_snapshot_details(snapshot))

    def _apply_pwm(self) -> None:
        if self._snapshot is None or not self._snapshot.control_available:
            self._explain_control_limit()
            return
        percent_value = self.pwm_slider.value()
        written: list[str] = []
        errors: list[str] = []
        for channel in self._snapshot.channels:
            if not channel.control_available or channel.pwm_path is None:
                if not channel.control_available or not channel.windows_control_id:
                    continue
            try:
                if channel.windows_control_id:
                    set_windows_fan_control_percent(channel.windows_control_id, percent_value)
                elif channel.pwm_path is not None:
                    self._write_linux_pwm(channel, percent_value)
                written.append(channel.name)
            except OSError as exc:
                errors.append(f"{channel.name}: {exc}")
            except Exception as exc:  # noqa: BLE001 - hardware write errors should stay in the UI.
                errors.append(f"{channel.name}: {exc}")
        if errors:
            self.details.append("\n写入失败:\n" + "\n".join(errors))
            self._set_status("普通风扇 PWM 写入失败")
        else:
            self.details.append(f"\n已写入 PWM {self.pwm_slider.value()}%: " + ", ".join(written))
            self._set_status(f"已应用 PWM {self.pwm_slider.value()}%")
        self.reload_fan_control(interactive_driver_probe=False)

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
        self.details.append(
            "\n控制限制说明:\n"
            f"{reason}\n"
            "Windows 普通主板风扇没有统一系统 API，通常只能通过 LibreHardwareMonitor/OpenHardwareMonitor 读取。"
            "Linux 下如果 hwmon 暴露 pwm* 且当前用户有写权限，本页会自动启用应用按钮。"
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
        card.setObjectName("MetricCard")
        layout = QVBoxLayout(card)
        title_label = QLabel(title)
        title_label.setStyleSheet("color: #a3a7aa;")
        value.setObjectName("MetricValue")
        value.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(value)
        return card
