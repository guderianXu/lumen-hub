from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


TARGET_VENDOR_ID = "0b05"
TARGET_PRODUCT_ID = "1c7b"


@dataclass(frozen=True)
class HidInterface:
    path: Path
    name: str
    report_size: int
    can_read: bool
    can_write: bool


def discover_from_sysfs(sys_root: Path = Path("/sys"), dev_root: Path = Path("/dev")) -> list[HidInterface]:
    hidraw_root = sys_root / "class" / "hidraw"
    interfaces: list[HidInterface] = []

    for hidraw in sorted(hidraw_root.glob("hidraw*"), key=lambda path: path.name):
        device = hidraw / "device"
        uevent = _read_text(device / "uevent")
        if not _is_target_lcd(uevent):
            continue

        report = _read_bytes(device / "report_descriptor")
        if report is None:
            continue

        report_size = _report_size(report)
        if report_size <= 0:
            continue

        path = dev_root / hidraw.name
        interfaces.append(
            HidInterface(
                path=path,
                name=hidraw.name,
                report_size=report_size,
                can_read=_can_access(path, os.R_OK),
                can_write=_can_access(path, os.W_OK),
            )
        )

    return interfaces


def choose_interfaces(interfaces: list[HidInterface]) -> tuple[HidInterface, HidInterface]:
    control = next((interface for interface in interfaces if interface.report_size == 440), None)
    data = next((interface for interface in interfaces if interface.report_size == 1024), None)
    if control is None or data is None:
        sizes = ", ".join(str(interface.report_size) for interface in interfaces) or "none"
        raise ValueError(f"expected HID report sizes 440 and 1024 for ASUS LCD; found {sizes}")

    return control, data


def _is_target_lcd(uevent: str) -> bool:
    for line in uevent.splitlines():
        if not line.startswith("HID_ID="):
            continue

        parts = line.removeprefix("HID_ID=").split(":")
        if len(parts) != 3:
            return False

        try:
            vendor = f"{int(parts[1], 16):04x}"
            product = f"{int(parts[2], 16):04x}"
        except ValueError:
            return False
        return vendor == TARGET_VENDOR_ID and product == TARGET_PRODUCT_ID

    return False


def _report_size(report: bytes) -> int:
    if bytes.fromhex("960004") in report:
        return 1024
    if bytes.fromhex("96b801") in report:
        return 440
    if bytes.fromhex("961000") in report:
        return 16
    return 0


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _can_access(path: Path, mode: int) -> bool:
    return os.access(path, mode)
