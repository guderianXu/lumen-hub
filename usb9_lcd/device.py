from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


TARGET_VENDOR_ID = "0b05"
TARGET_PRODUCT_ID = "1c7b"
TARGET_VENDOR_ID_INT = int(TARGET_VENDOR_ID, 16)
TARGET_PRODUCT_ID_INT = int(TARGET_PRODUCT_ID, 16)


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


def discover_from_hidapi(
    enumerate_devices: Callable[..., list[dict[str, Any]]] | None = None,
) -> list[HidInterface]:
    if enumerate_devices is None:
        try:
            import hid  # type: ignore[import-not-found]
        except ImportError:
            return []
        enumerate_devices = hid.enumerate

    interfaces: list[HidInterface] = []
    for item in _hidapi_target_items(enumerate_devices):
        path_text = _hidapi_path_text(item.get("path"))
        if not path_text:
            continue
        interface_number = _hidapi_interface_number(item)
        report_size = _hidapi_report_size(interface_number)
        if report_size <= 0:
            continue
        product = str(item.get("product_string") or "ASUS TUF Gaming LC III LCD")
        interfaces.append(
            HidInterface(
                path=Path(path_text),
                name=f"{product} MI_{interface_number:02d}",
                report_size=report_size,
                can_read=True,
                can_write=True,
            )
        )
    return sorted(interfaces, key=lambda interface: (interface.report_size, interface.name, str(interface.path)))


def _hidapi_target_items(enumerate_devices: Callable[..., list[dict[str, Any]]]) -> list[dict[str, Any]]:
    exact = list(enumerate_devices(TARGET_VENDOR_ID_INT, TARGET_PRODUCT_ID_INT))
    if exact:
        return exact
    try:
        all_devices = enumerate_devices()
    except TypeError:
        return []
    return [item for item in all_devices if _is_hidapi_asus_lcd_candidate(item)]


def choose_interfaces(interfaces: list[HidInterface]) -> tuple[HidInterface, HidInterface]:
    control = next((interface for interface in interfaces if interface.report_size == 440), None)
    data = next((interface for interface in interfaces if interface.report_size == 1024), None)
    if control is None or data is None:
        sizes = ", ".join(str(interface.report_size) for interface in interfaces) or "none"
        raise ValueError(f"expected HID report sizes 440 and 1024 for ASUS LCD; found {sizes}")

    return control, data


def discover_lcd_interfaces() -> list[HidInterface]:
    interfaces = discover_from_sysfs()
    if interfaces:
        return interfaces
    return discover_from_hidapi()


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


def _hidapi_path_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _is_hidapi_asus_lcd_candidate(item: dict[str, Any]) -> bool:
    if item.get("vendor_id") != TARGET_VENDOR_ID_INT:
        return False
    product = str(item.get("product_string") or "").lower()
    path_text = _hidapi_path_text(item.get("path")).lower()
    haystack = f"{product} {path_text}"
    return "lcd" in haystack and ("tuf" in haystack or "lc iii" in haystack or "lc 3" in haystack)


def _hidapi_interface_number(item: dict[str, Any]) -> int:
    value = item.get("interface_number")
    if isinstance(value, int) and value >= 0:
        return value
    path_text = _hidapi_path_text(item.get("path")).lower()
    if "mi_00" in path_text:
        return 0
    if "mi_01" in path_text:
        return 1
    return -1


def _hidapi_report_size(interface_number: int) -> int:
    if interface_number == 0:
        return 440
    if interface_number == 1:
        return 1024
    return 0


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
