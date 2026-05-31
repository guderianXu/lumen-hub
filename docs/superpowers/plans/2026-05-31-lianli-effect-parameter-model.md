# LIAN LI Effect Parameter Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LIAN LI wireless GUI controls match each effect's real parameters, and make direction and multi-color inputs change the generated TLV2 frames.

**Architecture:** Add a shared TLV2 effect capability table in `usb9_lcd/lianli/wireless.py`, export it through `usb9_lcd/lianli/__init__.py`, and consume it from `usb9_lcd/gui/lianli_wireless_page.py`. Tests drive the table, GUI visibility, GUI backend arguments, color-sensitive effect indexes, direction output, and palette output.

**Tech Stack:** Python 3.11, PySide6, pytest, existing LIAN LI wireless TLV2 frame generator.

---

### Task 1: Add Capability Model Tests

**Files:**
- Modify: `tests/test_lianli_wireless.py`

- [ ] **Step 1: Write failing tests for effect capabilities**

Add imports near the existing LIAN LI wireless imports:

```python
from usb9_lcd.lianli.wireless import (
    TLV2_EFFECT_CAPABILITIES,
    TLV2_EFFECT_SPECS,
    tlv2_effect_capability,
)
```

Add these tests after `test_tlv2_color_effect_index_changes_when_primary_color_changes`:

```python
def test_tlv2_effect_capabilities_cover_all_specs_and_aliases():
    missing = set(TLV2_EFFECT_SPECS) - set(TLV2_EFFECT_CAPABILITIES)
    assert missing == set()

    assert tlv2_effect_capability("starry").key == "twinkle"
    assert tlv2_effect_capability("gradient-rainbow").key == "rainbow-morph"


def test_tlv2_effect_capabilities_describe_user_controls():
    assert tlv2_effect_capability("static").uses_primary_color is True
    assert tlv2_effect_capability("static").uses_palette is False
    assert tlv2_effect_capability("static").uses_speed is False

    assert tlv2_effect_capability("breathing").uses_primary_color is True
    assert tlv2_effect_capability("breathing").uses_palette is False
    assert tlv2_effect_capability("breathing").uses_direction is False

    assert tlv2_effect_capability("twinkle").uses_primary_color is True
    assert tlv2_effect_capability("twinkle").uses_accent_color is True
    assert tlv2_effect_capability("twinkle").uses_palette is False

    assert tlv2_effect_capability("rainbow").uses_primary_color is False
    assert tlv2_effect_capability("rainbow").uses_palette is False
    assert tlv2_effect_capability("rainbow").uses_direction is True

    assert tlv2_effect_capability("ripple").uses_palette is True
    assert tlv2_effect_capability("ripple").uses_direction is True
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_lianli_wireless.py::test_tlv2_effect_capabilities_cover_all_specs_and_aliases tests/test_lianli_wireless.py::test_tlv2_effect_capabilities_describe_user_controls -q
```

Expected: fail with an import error or missing symbol for `TLV2_EFFECT_CAPABILITIES` / `tlv2_effect_capability`.

### Task 2: Implement Capability Model

**Files:**
- Modify: `usb9_lcd/lianli/wireless.py`
- Modify: `usb9_lcd/lianli/__init__.py`
- Test: `tests/test_lianli_wireless.py`

- [ ] **Step 1: Add the data model and table**

In `usb9_lcd/lianli/wireless.py`, add after `Tlv2EffectSpec`:

```python
@dataclass(frozen=True)
class Tlv2EffectCapability:
    key: str
    uses_primary_color: bool = False
    uses_accent_color: bool = False
    uses_palette: bool = False
    uses_direction: bool = False
    uses_speed: bool = True
    uses_brightness: bool = True
```

Add after `TLV2_EFFECT_ALIASES`:

```python
TLV2_EFFECT_CAPABILITIES: dict[str, Tlv2EffectCapability] = {
    "rainbow": Tlv2EffectCapability("rainbow", uses_direction=True),
    "rainbow-morph": Tlv2EffectCapability("rainbow-morph", uses_direction=True),
    "static": Tlv2EffectCapability("static", uses_primary_color=True, uses_speed=False),
    "breathing": Tlv2EffectCapability("breathing", uses_primary_color=True, uses_direction=False),
    "runway": Tlv2EffectCapability("runway", uses_primary_color=True, uses_direction=True),
    "meteor": Tlv2EffectCapability("meteor", uses_primary_color=True, uses_direction=True),
    "color-cycle": Tlv2EffectCapability("color-cycle", uses_palette=True, uses_direction=False),
    "staggered": Tlv2EffectCapability("staggered", uses_palette=True, uses_direction=True),
    "tide": Tlv2EffectCapability("tide", uses_palette=True, uses_direction=True),
    "mixing": Tlv2EffectCapability("mixing", uses_palette=True, uses_direction=True),
    "voice": Tlv2EffectCapability("voice", uses_palette=True, uses_direction=True),
    "door": Tlv2EffectCapability("door", uses_palette=True, uses_direction=True),
    "render": Tlv2EffectCapability("render", uses_palette=True, uses_direction=True),
    "ripple": Tlv2EffectCapability("ripple", uses_palette=True, uses_direction=True),
    "reflect": Tlv2EffectCapability("reflect", uses_palette=True, uses_direction=True),
    "tail-chasing": Tlv2EffectCapability("tail-chasing", uses_palette=True, uses_direction=True),
    "paint": Tlv2EffectCapability("paint", uses_palette=True, uses_direction=True),
    "ping-pong": Tlv2EffectCapability("ping-pong", uses_palette=True, uses_direction=True),
    "stack": Tlv2EffectCapability("stack", uses_palette=True, uses_direction=True),
    "cover-cycle": Tlv2EffectCapability("cover-cycle", uses_palette=True, uses_direction=True),
    "wave": Tlv2EffectCapability("wave", uses_primary_color=True, uses_direction=True),
    "racing": Tlv2EffectCapability("racing", uses_palette=True, uses_direction=True),
    "lottery": Tlv2EffectCapability("lottery", uses_palette=True, uses_direction=True),
    "intertwine": Tlv2EffectCapability("intertwine", uses_palette=True, uses_direction=True),
    "meteor-shower": Tlv2EffectCapability("meteor-shower", uses_palette=True, uses_direction=True),
    "collide": Tlv2EffectCapability("collide", uses_palette=True, uses_direction=True),
    "electric-current": Tlv2EffectCapability("electric-current", uses_primary_color=True, uses_direction=True),
    "kaleidoscope": Tlv2EffectCapability("kaleidoscope", uses_direction=True),
    "twinkle": Tlv2EffectCapability("twinkle", uses_primary_color=True, uses_accent_color=True),
}
```

Add helper:

```python
def tlv2_effect_capability(effect: str) -> Tlv2EffectCapability:
    effect_key = _normalize_tlv2_effect_key(effect)
    try:
        return TLV2_EFFECT_CAPABILITIES[effect_key]
    except KeyError as error:
        raise LianLiWirelessError(f"unsupported TLV2 effect capability: {effect}") from error
```

- [ ] **Step 2: Export the model**

In `usb9_lcd/lianli/__init__.py`, import and add to `__all__`:

```python
TLV2_EFFECT_CAPABILITIES,
Tlv2EffectCapability,
tlv2_effect_capability,
```

- [ ] **Step 3: Run capability tests**

Run:

```powershell
python -m pytest tests/test_lianli_wireless.py::test_tlv2_effect_capabilities_cover_all_specs_and_aliases tests/test_lianli_wireless.py::test_tlv2_effect_capabilities_describe_user_controls -q
```

Expected: `2 passed`.

### Task 3: Add Color Signature Tests

**Files:**
- Modify: `tests/test_lianli_wireless.py`

- [ ] **Step 1: Extend color index tests**

Replace `test_tlv2_color_effect_index_changes_when_primary_color_changes` with:

```python
def test_tlv2_color_effect_index_changes_when_used_colors_change():
    assert tlv2_color_effect_index("breathing", (0, 0, 255)) == 0x0209539F
    assert tlv2_color_effect_index("breathing", (0, 255, 0)) == 0x02095360
    assert tlv2_color_effect_index("breathing", (255, 0, 0)) == 0x020953FF

    red_blue = tlv2_color_effect_index(
        "twinkle",
        (255, 0, 0),
        accent_color=(0, 0, 255),
    )
    red_green = tlv2_color_effect_index(
        "twinkle",
        (255, 0, 0),
        accent_color=(0, 255, 0),
    )
    assert red_blue != red_green

    palette_a = tlv2_color_effect_index(
        "ripple",
        (255, 0, 0),
        palette=((255, 0, 0), (0, 255, 0), (0, 0, 255)),
    )
    palette_b = tlv2_color_effect_index(
        "ripple",
        (255, 0, 0),
        palette=((0, 0, 255), (0, 255, 0), (255, 0, 0)),
    )
    assert palette_a != palette_b
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```powershell
python -m pytest tests/test_lianli_wireless.py::test_tlv2_color_effect_index_changes_when_used_colors_change -q
```

Expected: fail because `tlv2_color_effect_index()` does not accept `accent_color` or `palette`.

### Task 4: Implement Color-Sensitive Effect Indexes

**Files:**
- Modify: `usb9_lcd/lianli/wireless.py`
- Test: `tests/test_lianli_wireless.py`

- [ ] **Step 1: Update `tlv2_color_effect_index()`**

Replace it with:

```python
def tlv2_color_effect_index(
    effect: str,
    color: tuple[int, int, int],
    *,
    accent_color: tuple[int, int, int] = (255, 255, 255),
    palette: Iterable[tuple[int, int, int]] | None = None,
) -> int:
    effect_key = _normalize_tlv2_effect_key(effect)
    base = TLV2_EFFECT_SPECS[effect_key].default_effect_index
    capability = tlv2_effect_capability(effect_key)

    signature = bytearray()
    if capability.uses_primary_color:
        signature.extend(_rgb_bytes(color))
    if capability.uses_accent_color:
        signature.extend(_rgb_bytes(accent_color))
    if capability.uses_palette:
        for palette_color in _resolve_tlv2_palette(
            palette,
            primary=_rgb_tuple(color),
            accent=_rgb_tuple(accent_color),
            brightness_scale=1.0,
        ):
            signature.extend(_rgb_bytes(palette_color))
    if not signature:
        signature.extend(effect_key.encode("ascii"))

    return (base & 0xFFFFFF00) | (zlib.crc32(bytes(signature)) & 0xFF)
```

- [ ] **Step 2: Run color index test**

Run:

```powershell
python -m pytest tests/test_lianli_wireless.py::test_tlv2_color_effect_index_changes_when_used_colors_change -q
```

Expected: `1 passed`.

### Task 5: Add GUI Capability Tests

**Files:**
- Modify: `tests/test_gui_import.py`

- [ ] **Step 1: Add test helper**

Add this helper near the LIAN LI wireless GUI tests:

```python
def _set_lianli_effect(page, effect: str) -> None:
    index = page.lianli_effect_combo.findData(effect)
    assert index >= 0
    page.lianli_effect_combo.setCurrentIndex(index)
    page._update_lianli_effect_fields()
```

- [ ] **Step 2: Add GUI visibility test**

Add:

```python
def test_lianli_wireless_page_uses_effect_capabilities_for_parameter_visibility():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage()

    _set_lianli_effect(page, "breathing")
    assert page.lianli_color_button.isVisible() is True
    assert page.lianli_accent_color_button.isVisible() is False
    assert page.lianli_rotation_colors.isVisible() is False
    assert page.lianli_direction_combo.isVisible() is False

    _set_lianli_effect(page, "twinkle")
    assert page.lianli_color_button.isVisible() is True
    assert page.lianli_accent_color_button.isVisible() is True
    assert page.lianli_rotation_colors.isVisible() is False

    _set_lianli_effect(page, "ripple")
    assert page.lianli_color_button.isVisible() is False
    assert page.lianli_accent_color_button.isVisible() is False
    assert page.lianli_rotation_colors.isVisible() is True
    assert page.lianli_direction_combo.isVisible() is True

    _set_lianli_effect(page, "rainbow")
    assert page.lianli_color_button.isVisible() is False
    assert page.lianli_rotation_colors.isVisible() is False
    assert page.lianli_direction_combo.isVisible() is True

    page.close()
    app.quit()
```

- [ ] **Step 3: Add GUI backend argument test**

Add:

```python
def test_lianli_wireless_page_sends_palette_and_accent_from_capabilities():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo

    class FakeLianLiBackend:
        def __init__(self):
            self.calls: list[tuple[str, dict[str, object]]] = []

        def send_tlv2_effect(self, target, effect, *, led_count, brightness, **kwargs):
            self.calls.append((effect, kwargs))
            return 20

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
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: backend)
    page.lianli_direct_led_count.setValue(26)
    page.set_lianli_static_color("#112233")
    page.lianli_rotation_colors.setText("#010203,#040506,#070809")
    page.lianli_direction_combo.setCurrentIndex(page.lianli_direction_combo.findData("right"))

    page._send_lianli_effect_with_backend(backend, target, "ripple")
    assert backend.calls[-1][0] == "ripple"
    assert backend.calls[-1][1]["palette"] == [(1, 2, 3), (4, 5, 6), (7, 8, 9)]
    assert backend.calls[-1][1]["direction"] == "right"

    page._send_lianli_effect_with_backend(backend, target, "starry")
    assert backend.calls[-1][0] == "twinkle"
    assert backend.calls[-1][1]["color"] == (17, 34, 51)
    assert backend.calls[-1][1]["accent_color"] == (255, 255, 255)

    page.close()
    app.quit()
```

- [ ] **Step 4: Run GUI tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_gui_import.py::test_lianli_wireless_page_uses_effect_capabilities_for_parameter_visibility tests/test_gui_import.py::test_lianli_wireless_page_sends_palette_and_accent_from_capabilities -q
```

Expected: fail because GUI still uses local hard-coded capability sets and sends static argument combinations.

### Task 6: Implement GUI Capability Consumption

**Files:**
- Modify: `usb9_lcd/gui/lianli_wireless_page.py`
- Test: `tests/test_gui_import.py`

- [ ] **Step 1: Import capability helper**

Add to the LIAN LI wireless imports:

```python
tlv2_effect_capability,
```

- [ ] **Step 2: Add effect normalization helper**

Add method in `LianLiWirelessPage` near `_send_lianli_effect_with_backend()`:

```python
    def _lianli_tlv2_effect_name(self, effect: str) -> str:
        return "twinkle" if effect == "starry" else effect
```

- [ ] **Step 3: Update `_send_lianli_effect_with_backend()`**

In the native TLV2 branch, replace the current argument construction with:

```python
            primary_color = self._hex_to_rgb(self.lianli_static_color)
            accent_color = (255, 255, 255)
            palette = [self._hex_to_rgb(color) for color in self._rotation_colors()]
            effect_name = self._lianli_tlv2_effect_name(effect)
            capability = tlv2_effect_capability(effect_name)
            effect_index = tlv2_color_effect_index(
                effect_name,
                primary_color,
                accent_color=accent_color,
                palette=palette,
            )

            kwargs: dict[str, object] = {
                "brightness": self.lianli_brightness_slider.value(),
                "led_count": led_count,
                "effect_index": effect_index,
            }
            if capability.uses_primary_color:
                kwargs["color"] = primary_color
            if capability.uses_accent_color:
                kwargs["accent_color"] = accent_color
            if capability.uses_palette:
                kwargs["palette"] = palette
            if capability.uses_direction:
                kwargs["direction"] = str(self.lianli_direction_combo.currentData() or "left")

            return backend.send_tlv2_effect(target, effect_name, **kwargs)
```

- [ ] **Step 4: Update `_update_lianli_effect_fields()`**

Replace local `color_effects`, `direction_effects`, and `rotation_effects` sets with:

```python
        if effect in {"off"}:
            has_speed = False
            has_brightness = False
            has_color = False
            has_accent = False
            has_rotation = False
            has_direction = False
        elif effect in {"static"}:
            has_speed = False
            has_brightness = True
            has_color = True
            has_accent = False
            has_rotation = False
            has_direction = False
        elif effect in {"rotate", "overlap-cycle"}:
            has_speed = True
            has_brightness = True
            has_color = False
            has_accent = False
            has_rotation = True
            has_direction = False
        else:
            capability = tlv2_effect_capability(self._lianli_tlv2_effect_name(effect))
            has_speed = capability.uses_speed
            has_brightness = capability.uses_brightness
            has_color = capability.uses_primary_color
            has_accent = capability.uses_accent_color
            has_rotation = capability.uses_palette
            has_direction = capability.uses_direction
```

- [ ] **Step 5: Run GUI tests**

Run:

```powershell
python -m pytest tests/test_gui_import.py::test_lianli_wireless_page_uses_effect_capabilities_for_parameter_visibility tests/test_gui_import.py::test_lianli_wireless_page_sends_palette_and_accent_from_capabilities tests/test_gui_import.py::test_lianli_wireless_page_sends_dynamic_effects_as_tlv2_frames tests/test_gui_import.py::test_lianli_wireless_page_sends_remaining_effects_as_tlv2_frames -q
```

Expected: all selected tests pass.

### Task 7: Add Direction and Palette Generator Tests

**Files:**
- Modify: `tests/test_lianli_wireless.py`

- [ ] **Step 1: Add helper for frame comparison**

Add near `_rgb_triplets` helpers:

```python
def _unique_frame_colors(raw: bytes) -> set[tuple[int, int, int]]:
    return set(_rgb_triplets(raw))
```

- [ ] **Step 2: Add direction tests**

Add:

```python
def test_tlv2_direction_capable_effects_change_when_direction_changes():
    effects = [
        effect
        for effect, capability in TLV2_EFFECT_CAPABILITIES.items()
        if capability.uses_direction
    ]
    assert effects

    for effect in effects:
        left_raw, _ = generate_tlv2_effect_rgb_frames(
            effect,
            led_count=26,
            color=(255, 45, 85),
            accent_color=(255, 255, 255),
            palette=((255, 45, 85), (31, 209, 255), (107, 255, 92), (255, 214, 10)),
            brightness=80,
            direction="left",
        )
        right_raw, _ = generate_tlv2_effect_rgb_frames(
            effect,
            led_count=26,
            color=(255, 45, 85),
            accent_color=(255, 255, 255),
            palette=((255, 45, 85), (31, 209, 255), (107, 255, 92), (255, 214, 10)),
            brightness=80,
            direction="right",
        )
        assert left_raw != right_raw, effect
```

- [ ] **Step 3: Add palette tests**

Add:

```python
def test_tlv2_palette_capable_effects_change_when_palette_changes():
    effects = [
        effect
        for effect, capability in TLV2_EFFECT_CAPABILITIES.items()
        if capability.uses_palette
    ]
    assert effects

    for effect in effects:
        first_raw, _ = generate_tlv2_effect_rgb_frames(
            effect,
            led_count=26,
            color=(255, 45, 85),
            accent_color=(255, 255, 255),
            palette=((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)),
            brightness=100,
            direction="left",
        )
        second_raw, _ = generate_tlv2_effect_rgb_frames(
            effect,
            led_count=26,
            color=(255, 45, 85),
            accent_color=(255, 255, 255),
            palette=((0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 0)),
            brightness=100,
            direction="left",
        )
        assert first_raw != second_raw, effect
        assert len(_unique_frame_colors(first_raw)) > 1
        assert len(_unique_frame_colors(second_raw)) > 1
```

- [ ] **Step 4: Run tests and verify failures**

Run:

```powershell
python -m pytest tests/test_lianli_wireless.py::test_tlv2_direction_capable_effects_change_when_direction_changes tests/test_lianli_wireless.py::test_tlv2_palette_capable_effects_change_when_palette_changes -q
```

Expected: at least one failure for an effect whose capability claims direction or palette support but generated frames do not change.

### Task 8: Strengthen Direction and Palette Generators

**Files:**
- Modify: `usb9_lcd/lianli/wireless.py`
- Test: `tests/test_lianli_wireless.py`

- [ ] **Step 1: Add reusable frame direction helper**

Add near `_reverse_rgb_frame_led_order()`:

```python
def _maybe_reverse_generated_frame(
    frame: list[tuple[int, int, int]],
    direction_step: int,
) -> list[tuple[int, int, int]]:
    if direction_step < 0:
        return list(reversed(frame))
    return frame
```

- [ ] **Step 2: Ensure extra generated effects receive palette and direction**

In `_extra_tlv2_effect_frame()`, preserve existing effect-specific logic but ensure every effect listed as palette-capable uses `colors` for visible LEDs. If a branch currently uses only `primary`, replace that color source with `colors[index % len(colors)]` or a blend between adjacent palette colors.

Use this pattern for moving effects:

```python
palette_color = colors[(frame_index // 4 + led_index) % len(colors)]
```

For effects where direction is not already used to calculate position, finish the branch with:

```python
return _maybe_reverse_generated_frame(frame, direction_step)
```

- [ ] **Step 3: Apply direction to generated non-extra effects**

For `color-cycle`, keep `uses_direction=False`.

For `wave`, `ripple`, `meteor-shower`, and `electric-current`, confirm the existing frame helpers either use `direction_step` internally or reverse the generated frame at the end. Use the smallest change that makes left and right raw bytes differ.

- [ ] **Step 4: Run direction and palette tests**

Run:

```powershell
python -m pytest tests/test_lianli_wireless.py::test_tlv2_direction_capable_effects_change_when_direction_changes tests/test_lianli_wireless.py::test_tlv2_palette_capable_effects_change_when_palette_changes -q
```

Expected: `2 passed`.

### Task 9: Run Focused Regression Suite

**Files:**
- Test only

- [ ] **Step 1: Run LIAN LI wireless tests around TLV2 generation**

Run:

```powershell
python -m pytest tests/test_lianli_wireless.py::test_tlv2_dynamic_effect_generators_do_not_collapse_to_static_rgb tests/test_lianli_wireless.py::test_tlv2_breathing_honors_primary_color_when_palette_is_unchanged tests/test_lianli_wireless.py::test_tlv2_effect_generators_match_official_lconnect_decoded_hashes tests/test_lianli_wireless.py::test_backend_builds_tlv2_dynamic_effect_rf_chunks tests/test_lianli_wireless.py::test_backend_send_tlv2_effect_uses_official_preamble_compression_and_repeats -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run GUI LIAN LI tests**

Run:

```powershell
python -m pytest tests/test_gui_import.py::test_lianli_wireless_page_sends_dynamic_effects_as_tlv2_frames tests/test_gui_import.py::test_lianli_wireless_page_sends_remaining_effects_as_tlv2_frames tests/test_gui_import.py::test_lianli_wireless_page_static_effect_uses_backend_default_color_slot tests/test_gui_import.py::test_lianli_wireless_page_uses_effect_capabilities_for_parameter_visibility tests/test_gui_import.py::test_lianli_wireless_page_sends_palette_and_accent_from_capabilities -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Compile changed modules**

Run:

```powershell
python -m py_compile usb9_lcd\lianli\wireless.py usb9_lcd\gui\lianli_wireless_page.py usb9_lcd\lianli\__init__.py
```

Expected: exit code `0` and no output.

### Task 10: Final Verification and Commit

**Files:**
- Modify: `usb9_lcd/lianli/wireless.py`
- Modify: `usb9_lcd/lianli/__init__.py`
- Modify: `usb9_lcd/gui/lianli_wireless_page.py`
- Modify: `tests/test_lianli_wireless.py`
- Modify: `tests/test_gui_import.py`

- [ ] **Step 1: Check diff hygiene**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only expected files modified.

- [ ] **Step 2: Run final focused test command**

Run:

```powershell
python -m pytest tests/test_lianli_wireless.py::test_tlv2_effect_capabilities_cover_all_specs_and_aliases tests/test_lianli_wireless.py::test_tlv2_effect_capabilities_describe_user_controls tests/test_lianli_wireless.py::test_tlv2_color_effect_index_changes_when_used_colors_change tests/test_lianli_wireless.py::test_tlv2_direction_capable_effects_change_when_direction_changes tests/test_lianli_wireless.py::test_tlv2_palette_capable_effects_change_when_palette_changes tests/test_gui_import.py::test_lianli_wireless_page_uses_effect_capabilities_for_parameter_visibility tests/test_gui_import.py::test_lianli_wireless_page_sends_palette_and_accent_from_capabilities tests/test_gui_import.py::test_lianli_wireless_page_sends_dynamic_effects_as_tlv2_frames tests/test_gui_import.py::test_lianli_wireless_page_sends_remaining_effects_as_tlv2_frames -q
```

Expected: all listed tests pass.

- [ ] **Step 3: Commit and push**

Run:

```powershell
git add usb9_lcd\lianli\wireless.py usb9_lcd\lianli\__init__.py usb9_lcd\gui\lianli_wireless_page.py tests\test_lianli_wireless.py tests\test_gui_import.py
git commit -m "Improve Lian Li effect parameter controls"
git push origin main
```

Expected: commit is created on `main` and pushed to `origin/main`.
