from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime

from .cpu import collect_cpu_temperature_from_hwmon
from .models import CpuTelemetry, FanTelemetry, GpuTelemetry, SystemTelemetry
from .nvidia import collect_nvidia_gpu
from .windows import collect_windows_cpu_telemetry, collect_windows_fans


def _default_cpu_collector() -> Callable[[], CpuTelemetry]:
    if sys.platform.startswith("win"):
        return collect_windows_cpu_telemetry
    return collect_cpu_temperature_from_hwmon


def _default_fan_collector() -> Callable[[], list[FanTelemetry]]:
    if sys.platform.startswith("win"):
        return collect_windows_fans
    return lambda: []


def collect_system_telemetry(
    cpu_collector: Callable[[], CpuTelemetry] | None = None,
    gpu_collector: Callable[[], GpuTelemetry] = collect_nvidia_gpu,
    fan_collector: Callable[[], list[FanTelemetry]] | None = None,
    clock: Callable[[], datetime] = datetime.now,
) -> SystemTelemetry:
    selected_cpu_collector = cpu_collector or _default_cpu_collector()
    selected_fan_collector = fan_collector or _default_fan_collector()
    return SystemTelemetry(
        cpu=selected_cpu_collector(),
        gpu=gpu_collector(),
        fans=selected_fan_collector(),
        captured_at=clock(),
    )
