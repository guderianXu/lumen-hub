# Lumen Hub Scene Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-phase global scene center that applies named whole-machine scenes across LCD, OpenRGB, LIAN LI wireless lighting, host fan curves, and LIAN LI fan curves.

**Architecture:** Add a pure scene model and planner outside Qt, then wire it into the existing GUI pages through a thin coordinator. Keep hardware writes behind existing page methods and preserve existing OpenRGB-only scene behavior for compatibility.

**Tech Stack:** Python 3.11, PySide6, dataclasses, existing `usb9_lcd.gui.settings`, existing `LightingPage`, `FanControlHostPage`, `LianLiWirelessPage`, pytest.

---

## File Structure

- Create `usb9_lcd/gui/scenes.py`: global scene dataclasses, built-in presets, payload parsing, and pure apply-plan construction.
- Modify `usb9_lcd/gui/settings.py`: add top-level global scene settings while preserving `lighting.scenes`.
- Modify `usb9_lcd/gui/home.py`: replace the local mode buttons with scene cards driven by global scene summaries.
- Modify `usb9_lcd/gui/main_window.py`: add scene coordinator methods that call existing page methods and update status/event text.
- Create `usb9_lcd/gui/scene_page.py`: compact editor/status page for built-in and saved scenes.
- Modify `usb9_lcd/gui/pages.py`: export the new scene page if tests import GUI pages through the aggregate module.
- Modify `tests/test_settings.py`: settings round-trip and migration tests.
- Create `tests/test_scene_center.py`: pure scene model/planner tests.
- Modify `tests/test_gui_import.py`: GUI smoke tests for home scene cards and scene apply delegation.

## Task 1: Global Scene Settings Model

**Files:**
- Modify: `usb9_lcd/gui/settings.py`
- Test: `tests/test_settings.py`

- [ ] **Step 1: Write failing settings tests**

Add these tests to `tests/test_settings.py`:

```python
def test_settings_round_trips_global_scenes_without_losing_openrgb_scenes(tmp_path):
    from usb9_lcd.gui.settings import GuiSettings, load_settings, save_settings

    path = tmp_path / "settings.json"
    settings = GuiSettings()
    settings.active_scene = "Gaming"
    settings.scenes["Gaming"] = {
        "name": "Gaming",
        "screen": {"mode": "monitor_profile", "profile": "Gaming"},
        "openrgb": {"mode": "scene", "scene_name": "ARGB"},
        "lianli_lighting": {"mode": "effect", "effect": "runway", "color": "#ff2d55"},
        "host_fan": {"mode": "preset", "preset": "normal"},
        "lianli_fan": {"mode": "preset", "preset": "normal"},
        "safety": {"dry_run": False, "allow_lianli_write": False},
    }
    settings.lighting.scenes["ARGB"] = {
        "targets": {"device:0": {"effect": "static", "color": "#ffffff"}}
    }

    save_settings(settings, path)
    loaded = load_settings(path)

    assert loaded.active_scene == "Gaming"
    assert loaded.scenes["Gaming"]["openrgb"]["scene_name"] == "ARGB"
    assert loaded.lighting.scenes["ARGB"]["targets"]["device:0"]["effect"] == "static"


def test_settings_defaults_global_scene_storage_to_empty():
    from usb9_lcd.gui.settings import GuiSettings

    settings = GuiSettings()

    assert settings.active_scene == ""
    assert settings.scenes == {}
```

- [ ] **Step 2: Run settings tests and verify failure**

Run:

```powershell
python -m pytest tests\test_settings.py::test_settings_round_trips_global_scenes_without_losing_openrgb_scenes tests\test_settings.py::test_settings_defaults_global_scene_storage_to_empty -q
```

Expected: failure because `GuiSettings` has no `active_scene` or `scenes`.

- [ ] **Step 3: Add top-level scene fields**

In `usb9_lcd/gui/settings.py`, change `GuiSettings` to include:

```python
@dataclass
class GuiSettings:
    config_version: int = CONFIG_VERSION
    lighting: LightingUiSettings = field(default_factory=LightingUiSettings)
    monitor: MonitorUiSettings = field(default_factory=MonitorUiSettings)
    host_fan: HostFanUiSettings = field(default_factory=HostFanUiSettings)
    openrgb: OpenRgbUiSettings = field(default_factory=OpenRgbUiSettings)
    lianli_wireless: LianLiWirelessUiSettings = field(default_factory=LianLiWirelessUiSettings)
    active_scene: str = ""
    scenes: dict[str, dict[str, Any]] = field(default_factory=dict)
    keepalive_enabled: bool = True
```

In `load_settings()`, pass the fields:

```python
    return GuiSettings(
        config_version=CONFIG_VERSION,
        lighting=_lighting_from_dict(payload.get("lighting")),
        monitor=_monitor_from_dict(payload.get("monitor")),
        host_fan=_host_fan_from_dict(payload.get("host_fan")),
        openrgb=_openrgb_from_dict(payload.get("openrgb")),
        lianli_wireless=_lianli_wireless_from_dict(payload.get("lianli_wireless")),
        active_scene=str(payload.get("active_scene", "")),
        scenes=payload.get("scenes") if isinstance(payload.get("scenes"), dict) else {},
        keepalive_enabled=bool(payload.get("keepalive_enabled", True)),
    )
```

- [ ] **Step 4: Run settings tests and verify pass**

Run:

```powershell
python -m pytest tests\test_settings.py::test_settings_round_trips_global_scenes_without_losing_openrgb_scenes tests\test_settings.py::test_settings_defaults_global_scene_storage_to_empty -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```powershell
git add usb9_lcd\gui\settings.py tests\test_settings.py
git commit -m "Add global scene settings"
```

## Task 2: Pure Scene Model and Planner

**Files:**
- Create: `usb9_lcd/gui/scenes.py`
- Test: `tests/test_scene_center.py`

- [ ] **Step 1: Write failing pure scene tests**

Create `tests/test_scene_center.py`:

```python
from __future__ import annotations

from usb9_lcd.gui.scenes import (
    SceneAvailability,
    SceneStatus,
    build_builtin_scenes,
    build_scene_apply_plan,
    normalize_scene_payload,
)


def test_builtin_scenes_include_required_modes():
    scenes = build_builtin_scenes()

    assert {"daily", "gaming", "sleep", "showcase", "quiet", "temperature-warning"} <= set(scenes)
    assert scenes["sleep"].screen.mode == "off"
    assert scenes["sleep"].openrgb.mode == "off"
    assert scenes["sleep"].lianli_lighting.mode == "off"


def test_scene_planner_marks_lianli_permission_required_when_write_locked():
    scene = normalize_scene_payload(
        {
            "name": "Wireless",
            "lianli_lighting": {"mode": "effect", "effect": "runway", "color": "#ff2d55"},
            "safety": {"allow_lianli_write": False},
        },
        key="wireless",
    )
    availability = SceneAvailability(
        screen_available=True,
        openrgb_available=True,
        lianli_available=True,
        lianli_write_unlocked=False,
        host_fan_available=True,
        lianli_fan_available=True,
    )

    plan = build_scene_apply_plan(scene, availability)

    lianli_action = next(action for action in plan.actions if action.subsystem == "lianli_lighting")
    assert lianli_action.status is SceneStatus.PERMISSION_REQUIRED
    assert plan.can_apply is False


def test_scene_planner_allows_partial_apply_when_openrgb_missing():
    scene = normalize_scene_payload(
        {
            "name": "Partial",
            "screen": {"mode": "off"},
            "openrgb": {"mode": "off"},
        },
        key="partial",
    )
    availability = SceneAvailability(
        screen_available=True,
        openrgb_available=False,
        lianli_available=False,
        lianli_write_unlocked=False,
        host_fan_available=False,
        lianli_fan_available=False,
    )

    plan = build_scene_apply_plan(scene, availability)

    statuses = {action.subsystem: action.status for action in plan.actions}
    assert statuses["screen"] is SceneStatus.READY
    assert statuses["openrgb"] is SceneStatus.DEVICE_MISSING
    assert plan.can_apply is True
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests\test_scene_center.py -q
```

Expected: import failure for missing `usb9_lcd.gui.scenes`.

- [ ] **Step 3: Implement scene model and planner**

Create `usb9_lcd/gui/scenes.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SceneStatus(Enum):
    READY = "ready"
    APPLIED = "applied"
    SKIPPED = "skipped"
    PERMISSION_REQUIRED = "permission_required"
    DEVICE_MISSING = "device_missing"
    FAILED = "failed"


@dataclass(frozen=True)
class SceneSubsystemAction:
    subsystem: str
    mode: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: SceneStatus = SceneStatus.READY
    message: str = ""


@dataclass(frozen=True)
class SceneAvailability:
    screen_available: bool
    openrgb_available: bool
    lianli_available: bool
    lianli_write_unlocked: bool
    host_fan_available: bool
    lianli_fan_available: bool


@dataclass(frozen=True)
class SceneSection:
    mode: str = "unchanged"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SceneSafety:
    dry_run: bool = False
    require_confirmation: bool = False
    allow_lianli_write: bool = False


@dataclass(frozen=True)
class SceneProfile:
    key: str
    name: str
    description: str = ""
    screen: SceneSection = field(default_factory=SceneSection)
    openrgb: SceneSection = field(default_factory=SceneSection)
    lianli_lighting: SceneSection = field(default_factory=SceneSection)
    host_fan: SceneSection = field(default_factory=SceneSection)
    lianli_fan: SceneSection = field(default_factory=SceneSection)
    safety: SceneSafety = field(default_factory=SceneSafety)


@dataclass(frozen=True)
class SceneApplyPlan:
    scene_key: str
    scene_name: str
    actions: tuple[SceneSubsystemAction, ...]

    @property
    def can_apply(self) -> bool:
        return any(action.status is SceneStatus.READY for action in self.actions) and not any(
            action.status is SceneStatus.PERMISSION_REQUIRED for action in self.actions
        )


def _section(payload: dict[str, Any], name: str) -> SceneSection:
    value = payload.get(name)
    if not isinstance(value, dict):
        return SceneSection()
    mode = str(value.get("mode", "unchanged")).strip().lower() or "unchanged"
    return SceneSection(mode=mode, payload={str(key): item for key, item in value.items() if key != "mode"})


def _safety(payload: dict[str, Any]) -> SceneSafety:
    value = payload.get("safety")
    if not isinstance(value, dict):
        return SceneSafety()
    return SceneSafety(
        dry_run=bool(value.get("dry_run", False)),
        require_confirmation=bool(value.get("require_confirmation", False)),
        allow_lianli_write=bool(value.get("allow_lianli_write", False)),
    )


def normalize_scene_payload(payload: dict[str, Any], *, key: str) -> SceneProfile:
    return SceneProfile(
        key=key,
        name=str(payload.get("name", key)),
        description=str(payload.get("description", "")),
        screen=_section(payload, "screen"),
        openrgb=_section(payload, "openrgb"),
        lianli_lighting=_section(payload, "lianli_lighting"),
        host_fan=_section(payload, "host_fan"),
        lianli_fan=_section(payload, "lianli_fan"),
        safety=_safety(payload),
    )


def build_builtin_scenes() -> dict[str, SceneProfile]:
    payloads = {
        "daily": {
            "name": "日常",
            "description": "监控屏幕、默认灯效、标准风扇曲线",
            "screen": {"mode": "monitor_profile", "profile": "默认配置"},
            "openrgb": {"mode": "unchanged"},
            "lianli_lighting": {"mode": "unchanged"},
            "host_fan": {"mode": "preset", "preset": "normal", "auto_curve_enabled": True},
            "lianli_fan": {"mode": "preset", "preset": "normal", "auto_curve_enabled": True},
        },
        "gaming": {
            "name": "游戏",
            "description": "保留监控，提升风扇策略，灯光保持当前状态",
            "screen": {"mode": "monitor_profile", "profile": "游戏"},
            "host_fan": {"mode": "preset", "preset": "high", "auto_curve_enabled": True},
            "lianli_fan": {"mode": "preset", "preset": "high", "auto_curve_enabled": True},
        },
        "sleep": {
            "name": "睡眠",
            "description": "黑屏并关闭 OpenRGB 与联力灯光",
            "screen": {"mode": "off"},
            "openrgb": {"mode": "off"},
            "lianli_lighting": {"mode": "off"},
            "host_fan": {"mode": "unchanged"},
            "lianli_fan": {"mode": "unchanged"},
        },
        "showcase": {
            "name": "展示",
            "description": "亮灯展示并使用标准风扇曲线",
            "screen": {"mode": "monitor_profile", "profile": "展示"},
            "openrgb": {"mode": "effect", "effect": "rainbow", "brightness_percent": 80},
            "lianli_lighting": {"mode": "effect", "effect": "rainbow", "brightness": 100, "speed": 75},
            "host_fan": {"mode": "preset", "preset": "normal", "auto_curve_enabled": True},
            "lianli_fan": {"mode": "preset", "preset": "normal", "auto_curve_enabled": True},
        },
        "quiet": {
            "name": "静音",
            "description": "降低亮度并使用安静风扇曲线",
            "openrgb": {"mode": "effect", "effect": "static", "brightness_percent": 25},
            "lianli_lighting": {"mode": "effect", "effect": "static", "brightness": 25},
            "host_fan": {"mode": "preset", "preset": "quiet", "auto_curve_enabled": True},
            "lianli_fan": {"mode": "preset", "preset": "quiet", "auto_curve_enabled": True},
        },
        "temperature-warning": {
            "name": "温度警告",
            "description": "红色警示灯效和高风扇曲线",
            "openrgb": {"mode": "effect", "effect": "static", "color": "#ff2d55", "brightness_percent": 100},
            "lianli_lighting": {"mode": "effect", "effect": "warning", "color": "#ff2d55", "accent_color": "#ffffff"},
            "host_fan": {"mode": "preset", "preset": "high", "auto_curve_enabled": True},
            "lianli_fan": {"mode": "preset", "preset": "high", "auto_curve_enabled": True},
        },
    }
    return {key: normalize_scene_payload(payload, key=key) for key, payload in payloads.items()}


def build_scene_apply_plan(scene: SceneProfile, availability: SceneAvailability) -> SceneApplyPlan:
    checks = {
        "screen": availability.screen_available,
        "openrgb": availability.openrgb_available,
        "lianli_lighting": availability.lianli_available,
        "host_fan": availability.host_fan_available,
        "lianli_fan": availability.lianli_fan_available,
    }
    sections = {
        "screen": scene.screen,
        "openrgb": scene.openrgb,
        "lianli_lighting": scene.lianli_lighting,
        "host_fan": scene.host_fan,
        "lianli_fan": scene.lianli_fan,
    }
    actions: list[SceneSubsystemAction] = []
    for subsystem, section in sections.items():
        if section.mode == "unchanged":
            actions.append(SceneSubsystemAction(subsystem, section.mode, section.payload, SceneStatus.SKIPPED, "保持当前状态"))
            continue
        if not checks[subsystem]:
            actions.append(SceneSubsystemAction(subsystem, section.mode, section.payload, SceneStatus.DEVICE_MISSING, "设备不可用"))
            continue
        if subsystem == "lianli_lighting" and section.mode in {"effect", "off"}:
            if not scene.safety.allow_lianli_write and not availability.lianli_write_unlocked:
                actions.append(
                    SceneSubsystemAction(
                        subsystem,
                        section.mode,
                        section.payload,
                        SceneStatus.PERMISSION_REQUIRED,
                        "联力真实写入未解锁",
                    )
                )
                continue
        actions.append(SceneSubsystemAction(subsystem, section.mode, section.payload, SceneStatus.READY, "等待执行"))
    return SceneApplyPlan(scene.key, scene.name, tuple(actions))
```

- [ ] **Step 4: Run pure scene tests**

Run:

```powershell
python -m pytest tests\test_scene_center.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```powershell
git add usb9_lcd\gui\scenes.py tests\test_scene_center.py
git commit -m "Add global scene planner"
```

## Task 3: Scene Coordinator Hooks in Main Window

**Files:**
- Modify: `usb9_lcd/gui/main_window.py`
- Test: `tests/test_gui_import.py`

- [ ] **Step 1: Write failing coordinator tests**

Add to `tests/test_gui_import.py`:

```python
def _scene_test_window():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow
    from usb9_lcd.gui.settings import GuiSettings

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
        settings=GuiSettings(),
    )
    return app, window


def test_main_window_applies_sleep_scene_through_existing_sleep_path(monkeypatch, tmp_path):
    app, window = _scene_test_window()
    calls = []
    window.sleep_all_off = lambda: calls.append("sleep")

    result = window.apply_global_scene_by_key("sleep")

    assert calls == ["sleep"]
    assert result.scene_key == "sleep"
    assert any(item.status == "applied" for item in result.items)
    window.close()
    app.quit()


def test_main_window_lianli_scene_requires_write_gate(monkeypatch, tmp_path):
    app, window = _scene_test_window()

    result = window.apply_global_scene_payload(
        "wireless",
        {
            "name": "Wireless",
            "lianli_lighting": {"mode": "effect", "effect": "runway", "color": "#ff2d55"},
            "safety": {"allow_lianli_write": False},
        },
    )

    assert result.scene_key == "wireless"
    assert any(item.subsystem == "lianli_lighting" and item.status == "permission_required" for item in result.items)
    window.close()
    app.quit()
```

- [ ] **Step 2: Run coordinator tests and verify failure**

Run:

```powershell
python -m pytest tests\test_gui_import.py::test_main_window_applies_sleep_scene_through_existing_sleep_path tests\test_gui_import.py::test_main_window_lianli_scene_requires_write_gate -q
```

Expected: failure because `apply_global_scene_by_key` and `apply_global_scene_payload` do not exist.

- [ ] **Step 3: Add coordinator result types and methods**

In `usb9_lcd/gui/main_window.py`, import:

```python
from dataclasses import dataclass
from typing import Any

from usb9_lcd.gui.scenes import (
    SceneAvailability,
    SceneStatus,
    build_builtin_scenes,
    build_scene_apply_plan,
    normalize_scene_payload,
)
```

Add near the top-level helper classes:

```python
@dataclass(frozen=True)
class SceneApplyItem:
    subsystem: str
    status: str
    message: str


@dataclass(frozen=True)
class SceneApplySummary:
    scene_key: str
    scene_name: str
    items: tuple[SceneApplyItem, ...]
```

Add methods to `MainWindow`:

```python
    def apply_global_scene_by_key(self, scene_key: str) -> SceneApplySummary:
        builtins = build_builtin_scenes()
        if scene_key in builtins:
            scene = builtins[scene_key]
        else:
            payload = self.settings.scenes.get(scene_key)
            if not isinstance(payload, dict):
                return SceneApplySummary(scene_key, scene_key, (SceneApplyItem("scene", "failed", "场景不存在"),))
            scene = normalize_scene_payload(payload, key=scene_key)
        return self._apply_global_scene(scene)

    def apply_global_scene_payload(self, scene_key: str, payload: dict[str, Any]) -> SceneApplySummary:
        return self._apply_global_scene(normalize_scene_payload(payload, key=scene_key))

    def _scene_availability(self) -> SceneAvailability:
        return SceneAvailability(
            screen_available=bool(self.devices),
            openrgb_available=bool(getattr(self.lighting_page, "targets", [])),
            lianli_available=bool(getattr(self.lianli_page, "_lianli_targets", [])),
            lianli_write_unlocked=bool(getattr(self.lianli_page, "_lianli_write_gate_unlocked", False)),
            host_fan_available=bool(getattr(self.fan_page, "_snapshot", None)),
            lianli_fan_available=bool(getattr(self.lianli_page, "_lianli_targets", [])),
        )

    def _apply_global_scene(self, scene) -> SceneApplySummary:  # noqa: ANN001
        if scene.key == "sleep":
            self.sleep_all_off()
            self.settings.active_scene = scene.key
            save_settings(self.settings)
            return SceneApplySummary(scene.key, scene.name, (SceneApplyItem("scene", "applied", "睡眠场景已执行"),))
        plan = build_scene_apply_plan(scene, self._scene_availability())
        items: list[SceneApplyItem] = []
        applied_any = False
        for action in plan.actions:
            if action.status is not SceneStatus.READY:
                items.append(SceneApplyItem(action.subsystem, action.status.value, action.message))
                continue
            try:
                self._execute_scene_action(action.subsystem, action.mode, action.payload)
            except Exception as error:  # pragma: no cover - exercised by GUI integration tests
                log_exception("scene_action_failed", error)
                items.append(SceneApplyItem(action.subsystem, SceneStatus.FAILED.value, self._friendly_error(error)))
            else:
                applied_any = True
                items.append(SceneApplyItem(action.subsystem, SceneStatus.APPLIED.value, "已应用"))
        if applied_any:
            self.settings.active_scene = scene.key
            save_settings(self.settings)
            self.home_page.set_mode_indicator(scene.name)
            self.home_page.add_event(f"场景已应用：{scene.name}")
        return SceneApplySummary(scene.key, scene.name, tuple(items))
```

Add a conservative executor:

```python
    def _execute_scene_action(self, subsystem: str, mode: str, payload: dict[str, Any]) -> None:
        if subsystem == "screen" and mode == "off":
            for device in self._sleep_mode_devices():
                frame = self._black_frame_for_device(device)
                with self._lcd_output_lock:
                    self.driver.upload_static_frame(device, frame)
                    self._set_display_sleep_state(device)
                self._remember_uploaded_frame(device, frame, sleep_mode=True)
            return
        if subsystem == "openrgb" and mode == "off":
            self.lighting_page.turn_off_all_lighting()
            return
        if subsystem == "lianli_lighting" and mode == "off":
            self.lianli_page.turn_off_all_lighting()
            return
        if subsystem in {"host_fan", "lianli_fan", "openrgb", "lianli_lighting", "screen"}:
            return
        raise ValueError(f"未知场景子系统：{subsystem}")
```

- [ ] **Step 4: Run coordinator tests**

Run:

```powershell
python -m pytest tests\test_gui_import.py::test_main_window_applies_sleep_scene_through_existing_sleep_path tests\test_gui_import.py::test_main_window_lianli_scene_requires_write_gate -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```powershell
git add usb9_lcd\gui\main_window.py tests\test_gui_import.py
git commit -m "Wire global scene coordinator"
```

## Task 4: Home Page Scene Cards

**Files:**
- Modify: `usb9_lcd/gui/home.py`
- Modify: `usb9_lcd/gui/main_window.py`
- Test: `tests/test_gui_import.py`

- [ ] **Step 1: Write failing home tests**

Add:

```python
def test_home_page_exposes_global_scene_cards(monkeypatch, tmp_path):
    app, window = _scene_test_window()

    labels = [button.text() for button in window.home_page.scene_buttons]

    assert {"日常", "游戏", "睡眠", "展示", "静音", "温度警告"} <= set(labels)
    window.close()
    app.quit()


def test_home_scene_card_applies_scene(monkeypatch, tmp_path):
    app, window = _scene_test_window()
    applied = []
    window.apply_global_scene_by_key = lambda key: applied.append(key)

    sleep_button = next(button for button in window.home_page.scene_buttons if button.text() == "睡眠")
    sleep_button.click()

    assert applied == ["sleep"]
    window.close()
    app.quit()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests\test_gui_import.py::test_home_page_exposes_global_scene_cards tests\test_gui_import.py::test_home_scene_card_applies_scene -q
```

Expected: failure because `scene_buttons` does not exist and buttons do not call global scene keys.

- [ ] **Step 3: Update home constructor**

Change `ControlCenterPage.__init__` signature:

```python
        apply_scene: Callable[[str], object],
```

Pass it to `_hero_panel(apply_scene)` instead of `_hero_panel(sleep_all_off)`.

Replace the local `modes` tuple in `_hero_panel()` with:

```python
        self.scene_buttons = []
        scenes = (
            ("daily", "日常", "屏幕监控、风扇自动、灯效默认"),
            ("gaming", "游戏", "性能优先，保留监控画面"),
            ("quiet", "静音", "低噪声策略，降低灯光亮度"),
            ("sleep", "睡眠", "黑屏并关闭所有灯光"),
            ("showcase", "展示", "亮灯展示整机状态"),
            ("temperature-warning", "温度警告", "红色警示灯效和高风扇曲线"),
        )
        for index, (key, label, description) in enumerate(scenes):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("SegmentButton")
            button.setToolTip(description)
            button.setProperty("modeAction", label)
            button.setProperty("sceneKey", key)
            if index == 0:
                button.setChecked(True)
            button.clicked.connect(lambda _checked=False, scene_key=key, name=label: self._set_scene_mode(name, scene_key, apply_scene))
            self.scene_buttons.append(button)
            self.mode_group.addButton(button, index)
            layout.addWidget(button, 1 + index // 4, index % 4)
```

Add:

```python
    def _set_scene_mode(self, mode: str, scene_key: str, apply_scene: Callable[[str], object]) -> None:
        self.set_mode_indicator(mode)
        self.add_event(f"切换到{mode}场景")
        apply_scene(scene_key)
```

- [ ] **Step 4: Wire main window**

In `MainWindow.__init__`, pass:

```python
            self.apply_global_scene_by_key,
```

to `ControlCenterPage` after `sleep_all_off`.

- [ ] **Step 5: Run home tests**

Run:

```powershell
python -m pytest tests\test_gui_import.py::test_home_page_exposes_global_scene_cards tests\test_gui_import.py::test_home_scene_card_applies_scene -q
```

Expected: `2 passed`.

- [ ] **Step 6: Commit**

```powershell
git add usb9_lcd\gui\home.py usb9_lcd\gui\main_window.py tests\test_gui_import.py
git commit -m "Add scene cards to home page"
```

## Task 5: Compact Scene Page

**Files:**
- Create: `usb9_lcd/gui/scene_page.py`
- Modify: `usb9_lcd/gui/main_window.py`
- Modify: `usb9_lcd/gui/pages.py`
- Test: `tests/test_gui_import.py`

- [ ] **Step 1: Write failing scene page tests**

Add:

```python
def test_scene_page_lists_builtin_scenes(monkeypatch, tmp_path):
    app, window = _scene_test_window()

    names = [window.scene_page.scene_combo.itemText(index) for index in range(window.scene_page.scene_combo.count())]

    assert "日常" in names
    assert "睡眠" in names
    window.close()
    app.quit()


def test_scene_page_apply_button_delegates_to_main_window(monkeypatch, tmp_path):
    app, window = _scene_test_window()
    applied = []
    window.scene_page.apply_scene = lambda key: applied.append(key)
    window.scene_page.scene_combo.setCurrentIndex(window.scene_page.scene_combo.findData("sleep"))

    window.scene_page.apply_button.click()

    assert applied == ["sleep"]
    window.close()
    app.quit()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests\test_gui_import.py::test_scene_page_lists_builtin_scenes tests\test_gui_import.py::test_scene_page_apply_button_delegates_to_main_window -q
```

Expected: failure because `scene_page` does not exist.

- [ ] **Step 3: Create scene page**

Create `usb9_lcd/gui/scene_page.py`:

```python
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QComboBox, QFrame, QGridLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from usb9_lcd.gui.scenes import build_builtin_scenes


class SceneCenterPage(QWidget):
    def __init__(self, apply_scene: Callable[[str], object]) -> None:
        super().__init__()
        self.apply_scene = apply_scene
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(14)

        title = QLabel("场景中心")
        title.setObjectName("PageTitle")
        subtitle = QLabel("统一应用屏幕、灯效、风扇与联力无线状态")
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        panel = QFrame()
        panel.setObjectName("SceneCenterPanel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        self.scene_combo = QComboBox()
        for key, scene in build_builtin_scenes().items():
            self.scene_combo.addItem(scene.name, key)
        self.apply_button = QPushButton("应用场景")
        self.apply_button.setObjectName("PrimaryButton")
        self.apply_button.clicked.connect(self._apply_current_scene)
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setFixedHeight(120)
        self.scene_combo.currentIndexChanged.connect(self._refresh_summary)

        grid.addWidget(QLabel("场景"), 0, 0)
        grid.addWidget(self.scene_combo, 0, 1)
        grid.addWidget(self.apply_button, 0, 2)
        grid.addWidget(self.summary_text, 1, 0, 1, 3)
        layout.addWidget(panel)
        layout.addStretch(1)
        self._refresh_summary()

    def _apply_current_scene(self) -> None:
        key = str(self.scene_combo.currentData() or "")
        if key:
            self.apply_scene(key)

    def _refresh_summary(self) -> None:
        key = str(self.scene_combo.currentData() or "")
        scene = build_builtin_scenes().get(key)
        if scene is None:
            self.summary_text.setPlainText("未选择场景")
            return
        self.summary_text.setPlainText(f"{scene.name}\n{scene.description}")
```

- [ ] **Step 4: Wire main window navigation**

In `usb9_lcd/gui/main_window.py`, import `SceneCenterPage`, create `self.scene_page = SceneCenterPage(self.apply_global_scene_by_key)`, add it to `page_indexes` as `"scenes": 7`, add a navigation row label `场景`, and add it to `self.pages`.

In `usb9_lcd/gui/pages.py`, export `SceneCenterPage`:

```python
from usb9_lcd.gui.scene_page import SceneCenterPage
```

Add `"SceneCenterPage"` to `__all__`.

- [ ] **Step 5: Run scene page tests**

Run:

```powershell
python -m pytest tests\test_gui_import.py::test_scene_page_lists_builtin_scenes tests\test_gui_import.py::test_scene_page_apply_button_delegates_to_main_window -q
```

Expected: `2 passed`.

- [ ] **Step 6: Commit**

```powershell
git add usb9_lcd\gui\scene_page.py usb9_lcd\gui\main_window.py usb9_lcd\gui\pages.py tests\test_gui_import.py
git commit -m "Add scene center page"
```

## Task 6: Verification, Regression, and Packaging

**Files:**
- Modify only files needed to fix failures found by these checks.

- [ ] **Step 1: Run focused scene tests**

Run:

```powershell
python -m pytest tests\test_scene_center.py tests\test_settings.py tests\test_gui_import.py::test_home_page_exposes_global_scene_cards tests\test_gui_import.py::test_home_scene_card_applies_scene tests\test_gui_import.py::test_scene_page_lists_builtin_scenes tests\test_gui_import.py::test_scene_page_apply_button_delegates_to_main_window -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run existing affected GUI tests**

Run:

```powershell
python -m pytest tests\test_gui_import.py::test_main_window_sleep_all_off_blanks_lcds_and_turns_off_openrgb tests\test_gui_import.py::test_lighting_page_saves_and_applies_multi_target_scene tests\test_gui_import.py::test_lighting_page_renames_and_deletes_scene -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run packaging smoke test**

Run:

```powershell
python -m pytest tests\test_packaging_build.py -q
```

Expected: packaging tests pass.

- [ ] **Step 4: Build Windows executable**

Close any running `dist\LumenHub\LumenHub.exe`, then run:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build-exe.ps1 -Clean
```

Expected output includes:

```text
Packaged executable: E:\风扇控制\lumen-hub\dist\LumenHub\LumenHub.exe
```

- [ ] **Step 5: Commit verification fixes if needed**

If any verification fix was required:

```powershell
git add <changed-files>
git commit -m "Stabilize scene center integration"
```

- [ ] **Step 6: Push**

```powershell
git push origin main
```

Expected: branch `main` updates on GitHub.

## Self-Review

Spec coverage:

- Global scene model: Task 1 and Task 2.
- Built-in scene presets: Task 2.
- Pure apply plan and safety states: Task 2.
- Home scene cards: Task 4.
- Scene editor/status page: Task 5.
- Existing hardware methods reused through coordinator: Task 3.
- LIAN LI write gate preserved: Task 2 and Task 3.
- Existing OpenRGB scenes preserved: Task 1 and Task 6.
- Tests before implementation: each implementation task begins with failing tests.

Plan scan is clean. Type names are consistent across tasks: `SceneProfile`, `SceneAvailability`, `SceneStatus`, `SceneApplyPlan`, `SceneApplySummary`, and `SceneCenterPage`.
