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
            "lianli_lighting": {
                "mode": "effect",
                "effect": "warning",
                "color": "#ff2d55",
                "accent_color": "#ffffff",
            },
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
            actions.append(
                SceneSubsystemAction(subsystem, section.mode, section.payload, SceneStatus.SKIPPED, "保持当前状态")
            )
            continue
        if not checks[subsystem]:
            actions.append(
                SceneSubsystemAction(subsystem, section.mode, section.payload, SceneStatus.DEVICE_MISSING, "设备不可用")
            )
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
