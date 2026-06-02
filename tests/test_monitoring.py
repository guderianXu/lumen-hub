from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from usb9_lcd.monitoring.cpu import (
    collect_cpu_temperature_from_hwmon,
    cpu_power_permission_paths,
    cpu_power_permission_shell,
)
from usb9_lcd.monitoring.models import CpuTelemetry, GpuTelemetry
from usb9_lcd.monitoring.nvidia import collect_nvidia_gpu, parse_nvidia_smi_csv
from usb9_lcd.monitoring.service import collect_system_telemetry
from usb9_lcd.monitoring.windows import collect_windows_fan_channels, set_windows_fan_control_percent


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


def test_collect_cpu_temperature_from_hwmon_reads_cpu_power_input(tmp_path: Path):
    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("zenpower\n", encoding="utf-8")
    (hwmon / "temp1_label").write_text("Tctl\n", encoding="utf-8")
    (hwmon / "temp1_input").write_text("54875\n", encoding="utf-8")
    (hwmon / "power1_label").write_text("PPT\n", encoding="utf-8")
    (hwmon / "power1_input").write_text("67500000\n", encoding="utf-8")

    telemetry = collect_cpu_temperature_from_hwmon(tmp_path, powercap_root=tmp_path / "powercap")

    assert telemetry.available is True
    assert telemetry.package_temperature_c == 54.875
    assert telemetry.power_w == 67.5
    assert telemetry.error == ""


def test_collect_cpu_temperature_from_hwmon_reads_linux_cpu_utilization(tmp_path: Path):
    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("coretemp\n", encoding="utf-8")
    (hwmon / "temp1_label").write_text("Package id 0\n", encoding="utf-8")
    (hwmon / "temp1_input").write_text("54875\n", encoding="utf-8")
    proc_stat = tmp_path / "proc_stat"
    proc_stat.write_text("cpu 100 0 100 800 0 0 0 0 0 0\n", encoding="utf-8")
    cache: dict[str, tuple[int, int]] = {}

    def write_second_sample(_seconds: float) -> None:
        proc_stat.write_text("cpu 150 0 150 900 0 0 0 0 0 0\n", encoding="utf-8")

    telemetry = collect_cpu_temperature_from_hwmon(
        tmp_path,
        powercap_root=tmp_path / "powercap",
        proc_stat_path=proc_stat,
        proc_stat_cache=cache,
        sleep_func=write_second_sample,
    )

    assert telemetry.package_temperature_c == 54.875
    assert telemetry.utilization_percent == 50.0
    assert telemetry.error == ""


def test_collect_cpu_temperature_from_hwmon_estimates_power_from_powercap_delta(tmp_path: Path):
    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("coretemp\n", encoding="utf-8")
    (hwmon / "temp1_label").write_text("Package id 0\n", encoding="utf-8")
    (hwmon / "temp1_input").write_text("50000\n", encoding="utf-8")
    powercap_root = tmp_path / "powercap"
    rapl = powercap_root / "intel-rapl:0"
    rapl.mkdir(parents=True)
    (rapl / "name").write_text("package-0\n", encoding="utf-8")
    (rapl / "energy_uj").write_text("100000000\n", encoding="utf-8")
    (rapl / "max_energy_range_uj").write_text("1000000000\n", encoding="utf-8")
    cache: dict[str, tuple[int, float, int]] = {}
    times = iter([10.0, 12.0])

    first = collect_cpu_temperature_from_hwmon(
        tmp_path,
        powercap_root=powercap_root,
        monotonic_clock=lambda: next(times),
        powercap_cache=cache,
    )
    (rapl / "energy_uj").write_text("130000000\n", encoding="utf-8")
    second = collect_cpu_temperature_from_hwmon(
        tmp_path,
        powercap_root=powercap_root,
        monotonic_clock=lambda: next(times),
        powercap_cache=cache,
    )

    assert first.power_w is None
    assert second.power_w == 15.0


def test_cpu_power_permission_paths_and_shell_target_unreadable_powercap_files(tmp_path: Path):
    powercap_root = tmp_path / "powercap"
    rapl = powercap_root / "intel-rapl:0"
    rapl.mkdir(parents=True)
    energy = rapl / "energy_uj"
    max_range = rapl / "max_energy_range_uj"
    (rapl / "name").write_text("package-0\n", encoding="utf-8")
    energy.write_text("100000000\n", encoding="utf-8")
    max_range.write_text("1000000000\n", encoding="utf-8")

    paths = cpu_power_permission_paths(powercap_root, access_checker=lambda path, _mode: path != energy)
    shell = cpu_power_permission_shell(paths)

    assert paths == [energy]
    assert "chown" in shell
    assert "chmod u+r,g+r" in shell
    assert str(energy) in shell
    assert "cpu-power-permissions=ok files=1" in shell


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

    telemetry = collect_cpu_temperature_from_hwmon(
        tmp_path,
        powercap_root=tmp_path / "missing_powercap",
        proc_stat_path=tmp_path / "missing_proc_stat",
    )

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
    telemetry = collect_cpu_temperature_from_hwmon(
        tmp_path,
        powercap_root=tmp_path / "missing_powercap",
        proc_stat_path=tmp_path / "missing_proc_stat",
    )

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


def test_windows_cpu_power_prefers_cpu_power_sensor(monkeypatch):
    import usb9_lcd.monitoring.windows as windows

    monkeypatch.setattr(
        windows,
        "_hardware_sensor_data",
        lambda sensor_types, name_pattern="": [
            {
                "Name": "CPU Package",
                "SensorType": "Power",
                "Value": 88.4,
                "HardwareName": "AMD Ryzen 9",
                "HardwareType": "Cpu",
            },
            {
                "Name": "GPU Package",
                "SensorType": "Power",
                "Value": 300.0,
                "HardwareName": "NVIDIA RTX",
                "HardwareType": "GpuNvidia",
            },
        ],
    )

    assert windows._cpu_power_from_hardware_monitor() == 88.4


def test_collect_windows_fan_channels_pairs_rpm_and_control_sensors():
    data = [
        {
            "Name": "Fan #1",
            "SensorType": "Fan",
            "Value": 1234,
            "HardwareName": "Nuvoton NCT6799D",
            "HardwareType": "SuperIO",
            "Identifier": "/lpc/nct6799d/fan/0",
            "Source": "LibreHardwareMonitorLib",
        },
        {
            "Name": "Fan Control #1",
            "SensorType": "Control",
            "Value": 45,
            "HardwareName": "Nuvoton NCT6799D",
            "HardwareType": "SuperIO",
            "Identifier": "/lpc/nct6799d/control/0",
            "Source": "LibreHardwareMonitorLib",
        },
    ]

    channels = collect_windows_fan_channels(sensor_data_provider=lambda *_args: data)

    assert len(channels) == 1
    assert channels[0].name == "Nuvoton NCT6799D Fan #1"
    assert channels[0].rpm == 1234
    assert channels[0].percent == 45
    assert channels[0].control_available is True
    assert channels[0].control_id == "/lpc/nct6799d/control/0"


def test_collect_windows_fan_channels_keeps_readonly_fans_visible():
    data = [
        {
            "Name": "GPU Fan 1",
            "SensorType": "Fan",
            "Value": 701,
            "HardwareName": "NVIDIA GeForce RTX 5080",
            "HardwareType": "GpuNvidia",
            "Identifier": "/gpu-nvidia/0/fan/0",
            "Source": "LibreHardwareMonitorLib",
        }
    ]

    channels = collect_windows_fan_channels(sensor_data_provider=lambda *_args: data)

    assert len(channels) == 1
    assert channels[0].name == "NVIDIA GeForce RTX 5080 GPU Fan 1"
    assert channels[0].rpm == 701
    assert channels[0].control_available is False
    assert "No matching Control" in channels[0].control_reason


def test_collect_windows_fan_channels_does_not_enable_gpu_control_on_motherboard_page():
    data = [
        {
            "Name": "GPU Fan 1",
            "SensorType": "Fan",
            "Value": 701,
            "HardwareName": "NVIDIA GeForce RTX 5080",
            "HardwareType": "GpuNvidia",
            "Identifier": "/gpu-nvidia/0/fan/0",
            "Source": "LibreHardwareMonitorLib",
        },
        {
            "Name": "GPU Fan Control 1",
            "SensorType": "Control",
            "Value": 30,
            "HardwareName": "NVIDIA GeForce RTX 5080",
            "HardwareType": "GpuNvidia",
            "Identifier": "/gpu-nvidia/0/control/0",
            "Source": "LibreHardwareMonitorLib",
        },
    ]

    channels = collect_windows_fan_channels(sensor_data_provider=lambda *_args: data)

    assert channels[0].control_available is False
    assert channels[0].control_id == "/gpu-nvidia/0/control/0"
    assert "GPU fan control" in channels[0].control_reason


def test_set_windows_fan_control_percent_rejects_unsafe_low_values():
    calls = []

    def runner(script, *, timeout=8):
        calls.append((script, timeout))
        return {"ok": True}

    try:
        set_windows_fan_control_percent("/lpc/nct6799d/control/0", 20, runner=runner)
    except ValueError as error:
        assert "30" in str(error)
    else:
        raise AssertionError("expected low fan control value to be rejected")

    assert calls == []


def test_set_windows_fan_control_percent_targets_identifier_with_software_mode():
    calls = []

    def runner(script, *, timeout=8):
        calls.append((script, timeout))
        return {"ok": True}

    set_windows_fan_control_percent("/lpc/nct6799d/control/0", 44, runner=runner)

    assert len(calls) == 1
    script, timeout = calls[0]
    assert timeout == 20
    assert "/lpc/nct6799d/control/0" in script
    assert "SetSoftware(44)" in script
