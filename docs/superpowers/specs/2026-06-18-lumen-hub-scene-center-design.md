# Lumen Hub Scene Center Design

## Goal

Turn Lumen Hub from a set of separate hardware pages into a unified control center where the user can switch the whole machine between named scenes such as daily, gaming, sleep, showcase, quiet, and temperature warning.

This first phase should not clone the full Aura Creator timeline editor. It should create a reliable scene system that can orchestrate the capabilities Lumen Hub already has: LCD content, OpenRGB lighting, LIAN LI wireless lighting, host fan curves, LIAN LI fan curves, and sleep/off actions.

## Current Context

The project already has the foundations for this:

- `LightingUiSettings` stores `active_scene` and `scenes`, but these currently describe OpenRGB lighting only.
- `LightingPage` can save, rename, delete, and apply OpenRGB scenes.
- `MainWindow.sleep_all_off()` already coordinates LCD blanking, OpenRGB off, and LIAN LI off.
- Host fan and LIAN LI wireless fan pages already have curve presets and one-shot apply actions.
- LIAN LI wireless lighting is still guarded by write safety checks, so scenes must never silently bypass write gates.

The main gap is that there is no single scene model, page, or execution plan that can apply all hardware domains together.

## User Experience

The home page should show a compact "Current Scene" area with scene cards:

- Daily
- Gaming
- Sleep
- Showcase
- Quiet
- Temperature Warning

Clicking a scene applies the supported parts of that scene. The result should be explicit: applied, skipped, failed, or needs permission for each subsystem.

A new scene page should allow editing one scene at a time. It should be utilitarian and dense, not a landing page. The page should be organized into subsystem sections:

- Screen: leave unchanged, turn off, monitoring profile, asset path.
- OpenRGB: leave unchanged, turn off, apply saved lighting payload.
- LIAN LI lighting: leave unchanged, turn off, apply effect with colors, speed, brightness, direction.
- Host fan: leave unchanged, apply preset, apply custom curve, enable or disable auto curve.
- LIAN LI fan: leave unchanged, apply preset, apply custom curve, enable or disable auto curve.
- Safety: dry run only, require confirmation, allow guarded LIAN LI write if already unlocked.
- Trigger: manual in phase one; application/time/temperature triggers are reserved for later.

The first implementation should provide built-in scenes and allow saving user scenes. Advanced timeline editing, AI-generated lighting, and community sharing are intentionally out of scope for phase one.

## Scene Model

Add a global scene model instead of stretching the OpenRGB-only scene payload.

Proposed payload shape:

```json
{
  "name": "Gaming",
  "description": "Bright lighting, monitoring LCD, normal curves",
  "screen": {
    "mode": "monitor_profile",
    "profile": "Gaming"
  },
  "openrgb": {
    "mode": "scene",
    "scene_name": "Gaming RGB"
  },
  "lianli_lighting": {
    "mode": "effect",
    "effect": "runway",
    "color": "#ff2d55",
    "accent_color": "#ffffff",
    "palette": ["#ff2d55", "#1fd1ff"],
    "brightness": 100,
    "speed": 75,
    "direction": "left"
  },
  "host_fan": {
    "mode": "preset",
    "preset": "normal",
    "auto_curve_enabled": true
  },
  "lianli_fan": {
    "mode": "preset",
    "preset": "normal",
    "auto_curve_enabled": true
  },
  "safety": {
    "dry_run": false,
    "require_confirmation": false,
    "allow_lianli_write": false
  }
}
```

Each subsystem uses a `mode` field so missing hardware can be skipped cleanly and old configs can migrate safely.

Store global scenes under a new top-level settings group, for example `GuiSettings.scenes`, with `active_scene` beside it. Keep `lighting.scenes` for backward compatibility with existing OpenRGB-only scenes, then migrate or reference them when a global scene uses `openrgb.mode = "scene"`.

## Execution Plan

Applying a scene should create a plan before writing hardware:

1. Resolve current device availability.
2. Expand the scene into subsystem actions.
3. Validate safety gates and permissions.
4. Execute safe fan actions first.
5. Execute screen and lighting actions.
6. Return a structured result per subsystem.
7. Save `active_scene` only after at least one subsystem applies successfully.

The execution layer should be separate from the UI. A `SceneApplyPlan` and `SceneApplyResult` make this testable without a running Qt window.

Suggested result states:

- `applied`
- `skipped`
- `permission_required`
- `device_missing`
- `failed`

The UI should show these states in the event log and in the scene page summary.

## Safety Rules

Scenes must preserve the existing conservative behavior:

- LIAN LI real writes require the existing write gate and user opt-in.
- A scene can request LIAN LI lighting, but it must produce `permission_required` if writes are not unlocked.
- Sleep/off scenes may call existing off paths, but they must still tolerate missing devices.
- A failed OpenRGB connection must not block LCD or fan actions.
- A failed fan action must not leave the UI claiming the scene fully applied.

## Migration

Existing OpenRGB-only scenes should remain usable. On load:

- Keep `settings.lighting.scenes` unchanged.
- Create no automatic global scenes from them unless the user saves one.
- When a user creates a global scene from the current state, include the selected OpenRGB scene or the current OpenRGB settings as that scene's OpenRGB action.

This avoids surprising users with changed lighting scene behavior.

## Testing

Tests should cover the scene engine before UI wiring:

- Built-in scenes validate against the schema.
- A scene with only OpenRGB actions expands to one OpenRGB action.
- A scene with LIAN LI lighting returns `permission_required` when write is disabled.
- A scene can partially apply when one subsystem is missing.
- `active_scene` updates only on successful or partial successful apply.
- Existing OpenRGB scene tests still pass.
- Settings round-trip global scenes without losing existing `lighting.scenes`.

GUI tests should then cover:

- Home page exposes scene cards.
- Scene page lists built-in and saved scenes.
- Applying a scene calls the scene coordinator and renders per-subsystem status.
- Sleep scene uses the existing off behavior without duplicating logic.

## Phase One Scope

In scope:

- Global scene settings model.
- Built-in scene presets.
- Scene apply plan/result engine.
- Home page scene cards.
- Scene editor page or a scene section integrated into the current lighting page.
- Integration with existing OpenRGB, LIAN LI lighting, host fan, LIAN LI fan, and LCD off/monitor hooks where those hooks already exist.

Out of scope:

- Aura Creator-style multi-layer timeline.
- Application/time/temperature automatic triggers.
- Community scene sharing.
- AI generated lighting.
- Reworking LIAN LI packet generation beyond using the existing effect model.

## Implementation Strategy

Start with the model and engine, then wire UI:

1. Add scene dataclasses and settings migration.
2. Add a pure scene planner that creates actions and safety statuses.
3. Add a scene coordinator in the GUI layer that calls existing page methods.
4. Add built-in scenes and home cards.
5. Add a compact scene editor.
6. Add packaging verification after tests pass.

This keeps the risky hardware writes behind existing methods and gives the new UX a testable core.
