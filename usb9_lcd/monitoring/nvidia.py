from __future__ import annotations

import csv
import subprocess
from collections.abc import Callable, Sequence

from .models import GpuTelemetry

NVIDIA_SMI_QUERY = (
    "name,temperature.gpu,utilization.gpu,power.draw,"
    "memory.used,memory.total,clocks.current.graphics"
)


def _parse_int(value: str) -> int | None:
    value = value.strip()
    if not value or value.upper() == "N/A" or "not supported" in value.lower():
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _parse_float(value: str) -> float | None:
    value = value.strip()
    if not value or value.upper() == "N/A" or "not supported" in value.lower():
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_nvidia_smi_csv(output: str) -> GpuTelemetry:
    rows = csv.reader(output.splitlines(), skipinitialspace=True)
    first_row = next((row for row in rows if any(field.strip() for field in row)), None)
    if first_row is None:
        return GpuTelemetry(error="nvidia-smi returned no GPU rows")

    parts = [part.strip() for part in first_row]
    if len(parts) != 7:
        return GpuTelemetry(error=f"unexpected nvidia-smi output: {', '.join(first_row)}")

    return GpuTelemetry(
        name=parts[0] or "NVIDIA GPU",
        temperature_c=_parse_int(parts[1]),
        utilization_percent=_parse_int(parts[2]),
        power_w=_parse_float(parts[3]),
        memory_used_mb=_parse_int(parts[4]),
        memory_total_mb=_parse_int(parts[5]),
        graphics_clock_mhz=_parse_int(parts[6]),
        available=True,
    )


def collect_nvidia_gpu(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: float = 2.0,
) -> GpuTelemetry:
    command: Sequence[str] = (
        "nvidia-smi",
        f"--query-gpu={NVIDIA_SMI_QUERY}",
        "--format=csv,noheader,nounits",
    )
    try:
        result = run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as error:
        return GpuTelemetry(error=str(error))
    except subprocess.TimeoutExpired:
        return GpuTelemetry(error="nvidia-smi timed out")
    except OSError as error:
        return GpuTelemetry(error=str(error))

    if result.returncode != 0:
        return GpuTelemetry(error=(result.stderr or result.stdout or "nvidia-smi failed").strip())

    return parse_nvidia_smi_csv(result.stdout)
