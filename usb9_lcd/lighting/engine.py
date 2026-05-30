from __future__ import annotations

from dataclasses import dataclass

from .effects import effect_aliases, effect_uses_color
from .profiles import mode_aliases_for_device, profile_for_device_name
from .software_effects import is_software_lighting_effect


@dataclass(frozen=True)
class LightingApplyPlan:
    effect: str
    mode_aliases: tuple[str, ...]
    zone_size: int | None
    stable_whole_device_color: bool
    color_reapply_count: int
    uses_color: bool
    supports_software_animation: bool

    def should_apply_explicit_color(self, mode_result: str) -> bool:
        if mode_result == "none":
            return True
        if self.supports_software_animation:
            return False
        if self.effect in {"static", "direct"}:
            return True
        return self.uses_color and mode_result != "mode-color"


def build_lighting_apply_plan(
    *,
    effect: str,
    device_name: str,
    target_zone_index: int | None,
    requested_zone_size: int | None,
    default_static_reapply_count: int,
) -> LightingApplyPlan:
    profile = profile_for_device_name(device_name)
    zone_size = requested_zone_size
    whole_device = target_zone_index is None
    static_like = effect in {"static", "direct"}
    stable_whole_device_color = (
        static_like
        and whole_device
        and profile.static_strategy == "whole-device-static-all-zones-same-color"
    )
    if zone_size is None and stable_whole_device_color:
        zone_size = profile.static_zone_size

    fallback_aliases = effect_aliases(effect)
    return LightingApplyPlan(
        effect=effect,
        mode_aliases=mode_aliases_for_device(effect, device_name, fallback_aliases),
        zone_size=zone_size,
        stable_whole_device_color=stable_whole_device_color and bool(zone_size),
        color_reapply_count=max(default_static_reapply_count, profile.static_reapply_count),
        uses_color=effect_uses_color(effect),
        supports_software_animation=is_software_lighting_effect(effect),
    )
