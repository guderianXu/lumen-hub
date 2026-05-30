from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LightingEffect:
    key: str
    label: str
    aliases: tuple[str, ...]
    uses_color: bool = False


LIGHTING_EFFECTS: tuple[LightingEffect, ...] = (
    LightingEffect("off", "关闭", ("off",)),
    LightingEffect("static", "静态", ("static", "fixed", "steady", "constant", "direct", "custom"), True),
    LightingEffect("breathing", "呼吸", ("breathing", "breath", "breathe"), True),
    LightingEffect("rainbow", "彩虹", ("rainbow", "spectrum cycle", "spectrum", "rainbow wave")),
    LightingEffect("spectrum", "光谱", ("spectrum cycle", "spectrum", "rainbow", "color cycle", "colour cycle")),
    LightingEffect("wave", "波浪", ("wave", "rainbow wave", "color wave", "colour wave")),
    LightingEffect("chase", "追逐", ("chase", "running lights", "chase fade", "rainbow chase"), True),
    LightingEffect("color_cycle", "颜色循环", ("color cycle", "colour cycle", "cycle", "color shift", "colour shift")),
    LightingEffect("color_pulse", "颜色脉冲", ("color pulse", "colour pulse", "pulse", "pulsing"), True),
    LightingEffect("flashing", "闪烁", ("flashing", "flash", "strobe", "blink", "blinking"), True),
    LightingEffect("star", "星空", ("star", "starry night", "sparkle", "twinkle")),
    LightingEffect("meteor", "流星", ("meteor", "meteor shower", "comet")),
    LightingEffect("comet", "彗星", ("comet", "comet tail", "meteor")),
    LightingEffect("scan", "扫描", ("scan", "scanner", "larson scanner")),
    LightingEffect("visor", "遮罩", ("visor", "rainbow visor")),
    LightingEffect("matrix", "矩阵", ("matrix", "digital rain")),
    LightingEffect("gradient", "渐变", ("gradient", "gradient cycle", "gradient wave")),
    LightingEffect("direct", "Direct", ("direct", "custom"), True),
)

LIGHTING_EFFECT_MAP: dict[str, str] = {effect.label: effect.key for effect in LIGHTING_EFFECTS}
_EFFECT_BY_KEY = {effect.key: effect for effect in LIGHTING_EFFECTS}


def effect_aliases(effect: str) -> tuple[str, ...]:
    entry = _EFFECT_BY_KEY.get(effect)
    return entry.aliases if entry is not None else (effect,)


def effect_uses_color(effect: str) -> bool:
    entry = _EFFECT_BY_KEY.get(effect)
    return bool(entry and entry.uses_color)


def effect_label(effect: str) -> str:
    entry = _EFFECT_BY_KEY.get(effect)
    return entry.label if entry is not None else effect
