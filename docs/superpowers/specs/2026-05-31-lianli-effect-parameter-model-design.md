# LIAN LI Effect Parameter Model Design

Date: 2026-05-31

## Goal

Improve the LIAN LI wireless lighting GUI so effect controls match what each effect can actually use, and make direction and multi-color parameters change the generated TLV2 RGB frames.

This phase targets practical correctness for the current cross-platform implementation. It does not try to reproduce every L-Connect 3 frame byte-for-byte for every effect; effects that still look different from official output can be calibrated later with focused captures.

## Current Problems

The GUI already exposes controls for direction, primary color, accent color, and color queue, but those controls are not driven by a single capability model. Some effects show parameters that are ignored, while other effects can use multiple colors or direction but do not expose that clearly.

The backend also has mixed behavior. Some TLV2 effects use official captured templates and can be color remapped, some are generated procedurally, and some generated effects only partially use `palette` or `direction`.

## Scope

Implement a unified effect capability table for LIAN LI TLV2 lighting effects.

Each effect will declare whether it uses:

- `primary_color`
- `accent_color`
- `palette`
- `direction`
- `speed`
- `brightness`

The GUI will use this table to show or hide controls, and the sender path will use the same table to pass only meaningful parameters to the backend.

The backend will strengthen generated TLV2 effects so direction and palette changes produce different RGB frames where the capability table says those parameters are supported.

## Out Of Scope

This design does not include a new capture session, L-Connect 3 automation, or frame-perfect official matching for every parameter combination.

It also does not change fan control, OpenRGB, LCD screen rendering, or non-LIAN-LI lighting pages.

## Effect Capability Model

Add a small immutable data model near the existing TLV2 effect metadata:

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

The table should include all currently exposed TLV2 effects and aliases should resolve through the same normalization path as `TLV2_EFFECT_SPECS`.

Initial categorization:

- `off`: no speed, no brightness, no color controls.
- `static`: primary color and brightness.
- `breathing`, `runway`, `meteor`, `wave`, `electric-current`: primary color, direction where motion exists, speed, brightness.
- `twinkle` / `starry`: primary color, accent color, speed, brightness.
- `rainbow`, `rainbow-morph`, `kaleidoscope`: direction, speed, brightness.
- `color-cycle`, `ripple`, `meteor-shower`, `staggered`, `tide`, `mixing`, `voice`, `door`, `render`, `reflect`, `tail-chasing`, `paint`, `ping-pong`, `stack`, `cover-cycle`, `racing`, `lottery`, `intertwine`, `collide`: palette, direction where motion exists, speed, brightness.

The exact direction flags should be conservative. If an effect has no meaningful spatial movement after implementation, it should not claim direction support.

## GUI Behavior

`LianLiWirelessPage._update_lianli_effect_fields()` will read the capability table instead of maintaining local hard-coded sets.

Control visibility:

- Primary color button visible only when `uses_primary_color`.
- Accent color button visible only when `uses_accent_color`.
- Color queue input visible only when `uses_palette`.
- Direction combo visible only when `uses_direction`.
- Speed slider visible only when `uses_speed`.
- Brightness slider visible only when `uses_brightness`.

The GUI sender path will resolve the normalized effect key and capability before calling `send_tlv2_effect()`.

For color-sensitive TLV2 dynamic effects, `tlv2_color_effect_index()` should include every color parameter the effect uses. For example:

- primary-only effects hash the primary color.
- palette effects hash the palette colors.
- primary plus accent effects hash both colors.

This prevents receiver-side caching when the user changes only the color queue or accent color.

## Backend Behavior

The procedural generator should ensure supported parameters actually affect frames.

Direction:

- Existing official-template reversal should remain.
- Procedural motion effects should use `_direction_step()` consistently.
- Direction tests should confirm left and right output differ for direction-capable effects.

Palette:

- Palette-capable effects should derive visible colors from the resolved palette instead of falling back to a single primary color.
- The resolved palette should preserve user order and apply brightness scaling.
- Empty or invalid palette input should fall back to a stable default palette.

Speed:

- TLV2 packet interval already carries timing metadata. GUI speed should map to interval or frame pacing where currently supported. If full speed remapping is too risky for this phase, keep existing interval behavior but preserve the parameter model so later speed calibration has a clear hook.

Brightness:

- Brightness remains a color scaling input to frame generation.

## Testing

Add focused tests for:

- Capability table contains all exposed GUI TLV2 effects.
- GUI shows primary color, accent color, palette, direction, speed, and brightness controls according to capability data.
- GUI sends primary color, accent color, palette, direction, brightness, and color-sensitive effect index according to capability data.
- Direction-capable generated effects produce different RGB output for left vs right.
- Palette-capable generated effects produce different RGB output when the palette changes.
- Existing static/off behavior remains unchanged.
- Existing official decoded hash tests remain valid for the default captured parameter set.

## Success Criteria

The GUI should no longer expose controls that are ignored for the selected LIAN LI effect.

For effects that expose direction or multiple colors, changing those controls should alter the sent TLV2 payload or effect index.

Existing working paths, especially `off`, `static`, and primary-color dynamic effects such as `breathing`, must keep working.
