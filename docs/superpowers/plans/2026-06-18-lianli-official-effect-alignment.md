# LIAN LI Official Effect Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align LIAN LI wireless lighting with the official L-Connect W/W* effect list and ensure every visible color/direction control affects the generated command.

**Architecture:** Add one official LIAN LI wireless effect catalog and make GUI, settings, scene center, and backend public validation read from that catalog. Keep packet generation in `usb9_lcd/lianli/wireless.py`, but narrow public TLV2 metadata to official wireless effects so stale generated effects cannot be selected or sent through user-facing paths.

**Tech Stack:** Python 3.11, PySide6, pytest, existing LIAN LI TLV2 packet builder.

---

## File Structure

- Create `usb9_lcd/lianli/effects.py`: official wireless effect catalog, aliases, normalization helpers, and GUI option tuples.
- Modify `usb9_lcd/lianli/wireless.py`: import the catalog, narrow public TLV2 specs/capabilities to official wireless effects, reject removed effects through the existing `LianLiWirelessError` path, and keep runway/heartbeat color inputs active.
- Modify `usb9_lcd/gui/lianli_wireless_page.py`: replace the local effect option tuple and native effect set with catalog-driven rendering and sending.
- Modify `usb9_lcd/gui/settings.py`: migrate stale saved LIAN LI effect keys to `off` during load.
- Modify `usb9_lcd/gui/scenes.py`: normalize scene-center LIAN LI effect payloads through the same official catalog.
- Modify `tests/test_lianli_wireless.py`: add catalog/backend tests and update tests that still treat removed generated effects as public.
- Modify `tests/test_gui_import.py`: make GUI effect list, swatch count, and sender tests catalog-driven.
- Modify `tests/test_settings.py`: cover stale LIAN LI effect migration.
- Modify `tests/test_scene_center.py`: cover stale scene effect normalization.

## Task 1: Add The Official LIAN LI Wireless Effect Catalog

**Files:**
- Create: `usb9_lcd/lianli/effects.py`
- Modify: `tests/test_lianli_wireless.py`

- [ ] **Step 1: Write the failing catalog test**

Add this test near the other TLV2 metadata tests in `tests/test_lianli_wireless.py`:

```python
def test_official_lianli_wireless_effect_catalog_matches_lconnect_w_list():
    from usb9_lcd.lianli.effects import (
        OFFICIAL_LIANLI_WIRELESS_EFFECT_OPTIONS,
        OFFICIAL_LIANLI_WIRELESS_EFFECTS,
        normalize_lianli_wireless_effect,
    )

    assert OFFICIAL_LIANLI_WIRELESS_EFFECT_OPTIONS == (
        ("关灯", "off"),
        ("彩虹 (W*)", "rainbow"),
        ("渐变彩虹 (W*)", "gradient-rainbow"),
        ("单色 (W*)", "static"),
        ("呼吸 (W*)", "breathing"),
        ("流星 (W*)", "meteor"),
        ("跑道 (W*)", "runway"),
        ("星空 (W*)", "starry"),
        ("色彩循环 (W*)", "color-cycle"),
        ("覆盖周期 (W*)", "cover-cycle"),
        ("波浪 (W*)", "wave"),
        ("流星雨 (W*)", "meteor-shower"),
        ("迪斯科 (W*)", "disco"),
        ("爆破 (W*)", "blow-up"),
        ("心跳 (W*)", "heartbeat"),
        ("警示 (W*)", "warning"),
        ("海洋 (W*)", "ocean"),
        ("涟漪 (W*)", "ripple"),
        ("回声 (W*)", "echo"),
    )
    assert normalize_lianli_wireless_effect("gradient-rainbow") == "rainbow-morph"
    assert normalize_lianli_wireless_effect("starry") == "twinkle"
    assert {effect.key for effect in OFFICIAL_LIANLI_WIRELESS_EFFECTS} == {
        value for _label, value in OFFICIAL_LIANLI_WIRELESS_EFFECT_OPTIONS
    }
```

- [ ] **Step 2: Run the catalog test and verify it fails**

Run:

```powershell
python -m pytest tests/test_lianli_wireless.py::test_official_lianli_wireless_effect_catalog_matches_lconnect_w_list -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'usb9_lcd.lianli.effects'`.

- [ ] **Step 3: Create the catalog module**

Create `usb9_lcd/lianli/effects.py` with this content:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ColorMode = Literal["none", "primary", "primary_accent", "palette"]


@dataclass(frozen=True)
class LianLiWirelessEffect:
    label: str
    key: str
    backend_key: str
    color_slots: int = 0
    color_mode: ColorMode = "none"
    uses_direction: bool = False
    uses_speed: bool = True
    uses_brightness: bool = True
    default_colors: tuple[str, ...] = ()
    template_backed: bool = True


OFFICIAL_LIANLI_WIRELESS_EFFECTS: tuple[LianLiWirelessEffect, ...] = (
    LianLiWirelessEffect("关灯", "off", "off", uses_speed=False, uses_brightness=False, template_backed=False),
    LianLiWirelessEffect("彩虹 (W*)", "rainbow", "rainbow", uses_direction=True),
    LianLiWirelessEffect("渐变彩虹 (W*)", "gradient-rainbow", "rainbow-morph", uses_direction=True),
    LianLiWirelessEffect(
        "单色 (W*)",
        "static",
        "static",
        color_slots=1,
        color_mode="primary",
        uses_speed=False,
        default_colors=("#00fe00",),
    ),
    LianLiWirelessEffect("呼吸 (W*)", "breathing", "breathing", color_slots=1, color_mode="primary", default_colors=("#fe0000",)),
    LianLiWirelessEffect("流星 (W*)", "meteor", "meteor", color_slots=1, color_mode="primary", uses_direction=True, default_colors=("#fe0000",)),
    LianLiWirelessEffect("跑道 (W*)", "runway", "runway", color_slots=2, color_mode="primary_accent", uses_direction=True, default_colors=("#fe0000", "#00fe00")),
    LianLiWirelessEffect("星空 (W*)", "starry", "twinkle", color_slots=2, color_mode="primary_accent", default_colors=("#87002a", "#ff69d9")),
    LianLiWirelessEffect("色彩循环 (W*)", "color-cycle", "color-cycle", color_slots=3, color_mode="palette", default_colors=("#0000fe", "#fe0000", "#ffff00")),
    LianLiWirelessEffect("覆盖周期 (W*)", "cover-cycle", "cover-cycle", color_slots=2, color_mode="palette", uses_direction=True, default_colors=("#0000fe", "#fe0000"), template_backed=False),
    LianLiWirelessEffect("波浪 (W*)", "wave", "wave", color_slots=1, color_mode="primary", uses_direction=True, default_colors=("#8a00ff",)),
    LianLiWirelessEffect("流星雨 (W*)", "meteor-shower", "meteor-shower", color_slots=4, color_mode="palette", uses_direction=True, default_colors=("#ff0090", "#0000fe", "#ffff00", "#00fe00")),
    LianLiWirelessEffect("迪斯科 (W*)", "disco", "disco", color_slots=4, color_mode="palette", uses_direction=True, default_colors=("#fe0000", "#00fe00", "#0000fe", "#ffff00"), template_backed=False),
    LianLiWirelessEffect("爆破 (W*)", "blow-up", "blow-up", color_slots=2, color_mode="primary_accent", uses_direction=True, default_colors=("#fe0000", "#007800"), template_backed=False),
    LianLiWirelessEffect("心跳 (W*)", "heartbeat", "heartbeat", color_slots=2, color_mode="primary_accent", uses_direction=True, default_colors=("#87002a", "#ff69d9"), template_backed=False),
    LianLiWirelessEffect("警示 (W*)", "warning", "warning", color_slots=2, color_mode="primary_accent", uses_direction=True, default_colors=("#ffff00", "#00ffff"), template_backed=False),
    LianLiWirelessEffect("海洋 (W*)", "ocean", "ocean", color_slots=2, color_mode="palette", uses_direction=True, default_colors=("#00008a", "#ffffff"), template_backed=False),
    LianLiWirelessEffect("涟漪 (W*)", "ripple", "ripple", color_slots=2, color_mode="palette", uses_direction=True, default_colors=("#87002a", "#00ffff")),
    LianLiWirelessEffect("回声 (W*)", "echo", "echo", color_slots=2, color_mode="primary_accent", uses_direction=True, default_colors=("#000000", "#00ffff"), template_backed=False),
)

OFFICIAL_LIANLI_WIRELESS_EFFECT_OPTIONS = tuple((effect.label, effect.key) for effect in OFFICIAL_LIANLI_WIRELESS_EFFECTS)
OFFICIAL_LIANLI_WIRELESS_EFFECT_BY_KEY = {effect.key: effect for effect in OFFICIAL_LIANLI_WIRELESS_EFFECTS}
OFFICIAL_LIANLI_WIRELESS_EFFECT_BY_BACKEND_KEY = {effect.backend_key: effect for effect in OFFICIAL_LIANLI_WIRELESS_EFFECTS}
OFFICIAL_LIANLI_WIRELESS_EFFECT_KEYS = frozenset(OFFICIAL_LIANLI_WIRELESS_EFFECT_BY_KEY)
OFFICIAL_LIANLI_WIRELESS_BACKEND_KEYS = frozenset(OFFICIAL_LIANLI_WIRELESS_EFFECT_BY_BACKEND_KEY)

_ALIASES = {
    "gradient_rainbow": "gradient-rainbow",
    "rainbow_morph": "gradient-rainbow",
    "rainbow-morph": "gradient-rainbow",
    "star": "starry",
    "twinkle": "starry",
}


def lianli_wireless_effect(effect: str) -> LianLiWirelessEffect:
    key = str(effect).strip().lower().replace("_", "-")
    key = _ALIASES.get(key, key)
    try:
        return OFFICIAL_LIANLI_WIRELESS_EFFECT_BY_KEY[key]
    except KeyError as error:
        raise ValueError(f"unsupported official LIAN LI wireless effect: {effect}") from error


def normalize_lianli_wireless_effect(effect: str) -> str:
    return lianli_wireless_effect(effect).backend_key
```

- [ ] **Step 4: Run the catalog test and verify it passes**

Run:

```powershell
python -m pytest tests/test_lianli_wireless.py::test_official_lianli_wireless_effect_catalog_matches_lconnect_w_list -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add usb9_lcd/lianli/effects.py tests/test_lianli_wireless.py
git commit -m "Add LIAN LI official wireless effect catalog"
```

## Task 2: Narrow Backend Public TLV2 Effects To The Official Catalog

**Files:**
- Modify: `usb9_lcd/lianli/wireless.py`
- Modify: `tests/test_lianli_wireless.py`

- [ ] **Step 1: Write failing backend public-scope tests**

Add these tests near the TLV2 capability tests in `tests/test_lianli_wireless.py`:

```python
def test_tlv2_public_specs_match_official_lianli_wireless_catalog():
    from usb9_lcd.lianli.effects import OFFICIAL_LIANLI_WIRELESS_BACKEND_KEYS

    assert set(TLV2_EFFECT_SPECS) == OFFICIAL_LIANLI_WIRELESS_BACKEND_KEYS - {"off"}
    assert set(TLV2_EFFECT_CAPABILITIES) == set(TLV2_EFFECT_SPECS)


def test_tlv2_rejects_removed_generated_effects_from_public_api():
    removed = (
        "staggered",
        "tide",
        "mixing",
        "voice",
        "door",
        "render",
        "reflect",
        "tail-chasing",
        "paint",
        "ping-pong",
        "stack",
        "racing",
        "lottery",
        "intertwine",
        "collide",
        "electric-current",
    )
    for effect in removed:
        with pytest.raises(LianLiWirelessError):
            tlv2_effect_capability(effect)
        with pytest.raises(LianLiWirelessError):
            generate_tlv2_effect_rgb_frames(effect, led_count=26)
```

- [ ] **Step 2: Run backend scope tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_lianli_wireless.py::test_tlv2_public_specs_match_official_lianli_wireless_catalog tests/test_lianli_wireless.py::test_tlv2_rejects_removed_generated_effects_from_public_api -q
```

Expected: FAIL because old generated effects are still present in `TLV2_EFFECT_SPECS`, aliases, and capabilities.

- [ ] **Step 3: Narrow backend specs and aliases**

In `usb9_lcd/lianli/wireless.py`, add the import near the existing official effects import:

```python
from usb9_lcd.lianli.effects import OFFICIAL_LIANLI_WIRELESS_BACKEND_KEYS
```

Then remove these keys from `TLV2_EFFECT_SPECS`, `TLV2_EFFECT_SEQUENCE_REPEAT_COUNTS`, and `TLV2_EFFECT_CAPABILITIES`:

```python
{
    "staggered",
    "tide",
    "mixing",
    "voice",
    "door",
    "render",
    "reflect",
    "tail-chasing",
    "paint",
    "ping-pong",
    "stack",
    "racing",
    "lottery",
    "intertwine",
    "collide",
    "electric-current",
}
```

Keep these official backend keys:

```python
{
    "rainbow",
    "rainbow-morph",
    "static",
    "breathing",
    "meteor",
    "runway",
    "twinkle",
    "color-cycle",
    "cover-cycle",
    "wave",
    "meteor-shower",
    "disco",
    "blow-up",
    "heartbeat",
    "warning",
    "ocean",
    "ripple",
    "echo",
}
```

Do not keep `kaleidoscope` in the public backend set for this phase because it is not in the confirmed wireless GUI list.

Update `TLV2_EFFECT_ALIASES` so it only contains official aliases:

```python
TLV2_EFFECT_ALIASES = {
    "gradient-rainbow": "rainbow-morph",
    "gradient_rainbow": "rainbow-morph",
    "rainbow_morph": "rainbow-morph",
    "colorcycle": "color-cycle",
    "meteor_shower": "meteor-shower",
    "blowup": "blow-up",
    "blow_up": "blow-up",
    "starry": "twinkle",
    "star": "twinkle",
    "twinkle": "twinkle",
}
```

Set `_GENERATED_EXTRA_TLV2_EFFECTS` to only generated official effects:

```python
_GENERATED_EXTRA_TLV2_EFFECTS = {
    "cover-cycle",
    "disco",
    "blow-up",
    "heartbeat",
    "warning",
    "ocean",
    "echo",
}
```

- [ ] **Step 4: Update old backend tests to official-only expectations**

In `tests/test_lianli_wireless.py`, replace any test list that includes removed effects with this official list:

```python
official_effects = (
    "rainbow",
    "rainbow-morph",
    "static",
    "breathing",
    "meteor",
    "runway",
    "twinkle",
    "color-cycle",
    "cover-cycle",
    "wave",
    "meteor-shower",
    "disco",
    "blow-up",
    "heartbeat",
    "warning",
    "ocean",
    "ripple",
    "echo",
)
```

Delete or rewrite tests whose only purpose is to prove removed effects such as `door`, `racing`, `lottery`, or `electric-current` are public color-aware effects. Preserve the existing runway and heartbeat tests.

- [ ] **Step 5: Run the backend LIAN LI tests**

Run:

```powershell
python -m pytest tests/test_lianli_wireless.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add usb9_lcd/lianli/wireless.py tests/test_lianli_wireless.py
git commit -m "Restrict LIAN LI TLV2 effects to official wireless list"
```

## Task 3: Drive The LIAN LI GUI From The Official Catalog

**Files:**
- Modify: `usb9_lcd/gui/lianli_wireless_page.py`
- Modify: `tests/test_gui_import.py`

- [ ] **Step 1: Write failing GUI catalog tests**

Update `tests/test_gui_import.py::test_lianli_wireless_page_only_exposes_official_lconnect_effects` so it imports the catalog:

```python
def test_lianli_wireless_page_only_exposes_official_lconnect_effects():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.lianli.effects import OFFICIAL_LIANLI_WIRELESS_EFFECT_OPTIONS

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage()

    available = tuple(
        (page.lianli_effect_combo.itemText(index), str(page.lianli_effect_combo.itemData(index)))
        for index in range(page.lianli_effect_combo.count())
    )

    assert available == OFFICIAL_LIANLI_WIRELESS_EFFECT_OPTIONS

    page.close()
    app.quit()
```

Replace `test_lianli_wireless_page_sends_remaining_effects_as_tlv2_frames` with this stale-effect rejection test:

```python
def test_lianli_wireless_page_rejects_removed_generated_effects():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo

    class FakeLianLiBackend:
        def send_tlv2_effect(self, *args, **kwargs):
            raise AssertionError("removed effects must not reach the backend")

    target = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=FakeLianLiBackend)

    for effect in ("staggered", "tide", "mixing", "door", "racing", "lottery", "collide"):
        with pytest.raises(ValueError, match="unsupported official LIAN LI wireless effect"):
            page._send_lianli_effect_with_backend(FakeLianLiBackend(), target, effect)

    page.close()
    app.quit()
```

Add a slot-count test:

```python
def test_lianli_wireless_page_swatches_match_official_slot_counts():
    from PySide6.QtWidgets import QApplication, QPushButton

    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.lianli.effects import OFFICIAL_LIANLI_WIRELESS_EFFECTS

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage()
    page.show()
    app.processEvents()

    for effect in OFFICIAL_LIANLI_WIRELESS_EFFECTS:
        index = page.lianli_effect_combo.findData(effect.key)
        assert index >= 0, effect.key
        page.lianli_effect_combo.setCurrentIndex(index)
        page._update_lianli_effect_fields()

        primary_visible = page.lianli_color_button.isVisible()
        accent_visible = page.lianli_accent_color_button.isVisible()
        palette_buttons = [
            button for button in page.lianli_rotation_colors.findChildren(QPushButton)
            if button.isVisible()
        ]
        visible_slots = int(primary_visible) + int(accent_visible) + len(palette_buttons)
        assert visible_slots == effect.color_slots, effect.key

    page.close()
    app.quit()
```

- [ ] **Step 2: Run GUI tests and verify at least one fails**

Run:

```powershell
python -m pytest tests/test_gui_import.py::test_lianli_wireless_page_only_exposes_official_lconnect_effects tests/test_gui_import.py::test_lianli_wireless_page_rejects_removed_generated_effects tests/test_gui_import.py::test_lianli_wireless_page_swatches_match_official_slot_counts -q
```

Expected: FAIL because `lianli_wireless_page.py` still keeps its own option tuple/native effect set and removed effects can still be sent.

- [ ] **Step 3: Import and use the catalog in the GUI**

In `usb9_lcd/gui/lianli_wireless_page.py`, extend the imports:

```python
from usb9_lcd.lianli.effects import (
    OFFICIAL_LIANLI_WIRELESS_EFFECT_OPTIONS,
    lianli_wireless_effect,
)
```

Replace the local `LIANLI_OFFICIAL_EFFECT_OPTIONS = (...)` block with:

```python
LIANLI_OFFICIAL_EFFECT_OPTIONS = OFFICIAL_LIANLI_WIRELESS_EFFECT_OPTIONS
```

Replace `_lianli_tlv2_effect_name()` with:

```python
def _lianli_tlv2_effect_name(self, effect: str) -> str:
    return lianli_wireless_effect(effect).backend_key
```

- [ ] **Step 4: Make the sender catalog-driven**

In `_send_lianli_effect_with_backend()`, replace the local `native_effects = {...}` block and `if effect in native_effects:` branch header with this structure:

```python
        try:
            effect_info = lianli_wireless_effect(effect)
        except ValueError as error:
            raise ValueError(str(error)) from error

        if effect_info.backend_key in {"static", "off"}:
            raise ValueError(f"unexpected catalog dispatch for {effect_info.backend_key}")

        primary_color = self._hex_to_rgb(self.lianli_static_color)
        accent_color = self._hex_to_rgb(self.lianli_accent_color)
        palette = [self._hex_to_rgb(color) for color in self._rotation_colors()[: max(1, effect_info.color_slots)]]

        effect_name = effect_info.backend_key
        capability = tlv2_effect_capability(effect_name)
```

Keep the existing static/off handling above this block. Keep the current `tlv2_color_effect_index()` call and kwargs building, but the available effect list now comes from `lianli_wireless_effect()`.

- [ ] **Step 5: Make control visibility catalog-driven**

In `_update_lianli_effect_fields()`, replace the current if/elif capability logic with:

```python
        try:
            effect_info = lianli_wireless_effect(effect)
        except ValueError:
            effect_info = lianli_wireless_effect("off")

        has_speed = effect_info.uses_speed
        has_brightness = effect_info.uses_brightness
        has_color = effect_info.color_mode in {"primary", "primary_accent"}
        has_accent = effect_info.color_mode == "primary_accent" and effect_info.color_slots >= 2
        has_rotation = effect_info.color_mode == "palette"
        has_direction = effect_info.uses_direction
        has_hold = False
```

Do not restore `rotate` or `overlap-cycle` in this page because they are not official wireless L-Connect W/W* effects.

- [ ] **Step 6: Make palette swatch count use the catalog**

Replace `_lianli_rotation_color_slot_count()` with:

```python
def _lianli_rotation_color_slot_count(self) -> int:
    try:
        effect = lianli_wireless_effect(str(self.lianli_effect_combo.currentData()))
    except ValueError:
        return 0
    if effect.color_mode != "palette":
        return 0
    return max(1, min(len(self._rotation_colors()), int(effect.color_slots)))
```

- [ ] **Step 7: Run focused GUI tests**

Run:

```powershell
python -m pytest tests/test_gui_import.py::test_lianli_wireless_page_only_exposes_official_lconnect_effects tests/test_gui_import.py::test_lianli_wireless_page_rejects_removed_generated_effects tests/test_gui_import.py::test_lianli_wireless_page_swatches_match_official_slot_counts tests/test_gui_import.py::test_lianli_wireless_page_sends_palette_and_accent_from_capabilities tests/test_gui_import.py::test_lianli_wireless_page_edits_palette_as_color_swatches -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```powershell
git add usb9_lcd/gui/lianli_wireless_page.py tests/test_gui_import.py
git commit -m "Drive LIAN LI GUI from official effect catalog"
```

## Task 4: Normalize Settings And Scene-Center LIAN LI Effects

**Files:**
- Modify: `usb9_lcd/gui/settings.py`
- Modify: `usb9_lcd/gui/scenes.py`
- Modify: `tests/test_settings.py`
- Modify: `tests/test_scene_center.py`

- [ ] **Step 1: Write stale settings migration test**

Add this test to `tests/test_settings.py`:

```python
def test_settings_migrates_removed_lianli_wireless_effect_to_off(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"lianli_wireless": {"effect": "staggered"}}),
        encoding="utf-8",
    )

    loaded = load_settings(settings_path)

    assert loaded.lianli_wireless.effect == "off"
```

- [ ] **Step 2: Write stale scene normalization test**

Add this test to `tests/test_scene_center.py`:

```python
def test_scene_payload_migrates_removed_lianli_effect_to_off():
    scene = normalize_scene_payload(
        {
            "name": "Legacy",
            "lianli_lighting": {"mode": "effect", "effect": "staggered"},
        },
        key="legacy",
    )

    assert scene.lianli_lighting.mode == "off"
    assert scene.lianli_lighting.payload == {}
```

If `normalize_scene_payload` is not imported at the top of `tests/test_scene_center.py`, add it to the existing import from `usb9_lcd.gui.scenes`.

- [ ] **Step 3: Run migration tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_settings.py::test_settings_migrates_removed_lianli_wireless_effect_to_off tests/test_scene_center.py::test_scene_payload_migrates_removed_lianli_effect_to_off -q
```

Expected: FAIL because settings and scenes currently preserve raw effect strings.

- [ ] **Step 4: Normalize settings on load**

In `usb9_lcd/gui/settings.py`, import:

```python
from usb9_lcd.lianli.effects import lianli_wireless_effect
```

Add this helper near `_lianli_wireless_from_dict()`:

```python
def _normalize_lianli_wireless_effect(value: Any, default: str = "off") -> str:
    try:
        return lianli_wireless_effect(str(value)).key
    except ValueError:
        return default
```

In `_lianli_wireless_from_dict()`, replace:

```python
effect=str(value.get("effect", defaults.effect)),
```

with:

```python
effect=_normalize_lianli_wireless_effect(value.get("effect", defaults.effect), defaults.effect),
```

- [ ] **Step 5: Normalize scene payloads**

In `usb9_lcd/gui/scenes.py`, import:

```python
from usb9_lcd.lianli.effects import lianli_wireless_effect
```

Add this helper above `normalize_scene_payload()`:

```python
def _normalize_lianli_lighting_section(section: SceneSection) -> SceneSection:
    if section.mode != "effect":
        return section
    try:
        effect = lianli_wireless_effect(str(section.payload.get("effect", "off")))
    except ValueError:
        return SceneSection(mode="off")
    payload = dict(section.payload)
    payload["effect"] = effect.key
    return SceneSection(mode=section.mode, payload=payload)
```

In `normalize_scene_payload()`, build the LIAN LI lighting section through the helper:

```python
lianli_lighting=_normalize_lianli_lighting_section(_section(payload, "lianli_lighting")),
```

- [ ] **Step 6: Run migration tests**

Run:

```powershell
python -m pytest tests/test_settings.py::test_settings_migrates_removed_lianli_wireless_effect_to_off tests/test_scene_center.py::test_scene_payload_migrates_removed_lianli_effect_to_off -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add usb9_lcd/gui/settings.py usb9_lcd/gui/scenes.py tests/test_settings.py tests/test_scene_center.py
git commit -m "Normalize LIAN LI effects in settings and scenes"
```

## Task 5: Verify Color Inputs Affect Runway, Heartbeat, And Palette Effects

**Files:**
- Modify: `tests/test_lianli_wireless.py`
- Modify: `usb9_lcd/lianli/wireless.py` only if these tests reveal a regression

- [ ] **Step 1: Strengthen color-sensitivity tests**

Add this test to `tests/test_lianli_wireless.py`:

```python
def test_official_lianli_effect_visible_color_slots_change_output_or_index():
    from usb9_lcd.lianli.effects import OFFICIAL_LIANLI_WIRELESS_EFFECTS

    base_primary = (255, 0, 0)
    base_accent = (0, 255, 0)
    alternate = (0, 0, 255)
    palette = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))

    for effect in OFFICIAL_LIANLI_WIRELESS_EFFECTS:
        if effect.backend_key == "off" or effect.color_slots == 0:
            continue
        raw_a, _ = generate_tlv2_effect_rgb_frames(
            effect.backend_key,
            led_count=26,
            color=base_primary,
            accent_color=base_accent,
            palette=palette,
            brightness=100,
            direction="left",
        )
        index_a = tlv2_color_effect_index(
            effect.backend_key,
            base_primary,
            accent_color=base_accent,
            palette=palette,
            direction="left",
        )

        changed_palette = (alternate, *palette[1:])
        changed_primary = alternate if effect.color_mode in {"primary", "primary_accent"} else base_primary
        changed_accent = alternate if effect.color_mode == "primary_accent" else base_accent
        raw_b, _ = generate_tlv2_effect_rgb_frames(
            effect.backend_key,
            led_count=26,
            color=changed_primary,
            accent_color=changed_accent,
            palette=changed_palette,
            brightness=100,
            direction="left",
        )
        index_b = tlv2_color_effect_index(
            effect.backend_key,
            changed_primary,
            accent_color=changed_accent,
            palette=changed_palette,
            direction="left",
        )

        assert raw_a != raw_b or index_a != index_b, effect.backend_key
```

- [ ] **Step 2: Run color-sensitivity tests and verify the result**

Run:

```powershell
python -m pytest tests/test_lianli_wireless.py::test_tlv2_runway_uses_accent_color_for_second_color_slot tests/test_lianli_wireless.py::test_tlv2_runway_official_template_uses_accent_color_for_second_color_slot tests/test_lianli_wireless.py::test_tlv2_heartbeat_uses_two_separate_color_pulses_with_dark_rest tests/test_lianli_wireless.py::test_official_lianli_effect_visible_color_slots_change_output_or_index -q
```

Expected: PASS. If it fails for a specific official effect, fix that effect in `usb9_lcd/lianli/wireless.py` by making `_effective_tlv2_color_inputs()`, `_official_tlv2_palette_basis()`, or the generated frame branch consume the catalog-visible color slot.

- [ ] **Step 3: If needed, patch the failing generated branch**

Use this pattern for any generated effect that ignores a visible accent color:

```python
if effect_key == "heartbeat":
    # Both official GUI swatches are visible for this effect: primary pulse and accent pulse.
    ...
    if primary_level >= accent_level:
        frame.append(_scale_rgb(primary, primary_level))
    else:
        frame.append(_scale_rgb(accent, accent_level))
    return frame
```

Use this pattern for any palette effect that ignores visible palette order:

```python
def palette(index: int) -> tuple[int, int, int]:
    return colors[index % len(colors)]
```

- [ ] **Step 4: Run backend tests**

Run:

```powershell
python -m pytest tests/test_lianli_wireless.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add usb9_lcd/lianli/wireless.py tests/test_lianli_wireless.py
git commit -m "Verify LIAN LI official color slots affect packets"
```

## Task 6: Full Regression, Build Smoke, And Push

**Files:**
- No source edits expected unless verification finds failures.

- [ ] **Step 1: Run focused GUI and backend regressions**

Run:

```powershell
python -m pytest tests/test_lianli_wireless.py tests/test_gui_import.py::test_lianli_wireless_page_only_exposes_official_lconnect_effects tests/test_gui_import.py::test_lianli_wireless_page_swatches_match_official_slot_counts tests/test_gui_import.py::test_lianli_wireless_page_sends_palette_and_accent_from_capabilities tests/test_settings.py tests/test_scene_center.py -q
```

Expected: PASS.

- [ ] **Step 2: Run the full suite**

Run:

```powershell
python -m pytest -q
```

Expected: PASS with the same expected skips/warnings profile as the current branch.

- [ ] **Step 3: Rebuild the Windows executable**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File packaging/windows/build-exe.ps1 -Clean -SkipInstall
```

Expected: command exits 0 and writes `dist/LumenHub/LumenHub.exe`.

- [ ] **Step 4: Commit any verification-only fixes**

If Step 1, 2, or 3 required small fixes, commit them:

```powershell
git add usb9_lcd tests packaging docs
git commit -m "Stabilize LIAN LI official effect alignment"
```

If there were no fixes, do not create an empty commit.

- [ ] **Step 5: Push**

Run:

```powershell
git push origin codex/scene-center
```

Expected: push succeeds.

## Self-Review Notes

- Spec coverage: The plan covers the official effect list, GUI catalog source, backend rejection of removed effects, fixed color-slot counts, direction visibility, stale settings migration, scene-center normalization, runway/heartbeat color sensitivity, full tests, Windows build, and push.
- Placeholder scan: The plan contains no unfinished placeholder markers and no open-ended "add tests" step without concrete test code.
- Type consistency: Catalog helper names are `lianli_wireless_effect()` and `normalize_lianli_wireless_effect()`. GUI and settings steps consistently use those names. Backend public tests rely on existing `TLV2_EFFECT_SPECS`, `TLV2_EFFECT_CAPABILITIES`, `LianLiWirelessError`, `tlv2_effect_capability()`, and `generate_tlv2_effect_rgb_frames()`.
