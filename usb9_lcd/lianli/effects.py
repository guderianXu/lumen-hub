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
