from __future__ import annotations

import colorsys
import math


SOFTWARE_LIGHTING_EFFECTS = frozenset({"star", "meteor", "comet", "scan", "visor", "matrix", "gradient"})

Rgb = tuple[int, int, int]


def is_software_lighting_effect(effect: str) -> bool:
    return effect in SOFTWARE_LIGHTING_EFFECTS


def software_effect_interval_seconds(speed_percent: int) -> float:
    speed = max(0, min(100, int(speed_percent))) / 100
    return 0.12 - 0.09 * speed


def render_software_effect_frame(
    effect: str,
    *,
    led_count: int,
    frame_index: int,
    base_color: Rgb,
    global_offset: int = 0,
    total_leds: int | None = None,
) -> list[Rgb]:
    count = max(0, int(led_count))
    total = max(count, int(total_leds or count), 1)
    if count <= 0:
        return []
    if effect == "star":
        return _star_frame(count, frame_index, base_color, global_offset)
    if effect == "meteor":
        return _tail_frame(count, frame_index, base_color, global_offset, total, tail_ratio=0.18, white_head=False)
    if effect == "comet":
        return _tail_frame(count, frame_index, base_color, global_offset, total, tail_ratio=0.32, white_head=True)
    if effect == "scan":
        return _scan_frame(count, frame_index, base_color, global_offset, total)
    if effect == "visor":
        return _visor_frame(count, frame_index, base_color, global_offset, total)
    if effect == "matrix":
        return _matrix_frame(count, frame_index, base_color, global_offset)
    if effect == "gradient":
        return _gradient_frame(count, frame_index, base_color, global_offset, total)
    return [base_color] * count


def _star_frame(count: int, frame_index: int, base: Rgb, offset: int) -> list[Rgb]:
    ambient = _scale(base, 0.05)
    frame: list[Rgb] = []
    for index in range(count):
        noise = _noise(index + offset, frame_index // 3, 19)
        pulse = 0.5 + 0.5 * math.sin((frame_index * 0.31) + (index + offset) * 1.73)
        if noise > 238:
            intensity = 0.75 + 0.25 * pulse
        elif noise > 224:
            intensity = 0.28 + 0.35 * pulse
        else:
            intensity = 0.08 if _noise(index + offset, 0, 97) > 210 else 0.05
        frame.append(_max_rgb(ambient, _scale(base, intensity)))
    return frame


def _tail_frame(
    count: int,
    frame_index: int,
    base: Rgb,
    offset: int,
    total: int,
    *,
    tail_ratio: float,
    white_head: bool,
) -> list[Rgb]:
    cycle = total
    positions = (
        frame_index % cycle,
        (frame_index + cycle // 3) % cycle,
        (frame_index + (cycle * 2) // 3) % cycle,
    )
    tail = max(3, round(total * tail_ratio))
    ambient = _scale(base, 0.05)
    frame: list[Rgb] = [ambient] * count
    for index in range(count):
        global_index = index + offset
        for actor, position in enumerate(positions):
            distance = (position - global_index) % cycle
            if distance == 0 and white_head and actor == 0:
                frame[index] = _max_rgb(frame[index], (255, 255, 255))
            elif 0 <= distance <= tail:
                intensity = (1 - distance / max(1, tail)) ** 1.7
                frame[index] = _max_rgb(frame[index], _scale(base, intensity))
    return frame


def _scan_frame(count: int, frame_index: int, base: Rgb, offset: int, total: int) -> list[Rgb]:
    span = max(1, total - 1)
    cycle = max(2, span * 2)
    phase = frame_index % cycle
    position = phase if phase <= span else cycle - phase
    width = max(2, total // 16)
    ambient = _scale(base, 0.06)
    frame: list[Rgb] = []
    for index in range(count):
        global_index = index + offset
        mirrored_position = span - position
        distance = min(abs(global_index - position), abs(global_index - mirrored_position))
        intensity = max(0.0, 1.0 - distance / max(1, width))
        frame.append(_max_rgb(ambient, _scale(base, intensity ** 1.4)))
    return frame


def _visor_frame(count: int, frame_index: int, base: Rgb, offset: int, total: int) -> list[Rgb]:
    width = max(4, total // 5)
    cycle = max(1, total)
    starts = ((frame_index * 2) % cycle, (frame_index * 2 + cycle // 2) % cycle)
    ambient = _scale(base, 0.05)
    frame: list[Rgb] = []
    for index in range(count):
        global_index = index + offset
        pixel = ambient
        for start in starts:
            local = (global_index - start) % cycle
            if local <= width:
                distance = abs(local - width / 2) / max(1.0, width / 2)
                intensity = 1.0 - distance ** 2
                color = _hue_shift(base, (global_index / max(1, total)) * 0.2 + frame_index * 0.01)
                pixel = _max_rgb(pixel, _scale(color, intensity))
        frame.append(pixel)
    return frame


def _matrix_frame(count: int, frame_index: int, base: Rgb, offset: int) -> list[Rgb]:
    green = _matrix_base_color(base)
    frame: list[Rgb] = []
    for index in range(count):
        global_index = index + offset
        drop_a = (frame_index + _noise(global_index, 0, 31)) % 23
        drop_b = (frame_index * 2 + _noise(global_index, 0, 53)) % 41
        if drop_a == 0 or drop_b == 0:
            intensity = 1.0
        elif drop_a in {1, 2, 3}:
            intensity = 0.55 / drop_a
        elif _noise(global_index, frame_index // 2, 71) > 245:
            intensity = 0.25
        else:
            intensity = 0.06
        frame.append(_scale(green, intensity))
    return frame


def _gradient_frame(count: int, frame_index: int, base: Rgb, offset: int, total: int) -> list[Rgb]:
    frame: list[Rgb] = []
    for index in range(count):
        phase = ((index + offset) / max(1, total)) + frame_index * 0.01
        frame.append(_hue_shift(base, phase))
    return frame


def _matrix_base_color(base: Rgb) -> Rgb:
    if max(base) <= 0:
        return (0, 255, 80)
    red, green, blue = base
    if green >= red and green >= blue:
        return base
    return (round(red * 0.12), max(120, green), round(blue * 0.18))


def _hue_shift(base: Rgb, phase: float) -> Rgb:
    red, green, blue = (channel / 255 for channel in base)
    hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
    if value <= 0:
        hue, saturation, value = 0.58, 1.0, 1.0
    shifted = colorsys.hsv_to_rgb((hue + phase) % 1.0, max(0.65, saturation), max(0.25, value))
    return tuple(max(0, min(255, round(channel * 255))) for channel in shifted)


def _scale(color: Rgb, factor: float) -> Rgb:
    clamped = max(0.0, min(1.0, factor))
    return tuple(max(0, min(255, round(channel * clamped))) for channel in color)


def _max_rgb(first: Rgb, second: Rgb) -> Rgb:
    return tuple(max(first[index], second[index]) for index in range(3))


def _noise(index: int, frame_index: int, salt: int) -> int:
    value = (index + 1) * 1103515245 + (frame_index + 17) * 12345 + salt * 2654435761
    value = (value ^ (value >> 16)) & 0xFFFFFFFF
    return (value >> 8) & 0xFF
