from __future__ import annotations

import time
from pathlib import Path

from .models import CpuTelemetry

CPU_LABEL_MARKERS = ("package", "tdie", "tctl", "cpu")
CPU_POWER_LABEL_MARKERS = ("package", "ppt", "cpu", "tdp", "power")
PRIMARY_CPU_HWMON_NAMES = {"coretemp", "k10temp", "zenpower", "cpu_thermal", "fam15h_power"}
FALLBACK_CPU_HWMON_NAMES = {"acpitz"}
NON_CPU_HWMON_NAME_MARKERS = ("nvme", "amdgpu", "nvidia", "asus", "iwlwifi")
_POWERCAP_PREVIOUS: dict[str, tuple[int, float, int]] = {}


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


def _power_from_microwatts(value: str) -> float | None:
    try:
        power = int(value.strip()) / 1_000_000.0
    except ValueError:
        return None
    if power < 0 or power > 2000:
        return None
    return power


def _read_int(path: Path) -> int | None:
    try:
        return int(_read_text(path))
    except ValueError:
        return None


def _label_priority(label: str) -> int:
    lowered = label.lower()
    for priority, marker in enumerate(CPU_LABEL_MARKERS):
        if marker in lowered:
            return priority
    return len(CPU_LABEL_MARKERS)


def _power_label_priority(label: str) -> int:
    lowered = label.lower()
    for priority, marker in enumerate(CPU_POWER_LABEL_MARKERS):
        if marker in lowered:
            return priority
    return len(CPU_POWER_LABEL_MARKERS)


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


def _powercap_package_entries(powercap_root: Path) -> list[Path]:
    if not powercap_root.exists():
        return []
    entries: list[Path] = []
    for entry in sorted(powercap_root.glob("intel-rapl:*")):
        if not entry.is_dir() or not (entry / "energy_uj").exists():
            continue
        name = _read_text(entry / "name").lower()
        is_top_level_package = entry.name.count(":") == 1
        if "package" in name or (is_top_level_package and name.startswith("intel-rapl")):
            entries.append(entry)
    return entries


def _cpu_power_from_powercap(
    powercap_root: Path = Path("/sys/class/powercap"),
    *,
    monotonic_clock=time.monotonic,
    cache: dict[str, tuple[int, float, int]] | None = None,
) -> float | None:
    readings: list[float] = []
    target_cache = _POWERCAP_PREVIOUS if cache is None else cache
    now = monotonic_clock()

    for entry in _powercap_package_entries(powercap_root):
        energy = _read_int(entry / "energy_uj")
        if energy is None:
            continue
        max_range = _read_int(entry / "max_energy_range_uj") or 0
        key = str(entry)
        previous = target_cache.get(key)
        target_cache[key] = (energy, now, max_range)
        if previous is None:
            continue
        previous_energy, previous_time, previous_range = previous
        elapsed = now - previous_time
        if elapsed <= 0 or elapsed > 60:
            continue
        range_uj = max_range or previous_range
        delta = energy - previous_energy
        if delta < 0 and range_uj > 0:
            delta = range_uj - previous_energy + energy
        if delta < 0:
            continue
        power_w = delta / elapsed / 1_000_000.0
        if 0 <= power_w <= 2000:
            readings.append(power_w)

    return round(sum(readings), 1) if readings else None


def collect_cpu_temperature_from_hwmon(
    hwmon_root: Path = Path("/sys/class/hwmon"),
    *,
    powercap_root: Path = Path("/sys/class/powercap"),
    monotonic_clock=time.monotonic,
    powercap_cache: dict[str, tuple[int, float, int]] | None = None,
) -> CpuTelemetry:
    readings: list[tuple[int, int, float]] = []
    power_readings: list[tuple[int, int, float]] = []

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

        for input_path in sorted(hwmon.glob("power*_input")):
            index = input_path.stem.removeprefix("power").removesuffix("_input")
            power = _power_from_microwatts(_read_text(input_path))
            if power is None:
                continue
            label = _read_text(hwmon / f"power{index}_label")
            power_readings.append((hwmon_priority, _power_label_priority(label), power))

    temperature = None
    if readings:
        readings.sort(key=lambda reading: (reading[0], reading[1]))
        temperature = readings[0][2]

    power = None
    if power_readings:
        power_readings.sort(key=lambda reading: (reading[0], reading[1]))
        power = power_readings[0][2]
    else:
        power = _cpu_power_from_powercap(
            powercap_root,
            monotonic_clock=monotonic_clock,
            cache=powercap_cache,
        )

    available = temperature is not None or power is not None
    errors: list[str] = []
    if temperature is None:
        errors.append("no identifiable CPU temperature sensor found in hwmon")
    if power is None:
        errors.append("CPU power sensor unavailable")
    return CpuTelemetry(
        package_temperature_c=temperature,
        power_w=round(power, 1) if power is not None else None,
        available=available,
        error="; ".join(errors) if not available else "",
    )
