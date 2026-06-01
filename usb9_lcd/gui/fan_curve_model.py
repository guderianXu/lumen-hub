from __future__ import annotations

DEFAULT_FAN_CURVE_POINTS = [[30, 25], [50, 40], [70, 70], [85, 100]]


def _clamp_int(value: object, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(maximum, parsed))


def sanitize_fan_curve_points(points: object, default: list[list[int]] | None = None) -> list[list[int]]:
    fallback = default or DEFAULT_FAN_CURVE_POINTS
    parsed: list[list[int]] = []
    if isinstance(points, list):
        for item in points[:12]:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            parsed.append([_clamp_int(item[0], 0, 100), _clamp_int(item[1], 0, 100)])
    while len(parsed) < 2:
        parsed.append(list(fallback[min(len(parsed), len(fallback) - 1)]))
    return sorted(parsed, key=lambda item: item[0])


def interpolate_fan_curve_percent(points: object, temperature_c: float | int | None) -> int | None:
    if temperature_c is None:
        return None
    curve = sanitize_fan_curve_points(points)
    temp = float(temperature_c)
    if temp <= curve[0][0]:
        return curve[0][1]
    for left, right in zip(curve, curve[1:], strict=False):
        if temp <= right[0]:
            span = max(1, right[0] - left[0])
            ratio = (temp - left[0]) / span
            return _clamp_int(round(left[1] + (right[1] - left[1]) * ratio), 0, 100)
    return curve[-1][1]
