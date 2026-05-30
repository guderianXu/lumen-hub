from __future__ import annotations

from pathlib import Path

from .models import CpuTelemetry

CPU_LABEL_MARKERS = ("package", "tdie", "tctl", "cpu")
PRIMARY_CPU_HWMON_NAMES = {"coretemp", "k10temp", "zenpower", "cpu_thermal"}
FALLBACK_CPU_HWMON_NAMES = {"acpitz"}
NON_CPU_HWMON_NAME_MARKERS = ("nvme", "amdgpu", "nvidia", "asus", "iwlwifi")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _temperature_from_millidegrees(value: str) -> float | None:
    try:
        return int(value.strip()) / 1000.0
    except ValueError:
        return None


def _label_priority(label: str) -> int:
    lowered = label.lower()
    for priority, marker in enumerate(CPU_LABEL_MARKERS):
        if marker in lowered:
            return priority
    return len(CPU_LABEL_MARKERS)


def _cpu_hwmon_priority(name: str) -> int | None:
    normalized = name.strip().lower()
    if not normalized:
        return None
    if any(marker in normalized for marker in NON_CPU_HWMON_NAME_MARKERS):
        return None
    if normalized in PRIMARY_CPU_HWMON_NAMES:
        return 0
    if normalized in FALLBACK_CPU_HWMON_NAMES:
        return 1
    return None


def collect_cpu_temperature_from_hwmon(hwmon_root: Path = Path("/sys/class/hwmon")) -> CpuTelemetry:
    readings: list[tuple[int, int, float]] = []

    for hwmon in sorted(hwmon_root.glob("hwmon*")):
        hwmon_priority = _cpu_hwmon_priority(_read_text(hwmon / "name"))
        if hwmon_priority is None:
            continue

        for input_path in sorted(hwmon.glob("temp*_input")):
            index = input_path.stem.removeprefix("temp").removesuffix("_input")
            temperature = _temperature_from_millidegrees(_read_text(input_path))
            if temperature is None:
                continue

            label = _read_text(hwmon / f"temp{index}_label")
            readings.append((hwmon_priority, _label_priority(label), temperature))

    if not readings:
        return CpuTelemetry(error="no identifiable CPU temperature sensor found in hwmon")

    readings.sort(key=lambda reading: (reading[0], reading[1]))
    return CpuTelemetry(package_temperature_c=readings[0][2], available=True)
