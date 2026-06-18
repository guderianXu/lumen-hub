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
