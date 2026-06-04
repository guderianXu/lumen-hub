from __future__ import annotations

from dataclasses import dataclass

DEFAULT_FAN_CURVE_POINTS = [[30, 25], [50, 40], [70, 70], [85, 100]]
FAN_CURVE_PRESETS: dict[str, tuple[str, list[list[int]]]] = {
    "quiet": ("安静", [[30, 18], [50, 25], [65, 38], [80, 62], [92, 100]]),
    "normal": ("标准", DEFAULT_FAN_CURVE_POINTS),
    "high": ("高速", [[30, 35], [45, 52], [60, 72], [75, 100]]),
    "full": ("全速", [[0, 100], [100, 100]]),
}
FAN_CURVE_CUSTOM_PRESET = "custom"
FAN_CURVE_SENSOR_SOURCES: dict[str, str] = {
    "cpu": "CPU",
    "gpu": "GPU",
    "max": "CPU/GPU 最大值",
}


@dataclass(frozen=True)
class FanCurvePolicyResult:
    percent: int
    temperature_c: float | None
    sensor_label: str
    reason: str = "curve"
    held_by_hysteresis: bool = False


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


def normalize_fan_curve_preset(value: object) -> str:
    preset = str(value or "normal").strip().lower()
    if preset in FAN_CURVE_PRESETS or preset == FAN_CURVE_CUSTOM_PRESET:
        return preset
    return "normal"


def normalize_fan_curve_sensor_source(value: object) -> str:
    source = str(value or "cpu").strip().lower()
    if source in FAN_CURVE_SENSOR_SOURCES:
        return source
    return "cpu"


def fan_curve_preset_label(preset: object) -> str:
    key = normalize_fan_curve_preset(preset)
    if key == FAN_CURVE_CUSTOM_PRESET:
        return "自定义"
    return FAN_CURVE_PRESETS[key][0]


def fan_curve_preset_points(preset: object) -> list[list[int]]:
    key = normalize_fan_curve_preset(preset)
    if key == FAN_CURVE_CUSTOM_PRESET:
        return sanitize_fan_curve_points(DEFAULT_FAN_CURVE_POINTS)
    return sanitize_fan_curve_points(FAN_CURVE_PRESETS[key][1])


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


def apply_fan_curve_policy(
    points: object,
    *,
    cpu_temperature_c: float | int | None,
    gpu_temperature_c: float | int | None = None,
    sensor_source: object = "cpu",
    previous_temperature_c: float | int | None = None,
    previous_percent: int | None = None,
    hysteresis_c: float | int = 0,
    fallback_percent: int = 100,
    minimum_percent: int = 0,
) -> FanCurvePolicyResult:
    source = normalize_fan_curve_sensor_source(sensor_source)
    temperature, label = _select_curve_temperature(source, cpu_temperature_c, gpu_temperature_c)
    safe_fallback = _clamp_int(fallback_percent, 0, 100)
    safe_minimum = _clamp_int(minimum_percent, 0, 100)

    if temperature is None:
        return FanCurvePolicyResult(safe_fallback, None, FAN_CURVE_SENSOR_SOURCES[source], "sensor-missing")

    if (
        previous_temperature_c is not None
        and previous_percent is not None
        and abs(float(temperature) - float(previous_temperature_c)) <= max(0.0, float(hysteresis_c))
    ):
        return FanCurvePolicyResult(
            _clamp_int(previous_percent, 0, 100),
            float(temperature),
            label,
            "hysteresis",
            held_by_hysteresis=True,
        )

    percent = interpolate_fan_curve_percent(points, temperature)
    if percent is None:
        return FanCurvePolicyResult(safe_fallback, float(temperature), label, "sensor-missing")
    if 0 < percent < safe_minimum:
        percent = safe_minimum
    return FanCurvePolicyResult(percent, float(temperature), label)


def _select_curve_temperature(
    source: str,
    cpu_temperature_c: float | int | None,
    gpu_temperature_c: float | int | None,
) -> tuple[float | None, str]:
    cpu = None if cpu_temperature_c is None else float(cpu_temperature_c)
    gpu = None if gpu_temperature_c is None else float(gpu_temperature_c)
    if source == "gpu":
        return gpu, "GPU"
    if source == "max":
        if cpu is None and gpu is None:
            return None, "CPU/GPU"
        if gpu is not None and (cpu is None or gpu >= cpu):
            return gpu, "GPU"
        return cpu, "CPU"
    return cpu, "CPU"
