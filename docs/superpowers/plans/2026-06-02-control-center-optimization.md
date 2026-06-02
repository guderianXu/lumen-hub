# Control Center Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement requested optimizations 1, 2, 3, 4, 7, 9, 13, 14, 16, 17, and 19 for lumen-hub without regressing existing fan, lighting, or monitoring behavior.

**Architecture:** Build a small diagnostics/status layer first, then have the GUI consume that layer from the home page, diagnostics dialog, fan page, and lighting page. Keep hardware-specific logic in focused Linux/Windows/lighting modules and keep GUI pages responsible only for presentation, user actions, and status feedback.

**Tech Stack:** Python 3, PySide6, pytest, Linux hwmon/powercap/hidraw, Windows LibreHardwareMonitor/OpenHardwareMonitor, OpenRGB SDK wrapper.

## Implementation Status

Completed on 2026-06-02:

- [x] `a5823e5 Add unified dashboard diagnostics foundation`: added `usb9_lcd/gui/system_status.py`, unified permission/component/recent-event reporting, homepage permission card, diagnostics-window system status, and recent log tail access.
- [x] `2dbfdd2 Add lighting target partition preview`: added OpenRGB target partition summaries and an apply-preview panel showing scope, target count, effect, color, brightness, speed, LED count, and frame behavior.
- [x] `8f9ec16 Improve Windows diagnostics for fan backends`: added Windows diagnostics for administrator status, PowerShell availability, `LibreHardwareMonitorLib.dll`, and ordinary fan-control backend availability.
- [x] `e8b76ef Show configuration save feedback`: added visible save feedback in the main settings/monitor flows and lighting-page save actions.

Known follow-up areas:

- [ ] Full `tests/test_gui_import.py` still has unrelated pre-existing LIAN LI/fan async failures in this working tree; this plan's touched tests pass.
- [ ] Fan and LIAN LI pages already have several page-local saved/status messages, but they can be standardized further later if a dedicated UI-wide toast/status system is introduced.
- [ ] Deeper Linux/Windows backend split can continue after the current GUI foundation stabilizes.

---

## File Structure

- Create: `usb9_lcd/gui/system_status.py` for unified permission/status/log report models.
- Modify: `usb9_lcd/gui/home.py` to show a real dashboard, permission summary, component state summary, and recent log rows.
- Modify: `usb9_lcd/gui/platform_diagnostics.py` to render the unified diagnostic report and include Windows/OpenRGB/permission state.
- Modify: `usb9_lcd/gui/main_window.py` to refresh the status model, expose diagnostics/log actions, and record operation feedback.
- Modify: `usb9_lcd/gui/debug.py` to expose recent log lines for GUI display.
- Modify: `usb9_lcd/gui/lighting_page.py` to improve lighting target grouping, preview/test surfaces, explicit apply feedback, and saved-setting feedback.
- Modify: `usb9_lcd/lighting/engine.py`, `usb9_lcd/lighting/effects.py`, `usb9_lcd/lighting/software_effects.py` only when a lighting preview/effect needs backend-independent data.
- Modify: `usb9_lcd/platforms/linux.py` and `usb9_lcd/platforms/windows.py` only for platform-specific diagnostics, not for GUI formatting.
- Test: `tests/test_gui_import.py`, `tests/test_monitoring.py`, and new focused tests when a small module is introduced.

---

### Task 1: Unified Status And Permission Model

**Covers:** 1 权限中心, 2 设备状态总览, 3 错误诊断面板, 13 后端分层 foundation, 14 Windows 诊断 foundation, 16 首页仪表盘 foundation.

**Files:**
- Create: `usb9_lcd/gui/system_status.py`
- Modify: `usb9_lcd/gui/platform_diagnostics.py`
- Test: `tests/test_gui_import.py`

- [ ] **Step 1: Write status model tests**

```python
def test_system_status_report_includes_permission_and_component_state(monkeypatch):
    from usb9_lcd.gui.system_status import SystemStatusSnapshot, StatusItem, render_system_status_report
    snapshot = SystemStatusSnapshot(
        components=[StatusItem("CPU", "ok", "温度/负载可读"), StatusItem("OpenRGB", "warn", "未连接")],
        permissions=[StatusItem("CPU 功耗", "warn", "/sys/class/powercap/.../energy_uj 需授权")],
        recent_events=["CPU 功耗权限已授权"],
    )
    report = render_system_status_report(snapshot)
    assert "CPU" in report
    assert "CPU 功耗" in report
    assert "CPU 功耗权限已授权" in report
```

- [ ] **Step 2: Run failing test**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_import.py::test_system_status_report_includes_permission_and_component_state -q`
Expected: FAIL because `usb9_lcd.gui.system_status` does not exist yet.

- [ ] **Step 3: Implement model**

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class StatusItem:
    label: str
    state: str
    detail: str = ""

@dataclass(frozen=True)
class SystemStatusSnapshot:
    components: list[StatusItem] = field(default_factory=list)
    permissions: list[StatusItem] = field(default_factory=list)
    recent_events: list[str] = field(default_factory=list)

def render_system_status_report(snapshot: SystemStatusSnapshot) -> str:
    lines = ["系统状态", "", "组件"]
    lines.extend(f"{item.state.upper()}  {item.label}: {item.detail}" for item in snapshot.components)
    lines.extend(["", "权限"])
    lines.extend(f"{item.state.upper()}  {item.label}: {item.detail}" for item in snapshot.permissions)
    lines.extend(["", "最近事件"])
    lines.extend(snapshot.recent_events or ["无"])
    return "\n".join(lines)
```

- [ ] **Step 4: Integrate diagnostics dialog**

Use `render_system_status_report()` inside `PlatformDiagnosticsDialog` after the existing platform report so the diagnostics window includes permissions, component states, and recent events.

- [ ] **Step 5: Run tests**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_import.py::test_system_status_report_includes_permission_and_component_state tests/test_gui_import.py::test_main_window_opens_platform_diagnostics_window -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add usb9_lcd/gui/system_status.py usb9_lcd/gui/platform_diagnostics.py tests/test_gui_import.py
git commit -m "Add unified system status report"
```

---

### Task 2: Home Dashboard And Visual Logs

**Covers:** 2 设备状态总览, 4 日志系统可视化, 16 首页仪表盘, 17 操作反馈更明确.

**Files:**
- Modify: `usb9_lcd/gui/home.py`
- Modify: `usb9_lcd/gui/main_window.py`
- Modify: `usb9_lcd/gui/debug.py`
- Test: `tests/test_gui_import.py`

- [ ] **Step 1: Add tests for dashboard fields**

```python
def test_home_page_shows_permission_status_and_operation_feedback():
    from PySide6.QtWidgets import QApplication
    from usb9_lcd.gui.home import ControlCenterPage
    app = QApplication.instance() or QApplication([])
    page = ControlCenterPage(lambda _page: None, lambda: None, lambda: None, lambda: None, lambda: None)
    page.update_permission_status("CPU 功耗: 已授权\nPWM: 可写")
    page.add_event("OpenRGB 应用成功：3 个目标")
    assert "CPU 功耗" in page.permission_value.text()
    assert page.event_labels[0].text() == "OpenRGB 应用成功：3 个目标"
    page.close()
```

- [ ] **Step 2: Implement permission/log widgets**

Add `self.permission_value`, `update_permission_status()`, and increase event rows from 4 to 6. Keep all labels word-wrapped.

- [ ] **Step 3: Wire main window feedback**

Call `home_page.update_permission_status()` after CPU power permission checks, fan permission checks, and diagnostics refresh. Use `home_page.add_event()` for every explicit operation result already displayed in `statusBar()`.

- [ ] **Step 4: Run tests**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_import.py::test_home_page_shows_permission_status_and_operation_feedback -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add usb9_lcd/gui/home.py usb9_lcd/gui/main_window.py usb9_lcd/gui/debug.py tests/test_gui_import.py
git commit -m "Improve home dashboard status feedback"
```

---

### Task 3: Lighting Target Partition And Preview Foundation

**Covers:** 7 灯效预览器, 9 设备分区控制, 13 分层 foundation, 17 操作反馈.

**Files:**
- Modify: `usb9_lcd/gui/lighting_page.py`
- Modify: `usb9_lcd/lighting/engine.py`
- Test: `tests/test_gui_import.py`

- [ ] **Step 1: Add tests for grouped target summary**

```python
def test_lighting_page_reports_target_partitions_after_connect():
    from PySide6.QtWidgets import QApplication
    from usb9_lcd.gui.lighting_page import LightingPage
    from usb9_lcd.gui.settings import GuiSettings
    page = LightingPage(controller=MultiDeviceLightingController(), settings=GuiSettings())
    page.connect_openrgb()
    assert _process_events_until(QApplication.instance(), lambda: "分区" in page.openrgb_modes_text.toPlainText())
```

- [ ] **Step 2: Add target partition rendering**

Append whole-device and zone target counts to the target panel text. Prefer zone targets for ARGB effects but retain whole-device apply-all paths.

- [ ] **Step 3: Add preview summary**

Render a text/mini-preview summary before applying: target count, effect, color, brightness, and estimated frame behavior.

- [ ] **Step 4: Run tests**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_import.py::test_lighting_page_reports_target_partitions_after_connect -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add usb9_lcd/gui/lighting_page.py usb9_lcd/lighting/engine.py tests/test_gui_import.py
git commit -m "Add lighting target partition preview"
```

---

### Task 4: Windows Permission And Driver Diagnostics

**Covers:** 14 Windows 权限和驱动诊断, 3 诊断面板, 13 platform split.

**Files:**
- Modify: `usb9_lcd/monitoring/windows.py`
- Modify: `usb9_lcd/platforms/windows.py`
- Modify: `usb9_lcd/gui/platform_diagnostics.py`
- Test: `tests/test_monitoring.py`, `tests/test_gui_import.py`

- [ ] **Step 1: Add diagnostic data tests**

```python
def test_windows_diagnostics_mentions_admin_and_lhm(monkeypatch):
    import usb9_lcd.platforms.windows as win_platform
    adapter = win_platform.WindowsPlatformAdapter()
    items = adapter.diagnostic_items(openrgb_path="OpenRGB.exe", openrgb_host="127.0.0.1", openrgb_port=6742)
    assert any("Windows" in item.label or "LibreHardwareMonitor" in item.label for item in items)
```

- [ ] **Step 2: Implement Windows-specific items**

Add items for administrator status, LibreHardwareMonitorLib.dll discovery, OpenRGB executable, and fan control backend availability.

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_gui_import.py::test_windows_diagnostics_mentions_admin_and_lhm -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add usb9_lcd/platforms/windows.py usb9_lcd/monitoring/windows.py usb9_lcd/gui/platform_diagnostics.py tests/test_gui_import.py
git commit -m "Add Windows hardware diagnostics"
```

---

### Task 5: Save Feedback Standardization

**Covers:** 19 配置实时保存提示, 17 操作反馈.

**Files:**
- Modify: `usb9_lcd/gui/lighting_page.py`
- Modify: `usb9_lcd/gui/fan_host.py`
- Modify: `usb9_lcd/gui/lianli_wireless_page.py`
- Modify: `usb9_lcd/gui/main_window.py`
- Test: `tests/test_gui_import.py`, `tests/test_fan_host.py`

- [ ] **Step 1: Add tests for saved messages**

```python
def test_lighting_profile_save_reports_saved_status(monkeypatch):
    page.save_current_target_profile()
    assert "已保存" in page.status_label.text()
```

- [ ] **Step 2: Add consistent save feedback helper**

Use one helper message format: `设置已保存：<area>` and add it to both page status labels and home recent events.

- [ ] **Step 3: Run tests**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_fan_host.py tests/test_gui_import.py -q`
Expected: PASS for touched tests.

- [ ] **Step 4: Commit**

```bash
git add usb9_lcd/gui/lighting_page.py usb9_lcd/gui/fan_host.py usb9_lcd/gui/lianli_wireless_page.py usb9_lcd/gui/main_window.py tests/test_gui_import.py tests/test_fan_host.py
git commit -m "Standardize saved setting feedback"
```

---

## Completion Audit Checklist

- [x] Item 1 is complete when GUI has a unified permission summary and can trigger/record relevant permission grants.
- [x] Item 2 is complete when homepage shows component statuses for LCD, CPU, GPU, fan, OpenRGB lighting, LIAN LI wireless, and permissions.
- [x] Item 3 is complete when diagnostics report combines platform, permissions, Windows/OpenRGB, and recent events.
- [x] Item 4 is complete when GUI exposes recent log/operation events beyond transient statusBar text.
- [x] Item 7 is complete when lighting page shows a preview/summary before applying effects.
- [x] Item 9 is complete when OpenRGB target partitions/zones are visible and selectable/apply-able.
- [x] Item 13 is complete when new logic is factored into platform/lighting/status modules rather than only page methods.
- [x] Item 14 is complete when Windows diagnostics report admin state and LibreHardwareMonitor/OpenHardwareMonitor availability.
- [x] Item 16 is complete when homepage is an actionable dashboard, not only raw labels.
- [x] Item 17 is complete when key operations report target counts, success counts, and errors in GUI and recent events.
- [x] Item 19 is complete when setting changes show explicit saved feedback.
