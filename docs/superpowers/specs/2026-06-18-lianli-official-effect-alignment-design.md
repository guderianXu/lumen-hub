# LIAN LI Official Effect Alignment Design

Date: 2026-06-18

## Goal

Make the LIAN LI wireless lighting controls match the official L-Connect effect list and make every visible GUI parameter affect the generated lighting command.

This phase focuses on the LIAN LI wireless lighting page and scene-center LIAN LI effect selection. It does not change fan control, ASUS LCD handling, OpenRGB, packaging, or the global scene executor beyond using the corrected LIAN LI effect catalog.

## Current Problems

The project already has a TLV2 effect capability model, color swatches, fixed color slots, and L-Connect-style direction buttons. However, the implementation still has old non-official effect keys in backend metadata and tests. This creates a split brain:

- The GUI drop-down only exposes official L-Connect effects.
- Backend specs and capability tables still include extra generated effects such as `staggered`, `tide`, `mixing`, `voice`, `door`, `render`, `reflect`, and related aliases.
- Some tests still expect the GUI to send those extra effects.
- Multi-color effects can show correct swatches while only part of the color input influences payload generation or effect indexing.

The visible result is that effects such as runway and heartbeat can look inconsistent with L-Connect, especially when changing the second color or switching colors.

## Official Effect Scope

The LIAN LI wireless GUI and scene-center effect picker should expose only the official L-Connect wireless effects currently verified from the user's screenshots. L-Connect also shows non-W wired variants for some effects, but this page talks to the wireless receiver TLV2 path, so this phase keeps the W/W* wireless set only:

- `off` / 关灯
- `rainbow` / 彩虹 (W*)
- `gradient-rainbow` -> `rainbow-morph` / 渐变彩虹 (W*)
- `static` / 单色 (W*)
- `breathing` / 呼吸 (W*)
- `meteor` / 流星 (W*)
- `runway` / 跑道 (W*)
- `starry` -> `twinkle` / 星空 (W*)
- `color-cycle` / 色彩循环 (W*)
- `cover-cycle` / 覆盖周期 (W*)
- `wave` / 波浪 (W*)
- `meteor-shower` / 流星雨 (W*)
- `disco` / 迪斯科 (W*)
- `blow-up` / 爆破 (W*)
- `heartbeat` / 心跳 (W*)
- `warning` / 警示 (W*)
- `ocean` / 海洋 (W*)
- `ripple` / 涟漪 (W*)
- `echo` / 回声 (W*)

Non-official generated effects should not appear in the LIAN LI GUI, scene center, or user-facing effect helpers. Existing backend code may keep private helpers temporarily only when needed to support an official effect, but user-facing catalogs and tests should not treat those old effect keys as valid choices.

## Effect Catalog

Introduce a single official LIAN LI effect catalog, or tighten the current metadata into that role. Each entry should define:

- User-facing label.
- Canonical backend effect key.
- Supported color slot count.
- Whether slots map to primary/accent colors or a palette.
- Whether direction is visible and sent.
- Whether speed and brightness are visible and sent.
- Default color values.
- Whether the effect uses an official captured TLV2 template or generated frames.

The GUI, scene center, settings normalization, and tests should all read from this catalog instead of maintaining separate lists.

Recommended initial slot model:

- `off`: 0 colors, no direction, no speed, no brightness.
- `rainbow`, `rainbow-morph`: 0 custom colors, direction, speed, brightness.
- `static`, `breathing`, `meteor`, `wave`: 1 color, direction only where motion uses it, speed and brightness.
- `runway`, `twinkle`, `cover-cycle`, `disco`, `blow-up`, `heartbeat`, `warning`, `ocean`, `ripple`, `echo`: 2 colors unless a current official capture proves otherwise.
- `electric-current` may remain only as a private backend helper for captured templates. It should not be user-selectable in the wireless GUI unless it appears in the wireless catalog.
- `color-cycle`: 3 colors.
- `meteor-shower`: 4 colors.

If a screenshot or packet capture later proves a different count, update the catalog and tests in one place.

## GUI Behavior

The LIAN LI wireless page should render controls directly from the official catalog:

- Color swatches are fixed to the catalog slot count. Users can edit colors but cannot add or remove slots.
- Swatches should remain blank text buttons with background color and tooltips, not hex strings as visible labels.
- Primary/accent-only effects should show a compact row of swatches rather than a string field.
- Direction controls use the existing L-Connect-style left and right arrow buttons.
- Direction controls are hidden for effects that do not use direction.
- Speed and brightness controls are hidden for effects that do not use them.

The scene center should use the same official catalog so scene presets cannot select effects that the LIAN LI page no longer exposes.

## Backend Behavior

The sender path should normalize GUI aliases through the official catalog before generating packets.

For every effect with visible colors:

- Changing any visible color must change either the RGB frame payload, the TLV2 effect index, or both.
- Runway must use both color slots in frame generation and effect-index calculation.
- Heartbeat must use both color slots in frame generation and effect-index calculation.
- Palette effects must preserve slot order and not silently fall back to only the first color.

For effects backed by official TLV2 templates:

- Remapping should use the catalog-defined slots.
- Direction should reverse the official frames only when the catalog says direction is supported.

For generated effects:

- Generation should be kept only for official effects.
- Internal helpers for old effects should be removed or made private if they are no longer reachable.

## Error Handling

Unsupported old effect keys should be handled deliberately:

- GUI and scene center should not offer them.
- Loading old settings should migrate unknown LIAN LI effects to `off` or the closest official alias.
- Backend public APIs should raise `LianLiWirelessError` for unsupported user-facing effects.

Write-gate behavior and guarded LIAN LI writes stay unchanged.

## Testing

Add or update focused tests for:

- The GUI effect drop-down exactly matches the official effect catalog.
- The scene-center LIAN LI effect options come from the same catalog.
- No non-official generated effect is user-selectable.
- Each official effect shows the expected number of swatches.
- Direction buttons are visible only for direction-capable effects.
- Runway uses two colors and changing the second color changes generated output or effect index.
- Heartbeat uses two colors and changing either color changes generated output or effect index.
- Palette effects preserve color order and all visible slots are used.
- Loading stale settings with removed effect keys falls back safely.

Run the full test suite after implementation.

## Success Criteria

The LIAN LI page should no longer have a separate visible effect list, capability list, and backend list that can drift apart.

For every official effect, the controls shown to the user should be exactly the controls used by the packet builder.

For runway and heartbeat specifically, changing the second color must visibly change the generated command data, not only the GUI swatch.
