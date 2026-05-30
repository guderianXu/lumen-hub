from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class GpuTelemetry:
    name: str = "NVIDIA GPU"
    temperature_c: int | None = None
    utilization_percent: int | None = None
    power_w: float | None = None
    memory_used_mb: int | None = None
    memory_total_mb: int | None = None
    graphics_clock_mhz: int | None = None
    available: bool = False
    error: str = ""


@dataclass(frozen=True)
class CpuTelemetry:
    package_temperature_c: float | None = None
    utilization_percent: float | None = None
    available: bool = False
    error: str = ""


@dataclass(frozen=True)
class FanTelemetry:
    name: str
    rpm: int | None = None
    percent: float | None = None
    available: bool = False
    error: str = ""


@dataclass(frozen=True)
class SystemTelemetry:
    cpu: CpuTelemetry
    gpu: GpuTelemetry
    captured_at: datetime
    fans: list[FanTelemetry] = field(default_factory=list)
