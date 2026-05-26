from __future__ import annotations

from collections import deque
import csv
from dataclasses import dataclass
import grp
import json
import math
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys
from types import ModuleType

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QApplication,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


DEFAULT_FAN_CONTROL_PATH = Path("/home/xjw/code/风扇控制")
FAN_HWMON_MODULE_CANDIDATES = (
    "nct6775",
    "nct6683",
    "it87",
    "asus_wmi_sensors",
    "asus-ec-sensors",
    "w83627ehf",
)
FORCED_FAN_HWMON_PROBES = (
    ("nct6683", ("force=1",), "新主板 NCT6683/NCT6687 兼容探测"),
)
CHART_COLORS = (
    "#6fb6a0",
    "#7aa2d6",
    "#d2a04c",
    "#d9847a",
    "#b89ad7",
    "#82b9b2",
)
FAN_CHANNEL_LABELS_PATH = Path.home() / ".config" / "usb9-lcd" / "fan-channels.json"
FANCONTROL_CONFIG_PATH = Path("/etc/fancontrol")
FAN_CHIP_NAMES = {
    "k10temp": "CPU",
    "zenpower": "CPU",
    "amdgpu": "集显",
    "nvme": "NVMe SSD",
    "spd5118": "内存",
    "mt7925_phy0": "WiFi",
    "mt7921_phy0": "WiFi",
    "iwlwifi_1": "WiFi",
    "asus": "主板",
    "nct6798": "主板",
    "nct6799": "主板",
    "nct6775": "主板",
    "nct6779": "主板",
    "nct6683": "主板",
    "it87": "主板",
    "it8686": "主板",
    "it8792": "主板",
}
FAN_ROLE_OPTIONS = (
    ("自动识别", ""),
    ("CPU 风扇", "CPU 风扇"),
    ("水泵/AIO", "水泵/AIO"),
    ("机箱风扇", "机箱风扇"),
    ("GPU 风扇", "GPU 风扇"),
    ("未识别通道", "未识别通道"),
)
FAN_HEADER_OPTIONS = (
    ("自动识别", ""),
    ("CPU_FAN", "CPU_FAN"),
    ("CPU_OPT", "CPU_OPT"),
    ("AIO_PUMP", "AIO_PUMP"),
    ("W_PUMP", "W_PUMP"),
    ("H_AMP", "H_AMP"),
    ("CHA_FAN", "CHA_FAN"),
    ("CHA_FAN1", "CHA_FAN1"),
    ("CHA_FAN2", "CHA_FAN2"),
    ("CHA_FAN3", "CHA_FAN3"),
    ("CHA_FAN4", "CHA_FAN4"),
    ("SYS_FAN", "SYS_FAN"),
    ("SYS_FAN1", "SYS_FAN1"),
    ("SYS_FAN2", "SYS_FAN2"),
    ("SYS_FAN3", "SYS_FAN3"),
    ("SYS_FAN4", "SYS_FAN4"),
    ("EXT_FAN", "EXT_FAN"),
)
FAN_HEADER_ROLE_HINTS = {
    "CPU_FAN": "CPU 风扇",
    "CPU_OPT": "CPU 风扇",
    "AIO_PUMP": "水泵/AIO",
    "W_PUMP": "水泵/AIO",
    "PUMP_FAN": "水泵/AIO",
    "H_AMP": "机箱风扇",
    "EXT_FAN": "机箱风扇",
}
ASUS_AM5_7PWM_HEADER_CANDIDATES = {
    "1": "CPU_FAN",
    "2": "CPU_OPT",
    "3": "CHA_FAN1",
    "4": "CHA_FAN2",
    "5": "CHA_FAN3",
    "6": "CHA_FAN4",
    "7": "AIO_PUMP",
}
FAN_BOARD_HEADER_CANDIDATE_PROFILES = (
    {
        "name": "ASUS B850-A 7 路接口映射",
        "vendors": ("ASUS", "ASUSTEK"),
        "board_keywords": ("B850-A",),
        "hwmon_names": ("nct6798", "nct6799"),
        "mapping": ASUS_AM5_7PWM_HEADER_CANDIDATES,
    },
)
FAN_ROLE_KEYWORDS = (
    ("GPU 风扇", ("gpu", "vga", "graphics", "显卡", "图形")),
    (
        "水泵/AIO",
        (
            "pump",
            "pumpin",
            "aio",
            "water",
            "w pump",
            "wpump",
            "aio pump",
            "cpu pump",
            "water pump",
            "waterpump",
            "水泵",
            "水冷",
            "冷头",
            "泵",
        ),
    ),
    (
        "CPU 风扇",
        (
            "cpu fan",
            "cpu fanin",
            "cpu opt",
            "cpufan",
            "cpu_fan",
            "cpu fan opt",
            "cpu",
            "processor fan",
            "processor",
            "处理器风扇",
            "处理器",
        ),
    ),
    (
        "机箱风扇",
        (
            "system fan",
            "system fanin",
            "sys fan",
            "sys fanin",
            "sysfan",
            "chassis",
            "cha fan",
            "cha fanin",
            "chafan",
            "cha_fan",
            "case fan",
            "casefan",
            "h amp",
            "h_amp",
            "ext fan",
            "ext_fan",
            "rear fan",
            "front fan",
            "top fan",
            "bottom fan",
            "radiator fan",
            "机箱",
            "系统风扇",
            "机箱扇",
            "系统扇",
            "冷排风扇",
            "冷排",
        ),
    ),
)
FAN_ROLE_COLORS = {
    "CPU 风扇": "#7aa2d6",
    "水泵/AIO": "#6fb6a0",
    "机箱风扇": "#d2a04c",
    "GPU 风扇": "#b89ad7",
    "未识别通道": "#969b9e",
    "未识别风扇": "#969b9e",
    "未知 PWM": "#969b9e",
    "未知风扇": "#969b9e",
}
FAN_ROLE_SPEED_LABELS = {
    "CPU 风扇": "CPU 转速",
    "水泵/AIO": "水泵转速",
    "机箱风扇": "风扇转速",
    "GPU 风扇": "GPU 风扇",
}
FAN_ROLE_CONTROL_LABELS = {
    "CPU 风扇": "CPU 输出",
    "水泵/AIO": "水泵输出",
    "机箱风扇": "机箱输出",
    "GPU 风扇": "GPU 输出",
}
FAN_ROLE_CONTROL_HINTS = {
    "CPU 风扇": "通常绑定 CPU Tctl/Package 温度，适合快速升降曲线",
    "水泵/AIO": "确认是水泵后再调速，通常保持较高固定转速或平缓曲线",
    "机箱风扇": "可绑定 CPU/GPU/主板温度，优先用较平滑曲线降低噪音",
    "GPU 风扇": "通常绑定 GPU 温度，只读百分比不会写入主板 PWM",
}
FAN_ROLE_SORT_ORDER = {
    "CPU 风扇": 0,
    "水泵/AIO": 1,
    "机箱风扇": 2,
    "GPU 风扇": 3,
    "未识别通道": 8,
    "未识别风扇": 8,
}
FAN_ROLE_OVERVIEW_ORDER = ("CPU 风扇", "水泵/AIO", "机箱风扇", "GPU 风扇", "未识别通道", "未识别风扇")
FAN_ROLE_QUICK_LABELS = (
    ("标记 CPU_FAN", "CPU 风扇", "CPU_FAN", "CPU 风扇"),
    ("标记水泵", "水泵/AIO", "AIO_PUMP", "水泵"),
    ("标记机箱风扇", "机箱风扇", "", "机箱风扇"),
)


def _profile_manager_config_dir(profile_manager) -> Path | None:  # noqa: ANN001
    config_dir = getattr(profile_manager, "config_dir", None)
    if config_dir is None:
        return None
    return Path(config_dir).expanduser()


def _canonical_fan_role(role: str) -> str:
    role = str(role or "").strip()
    if role == "未知 PWM":
        return "未识别通道"
    if role == "未知风扇":
        return "未识别风扇"
    return role


def _profile_manager_active_path(profile_manager) -> Path | None:  # noqa: ANN001
    active_path = getattr(profile_manager, "_active_path", None)
    if active_path is not None:
        return Path(active_path).expanduser()
    config_dir = _profile_manager_config_dir(profile_manager)
    return config_dir / ".active" if config_dir is not None else None


def _profile_manager_profile_path(profile_manager, name: str) -> Path | None:  # noqa: ANN001
    path_func = getattr(profile_manager, "_path", None)
    if callable(path_func):
        try:
            return Path(path_func(name)).expanduser()
        except Exception:
            return None
    config_dir = _profile_manager_config_dir(profile_manager)
    return config_dir / f"{name}.json" if config_dir is not None else None


def _default_profile_config_dir() -> Path:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        return Path(f"/home/{sudo_user}/.config/fan-control")
    return Path("~/.config/fan-control").expanduser()


def _profile_config_owner_ids() -> tuple[int, int]:
    uid_text = os.environ.get("SUDO_UID")
    gid_text = os.environ.get("SUDO_GID")
    try:
        uid = int(uid_text) if uid_text else os.getuid()
    except ValueError:
        uid = os.getuid()
    try:
        gid = int(gid_text) if gid_text else os.getgid()
    except ValueError:
        gid = os.getgid()
    return uid, gid


def _profile_config_repair_commands(config_dir: Path) -> str:
    uid, gid = _profile_config_owner_ids()
    quoted_dir = shlex.quote(str(config_dir.expanduser()))
    return (
        f"mkdir -p -- {quoted_dir} "
        f"&& chown -R {uid}:{gid} -- {quoted_dir} "
        f"&& find {quoted_dir} -type d -exec chmod u+rwx {{}} + "
        f"&& find {quoted_dir} -type f -exec chmod u+rw {{}} +"
    )


def _repair_profile_config_permissions(
    config_dir: Path | None,
    *,
    interactive: bool = True,
) -> tuple[bool, str]:
    if config_dir is None:
        return False, "无法确定策略配置目录"
    config_dir = config_dir.expanduser()
    if str(config_dir) in {"", "/"} or config_dir.parent == config_dir:
        return False, f"拒绝修复异常策略目录：{config_dir}"
    commands = _profile_config_repair_commands(config_dir)
    if os.geteuid() == 0:
        command = ["/bin/sh", "-c", commands]
    elif interactive:
        pkexec = shutil.which("pkexec")
        if not pkexec:
            return False, "未找到 pkexec，无法弹出系统授权窗口"
        command = [pkexec, "/bin/sh", "-c", commands]
    else:
        sudo = shutil.which("sudo")
        if not sudo:
            return False, "未找到可用的非交互式 sudo"
        command = [sudo, "-n", "/bin/sh", "-c", commands]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120 if interactive else 45,
        )
    except subprocess.TimeoutExpired:
        return False, "授权等待超时"
    except OSError as error:
        return False, str(error)
    if result.returncode == 0:
        return True, "ok"
    message = (result.stderr or result.stdout or "").strip()
    if not message:
        message = f"授权命令退出码 {result.returncode}"
    return False, message.splitlines()[-1]


def _prepare_profile_write_path(path: Path | None) -> None:
    if path is None:
        return
    path = path.expanduser()
    parent = path.parent
    if path.exists() and os.access(path, os.W_OK):
        return
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as error:
        raise PermissionError(f"无法创建策略配置目录：{parent}") from error
    if not os.access(parent, os.W_OK):
        raise PermissionError(f"策略配置目录不可写：{parent}")
    if not path.exists():
        return
    try:
        path.unlink()
    except PermissionError as error:
        raise PermissionError(f"策略配置文件不可写：{path}") from error


def _load_fan_channel_overrides(path: Path = FAN_CHANNEL_LABELS_PATH) -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    channels = payload.get("channels") if isinstance(payload, dict) else None
    if not isinstance(channels, dict):
        return {}
    overrides: dict[str, dict[str, str]] = {}
    for key, value in channels.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        role = _canonical_fan_role(str(value.get("role", "") or "").strip())
        alias = str(value.get("alias", "") or "").strip()
        header = str(value.get("header", "") or "").strip()
        if role or alias or header:
            overrides[key] = {"role": role, "alias": alias, "header": header}
    return overrides


def _save_fan_channel_overrides(
    overrides: dict[str, dict[str, str]],
    path: Path = FAN_CHANNEL_LABELS_PATH,
) -> None:
    cleaned = {
        key: {
            field: text
            for field, text in {
                "role": str(value.get("role", "") or "").strip(),
                "alias": str(value.get("alias", "") or "").strip(),
                "header": str(value.get("header", "") or "").strip(),
            }.items()
            if text
        }
        for key, value in sorted(overrides.items())
        if str(key).strip()
        and (
            str(value.get("role", "") or "").strip()
            or str(value.get("alias", "") or "").strip()
            or str(value.get("header", "") or "").strip()
        )
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"channels": cleaned}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _clean_fan_alias(text: str) -> str:
    alias = re.sub(r"\s+", " ", text.strip())
    return alias[:36]


@dataclass(frozen=True)
class ReadOnlyFanChannel:
    name: str
    pwm_path: str
    rpm_input: str | None = None
    min_pwm: int = 0
    max_pwm: int = 255
    read_only: bool = True
    rpm_unit: str = "RPM"


@dataclass(frozen=True)
class DisplayFanChannel:
    name: str
    pwm_path: str
    rpm_input: str | None = None
    min_pwm: int = 0
    max_pwm: int = 255
    read_only: bool = False
    rpm_unit: str = "RPM"
    original_name: str = ""
    type_label: str = "未识别通道"
    channel_label: str = ""
    header_label: str = ""
    header_basis: str = ""
    header_confirmed: bool = False
    hwmon_name: str = ""
    chip_label: str = ""
    hwmon_path: str = ""
    role_basis: str = ""
    sensor_label: str = ""
    sensor_basis: str = ""
    detail_text: str = ""
    evidence_text: str = ""
    identity_key: str = ""


@dataclass(frozen=True)
class FanControlConfig:
    path: Path
    devpath: dict[str, str]
    devname: dict[str, str]
    fctemps: dict[str, str]
    fcfans: dict[str, str]


@dataclass(frozen=True)
class SupplementalSensor:
    name: str
    unit: str
    source: str
    internal_id: str


@dataclass(frozen=True)
class NvidiaSmiSample:
    index: int
    name: str
    temperature_c: int | None
    fan_percent: int | None


def _parse_fancontrol_config(text: str, *, path: Path | None = None) -> FanControlConfig:
    joined = text.replace("\\\n", " ")
    fields: dict[str, dict[str, str]] = {
        "DEVPATH": {},
        "DEVNAME": {},
        "FCTEMPS": {},
        "FCFANS": {},
    }
    for raw_line in joined.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in fields:
            continue
        try:
            tokens = shlex.split(value, comments=True, posix=True)
        except ValueError:
            tokens = value.split()
        for token in tokens:
            if "=" not in token:
                continue
            left, right = token.split("=", 1)
            left = left.strip()
            right = right.strip()
            if left and right:
                fields[key][left] = right
    return FanControlConfig(
        path=path or FANCONTROL_CONFIG_PATH,
        devpath=fields["DEVPATH"],
        devname=fields["DEVNAME"],
        fctemps=fields["FCTEMPS"],
        fcfans=fields["FCFANS"],
    )


def _load_fancontrol_config(path: Path | None = None) -> FanControlConfig:
    config_path = path or FANCONTROL_CONFIG_PATH
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return FanControlConfig(path=config_path, devpath={}, devname={}, fctemps={}, fcfans={})
    return _parse_fancontrol_config(text, path=config_path)


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _friendly_fan_chip_name(chip_name: str) -> str:
    chip_name = str(chip_name or "").strip()
    return FAN_CHIP_NAMES.get(chip_name, chip_name)


def _fan_chip_display_name(chip_name: str) -> str:
    chip_name = str(chip_name or "").strip()
    if not chip_name:
        return ""
    friendly = _friendly_fan_chip_name(chip_name)
    return chip_name if friendly == chip_name else f"{friendly} {chip_name}"


def _fan_path_leaf(path_text: str) -> str:
    path_text = str(path_text or "").removeprefix("readonly:")
    if not path_text or path_text.startswith(("nvidia:", "nvidia-smi:")):
        return ""
    return Path(path_text).name


def _fan_identity_evidence_text(fan, *, compact: bool = False) -> str:  # noqa: ANN001
    role = str(getattr(fan, "type_label", "") or "")
    header = str(getattr(fan, "header_label", "") or "")
    channel = str(getattr(fan, "channel_label", "") or "")
    chip = str(getattr(fan, "chip_label", "") or _fan_chip_display_name(str(getattr(fan, "hwmon_name", "") or "")))
    sensor = str(getattr(fan, "sensor_label", "") or "")
    pwm_name = _fan_path_leaf(str(getattr(fan, "pwm_path", "") or ""))
    rpm_name = _fan_path_leaf(str(getattr(fan, "rpm_input", "") or ""))
    identity = _fan_identity_state_label(fan)

    parts: list[str] = []
    if role:
        parts.append(role)
    if header:
        parts.append(f"接口 {header}")
    elif role in {"未识别通道", "未识别风扇", "未知 PWM", "未知风扇", ""}:
        parts.append("接口未确认")
    if channel and channel != "Channel":
        parts.append(channel)
    file_parts = []
    if pwm_name:
        file_parts.append(f"PWM {pwm_name}")
    if rpm_name:
        file_parts.append(f"转速 {rpm_name}")
    if file_parts:
        parts.append(" / ".join(file_parts))
    if chip:
        parts.append(f"芯片 {chip}")
    if sensor:
        parts.append(f"温度源 {sensor}")
    if identity:
        parts.append(identity)

    if not compact:
        header_basis = str(getattr(fan, "header_basis", "") or "")
        role_basis = str(getattr(fan, "role_basis", "") or "")
        sensor_basis = str(getattr(fan, "sensor_basis", "") or "")
        if header_basis:
            parts.append(f"接口依据 {header_basis}")
        if role_basis:
            parts.append(f"角色依据 {role_basis}")
        if sensor_basis:
            parts.append(f"温度依据 {sensor_basis}")
    return " · ".join(dict.fromkeys(part for part in parts if part)) or "--"


def _normalized_keyword_text(parts: list[str]) -> tuple[str, str]:
    joined = " ".join(part for part in parts if part)
    normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", " ", joined.casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    compact = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", joined.casefold())
    return f" {normalized} ", compact


def _contains_keyword(normalized: str, compact: str, keywords: tuple[str, ...]) -> bool:
    return any(f" {keyword} " in normalized or keyword.replace(" ", "") in compact for keyword in keywords)


def _fan_type_label(parts: list[str], *, has_pwm: bool) -> str:
    return _fan_role_detection([("名称", part) for part in parts], has_pwm=has_pwm)[0]


def _fan_role_detection(parts: list[tuple[str, str]], *, has_pwm: bool) -> tuple[str, str]:
    for source, text in parts:
        header = _known_fan_header_label(text)
        header_role = _fan_header_role(header) if header else ""
        if header_role:
            return header_role, f"{source}: {text}"
    for role, keywords in FAN_ROLE_KEYWORDS:
        for source, text in parts:
            normalized, compact = _normalized_keyword_text([text])
            if _contains_keyword(normalized, compact, keywords):
                return role, f"{source}: {text}"
    fallback = "未识别通道" if has_pwm else "未识别风扇"
    return fallback, "未匹配到主板 fan*_label/pwm*_label，需要手动识别"


def _fan_role_color(role: str) -> str:
    return FAN_ROLE_COLORS.get(role, "#969b9e")


def _fan_speed_label(role: str, rpm_unit: str) -> str:
    if rpm_unit == "%":
        return "风扇占用"
    return FAN_ROLE_SPEED_LABELS.get(role, "转速")


def _fan_control_output_label(role: str, *, read_only: bool) -> str:
    if read_only:
        return "只读转速"
    return FAN_ROLE_CONTROL_LABELS.get(role, "PWM 输出")


def _fan_role_compact_label(role: str) -> str:
    return {
        "CPU 风扇": "CPU",
        "水泵/AIO": "水泵",
        "机箱风扇": "机箱",
        "GPU 风扇": "GPU",
        "未识别通道": "未识别",
        "未识别风扇": "未识别",
    }.get(role, role or "未知")


def _fan_role_control_hint(role: str, *, has_pwm: bool) -> str:
    if not has_pwm:
        return "当前通道只有转速监控，不能直接写入 PWM"
    return FAN_ROLE_CONTROL_HINTS.get(role, "未确认物理接口前建议先用识别脉冲或手动标签确认")


def _fan_identity_state_label(fan) -> str:  # noqa: ANN001
    role = str(getattr(fan, "type_label", "") or "")
    header = str(getattr(fan, "header_label", "") or "")
    basis = str(getattr(fan, "header_basis", "") or "")
    if basis.startswith("手动指定"):
        return "手动标定"
    if bool(getattr(fan, "header_confirmed", False)):
        return "hwmon 确认"
    if header.endswith("?"):
        return "主板候选"
    if role in {"未识别通道", "未识别风扇", "未知 PWM", "未知风扇", ""}:
        return "需标定"
    if bool(getattr(fan, "read_only", False)):
        return "只读监控"
    return "名称推断"


def _fan_control_title(fan) -> str:  # noqa: ANN001
    role = str(getattr(fan, "type_label", "") or "未识别通道")
    channel = str(getattr(fan, "channel_label", "") or "Channel")
    header = str(getattr(fan, "header_label", "") or "")
    name = str(getattr(fan, "name", "") or channel)
    if header:
        return f"{header} · {_fan_role_compact_label(role)}"
    if role not in {"未识别通道", "未识别风扇"}:
        return f"{_fan_role_compact_label(role)} · {channel}"
    return name


def _fan_header_sort_rank(header_label: str) -> int:
    header = _known_fan_header_label(str(header_label or "").rstrip("?"))
    explicit = {
        "CPU_FAN": 0,
        "CPU_OPT": 1,
        "AIO_PUMP": 2,
        "W_PUMP": 3,
    }
    if header in explicit:
        return explicit[header]
    if header in {"CHA_FAN", "SYS_FAN"}:
        return 10
    match = re.fullmatch(r"(CHA|SYS)_FAN(\d+)", header)
    if match:
        return 10 + int(match.group(2))
    if header.startswith("GPU"):
        return 40
    return 90


def _fan_sort_key(fan) -> tuple[int, int, int, str]:  # noqa: ANN001
    role = str(getattr(fan, "type_label", "") or "")
    channel = str(getattr(fan, "channel_label", "") or "")
    indexes = _channel_indexes_from_label(channel)
    channel_index = int(indexes[0]) if indexes else 999
    return (
        FAN_ROLE_SORT_ORDER.get(role, 9),
        _fan_header_sort_rank(str(getattr(fan, "header_label", "") or "")),
        channel_index,
        str(getattr(fan, "name", "") or ""),
    )


def _pwm_enable_mode_label(value: str) -> str:
    return {
        "0": "全速",
        "1": "手动 PWM",
        "2": "主板自动",
        "3": "热巡航",
        "4": "Smart Fan IV",
        "5": "Smart Fan IV",
    }.get(value.strip(), value.strip())


def _format_millidegree_text(value: str) -> str:
    try:
        raw = int(value.strip())
    except ValueError:
        return value.strip()
    if abs(raw) > 1000:
        return f"{raw / 1000:.1f}°C"
    return f"{raw}°C"


def _profile_fan_status_name(name: str) -> str:
    text = str(name or "").strip()
    match = re.fullmatch(r"主板 PWM(\d+)", text)
    if match:
        return f"PWM{match.group(1)}/FAN{match.group(1)}"
    return text.removeprefix("主板 ").strip() or text


def _channel_index_from_name(prefix: str, name: str) -> str:
    match = re.fullmatch(rf"{re.escape(prefix)}(\d+)(?:_input)?", name)
    return match.group(1) if match else ""


def _path_channel_label(pwm_path: str, rpm_input: str | None) -> str:
    pwm_index = _channel_index_from_name("pwm", Path(pwm_path).name) if pwm_path else ""
    fan_index = _channel_index_from_name("fan", Path(rpm_input).name) if rpm_input else ""
    parts = []
    if pwm_index:
        parts.append(f"PWM{pwm_index}")
    if fan_index:
        parts.append(f"FAN{fan_index}")
    if parts:
        return "/".join(parts)
    nvidia_match = re.search(r"(?:nvidia|nvidia-smi):(\d+)", pwm_path or rpm_input or "")
    if nvidia_match:
        return f"GPU{nvidia_match.group(1)}"
    return "Channel"


def _channel_indexes_from_label(channel_label: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"(?:PWM|FAN)(\d+)", channel_label.upper())))


def _normalize_fan_header_label(text: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z]+", "_", str(text or "").strip().upper()).strip("_")
    replacements = {
        "CPUFAN": "CPU_FAN",
        "CPU_FANIN": "CPU_FAN",
        "CPUFANIN": "CPU_FAN",
        "CPU_FAN_OPT": "CPU_OPT",
        "CPUOPT": "CPU_OPT",
        "AIO": "AIO_PUMP",
        "AIOPUMP": "AIO_PUMP",
        "AIO_PUMPIN": "AIO_PUMP",
        "AIOPUMPIN": "AIO_PUMP",
        "AIO_FAN": "AIO_PUMP",
        "PUMP": "W_PUMP",
        "PUMPIN": "W_PUMP",
        "PUMPFAN": "W_PUMP",
        "PUMP_FAN": "W_PUMP",
        "WATER_PUMP": "W_PUMP",
        "WPUMP": "W_PUMP",
        "W_PUMP+": "W_PUMP",
        "CHAFAN": "CHA_FAN",
        "CHA_FANIN": "CHA_FAN",
        "CHAFANIN": "CHA_FAN",
        "CHASSIS_FAN": "CHA_FAN",
        "CHASSIS": "CHA_FAN",
        "CASE_FAN": "CHA_FAN",
        "CASEFAN": "CHA_FAN",
        "SYSTEM_FAN": "SYS_FAN",
        "SYSFAN": "SYS_FAN",
        "SYS_FANIN": "SYS_FAN",
        "SYSFANIN": "SYS_FAN",
        "HAMP": "H_AMP",
        "H_AMP_FAN": "H_AMP",
        "HAMP_FAN": "H_AMP",
        "HIGH_AMP": "H_AMP",
        "HIGH_AMP_FAN": "H_AMP",
        "EXTFAN": "EXT_FAN",
        "EXT_FAN1": "EXT_FAN",
    }
    return replacements.get(cleaned, cleaned)


def _known_fan_header_label(text: str) -> str:
    normalized = _normalize_fan_header_label(text)
    if normalized in {value for _label, value in FAN_HEADER_OPTIONS if value}:
        return normalized
    if re.fullmatch(r"CPU_?FAN_?\d+", normalized):
        return "CPU_FAN"
    if re.fullmatch(r"CPU_?OPT_?\d+", normalized):
        return "CPU_OPT"
    if re.fullmatch(r"AIO_?PUMP_?\d+", normalized):
        return "AIO_PUMP"
    if re.fullmatch(r"(W_?PUMP|WATER_?PUMP|PUMP_?FAN)_?\d+", normalized):
        return "W_PUMP"
    match = re.fullmatch(r"(CHA|SYS)_?FAN_?(\d+)", normalized)
    if match:
        return f"{match.group(1)}_FAN{match.group(2)}"
    match = re.fullmatch(r"(CHA|SYS)_?(\d+)_?FAN(?:IN)?", normalized)
    if match:
        return f"{match.group(1)}_FAN{match.group(2)}"
    match = re.fullmatch(r"CHASSIS_?FAN_?(\d+)", normalized)
    if match:
        return f"CHA_FAN{match.group(1)}"
    match = re.fullmatch(r"CHASSIS_?(\d+)_?FAN(?:IN)?", normalized)
    if match:
        return f"CHA_FAN{match.group(1)}"
    match = re.fullmatch(r"SYSTEM_?FAN_?(\d+)", normalized)
    if match:
        return f"SYS_FAN{match.group(1)}"
    match = re.fullmatch(r"SYSTEM_?(\d+)_?FAN(?:IN)?", normalized)
    if match:
        return f"SYS_FAN{match.group(1)}"
    if re.fullmatch(r"EXT_?FAN_?\d+", normalized):
        return "EXT_FAN"
    return ""


def _fan_header_role(header_label: str) -> str:
    normalized = _known_fan_header_label(header_label.rstrip("?"))
    if normalized in FAN_HEADER_ROLE_HINTS:
        return FAN_HEADER_ROLE_HINTS[normalized]
    if normalized.startswith(("CHA_FAN", "SYS_FAN")):
        return "机箱风扇"
    return ""


def _header_for_display(header_label: str, *, confirmed: bool) -> str:
    header = _known_fan_header_label(header_label.rstrip("?")) or header_label.rstrip("?")
    if not header:
        return ""
    return header if confirmed else f"{header}?"


def _is_real_sysfs_hwmon_path(path_text: str) -> bool:
    if not path_text:
        return False
    path_text = path_text.removeprefix("readonly:")
    try:
        path = Path(path_text).resolve()
    except OSError:
        path = Path(path_text)
    return str(path).startswith("/sys/devices/") or str(path).startswith("/sys/class/hwmon/")


def _is_temperature_chart_sensor(name: str, unit: str) -> bool:
    if unit != "°C":
        return False
    lowered = name.lower()
    return (
        name == "CPU Tctl"
        or name.startswith("CPU ")
        or "gpu" in lowered
        or name.startswith("集显")
        or "主板" in name
        or "chipset" in lowered
    )


def _temperature_sensor_priority(name: str) -> tuple[int, str]:
    lowered = name.lower()
    if name == "CPU Tctl":
        return (0, name)
    if name.startswith("CPU "):
        return (1, name)
    if "gpu" in lowered or name.startswith("集显"):
        return (2, name)
    if "主板" in name or "chipset" in lowered:
        return (3, name)
    return (9, name)


class FanTrendChart(QFrame):
    def __init__(self, title: str, unit: str, *, default_max: float) -> None:
        super().__init__()
        self.setObjectName("FanTrendChart")
        self.setMinimumHeight(228)
        self._title = title
        self._unit = unit
        self._default_max = default_max
        self._series: dict[str, list[float]] = {}

    def set_series(self, series: dict[str, list[float]]) -> None:
        self._series = {name: list(values)[-90:] for name, values in series.items() if values}
        self.update()

    def clear(self) -> None:
        self._series = {}
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001, D401
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(14, 12, -14, -12)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        painter.setPen(QColor("#eef1ee"))
        title_font = painter.font()
        title_font.setBold(True)
        title_font.setPointSize(max(10, title_font.pointSize()))
        painter.setFont(title_font)
        painter.drawText(rect.left(), rect.top(), rect.width(), 22, Qt.AlignmentFlag.AlignLeft, self._title)

        values = [value for items in self._series.values() for value in items if math.isfinite(value)]
        top_value = next((items[-1] for items in self._series.values() if items), None)
        if top_value is not None:
            painter.setPen(QColor("#969b9e"))
            badge = f"当前 {top_value:.0f} {self._unit}"
            painter.drawText(rect.left(), rect.top(), rect.width(), 22, Qt.AlignmentFlag.AlignRight, badge)

        legend_height = 26 if values else 0
        plot_rect = QRectF(rect.left() + 42, rect.top() + 42 + legend_height, rect.width() - 92, rect.height() - 72 - legend_height)
        if plot_rect.width() <= 4 or plot_rect.height() <= 4:
            return

        self._draw_plot_background(painter, plot_rect)

        if not values:
            self._draw_empty_state(painter, plot_rect)
            return

        min_value, max_value = self._value_range(values)
        if max_value <= min_value:
            max_value = min_value + 1

        self._draw_axes(painter, plot_rect, min_value, max_value)
        self._draw_legend(painter, rect, plot_rect)
        self._draw_series(painter, plot_rect, min_value, max_value)

    def _draw_plot_background(self, painter: QPainter, plot_rect: QRectF) -> None:
        gradient = QLinearGradient(plot_rect.topLeft(), plot_rect.bottomLeft())
        gradient.setColorAt(0.0, QColor("#1d2021"))
        gradient.setColorAt(1.0, QColor("#121314"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(plot_rect, 6, 6)

    def _draw_empty_state(self, painter: QPainter, plot_rect: QRectF) -> None:
        painter.setPen(QPen(QColor("#303438"), 1))
        for index in range(4):
            y = plot_rect.top() + plot_rect.height() * index / 3
            painter.drawLine(plot_rect.left(), y, plot_rect.right(), y)
        painter.setPen(QColor("#8f9698"))
        painter.drawText(plot_rect, Qt.AlignmentFlag.AlignCenter, "等待实时数据")

    def _value_range(self, values: list[float]) -> tuple[float, float]:
        if self._unit == "°C":
            low = min(values)
            high = max(values)
            min_value = max(0.0, math.floor((low - 6) / 5) * 5)
            max_value = max(80.0, math.ceil((high + 6) / 5) * 5)
            return min_value, max_value
        high = max(self._default_max, max(values) * 1.18)
        return 0.0, self._nice_ceiling(high)

    def _nice_ceiling(self, value: float) -> float:
        if value <= 0:
            return self._default_max
        magnitude = 10 ** math.floor(math.log10(value))
        normalized = value / magnitude
        if normalized <= 1:
            nice = 1
        elif normalized <= 2:
            nice = 2
        elif normalized <= 5:
            nice = 5
        else:
            nice = 10
        return nice * magnitude

    def _draw_axes(self, painter: QPainter, plot_rect: QRectF, min_value: float, max_value: float) -> None:
        label_font = painter.font()
        label_font.setBold(False)
        label_font.setPointSize(max(8, label_font.pointSize() - 1))
        painter.setFont(label_font)

        grid_pen = QPen(QColor("#303438"), 1)
        grid_pen.setStyle(Qt.PenStyle.DashLine)
        axis_pen = QPen(QColor("#484d50"), 1)
        painter.setPen(grid_pen)
        ticks = 4
        for index in range(ticks + 1):
            ratio = index / ticks
            y = plot_rect.top() + plot_rect.height() * ratio
            value = max_value - (max_value - min_value) * ratio
            painter.drawLine(plot_rect.left(), y, plot_rect.right(), y)
            painter.setPen(QColor("#8f9698"))
            painter.drawText(
                QRectF(plot_rect.left() - 40, y - 8, 34, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{value:.0f}",
            )
            painter.setPen(grid_pen)

        for index in range(4):
            x = plot_rect.left() + plot_rect.width() * index / 3
            painter.drawLine(x, plot_rect.top(), x, plot_rect.bottom())
        painter.setPen(axis_pen)
        painter.drawLine(plot_rect.left(), plot_rect.bottom(), plot_rect.right(), plot_rect.bottom())
        painter.setPen(QColor("#8f9698"))
        painter.drawText(
            QRectF(plot_rect.right() - 52, plot_rect.top() - 22, 52, 16),
            Qt.AlignmentFlag.AlignRight,
            self._unit,
        )

    def _draw_legend(self, painter: QPainter, rect, plot_rect: QRectF) -> None:  # noqa: ANN001
        painter.setPen(Qt.PenStyle.NoPen)
        legend_y = rect.top() + 38
        legend_x = plot_rect.left()
        metrics = QFontMetrics(painter.font())
        for index, (name, items) in enumerate(list(self._series.items())[:4]):
            if not items:
                continue
            color = QColor(CHART_COLORS[index % len(CHART_COLORS)])
            text = metrics.elidedText(f"{name} {items[-1]:.0f}", Qt.TextElideMode.ElideRight, 138)
            chip_width = min(154, max(82, metrics.horizontalAdvance(text) + 26))
            if legend_x + chip_width > plot_rect.right():
                break
            painter.setBrush(QColor("#202224"))
            painter.setPen(QColor("#3a3f42"))
            painter.drawRoundedRect(QRectF(legend_x, legend_y - 2, chip_width, 20), 5, 5)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QRectF(legend_x + 8, legend_y + 4, 8, 8))
            painter.setPen(QColor("#d4d9d6"))
            painter.drawText(
                QRectF(legend_x + 21, legend_y, chip_width - 26, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
            legend_x += chip_width + 8

    def _draw_series(self, painter: QPainter, plot_rect: QRectF, min_value: float, max_value: float) -> None:
        endpoint_labels: list[tuple[QColor, QPointF, str]] = []
        for index, (name, items) in enumerate(list(self._series.items())[:5]):
            color = QColor(CHART_COLORS[index % len(CHART_COLORS)])
            points = [value for value in items if math.isfinite(value)]
            if not points:
                continue
            screen_points = [
                QPointF(
                    plot_rect.left() + plot_rect.width() * point_index / max(1, len(points) - 1),
                    self._value_to_y(value, min_value, max_value, plot_rect),
                )
                for point_index, value in enumerate(points)
            ]
            if len(points) == 1:
                x = screen_points[0].x()
                y = screen_points[0].y()
                painter.setBrush(color)
                painter.drawEllipse(QRectF(x - 3, y - 3, 6, 6))
                endpoint_labels.append((color, screen_points[-1], f"{points[-1]:.0f}"))
                continue

            path = self._smooth_path(screen_points)
            area = QPainterPath(path)
            area.lineTo(screen_points[-1].x(), plot_rect.bottom())
            area.lineTo(screen_points[0].x(), plot_rect.bottom())
            area.closeSubpath()
            fill = QColor(color)
            fill.setAlpha(34 if index == 0 else 22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawPath(area)

            shadow = QColor(color)
            shadow.setAlpha(70)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(shadow, 6.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
            painter.setPen(QPen(color, 2.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)

            endpoint = screen_points[-1]
            painter.setPen(QPen(QColor("#121314"), 3))
            painter.setBrush(color)
            painter.drawEllipse(QRectF(endpoint.x() - 4, endpoint.y() - 4, 8, 8))
            endpoint_labels.append((color, endpoint, f"{points[-1]:.0f}"))

        self._draw_endpoint_labels(painter, plot_rect, endpoint_labels)

    def _smooth_path(self, points: list[QPointF]) -> QPainterPath:
        path = QPainterPath(points[0])
        if len(points) < 3:
            for point in points[1:]:
                path.lineTo(point)
            return path
        for index in range(len(points) - 1):
            p0 = points[max(index - 1, 0)]
            p1 = points[index]
            p2 = points[index + 1]
            p3 = points[min(index + 2, len(points) - 1)]
            c1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6, p1.y() + (p2.y() - p0.y()) / 6)
            c2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6, p2.y() - (p3.y() - p1.y()) / 6)
            path.cubicTo(c1, c2, p2)
        return path

    def _draw_endpoint_labels(
        self,
        painter: QPainter,
        plot_rect: QRectF,
        labels: list[tuple[QColor, QPointF, str]],
    ) -> None:
        used_y: list[float] = []
        for color, point, text in labels[:4]:
            y = point.y()
            for existing in used_y:
                if abs(y - existing) < 18:
                    y = existing + 18
            y = max(plot_rect.top() + 8, min(plot_rect.bottom() - 8, y))
            used_y.append(y)
            label_rect = QRectF(plot_rect.right() + 8, y - 10, 38, 20)
            fill = QColor("#202224")
            fill.setAlpha(235)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(label_rect, 5, 5)
            painter.setPen(color)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, text)

    def _value_to_y(self, value: float, min_value: float, max_value: float, plot_rect: QRectF) -> float:
        ratio = (value - min_value) / (max_value - min_value)
        ratio = max(0.0, min(1.0, ratio))
        return plot_rect.bottom() - plot_rect.height() * ratio


class FanStatusCard(QFrame):
    def __init__(self, fan_name: str) -> None:
        super().__init__()
        self.setObjectName("FanStatusCard")
        self.setMinimumHeight(126)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self.name_label = QLabel(fan_name)
        self.name_label.setObjectName("FanCardName")
        self.name_label.setWordWrap(True)
        self.source_label = QLabel("--")
        self.source_label.setObjectName("FanCardMeta")
        self.source_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.name_label, 1)
        top.addWidget(self.source_label)
        layout.addLayout(top)

        meta_row = QHBoxLayout()
        self.role_label = QLabel("待识别")
        self.role_label.setObjectName("FanRoleBadge")
        self.channel_label = QLabel("--")
        self.channel_label.setObjectName("FanCardMeta")
        self.channel_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        meta_row.addWidget(self.role_label)
        meta_row.addWidget(self.channel_label, 1)
        layout.addLayout(meta_row)

        metric_row = QHBoxLayout()
        self.speed_label = QLabel("转速")
        self.speed_label.setObjectName("FanCardMeta")
        self.rpm_value = QLabel("-- RPM")
        self.rpm_value.setObjectName("FanRpmValue")
        self.pwm_value = QLabel("输出 --")
        self.pwm_value.setObjectName("FanCardMeta")
        metric_row.addWidget(self.speed_label)
        metric_row.addWidget(self.rpm_value, 1)
        metric_row.addWidget(self.pwm_value)
        layout.addLayout(metric_row)

        self.rpm_bar = QProgressBar()
        self.rpm_bar.setTextVisible(False)
        self.rpm_bar.setRange(0, 1800)
        self.rpm_bar.setFixedHeight(7)
        layout.addWidget(self.rpm_bar)

        self.status_label = QLabel("等待转速")
        self.status_label.setObjectName("FanCardMeta")
        layout.addWidget(self.status_label)

    def update_status(
        self,
        *,
        name: str,
        rpm: int | None,
        rpm_text: str,
        rpm_unit: str,
        pwm: str,
        source: str,
        status: str,
        role: str,
        channel: str,
        detail: str,
        read_only: bool,
        control_label: str,
    ) -> None:
        self.name_label.setText(name)
        self.source_label.setText(source)
        self.channel_label.setText(channel)
        self.role_label.setText(role)
        role_color = _fan_role_color(role)
        self.role_label.setStyleSheet(
            f"color: {role_color}; border: 1px solid {role_color}; border-radius: 5px; padding: 2px 6px;"
        )
        self.speed_label.setText(_fan_speed_label(role, rpm_unit))
        if detail:
            self.setToolTip(detail)
        if rpm is None:
            self.rpm_value.setText("--" if rpm_unit == "%" else "-- RPM")
            self.rpm_bar.setValue(0)
        else:
            self.rpm_value.setText(rpm_text)
            maximum = 100 if rpm_unit == "%" else max(1800, int(rpm * 1.25))
            self.rpm_bar.setMaximum(maximum)
            self.rpm_bar.setValue(max(0, min(rpm, maximum)))
        self.pwm_value.setText(control_label if read_only else f"{control_label} {pwm}")
        self.status_label.setText(status)
        color = "#b7dfd2" if rpm and rpm > 0 else "#d9bc79"
        if "无转速" in status:
            color = "#e0a2a7"
        self.status_label.setStyleSheet(f"color: {color};")


class FanRoleMetricCard(QFrame):
    def __init__(self, role: str) -> None:
        super().__init__()
        self.role = role
        self.setObjectName("FanRoleMetricCard")
        self.setMinimumHeight(82)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)

        top = QHBoxLayout()
        self.title_label = QLabel(_fan_role_compact_label(role))
        self.title_label.setObjectName("FanRoleMetricTitle")
        self.count_label = QLabel("0 通道")
        self.count_label.setObjectName("FanCardMeta")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.title_label)
        top.addWidget(self.count_label, 1)
        layout.addLayout(top)

        self.speed_label = QLabel("未发现")
        self.speed_label.setObjectName("FanRoleMetricValue")
        self.speed_label.setWordWrap(True)
        layout.addWidget(self.speed_label)

        self.detail_label = QLabel("--")
        self.detail_label.setObjectName("FanCardMeta")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

    def update_status(
        self,
        *,
        count: int,
        active: int,
        headline: str,
        detail: str,
        color: str,
    ) -> None:
        self.count_label.setText(f"{count} 通道" if count else "未发现")
        self.speed_label.setText(headline)
        self.detail_label.setText(detail)
        self.title_label.setStyleSheet(f"color: {color};")
        self.speed_label.setStyleSheet(f"color: {color};")
        border_color = color if count else "#303438"
        self.setStyleSheet(f"border-color: {border_color};")
        self.setToolTip(f"{self.role}：{active}/{count} 个通道有转速" if count else f"未发现{self.role}")


class StressTestPanel(QWidget):
    _LABELS = {
        "cpu": ("CPU 压力", "stress-ng CPU"),
        "fpu": ("FPU 压力", "stress-ng FFT"),
        "gpu": ("GPU 压力", "gpu-burn"),
    }

    def __init__(self) -> None:
        super().__init__()
        self.burner = None
        self._tool_available: dict[str, bool] = {key: False for key in self._LABELS}
        self._buttons: dict[str, QPushButton] = {}
        self._state_labels: dict[str, QLabel] = {}
        self._tool_labels: dict[str, QLabel] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("压力测试")
        title.setObjectName("SectionLabel")
        self.status_label = QLabel("加载风扇监控后可启动压力测试")
        self.status_label.setObjectName("FieldHint")
        self.stop_all_button = QPushButton("停止全部")
        self.stop_all_button.setObjectName("DangerButton")
        self.stop_all_button.setEnabled(False)
        self.stop_all_button.clicked.connect(self.stop_all)
        header.addWidget(title)
        header.addWidget(self.status_label, 1)
        header.addWidget(self.stop_all_button)
        layout.addLayout(header)

        cards = QGridLayout()
        cards.setHorizontalSpacing(12)
        cards.setVerticalSpacing(12)
        for column, (kind, (title_text, tool_text)) in enumerate(self._LABELS.items()):
            card = QFrame()
            card.setObjectName("MetricCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(8)

            name = QLabel(title_text)
            name.setObjectName("SectionLabel")
            tool = QLabel(tool_text)
            tool.setObjectName("FieldHint")
            state = QLabel("未运行")
            state.setObjectName("HomeMetricValue")
            button = QPushButton("启动")
            button.setCheckable(True)
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, current=kind: self.toggle(current))

            card_layout.addWidget(name)
            card_layout.addWidget(tool)
            card_layout.addWidget(state)
            card_layout.addStretch(1)
            card_layout.addWidget(button)
            cards.addWidget(card, 0, column)
            self._buttons[kind] = button
            self._state_labels[kind] = state
            self._tool_labels[kind] = tool
        layout.addLayout(cards)
        layout.addStretch(1)

    def set_burner(self, burner) -> None:  # noqa: ANN001
        self.burner = burner
        if hasattr(burner, "state_changed"):
            burner.state_changed.connect(self._burner_state_changed)
        if hasattr(burner, "tool_missing"):
            burner.tool_missing.connect(self._tool_missing)
        self.refresh_tools()
        self.refresh_state()

    def clear_burner(self) -> None:
        self.burner = None
        self._tool_available = {key: False for key in self._LABELS}
        self.status_label.setText("加载风扇监控后可启动压力测试")
        self.stop_all_button.setEnabled(False)
        for kind in self._LABELS:
            self._set_kind_state(kind, False)
            self._buttons[kind].setEnabled(False)
            self._tool_labels[kind].setText(self._LABELS[kind][1])

    def refresh_tools(self) -> None:
        if self.burner is None or not hasattr(self.burner, "check_tools"):
            return
        self._tool_available = dict(self.burner.check_tools())
        for kind, available in self._tool_available.items():
            self._buttons[kind].setEnabled(available)
            tool_name = self._LABELS[kind][1]
            self._tool_labels[kind].setText(f"{tool_name} · {'可用' if available else '未安装'}")
        missing = [self._LABELS[kind][1] for kind, ok in self._tool_available.items() if not ok]
        self.status_label.setText("缺少工具：" + "、".join(missing) if missing else "压力测试工具可用")

    def refresh_state(self) -> None:
        running = getattr(self.burner, "running", {}) if self.burner is not None else {}
        for kind in self._LABELS:
            self._set_kind_state(kind, bool(running.get(kind)))
        self.stop_all_button.setEnabled(any(bool(value) for value in running.values()))

    def toggle(self, kind: str) -> None:
        if self.burner is None:
            self.status_label.setText("请先加载风扇监控")
            return
        running = bool(getattr(self.burner, "running", {}).get(kind))
        if running:
            self.burner.stop(kind)
        else:
            self.burner.start(kind)
        self.refresh_state()

    def stop_all(self) -> None:
        if self.burner is None:
            return
        self.burner.stop_all()
        self.refresh_state()

    def _burner_state_changed(self, kind: str, running: bool) -> None:
        self._set_kind_state(kind, running)
        self.refresh_state()

    def _tool_missing(self, tool: str) -> None:
        self.status_label.setText(f"缺少压力测试工具：{tool}")
        self.refresh_tools()

    def _set_kind_state(self, kind: str, running: bool) -> None:
        if kind not in self._buttons:
            return
        self._buttons[kind].blockSignals(True)
        self._buttons[kind].setChecked(running)
        self._buttons[kind].setText("停止" if running else "启动")
        self._buttons[kind].blockSignals(False)
        self._state_labels[kind].setText("运行中" if running else "未运行")
        self._state_labels[kind].setStyleSheet("color: #d9847a;" if running else "color: #f2f3f0;")


class EmbeddedFanControlPanel(QWidget):
    """Role-aware fan control panel used by the combined GUI.

    The old fan control project exposes generic PWM channels. This wrapper keeps
    its slider behavior but makes the physical role and evidence visible before
    the user writes any PWM value.
    """

    def __init__(self, fan_slider_cls, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._fan_slider_cls = fan_slider_cls
        self._sliders: dict[str, object] = {}
        self._fan_rows: dict[str, QWidget] = {}
        self._fan_roles: dict[str, str] = {}
        self._section_rows: dict[str, list[QWidget]] = {}
        self._section_widgets: dict[str, QWidget] = {}
        self._active_fans: set[str] = set()
        self._total_fans = 0
        self._control_enabled = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self._info_label = QLabel("加载后按 CPU 风扇、水泵和机箱风扇分组显示")
        self._info_label.setObjectName("FieldHint")
        self._role_filter_combo = QComboBox()
        self._role_filter_combo.setMinimumWidth(150)
        self._role_filter_combo.currentIndexChanged.connect(self._apply_filter)
        self._show_active_only_cb = QCheckBox("只看有转速")
        self._show_active_only_cb.toggled.connect(self._apply_filter)
        top.addWidget(self._info_label, 1)
        top.addWidget(self._role_filter_combo)
        top.addWidget(self._show_active_only_cb)
        layout.addLayout(top)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("FanControlScrollArea")
        self._scroll.setWidgetResizable(True)
        self._fan_container = QWidget()
        self._fan_container.setObjectName("FanControlContainer")
        self._fan_layout = QVBoxLayout(self._fan_container)
        self._fan_layout.setContentsMargins(0, 0, 0, 0)
        self._fan_layout.setSpacing(10)
        self._scroll.setWidget(self._fan_container)
        layout.addWidget(self._scroll, 1)
        self._rebuild_role_filter([])

    def populate_fans(self, fans, temp_sensors) -> None:  # noqa: ANN001
        self._clear_rows()
        fans = sorted(list(fans), key=_fan_sort_key)
        self._total_fans = len(fans)
        roles = self._ordered_roles(fans)
        self._rebuild_role_filter(roles)

        for role in roles:
            role_fans = [fan for fan in fans if str(getattr(fan, "type_label", "") or "未识别通道") == role]
            if not role_fans:
                continue
            section = self._make_section(role, len(role_fans))
            section_layout = section.layout()
            for fan in role_fans:
                row = self._make_fan_row(fan, temp_sensors)
                section_layout.addWidget(row)
                name = str(getattr(fan, "name", ""))
                self._fan_rows[name] = row
                self._fan_roles[name] = role
                self._section_rows.setdefault(role, []).append(row)
            self._fan_layout.addWidget(section)
            self._section_widgets[role] = section

        self._fan_layout.addStretch(1)
        self._apply_filter()
        self._update_info()

    def update_fan_rpm(self, fan_name: str, rpm: int) -> None:
        slider = self._sliders.get(fan_name)
        if slider is not None and hasattr(slider, "update_rpm"):
            slider.update_rpm(rpm)
        if rpm > 0:
            self._active_fans.add(fan_name)
            if self._show_active_only_cb.isChecked():
                self._apply_filter()
        self._update_info()

    def set_control_enabled(self, enabled: bool) -> None:
        self._control_enabled = bool(enabled)
        for slider in self._sliders.values():
            if hasattr(slider, "set_control_enabled"):
                slider.set_control_enabled(self._control_enabled)

    def _ordered_roles(self, fans: list[object]) -> list[str]:
        seen = {str(getattr(fan, "type_label", "") or "未识别通道") for fan in fans}
        ordered = [role for role in FAN_ROLE_OVERVIEW_ORDER if role in seen]
        ordered.extend(sorted(role for role in seen if role not in set(ordered)))
        return ordered

    def _rebuild_role_filter(self, roles: list[str]) -> None:
        current = str(self._role_filter_combo.currentData() or "")
        self._role_filter_combo.blockSignals(True)
        self._role_filter_combo.clear()
        self._role_filter_combo.addItem("全部角色", "")
        for role in roles:
            self._role_filter_combo.addItem(_fan_role_compact_label(role), role)
        index = self._role_filter_combo.findData(current) if current else 0
        self._role_filter_combo.setCurrentIndex(index if index >= 0 else 0)
        self._role_filter_combo.blockSignals(False)

    def _make_section(self, role: str, count: int) -> QFrame:
        section = QFrame()
        section.setObjectName("FanControlGroup")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel(f"{_fan_role_compact_label(role)} · {count} 通道")
        title.setObjectName("SectionLabel")
        hint = QLabel(FAN_ROLE_CONTROL_HINTS.get(role, "先确认物理接口，再启用 PWM 写入"))
        hint.setObjectName("FieldHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(title)
        header.addWidget(hint, 1)
        layout.addLayout(header)
        return section

    def _make_fan_row(self, fan, temp_sensors) -> QFrame:  # noqa: ANN001
        row = QFrame()
        row.setObjectName("FanControlRow")
        detail = str(getattr(fan, "detail_text", "") or "")
        row.setToolTip(detail)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(7)

        meta = QHBoxLayout()
        channel = str(getattr(fan, "channel_label", "") or "Channel")
        role = str(getattr(fan, "type_label", "") or "未识别通道")
        header = str(getattr(fan, "header_label", "") or "")
        identity = _fan_identity_state_label(fan)
        title = QLabel(header or channel)
        title.setObjectName("FanControlChannelTitle")
        role_label = QLabel(role)
        role_label.setObjectName("FanRoleBadge")
        role_label.setStyleSheet(
            f"color: {_fan_role_color(role)}; border: 1px solid {_fan_role_color(role)};"
            " border-radius: 5px; padding: 2px 6px;"
        )
        identity_label = QLabel(identity)
        identity_label.setObjectName("FanIdentityBadge")
        meta.addWidget(title)
        meta.addWidget(role_label)
        meta.addWidget(identity_label)
        meta.addStretch(1)
        meta.addWidget(QLabel(channel))
        layout.addLayout(meta)

        evidence_label = QLabel(_fan_identity_evidence_text(fan, compact=True))
        evidence_label.setObjectName("FieldHint")
        evidence_label.setWordWrap(True)
        evidence_label.setToolTip(detail)
        layout.addWidget(evidence_label)

        slider = self._fan_slider_cls(str(getattr(fan, "name", channel)))
        slider.setObjectName("EmbeddedFanSlider")
        slider.setToolTip(detail)
        if hasattr(slider, "set_control_enabled"):
            slider.set_control_enabled(self._control_enabled)
        name_label = getattr(slider, "_name_label", None)
        if name_label is not None:
            name_label.setText(_fan_control_title(fan))
            name_label.setToolTip(detail)
        self._sliders[str(getattr(fan, "name", channel))] = slider
        layout.addWidget(slider)

        bind_row = QHBoxLayout()
        bind_label = QLabel("建议温度源")
        bind_label.setObjectName("FieldHint")
        combo = QComboBox()
        combo.addItem("(自动)", None)
        for sensor in temp_sensors:
            combo.addItem(str(getattr(sensor, "name", "")), getattr(sensor, "internal_id", None))
        default_index = self._default_sensor_index(combo, role)
        if default_index >= 0:
            combo.setCurrentIndex(default_index)
        combo.setMaximumWidth(360)
        bind_row.addWidget(bind_label)
        bind_row.addWidget(combo)
        bind_row.addStretch(1)
        layout.addLayout(bind_row)
        return row

    def _default_sensor_index(self, combo: QComboBox, role: str) -> int:
        best_index = 0
        best_score = 0
        for index in range(1, combo.count()):
            score = self._sensor_role_score(combo.itemText(index), role)
            if score > best_score:
                best_score = score
                best_index = index
        return best_index

    def _sensor_role_score(self, name: str, role: str) -> int:
        lowered = name.casefold()
        if role in {"CPU 风扇", "水泵/AIO", "机箱风扇"}:
            if "tctl" in lowered:
                return 50
            if "cpu" in lowered or "tsi0" in lowered or "package" in lowered:
                return 40
            if "cputin" in lowered:
                return 30
        if role == "GPU 风扇":
            if "gpu" in lowered or "显卡" in name or "集显" in name:
                return 50
        if "温度" in name or "temp" in lowered:
            return 10
        return 0

    def _apply_filter(self, *_args) -> None:  # noqa: ANN002
        role_filter = str(self._role_filter_combo.currentData() or "")
        active_only = self._show_active_only_cb.isChecked()
        for name, row in self._fan_rows.items():
            role = self._fan_roles.get(name, "")
            visible = (not role_filter or role == role_filter) and (not active_only or name in self._active_fans)
            row.setVisible(visible)
        for role, section in self._section_widgets.items():
            section.setVisible(any(not row.isHidden() for row in self._section_rows.get(role, [])))

    def _update_info(self) -> None:
        counts: dict[str, int] = {}
        for role in self._fan_roles.values():
            counts[role] = counts.get(role, 0) + 1
        role_parts = [
            f"{_fan_role_compact_label(role)} {counts[role]}"
            for role in FAN_ROLE_OVERVIEW_ORDER
            if counts.get(role)
        ]
        role_parts.extend(
            f"{_fan_role_compact_label(role)} {count}"
            for role, count in sorted(counts.items())
            if role not in FAN_ROLE_OVERVIEW_ORDER
        )
        active = len(self._active_fans)
        if self._total_fans:
            suffix = " · " + " · ".join(role_parts) if role_parts else ""
            self._info_label.setText(f"检测到 {self._total_fans} 个可控通道 · {active} 个有转速{suffix}")
        else:
            self._info_label.setText("没有可写 PWM 通道；请查看总览或明细中的只读转速")

    def _clear_rows(self) -> None:
        while self._fan_layout.count():
            item = self._fan_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._sliders = {}
        self._fan_rows = {}
        self._fan_roles = {}
        self._section_rows = {}
        self._section_widgets = {}
        self._active_fans = set()


class EmbeddedProfileEditor(QWidget):
    profile_activated = Signal()

    def __init__(self, profile_manager, curve_editor_cls, fan_curve_cls, profile_cls, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._pm = profile_manager
        self._fan_curve_cls = fan_curve_cls
        self._profile_cls = profile_cls
        self._fan_rpm_data: dict[str, int] = {}
        self._fan_pwm_data: dict[str, int] = {}
        self._cpu_power = ""
        self._gpu_power = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        top_card = QFrame()
        top_card.setObjectName("MetricCard")
        top_layout = QGridLayout(top_card)
        top_layout.setContentsMargins(14, 12, 14, 12)
        top_layout.setHorizontalSpacing(10)
        top_layout.setVerticalSpacing(8)
        title = QLabel("配置文件")
        title.setObjectName("SectionLabel")
        top_layout.addWidget(title, 0, 0)
        self._profile_state_label = QLabel("--")
        self._profile_state_label.setObjectName("FieldHint")
        top_layout.addWidget(self._profile_state_label, 0, 1, 1, 4, Qt.AlignmentFlag.AlignRight)
        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(260)
        self._profile_combo.currentTextChanged.connect(self._on_select)
        top_layout.addWidget(self._profile_combo, 1, 0, 1, 2)
        self._btn_new = QPushButton("新建")
        self._btn_delete = QPushButton("删除")
        self._btn_save = QPushButton("保存")
        self._btn_activate = QPushButton("激活此配置")
        self._btn_activate.setObjectName("PrimaryButton")
        for column, button in enumerate((self._btn_new, self._btn_delete, self._btn_save, self._btn_activate), start=2):
            top_layout.addWidget(button, 1, column)
        top_layout.setColumnStretch(0, 1)
        top_layout.setColumnStretch(1, 1)
        layout.addWidget(top_card)

        status = QFrame()
        status.setObjectName("MetricCard")
        status_layout = QHBoxLayout(status)
        status_layout.setContentsMargins(12, 8, 12, 8)
        status_layout.setSpacing(8)
        self._cpu_temp_label = self._status_chip("CPU: --°C")
        self._gpu_temp_label = self._status_chip("GPU: --°C")
        self._fan_rpm_label = self._status_chip("风扇: -- RPM")
        status_layout.addWidget(self._cpu_temp_label)
        status_layout.addWidget(self._gpu_temp_label)
        status_layout.addWidget(self._fan_rpm_label, 1)
        layout.addWidget(status)

        self._curve_tabs = QTabWidget()
        self._cpu_editor = self._make_curve_editor(curve_editor_cls)
        self._gpu_editor = self._make_curve_editor(curve_editor_cls)
        self._curve_tabs.addTab(self._curve_page("CPU 风扇曲线", self._cpu_editor), "CPU 曲线")
        self._curve_tabs.addTab(self._curve_page("GPU 风扇曲线", self._gpu_editor), "GPU 曲线")
        layout.addWidget(self._curve_tabs, 1)

        self._populate_list()
        self._btn_new.clicked.connect(self._new_profile)
        self._btn_delete.clicked.connect(self._delete_profile)
        self._btn_save.clicked.connect(self._save_profile)
        self._btn_activate.clicked.connect(self._activate_profile)

    def _status_chip(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FilePathLabel")
        return label

    def _make_curve_editor(self, curve_editor_cls):  # noqa: ANN001
        editor = curve_editor_cls()
        editor.setMinimumSize(520, 300)
        editor.setAutoFillBackground(True)
        editor.setStyleSheet("background: #eef0ed; border: 1px solid #62686c; border-radius: 6px;")
        return editor

    def _curve_page(self, title: str, editor) -> QWidget:  # noqa: ANN001
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("SectionLabel")
        layout.addWidget(label)
        layout.addWidget(editor, 1)
        return page

    def update_sensor(self, name: str, value: float) -> None:
        if math.isnan(value):
            return
        if name.startswith("CPU T") or name.startswith("CPU C"):
            self._cpu_temp_label.setText(f"CPU: {value:.0f}°C{self._cpu_power}")
        elif "温度" in name and ("GPU" in name or "集显" in name):
            self._gpu_temp_label.setText(f"GPU: {value:.0f}°C{self._gpu_power}")
        elif name.startswith("CPU ") and "package" in name:
            self._cpu_power = f"  {value:.0f}W"
        elif "GPU" in name and "功耗" in name:
            self._gpu_power = f"  {value:.0f}W"

    def update_fan_rpm(self, fan_name: str, rpm: int) -> None:
        self._fan_rpm_data[fan_name] = rpm
        self._refresh_fan_label()

    def update_fan_pwm(self, fan_name: str, pwm: int) -> None:
        self._fan_pwm_data[fan_name] = int(pwm / 255 * 100)
        self._refresh_fan_label()

    def _refresh_fan_label(self) -> None:
        parts = []
        for name, rpm in sorted(self._fan_rpm_data.items()):
            if rpm > 0:
                pct = self._fan_pwm_data.get(name, 0)
                short = _profile_fan_status_name(name)
                parts.append(f"{short}: {rpm} RPM ({pct}%)")
        self._fan_rpm_label.setText(" | ".join(parts) if parts else "风扇: -- RPM")

    def _populate_list(self) -> None:
        current = self._profile_combo.currentText().strip()
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        names = sorted(self._pm.list_names())
        self._profile_combo.addItems(names)
        wanted = current or (names[0] if names else "")
        if wanted:
            index = self._profile_combo.findText(wanted)
            if index >= 0:
                self._profile_combo.setCurrentIndex(index)
        self._profile_combo.blockSignals(False)
        self._on_select(wanted)

    def _on_select(self, name: str) -> None:
        if not name:
            return
        profile = self._pm.load(name)
        if profile:
            cpu_pts = profile.curves.get("CPU", [(30, 25), (50, 50), (70, 80), (85, 100)])
            gpu_pts = profile.curves.get("GPU", [(30, 25), (50, 50), (70, 80), (85, 100)])
            self._cpu_editor.set_curve(self._fan_curve_cls(points=cpu_pts))
            self._gpu_editor.set_curve(self._fan_curve_cls(points=gpu_pts))
        active_name = self._active_profile_name()
        if name == active_name:
            self._profile_state_label.setText(f"当前启用：{name}")
        elif active_name:
            self._profile_state_label.setText(f"已启用：{active_name}，当前编辑：{name}")
        else:
            self._profile_state_label.setText(f"当前编辑：{name}")

    def _active_profile_name(self) -> str:
        get_active = getattr(self._pm, "get_active", None)
        if not callable(get_active):
            return ""
        try:
            active = get_active()
        except (PermissionError, OSError):
            return ""
        return str(getattr(active, "name", "") if active else "")

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "新建配置", "配置名称:")
        if ok and name:
            try:
                self._prepare_profile_write(self._profile_path(name))
                self._pm.save(self._profile_cls(name=name))
            except PermissionError as error:
                self._show_profile_permission_error("新建配置", error)
                return
            except OSError as error:
                self._show_profile_io_error("新建配置", error)
                return
            self._populate_list()
            index = self._profile_combo.findText(name)
            if index >= 0:
                self._profile_combo.setCurrentIndex(index)

    def _delete_profile(self) -> None:
        name = self._profile_combo.currentText().strip()
        if not name:
            return
        if QMessageBox.question(self, "删除", f"删除配置 '{name}'?") == QMessageBox.StandardButton.Yes:
            try:
                self._pm.delete(name)
            except PermissionError as error:
                self._show_profile_permission_error("删除配置", error)
                return
            except OSError as error:
                self._show_profile_io_error("删除配置", error)
                return
            self._populate_list()

    def _save_profile(self) -> None:
        name = self._profile_combo.currentText().strip()
        if not name:
            return
        profile = self._pm.load(name)
        if profile:
            profile.curves["CPU"] = self._cpu_editor.get_curve().points
            profile.curves["GPU"] = self._gpu_editor.get_curve().points
            try:
                self._prepare_profile_write(self._profile_path(name))
                self._pm.save(profile)
            except PermissionError as error:
                self._show_profile_permission_error("保存配置", error)
            except OSError as error:
                self._show_profile_io_error("保存配置", error)

    def _activate_profile(self) -> None:
        name = self._profile_combo.currentText().strip()
        if not name:
            return
        try:
            self._write_active_profile(name)
        except PermissionError as error:
            if self._repair_profile_permissions("激活配置"):
                try:
                    self._write_active_profile(name)
                except PermissionError as retry_error:
                    self._show_profile_permission_error("激活配置", retry_error)
                    return
                except OSError as retry_error:
                    self._show_profile_io_error("激活配置", retry_error)
                    return
            else:
                self._show_profile_permission_error("激活配置", error)
                return
        except OSError as error:
            self._show_profile_io_error("激活配置", error)
            return
        self._on_select(name)
        self.profile_activated.emit()
        QMessageBox.information(self, "已激活", f"已切换到 '{name}'")

    def _write_active_profile(self, name: str) -> None:
        self._prepare_profile_write(self._active_profile_path())
        self._pm.set_active(name)

    def _repair_profile_permissions(self, action: str) -> bool:
        config_dir = self._profile_config_dir()
        if config_dir is None:
            return False
        self._profile_state_label.setText(f"{action}需要修复策略配置权限，正在请求系统授权...")
        QApplication.processEvents()
        ok, message = _repair_profile_config_permissions(config_dir, interactive=True)
        if ok:
            self._profile_state_label.setText(f"策略配置权限已修复：{config_dir}")
            return True
        self._profile_state_label.setText(f"策略配置权限修复失败：{message}")
        return False

    def _profile_path(self, name: str) -> Path | None:
        return _profile_manager_profile_path(self._pm, name)

    def _active_profile_path(self) -> Path | None:
        return _profile_manager_active_path(self._pm)

    def _profile_config_dir(self) -> Path | None:
        return _profile_manager_config_dir(self._pm)

    def _prepare_profile_write(self, path: Path | None) -> None:
        _prepare_profile_write_path(path)

    def _show_profile_permission_error(self, action: str, error: PermissionError) -> None:
        config_dir = self._profile_config_dir()
        detail = f"{action}失败：策略配置文件权限不足。"
        if config_dir is not None:
            detail += f"\n目录：{config_dir}"
            detail += f"\n修复命令：pkexec /bin/sh -c {shlex.quote(_profile_config_repair_commands(config_dir))}"
        detail += "\n通常是之前用 sudo 运行风扇程序导致配置文件归 root 所有。"
        detail += "\n也可以在维护 > 权限中点击“修复策略权限”。"
        self._profile_state_label.setText(detail.replace("\n", " "))
        QMessageBox.warning(self, "策略权限不足", f"{detail}\n\n原始错误：{error}")

    def _show_profile_io_error(self, action: str, error: OSError) -> None:
        detail = f"{action}失败：{error}"
        self._profile_state_label.setText(detail)
        QMessageBox.warning(self, "策略操作失败", detail)


class FanControlHostPage(QWidget):
    status_changed = Signal(str)

    def __init__(
        self,
        project_path: Path | None = None,
        auto_grant_pwm_permissions: bool = False,
        auto_probe_hwmon_drivers: bool = True,
        auto_load: bool = False,
        auto_enable_pwm_control: bool = True,
    ) -> None:
        super().__init__()
        self.project_path = project_path or Path(os.environ.get("USB9_FAN_CONTROL_PATH", DEFAULT_FAN_CONTROL_PATH))
        self.auto_grant_pwm_permissions = auto_grant_pwm_permissions
        self.auto_probe_hwmon_drivers = auto_probe_hwmon_drivers
        self.auto_enable_pwm_control = auto_enable_pwm_control
        self.monitor = None
        self.burner = None
        self._tabs: QTabWidget | None = None
        self._loaded = False
        self._loading = False
        self._profile_manager = None
        self._fans: list[object] = []
        self._sensors: list[object] = []
        self._fan_rows: dict[str, int] = {}
        self._latest_rpm: dict[str, int] = {}
        self._latest_pwm: dict[str, int] = {}
        self._latest_sensor_values: dict[str, float] = {}
        self._rpm_history: dict[str, deque[int]] = {}
        self._sensor_history: dict[str, deque[float]] = {}
        self._fan_cards: dict[str, FanStatusCard] = {}
        self._readonly_rpm_sensor_names: set[str] = set()
        self._readonly_rpm_sensor_aliases: dict[str, str] = {}
        self._fan_signal_name_aliases: dict[str, str] = {}
        self._fan_original_name_by_display: dict[str, str] = {}
        self._fan_label_overrides = _load_fan_channel_overrides()
        self._nvidia_smi_timer: QTimer | None = None
        self._nvidia_smi_fan_names: dict[int, str] = {}
        self._nvidia_smi_temperature_names: dict[int, str] = {}
        self._driver_probe_message = ""
        self._embedded_widgets: list[QWidget] = []
        self.quick_channel_role_buttons: list[QPushButton] = []

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(24, 22, 24, 24)
        self.layout.setSpacing(16)

        header_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("风扇控制")
        title.setObjectName("PageTitle")
        subtitle = QLabel("先以只读方式查看传感器和风扇状态，需要时再明确启用 PWM 控制")
        subtitle.setObjectName("PageSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_row.addLayout(title_box, 1)
        self.load_button = QPushButton("加载/扫描风扇")
        self.load_button.setObjectName("PrimaryButton")
        self.load_button.clicked.connect(self._load_button_clicked)
        header_row.addWidget(self.load_button)
        self.driver_probe_button = QPushButton("授权加载主板驱动")
        self.driver_probe_button.setToolTip("通过系统授权窗口加载 nct6775 / nct6683 / it87 等主板风扇 hwmon 驱动")
        self.driver_probe_button.clicked.connect(self.request_hwmon_driver_probe)
        header_row.addWidget(self.driver_probe_button)
        self.enable_control_button = QPushButton("启用 PWM 控制")
        self.enable_control_button.setObjectName("DangerButton")
        self.enable_control_button.setCheckable(True)
        self.enable_control_button.setEnabled(False)
        self.enable_control_button.clicked.connect(self.toggle_pwm_control)
        header_row.addWidget(self.enable_control_button)
        self.layout.addLayout(header_row)

        self.status_label = QLabel("尚未加载风扇监控。加载只读监控不会写入 PWM。")
        self.status_label.setObjectName("FieldHint")
        self.status_label.setWordWrap(True)
        self.layout.addWidget(self.status_label)

        summary = QGridLayout()
        summary.setSpacing(12)
        self.control_state_value = QLabel("未加载")
        self.fan_count_value = QLabel("--")
        self.sensor_count_value = QLabel("--")
        self.active_profile_value = QLabel("--")
        self.permission_value = QLabel(self._permission_summary_text())
        summary.addWidget(self._summary_card("控制状态", self.control_state_value), 0, 0)
        summary.addWidget(self._summary_card("风扇通道", self.fan_count_value), 0, 1)
        summary.addWidget(self._summary_card("传感器", self.sensor_count_value), 0, 2)
        summary.addWidget(self._summary_card("当前策略", self.active_profile_value), 0, 3)
        summary.addWidget(self._summary_card("PWM 权限", self.permission_value), 0, 4)
        self.layout.addLayout(summary)

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setObjectName("FanWorkspaceTabs")
        self.workspace_tabs.setMaximumHeight(560)
        self.workspace_tabs.currentChanged.connect(self._workspace_tab_changed)
        self.overview_tab = QWidget()
        self.overview_layout = QVBoxLayout(self.overview_tab)
        self.overview_layout.setContentsMargins(0, 0, 0, 0)
        self.overview_layout.setSpacing(12)

        self.control_tab = QWidget()
        self.control_layout = QVBoxLayout(self.control_tab)
        self.control_layout.setContentsMargins(0, 0, 0, 0)
        self.control_placeholder = self._tab_placeholder("加载后显示风扇曲线和手动调速控件")
        self.control_layout.addWidget(self.control_placeholder, 1)

        self.strategy_tab = QWidget()
        self.strategy_layout = QVBoxLayout(self.strategy_tab)
        self.strategy_layout.setContentsMargins(0, 0, 0, 0)
        self.strategy_layout.setSpacing(12)
        self.strategy_tabs = QTabWidget()
        self.strategy_quick_tab = QWidget()
        self.strategy_quick_layout = QVBoxLayout(self.strategy_quick_tab)
        self.strategy_quick_layout.setContentsMargins(0, 0, 0, 0)
        self.strategy_quick_layout.setSpacing(12)
        self.strategy_editor_tab = QWidget()
        self.strategy_editor_layout = QVBoxLayout(self.strategy_editor_tab)
        self.strategy_editor_layout.setContentsMargins(0, 0, 0, 0)
        self.strategy_placeholder = self._tab_placeholder("加载后显示完整策略编辑器")
        self.strategy_editor_layout.addWidget(self.strategy_placeholder, 1)
        self.strategy_tabs.addTab(self.strategy_quick_tab, "选择策略")
        self.strategy_tabs.addTab(self.strategy_editor_tab, "编辑曲线")
        self.strategy_tabs.currentChanged.connect(self._strategy_tab_changed)
        self.strategy_layout.addWidget(self.strategy_tabs, 1)

        self.permission_tab = QWidget()
        self.permission_layout = QVBoxLayout(self.permission_tab)
        self.permission_layout.setContentsMargins(0, 0, 0, 0)
        self.permission_layout.setSpacing(12)

        self.maintenance_tab = QWidget()
        self.maintenance_layout = QVBoxLayout(self.maintenance_tab)
        self.maintenance_layout.setContentsMargins(0, 0, 0, 0)
        self.maintenance_layout.setSpacing(12)
        self.maintenance_tabs = QTabWidget()
        self.maintenance_tabs.setObjectName("FanMaintenanceTabs")
        self.maintenance_tabs.currentChanged.connect(lambda _index: self._workspace_tab_changed(self.workspace_tabs.currentIndex()))
        self.maintenance_layout.addWidget(self.maintenance_tabs, 1)

        self.details_tab = QWidget()
        self.details_layout = QVBoxLayout(self.details_tab)
        self.details_layout.setContentsMargins(0, 0, 0, 0)
        self.details_layout.setSpacing(12)

        self.test_tab = QWidget()
        self.test_layout = QVBoxLayout(self.test_tab)
        self.test_layout.setContentsMargins(0, 0, 0, 0)
        self.stress_panel = StressTestPanel()
        self.test_layout.addWidget(self.stress_panel, 1)

        self.history_tab = QWidget()
        self.history_layout = QVBoxLayout(self.history_tab)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_placeholder = self._tab_placeholder("加载后显示历史数据曲线")
        self.history_layout.addWidget(self.history_placeholder, 1)

        self.visual_panel = QFrame()
        self.visual_panel.setObjectName("FanDashboardPanel")
        visual_layout = QGridLayout(self.visual_panel)
        visual_layout.setContentsMargins(16, 14, 16, 16)
        visual_layout.setHorizontalSpacing(14)
        visual_layout.setVerticalSpacing(12)
        visual_title = QLabel("实时风扇仪表盘")
        visual_title.setObjectName("SectionLabel")
        self.visual_status_label = QLabel("等待加载监控")
        self.visual_status_label.setObjectName("FieldHint")
        visual_layout.addWidget(visual_title, 0, 0)
        visual_layout.addWidget(self.visual_status_label, 0, 1, Qt.AlignmentFlag.AlignRight)
        self.fan_role_summary_label = QLabel("加载后会按 CPU 风扇、水泵/AIO、机箱风扇和未识别通道分组显示。")
        self.fan_role_summary_label.setObjectName("FanRoleSummary")
        self.fan_role_summary_label.setWordWrap(True)
        visual_layout.addWidget(self.fan_role_summary_label, 1, 0, 1, 2)
        self.fan_role_speed_label = QLabel("加载后会直接列出 CPU_FAN、CPU_OPT、水泵和机箱风扇的实时转速。")
        self.fan_role_speed_label.setObjectName("FanSpeedSummary")
        self.fan_role_speed_label.setWordWrap(True)
        visual_layout.addWidget(self.fan_role_speed_label, 2, 0, 1, 2)
        self.fan_identity_overview_label = QLabel("加载后显示每路物理接口、角色、转速和识别状态。")
        self.fan_identity_overview_label.setObjectName("FanIdentityOverview")
        self.fan_identity_overview_label.setWordWrap(True)
        visual_layout.addWidget(self.fan_identity_overview_label, 3, 0, 1, 2)

        identity_notice_row = QHBoxLayout()
        identity_notice_row.setSpacing(10)
        self.fan_identity_notice_label = QLabel("加载后会提示哪些 PWM/FAN 可以候选映射到 CPU_FAN、水泵和机箱风扇。")
        self.fan_identity_notice_label.setObjectName("FanIdentityNotice")
        self.fan_identity_notice_label.setWordWrap(True)
        self.confirm_all_candidates_button = QPushButton("确认全部候选")
        self.confirm_all_candidates_button.setEnabled(False)
        self.confirm_all_candidates_button.clicked.connect(self.confirm_all_candidate_channel_detections)
        identity_notice_row.addWidget(self.fan_identity_notice_label, 1)
        identity_notice_row.addWidget(self.confirm_all_candidates_button)
        visual_layout.addLayout(identity_notice_row, 4, 0, 1, 2)

        self.fan_identity_table = QTableWidget(0, 5)
        self.fan_identity_table.setObjectName("FanIdentityTable")
        self.fan_identity_table.setHorizontalHeaderLabels(["物理接口", "角色", "转速", "识别状态", "建议操作"])
        self.fan_identity_table.verticalHeader().setVisible(False)
        self.fan_identity_table.verticalHeader().setDefaultSectionSize(30)
        self.fan_identity_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.fan_identity_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.fan_identity_table.setAlternatingRowColors(True)
        self.fan_identity_table.setMinimumHeight(96)
        self.fan_identity_table.setMaximumHeight(138)
        self.fan_identity_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.fan_identity_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.fan_identity_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.fan_identity_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.fan_identity_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        visual_layout.addWidget(self.fan_identity_table, 5, 0, 1, 2)

        self.fan_role_metric_cards: dict[str, FanRoleMetricCard] = {}
        role_metrics = QWidget()
        role_metrics.setObjectName("FanRoleMetrics")
        role_metrics_layout = QGridLayout(role_metrics)
        role_metrics_layout.setContentsMargins(0, 0, 0, 0)
        role_metrics_layout.setHorizontalSpacing(10)
        role_metrics_layout.setVerticalSpacing(10)
        for column, role in enumerate(("CPU 风扇", "水泵/AIO", "机箱风扇", "GPU 风扇")):
            card = FanRoleMetricCard(role)
            self.fan_role_metric_cards[role] = card
            role_metrics_layout.addWidget(card, 0, column)
            role_metrics_layout.setColumnStretch(column, 1)
        visual_layout.addWidget(role_metrics, 6, 0, 1, 2)

        self.fan_cards_container = QWidget()
        self.fan_cards_container.setObjectName("FanCardsContainer")
        self.fan_cards_layout = QGridLayout(self.fan_cards_container)
        self.fan_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.fan_cards_layout.setHorizontalSpacing(10)
        self.fan_cards_layout.setVerticalSpacing(10)
        self.fan_cards_empty_label = QLabel("加载后显示每个通道的转速、PWM 和状态")
        self.fan_cards_empty_label.setObjectName("FieldHint")
        self.fan_cards_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fan_cards_layout.addWidget(self.fan_cards_empty_label, 0, 0)
        visual_layout.addWidget(self.fan_cards_container, 7, 0, 1, 2)

        self.rpm_chart = FanTrendChart("RPM 实时趋势", "RPM", default_max=1800)
        self.temperature_chart = FanTrendChart("温度趋势", "°C", default_max=90)
        visual_layout.addWidget(self.rpm_chart, 8, 0)
        visual_layout.addWidget(self.temperature_chart, 8, 1)
        visual_layout.setColumnStretch(0, 1)
        visual_layout.setColumnStretch(1, 1)
        visual_layout.setRowStretch(0, 0)
        visual_layout.setRowStretch(1, 0)
        visual_layout.setRowStretch(2, 0)
        visual_layout.setRowStretch(3, 0)
        visual_layout.setRowStretch(4, 0)
        visual_layout.setRowStretch(5, 0)
        visual_layout.setRowStretch(6, 0)
        visual_layout.setRowStretch(7, 0)
        visual_layout.setRowStretch(8, 1)
        self.overview_layout.addWidget(self.visual_panel)
        self.overview_layout.addStretch(1)

        strategy_panel = QFrame()
        strategy_panel.setObjectName("MetricCard")
        strategy_layout = QGridLayout(strategy_panel)
        strategy_layout.setContentsMargins(14, 12, 14, 12)
        strategy_layout.setHorizontalSpacing(12)
        strategy_layout.setVerticalSpacing(8)
        strategy_title = QLabel("风扇策略")
        strategy_title.setObjectName("SectionLabel")
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(180)
        self.profile_combo.currentIndexChanged.connect(self._preview_profile_selection)
        self.apply_profile_button = QPushButton("设为当前策略")
        self.apply_profile_button.clicked.connect(self.apply_selected_profile)
        strategy_hint = QLabel("策略只在 PWM 启用后写入，当前曲线仍由旧风扇后端执行。")
        strategy_hint.setObjectName("FieldHint")
        strategy_hint.setWordWrap(True)
        strategy_layout.addWidget(strategy_title, 0, 0)
        strategy_layout.addWidget(self.profile_combo, 0, 1)
        strategy_layout.addWidget(self.apply_profile_button, 0, 2)
        strategy_layout.addWidget(strategy_hint, 1, 0, 1, 3)
        self.strategy_quick_layout.addWidget(strategy_panel)
        self.strategy_quick_layout.addStretch(1)

        permission_panel = QFrame()
        permission_panel.setObjectName("MetricCard")
        permission_layout = QGridLayout(permission_panel)
        permission_layout.setContentsMargins(14, 12, 14, 12)
        permission_layout.setHorizontalSpacing(10)
        permission_layout.setVerticalSpacing(8)
        permission_title = QLabel("PWM 权限管理")
        permission_title.setObjectName("SectionLabel")
        permission_hint = QLabel("点击“请求系统权限”会调用 pkexec，并由系统弹窗要求输入密码。")
        permission_hint.setObjectName("FieldHint")
        permission_hint.setWordWrap(True)
        self.refresh_permissions_button = QPushButton("刷新权限")
        self.refresh_permissions_button.clicked.connect(self.refresh_pwm_permissions)
        self.grant_permissions_button = QPushButton("请求系统权限")
        self.grant_permissions_button.clicked.connect(self.grant_pwm_permissions)
        self.copy_permission_commands_button = QPushButton("复制授权命令")
        self.copy_permission_commands_button.clicked.connect(self.copy_pwm_permission_commands)
        self.diagnose_permissions_button = QPushButton("权限诊断")
        self.diagnose_permissions_button.clicked.connect(self.run_permission_diagnostics)
        self.repair_profile_permissions_button = QPushButton("修复策略权限")
        self.repair_profile_permissions_button.clicked.connect(self.repair_profile_config_permissions)
        self.copy_udev_rules_button = QPushButton("复制长期规则")
        self.copy_udev_rules_button.clicked.connect(self.copy_permanent_permission_rules)
        self.permission_detail_text = QTextEdit()
        self.permission_detail_text.setReadOnly(True)
        self.permission_detail_text.setMinimumHeight(104)
        self.permission_detail_text.setMaximumHeight(170)
        self.permission_wizard_text = QTextEdit()
        self.permission_wizard_text.setReadOnly(True)
        self.permission_wizard_text.setMinimumHeight(96)
        self.permission_wizard_text.setMaximumHeight(150)
        permission_layout.addWidget(permission_title, 0, 0)
        permission_layout.addWidget(self.refresh_permissions_button, 0, 1)
        permission_layout.addWidget(self.grant_permissions_button, 0, 2)
        permission_layout.addWidget(self.copy_permission_commands_button, 0, 3)
        permission_layout.addWidget(self.diagnose_permissions_button, 1, 1)
        permission_layout.addWidget(self.copy_udev_rules_button, 1, 2)
        permission_layout.addWidget(self.repair_profile_permissions_button, 1, 3)
        permission_layout.addWidget(permission_hint, 2, 0, 1, 4)
        permission_layout.addWidget(self.permission_detail_text, 3, 0, 1, 4)
        permission_layout.addWidget(self.permission_wizard_text, 4, 0, 1, 4)
        self.permission_layout.addWidget(permission_panel)
        self.permission_layout.addStretch(1)
        self._update_permission_summary()
        self.run_permission_diagnostics(initial=True)

        details_panel = QFrame()
        details_panel.setObjectName("MetricCard")
        details_layout = QVBoxLayout(details_panel)
        details_layout.setContentsMargins(14, 12, 14, 14)
        details_layout.setSpacing(8)
        details_title = QLabel("通道明细")
        details_title.setObjectName("SectionLabel")
        identity_layout = QGridLayout()
        identity_layout.setHorizontalSpacing(10)
        identity_layout.setVerticalSpacing(8)
        identity_hint = QLabel("自动识别依赖主板 hwmon 标签；识别不准时可以手动给 PWM/FAN 通道命名。")
        identity_hint.setObjectName("FieldHint")
        identity_hint.setWordWrap(True)
        self.channel_label_combo = QComboBox()
        self.channel_label_combo.currentIndexChanged.connect(self._channel_label_selection_changed)
        self.channel_role_combo = QComboBox()
        for label, value in FAN_ROLE_OPTIONS:
            self.channel_role_combo.addItem(label, value)
        self.channel_header_combo = QComboBox()
        for label, value in FAN_HEADER_OPTIONS:
            self.channel_header_combo.addItem(label, value)
        self.channel_alias_input = QLineEdit()
        self.channel_alias_input.setPlaceholderText("例如：水泵 / 顶部冷排 / 后置排风")
        self.save_channel_label_button = QPushButton("保存标签")
        self.save_channel_label_button.clicked.connect(self.save_channel_label_override)
        self.confirm_channel_label_button = QPushButton("确认当前识别")
        self.confirm_channel_label_button.clicked.connect(self.confirm_selected_channel_detection)
        self.clear_channel_label_button = QPushButton("清除标签")
        self.clear_channel_label_button.clicked.connect(self.clear_channel_label_override)
        self.identify_channel_button = QPushButton("识别选中通道")
        self.identify_channel_button.clicked.connect(self.identify_selected_fan_channel)
        quick_role_layout = QHBoxLayout()
        quick_role_layout.setSpacing(6)
        for text, role, header, alias in FAN_ROLE_QUICK_LABELS:
            button = QPushButton(text)
            button.clicked.connect(
                lambda _checked=False, current_role=role, current_header=header, current_alias=alias: (
                    self.apply_quick_channel_label(role=current_role, header=current_header, alias=current_alias)
                )
            )
            quick_role_layout.addWidget(button)
            self.quick_channel_role_buttons.append(button)
        quick_role_layout.addStretch(1)
        self.channel_evidence_label = QLabel("加载后选择通道可查看 hwmon label、pwm/fan 路径和识别依据。")
        self.channel_evidence_label.setObjectName("FieldHint")
        self.channel_evidence_label.setWordWrap(True)
        identity_layout.addWidget(identity_hint, 0, 0, 1, 4)
        identity_layout.addWidget(QLabel("通道"), 1, 0)
        identity_layout.addWidget(self.channel_label_combo, 1, 1)
        identity_layout.addWidget(QLabel("角色"), 1, 2)
        identity_layout.addWidget(self.channel_role_combo, 1, 3)
        identity_layout.addWidget(QLabel("物理接口"), 2, 0)
        identity_layout.addWidget(self.channel_header_combo, 2, 1)
        identity_layout.addWidget(QLabel("别名"), 2, 2)
        identity_layout.addWidget(self.channel_alias_input, 2, 3)
        identity_layout.addWidget(self.save_channel_label_button, 3, 0)
        identity_layout.addWidget(self.confirm_channel_label_button, 3, 1)
        identity_layout.addWidget(self.clear_channel_label_button, 3, 2)
        identity_layout.addWidget(self.identify_channel_button, 3, 3)
        identity_layout.addWidget(QLabel("快速标记"), 4, 0)
        identity_layout.addLayout(quick_role_layout, 4, 1, 1, 3)
        identity_layout.addWidget(self.channel_evidence_label, 5, 0, 1, 4)
        identity_layout.setColumnStretch(1, 1)
        self.fan_table = QTableWidget(0, 8)
        self.fan_table.setObjectName("FanChannelTable")
        self.fan_table.setHorizontalHeaderLabels(["名称", "角色", "接口", "关联传感器", "转速", "输出", "来源", "状态"])
        self.fan_table.verticalHeader().setVisible(False)
        self.fan_table.verticalHeader().setDefaultSectionSize(34)
        self.fan_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.fan_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.fan_table.setAlternatingRowColors(True)
        self.fan_table.itemSelectionChanged.connect(self._fan_table_selection_changed)
        self.fan_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4, 5, 6, 7):
            self.fan_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.fan_table.setMinimumHeight(170)
        self.fan_table.setMaximumHeight(280)
        self.fan_table_hint = QLabel("加载只读监控后，这里会显示每个风扇的 RPM、PWM、来源和写入状态。")
        self.fan_table_hint.setObjectName("FieldHint")
        self.fan_table_hint.setWordWrap(True)
        details_layout.addWidget(details_title)
        details_layout.addLayout(identity_layout)
        details_layout.addWidget(self.fan_table_hint)
        details_layout.addWidget(self.fan_table)
        self.details_layout.addWidget(details_panel, 1)
        self._refresh_channel_label_editor()

        self.maintenance_tabs.addTab(self.control_tab, "调速")
        self.maintenance_tabs.addTab(self.permission_tab, "权限")
        self.maintenance_tabs.addTab(self.details_tab, "明细")
        self.maintenance_tabs.addTab(self.history_tab, "历史")
        self.maintenance_tabs.addTab(self.test_tab, "压力测试")
        self.workspace_tabs.addTab(self.overview_tab, "总览")
        self.workspace_tabs.addTab(self.strategy_tab, "策略")
        self.workspace_tabs.addTab(self.maintenance_tab, "维护")
        self._workspace_tab_changed(0)
        self.layout.addWidget(self.workspace_tabs)
        self.layout.addStretch(1)
        if auto_load:
            QTimer.singleShot(0, lambda: self.load_fan_control(interactive_driver_probe=False))

    def reload_fan_control(self, *, interactive_driver_probe: bool = False) -> None:
        if self._loading:
            return
        if self._loaded or self.monitor is not None:
            self.release()
        self.load_fan_control(interactive_driver_probe=interactive_driver_probe)

    def _load_button_clicked(self) -> None:
        if self._pwm_action_requests_driver_probe():
            self.request_hwmon_driver_probe()
            return
        self.reload_fan_control(interactive_driver_probe=True)

    def load_fan_control(self, *, interactive_driver_probe: bool = False) -> None:
        if self._loaded or self._loading:
            return
        self._loading = True
        self._sync_load_action_button()
        self.status_changed.emit("加载中")
        try:
            if self.auto_probe_hwmon_drivers:
                self._ensure_fan_hwmon_drivers(interactive=interactive_driver_probe)
            modules = self._import_modules()
            self._build_embedded_ui(modules)
        except Exception as error:
            self.release()
            self.status_label.setText(f"加载失败：{error}")
            self._loading = False
            self._sync_load_action_button()
            self.status_changed.emit("加载失败")
            return
        self._loading = False
        self._loaded = True
        self._sync_load_action_button()
        self.enable_control_button.setEnabled(True)
        self.control_state_value.setText("只读")
        self._refresh_summary()
        self._refresh_fan_table()
        self._update_permission_summary()
        permission_grant_attempted = False
        if self.auto_grant_pwm_permissions:
            permission_grant_attempted = bool(self._permission_grant_commands())
            self.grant_pwm_permissions(silent=True)
        if not self._fans:
            self.control_state_value.setText("未发现风扇")
            self.status_label.setText(self._missing_fan_status_text())
            self._refresh_summary()
            self._refresh_fan_table()
            self._update_permission_summary()
            self._update_fan_table_hint()
            self.run_permission_diagnostics(initial=True)
            self._emit_status_changed()
            return
        if not self._has_controllable_fans():
            self.status_label.setText(self._readonly_only_status_text())
            self._refresh_summary()
            self._refresh_fan_table()
            return
        if self.auto_enable_pwm_control:
            self.enable_pwm_control()
            return
        if not permission_grant_attempted:
            self.status_label.setText("风扇监控已加载：当前为只读模式，不会写入 PWM")
        self._emit_status_changed()

    def request_hwmon_driver_probe(self) -> None:
        if self._loading:
            return
        self.driver_probe_button.setEnabled(False)
        self.status_label.setText("正在请求系统权限加载主板风扇驱动，请在系统弹窗中输入密码...")
        QApplication.processEvents()
        ok, message = self._load_fan_hwmon_drivers(interactive=True)
        self._driver_probe_message = message
        self.driver_probe_button.setEnabled(True)
        self._update_permission_summary()
        if not ok:
            self.status_label.setText(f"主板风扇驱动加载失败：{message}")
            self._update_fan_table_hint()
            self._sync_load_action_button()
            self._sync_pwm_action_button()
            return

        self.status_label.setText("主板风扇驱动加载请求已完成，正在重新扫描风扇通道...")
        QApplication.processEvents()
        previous_auto_probe = self.auto_probe_hwmon_drivers
        self.auto_probe_hwmon_drivers = False
        try:
            self.reload_fan_control()
        finally:
            self.auto_probe_hwmon_drivers = previous_auto_probe
        if not self._fans:
            self.status_label.setText(self._missing_fan_status_text())
            self._sync_pwm_action_button()

    def toggle_pwm_control(self) -> None:
        if self.monitor is None:
            self.enable_control_button.setChecked(False)
            self.status_label.setText("请先加载风扇监控")
            return
        if self._pwm_action_requests_driver_probe():
            self.enable_control_button.setChecked(False)
            self.request_hwmon_driver_probe()
            return
        if not self._fans:
            self.enable_control_button.setChecked(False)
            self.enable_control_button.setEnabled(False)
            self.status_label.setText("未发现风扇通道：当前系统没有暴露 fan/pwm hwmon 文件")
            self._update_fan_table_hint()
            return
        if not self._has_controllable_fans():
            self.enable_control_button.setChecked(False)
            self.enable_control_button.setEnabled(False)
            self.status_label.setText("当前只有只读风扇通道，没有可写 PWM 控制")
            self._refresh_fan_table_status()
            return
        set_control_enabled = getattr(self.monitor, "set_control_enabled", None)
        if not callable(set_control_enabled):
            self.enable_control_button.setChecked(False)
            self.status_label.setText("当前风扇模块不支持运行时启用 PWM 控制")
            return
        enabled = self.enable_control_button.isChecked()
        if enabled and not self._ensure_pwm_permissions_for_enable():
            return
        try:
            set_control_enabled(enabled)
        except Exception as error:
            self.enable_control_button.setChecked(not enabled)
            self.status_label.setText(f"PWM 状态切换失败：{error}")
            return
        self._set_control_state(enabled)
        self._refresh_fan_table_status()
        if enabled and self.permission_value.text() == "需 sudo/udev":
            self.status_label.setText("PWM 控制已启用；如果风扇不响应，请为 hwmon PWM 文件配置 sudo/udev 写权限")
        else:
            self.status_label.setText("PWM 控制已启用：正在写入当前曲线和配置" if enabled else "PWM 写入已暂停：当前为只读模式")
        self._emit_status_changed()

    def enable_pwm_control(self) -> None:
        self.enable_control_button.setChecked(True)
        self.toggle_pwm_control()

    def _ensure_pwm_permissions_for_enable(self) -> bool:
        self._update_permission_summary()
        if self._can_enable_pwm_control():
            return True
        if not self._permission_grant_commands():
            self.enable_control_button.setChecked(False)
            self.status_label.setText("PWM 文件不可写：没有可自动授权的 sysfs 文件，可复制授权命令手动执行")
            return False
        if not self.grant_pwm_permissions(silent=False):
            self.enable_control_button.setChecked(False)
            return False
        self._update_permission_summary()
        if self._can_enable_pwm_control():
            return True
        self.enable_control_button.setChecked(False)
        self.status_label.setText("PWM 授权命令已执行，但文件仍不可写；请刷新权限或复制授权命令手动执行")
        return False

    def release(self) -> None:
        self._stop_nvidia_smi_fallback()
        if self.burner is not None:
            self.burner.stop_all()
        if self.monitor is not None:
            self.monitor.stop()
        self.burner = None
        self.monitor = None
        self._loaded = False
        self._loading = False
        self._profile_manager = None
        self._fans = []
        self._sensors = []
        self._fan_rows = {}
        self._latest_rpm = {}
        self._latest_pwm = {}
        self._latest_sensor_values = {}
        self._rpm_history = {}
        self._sensor_history = {}
        self._readonly_rpm_sensor_names = set()
        self._readonly_rpm_sensor_aliases = {}
        self._fan_signal_name_aliases = {}
        self._fan_original_name_by_display = {}
        self._nvidia_smi_fan_names = {}
        self._nvidia_smi_temperature_names = {}
        self._clear_embedded_widgets()
        self._clear_fan_cards()
        self.rpm_chart.clear()
        self.temperature_chart.clear()
        self.visual_status_label.setText("等待加载监控")
        self.fan_role_summary_label.setText("加载后会按 CPU 风扇、水泵/AIO、机箱风扇和未识别通道分组显示。")
        self.fan_role_speed_label.setText("加载后会直接列出 CPU_FAN、CPU_OPT、水泵和机箱风扇的实时转速。")
        self.fan_identity_overview_label.setText("加载后显示每路物理接口、角色、转速和识别状态。")
        if hasattr(self, "fan_identity_notice_label"):
            self.fan_identity_notice_label.setText("加载后会提示哪些 PWM/FAN 可以候选映射到 CPU_FAN、水泵和机箱风扇。")
        if hasattr(self, "confirm_all_candidates_button"):
            self.confirm_all_candidates_button.setEnabled(False)
        if hasattr(self, "fan_identity_table"):
            self.fan_identity_table.setRowCount(0)
        self._refresh_fan_role_metrics()
        self.fan_cards_empty_label.setText("加载后显示每个通道的转速、PWM 和状态")
        self.fan_cards_empty_label.setVisible(True)
        self.fan_cards_layout.addWidget(self.fan_cards_empty_label, 0, 0)
        self.control_placeholder.setVisible(True)
        self.strategy_placeholder.setVisible(True)
        self.history_placeholder.setVisible(True)
        self.strategy_tabs.setCurrentWidget(self.strategy_quick_tab)
        self.stress_panel.clear_burner()
        self.workspace_tabs.setCurrentWidget(self.overview_tab)
        if self._tabs is not None:
            self.layout.removeWidget(self._tabs)
            self._tabs.deleteLater()
            self._tabs = None
        self.fan_table.setRowCount(0)
        self.fan_table_hint.setVisible(True)
        self.fan_table_hint.setText("加载只读监控后，这里会显示每个风扇的 RPM、PWM、来源和写入状态。")
        self._refresh_channel_label_editor()
        self.driver_probe_button.setEnabled(True)
        self.enable_control_button.setChecked(False)
        self.enable_control_button.setEnabled(False)
        self.enable_control_button.setText("启用 PWM 控制")
        self.control_state_value.setText("未加载")
        self.fan_count_value.setText("--")
        self.sensor_count_value.setText("--")
        self.active_profile_value.setText("--")
        self.permission_value.setText(self._permission_summary_text())
        self._sync_load_action_button()
        self._emit_status_changed()

    def _workspace_tab_changed(self, index: int) -> None:
        if not hasattr(self, "workspace_tabs"):
            return
        widget = self.workspace_tabs.widget(index)
        if widget is self.overview_tab:
            height = 640
        elif widget is self.strategy_tab:
            height = 680 if self.strategy_tabs.currentWidget() is self.strategy_editor_tab else 205
        elif widget is self.maintenance_tab:
            active = self.maintenance_tabs.currentWidget()
            if active is self.control_tab or active is self.history_tab:
                height = 560
            elif active is self.details_tab:
                height = 430
            elif active is self.test_tab:
                height = 330
            else:
                height = 430
        else:
            height = 230
        self.workspace_tabs.setMaximumHeight(height)

    def _strategy_tab_changed(self, _index: int) -> None:
        if hasattr(self, "workspace_tabs") and self.workspace_tabs.currentWidget() is self.strategy_tab:
            self._workspace_tab_changed(self.workspace_tabs.currentIndex())

    def home_status_text(self) -> str:
        if self._loading:
            return "加载中"
        if not self._loaded:
            return "未加载"
        if not self._fans:
            if not self._system_has_fan_pwm_files():
                return "需要驱动授权"
            return "未发现风扇"
        state = "PWM 已启用" if self._control_is_enabled() else "只读监控"
        active_count = sum(1 for fan in self._fans if self._fan_has_live_speed(str(getattr(fan, "name", ""))))
        if not self._has_controllable_fans() and not self._mainboard_fan_interface_visible():
            return f"主板未暴露\n{active_count}/{len(self._fans)} 只读通道"
        return f"{len(self._fans)} 通道\n{active_count} 有转速 · {state}"

    def _emit_status_changed(self) -> None:
        self.status_changed.emit(self.home_status_text())

    def _import_modules(self) -> dict[str, ModuleType | None]:
        if not self.project_path.is_dir():
            raise FileNotFoundError(f"找不到风扇控制项目：{self.project_path}")
        project_root = str(self.project_path)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        import importlib

        modules: dict[str, ModuleType | None] = {
            "monitor": importlib.import_module("src.core.monitor"),
            "burner": importlib.import_module("src.core.burner"),
            "profile": importlib.import_module("src.core.profile"),
            "fan_engine": importlib.import_module("src.core.fan_engine"),
            "fan_control": importlib.import_module("src.ui.fan_control"),
            "profile_editor": importlib.import_module("src.ui.profile_editor"),
            "curve_editor": importlib.import_module("src.ui.widgets.curve_editor"),
            "fan_slider": importlib.import_module("src.ui.widgets.fan_slider"),
        }
        for key, module_name in (
            ("dashboard", "src.ui.dashboard"),
            ("history_view", "src.ui.history_view"),
        ):
            try:
                modules[key] = importlib.import_module(module_name)
            except ModuleNotFoundError as error:
                if error.name != "PySide6.QtCharts":
                    raise
                modules[key] = None
        return modules

    def _ensure_fan_hwmon_drivers(self, *, interactive: bool = False) -> None:
        if self._system_has_fan_pwm_files():
            return
        if not interactive:
            ready, message = self._noninteractive_driver_probe_ready()
            if not ready:
                self._driver_probe_message = message
                return
        if interactive:
            self.status_label.setText("正在请求系统权限加载主板风扇驱动，请在系统弹窗中输入密码...")
            QApplication.processEvents()
        ok, message = self._load_fan_hwmon_drivers(interactive=interactive)
        self._driver_probe_message = message
        if ok and self._system_has_fan_pwm_files():
            self.status_label.setText("已加载主板风扇 hwmon 驱动，正在读取风扇通道...")
        elif interactive:
            self.status_label.setText(f"主板风扇驱动加载后仍未发现风扇节点：{message}")

    def _noninteractive_driver_probe_ready(self) -> tuple[bool, str]:
        if os.geteuid() == 0:
            return True, "root 会话可自动加载主板风扇驱动"
        sudo = shutil.which("sudo")
        if not sudo:
            return False, "自动加载已跳过：未找到 sudo；请点击“授权加载主板驱动”触发系统授权。"
        try:
            result = subprocess.run(
                [sudo, "-n", "true"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except subprocess.TimeoutExpired:
            return False, "自动加载已跳过：sudo 非交互检查超时；请点击“授权加载主板驱动”。"
        except OSError as error:
            return False, f"自动加载已跳过：无法运行 sudo（{error}）；请点击“授权加载主板驱动”。"
        if result.returncode == 0:
            return True, "sudo 免密可用"
        lines = (result.stderr or result.stdout or "").strip().splitlines()
        suffix = f"（{lines[-1]}）" if lines else ""
        return False, f"自动加载已跳过：当前会话没有免密 sudo{suffix}；请点击“授权加载主板驱动”触发系统授权。"

    def _system_has_fan_pwm_files(self) -> bool:
        root = Path("/sys/class/hwmon")
        if not root.is_dir():
            return False
        for hwmon in root.glob("hwmon*"):
            try:
                if any(self._is_pwm_control_file(path.name) for path in hwmon.iterdir()):
                    return True
            except OSError:
                continue
        return False

    def _mainboard_fan_interface_visible(self) -> bool:
        if self._system_has_fan_pwm_files():
            return True
        for fan in self._fans:
            rpm_input = str(getattr(fan, "rpm_input", "") or "")
            pwm_path = str(getattr(fan, "pwm_path", "") or "")
            if "/sys/class/hwmon/" in rpm_input and "/fan" in rpm_input:
                return True
            if pwm_path.startswith("readonly:/sys/class/hwmon/") and "/fan" in pwm_path:
                return True
        return False

    def _is_pwm_control_file(self, name: str) -> bool:
        return name.startswith("pwm") and name[-1:].isdigit()

    def _is_fan_control_file(self, name: str) -> bool:
        return self._is_pwm_control_file(name)

    def _load_fan_hwmon_drivers(self, *, interactive: bool = False) -> tuple[bool, str]:
        commands = self._fan_hwmon_probe_shell(include_forced_probes=interactive)
        if interactive:
            ok, message = self._run_privileged_shell(commands, timeout=120, interactive=True)
        else:
            ok, message = self._run_privileged_shell(commands, timeout=60)
        message = self._compact_driver_probe_message(message)
        if not ok:
            return False, message
        if not self._system_has_fan_pwm_files():
            return False, f"驱动加载命令已执行，但 /sys/class/hwmon 仍未暴露 fan/pwm 文件；{message}"
        return True, message

    def _fan_hwmon_probe_shell(self, *, include_forced_probes: bool) -> str:
        module_list = " ".join(shlex.quote(module) for module in FAN_HWMON_MODULE_CANDIDATES)
        commands = [
            "modprobe_cmd='';",
            "for candidate in /sbin/modprobe /usr/sbin/modprobe /bin/modprobe /usr/bin/modprobe; do",
            "[ -x \"$candidate\" ] && modprobe_cmd=\"$candidate\" && break;",
            "done;",
            "if [ -z \"$modprobe_cmd\" ]; then echo '未找到 modprobe'; exit 0; fi;",
            "has_pwm_control() {",
            "for path in /sys/class/hwmon/hwmon*/pwm[0-9]; do",
            "[ -e \"$path\" ] && return 0;",
            "done;",
            "return 1;",
            "};",
            f"for module in {module_list}; do",
            "output=\"$($modprobe_cmd \"$module\" 2>&1)\"; rc=$?;",
            "if [ \"$rc\" -eq 0 ]; then echo \"$module=已加载\";",
            "else echo \"$module=失败:${output:-退出码 $rc}\"; fi;",
            "if has_pwm_control; then echo 'pwm-control=已暴露'; exit 0; fi;",
            "done;",
        ]
        if include_forced_probes:
            commands.extend(self._forced_fan_hwmon_probe_shell_lines())
        return " ".join(commands)

    def _forced_fan_hwmon_probe_shell_lines(self) -> list[str]:
        lines: list[str] = ["if ! has_pwm_control; then"]
        for module, options, label in FORCED_FAN_HWMON_PROBES:
            option_text = " ".join(shlex.quote(option) for option in options)
            module_text = shlex.quote(module)
            lines.extend(
                [
                    f"echo {shlex.quote(label + '：尝试 ' + module + ' ' + ' '.join(options))};",
                    f"remove_output=\"$($modprobe_cmd -r {module_text} 2>&1)\"; remove_rc=$?;",
                    "if [ \"$remove_rc\" -ne 0 ] && [ -n \"$remove_output\" ]; then "
                    f"echo {shlex.quote(module + '=卸载跳过')}:$remove_output; fi;",
                    f"output=\"$($modprobe_cmd {module_text} {option_text} 2>&1)\"; rc=$?;",
                    f"if [ \"$rc\" -eq 0 ]; then echo {shlex.quote(module + ' force=1=已加载')};",
                    f"else echo {shlex.quote(module + ' force=1=失败')}:${{output:-退出码 $rc}}; fi;",
                    "if has_pwm_control; then echo 'pwm-control=已暴露'; exit 0; fi;",
                ]
            )
        lines.append("fi;")
        return lines

    def _compact_driver_probe_message(self, message: str) -> str:
        lines = [line.strip() for line in message.splitlines() if line.strip()]
        if not lines:
            return "ok"
        interesting = [
            line
            for line in lines
            if not line.endswith("=失败:")
        ]
        if not interesting:
            interesting = lines
        return "；".join(interesting[:8])

    def _build_embedded_ui(self, modules: dict[str, ModuleType | None]) -> None:
        monitor = modules["monitor"].Monitor()
        burner = modules["burner"].Burner()
        try:
            self._start_monitor_read_only(monitor)
            self.monitor = monitor
            self.burner = burner
            self._profile_manager = monitor.profiles
            self._sensors = monitor.sensors
            self._fans = self._display_fans_from_monitor(monitor.fans, self._sensors)

            fan_control = EmbeddedFanControlPanel(modules["fan_slider"].FanSlider)
            profile_editor = EmbeddedProfileEditor(
                monitor.profiles,
                modules["curve_editor"].CurveEditor,
                modules["fan_engine"].FanCurve,
                modules["profile"].Profile,
            )
            history_view = modules["history_view"].HistoryView(monitor.history) if modules["history_view"] is not None else _FallbackHistoryView()

            monitor.sensor_updated.connect(self._update_sensor_value)
            monitor.fan_rpm_updated.connect(
                lambda name, rpm: fan_control.update_fan_rpm(self._display_fan_name(name), rpm)
            )
            monitor.fan_rpm_updated.connect(self._update_fan_rpm)
            monitor.fan_pwm_updated.connect(self._update_fan_pwm)
            if hasattr(monitor, "pwm_error"):
                monitor.pwm_error.connect(self.status_label.setText)
            monitor.sensor_updated.connect(profile_editor.update_sensor)
            monitor.fan_rpm_updated.connect(
                lambda name, rpm: profile_editor.update_fan_rpm(self._display_fan_name(name), rpm)
            )
            monitor.fan_pwm_updated.connect(
                lambda name, pwm: profile_editor.update_fan_pwm(self._display_fan_name(name), pwm)
            )
            monitor.history_tick.connect(history_view.refresh_sensors)
            profile_editor.profile_activated.connect(monitor._load_active_profile)

            self._start_nvidia_smi_fallback_if_needed()
            self._populate_fan_control(fan_control, monitor)
            self._refresh_profile_options()
            self._refresh_summary()
            self._refresh_fan_table()

            self._mount_embedded_widgets(
                fan_control=fan_control,
                profile_editor=profile_editor,
                history_view=history_view,
            )
        except Exception:
            burner.stop_all()
            monitor.stop()
            raise

    def _mount_embedded_widgets(self, *, fan_control, profile_editor, history_view) -> None:  # noqa: ANN001
        self._clear_embedded_widgets()
        self.control_placeholder.setVisible(False)
        self.strategy_placeholder.setVisible(False)
        self.history_placeholder.setVisible(False)

        self.control_layout.addWidget(fan_control, 1)
        self.strategy_editor_layout.addWidget(profile_editor, 1)
        self.history_layout.addWidget(history_view, 1)
        self.stress_panel.set_burner(self.burner)
        self._embedded_widgets = [fan_control, profile_editor, history_view]
        self.workspace_tabs.setCurrentWidget(self.overview_tab)
        self._tabs = None

    def _prepare_profile_editor_for_embedding(self, profile_editor) -> None:  # noqa: ANN001
        profile_editor.setMinimumHeight(620)
        profile_list = getattr(profile_editor, "_profile_list", None)
        if profile_list is not None:
            profile_list.setMaximumHeight(58)
        for attr in ("_cpu_editor", "_gpu_editor"):
            editor = getattr(profile_editor, attr, None)
            if editor is not None:
                editor.setMinimumSize(360, 220)
                editor.setMaximumHeight(240)
        status_frame = getattr(profile_editor, "_status_frame", None)
        if status_frame is not None:
            status_frame.setStyleSheet("background: #1a1b1c; border: 1px solid #33363a; border-radius: 6px;")
        for attr in ("_cpu_temp_label", "_gpu_temp_label", "_fan_rpm_label"):
            label = getattr(profile_editor, attr, None)
            if label is not None:
                label.setStyleSheet("color: #e8e9e6; font-size: 13px; font-weight: 800; padding: 4px 10px;")

    def _clear_embedded_widgets(self) -> None:
        for widget in self._embedded_widgets:
            parent = widget.parentWidget()
            layout = parent.layout() if parent is not None else None
            if layout is not None:
                layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._embedded_widgets = []

    def _connect_burn_buttons(self, dashboard) -> None:  # noqa: ANN001
        if self.burner is None:
            return

        def toggle(burn_type: str) -> None:
            if self.burner.running.get(burn_type):
                self.burner.stop(burn_type)
            else:
                self.burner.start(burn_type)

        for attr, burn_type in (("_btn_cpu", "cpu"), ("_btn_fpu", "fpu"), ("_btn_gpu", "gpu")):
            button = getattr(dashboard, attr, None)
            if button is not None:
                button.clicked.connect(lambda _checked=False, kind=burn_type: toggle(kind))
        stop_button = getattr(dashboard, "_btn_stop", None)
        if stop_button is not None:
            stop_button.clicked.connect(self.burner.stop_all)

    def _populate_fan_control(self, fan_control, monitor) -> None:  # noqa: ANN001
        if getattr(fan_control, "_sliders", None):
            return
        temp_sensors = [sensor for sensor in monitor.sensors if getattr(sensor, "unit", "") == "°C"]
        fan_control.populate_fans(self._controllable_display_fans(), temp_sensors)
        self._decorate_embedded_fan_control(fan_control)
        self._connect_display_fan_control(fan_control, monitor)

    def _controllable_display_fans(self) -> list[object]:
        return [fan for fan in self._fans if not self._fan_is_readonly(fan)]

    def _decorate_embedded_fan_control(self, fan_control) -> None:  # noqa: ANN001
        sliders = getattr(fan_control, "_sliders", {})
        for fan in self._fans:
            display_name = str(getattr(fan, "name", ""))
            slider = sliders.get(display_name)
            if slider is None:
                continue
            role = str(getattr(fan, "type_label", "") or "未识别通道")
            channel = self._fan_channel_display_label(fan)
            detail = self._fan_detail_text(fan)
            header = str(getattr(fan, "header_label", "") or "")
            readable = header or channel
            name_label = getattr(slider, "_name_label", None)
            if name_label is not None:
                name_label.setText(f"{readable} · {_fan_role_compact_label(role)}")
                name_label.setToolTip(detail)
            slider.setToolTip(detail)
            hint_label = getattr(slider, "_usb9_role_hint_label", None)
            if hint_label is None:
                hint_label = QLabel()
                hint_label.setObjectName("FieldHint")
                hint_label.setWordWrap(True)
                layout = slider.layout()
                if layout is not None:
                    layout.insertWidget(1, hint_label)
                setattr(slider, "_usb9_role_hint_label", hint_label)
            hint_label.setText(f"{role} · {channel} · {_fan_role_control_hint(role, has_pwm=True)}")

    def _connect_display_fan_control(self, fan_control, monitor) -> None:  # noqa: ANN001
        if hasattr(monitor, "control_state_changed"):
            monitor.control_state_changed.connect(fan_control.set_control_enabled)
            fan_control.set_control_enabled(getattr(monitor, "control_enabled", False))
        for display_name, slider in getattr(fan_control, "_sliders", {}).items():
            slider.pwm_changed.connect(
                lambda name, pwm, m=monitor: m.set_fan_manual(self._original_fan_name(name), pwm)
            )
            slider.auto_toggled.connect(
                lambda name, auto, m=monitor: m.set_fan_auto(self._original_fan_name(name)) if auto else None
            )

    def _populate_when_ready(self, dashboard, fan_control, profile_editor, monitor) -> None:  # noqa: ANN001
        def populate_sensors_once(_name: str, _value: float) -> None:
            if hasattr(dashboard, "populate_sensors") and not dashboard._cards:
                dashboard.populate_sensors(monitor.sensors)
                try:
                    monitor.sensor_updated.disconnect(populate_sensors_once)
                except Exception:
                    pass

        def populate_fans_once(_name: str, _value: float) -> None:
            if not fan_control._sliders:
                temp_sensors = [sensor for sensor in monitor.sensors if sensor.unit == "°C"]
                fan_control.populate_fans(self._controllable_display_fans(), temp_sensors)
                self._decorate_embedded_fan_control(fan_control)
                self._connect_display_fan_control(fan_control, monitor)
                try:
                    monitor.sensor_updated.disconnect(populate_fans_once)
                except Exception:
                    pass

        monitor.sensor_updated.connect(populate_sensors_once)
        monitor.sensor_updated.connect(populate_fans_once)

    def _start_monitor_read_only(self, monitor) -> None:  # noqa: ANN001
        try:
            monitor.start(control_enabled=False)
        except TypeError:
            monitor.start()
            set_control_enabled = getattr(monitor, "set_control_enabled", None)
            if callable(set_control_enabled):
                set_control_enabled(False)

    def _display_fans_from_monitor(self, fans: list[object], sensors: list[object]) -> list[object]:
        display_fans: list[object] = []
        known_names: set[str] = set()
        self._fan_signal_name_aliases = {}
        self._fan_original_name_by_display = {}
        for fan in fans:
            display_fan = self._display_fan_channel(fan, existing=known_names, sensors=sensors)
            display_fans.append(display_fan)
            known_names.add(str(getattr(display_fan, "name", "")))
            original_name = str(getattr(display_fan, "original_name", "") or getattr(fan, "original_name", "") or getattr(fan, "name", ""))
            display_name = str(getattr(display_fan, "name", original_name))
            if original_name:
                self._fan_signal_name_aliases[original_name] = display_name
                self._fan_original_name_by_display[display_name] = original_name
        known_rpm_inputs = {
            str(getattr(fan, "rpm_input", ""))
            for fan in fans
            if getattr(fan, "rpm_input", None)
        }
        self._readonly_rpm_sensor_names = set()
        self._readonly_rpm_sensor_aliases = {}
        for sensor in sensors:
            if str(getattr(sensor, "unit", "")) != "RPM":
                continue
            name = str(getattr(sensor, "name", "") or "只读风扇")
            internal_id = str(getattr(sensor, "internal_id", ""))
            if internal_id and internal_id in known_rpm_inputs:
                continue
            readonly_channel = ReadOnlyFanChannel(
                name=name,
                pwm_path=f"readonly:{internal_id or name}",
                rpm_input=internal_id or None,
            )
            display_fan = self._display_fan_channel(readonly_channel, existing=known_names, sensors=sensors)
            resolved_name = str(getattr(display_fan, "name", name))
            known_names.add(resolved_name)
            self._readonly_rpm_sensor_names.add(resolved_name)
            self._readonly_rpm_sensor_aliases[name] = resolved_name
            self._fan_original_name_by_display[resolved_name] = name
            display_fans.append(display_fan)
        display_fans = sorted(display_fans, key=_fan_sort_key)
        self._seed_initial_fan_runtime(display_fans)
        return display_fans

    def _seed_initial_fan_runtime(self, fans: list[object]) -> None:
        for fan in fans:
            name = str(getattr(fan, "name", "") or "")
            if not name or name in self._latest_rpm:
                continue
            value = self._read_fan_runtime_rpm(fan)
            if value is None:
                continue
            self._latest_rpm[name] = value
            if value > 0:
                self._rpm_history.setdefault(name, deque(maxlen=90)).append(value)

    def _display_fan_channel(self, fan, *, existing: set[str], sensors: list[object] | None = None) -> object:  # noqa: ANN001
        pwm_path = str(getattr(fan, "pwm_path", "") or "")
        rpm_input = str(getattr(fan, "rpm_input", "") or "") or None
        identity = self._fan_channel_identity(fan, sensors=sensors or [])
        override = self._fan_label_overrides.get(identity["identity_key"], {})
        override_header = _known_fan_header_label(str(override.get("header", "") or ""))
        header_label = override_header or identity["header_label"]
        header_confirmed = bool(override_header) or bool(identity["header_confirmed"])
        header_role = _fan_header_role(header_label)
        type_label = _canonical_fan_role(str(override.get("role", "") or header_role or identity["type_label"]))
        alias = _clean_fan_alias(str(override.get("alias", "") or ""))
        display_name = self._unique_fan_name(
            self._fan_display_name_from_identity(
                identity,
                type_label=type_label,
                alias=alias,
                header_label=header_label,
                header_confirmed=header_confirmed,
            ),
            existing,
        )
        return DisplayFanChannel(
            name=display_name,
            pwm_path=pwm_path,
            rpm_input=rpm_input,
            min_pwm=int(getattr(fan, "min_pwm", 0)),
            max_pwm=int(getattr(fan, "max_pwm", 255)),
            read_only=self._fan_is_readonly(fan),
            rpm_unit=str(getattr(fan, "rpm_unit", "RPM") or "RPM"),
            original_name=str(getattr(fan, "original_name", "") or getattr(fan, "name", "") or display_name),
            type_label=type_label,
            channel_label=identity["channel_label"],
            header_label=_header_for_display(header_label, confirmed=header_confirmed),
            header_basis=(
                f"手动指定: {override_header}"
                if override_header
                else identity["header_basis"]
            ),
            header_confirmed=header_confirmed,
            hwmon_name=identity["hwmon_name"],
            chip_label=identity["chip_label"],
            hwmon_path=identity["hwmon_path"],
            role_basis=identity["role_basis"],
            sensor_label=identity["sensor_label"],
            sensor_basis=identity["sensor_basis"],
            detail_text=identity["detail_text"],
            evidence_text=identity["evidence_text"],
            identity_key=identity["identity_key"],
        )

    def _fan_channel_identity(self, fan, *, sensors: list[object]) -> dict[str, str]:  # noqa: ANN001
        original_name = str(getattr(fan, "original_name", "") or getattr(fan, "name", "") or "Unknown")
        pwm_path = str(getattr(fan, "pwm_path", "") or "")
        rpm_input = str(getattr(fan, "rpm_input", "") or "")
        has_pwm = bool(pwm_path and not pwm_path.startswith("readonly:"))
        hwmon_name, labels = self._fan_hwmon_context(pwm_path, rpm_input)
        hwmon_dir = self._fan_hwmon_dir_from_paths(pwm_path, rpm_input)
        hwmon_path = str(hwmon_dir) if hwmon_dir is not None else ""
        chip_label = _fan_chip_display_name(hwmon_name)
        channel_label = _path_channel_label(pwm_path if has_pwm else "", rpm_input or None)
        sensor_context = self._fan_sensor_context(
            pwm_path=pwm_path,
            rpm_input=rpm_input,
            hwmon_name=hwmon_name,
            channel_label=channel_label,
            sensors=sensors,
        )
        header_label, header_basis, header_confirmed = self._fan_header_identity(
            hwmon_name=hwmon_name,
            labels=labels,
            channel_label=channel_label,
            pwm_path=pwm_path,
            rpm_input=rpm_input,
            original_name=original_name,
        )
        type_label, role_basis = _fan_role_detection(
            [
                ("原始名称", original_name),
                ("hwmon", hwmon_name),
                ("物理接口", header_label.rstrip("?")),
                *[("label", label) for label in labels],
            ],
            has_pwm=has_pwm,
        )
        header_role = _fan_header_role(header_label)
        if header_role and type_label in {"未识别通道", "未识别风扇", ""}:
            type_label = header_role
            role_basis = header_basis
        sensor_role, sensor_role_basis, sensor_header = self._fan_role_hint_from_sensor_context(
            sensor_context,
            channel_label=channel_label,
            has_pwm=has_pwm,
        )
        if sensor_role and type_label in {"未识别通道", "未识别风扇", ""}:
            type_label = sensor_role
            role_basis = sensor_role_basis
            if sensor_header and not header_label:
                header_label = sensor_header
                header_basis = sensor_role_basis
                header_confirmed = False
        display_name = self._fan_display_name_from_identity(
            {
                "type_label": type_label,
                "channel_label": channel_label,
                "header_label": header_label,
                "header_confirmed": "1" if header_confirmed else "",
                "original_name": original_name,
                "has_pwm": "1" if has_pwm else "",
            },
            type_label=type_label,
            alias="",
            header_label=header_label,
            header_confirmed=header_confirmed,
        )
        details: list[str] = []
        details.append(f"识别: {role_basis}")
        if chip_label:
            details.append(f"芯片: {chip_label}")
        if hwmon_path:
            details.append(f"hwmon: {hwmon_path}")
        if header_label:
            details.append(f"物理接口: {_header_for_display(header_label, confirmed=header_confirmed)}")
            details.append(f"接口依据: {header_basis}")
        if labels:
            details.append("label: " + ", ".join(dict.fromkeys(labels)))
        if sensor_context["sensor_label"]:
            details.append(f"关联传感器: {sensor_context['sensor_label']}")
        if sensor_context["sensor_basis"]:
            details.append(f"传感器依据: {sensor_context['sensor_basis']}")
        details.extend(sensor_context["detail_lines"])
        if hwmon_name:
            details.append(f"hwmon name: {hwmon_name}")
        details.extend(self._fan_hwmon_runtime_details(pwm_path, rpm_input))
        if rpm_input:
            details.append(f"rpm: {rpm_input}")
        if has_pwm:
            details.append(f"pwm: {pwm_path}")
        elif pwm_path:
            details.append(f"path: {pwm_path}")
        if original_name and original_name not in display_name:
            details.append(f"原始: {original_name}")
        control_hint = _fan_role_control_hint(type_label, has_pwm=has_pwm)
        if control_hint:
            details.append(f"操作提示: {control_hint}")
        identity_payload = {
            "type_label": type_label,
            "channel_label": channel_label,
            "header_label": header_label,
            "header_basis": header_basis,
            "header_confirmed": "1" if header_confirmed else "",
            "display_name": display_name,
            "role_basis": role_basis,
            "sensor_label": sensor_context["sensor_label"],
            "sensor_basis": sensor_context["sensor_basis"],
            "detail_text": " · ".join(details) if details else "--",
            "hwmon_name": hwmon_name,
            "chip_label": chip_label,
            "hwmon_path": hwmon_path,
            "identity_key": self._fan_identity_key(
                hwmon_name=hwmon_name,
                channel_label=channel_label,
                original_name=original_name,
                pwm_path=pwm_path,
                rpm_input=rpm_input,
            ),
            "original_name": original_name,
            "has_pwm": "1" if has_pwm else "",
        }
        identity_payload["evidence_text"] = _fan_identity_evidence_text(
            DisplayFanChannel(
                name=display_name,
                pwm_path=pwm_path,
                rpm_input=rpm_input or None,
                read_only=not has_pwm,
                original_name=original_name,
                type_label=type_label,
                channel_label=channel_label,
                header_label=_header_for_display(header_label, confirmed=header_confirmed),
                header_basis=header_basis,
                header_confirmed=header_confirmed,
                hwmon_name=hwmon_name,
                chip_label=chip_label,
                hwmon_path=hwmon_path,
                role_basis=role_basis,
                sensor_label=str(sensor_context["sensor_label"]),
                sensor_basis=str(sensor_context["sensor_basis"]),
            ),
            compact=True,
        )
        return identity_payload

    def _fan_display_name_from_identity(
        self,
        identity: dict[str, str],
        *,
        type_label: str,
        alias: str,
        header_label: str,
        header_confirmed: bool,
    ) -> str:
        channel_label = identity.get("channel_label", "Channel")
        original_name = identity.get("original_name", "")
        has_pwm = bool(identity.get("has_pwm"))
        if alias:
            return f"{alias} · {channel_label}"
        header_display = _header_for_display(header_label, confirmed=header_confirmed)
        if header_display:
            return f"{header_display} · {channel_label}"
        if (
            type_label in {"未识别风扇", "未知风扇", "GPU 风扇"}
            and not has_pwm
            and original_name
            and original_name != "Unknown"
        ):
            return f"{original_name} · {channel_label}"
        return f"{type_label} · {channel_label}"

    def _fan_header_identity(
        self,
        *,
        hwmon_name: str,
        labels: list[str],
        channel_label: str,
        pwm_path: str,
        rpm_input: str,
        original_name: str,
    ) -> tuple[str, str, bool]:
        for label in labels:
            header = _known_fan_header_label(label)
            if header:
                return header, f"hwmon label: {label}", True
        raw_header = _known_fan_header_label(original_name)
        if raw_header:
            return raw_header, f"旧风扇后端名称候选: {original_name}，需要确认", False
        candidate = self._fan_board_header_candidate(
            hwmon_name=hwmon_name,
            channel_label=channel_label,
            pwm_path=pwm_path,
            rpm_input=rpm_input,
        )
        if candidate:
            basis = self._fan_board_header_candidate_basis(
                candidate,
                hwmon_name=hwmon_name,
                channel_label=channel_label,
            )
            return candidate, basis, False
        return "", "", False

    def _fan_board_header_candidate(
        self,
        *,
        hwmon_name: str,
        channel_label: str,
        pwm_path: str,
        rpm_input: str,
    ) -> str:
        profile = self._fan_board_header_candidate_profile(hwmon_name)
        if not profile:
            return ""
        if not any(_is_real_sysfs_hwmon_path(path) for path in (pwm_path, rpm_input)):
            return ""
        indexes = _channel_indexes_from_label(channel_label)
        if len(indexes) != 1:
            return ""
        mapping = profile.get("mapping", {})
        return str(mapping.get(indexes[0], "")) if isinstance(mapping, dict) else ""

    def _fan_board_header_candidate_profile(self, hwmon_name: str) -> dict[str, object] | None:
        if not hwmon_name:
            return None
        vendor, board_name = self._system_board_identity()
        vendor_text = vendor.upper()
        board_text = board_name.upper()
        for profile in FAN_BOARD_HEADER_CANDIDATE_PROFILES:
            hwmon_names = tuple(str(name) for name in profile.get("hwmon_names", ()))
            vendors = tuple(str(name).upper() for name in profile.get("vendors", ()))
            board_keywords = tuple(str(name).upper() for name in profile.get("board_keywords", ()))
            if hwmon_name not in hwmon_names:
                continue
            if not any(vendor_key in vendor_text for vendor_key in vendors):
                continue
            if not all(keyword in board_text for keyword in board_keywords):
                continue
            return profile
        return None

    def _fan_board_header_candidate_basis(self, header: str, *, hwmon_name: str, channel_label: str) -> str:
        vendor, board_name = self._system_board_identity()
        profile = self._fan_board_header_candidate_profile(hwmon_name)
        profile_name = str(profile.get("name", "主板接口候选映射")) if profile else "主板接口候选映射"
        board = " / ".join(part for part in (vendor.strip(), board_name.strip()) if part)
        board_text = f"{board} " if board else ""
        return (
            f"主板型号候选: {board_text}{hwmon_name} 无 hwmon label，"
            f"按 {profile_name} 推断 {channel_label} -> {header}；"
            "需要用识别脉冲或人工确认"
        )

    def _system_board_identity(self) -> tuple[str, str]:
        vendor = _read_text_file(Path("/sys/class/dmi/id/board_vendor"))
        board_name = _read_text_file(Path("/sys/class/dmi/id/board_name"))
        return vendor, board_name

    def _fan_channel_display_label(self, fan) -> str:  # noqa: ANN001
        channel = str(getattr(fan, "channel_label", "") or "--")
        header = str(getattr(fan, "header_label", "") or "")
        return f"{header} · {channel}" if header else channel

    def _fan_identity_key(
        self,
        *,
        hwmon_name: str,
        channel_label: str,
        original_name: str,
        pwm_path: str,
        rpm_input: str,
    ) -> str:
        if hwmon_name and channel_label and channel_label != "Channel":
            hwmon_key = self._fan_hwmon_directory_key(pwm_path, rpm_input)
            if hwmon_key:
                return f"hwmon:{hwmon_key}:{channel_label}"
            return f"hwmon:{hwmon_name}:{channel_label}"
        if pwm_path:
            return f"pwm:{pwm_path.removeprefix('readonly:')}"
        if rpm_input:
            return f"rpm:{rpm_input.removeprefix('readonly:')}"
        return f"name:{original_name or 'Unknown'}"

    def _fan_hwmon_directory_key(self, pwm_path: str, rpm_input: str) -> str:
        for text in (pwm_path, rpm_input):
            if not text or text.startswith(("nvidia:", "nvidia-smi:", "readonly:nvidia-smi:")):
                continue
            path_text = text.removeprefix("readonly:")
            parent = Path(path_text).parent
            if not parent.name.startswith("hwmon"):
                continue
            try:
                resolved = parent.resolve()
            except OSError:
                resolved = parent
            if resolved.name.startswith("hwmon") and resolved.parent.name == "hwmon":
                return str(resolved.parent.parent)
            return str(resolved)
        return ""

    def _fan_hwmon_dir_from_paths(self, pwm_path: str, rpm_input: str) -> Path | None:
        for text in (pwm_path, rpm_input):
            if not text or text.startswith(("nvidia:", "nvidia-smi:", "readonly:nvidia-smi:")):
                continue
            return Path(text.removeprefix("readonly:")).parent
        return None

    def _fan_hwmon_context(self, pwm_path: str, rpm_input: str) -> tuple[str, list[str]]:
        hwmon_dir = self._fan_hwmon_dir_from_paths(pwm_path, rpm_input)
        if hwmon_dir is None:
            return "", []
        labels: list[str] = []
        hwmon_name = _read_text_file(hwmon_dir / "name")
        indexes = {
            index
            for index in (
                _channel_index_from_name("pwm", Path(pwm_path).name) if pwm_path and not pwm_path.startswith("readonly:") else "",
                _channel_index_from_name("fan", Path(rpm_input).name) if rpm_input else "",
            )
            if index
        }
        for index in sorted(indexes):
            for label_name in (f"fan{index}_label", f"pwm{index}_label"):
                label = _read_text_file(hwmon_dir / label_name)
                if label and label not in labels:
                    labels.append(label)
        return hwmon_name, labels

    def _fan_sensor_context(
        self,
        *,
        pwm_path: str,
        rpm_input: str,
        hwmon_name: str,
        channel_label: str,
        sensors: list[object],
    ) -> dict[str, object]:
        fancontrol = self._fancontrol_sensor_context(
            pwm_path=pwm_path,
            rpm_input=rpm_input,
            hwmon_name=hwmon_name,
            sensors=sensors,
        )
        if fancontrol["sensor_label"]:
            return fancontrol
        auto_temp = self._hwmon_auto_temp_sensor_context(
            pwm_path=pwm_path,
            rpm_input=rpm_input,
            channel_label=channel_label,
            sensors=sensors,
        )
        if auto_temp["sensor_label"]:
            return auto_temp
        return {"sensor_label": "", "sensor_basis": "", "detail_lines": []}

    def _fancontrol_sensor_context(
        self,
        *,
        pwm_path: str,
        rpm_input: str,
        hwmon_name: str,
        sensors: list[object],
    ) -> dict[str, object]:
        config = _load_fancontrol_config()
        if not config.fctemps and not config.fcfans:
            return {"sensor_label": "", "sensor_basis": "", "detail_lines": []}
        keys = self._fancontrol_pwm_keys(pwm_path, hwmon_name=hwmon_name, config=config)
        if not keys:
            return {"sensor_label": "", "sensor_basis": "", "detail_lines": []}
        pwm_key = next((key for key in keys if key in config.fctemps or key in config.fcfans), "")
        if not pwm_key:
            return {"sensor_label": "", "sensor_basis": "", "detail_lines": []}
        temp_key = config.fctemps.get(pwm_key, "")
        fan_key = config.fcfans.get(pwm_key, "")
        sensor_label = self._sensor_name_for_fancontrol_key(temp_key, sensors)
        if not sensor_label:
            sensor_label = self._fancontrol_temp_label(temp_key, config)
        if not sensor_label and temp_key:
            sensor_label = temp_key
        detail_lines = [f"fancontrol: {pwm_key}"]
        if temp_key:
            detail_lines.append(f"fancontrol 温度源: {sensor_label or temp_key} ({temp_key})")
        if fan_key:
            detail_lines.append(f"fancontrol 转速: {fan_key}")
        basis = f"fancontrol FCTEMPS: {pwm_key} -> {sensor_label or temp_key}" if temp_key else ""
        if not basis and fan_key:
            basis = f"fancontrol FCFANS: {pwm_key} -> {fan_key}"
        return {
            "sensor_label": sensor_label,
            "sensor_basis": basis,
            "detail_lines": detail_lines,
        }

    def _fancontrol_pwm_keys(self, pwm_path: str, *, hwmon_name: str, config: FanControlConfig) -> list[str]:
        if not pwm_path or pwm_path.startswith(("readonly:", "nvidia:", "nvidia-smi:")):
            return []
        path = Path(pwm_path)
        if not path.name.startswith("pwm"):
            return []
        keys: list[str] = []
        if path.parent.name.startswith("hwmon"):
            keys.append(f"{path.parent.name}/{path.name}")
        resolved = self._safe_resolve(path)
        resolved_text = str(resolved)
        for config_hwmon, device_path in config.devpath.items():
            if self._path_matches_fancontrol_devpath(resolved_text, device_path):
                keys.append(f"{config_hwmon}/{path.name}")
        if hwmon_name:
            for config_hwmon, device_name in config.devname.items():
                if device_name == hwmon_name:
                    keys.append(f"{config_hwmon}/{path.name}")
        return list(dict.fromkeys(keys))

    def _path_matches_fancontrol_devpath(self, resolved_text: str, device_path: str) -> bool:
        cleaned = device_path.strip("/")
        if not cleaned:
            return False
        return f"/{cleaned}/" in resolved_text or resolved_text.endswith(f"/{cleaned}")

    def _safe_resolve(self, path: Path) -> Path:
        try:
            return path.resolve()
        except OSError:
            return path

    def _sensor_name_for_fancontrol_key(self, key: str, sensors: list[object]) -> str:
        if not key:
            return ""
        key_parts = tuple(part for part in key.split("/") if part)
        for sensor in sensors:
            internal_id = str(getattr(sensor, "internal_id", "") or "")
            if not internal_id:
                continue
            path_parts = Path(internal_id).parts
            if len(path_parts) >= len(key_parts) and tuple(path_parts[-len(key_parts) :]) == key_parts:
                return str(getattr(sensor, "name", "") or key)
        return ""

    def _sensor_name_for_path(self, path: Path, sensors: list[object]) -> str:
        path_text = str(path)
        for sensor in sensors:
            if str(getattr(sensor, "internal_id", "") or "") == path_text:
                return str(getattr(sensor, "name", "") or "")
        return ""

    def _fancontrol_temp_label(self, key: str, config: FanControlConfig) -> str:
        if not key or "/" not in key:
            return ""
        hwmon_key, item = key.split("/", 1)
        if not item.startswith("temp") or not item.endswith("_input"):
            return ""
        label_item = item.replace("_input", "_label")
        candidates = [Path("/sys/class/hwmon") / hwmon_key / label_item]
        device_path = config.devpath.get(hwmon_key, "")
        if device_path:
            candidates.extend((Path("/sys") / device_path / "hwmon").glob(f"hwmon*/{label_item}"))
        for candidate in candidates:
            label = _read_text_file(candidate)
            if label:
                hwmon_name = _read_text_file(candidate.parent / "name")
                return f"{hwmon_name} {label}".strip()
        return ""

    def _hwmon_auto_temp_sensor_context(
        self,
        *,
        pwm_path: str,
        rpm_input: str,
        channel_label: str,
        sensors: list[object],
    ) -> dict[str, object]:
        probe_path = next(
            (
                path.removeprefix("readonly:")
                for path in (pwm_path, rpm_input)
                if path and not path.startswith(("nvidia:", "nvidia-smi:", "readonly:nvidia-smi:"))
            ),
            "",
        )
        if not probe_path:
            return {"sensor_label": "", "sensor_basis": "", "detail_lines": []}
        hwmon_dir = Path(probe_path).parent
        indexes = _channel_indexes_from_label(channel_label)
        for index in indexes:
            temp_sel = _read_text_file(hwmon_dir / f"pwm{index}_temp_sel")
            if not temp_sel or not temp_sel.isdigit():
                continue
            temp_path = hwmon_dir / f"temp{temp_sel}_input"
            sensor_label = self._sensor_name_for_path(temp_path, sensors)
            temp_label = _read_text_file(hwmon_dir / f"temp{temp_sel}_label")
            if not sensor_label:
                sensor_label = " ".join(part for part in (temp_label, f"temp{temp_sel}") if part)
            if not sensor_label:
                sensor_label = f"temp{temp_sel}"
            detail_lines = [f"主板自动温度源: {sensor_label} ({temp_path})"]
            temp_value = _read_text_file(temp_path)
            if temp_value:
                detail_lines.append(f"温度源当前值: {_format_millidegree_text(temp_value)}")
            return {
                "sensor_label": sensor_label,
                "sensor_basis": f"pwm{index}_temp_sel -> {sensor_label}",
                "detail_lines": detail_lines,
            }
        return {"sensor_label": "", "sensor_basis": "", "detail_lines": []}

    def _fan_role_hint_from_sensor_context(
        self,
        sensor_context: dict[str, object],
        *,
        channel_label: str,
        has_pwm: bool,
    ) -> tuple[str, str, str]:
        sensor_label = str(sensor_context.get("sensor_label", "") or "")
        if not sensor_label or not has_pwm:
            return "", "", ""
        role, basis = _fan_role_detection([("关联传感器", sensor_label)], has_pwm=has_pwm)
        indexes = _channel_indexes_from_label(channel_label)
        if role == "CPU 风扇" and indexes == ["1"]:
            return (
                "CPU 风扇",
                f"{basis}；PWM1/FAN1 通常是 CPU_FAN，需要确认",
                "CPU_FAN",
            )
        return "", "", ""

    def _fan_hwmon_runtime_details(self, pwm_path: str, rpm_input: str) -> list[str]:
        probe_paths = [
            path
            for path in (pwm_path, rpm_input)
            if path and not path.startswith(("nvidia:", "nvidia-smi:", "readonly:nvidia-smi:"))
        ]
        if not probe_paths:
            return []
        base_text = probe_paths[0].removeprefix("readonly:")
        hwmon_dir = Path(base_text).parent
        indexes = {
            index
            for index in (
                _channel_index_from_name("pwm", Path(pwm_path).name) if pwm_path and not pwm_path.startswith("readonly:") else "",
                _channel_index_from_name("fan", Path(rpm_input).name) if rpm_input else "",
            )
            if index
        }
        details: list[str] = []
        for index in sorted(indexes):
            rpm = _read_text_file(hwmon_dir / f"fan{index}_input")
            if rpm:
                details.append(f"当前转速: {rpm} RPM")
            pwm = _read_text_file(hwmon_dir / f"pwm{index}")
            if pwm:
                parsed_pwm = self._parse_int(pwm)
                if parsed_pwm is None:
                    details.append(f"当前 PWM: {pwm}")
                else:
                    details.append(f"当前 PWM: {round(parsed_pwm / 255 * 100)}% ({parsed_pwm})")
            enable = _read_text_file(hwmon_dir / f"pwm{index}_enable")
            if enable:
                details.append(f"PWM 模式: {_pwm_enable_mode_label(enable)}")
            temp_sel = _read_text_file(hwmon_dir / f"pwm{index}_temp_sel")
            if temp_sel and temp_sel.isdigit():
                temp_label = _read_text_file(hwmon_dir / f"temp{temp_sel}_label")
                temp_value = _read_text_file(hwmon_dir / f"temp{temp_sel}_input")
                temp_parts = [f"temp{temp_sel}"]
                if temp_label:
                    temp_parts.append(temp_label)
                if temp_value:
                    temp_parts.append(_format_millidegree_text(temp_value))
                details.append("主板自动温度源: " + " ".join(temp_parts))
        return details

    def _start_nvidia_smi_fallback_if_needed(self) -> None:
        self._stop_nvidia_smi_fallback()
        if any(str(getattr(fan, "pwm_path", "")).startswith("nvidia:") for fan in self._fans):
            return
        if shutil.which("nvidia-smi") is None:
            return
        samples = self._read_nvidia_smi_samples()
        if not samples:
            return

        known_names = {str(getattr(fan, "name", "")) for fan in self._fans}
        sensor_names = {str(getattr(sensor, "name", "")) for sensor in self._sensors}
        for sample in samples:
            base_name = f"GPU{sample.index} 风扇"
            if sample.name:
                base_name = f"GPU{sample.index} {sample.name} 风扇"
            if sample.fan_percent is not None:
                raw_channel = ReadOnlyFanChannel(
                    name=base_name,
                    pwm_path=f"readonly:nvidia-smi:{sample.index}:fan",
                    rpm_input=f"nvidia-smi:{sample.index}:fan",
                    rpm_unit="%",
                )
                display_channel = self._display_fan_channel(raw_channel, existing=known_names)
                fan_name = str(getattr(display_channel, "name", base_name))
                known_names.add(fan_name)
                self._readonly_rpm_sensor_names.add(fan_name)
                self._readonly_rpm_sensor_aliases[base_name] = fan_name
                self._readonly_rpm_sensor_aliases[fan_name] = fan_name
                self._fan_original_name_by_display[fan_name] = base_name
                self._nvidia_smi_fan_names[sample.index] = fan_name
                self._fans.append(display_channel)
                self._sensors.append(
                    SupplementalSensor(
                        name=fan_name,
                        unit="%",
                        source="nvidia-smi",
                        internal_id=f"nvidia-smi:{sample.index}:fan",
                    )
                )
            if sample.temperature_c is not None:
                temp_name = self._unique_fan_name(f"GPU{sample.index} 温度", sensor_names)
                sensor_names.add(temp_name)
                self._nvidia_smi_temperature_names[sample.index] = temp_name
                self._sensors.append(
                    SupplementalSensor(
                        name=temp_name,
                        unit="°C",
                        source="nvidia-smi",
                        internal_id=f"nvidia-smi:{sample.index}:temp",
                    )
                )

        if not self._nvidia_smi_fan_names and not self._nvidia_smi_temperature_names:
            return
        self._poll_nvidia_smi_fallback(samples)
        self._nvidia_smi_timer = QTimer(self)
        self._nvidia_smi_timer.setInterval(1500)
        self._nvidia_smi_timer.timeout.connect(self._poll_nvidia_smi_fallback)
        self._nvidia_smi_timer.start()

    def _stop_nvidia_smi_fallback(self) -> None:
        if self._nvidia_smi_timer is not None:
            self._nvidia_smi_timer.stop()
            self._nvidia_smi_timer.deleteLater()
            self._nvidia_smi_timer = None

    def _poll_nvidia_smi_fallback(self, samples: list[NvidiaSmiSample] | None = None) -> None:
        samples = samples if samples is not None else self._read_nvidia_smi_samples()
        for sample in samples:
            fan_name = self._nvidia_smi_fan_names.get(sample.index)
            if fan_name and sample.fan_percent is not None:
                self._update_fan_rpm(fan_name, sample.fan_percent)
            temp_name = self._nvidia_smi_temperature_names.get(sample.index)
            if temp_name and sample.temperature_c is not None:
                self._update_sensor_value(temp_name, float(sample.temperature_c))

    def _read_nvidia_smi_samples(self) -> list[NvidiaSmiSample]:
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,temperature.gpu,fan.speed",
                    "--format=csv,noheader,nounits",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=1.5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        return self._parse_nvidia_smi_samples(result.stdout)

    def _parse_nvidia_smi_samples(self, output: str) -> list[NvidiaSmiSample]:
        samples: list[NvidiaSmiSample] = []
        for row in csv.reader(line for line in output.splitlines() if line.strip()):
            if len(row) < 4:
                continue
            index = self._parse_int(row[0])
            if index is None:
                continue
            samples.append(
                NvidiaSmiSample(
                    index=index,
                    name=row[1].strip(),
                    temperature_c=self._parse_int(row[2]),
                    fan_percent=self._parse_int(row[3]),
                )
            )
        return samples

    def _parse_int(self, value: str) -> int | None:
        text = value.strip()
        if not text or text.upper() in {"N/A", "[N/A]"}:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None

    def _unique_fan_name(self, name: str, existing: set[str]) -> str:
        if name not in existing:
            return name
        index = 2
        while f"{name} #{index}" in existing:
            index += 1
        return f"{name} #{index}"

    def _display_fan_name(self, fan_name: str) -> str:
        return self._fan_signal_name_aliases.get(fan_name, fan_name)

    def _original_fan_name(self, fan_name: str) -> str:
        return self._fan_original_name_by_display.get(fan_name, fan_name)

    def _fan_count_summary_text(self) -> str:
        if not self._fans:
            return "0 通道"
        counts: dict[str, int] = {}
        for fan in self._fans:
            role = str(getattr(fan, "type_label", "") or "")
            if not role:
                role = _fan_role_detection(
                    [
                        ("名称", str(getattr(fan, "name", "") or "")),
                        ("路径", str(getattr(fan, "pwm_path", "") or "")),
                    ],
                    has_pwm=bool(str(getattr(fan, "pwm_path", "") or "")),
                )[0]
            counts[role] = counts.get(role, 0) + 1
        priority = ("CPU 风扇", "水泵/AIO", "机箱风扇", "GPU 风扇")
        parts = [f"{role.replace('/AIO', '')} {counts[role]}" for role in priority if counts.get(role)]
        unknown = sum(count for role, count in counts.items() if role not in priority)
        if unknown:
            parts.append(f"未识别 {unknown}")
        return f"{len(self._fans)} 通道\n" + " · ".join(parts)

    def _fan_role_summary_text(self) -> str:
        if not self._fans:
            return "加载后会按 CPU 风扇、水泵/AIO、机箱风扇和未识别通道分组显示。"
        groups = self._fan_role_groups()
        parts: list[str] = []
        for role in FAN_ROLE_OVERVIEW_ORDER:
            fans = groups.get(role, [])
            if not fans:
                continue
            active = sum(1 for fan in fans if self._fan_has_live_speed(str(getattr(fan, "name", ""))))
            channels = "、".join(self._fan_channel_display_label(fan) for fan in fans[:3])
            if len(fans) > 3:
                channels += f" 等 {len(fans)} 个"
            live_text = f"{active}/{len(fans)} 有转速" if active else f"{len(fans)} 个待确认"
            parts.append(f"{_fan_role_compact_label(role)}：{live_text}（{channels}）")
        extras = [
            (role, fans)
            for role, fans in groups.items()
            if role not in FAN_ROLE_OVERVIEW_ORDER and fans
        ]
        for role, fans in extras:
            parts.append(f"{role or '未知'}：{len(fans)} 个")
        return " · ".join(parts)

    def _fan_role_speed_summary_text(self) -> str:
        if not self._fans:
            return "加载后会直接列出 CPU_FAN、CPU_OPT、水泵和机箱风扇的实时转速。"
        groups = self._fan_role_groups()
        parts: list[str] = []
        for role in FAN_ROLE_OVERVIEW_ORDER:
            fans = groups.get(role, [])
            if not fans:
                continue
            fan_parts = [self._fan_role_speed_item_text(fan) for fan in fans[:4]]
            if len(fans) > 4:
                inactive = sum(
                    1
                    for fan in fans[4:]
                    if not self._fan_has_live_speed(str(getattr(fan, "name", "")))
                )
                suffix = f"另 {len(fans) - 4} 个"
                if inactive:
                    suffix += f"，{inactive} 个无转速"
                fan_parts.append(suffix)
            parts.append(f"{_fan_role_compact_label(role)}：{'、'.join(fan_parts)}")
        if parts:
            return " · ".join(parts)
        return "未识别到可分组的风扇通道；可以在维护 > 明细里给每个 PWM/FAN 保存角色和物理接口。"

    def _fan_identity_overview_text(self) -> str:
        if not self._fans:
            return "加载后显示每路物理接口、角色、转速和识别状态。"
        confirmed = 0
        candidates = 0
        needs_calibration = 0
        readonly = 0
        for fan in self._fans:
            state = _fan_identity_state_label(fan)
            if state in {"hwmon 确认", "手动标定", "名称推断"}:
                confirmed += 1
            elif state == "主板候选":
                candidates += 1
            elif state == "只读监控":
                readonly += 1
            else:
                needs_calibration += 1
        parts = []
        if confirmed:
            parts.append(f"{confirmed} 路已命名")
        if candidates:
            parts.append(f"{candidates} 路主板候选")
        if readonly:
            parts.append(f"{readonly} 路只读")
        if needs_calibration:
            parts.append(f"{needs_calibration} 路待标定")
        return "物理接口识别：" + " · ".join(parts) + "。下表优先显示 CPU_FAN、水泵和机箱接口；底层 PWM/FAN 路径放在明细提示里。"

    def _refresh_fan_identity_table(self) -> None:
        if not hasattr(self, "fan_identity_table"):
            return
        fans = sorted(self._fans, key=_fan_sort_key)
        self.fan_identity_table.setRowCount(len(fans))
        unknown_index = 0
        for row, fan in enumerate(fans):
            role = str(getattr(fan, "type_label", "") or "未识别通道")
            if role in {"未识别通道", "未识别风扇", "未知 PWM", "未知风扇"} and not str(getattr(fan, "header_label", "") or ""):
                unknown_index += 1
            detail = self._fan_detail_text(fan)
            self._set_fan_identity_table_item(
                row,
                0,
                self._fan_overview_identity_label(fan, unknown_index=unknown_index),
                tooltip=detail,
            )
            self._set_fan_identity_table_item(row, 1, _fan_role_compact_label(role), tooltip=detail)
            self._set_fan_identity_table_item(row, 2, self._fan_compact_speed_text(fan), tooltip=detail)
            self._set_fan_identity_table_item(row, 3, self._fan_overview_state_label(fan), tooltip=detail)
            self._set_fan_identity_table_item(row, 4, self._fan_overview_action_text(fan), tooltip=detail)
        if fans:
            self.fan_identity_table.resizeColumnsToContents()

    def _fan_overview_identity_label(self, fan, *, unknown_index: int) -> str:  # noqa: ANN001
        header = str(getattr(fan, "header_label", "") or "")
        if header:
            return header
        role = str(getattr(fan, "type_label", "") or "")
        if role == "GPU 风扇":
            return self._fan_short_channel_label(fan)
        if role not in {"", "未识别通道", "未识别风扇", "未知 PWM", "未知风扇"}:
            return f"{_fan_role_compact_label(role)}通道"
        return f"待标定通道 {unknown_index or 1}"

    def _fan_overview_state_label(self, fan) -> str:  # noqa: ANN001
        state = _fan_identity_state_label(fan)
        return {
            "hwmon 确认": "已确认",
            "手动标定": "手动",
            "主板候选": "候选",
            "需标定": "待标定",
            "名称推断": "名称推断",
            "只读监控": "只读",
        }.get(state, state)

    def _fan_overview_action_text(self, fan) -> str:  # noqa: ANN001
        state = _fan_identity_state_label(fan)
        if state == "hwmon 确认":
            return "主板标签已确认，可直接观察转速"
        if state == "手动标定":
            return "使用已保存的手动标签"
        if state == "主板候选":
            return "确认当前识别；不确定时先做识别脉冲"
        if state == "需标定":
            return "到维护 > 明细选择该通道并点识别选中通道"
        if state == "只读监控":
            return "只显示转速，不能写入 PWM"
        return "建议核对物理接口后保存标签"

    def _fan_role_speed_item_text(self, fan) -> str:  # noqa: ANN001
        label = self._fan_short_channel_label(fan)
        speed = self._fan_compact_speed_text(fan)
        identity = _fan_identity_state_label(fan)
        if identity in {"主板候选", "需标定"}:
            return f"{label} {speed}（{identity}）"
        return f"{label} {speed}"

    def _fan_short_channel_label(self, fan) -> str:  # noqa: ANN001
        header = str(getattr(fan, "header_label", "") or "")
        channel = str(getattr(fan, "channel_label", "") or "")
        if header:
            return header
        if channel and channel != "Channel":
            return channel
        return str(getattr(fan, "name", "") or "通道")

    def _fan_compact_speed_text(self, fan) -> str:  # noqa: ANN001
        name = str(getattr(fan, "name", "") or "")
        value = self._latest_rpm.get(name)
        unit = str(getattr(fan, "rpm_unit", "RPM") or "RPM")
        if value is None:
            runtime = self._read_fan_runtime_rpm(fan)
            if runtime is not None:
                value = runtime
        if value is None:
            return "等待转速"
        if value <= 0:
            if unit == "%":
                return "0%"
            return "无转速"
        if unit == "%":
            return f"{value}%"
        return f"{value} {unit}"

    def _read_fan_runtime_rpm(self, fan) -> int | None:  # noqa: ANN001
        rpm_input = str(getattr(fan, "rpm_input", "") or "")
        if not rpm_input or rpm_input.startswith(("nvidia:", "nvidia-smi:")):
            return None
        text = _read_text_file(Path(rpm_input.removeprefix("readonly:")))
        value = self._parse_int(text) if text else None
        if value is None:
            return None
        return max(0, value)

    def _fan_role_groups(self) -> dict[str, list[object]]:
        groups: dict[str, list[object]] = {}
        for fan in sorted(self._fans, key=_fan_sort_key):
            role = str(getattr(fan, "type_label", "") or "未识别通道")
            groups.setdefault(role, []).append(fan)
        return groups

    def _refresh_channel_label_editor(self) -> None:
        if not hasattr(self, "channel_label_combo"):
            return
        current_key = str(self.channel_label_combo.currentData() or "")
        self.channel_label_combo.blockSignals(True)
        self.channel_label_combo.clear()
        for fan in sorted(self._fans, key=_fan_sort_key):
            key = str(getattr(fan, "identity_key", "") or "")
            if not key:
                continue
            self.channel_label_combo.addItem(self._fan_channel_combo_text(fan), key)
        index = self.channel_label_combo.findData(current_key) if current_key else 0
        if index < 0:
            index = 0
        self.channel_label_combo.setCurrentIndex(index if self.channel_label_combo.count() else -1)
        self.channel_label_combo.blockSignals(False)
        enabled = self.channel_label_combo.count() > 0
        self.channel_label_combo.setEnabled(enabled)
        self.channel_role_combo.setEnabled(enabled)
        self.channel_header_combo.setEnabled(enabled)
        self.channel_alias_input.setEnabled(enabled)
        self.save_channel_label_button.setEnabled(enabled)
        self.confirm_channel_label_button.setEnabled(enabled)
        self.clear_channel_label_button.setEnabled(enabled)
        self.identify_channel_button.setEnabled(enabled)
        for button in self.quick_channel_role_buttons:
            button.setEnabled(enabled)
        if hasattr(self, "confirm_all_candidates_button"):
            self.confirm_all_candidates_button.setEnabled(bool(self._candidate_channel_fans()))
        self._channel_label_selection_changed(self.channel_label_combo.currentIndex())

    def _channel_label_selection_changed(self, _index: int) -> None:
        if not hasattr(self, "channel_label_combo"):
            return
        key = str(self.channel_label_combo.currentData() or "")
        override = self._fan_label_overrides.get(key, {})
        fan = self._fan_by_identity_key(key)
        role = str(override.get("role", "") or getattr(fan, "type_label", "") or "")
        role_index = self.channel_role_combo.findData(role)
        self.channel_role_combo.blockSignals(True)
        self.channel_role_combo.setCurrentIndex(role_index if role_index >= 0 else 0)
        self.channel_role_combo.blockSignals(False)
        header = _known_fan_header_label(
            str(override.get("header", "") or getattr(fan, "header_label", "") or "").rstrip("?")
        )
        header_index = self.channel_header_combo.findData(header)
        self.channel_header_combo.blockSignals(True)
        self.channel_header_combo.setCurrentIndex(header_index if header_index >= 0 else 0)
        self.channel_header_combo.blockSignals(False)
        self.channel_alias_input.setText(str(override.get("alias", "") or ""))
        self._set_channel_evidence(fan)
        self._select_fan_table_row_for_identity(key)

    def _set_channel_evidence(self, fan) -> None:  # noqa: ANN001
        if not hasattr(self, "channel_evidence_label"):
            return
        if fan is None:
            self.channel_evidence_label.setText("加载后选择通道可查看 hwmon label、pwm/fan 路径和识别依据。")
            self.channel_evidence_label.setToolTip("")
            return
        evidence = self._fan_channel_evidence_text(fan)
        self.channel_evidence_label.setText(evidence)
        self.channel_evidence_label.setToolTip(self._fan_detail_text(fan))

    def _fan_channel_evidence_text(self, fan) -> str:  # noqa: ANN001
        channel = str(getattr(fan, "channel_label", "") or "--")
        header = str(getattr(fan, "header_label", "") or "")
        header_basis = str(getattr(fan, "header_basis", "") or "")
        role_basis = str(getattr(fan, "role_basis", "") or "未匹配到可用标签")
        role = str(getattr(fan, "type_label", "") or "未知")
        identity = _fan_identity_state_label(fan)
        speed = self._fan_snapshot_speed_text(fan)
        source = self._fan_display_source(fan)
        path_text = self._fan_compact_path_text(fan)
        sensor = str(getattr(fan, "sensor_label", "") or "未关联")
        sensor_basis = str(getattr(fan, "sensor_basis", "") or "")
        chip = str(getattr(fan, "chip_label", "") or "")
        hwmon_path = str(getattr(fan, "hwmon_path", "") or "")
        header_text = f"{header}（{header_basis or '自动识别'}）" if header else "未确认"
        if identity == "主板候选":
            guidance = (
                "带 ? 的接口来自主板型号候选映射，尚未固定保存；确认物理接口后点“确认当前识别”，"
                "不确定时先点“识别选中通道”。"
            )
        else:
            guidance = "确认后可直接保存为固定标签；不确定时先点“识别选中通道”。"
        sensor_text = f"关联传感器：{sensor}"
        if sensor_basis:
            sensor_text += f"（{sensor_basis}）"
        chip_text = ""
        if chip:
            chip_text = f"芯片：{chip}。"
        if hwmon_path:
            chip_text += f"hwmon：{hwmon_path}。"
        return (
            f"当前识别：{role} · 物理接口：{header_text} · {identity} · {speed}。"
            f"{sensor_text}。{chip_text}证据：{role_basis}。PWM/FAN：{channel}；来源：{source}{path_text}。{guidance}"
        )

    def _fan_channel_combo_text(self, fan) -> str:  # noqa: ANN001
        role = str(getattr(fan, "type_label", "") or "未知")
        name = str(getattr(fan, "name", "") or "")
        raw_channel = str(getattr(fan, "channel_label", "") or "")
        header = str(getattr(fan, "header_label", "") or "")
        identity = _fan_identity_state_label(fan)
        speed = self._fan_snapshot_speed_text(fan)
        primary = name or raw_channel or "风扇通道"
        if identity == "主板候选" and header and not primary.startswith("候选接口 "):
            primary = f"候选接口 {primary}"
        extra_parts = []
        if header and header not in primary:
            extra_parts.append(header)
        if raw_channel and raw_channel not in primary:
            extra_parts.append(raw_channel)
        sensor = str(getattr(fan, "sensor_label", "") or "")
        if sensor:
            extra_parts.append(f"温度源 {sensor}")
        chip = str(getattr(fan, "chip_label", "") or "")
        if chip:
            extra_parts.append(f"芯片 {chip}")
        if extra_parts:
            primary = f"{primary} · {' · '.join(extra_parts)}"
        return f"{primary} · {role} · {identity} · {speed}"

    def _fan_snapshot_speed_text(self, fan) -> str:  # noqa: ANN001
        rpm = self._fan_runtime_speed_value(fan)
        unit = str(getattr(fan, "rpm_unit", "RPM") or "RPM")
        if rpm is None:
            return "等待转速"
        if rpm > 0:
            return f"有转速 {rpm} {unit}"
        return "无转速"

    def _fan_compact_path_text(self, fan) -> str:  # noqa: ANN001
        parts = []
        pwm_path = str(getattr(fan, "pwm_path", "") or "").removeprefix("readonly:")
        rpm_input = str(getattr(fan, "rpm_input", "") or "")
        if pwm_path and not pwm_path.startswith(("nvidia:", "nvidia-smi:")):
            parts.append(f"pwm={pwm_path}")
        if rpm_input and not rpm_input.startswith("nvidia-smi:"):
            parts.append(f"rpm={rpm_input}")
        return "；路径：" + "；".join(parts) if parts else ""

    def save_channel_label_override(self) -> None:
        key = str(self.channel_label_combo.currentData() or "")
        if not key:
            self.status_label.setText("没有可保存的风扇通道标签")
            return
        role = str(self.channel_role_combo.currentData() or "").strip()
        header = _known_fan_header_label(str(self.channel_header_combo.currentData() or ""))
        alias = _clean_fan_alias(self.channel_alias_input.text())
        if role or alias or header:
            self._fan_label_overrides[key] = {"role": role, "alias": alias, "header": header}
        else:
            self._fan_label_overrides.pop(key, None)
        try:
            _save_fan_channel_overrides(self._fan_label_overrides)
        except OSError as error:
            self.status_label.setText(f"风扇通道标签保存失败：{error}")
            return
        self.status_label.setText("风扇通道标签已保存，正在刷新显示...")
        self._reload_after_channel_label_change()

    def clear_channel_label_override(self) -> None:
        key = str(self.channel_label_combo.currentData() or "")
        if not key:
            return
        self._fan_label_overrides.pop(key, None)
        try:
            _save_fan_channel_overrides(self._fan_label_overrides)
        except OSError as error:
            self.status_label.setText(f"风扇通道标签清除失败：{error}")
            return
        self.status_label.setText("风扇通道标签已清除，正在刷新显示...")
        self._reload_after_channel_label_change()

    def apply_quick_channel_label(self, *, role: str, header: str, alias: str) -> None:
        key = str(self.channel_label_combo.currentData() or "")
        if not key:
            self.status_label.setText("没有选中的风扇通道")
            return
        cleaned_role = _canonical_fan_role(role)
        cleaned_header = _known_fan_header_label(header)
        cleaned_alias = _clean_fan_alias(alias)
        self._fan_label_overrides[key] = {
            field: value
            for field, value in {
                "role": cleaned_role,
                "header": cleaned_header,
                "alias": cleaned_alias,
            }.items()
            if value
        }
        try:
            _save_fan_channel_overrides(self._fan_label_overrides)
        except OSError as error:
            self.status_label.setText(f"风扇通道标签保存失败：{error}")
            return
        self.status_label.setText(f"已标记为 {cleaned_role or cleaned_alias or cleaned_header}，正在刷新显示...")
        self._reload_after_channel_label_change()

    def confirm_selected_channel_detection(self) -> None:
        key = str(self.channel_label_combo.currentData() or "")
        fan = self._fan_by_identity_key(key)
        if fan is None:
            self.status_label.setText("没有可确认的风扇通道")
            return
        role = _canonical_fan_role(str(getattr(fan, "type_label", "") or ""))
        header = _known_fan_header_label(str(getattr(fan, "header_label", "") or "").rstrip("?"))
        alias = _clean_fan_alias(self.channel_alias_input.text())
        if role in {"", "未识别通道", "未识别风扇"} and not header and not alias:
            self.status_label.setText("当前通道仍未识别，请先选择角色/接口或用识别脉冲确认")
            return
        self._fan_label_overrides[key] = {
            field: value
            for field, value in {
                "role": role if role not in {"未识别通道", "未识别风扇"} else "",
                "header": header,
                "alias": alias,
            }.items()
            if value
        }
        try:
            _save_fan_channel_overrides(self._fan_label_overrides)
        except OSError as error:
            self.status_label.setText(f"风扇通道识别确认失败：{error}")
            return
        display = alias or header or role
        self.status_label.setText(f"已确认 {display}，后续会固定显示这个物理接口")
        self._reload_after_channel_label_change()

    def confirm_all_candidate_channel_detections(self) -> None:
        candidates = self._candidate_channel_fans()
        if not candidates:
            self.status_label.setText("当前没有可批量确认的主板候选接口")
            return
        saved = 0
        for fan in candidates:
            key = str(getattr(fan, "identity_key", "") or "")
            header = _known_fan_header_label(str(getattr(fan, "header_label", "") or "").rstrip("?"))
            role = _canonical_fan_role(str(getattr(fan, "type_label", "") or ""))
            if not key or not header:
                continue
            self._fan_label_overrides[key] = {
                field: value
                for field, value in {
                    "role": role if role not in {"未识别通道", "未识别风扇"} else "",
                    "header": header,
                    "alias": str(self._fan_label_overrides.get(key, {}).get("alias", "") or ""),
                }.items()
                if value
            }
            saved += 1
        if not saved:
            self.status_label.setText("候选接口缺少可保存的角色或接口名")
            return
        try:
            _save_fan_channel_overrides(self._fan_label_overrides)
        except OSError as error:
            self.status_label.setText(f"批量确认候选接口失败：{error}")
            return
        self.status_label.setText(f"已确认 {saved} 个主板候选接口，后续将按 CPU_FAN/水泵/机箱风扇显示")
        self._reload_after_channel_label_change()

    def _reload_after_channel_label_change(self) -> None:
        if self._loaded or self.monitor is not None:
            self.reload_fan_control(interactive_driver_probe=False)
        else:
            self._refresh_channel_label_editor()

    def _fan_table_selection_changed(self) -> None:
        if not hasattr(self, "fan_table") or not hasattr(self, "channel_label_combo"):
            return
        row = self.fan_table.currentRow()
        if row < 0:
            return
        item = self.fan_table.item(row, 0)
        if item is None:
            return
        fan = self._fan_by_display_name(item.text())
        key = str(getattr(fan, "identity_key", "") or "") if fan is not None else ""
        if not key:
            return
        index = self.channel_label_combo.findData(key)
        if index < 0 or index == self.channel_label_combo.currentIndex():
            return
        self.channel_label_combo.blockSignals(True)
        self.channel_label_combo.setCurrentIndex(index)
        self.channel_label_combo.blockSignals(False)
        self._channel_label_selection_changed(index)

    def _select_fan_table_row_for_identity(self, key: str) -> None:
        if not key or not hasattr(self, "fan_table"):
            return
        for row, fan in enumerate(self._fans):
            if str(getattr(fan, "identity_key", "") or "") != key:
                continue
            if self.fan_table.currentRow() == row:
                return
            self.fan_table.blockSignals(True)
            self.fan_table.selectRow(row)
            self.fan_table.blockSignals(False)
            return

    def _fan_by_identity_key(self, key: str):
        return next((fan for fan in self._fans if str(getattr(fan, "identity_key", "") or "") == key), None)

    def _fan_by_display_name(self, name: str):
        return next((fan for fan in self._fans if str(getattr(fan, "name", "") or "") == name), None)

    def identify_selected_fan_channel(self) -> None:
        key = str(self.channel_label_combo.currentData() or "")
        fan = self._fan_by_identity_key(key)
        if fan is None:
            self.status_label.setText("没有可识别的风扇通道")
            return
        if self.monitor is None:
            self.status_label.setText("请先加载风扇监控，再识别通道")
            return
        if self._fan_is_readonly(fan):
            self.status_label.setText("该通道只有只读转速，不能通过 PWM 脉冲识别")
            return
        set_manual = getattr(self.monitor, "set_fan_manual", None)
        set_auto = getattr(self.monitor, "set_fan_auto", None)
        set_control_enabled = getattr(self.monitor, "set_control_enabled", None)
        if not callable(set_manual) or not callable(set_auto) or not callable(set_control_enabled):
            self.status_label.setText("当前风扇后端不支持通道识别")
            return
        if not self._ensure_pwm_permissions_for_enable():
            return
        was_enabled = self._control_is_enabled()
        if not was_enabled:
            try:
                set_control_enabled(True)
            except Exception as error:
                self.status_label.setText(f"通道识别失败：无法启用 PWM 控制（{error}）")
                return
            self._set_control_state(True)
        min_pwm = int(getattr(fan, "min_pwm", 0))
        max_pwm = int(getattr(fan, "max_pwm", 255))
        pulse_pwm = max(min_pwm, min(max_pwm, 230))
        display_name = str(getattr(fan, "name", "风扇通道"))
        original_name = self._original_fan_name(display_name)
        try:
            set_manual(original_name, pulse_pwm)
        except Exception as error:
            self.status_label.setText(f"通道识别失败：无法写入 {display_name}（{error}）")
            if not was_enabled:
                set_control_enabled(False)
                self._set_control_state(False)
            return
        self._update_fan_pwm(original_name, pulse_pwm)
        self.status_label.setText(
            f"正在识别 {display_name}：约 {round(pulse_pwm / 255 * 100)}% PWM，"
            "请观察哪把风扇/水泵转速或声音变化"
        )
        QTimer.singleShot(3500, lambda: self._finish_fan_channel_identify(original_name, display_name, was_enabled))

    def _finish_fan_channel_identify(self, original_name: str, display_name: str, was_enabled: bool) -> None:
        if self.monitor is None:
            return
        set_auto = getattr(self.monitor, "set_fan_auto", None)
        set_control_enabled = getattr(self.monitor, "set_control_enabled", None)
        try:
            if callable(set_auto):
                set_auto(original_name)
            if not was_enabled and callable(set_control_enabled):
                set_control_enabled(False)
                self._set_control_state(False)
        except Exception as error:
            self.status_label.setText(f"通道识别结束，但恢复自动模式失败：{error}")
            return
        suffix = "已恢复只读模式" if not was_enabled else "已恢复自动曲线"
        self.status_label.setText(f"{display_name} 识别脉冲结束，{suffix}；确认后可保存角色和别名")

    def apply_selected_profile(self) -> None:
        profile_name = self.profile_combo.currentText().strip()
        if not profile_name:
            self.status_label.setText("没有可用风扇策略")
            return
        repaired = False
        try:
            if self._profile_manager is None:
                modules = self._import_modules()
                self._profile_manager = modules["profile"].ProfileManager()
            self._set_active_profile(profile_name)
        except PermissionError as error:
            if self.repair_profile_config_permissions(silent=False):
                repaired = True
                try:
                    self._set_active_profile(profile_name)
                except PermissionError as retry_error:
                    self._show_profile_permission_status("策略切换失败", retry_error)
                    return
                except Exception as retry_error:
                    self.status_label.setText(f"策略切换失败：{retry_error}")
                    return
            else:
                self._show_profile_permission_status("策略切换失败", error)
                return
        except Exception as error:
            self.status_label.setText(f"策略切换失败：{error}")
            return
        self.active_profile_value.setText(profile_name)
        state = "已应用" if self._loaded else "已设为启用后使用"
        prefix = "策略配置权限已修复，" if repaired else ""
        self.status_label.setText(f"{prefix}{state}：{profile_name}")

    def _set_active_profile(self, profile_name: str) -> None:
        _prepare_profile_write_path(_profile_manager_active_path(self._profile_manager))
        self._profile_manager.set_active(profile_name)
        if self.monitor is not None:
            self.monitor._load_active_profile()

    def _show_profile_permission_status(self, action: str, error: OSError) -> None:
        config_dir = _profile_manager_config_dir(self._profile_manager) or _default_profile_config_dir()
        self.status_label.setText(
            f"{action}：策略配置权限不足；目录：{config_dir}；"
            "可在维护 > 权限点击“修复策略权限”；"
            f"{error}"
        )

    def _refresh_profile_options(self) -> None:
        if self._profile_manager is None:
            return
        current = self.profile_combo.currentText()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        try:
            names = sorted(self._profile_manager.list_names())
        except (PermissionError, OSError) as error:
            self.profile_combo.blockSignals(False)
            self._show_profile_permission_status("读取策略失败", error)
            return
        self.profile_combo.addItems(names)
        try:
            active = self._profile_manager.get_active()
        except (PermissionError, OSError):
            active = None
        active_name = active.name if active else ""
        wanted = current or active_name
        if wanted:
            index = self.profile_combo.findText(wanted)
            if index >= 0:
                self.profile_combo.setCurrentIndex(index)
        self.profile_combo.blockSignals(False)
        self._preview_profile_selection()

    def _refresh_summary(self) -> None:
        if not self._fans and self._loaded:
            self.control_state_value.setText("未发现风扇")
        else:
            self.control_state_value.setText("PWM 已启用" if self._control_is_enabled() else "只读")
        self._sync_pwm_action_button()
        fan_summary = self._fan_count_summary_text() if self._loaded or self._fans else "--"
        self.fan_count_value.setText(fan_summary)
        self.fan_count_value.setToolTip(fan_summary if fan_summary != "--" else "")
        self.sensor_count_value.setText(str(len(self._sensors)) if self._loaded or self._sensors else "--")
        self._update_permission_summary()
        self._sync_load_action_button()
        self._update_fan_table_hint()
        self._refresh_fan_visuals()
        self._refresh_channel_label_editor()
        self._preview_profile_selection()
        self._emit_status_changed()

    def _refresh_fan_table(self) -> None:
        self.fan_table.setRowCount(len(self._fans))
        self._update_fan_table_hint()
        self._fan_rows = {}
        for row, fan in enumerate(self._fans):
            name = getattr(fan, "name", "Unknown")
            self._fan_rows[name] = row
            self._rpm_history.setdefault(name, deque(maxlen=90))
            detail = self._fan_detail_text(fan)
            self._set_fan_table_item(row, 0, name, tooltip=detail)
            self._set_fan_table_item(row, 1, str(getattr(fan, "type_label", "--") or "--"))
            self._set_fan_table_item(
                row,
                2,
                self._fan_channel_display_label(fan),
                tooltip="；".join(
                    part
                    for part in (
                        str(getattr(fan, "header_basis", "") or ""),
                        str(getattr(fan, "role_basis", "") or ""),
                        detail,
                    )
                    if part
                ),
            )
            sensor_label = str(getattr(fan, "sensor_label", "") or "--")
            sensor_basis = str(getattr(fan, "sensor_basis", "") or detail)
            self._set_fan_table_item(row, 3, sensor_label, tooltip=sensor_basis)
            rpm = self._fan_runtime_speed_value(fan)
            pwm = self._latest_pwm.get(name)
            self._set_fan_table_item(row, 4, self._format_fan_speed(name, rpm))
            self._set_fan_table_item(
                row,
                5,
                self._format_fan_output(fan, pwm),
                tooltip=_fan_control_output_label(
                    str(getattr(fan, "type_label", "") or ""),
                    read_only=self._fan_is_readonly(fan),
                ),
            )
            evidence = str(getattr(fan, "evidence_text", "") or _fan_identity_evidence_text(fan, compact=True))
            source_tooltip = " · ".join(part for part in (evidence, detail) if part and part != "--")
            self._set_fan_table_item(row, 6, self._fan_display_source(fan), tooltip=source_tooltip)
            self._set_fan_table_item(row, 7, self._fan_status_text(name))
        self.fan_table.resizeColumnsToContents()
        self._refresh_channel_label_editor()
        self._refresh_fan_visuals()

    def _update_fan_table_hint(self) -> None:
        if self._fans:
            self.fan_table_hint.setVisible(False)
            return
        self.fan_table_hint.setVisible(True)
        if not self._loaded:
            self.fan_table_hint.setText("加载只读监控后，这里会显示每个风扇的 RPM、PWM、来源和写入状态。")
            return
        self.fan_table_hint.setText(self._fan_discovery_diagnostics())

    def _fan_discovery_diagnostics(self) -> str:
        hwmon_root = Path("/sys/class/hwmon")
        if not hwmon_root.is_dir():
            return "未发现 /sys/class/hwmon；旧风扇控制后端无法读取主板风扇。"
        fan_like_count, chips = self._hwmon_chip_summaries(hwmon_root)
        if fan_like_count:
            return "检测到 fan/pwm 文件，但旧后端没有生成风扇通道：\n" + "\n".join(chips[:8])
        lines = [
            "旧风扇控制后端已运行，但当前 /sys/class/hwmon 没有 fan*_input 或 pwm* 文件。",
            "这不是 GUI 绘制问题，而是内核还没有把主板风扇控制器暴露给普通 hwmon 接口。",
        ]
        if self._driver_probe_message:
            lines.append(f"驱动探测：{self._driver_probe_message}")
        lines.append(self._candidate_driver_diagnostics())
        lines.append(self._sensor_tool_diagnostics())
        lines.append("下一步：点击“授权加载主板驱动”；如果授权后仍没有 fan/pwm，通常需要安装 lm-sensors 后运行 sensors-detect，或检查 BIOS/内核是否限制 Super I/O 访问。")
        lines.extend(chips[:8])
        return "\n".join(lines)

    def _hwmon_chip_summaries(self, hwmon_root: Path) -> tuple[int, list[str]]:
        chips: list[str] = []
        fan_like_count = 0
        for hwmon in sorted(hwmon_root.glob("hwmon*")):
            name_path = hwmon / "name"
            try:
                chip_name = name_path.read_text(encoding="utf-8").strip()
            except OSError:
                chip_name = hwmon.name
            try:
                names = sorted(path.name for path in hwmon.iterdir())
            except OSError:
                names = []
            fan_like = [name for name in names if name.startswith("fan") or name.startswith("pwm")]
            temp_count = sum(1 for name in names if name.startswith("temp") and name.endswith("_input"))
            fan_like_count += len(fan_like)
            if fan_like:
                suffix = ", ".join(fan_like[:8])
            elif temp_count:
                suffix = f"无 fan/pwm，温度传感器 {temp_count} 个"
            else:
                suffix = "无 fan/pwm"
            chips.append(f"{chip_name or hwmon.name}: {suffix}")
        return fan_like_count, chips

    def _missing_fan_status_text(self) -> str:
        message = "风扇监控已加载，但没有发现 fan/pwm 通道。"
        if self._driver_probe_message:
            probe_message = self._driver_probe_message.rstrip("。.")
            message += f" 驱动探测：{probe_message}。"
        if "授权加载主板驱动" not in message:
            message += " 可以点击“授权加载主板驱动”弹出系统授权窗口，完成后会自动重新扫描。"
        return message

    def _readonly_only_status_text(self) -> str:
        if self._mainboard_fan_interface_visible():
            return "风扇监控已加载：当前只有只读风扇通道，没有可写 PWM 控制"
        parts = [
            f"风扇监控已加载：当前只有 {len(self._fans)} 个只读通道，主板 fan/pwm 没有暴露。"
        ]
        if self._driver_probe_message:
            probe_message = self._driver_probe_message.rstrip("。.")
            parts.append(f"驱动探测：{probe_message}。")
        parts.append("点击“授权加载主板驱动”会弹出系统认证窗口，完成后会自动重新扫描。")
        return " ".join(parts)

    def _candidate_driver_diagnostics(self) -> str:
        modinfo = self._find_system_command("modinfo")
        if not modinfo:
            return "候选驱动模块：无法检查，系统未找到 modinfo"
        parts: list[str] = []
        for module in FAN_HWMON_MODULE_CANDIDATES:
            normalized = module.replace("-", "_")
            if (Path("/sys/module") / normalized).exists():
                parts.append(f"{module}=已加载")
                continue
            available = self._module_available(modinfo, module)
            state = "可加载" if available else "当前内核不可用"
            if module == "nct6683" and available and self._module_parameter_available(modinfo, module, "force"):
                state += "，force=1 可用"
            parts.append(f"{module}={state}")
        return "候选驱动模块：" + "，".join(parts)

    def _sensor_tool_diagnostics(self) -> str:
        sensors = self._find_system_command("sensors")
        sensors_detect = self._find_system_command("sensors-detect")
        sensors_state = "已安装" if sensors else "未安装"
        detect_state = "已安装" if sensors_detect else "未安装"
        return f"lm-sensors 工具：sensors {sensors_state}，sensors-detect {detect_state}"

    def _find_system_command(self, name: str) -> str | None:
        found = shutil.which(name)
        if found:
            return found
        for directory in ("/usr/sbin", "/sbin", "/usr/bin", "/bin"):
            candidate = Path(directory) / name
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)
        return None

    def _module_available(self, modinfo: str, module: str) -> bool:
        try:
            result = subprocess.run(
                [modinfo, "-F", "filename", module],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and bool(result.stdout.strip())

    def _module_parameter_available(self, modinfo: str, module: str, parameter: str) -> bool:
        try:
            result = subprocess.run(
                [modinfo, "-F", "parm", module],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0:
            return False
        prefix = f"{parameter}:"
        return any(line.startswith(prefix) for line in result.stdout.splitlines())

    def _update_fan_rpm(self, fan_name: str, rpm: int) -> None:
        display_name = self._display_fan_name(fan_name)
        self._latest_rpm[display_name] = rpm
        self._rpm_history.setdefault(display_name, deque(maxlen=90)).append(max(0, int(rpm)))
        row = self._fan_row(display_name)
        if row is not None:
            self._set_fan_table_item(row, 4, self._format_fan_speed(display_name, rpm))
            self._set_fan_table_item(row, 7, self._fan_status_text(display_name))
        self._refresh_fan_visuals()

    def _update_fan_pwm(self, fan_name: str, pwm: int) -> None:
        display_name = self._display_fan_name(fan_name)
        self._latest_pwm[display_name] = pwm
        row = self._fan_row(display_name)
        if row is not None:
            fan = self._fan_by_display_name(display_name)
            if fan is None:
                self._set_fan_table_item(row, 5, self._format_pwm(pwm))
            else:
                self._set_fan_table_item(
                    row,
                    5,
                    self._format_fan_output(fan, pwm),
                    tooltip=_fan_control_output_label(
                        str(getattr(fan, "type_label", "") or ""),
                        read_only=self._fan_is_readonly(fan),
                    ),
                )
            self._set_fan_table_item(row, 7, self._fan_status_text(display_name))
        self._refresh_fan_visuals()

    def _update_sensor_value(self, sensor_name: str, value: float) -> None:
        if not math.isfinite(value):
            return
        unit = self._sensor_unit(sensor_name)
        readonly_fan_name = self._readonly_rpm_sensor_aliases.get(sensor_name, sensor_name)
        if unit == "RPM" or readonly_fan_name in self._readonly_rpm_sensor_names:
            self._update_fan_rpm(readonly_fan_name, int(max(0, round(float(value)))))
            return
        if not _is_temperature_chart_sensor(sensor_name, unit):
            return
        self._latest_sensor_values[sensor_name] = float(value)
        self._sensor_history.setdefault(sensor_name, deque(maxlen=90)).append(float(value))
        self._refresh_temperature_chart()

    def _refresh_fan_visuals(self) -> None:
        if not hasattr(self, "rpm_chart"):
            return
        self._rebuild_fan_cards_if_needed()
        fan_names = [str(getattr(fan, "name", "Unknown")) for fan in self._fans]
        for fan in self._fans:
            name = str(getattr(fan, "name", "Unknown"))
            card = self._fan_cards.get(name)
            if card is None:
                continue
            role = str(getattr(fan, "type_label", "") or "未知")
            read_only = self._fan_is_readonly(fan)
            rpm = self._fan_runtime_speed_value(fan)
            pwm = self._latest_pwm.get(name)
            card.update_status(
                name=name,
                rpm=rpm,
                rpm_text=self._format_fan_speed(name, rpm),
                rpm_unit=self._fan_speed_unit(name),
                pwm=self._format_pwm(pwm),
                source=self._fan_display_source(fan),
                status=self._fan_status_text(name),
                role=role,
                channel=self._fan_channel_display_label(fan),
                detail=self._fan_detail_text(fan),
                read_only=read_only,
                control_label=_fan_control_output_label(role, read_only=read_only),
            )
        active_count = sum(1 for name in fan_names if self._fan_has_live_speed(name))
        if fan_names:
            mode = "PWM 写入中" if self._control_is_enabled() else "只读监控"
            if active_count:
                inactive = len(fan_names) - active_count
                suffix = f" · {inactive} 个无转速在明细中" if inactive else ""
                self.visual_status_label.setText(f"{active_count}/{len(fan_names)} 个通道有转速{suffix} · {mode}")
            else:
                self.visual_status_label.setText(f"0/{len(fan_names)} 个通道有转速 · {mode}")
        elif self._loaded:
            self.visual_status_label.setText("未发现风扇通道")
        else:
            self.visual_status_label.setText("等待加载监控")
        self.fan_role_summary_label.setText(self._fan_role_summary_text())
        self.fan_role_speed_label.setText(self._fan_role_speed_summary_text())
        self.fan_identity_overview_label.setText(self._fan_identity_overview_text())
        if hasattr(self, "fan_identity_notice_label"):
            self.fan_identity_notice_label.setText(self._fan_identity_notice_text())
        if hasattr(self, "confirm_all_candidates_button"):
            self.confirm_all_candidates_button.setEnabled(bool(self._candidate_channel_fans()))
        self._refresh_fan_identity_table()
        self._refresh_fan_role_metrics()
        self._refresh_rpm_chart()
        self._refresh_temperature_chart()
        self._emit_status_changed()

    def _refresh_fan_role_metrics(self) -> None:
        if not hasattr(self, "fan_role_metric_cards"):
            return
        groups = self._fan_role_groups() if self._fans else {}
        for role, card in self.fan_role_metric_cards.items():
            fans = groups.get(role, [])
            active = sum(1 for fan in fans if self._fan_has_live_speed(str(getattr(fan, "name", ""))))
            headline, detail = self._fan_role_metric_text(role, fans)
            card.update_status(
                count=len(fans),
                active=active,
                headline=headline,
                detail=detail,
                color=_fan_role_color(role),
            )

    def _fan_role_metric_text(self, role: str, fans: list[object]) -> tuple[str, str]:
        if not fans:
            return "未发现", "加载后会自动归类"
        speed_values = [(fan, self._fan_runtime_speed_value(fan)) for fan in fans]
        live_values = [(fan, value) for fan, value in speed_values if value is not None and value > 0]
        if not live_values:
            return self._fan_role_no_speed_text(role, fans), self._fan_role_metric_detail(fans)
        unit = str(getattr(live_values[0][0], "rpm_unit", "RPM") or "RPM")
        if len(live_values) == 1:
            fan, value = live_values[0]
            headline = f"{self._fan_short_channel_label(fan)} {value} {unit}"
        else:
            average = round(sum(value for _fan, value in live_values) / len(live_values))
            headline = f"{len(live_values)}/{len(fans)} 有转速 · 平均 {average} {unit}"
        return headline, self._fan_role_metric_detail(fans)

    def _fan_role_no_speed_text(self, role: str, fans: list[object]) -> str:
        if any(self._fan_runtime_speed_value(fan) == 0 for fan in fans):
            return "无转速"
        if role == "水泵/AIO":
            return "等待水泵转速"
        return "等待转速"

    def _fan_role_metric_detail(self, fans: list[object]) -> str:
        labels: list[str] = []
        for fan in fans[:3]:
            label = self._fan_short_channel_label(fan)
            identity = _fan_identity_state_label(fan)
            if identity in {"主板候选", "需标定"}:
                label = f"{label}（{identity}）"
            labels.append(label)
        if len(fans) > 3:
            labels.append(f"另 {len(fans) - 3} 个")
        return "、".join(labels) if labels else "--"

    def _candidate_channel_fans(self) -> list[object]:
        return [
            fan
            for fan in sorted(self._fans, key=_fan_sort_key)
            if _fan_identity_state_label(fan) == "主板候选"
            and bool(_known_fan_header_label(str(getattr(fan, "header_label", "") or "").rstrip("?")))
        ]

    def _fan_identity_notice_text(self) -> str:
        if not self._fans:
            return "加载后会提示哪些 PWM/FAN 可以候选映射到 CPU_FAN、水泵和机箱风扇。"
        candidates = self._candidate_channel_fans()
        if candidates:
            parts = [
                f"{self._fan_short_channel_label(fan)} {self._fan_compact_speed_text(fan)}"
                for fan in candidates[:6]
            ]
            if len(candidates) > 6:
                parts.append(f"另 {len(candidates) - 6} 路")
            return (
                "检测到主板候选接口："
                + "、".join(parts)
                + "。带 ? 的接口尚未写入本机配置；确认物理位置后可以批量确认，之后界面会固定显示 CPU_FAN、水泵和机箱风扇。"
            )
        unconfirmed = [
            fan
            for fan in self._fans
            if _fan_identity_state_label(fan) in {"需标定", "名称推断"}
        ]
        if unconfirmed:
            return (
                f"还有 {len(unconfirmed)} 路接口需要标定。选择通道后可用识别脉冲观察哪把风扇变化，"
                "再保存为 CPU 风扇、水泵/AIO 或机箱风扇。"
            )
        return "风扇接口已完成识别或为只读监控；底层 PWM/FAN 路径仍保留在明细提示中。"

    def _fan_runtime_speed_value(self, fan) -> int | None:  # noqa: ANN001
        name = str(getattr(fan, "name", "") or "")
        value = self._latest_rpm.get(name)
        if value is None:
            value = self._read_fan_runtime_rpm(fan)
        return value

    def _fan_runtime_speed_value_by_name(self, fan_name: str) -> int | None:
        value = self._latest_rpm.get(fan_name)
        if value is not None:
            return value
        fan = self._fan_by_display_name(fan_name)
        if fan is None:
            return None
        return self._read_fan_runtime_rpm(fan)

    def _rebuild_fan_cards_if_needed(self) -> None:
        fan_names = self._overview_fan_names()
        if fan_names == list(self._fan_cards):
            return
        self._clear_fan_cards()
        if not fan_names:
            self.fan_cards_empty_label.setVisible(True)
            self.fan_cards_layout.addWidget(self.fan_cards_empty_label, 0, 0)
            return
        self.fan_cards_empty_label.setVisible(False)
        columns = 3 if len(fan_names) > 2 else max(1, len(fan_names))
        for index, name in enumerate(fan_names):
            card = FanStatusCard(name)
            self._fan_cards[name] = card
            self.fan_cards_layout.addWidget(card, index // columns, index % columns)

    def _overview_fan_names(self) -> list[str]:
        return [str(getattr(fan, "name", "Unknown")) for fan in sorted(self._fans, key=_fan_sort_key)]

    def _clear_fan_cards(self) -> None:
        if not hasattr(self, "fan_cards_layout"):
            return
        while self.fan_cards_layout.count():
            item = self.fan_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            widget.setParent(self.fan_cards_container)
            if widget is not self.fan_cards_empty_label:
                widget.deleteLater()
        self._fan_cards = {}

    def _refresh_rpm_chart(self) -> None:
        if not hasattr(self, "rpm_chart"):
            return
        fan_names = [str(getattr(fan, "name", "Unknown")) for fan in self._fans]
        rpm_names = [name for name in fan_names if self._fan_speed_unit(name) == "RPM"]
        active_names = [name for name in rpm_names if self._latest_rpm.get(name, 0) > 0]
        chart_names = active_names or rpm_names
        ordered_names = sorted(chart_names, key=lambda name: (-self._latest_rpm.get(name, 0), name))
        series = {
            name: list(self._rpm_history.get(name, ()))
            for name in ordered_names[:6]
            if self._rpm_history.get(name)
        }
        self.rpm_chart.set_series(series)

    def _refresh_temperature_chart(self) -> None:
        if not hasattr(self, "temperature_chart"):
            return
        names = sorted(self._sensor_history, key=_temperature_sensor_priority)[:4]
        series = {name: list(self._sensor_history[name]) for name in names if self._sensor_history[name]}
        self.temperature_chart.set_series(series)

    def _sensor_unit(self, sensor_name: str) -> str:
        for sensor in self._sensors:
            if str(getattr(sensor, "name", "")) == sensor_name:
                return str(getattr(sensor, "unit", ""))
        return ""

    def _fan_row(self, fan_name: str) -> int | None:
        return self._fan_rows.get(fan_name)

    def _set_fan_table_item(self, row: int, column: int, text: str, *, tooltip: str = "") -> None:
        item = QTableWidgetItem(text)
        if tooltip and tooltip != "--":
            item.setToolTip(tooltip)
        if column in (4, 5):
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        else:
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        if column == 1:
            item.setForeground(QColor(_fan_role_color(text)))
        if column == 7:
            if "PWM 写入中" in text or "已连接" in text:
                item.setForeground(QColor("#b7dfd2"))
            elif "无转速" in text:
                item.setForeground(QColor("#e9c0c4"))
            elif "等待" in text:
                item.setForeground(QColor("#d9bc79"))
        self.fan_table.setItem(row, column, item)

    def _set_fan_identity_table_item(self, row: int, column: int, text: str, *, tooltip: str = "") -> None:
        item = QTableWidgetItem(text)
        if tooltip and tooltip != "--":
            item.setToolTip(tooltip)
        if column == 1:
            full_role = {
                "CPU": "CPU 风扇",
                "水泵": "水泵/AIO",
                "机箱": "机箱风扇",
                "GPU": "GPU 风扇",
            }.get(text, text)
            item.setForeground(QColor(_fan_role_color(full_role)))
        elif column == 2:
            if "无转速" in text:
                item.setForeground(QColor("#e9c0c4"))
            elif "等待" in text:
                item.setForeground(QColor("#d9bc79"))
            else:
                item.setForeground(QColor("#b7dfd2"))
        elif column == 3:
            if text in {"已确认", "手动", "名称推断"}:
                item.setForeground(QColor("#b7dfd2"))
            elif text == "候选":
                item.setForeground(QColor("#d9bc79"))
            elif text == "待标定":
                item.setForeground(QColor("#e9c0c4"))
        self.fan_identity_table.setItem(row, column, item)

    def _fan_source(self, fan) -> str:  # noqa: ANN001
        pwm_path = str(getattr(fan, "pwm_path", ""))
        if pwm_path.startswith("readonly:nvidia-smi:"):
            return "NVIDIA 只读"
        if self._fan_is_readonly(fan):
            return "RPM 只读"
        if pwm_path.startswith("nvidia:"):
            return "NVIDIA"
        return "主板"

    def _fan_display_source(self, fan) -> str:  # noqa: ANN001
        source = self._fan_source(fan)
        hwmon_name = str(getattr(fan, "hwmon_name", "") or "")
        if source == "主板" and hwmon_name:
            return f"{source} · {hwmon_name}"
        return source

    def _fan_detail_text(self, fan) -> str:  # noqa: ANN001
        detail = str(getattr(fan, "detail_text", "") or "")
        if detail:
            return detail
        path = str(getattr(fan, "pwm_path", "") or getattr(fan, "rpm_input", "") or "")
        return path or "--"

    def _control_is_enabled(self) -> bool:
        return bool(getattr(self.monitor, "control_enabled", False))

    def _set_control_state(self, enabled: bool) -> None:
        self.enable_control_button.setText("暂停 PWM 控制" if enabled else "启用 PWM 控制")
        self.control_state_value.setText("PWM 已启用" if enabled else "只读")
        self._update_permission_summary()
        self._sync_pwm_action_button()
        self._refresh_fan_visuals()
        self._emit_status_changed()

    def _sync_pwm_action_button(self) -> None:
        if not hasattr(self, "enable_control_button"):
            return
        if not self._loaded:
            self.enable_control_button.setEnabled(False)
            self.enable_control_button.setText("启用 PWM 控制")
            return
        if self._control_is_enabled():
            self.enable_control_button.setEnabled(True)
            self.enable_control_button.setText("暂停 PWM 控制")
            return
        if self._has_controllable_fans():
            self.enable_control_button.setEnabled(True)
            self.enable_control_button.setText("启用 PWM 控制")
            return
        if self._pwm_action_requests_driver_probe():
            self.enable_control_button.setEnabled(True)
            self.enable_control_button.setText("授权加载主板 PWM")
            return
        self.enable_control_button.setEnabled(False)
        self.enable_control_button.setText("启用 PWM 控制")

    def _sync_load_action_button(self) -> None:
        if not hasattr(self, "load_button"):
            return
        if self._loading:
            self.load_button.setEnabled(False)
            self.load_button.setText("加载中...")
            return
        self.load_button.setEnabled(True)
        if self._pwm_action_requests_driver_probe():
            self.load_button.setText("授权扫描主板风扇")
        elif self._loaded:
            self.load_button.setText("重新扫描风扇")
        else:
            self.load_button.setText("加载/扫描风扇")

    def _pwm_action_requests_driver_probe(self) -> bool:
        return self._loaded and not self._has_controllable_fans() and not self._mainboard_fan_interface_visible()

    def _can_enable_pwm_control(self) -> bool:
        if not self._has_controllable_fans():
            return False
        if os.geteuid() == 0:
            return True
        details = self._pwm_permission_details()
        if not details:
            return True
        return all(
            bool(
                detail["exists"]
                and detail["writable"]
                and (not detail["enable_exists"] or detail["enable_writable"])
            )
            for detail in details
        )

    def _update_permission_summary(self) -> None:
        self.permission_value.setText(self._permission_summary_text())
        if hasattr(self, "permission_detail_text"):
            self.permission_detail_text.setPlainText(self._permission_detail_text())
        if hasattr(self, "copy_permission_commands_button"):
            commands_available = bool(self._permission_fix_commands())
            self.copy_permission_commands_button.setEnabled(commands_available)
        if hasattr(self, "grant_permissions_button"):
            self.grant_permissions_button.setEnabled(bool(self._permission_grant_commands()))
        if hasattr(self, "permission_wizard_text"):
            self.run_permission_diagnostics(initial=True)

    def _permission_summary_text(self) -> str:
        if self._loaded and not self._has_controllable_fans() and not self._mainboard_fan_interface_visible():
            return "需驱动授权"
        if self._fans and not self._has_controllable_fans():
            return "只读通道"
        if os.geteuid() == 0:
            return "root 会话"
        sysfs_fans = [
            fan
            for fan in self._fans
            if self._fan_is_sysfs_pwm(fan)
        ]
        if not sysfs_fans:
            return "只读通道" if self._fans else "普通用户"
        writable = sum(1 for fan in sysfs_fans if os.access(str(getattr(fan, "pwm_path", "")), os.W_OK))
        if writable == len(sysfs_fans):
            return "PWM 可写"
        if writable:
            return f"部分可写 {writable}/{len(sysfs_fans)}"
        return "需 sudo/udev"

    def refresh_pwm_permissions(self) -> None:
        self._update_permission_summary()
        self.status_label.setText(f"PWM 权限已刷新：{self.permission_value.text()}")

    def run_permission_diagnostics(self, initial: bool = False) -> None:
        if not hasattr(self, "permission_wizard_text"):
            return
        self.permission_wizard_text.setPlainText("\n".join(self._permission_diagnostics_lines()))
        if not initial:
            self.status_label.setText(f"权限诊断完成：{self.permission_value.text()}")

    def grant_pwm_permissions(self, silent: bool = False) -> bool:
        commands = self._permission_grant_commands()
        if not commands:
            self._update_permission_summary()
            ready = self._can_enable_pwm_control()
            if not silent:
                if ready:
                    self.status_label.setText(f"无需额外授权：{self.permission_value.text()}")
                else:
                    self.status_label.setText("没有可自动授权的 PWM 文件；可复制授权命令后手动执行。")
            return ready
        interactive = not silent
        if interactive:
            self.status_label.setText("正在请求系统权限；请在系统弹窗中输入密码...")
            QApplication.processEvents()
        ok, message = self._run_permission_grant(commands, interactive=interactive)
        self._update_permission_summary()
        if ok:
            ready = self._can_enable_pwm_control()
            if ready:
                self.status_label.setText(f"PWM 权限已授权：{self.permission_value.text()}")
            else:
                self.status_label.setText("PWM 授权命令已执行，但文件仍不可写；请刷新权限后再试。")
            return ready
        if silent:
            self.status_label.setText(f"PWM 自动授权失败：{message}。可点击复制授权命令后手动执行。")
        else:
            self.status_label.setText(f"PWM 系统授权失败：{message}。可复制授权命令后手动执行。")
        return False

    def repair_profile_config_permissions(self, silent: bool = False) -> bool:
        config_dir = _profile_manager_config_dir(self._profile_manager) or _default_profile_config_dir()
        if not silent:
            self.status_label.setText("正在请求系统权限修复策略配置目录，请在系统弹窗中输入密码...")
            QApplication.processEvents()
        ok, message = _repair_profile_config_permissions(config_dir, interactive=not silent)
        if ok:
            if not silent:
                self.status_label.setText(f"策略配置权限已修复：{config_dir}")
            self._refresh_profile_options()
            return True
        if not silent:
            self.status_label.setText(f"策略配置权限修复失败：{message}")
        return False

    def copy_pwm_permission_commands(self) -> None:
        commands = self._permission_fix_commands()
        if not commands:
            self.status_label.setText("当前没有需要复制的 PWM 授权命令")
            return
        QApplication.clipboard().setText(commands)
        self.status_label.setText("已复制临时 PWM 授权命令；在终端执行后点击刷新权限")

    def copy_permanent_permission_rules(self) -> None:
        QApplication.clipboard().setText(self._permanent_permission_rules())
        self.status_label.setText("已复制长期 PWM 权限规则模板；安装后重启或重新加载 tmpfiles")

    def _permission_diagnostics_lines(self) -> list[str]:
        lines = [
            f"会话权限：{'root' if os.geteuid() == 0 else '普通用户'}",
            f"当前组：{self._current_group_name()}",
            f"风扇监控：{'已加载' if self._loaded else '未加载'}",
            f"PWM 状态：{self.permission_value.text() if hasattr(self, 'permission_value') else self._permission_summary_text()}",
            f"策略配置：{self._profile_config_permission_summary()}",
            f"系统授权弹窗：{'可用' if shutil.which('pkexec') else '未找到 pkexec'}",
        ]
        details = self._pwm_permission_details()
        if details:
            missing = sum(1 for detail in details if not detail["exists"])
            blocked = sum(
                1
                for detail in details
                if detail["exists"]
                and (
                    not detail["writable"]
                    or (detail["enable_exists"] and not detail["enable_writable"])
                )
            )
            lines.append(f"主板 PWM 文件：{len(details)} 个，缺失 {missing} 个，需授权 {blocked} 个")
        elif self._loaded and self._fans and not self._has_controllable_fans() and not self._mainboard_fan_interface_visible():
            lines.append("主板 PWM 文件：未暴露；当前只显示 NVIDIA/只读风扇通道")
        elif self._loaded:
            lines.append("主板 PWM 文件：未发现需要授权的 sysfs PWM 通道")
        else:
            lines.append("主板 PWM 文件：加载风扇监控后可诊断")
        if self._loaded and not self._fans:
            lines.append(f"驱动探测：{self._driver_probe_message or '尚未请求加载主板驱动'}")
        lines.append("长期方案：复制规则模板后安装到 /etc/tmpfiles.d/，避免每次手动 chmod")
        return lines

    def _profile_config_permission_summary(self) -> str:
        config_dir = _profile_manager_config_dir(self._profile_manager) or _default_profile_config_dir()
        if config_dir.exists():
            writable = os.access(config_dir, os.W_OK)
            active_path = config_dir / ".active"
            if active_path.exists() and not os.access(active_path, os.W_OK):
                return f"需修复 .active：{active_path}"
            return f"{'可写' if writable else '需修复'}：{config_dir}"
        parent = config_dir.parent
        return f"{'可创建' if os.access(parent, os.W_OK) else '需修复父目录'}：{config_dir}"

    def _permanent_permission_rules(self) -> str:
        group_name = self._current_group_name()
        return "\n".join(
            (
                "# /etc/tmpfiles.d/lumen-hub-pwm.conf",
                "# 让普通用户组长期获得 hwmon PWM 写入权限；不同主板暴露的 hwmon 编号可能会变化",
                f"z /sys/class/hwmon/hwmon*/pwm[0-9] 0660 - {group_name} -",
                f"z /sys/class/hwmon/hwmon*/pwm[0-9]_enable 0660 - {group_name} -",
                "",
                "# 安装后执行：sudo systemd-tmpfiles --create /etc/tmpfiles.d/lumen-hub-pwm.conf",
            )
        )

    def _permission_detail_text(self) -> str:
        details = self._pwm_permission_details()
        if self._fans and not self._has_controllable_fans():
            if not self._mainboard_fan_interface_visible():
                return "\n\n".join((self._readonly_only_status_text(), self._fan_discovery_diagnostics()))
            return "当前只有只读风扇通道；可以显示转速或占空比，但没有可写 PWM 文件。"
        if os.geteuid() == 0:
            return "当前 GUI 以 root 身份运行，sysfs PWM 写入权限通常可用。"
        if not self._fans:
            if self._loaded:
                return self._fan_discovery_diagnostics()
            return "尚未加载风扇监控。加载后会列出每个主板 PWM 文件的写入权限。"
        if not details:
            if self._has_controllable_fans():
                return "未发现需要 sysfs 授权的主板 PWM 通道；NVIDIA 风扇由 NVIDIA 接口控制。"
            return "当前只有只读风扇通道；可以显示转速或占空比，但没有可写 PWM 文件。"
        lines = []
        for detail in details:
            path_status = self._permission_path_status(detail["exists"], detail["writable"])
            enable_status = self._permission_path_status(detail["enable_exists"], detail["enable_writable"])
            lines.append(
                f"{detail['name']}（{detail['source']}）| PWM {path_status}: {detail['path']}\n"
                f"  enable {enable_status}: {detail['enable_path']}\n"
                f"  线索：{detail['detail']}"
            )
        commands = self._permission_fix_commands()
        if commands:
            lines.append("可点击“请求系统权限”弹出系统认证窗口，或复制授权命令后在终端执行。")
        return "\n".join(lines)

    def _permission_path_status(self, exists: bool, writable: bool) -> str:
        if not exists:
            return "不存在"
        return "可写" if writable else "不可写"

    def _permission_fix_commands(self) -> str:
        if os.geteuid() == 0:
            return ""
        command_paths = self._permission_command_paths()
        if not command_paths:
            return ""
        quoted_paths = " ".join(shlex.quote(path) for path in command_paths)
        return "\n".join(
            (
                "# 临时授权当前用户组写入 PWM；重启或设备重枚举后可能失效",
                f"sudo chgrp \"$(id -gn)\" {quoted_paths}",
                f"sudo chmod g+rw {quoted_paths}",
            )
        )

    def _permission_grant_commands(self) -> str:
        command_paths = self._permission_command_paths()
        if not command_paths:
            return ""
        quoted_paths = " ".join(shlex.quote(path) for path in command_paths)
        group_name = self._current_group_name()
        return "\n".join(
            (
                f"chgrp {shlex.quote(group_name)} {quoted_paths}",
                f"chmod g+rw {quoted_paths}",
            )
        )

    def _permission_command_paths(self) -> list[str]:
        command_paths: list[str] = []
        for detail in self._pwm_permission_details():
            if detail["exists"] and not detail["writable"]:
                command_paths.append(str(detail["path"]))
            if detail["enable_exists"] and not detail["enable_writable"]:
                command_paths.append(str(detail["enable_path"]))
        return list(dict.fromkeys(command_paths))

    def _current_group_name(self) -> str:
        try:
            return grp.getgrgid(os.getgid()).gr_name
        except KeyError:
            return str(os.getgid())

    def _run_permission_grant(self, commands: str, *, interactive: bool = True) -> tuple[bool, str]:
        timeout = 120 if interactive else 45
        return self._run_privileged_shell(commands, timeout=timeout, interactive=interactive)

    def _run_privileged_shell(self, commands: str, *, timeout: int, interactive: bool = False) -> tuple[bool, str]:
        if os.geteuid() == 0:
            command = ["/bin/sh", "-c", commands]
        elif interactive:
            pkexec = shutil.which("pkexec")
            if not pkexec:
                return False, "未找到 pkexec，无法弹出系统授权窗口"
            command = [pkexec, "/bin/sh", "-c", commands]
        else:
            sudo = shutil.which("sudo")
            if not sudo:
                return False, "未找到可用的非交互式 sudo"
            command = [sudo, "-n", "/bin/sh", "-c", commands]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, "授权等待超时"
        except OSError as error:
            return False, str(error)
        if result.returncode == 0:
            return True, (result.stdout or "").strip() or "ok"
        message = (result.stderr or result.stdout or "").strip()
        if not message:
            message = f"授权命令退出码 {result.returncode}"
        return False, message.splitlines()[-1]

    def _pwm_permission_details(self) -> list[dict[str, object]]:
        details: list[dict[str, object]] = []
        for fan in self._fans:
            path = str(getattr(fan, "pwm_path", ""))
            if not self._fan_is_sysfs_pwm(fan):
                continue
            enable_path = f"{path}_enable"
            details.append(
                {
                    "name": str(getattr(fan, "name", "Unknown")),
                    "source": self._fan_display_source(fan),
                    "detail": self._fan_detail_text(fan),
                    "path": path,
                    "exists": os.path.exists(path),
                    "writable": os.path.exists(path) and os.access(path, os.W_OK),
                    "enable_path": enable_path,
                    "enable_exists": os.path.exists(enable_path),
                    "enable_writable": os.path.exists(enable_path) and os.access(enable_path, os.W_OK),
                }
            )
        return details

    def _preview_profile_selection(self) -> None:
        if self._profile_manager is None:
            self.active_profile_value.setText("--")
            return
        selected_name = self.profile_combo.currentText().strip()
        active = self._profile_manager.get_active()
        active_name = active.name if active else ""
        if active_name:
            suffix = "" if selected_name == active_name else f"（待切换到 {selected_name}）"
            self.active_profile_value.setText(f"{active_name}{suffix}")
        else:
            self.active_profile_value.setText(f"{selected_name}（待应用）" if selected_name else "--")

    def _format_pwm(self, pwm: int | None) -> str:
        if pwm is None:
            return "--"
        return f"{round(pwm / 255 * 100)}% ({pwm})"

    def _format_fan_output(self, fan, pwm: int | None) -> str:  # noqa: ANN001
        if self._fan_is_readonly(fan):
            return "只读转速"
        return self._format_pwm(pwm)

    def _format_fan_speed(self, fan_name: str, value: int | None) -> str:
        if value is None:
            return "--"
        unit = self._fan_speed_unit(fan_name)
        if unit == "%":
            return f"{value}%"
        return f"{value} RPM"

    def _fan_speed_unit(self, fan_name: str) -> str:
        for fan in self._fans:
            if str(getattr(fan, "name", "")) == fan_name:
                return str(getattr(fan, "rpm_unit", "RPM") or "RPM")
        return "RPM"

    def _fan_has_live_speed(self, fan_name: str) -> bool:
        value = self._fan_runtime_speed_value_by_name(fan_name)
        if value is None:
            return False
        if self._fan_speed_unit(fan_name) == "%":
            return True
        return value > 0

    def _fan_status_text(self, fan_name: str) -> str:
        if self.monitor is None:
            return "--"
        fan = self._fan_by_display_name(fan_name)
        role = str(getattr(fan, "type_label", "") or "") if fan is not None else ""
        if fan_name in self._readonly_rpm_sensor_names:
            rpm = self._fan_runtime_speed_value_by_name(fan_name)
            if rpm is None:
                return "等待转速，只读"
            if self._fan_speed_unit(fan_name) == "%":
                return "已连接，只读"
            if rpm > 0:
                return "已连接，只读"
            return "水泵无转速，只读" if role == "水泵/AIO" else "无转速，只读"
        if self._control_is_enabled():
            return "PWM 写入中" if fan_name in self._latest_pwm else "等待 PWM"
        rpm = self._fan_runtime_speed_value_by_name(fan_name)
        if rpm is None:
            return "等待转速"
        if rpm > 0:
            return "已连接，只读"
        return "水泵无转速，只读" if role == "水泵/AIO" else "无转速，只读"

    def _fan_is_readonly(self, fan) -> bool:  # noqa: ANN001
        return bool(getattr(fan, "read_only", False)) or str(getattr(fan, "pwm_path", "")).startswith("readonly:")

    def _fan_is_sysfs_pwm(self, fan) -> bool:  # noqa: ANN001
        path = str(getattr(fan, "pwm_path", ""))
        return bool(path) and not path.startswith("nvidia:") and not path.startswith("readonly:")

    def _has_controllable_fans(self) -> bool:
        return any(not self._fan_is_readonly(fan) for fan in self._fans)

    def _refresh_fan_table_status(self) -> None:
        for fan_name, row in self._fan_rows.items():
            self._set_fan_table_item(row, 7, self._fan_status_text(fan_name))
        self._refresh_fan_visuals()

    def _summary_card(self, title: str, value: QLabel) -> QFrame:
        card = QFrame()
        card.setObjectName("MetricCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(6)
        title_label = QLabel(title)
        title_label.setObjectName("SectionLabel")
        value.setObjectName("HomeMetricValue")
        value.setWordWrap(True)
        card_layout.addWidget(title_label)
        card_layout.addWidget(value, 1)
        return card

    def _tab_placeholder(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldHint")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        return label


class _FallbackFanDashboard(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        self._cards: dict[str, QLabel] = {}

        burn_layout = QHBoxLayout()
        self._btn_cpu = QPushButton("CPU 烤机")
        self._btn_fpu = QPushButton("FPU 烤机")
        self._btn_gpu = QPushButton("GPU 烤机")
        self._btn_stop = QPushButton("停止全部")
        for button in (self._btn_cpu, self._btn_fpu, self._btn_gpu, self._btn_stop):
            burn_layout.addWidget(button)
        burn_layout.addStretch(1)
        layout.addLayout(burn_layout)

        self._sensor_layout = QVBoxLayout()
        self._sensor_layout.setSpacing(6)
        layout.addLayout(self._sensor_layout)
        layout.addStretch(1)

    def populate_sensors(self, sensors) -> None:  # noqa: ANN001
        for sensor in sensors:
            if sensor.name in self._cards:
                continue
            label = QLabel(f"{sensor.name}: -- {sensor.unit}")
            label.setObjectName("ChecklistItem")
            self._cards[sensor.name] = label
            self._sensor_layout.addWidget(label)

    def update_sensor(self, name: str, value: float) -> None:
        label = self._cards.get(name)
        if label is None:
            return
        label.setText(f"{name}: {value:.1f}")


class _FallbackHistoryView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        label = QLabel("当前环境缺少 PySide6.QtCharts，历史曲线图不可用。")
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch(1)

    def refresh_sensors(self) -> None:
        return
