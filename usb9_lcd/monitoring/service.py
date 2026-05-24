from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from .cpu import collect_cpu_temperature_from_hwmon
from .models import CpuTelemetry, GpuTelemetry, SystemTelemetry
from .nvidia import collect_nvidia_gpu


def collect_system_telemetry(
    cpu_collector: Callable[[], CpuTelemetry] = collect_cpu_temperature_from_hwmon,
    gpu_collector: Callable[[], GpuTelemetry] = collect_nvidia_gpu,
    clock: Callable[[], datetime] = datetime.now,
) -> SystemTelemetry:
    return SystemTelemetry(
        cpu=cpu_collector(),
        gpu=gpu_collector(),
        captured_at=clock(),
    )
