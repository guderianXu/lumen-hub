from __future__ import annotations

from dataclasses import dataclass, field

from usb9_lcd.drivers.base import DisplayDevice
from usb9_lcd.monitoring.models import SystemTelemetry
from usb9_lcd.service.permissions import PermissionHelperStatus


@dataclass(frozen=True)
class DeviceTreeItem:
    name: str
    kind: str
    state: str = "info"
    detail: str = ""
    children: list["DeviceTreeItem"] = field(default_factory=list)


@dataclass(frozen=True)
class DeviceTreeSnapshot:
    roots: list[DeviceTreeItem] = field(default_factory=list)


def build_device_tree_snapshot(
    *,
    lcd_devices: list[DisplayDevice],
    telemetry: SystemTelemetry | None,
    fan_status: str,
    lighting_status: str,
    lianli_status: str,
    permission_helper_status: PermissionHelperStatus | None = None,
) -> DeviceTreeSnapshot:
    roots = [
        _lcd_root(lcd_devices),
        _telemetry_root(telemetry),
        _subsystem_root("普通风扇", "fan", fan_status),
        _subsystem_root("灯效", "lighting", lighting_status),
        _subsystem_root("联力无线", "lianli", lianli_status),
    ]
    if permission_helper_status is not None:
        roots.append(_permission_helper_root(permission_helper_status))
    return DeviceTreeSnapshot(roots=roots)


def render_device_tree_report(snapshot: DeviceTreeSnapshot | None) -> str:
    lines = ["设备树"]
    if snapshot is None or not snapshot.roots:
        lines.append("--  无")
        return "\n".join(lines)
    for item in snapshot.roots:
        _append_tree_item(lines, item, depth=0)
    return "\n".join(lines)


def summarize_device_tree(snapshot: DeviceTreeSnapshot | None) -> str:
    if snapshot is None:
        return "设备树未生成"
    ok_count = sum(1 for item in _walk_items(snapshot.roots) if item.state == "ok")
    warn_count = sum(1 for item in _walk_items(snapshot.roots) if item.state == "warn")
    return f"{ok_count} 正常 / {warn_count} 需处理"


def _lcd_root(devices: list[DisplayDevice]) -> DeviceTreeItem:
    children = [
        DeviceTreeItem(
            name=device.display_name,
            kind="lcd",
            state="ok" if device.connection.writable else "warn",
            detail=f"{device.width}x{device.height} / {'可写' if device.connection.writable else '只读'}",
        )
        for device in devices
    ]
    return DeviceTreeItem(
        name="LCD 屏幕",
        kind="lcd-group",
        state="ok" if children else "warn",
        detail=f"{len(children)} 个设备" if children else "未发现设备",
        children=children,
    )


def _telemetry_root(telemetry: SystemTelemetry | None) -> DeviceTreeItem:
    if telemetry is None:
        return DeviceTreeItem("硬件监控", "telemetry", "info", "等待采集")
    children = [
        DeviceTreeItem("CPU", "cpu", "ok" if telemetry.cpu.available else "warn", telemetry.cpu.error or "可读"),
        DeviceTreeItem("GPU", "gpu", "ok" if telemetry.gpu.available else "warn", telemetry.gpu.error or telemetry.gpu.name),
    ]
    children.extend(
        DeviceTreeItem(
            name=fan.name,
            kind="fan-sensor",
            state="ok" if fan.available and fan.rpm is not None else "warn",
            detail=_fan_detail(fan.rpm, fan.percent, fan.error),
        )
        for fan in telemetry.fans
    )
    return DeviceTreeItem("硬件监控", "telemetry", "ok", f"{len(children)} 项", children)


def _subsystem_root(name: str, kind: str, status: str) -> DeviceTreeItem:
    return DeviceTreeItem(name, kind, _state_from_text(status), status or "未加载")


def _permission_helper_root(status: PermissionHelperStatus) -> DeviceTreeItem:
    operations = ", ".join(status.supported_operations) if status.supported_operations else "none"
    return DeviceTreeItem(
        "权限 Helper",
        "permission-helper",
        "ok" if status.available else "info",
        f"{status.backend}: {status.detail}; operations: {operations}",
    )


def _fan_detail(rpm: int | None, percent: float | None, error: str) -> str:
    parts: list[str] = []
    if rpm is not None:
        parts.append(f"{rpm} RPM")
    if percent is not None:
        parts.append(f"{percent:.0f}%")
    if error:
        parts.append(error)
    return " / ".join(parts) if parts else "--"


def _state_from_text(text: str) -> str:
    if any(marker in text for marker in ("失败", "错误", "不可用", "未发现", "未连接")):
        return "warn"
    if any(marker in text for marker in ("可控", "已连接", "成功", "接收器", "可写")):
        return "ok"
    return "info"


def _append_tree_item(lines: list[str], item: DeviceTreeItem, *, depth: int) -> None:
    indent = "  " * depth
    detail = f": {item.detail}" if item.detail else ""
    lines.append(f"{indent}{_state_marker(item.state)}  {item.name} [{item.kind}]{detail}")
    for child in item.children:
        _append_tree_item(lines, child, depth=depth + 1)


def _state_marker(state: str) -> str:
    if state == "ok":
        return "OK"
    if state == "warn":
        return "WARN"
    if state == "error":
        return "ERR"
    return "INFO"


def _walk_items(items: list[DeviceTreeItem]):
    for item in items:
        yield item
        yield from _walk_items(item.children)
