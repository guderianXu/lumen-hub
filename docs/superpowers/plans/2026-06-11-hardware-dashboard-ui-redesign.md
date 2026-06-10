# Hardware Dashboard UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the PySide GUI into a more elegant advanced hardware dashboard without changing hardware control behavior.

**Architecture:** Keep the existing PySide widget hierarchy and improve presentation through shared QSS selectors plus a focused home-page layout refactor. Behavior remains in the existing pages; this plan only adds visual hooks, grouped layout containers, and screenshot verification tooling.

**Tech Stack:** Python 3, PySide6 widgets/QSS, pytest with `QT_QPA_PLATFORM=offscreen`, existing GUI test helpers.

---

## File Structure

- Modify `usb9_lcd/gui/theme.py`: owns the global QSS visual language. Add hardware-dashboard palette, shell/card/button/form/tab/scrollbar selectors, and dedicated home-page selectors.
- Modify `usb9_lcd/gui/home.py`: owns `ControlCenterPage`. Replace equal-weight status layout with hero/status band, metric grid, grouped command dock, and timeline event panel.
- Modify `usb9_lcd/gui/main_window.py`: owns application shell. Add or refine object names for top bar, content shell, device/status chips, and platform diagnostics button.
- Create `tests/test_hardware_dashboard_theme.py`: protects QSS selectors and palette choices without launching hardware operations.
- Modify `tests/test_gui_import.py`: add GUI structure tests for dashboard sections and shell hooks.
- Create `tools/capture_gui_screenshots.py`: deterministic offscreen screenshot helper for home, screen, fan, lighting, and LIAN LI pages.

---

### Task 1: Protect The Hardware Dashboard Theme Selectors

**Files:**
- Create: `tests/test_hardware_dashboard_theme.py`
- Modify: `usb9_lcd/gui/theme.py`

- [ ] **Step 1: Write the failing theme-selector test**

Create `tests/test_hardware_dashboard_theme.py` with this content:

```python
from __future__ import annotations

from usb9_lcd.gui.theme import gui_stylesheet


def test_hardware_dashboard_theme_exposes_core_selectors():
    qss = gui_stylesheet()

    required_selectors = (
        "QFrame#AppShell",
        "QFrame#TopBar",
        "QFrame#MainContentShell",
        "QListWidget#SideNav",
        "QFrame#MetricCard",
        "QFrame#HomeHeroPanel",
        "QFrame#HomeStatusCard",
        "QFrame#HomeCommandDock",
        "QFrame#HomeTimelinePanel",
        "QPushButton#PrimaryButton",
        "QPushButton#DangerButton",
        "QPushButton#SecondaryButton",
        "QLabel#StatusPill",
    )
    missing = [selector for selector in required_selectors if selector not in qss]
    assert missing == []


def test_hardware_dashboard_theme_uses_approved_palette_not_generic_purple():
    qss = gui_stylesheet().lower()

    assert "#6ee7d8" in qss or "#67d8c5" in qss
    assert "#f0b35a" in qss or "#d99a3d" in qss
    assert "#d76f7a" in qss or "#b85c66" in qss
    assert "#7c3aed" not in qss
    assert "#a855f7" not in qss
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python -m pytest tests/test_hardware_dashboard_theme.py -q
```

Expected: FAIL because `HomeHeroPanel`, `HomeCommandDock`, `SecondaryButton`, and `StatusPill` selectors do not exist yet.

- [ ] **Step 3: Implement the first QSS layer**

Modify `usb9_lcd/gui/theme.py` so `gui_stylesheet()` returns a QSS string with these concrete design constants and selector groups:

```python
def gui_stylesheet() -> str:
    return """
    QMainWindow, QWidget {
        background: #0b0f12;
        color: #eef4f2;
        font-family: "Noto Sans CJK SC", "Microsoft YaHei", "Segoe UI";
        font-size: 13px;
    }
    QLabel { background: transparent; }
    QFrame#AppShell { background: #0b0f12; }
    QFrame#TopBar {
        background: #11181c;
        border-bottom: 1px solid #26343a;
    }
    QFrame#MainContentShell {
        background: #0d1215;
        border-left: 1px solid #1e2a30;
    }
    QListWidget#SideNav {
        background: #090d10;
        border: 0;
        border-right: 1px solid #1f2a2f;
        border-radius: 0;
        padding: 14px 10px;
    }
    QListWidget#SideNav::item {
        min-height: 34px;
        padding: 10px 12px;
        border-radius: 10px;
        color: #93a1a7;
        margin: 4px 0;
        font-weight: 700;
    }
    QListWidget#SideNav::item:hover {
        background: #142026;
        color: #eaf2ef;
    }
    QListWidget#SideNav::item:selected {
        background: #17382f;
        color: #f4fffb;
        border-left: 3px solid #6ee7d8;
    }
    QLabel#AppTitle { font-size: 18px; font-weight: 900; color: #f5fbf8; }
    QLabel#PageTitle { font-size: 25px; font-weight: 900; color: #f5fbf8; }
    QLabel#PageSubtitle { color: #92a2aa; font-size: 13px; }
    QLabel#SectionLabel { color: #dce7e3; font-weight: 900; }
    QLabel#FieldHint { color: #8c9ba2; line-height: 1.35; }
    QLabel#StatusPill, QLabel#DeviceBadge {
        background: #102d28;
        border: 1px solid #2f8a78;
        border-radius: 10px;
        color: #d8fff6;
        padding: 7px 12px;
        font-weight: 800;
    }
    QFrame#MetricCard,
    QFrame#HomeStatusCard,
    QFrame#HomeCommandDock,
    QFrame#HomeTimelinePanel {
        background: #12191d;
        border: 1px solid #27343b;
        border-radius: 14px;
    }
    QFrame#MetricCard:hover,
    QFrame#HomeStatusCard:hover {
        border-color: #3c555b;
        background: #151f23;
    }
    QFrame#HomeHeroPanel {
        background: #10211f;
        border: 1px solid #2d6f65;
        border-radius: 18px;
    }
    QPushButton {
        background: #172126;
        border: 1px solid #32444c;
        border-radius: 10px;
        padding: 8px 13px;
        color: #edf5f2;
        font-weight: 700;
    }
    QPushButton:hover { background: #1d2b31; border-color: #46616b; }
    QPushButton:pressed { background: #25363d; border-color: #5f7d87; }
    QPushButton:disabled { background: #10171a; border-color: #202b30; color: #65747b; }
    QPushButton#PrimaryButton {
        background: #247766;
        border-color: #6ee7d8;
        color: #f7fffc;
        font-weight: 900;
    }
    QPushButton#SecondaryButton {
        background: #172126;
        border-color: #41545c;
        color: #cbd9d5;
    }
    QPushButton#DangerButton {
        background: #351f24;
        border-color: #8b4752;
        color: #ffd9dc;
        font-weight: 900;
    }
    QComboBox, QLineEdit, QTextEdit, QSpinBox {
        background: #10171a;
        border: 1px solid #2b3a40;
        border-radius: 10px;
        padding: 7px;
        color: #edf5f2;
        selection-background-color: #247766;
    }
    QComboBox:focus, QLineEdit:focus, QTextEdit:focus, QSpinBox:focus {
        border-color: #6ee7d8;
        background: #131d21;
    }
    QScrollBar:vertical { background: #0b0f12; width: 10px; margin: 0; }
    QScrollBar::handle:vertical { background: #31424a; border-radius: 5px; min-height: 36px; }
    QScrollBar::handle:vertical:hover { background: #4b626b; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    """
```

Keep existing specialized selectors for fan charts, lighting cards, color swatches, tabs, sliders, and preview widgets by merging them into this new palette instead of deleting behavior-specific styling.

- [ ] **Step 4: Run the selector test and py_compile**

Run:

```bash
python -m pytest tests/test_hardware_dashboard_theme.py -q
python -m py_compile usb9_lcd/gui/theme.py
```

Expected: both commands pass.

- [ ] **Step 5: Commit the theme test and first QSS layer**

```bash
git add tests/test_hardware_dashboard_theme.py usb9_lcd/gui/theme.py
git commit -m "Redesign hardware dashboard theme palette"
```

---

### Task 2: Add Main Shell Visual Hooks

**Files:**
- Modify: `tests/test_gui_import.py`
- Modify: `usb9_lcd/gui/main_window.py`

- [ ] **Step 1: Write the failing shell-hook test**

Append this test to `tests/test_gui_import.py` near the existing main-window construction tests:

```python
def test_main_window_uses_hardware_dashboard_shell_hooks():
    from PySide6.QtWidgets import QApplication, QFrame, QPushButton

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    assert window.findChild(QFrame, "AppShell") is not None
    assert window.findChild(QFrame, "TopBar") is not None
    assert window.findChild(QFrame, "MainContentShell") is not None
    assert window.navigation.objectName() == "SideNav"
    assert any(
        button.objectName() == "SecondaryButton" and button.text() == "平台诊断"
        for button in window.findChildren(QPushButton)
    )

    window.close()
    app.quit()
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_import.py::test_main_window_uses_hardware_dashboard_shell_hooks -q
```

Expected: FAIL because `MainContentShell` and `SecondaryButton` do not exist yet.

- [ ] **Step 3: Add the shell content frame and secondary button hook**

In `usb9_lcd/gui/main_window.py`, replace the direct content layout block with a named shell frame:

```python
        content_shell = QFrame()
        content_shell.setObjectName("MainContentShell")
        content = QHBoxLayout(content_shell)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        content.addWidget(self.navigation)
        content.addWidget(self.pages, 1)
        shell_layout.addWidget(content_shell, 1)
```

In `_top_bar()`, after creating `platform_diagnostics_button`, set:

```python
        platform_diagnostics_button.setObjectName("SecondaryButton")
```

- [ ] **Step 4: Run shell test and focused GUI construction tests**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_import.py::test_main_window_uses_hardware_dashboard_shell_hooks tests/test_gui_import.py::test_control_center_opens_lianli_wireless_page tests/test_gui_import.py::test_main_window_pages_are_scroll_wrapped -q
```

Expected: all pass.

- [ ] **Step 5: Commit shell hooks**

```bash
git add tests/test_gui_import.py usb9_lcd/gui/main_window.py
git commit -m "Add hardware dashboard shell hooks"
```

---

### Task 3: Redesign The Home Page Dashboard Structure

**Files:**
- Modify: `tests/test_gui_import.py`
- Modify: `usb9_lcd/gui/home.py`

- [ ] **Step 1: Write the failing home-dashboard structure test**

Append this test to `tests/test_gui_import.py` near other control-center tests:

```python
def test_control_center_exposes_hardware_dashboard_sections():
    from PySide6.QtWidgets import QApplication, QFrame, QPushButton

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    required_frames = (
        "HomeHeroPanel",
        "HomeMetricGrid",
        "HomeCommandDock",
        "HomeTimelinePanel",
    )
    missing = [name for name in required_frames if window.home_page.findChild(QFrame, name) is None]
    assert missing == []

    groups = {
        button.property("commandGroup")
        for button in window.home_page.findChildren(QPushButton)
        if button.property("commandGroup")
    }
    assert {"safety", "screen", "fan", "lighting", "lianli"}.issubset(groups)

    window.close()
    app.quit()
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_import.py::test_control_center_exposes_hardware_dashboard_sections -q
```

Expected: FAIL because the home page does not have these named frames or grouped command button properties.

- [ ] **Step 3: Refactor `ControlCenterPage.__init__` around dashboard sections**

In `usb9_lcd/gui/home.py`, keep the same constructor signature and existing update methods. Change layout construction to this shape:

```python
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(16)

        layout.addWidget(self._hero_panel(sleep_all_off))
        layout.addWidget(self._metric_grid())
        layout.addWidget(
            self._command_panel(
                navigate,
                upload_monitor,
                load_fan_control,
                connect_lighting,
                sleep_all_off,
            )
        )
        layout.addWidget(self._event_panel())
        layout.addStretch(1)
        self.add_event("控制中心已就绪")
```

Add `_hero_panel()`:

```python
    def _hero_panel(self, sleep_all_off: Callable[[], None]) -> QFrame:
        panel = QFrame()
        panel.setObjectName("HomeHeroPanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(10)

        title = QLabel("Lumen Hub 控制中心")
        title.setObjectName("PageTitle")
        subtitle = QLabel("屏幕、风扇、灯效与联力无线的实时硬件指挥台")
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        self.mode_value = QLabel("日常")
        self.mode_value.setObjectName("StatusPill")

        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box, 0, 0, 1, 3)
        layout.addWidget(self.mode_value, 0, 3)

        self.mode_group = QButtonGroup(self)
        modes = (
            ("日常", "屏幕监控、风扇自动、灯效默认", None),
            ("游戏", "性能优先，保留监控画面", None),
            ("静音", "低噪声策略，降低灯光亮度", None),
            ("睡眠", "黑屏并关闭所有灯光", sleep_all_off),
        )
        for index, (label, description, action) in enumerate(modes):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("SegmentButton")
            button.setToolTip(description)
            button.setProperty("modeAction", label)
            if index == 0:
                button.setChecked(True)
            button.clicked.connect(
                lambda _checked=False, name=label, callback=action: self._set_mode(name, callback)
            )
            self.mode_group.addButton(button, index)
            layout.addWidget(button, 1, index)
        return panel
```

Add `_metric_grid()`:

```python
    def _metric_grid(self) -> QFrame:
        wrapper = QFrame()
        wrapper.setObjectName("HomeMetricGrid")
        overview = QGridLayout(wrapper)
        overview.setContentsMargins(0, 0, 0, 0)
        overview.setSpacing(12)

        self.device_value = QLabel("未发现设备")
        self.cpu_value = QLabel("CPU --")
        self.gpu_value = QLabel("GPU --")
        self._fan_status_text = "未加载"
        self._fan_rpm_text = ""
        self.fan_value = QLabel(self._fan_status_text)
        self.lighting_value = QLabel("默认关闭")
        self.lianli_value = QLabel("未连接")
        self.permission_value = QLabel("权限未检查")
        self.device_tree_value = QLabel("设备树未生成")

        overview.addWidget(self._status_card("LCD", self.device_value, "screen"), 0, 0, 1, 2)
        overview.addWidget(self._status_card("CPU", self.cpu_value, "cpu"), 0, 2)
        overview.addWidget(self._status_card("GPU", self.gpu_value, "gpu"), 0, 3)
        overview.addWidget(self._status_card("风扇", self.fan_value, "fan"), 1, 0, 1, 2)
        overview.addWidget(self._status_card("灯效", self.lighting_value, "lighting"), 1, 2)
        overview.addWidget(self._status_card("联力无线", self.lianli_value, "lianli"), 1, 3)
        overview.addWidget(self._status_card("设备树", self.device_tree_value, "device-tree"), 2, 0, 1, 2)
        overview.addWidget(self._status_card("权限", self.permission_value, "permission"), 2, 2, 1, 2)
        return wrapper
```

Update `_status_card()` signature and object names:

```python
    def _status_card(self, title: str, value: QLabel, role: str = "") -> QFrame:
        card = QFrame()
        card.setObjectName("HomeStatusCard")
        if role:
            card.setProperty("statusRole", role)
        card.setMinimumHeight(108)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(15, 13, 15, 13)
        card_layout.setSpacing(7)
        title_label = QLabel(title)
        title_label.setObjectName("SectionLabel")
        value.setObjectName("HomeMetricValue")
        value.setWordWrap(True)
        card_layout.addWidget(title_label)
        card_layout.addWidget(value, 1)
        return card
```

- [ ] **Step 4: Group command buttons**

In `_command_panel()`, set the panel object name to `HomeCommandDock` and use action tuples that include a group:

```python
        actions = [
            ("safety", "睡眠全关", sleep_all_off, False),
            ("screen", "发送监控", upload_monitor, True),
            ("screen", "打开屏幕", lambda: navigate("screen"), True),
            ("screen", "素材库", lambda: navigate("assets"), False),
            ("fan", "打开风扇", lambda: navigate("fan"), True),
            ("fan", "扫描风扇", load_fan_control, False),
            ("lighting", "打开灯效", lambda: navigate("lighting"), True),
            ("lighting", "连接灯效", connect_lighting, False),
            ("lianli", "打开联力", lambda: navigate("lianli"), True),
            ("lianli", "读取联力状态", lambda: navigate("lianli"), False),
        ]
```

For each button:

```python
            button.setProperty("commandGroup", group)
            if label == "睡眠全关":
                button.setObjectName("DangerButton")
            elif primary:
                button.setObjectName("PrimaryButton")
            else:
                button.setObjectName("SecondaryButton")
```

- [ ] **Step 5: Style the event panel as timeline**

Set `_event_panel()` object name to `HomeTimelinePanel`. Keep `self.event_labels` and `add_event()` unchanged except for label object name:

```python
            label.setObjectName("TimelineItem")
```

- [ ] **Step 6: Run home tests**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_import.py::test_control_center_exposes_hardware_dashboard_sections tests/test_gui_import.py::test_control_center_opens_lianli_wireless_page tests/test_gui_import.py::test_main_window_refresh_telemetry_updates_dashboard_labels -q
```

Expected: all pass.

- [ ] **Step 7: Commit home dashboard structure**

```bash
git add tests/test_gui_import.py usb9_lcd/gui/home.py
git commit -m "Redesign control center dashboard layout"
```

---

### Task 4: Extend QSS For Home Dashboard And Existing Complex Pages

**Files:**
- Modify: `usb9_lcd/gui/theme.py`
- Test: `tests/test_hardware_dashboard_theme.py`, `tests/test_gui_import.py`

- [ ] **Step 1: Add QSS for the new home selectors**

In `usb9_lcd/gui/theme.py`, add concrete selector blocks for:

```css
QFrame#HomeHeroPanel { min-height: 132px; }
QFrame#HomeMetricGrid { background: transparent; border: 0; }
QFrame#HomeStatusCard[statusRole="cpu"] { border-color: #274f59; }
QFrame#HomeStatusCard[statusRole="gpu"] { border-color: #2b4d62; }
QFrame#HomeStatusCard[statusRole="fan"] { border-color: #2f5e55; }
QFrame#HomeStatusCard[statusRole="permission"] { border-color: #5f4930; }
QLabel#HomeMetricValue { font-size: 19px; font-weight: 900; color: #f5fbf8; }
QLabel#TimelineItem {
    color: #9fb0b7;
    padding: 6px 0 6px 12px;
    border-left: 2px solid #2d6f65;
}
```

- [ ] **Step 2: Harmonize existing complex page containers**

Update existing selector colors in `theme.py` so these object names use the same palette and radii as `MetricCard`:

```css
QFrame#FanDashboardPanel,
QFrame#FanChartPanel,
QFrame#FanStatusCard,
QFrame#FanRoleMetricCard,
QFrame#FanCurveEditorPanel,
QFrame#LightingTargetPanel,
QFrame#LightingPresetPanel,
QFrame#LightingActionPanel,
QFrame#LightingSyncPanel,
QFrame#LightingScenePanel,
QFrame#ScreenPreviewCard,
QLabel#AssetPreview,
QLabel#LcdPreview
```

Use `#12191d` or `#10171a` backgrounds, `#27343b` borders, and 12-14 px radii. Keep special functional selectors like `QWidget#FanCurveCanvas`, `QPushButton#ColorSwatch`, and slider groove/handle rules intact.

- [ ] **Step 3: Run targeted theme and GUI tests**

Run:

```bash
python -m pytest tests/test_hardware_dashboard_theme.py -q
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_import.py::test_control_center_exposes_hardware_dashboard_sections tests/test_gui_import.py::test_main_window_constructs_with_dark_dashboard_pages -q
```

Expected: all pass.

- [ ] **Step 4: Commit visual selector expansion**

```bash
git add usb9_lcd/gui/theme.py tests/test_hardware_dashboard_theme.py
git commit -m "Polish dashboard card and page styling"
```

---

### Task 5: Add Offscreen Screenshot Capture Tool

**Files:**
- Create: `tools/capture_gui_screenshots.py`
- Test: `tests/test_packaging_build.py` or `tests/test_gui_import.py`

- [ ] **Step 1: Write a lightweight tool existence/import test**

Add this test to `tests/test_gui_import.py`:

```python
def test_gui_screenshot_tool_imports_without_starting_qt():
    import importlib.util
    from pathlib import Path

    path = Path("tools/capture_gui_screenshots.py")
    spec = importlib.util.spec_from_file_location("capture_gui_screenshots", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.PAGE_ROWS == {
        "home": 0,
        "screen": 1,
        "fan": 2,
        "lighting": 3,
        "lianli": 5,
    }
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_import.py::test_gui_screenshot_tool_imports_without_starting_qt -q
```

Expected: FAIL because `tools/capture_gui_screenshots.py` does not exist.

- [ ] **Step 3: Create the screenshot tool**

Create `tools/capture_gui_screenshots.py` with:

```python
from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication

from usb9_lcd.drivers.base import Capability, DeviceConnection, DisplayDevice, PixelFormat, PreviewProfile, PreviewShape, PixelStyle
from usb9_lcd.gui.main_window import MainWindow
from usb9_lcd.monitoring.models import CpuTelemetry, FanTelemetry, GpuTelemetry, SystemTelemetry

PAGE_ROWS = {
    "home": 0,
    "screen": 1,
    "fan": 2,
    "lighting": 3,
    "lianli": 5,
}


class DemoDriver:
    driver_id = "demo.driver"
    display_name = "Demo LCD Driver"

    def __init__(self) -> None:
        self.device = DisplayDevice(
            connection=DeviceConnection(
                driver_id="asus.lc_iii",
                display_name="ASUS Test LCD",
                paths=(Path("/dev/hidraw-control"), Path("/dev/hidraw-data")),
                writable=True,
                readable=True,
                details="demo",
            ),
            width=480,
            height=480,
            pixel_format=PixelFormat.RGB565,
            preview=PreviewProfile(
                width=480,
                height=480,
                shape=PreviewShape.CIRCLE,
                pixel_style=PixelStyle.CONTINUOUS,
                orientation=180,
                label="LCD",
            ),
            capabilities=frozenset({Capability.STATIC_IMAGE, Capability.SENSOR_MONITOR}),
        )

    def discover(self) -> list[DisplayDevice]:
        return [self.device]

    def upload_static_frame(self, device: DisplayDevice, frame: bytes) -> None:
        return None


def demo_telemetry() -> SystemTelemetry:
    return SystemTelemetry(
        cpu=CpuTelemetry(package_temperature_c=54.0, utilization_percent=18.0, power_w=86.0, available=True),
        gpu=GpuTelemetry(name="NVIDIA RTX", temperature_c=61, utilization_percent=42, power_w=216.0, fan_speed_percent=48, available=True),
        fans=[
            FanTelemetry(name="CPU Fan", rpm=987, percent=82, available=True),
            FanTelemetry(name="GPU Fan", rpm=1530, percent=48, available=True),
        ],
        captured_at=datetime(2026, 6, 11, 12, 0, 0),
    )


def capture_pages(output_dir: Path, *, width: int = 1440, height: int = 920) -> list[Path]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(driver=DemoDriver(), telemetry_provider=demo_telemetry, auto_refresh=False)
    window.resize(width, height)
    window.refresh_devices()
    window.refresh_telemetry()
    window.show()
    app.processEvents()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, row in PAGE_ROWS.items():
        window.navigation.setCurrentRow(row)
        app.processEvents()
        path = output_dir / f"lumen-hub-{name}.png"
        window.grab().save(str(path))
        paths.append(path)
    window.close()
    app.processEvents()
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture Lumen Hub GUI screenshots with demo data.")
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/gui-screenshots"))
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=920)
    args = parser.parse_args(argv)
    for path in capture_pages(args.output_dir, width=args.width, height=args.height):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run import test**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_import.py::test_gui_screenshot_tool_imports_without_starting_qt -q
python -m py_compile tools/capture_gui_screenshots.py
```

Expected: both pass.

- [ ] **Step 5: Commit screenshot tool**

```bash
git add tools/capture_gui_screenshots.py tests/test_gui_import.py
git commit -m "Add GUI screenshot capture helper"
```

---

### Task 6: Visual Verification And Final Test Pass

**Files:**
- No production changes unless screenshots reveal clipping or broken layout.

- [ ] **Step 1: Capture 1440x920 screenshots**

Run:

```bash
QT_QPA_PLATFORM=offscreen python tools/capture_gui_screenshots.py --output-dir .cache/gui-screenshots/1440 --width 1440 --height 920
```

Expected: prints five PNG paths for home, screen, fan, lighting, and LIAN LI.

- [ ] **Step 2: Capture 1180x760 screenshots**

Run:

```bash
QT_QPA_PLATFORM=offscreen python tools/capture_gui_screenshots.py --output-dir .cache/gui-screenshots/1180 --width 1180 --height 760
```

Expected: prints five PNG paths and no exceptions.

- [ ] **Step 3: Inspect screenshots**

Open at least these screenshots:

```bash
.cache/gui-screenshots/1440/lumen-hub-home.png
.cache/gui-screenshots/1440/lumen-hub-fan.png
.cache/gui-screenshots/1440/lumen-hub-lianli.png
.cache/gui-screenshots/1180/lumen-hub-home.png
```

Acceptance checks:

- Home page has `HomeHeroPanel` visually prominent at top.
- Metric cards have stronger hierarchy than baseline flat gray cards.
- Command dock is grouped and action types are visually distinct.
- Fan and LIAN LI curve editors remain visible and readable.
- No obvious text clipping in the 1180x760 home screenshot.

- [ ] **Step 4: Run focused GUI tests**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_hardware_dashboard_theme.py tests/test_gui_import.py -q
```

Expected: all GUI and theme tests pass.

- [ ] **Step 5: Run full test suite**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

Expected: full suite passes with no failures.

- [ ] **Step 6: Commit any screenshot-driven polish fixes**

If screenshots required minor polish changes, commit them:

```bash
git add usb9_lcd/gui/theme.py usb9_lcd/gui/home.py usb9_lcd/gui/main_window.py tests/test_gui_import.py tests/test_hardware_dashboard_theme.py tools/capture_gui_screenshots.py
git commit -m "Polish hardware dashboard screenshots"
```

If no extra changes are needed, do not create an empty commit.

---

## Self-Review Notes

- Spec coverage: Tasks 1 and 4 cover the global QSS visual system; Task 2 covers the main shell; Task 3 covers the dashboard-first home page; Task 5 and Task 6 cover screenshot verification; all tasks preserve hardware behavior by limiting changes to object names, layouts, and style.
- Red-flag scan: This plan uses exact file paths, tests, commands, and expected outcomes without open-ended work items.
- Type consistency: New object names are consistently `MainContentShell`, `HomeHeroPanel`, `HomeMetricGrid`, `HomeCommandDock`, `HomeTimelinePanel`, `HomeStatusCard`, `SecondaryButton`, and `StatusPill` across tests, QSS, and planned implementation.
