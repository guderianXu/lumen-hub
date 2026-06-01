# Host Fan Curve Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a LIAN LI-style draggable temperature-to-PWM curve to the ordinary fan page and use it to control writable motherboard/system fan channels.

**Architecture:** Add focused pure sanitize/interpolation helpers in `fan_curve_model.py` and a reusable `FanCurveEditor` widget in `fan_curve.py`. Store ordinary fan curve settings in `GuiSettings.host_fan`, wire the ordinary fan page to periodically rescan CPU temperature, interpolate the target PWM percent, and write it through the existing Linux/Windows PWM write paths.

**Tech Stack:** Python, PySide6 widgets/painting, existing `hwmon` PWM sysfs writes, existing Windows LibreHardwareMonitor write bridge, pytest.

---

### Task 1: Shared Curve Widget And Math

**Files:**
- Create: `usb9_lcd/gui/fan_curve_model.py`
- Create: `usb9_lcd/gui/fan_curve.py`
- Test: `tests/test_fan_host.py`

- [x] **Step 1: Add failing tests for curve sanitizing and interpolation**

```python
def test_fan_curve_helpers_sanitize_and_interpolate():
    from usb9_lcd.gui.fan_curve import interpolate_fan_curve_percent, sanitize_fan_curve_points

    points = sanitize_fan_curve_points([[80, 110], [20, -5], [50, 40]])

    assert points == [[20, 0], [50, 40], [80, 100]]
    assert interpolate_fan_curve_percent(points, 10) == 0
    assert interpolate_fan_curve_percent(points, 35) == 20
    assert interpolate_fan_curve_percent(points, 90) == 100
```

- [x] **Step 2: Run the test and verify it fails before implementation**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_fan_host.py::test_fan_curve_helpers_sanitize_and_interpolate -q`
Expected: FAIL because `usb9_lcd.gui.fan_curve` does not exist.

- [x] **Step 3: Implement `fan_curve_model.py` and `fan_curve.py`**

```python
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

DEFAULT_FAN_CURVE_POINTS = [[30, 25], [50, 40], [70, 70], [85, 100]]


def sanitize_fan_curve_points(points: object, default: list[list[int]] | None = None) -> list[list[int]]:
    fallback = default or DEFAULT_FAN_CURVE_POINTS
    parsed: list[list[int]] = []
    if isinstance(points, list):
        for item in points[:12]:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                parsed.append([max(0, min(100, int(item[0]))), max(0, min(100, int(item[1])))])
    while len(parsed) < 2:
        parsed.append(list(fallback[min(len(parsed), len(fallback) - 1)]))
    return sorted(parsed, key=lambda item: item[0])


def interpolate_fan_curve_percent(points: object, temperature_c: float | int) -> int:
    curve = sanitize_fan_curve_points(points)
    temp = float(temperature_c)
    if temp <= curve[0][0]:
        return curve[0][1]
    for left, right in zip(curve, curve[1:], strict=False):
        if temp <= right[0]:
            span = max(1, right[0] - left[0])
            ratio = (temp - left[0]) / span
            return round(left[1] + (right[1] - left[1]) * ratio)
    return curve[-1][1]
```

- [x] **Step 4: Run the focused test**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_fan_host.py::test_fan_curve_helpers_sanitize_and_interpolate -q`
Expected: PASS.

### Task 2: Persist Ordinary Fan Curve Settings

**Files:**
- Modify: `usb9_lcd/gui/settings.py`
- Test: `tests/test_fan_host.py`

- [x] **Step 1: Add tests for loading `host_fan` defaults and clamping saved points**

```python
def test_host_fan_settings_load_defaults_and_curve_points(tmp_path):
    import json
    from usb9_lcd.gui.settings import load_settings

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"host_fan": {"curve_enabled": True, "curve_interval_seconds": 99, "curve_points": [[90, 120], [20, -1]]}}), encoding="utf-8")

    settings = load_settings(path)

    assert settings.host_fan.curve_enabled is True
    assert settings.host_fan.curve_interval_seconds == 60
    assert settings.host_fan.curve_points == [[20, 0], [90, 100]]
```

- [x] **Step 2: Add `HostFanUiSettings` and parser**

Implement `HostFanUiSettings(curve_enabled=False, curve_interval_seconds=3, curve_points=[[30,25],[50,40],[70,70],[85,100]])`, add `host_fan` to `GuiSettings`, parse it in `load_settings`, and clamp interval to 1..60 seconds and points to 0..100.

- [x] **Step 3: Run settings test**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_fan_host.py::test_host_fan_settings_load_defaults_and_curve_points -q`
Expected: PASS.

### Task 3: Wire Curve UI And Writes In Ordinary Fan Page

**Files:**
- Modify: `usb9_lcd/gui/fan_host.py`
- Modify: `usb9_lcd/gui/main_window.py`
- Test: `tests/test_fan_host.py`

- [x] **Step 1: Add tests for curve write behavior**

```python
def test_fan_host_applies_curve_pwm_from_cpu_temperature(tmp_path):
    from PySide6.QtWidgets import QApplication
    from usb9_lcd.gui.fan_host import FanControlHostPage, GenericFanChannel, GenericFanSnapshot
    from usb9_lcd.gui.settings import GuiSettings

    pwm_path = tmp_path / "pwm1"
    pwm_enable_path = tmp_path / "pwm1_enable"
    pwm_path.write_text("0\n", encoding="utf-8")
    pwm_enable_path.write_text("2\n", encoding="utf-8")
    settings = GuiSettings()
    settings.host_fan.curve_points = [[40, 20], [80, 100]]
    snapshot = GenericFanSnapshot(
        platform_name="Linux",
        telemetry=_telemetry(),
        channels=[GenericFanChannel(name="CPU Fan", rpm=900, pwm_path=pwm_path, pwm_enable_path=pwm_enable_path, control_available=True)],
        control_available=True,
        control_reason="1 writable PWM channel(s) detected",
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_load=False, settings=settings, settings_saver=lambda _settings: None, snapshot_collector=lambda: snapshot)
    page._snapshot = snapshot
    page._apply_curve_to_snapshot(snapshot, source="test")

    assert pwm_enable_path.read_text(encoding="utf-8") == "1\n"
    assert pwm_path.read_text(encoding="utf-8") == "112\n"
```

- [x] **Step 2: Implement curve card and apply logic**

Add `FanCurveEditor`, interval spin box, enable checkbox, and immediate apply button. Implement `_write_pwm_percent_to_channels`, `_apply_curve_to_snapshot`, `_curve_enabled_changed`, `_curve_tick`, and `_sync_curve_timer` so enabled curve periodically rescans and writes interpolated PWM.

- [x] **Step 3: Pass shared settings from `MainWindow`**

Construct `FanControlHostPage(..., settings=self.settings)` so curve edits persist through the existing GUI settings object.

- [x] **Step 4: Run focused fan tests**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_fan_host.py -q`
Expected: PASS.

### Task 4: Validate And Commit

**Files:**
- Modify: `README.md`
- Commit only files touched by this feature.

- [x] **Step 1: Update README fan section**

Document that ordinary fan control now supports manual PWM and CPU-temperature curve control.

- [x] **Step 2: Run verification**

Run:
```bash
python -m py_compile usb9_lcd/gui/fan_curve.py usb9_lcd/gui/fan_host.py usb9_lcd/gui/main_window.py usb9_lcd/gui/settings.py
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_fan_host.py -q
```
Expected: compile succeeds and tests pass.

- [x] **Step 3: Commit and push**

```bash
git add README.md usb9_lcd/gui/fan_curve.py usb9_lcd/gui/fan_host.py usb9_lcd/gui/main_window.py usb9_lcd/gui/settings.py tests/test_fan_host.py docs/superpowers/plans/2026-06-01-host-fan-curve-control.md
git commit -m "Add ordinary fan curve control"
git push origin main
```

### Task 5: Ordinary Fan Curve Presets

**Files:**
- Modify: `usb9_lcd/gui/fan_curve_model.py`
- Modify: `usb9_lcd/gui/fan_host.py`
- Modify: `usb9_lcd/gui/settings.py`
- Test: `tests/test_fan_host.py`

- [x] **Step 1: Add preset curve definitions**

Added `quiet`, `normal`, `high`, and `full` presets in `fan_curve_model.py`, plus helpers to normalize preset ids and return preset points.

- [x] **Step 2: Persist selected preset**

Added `HostFanUiSettings.curve_preset`, parsed it through settings loading, and kept `custom` for hand-edited curves.

- [x] **Step 3: Add preset selector to ordinary fan page**

Added a preset combo box with 安静/标准/高速/全速/自定义. Selecting a preset replaces curve points; dragging or adding points switches the preset to 自定义.

- [x] **Step 4: Cover preset behavior in tests**

Added tests for preset points, settings parsing, preset selection, and custom-state transition.
