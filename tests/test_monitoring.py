from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from usb9_lcd.monitoring.cpu import collect_cpu_temperature_from_hwmon
from usb9_lcd.monitoring.models import CpuTelemetry, GpuTelemetry
from usb9_lcd.monitoring.nvidia import collect_nvidia_gpu, parse_nvidia_smi_csv
from usb9_lcd.monitoring.service import collect_system_telemetry


def test_parse_nvidia_smi_csv_parses_first_gpu():
    output = "NVIDIA GeForce RTX 4090, 61, 42, 216.50, 8123, 24564, 2745\n"

    telemetry = parse_nvidia_smi_csv(output)

    assert telemetry.available is True
    assert telemetry.name == "NVIDIA GeForce RTX 4090"
    assert telemetry.temperature_c == 61
    assert telemetry.utilization_percent == 42
    assert telemetry.power_w == 216.50
    assert telemetry.memory_used_mb == 8123
    assert telemetry.memory_total_mb == 24564
    assert telemetry.graphics_clock_mhz == 2745
    assert telemetry.error == ""


def test_parse_nvidia_smi_csv_handles_quoted_gpu_name_with_comma():
    output = '"NVIDIA, RTX 4090", 61, 42, 216.50, 8123, 24564, 2745\n'

    telemetry = parse_nvidia_smi_csv(output)

    assert telemetry.available is True
    assert telemetry.name == "NVIDIA, RTX 4090"
    assert telemetry.temperature_c == 61


def test_parse_nvidia_smi_csv_rejects_extra_columns():
    output = "NVIDIA GPU, 61, 42, 216.50, 8123, 24564, 2745, extra\n"

    telemetry = parse_nvidia_smi_csv(output)

    assert telemetry.available is False
    assert "unexpected nvidia-smi output" in telemetry.error


def test_parse_nvidia_smi_csv_handles_not_available_fields():
    output = "NVIDIA GPU, N/A, [Not Supported], N/A, 100, 200, N/A\n"

    telemetry = parse_nvidia_smi_csv(output)

    assert telemetry.available is True
    assert telemetry.temperature_c is None
    assert telemetry.utilization_percent is None
    assert telemetry.power_w is None
    assert telemetry.memory_used_mb == 100
    assert telemetry.memory_total_mb == 200
    assert telemetry.graphics_clock_mhz is None


def test_collect_nvidia_gpu_returns_unavailable_when_command_missing():
    def run_missing(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    telemetry = collect_nvidia_gpu(run=run_missing)

    assert telemetry.available is False
    assert telemetry.name == "NVIDIA GPU"
    assert "nvidia-smi" in telemetry.error


def test_collect_nvidia_gpu_returns_unavailable_on_command_failure():
    def run_failed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], returncode=1, stdout="", stderr="driver not loaded")

    telemetry = collect_nvidia_gpu(run=run_failed)

    assert telemetry.available is False
    assert telemetry.error == "driver not loaded"


def test_collect_cpu_temperature_from_hwmon_prefers_package_label(tmp_path: Path):
    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("coretemp\n", encoding="utf-8")
    (hwmon / "temp1_label").write_text("Package id 0\n", encoding="utf-8")
    (hwmon / "temp1_input").write_text("54875\n", encoding="utf-8")
    (hwmon / "temp2_label").write_text("Core 0\n", encoding="utf-8")
    (hwmon / "temp2_input").write_text("50000\n", encoding="utf-8")

    telemetry = collect_cpu_temperature_from_hwmon(tmp_path)

    assert telemetry.available is True
    assert telemetry.package_temperature_c == 54.875
    assert telemetry.error == ""


def test_collect_cpu_temperature_from_hwmon_ignores_non_cpu_hwmon_when_cpu_exists(tmp_path: Path):
    nvme = tmp_path / "hwmon0"
    nvme.mkdir()
    (nvme / "name").write_text("nvme\n", encoding="utf-8")
    (nvme / "temp1_input").write_text("70000\n", encoding="utf-8")

    cpu = tmp_path / "hwmon1"
    cpu.mkdir()
    (cpu / "name").write_text("coretemp\n", encoding="utf-8")
    (cpu / "temp1_label").write_text("Package id 0\n", encoding="utf-8")
    (cpu / "temp1_input").write_text("55000\n", encoding="utf-8")

    telemetry = collect_cpu_temperature_from_hwmon(tmp_path)

    assert telemetry.available is True
    assert telemetry.package_temperature_c == 55.0
    assert telemetry.error == ""


def test_collect_cpu_temperature_from_hwmon_returns_unavailable_for_non_cpu_hwmon(tmp_path: Path):
    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nvme\n", encoding="utf-8")
    (hwmon / "temp1_input").write_text("70000\n", encoding="utf-8")

    telemetry = collect_cpu_temperature_from_hwmon(tmp_path)

    assert telemetry.available is False
    assert telemetry.package_temperature_c is None
    assert "CPU temperature sensor" in telemetry.error


@pytest.mark.parametrize("label", ["Package id 0", "Tdie", "Tctl", "CPU"])
def test_collect_cpu_temperature_from_hwmon_prefers_cpu_labels(tmp_path: Path, label: str):
    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("coretemp\n", encoding="utf-8")
    (hwmon / "temp1_label").write_text("Core 0\n", encoding="utf-8")
    (hwmon / "temp1_input").write_text("50000\n", encoding="utf-8")
    (hwmon / "temp2_label").write_text(f"{label}\n", encoding="utf-8")
    (hwmon / "temp2_input").write_text("62500\n", encoding="utf-8")

    telemetry = collect_cpu_temperature_from_hwmon(tmp_path)

    assert telemetry.available is True
    assert telemetry.package_temperature_c == 62.5
    assert telemetry.error == ""


def test_collect_cpu_temperature_from_hwmon_returns_unavailable_when_missing(tmp_path: Path):
    telemetry = collect_cpu_temperature_from_hwmon(tmp_path)

    assert telemetry.available is False
    assert telemetry.package_temperature_c is None
    assert "temperature" in telemetry.error


def test_collect_system_telemetry_combines_cpu_gpu_and_timestamp():
    now = datetime(2026, 5, 20, 12, 0, 0)

    telemetry = collect_system_telemetry(
        cpu_collector=lambda: CpuTelemetry(package_temperature_c=50.0, available=True),
        gpu_collector=lambda: GpuTelemetry(name="RTX", temperature_c=61, available=True),
        clock=lambda: now,
    )

    assert telemetry.cpu.package_temperature_c == 50.0
    assert telemetry.gpu.temperature_c == 61
    assert telemetry.captured_at == now
