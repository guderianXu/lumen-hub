from __future__ import annotations

from dataclasses import dataclass, field

from usb9_lcd.gui.device_inventory import DeviceTreeSnapshot, render_device_tree_report


@dataclass(frozen=True)
class StatusItem:
    label: str
    state: str
    detail: str = ""


@dataclass(frozen=True)
class SystemStatusSnapshot:
    components: list[StatusItem] = field(default_factory=list)
    permissions: list[StatusItem] = field(default_factory=list)
    device_tree: DeviceTreeSnapshot | None = None
    recent_events: list[str] = field(default_factory=list)


def render_system_status_report(snapshot: SystemStatusSnapshot) -> str:
    lines = ["系统状态"]
    _append_section(lines, "组件", snapshot.components)
    _append_section(lines, "权限", snapshot.permissions)
    lines.extend(["", render_device_tree_report(snapshot.device_tree)])
    lines.extend(["", "最近事件"])
    lines.extend(snapshot.recent_events or ["--  无"])
    return "\n".join(lines)


def summarize_permission_status(snapshot: SystemStatusSnapshot) -> str:
    if not snapshot.permissions:
        return "权限未检查"
    blocking = [item for item in snapshot.permissions if _normalized_state(item.state) in {"warn", "error"}]
    ok_count = sum(1 for item in snapshot.permissions if _normalized_state(item.state) == "ok")
    if not blocking:
        return f"{len(snapshot.permissions)} 项正常"
    first = blocking[0]
    return f"{ok_count} 项正常 / {len(blocking)} 项需处理\n{first.label}: {first.detail}"


def _append_section(lines: list[str], title: str, items: list[StatusItem]) -> None:
    lines.extend(["", title])
    if not items:
        lines.append("--  无")
        return
    for item in items:
        detail = f": {item.detail}" if item.detail else ""
        lines.append(f"{_state_marker(item.state)}  {item.label}{detail}")


def _state_marker(state: str) -> str:
    normalized = _normalized_state(state)
    if normalized == "ok":
        return "OK"
    if normalized == "error":
        return "ERR"
    if normalized == "warn":
        return "WARN"
    return "INFO"


def _normalized_state(state: str) -> str:
    normalized = state.strip().lower()
    if normalized in {"ok", "warn", "error", "info"}:
        return normalized
    return "info"
