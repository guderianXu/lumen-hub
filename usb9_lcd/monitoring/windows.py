from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from usb9_lcd.platforms.process import hidden_subprocess_kwargs

from .models import CpuTelemetry, FanTelemetry


POWERSHELL = "powershell.exe"
_LHM_SENSOR_TYPES = ("Temperature", "Power", "Fan", "Control")
_LHM_CACHE_SECONDS = 2.5
_LHM_PROBE_LOCK = threading.Lock()
_LHM_SENSOR_CACHE: tuple[float, list[dict[str, Any]]] = (0.0, [])


@dataclass(frozen=True)
class WindowsFanChannel:
    name: str
    rpm: int | None = None
    percent: float | None = None
    control_id: str = ""
    control_available: bool = False
    control_reason: str = ""
    hardware_name: str = ""
    hardware_type: str = ""
    source: str = ""


def _run_powershell_json(script: str, *, timeout: int = 8) -> Any:
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-Command",
            script,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        **hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "").strip() or f"PowerShell exited {result.returncode}")
    text = result.stdout.strip()
    if not text:
        return None
    return json.loads(text)


def _first_number(value: Any) -> float | None:
    if isinstance(value, list):
        for item in value:
            parsed = _first_number(item)
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, dict):
        for key in ("Value", "LoadPercentage", "CurrentTemperature"):
            if key in value:
                return _first_number(value[key])
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hardware_sensor_script(sensor_type: str, name_pattern: str = "") -> str:
    pattern = name_pattern.replace("'", "''")
    return f"""
$items = @()
foreach ($namespace in @('root\\LibreHardwareMonitor', 'root\\OpenHardwareMonitor')) {{
  try {{
    $items += Get-CimInstance -Namespace $namespace -ClassName Sensor -ErrorAction Stop |
      Where-Object {{ $_.SensorType -eq '{sensor_type}' -and ('{pattern}' -eq '' -or $_.Name -match '{pattern}' -or $_.HardwareType -match 'CPU') }} |
      Select-Object Name, SensorType, Value, HardwareName, HardwareType
  }} catch {{}}
}}
$items | ConvertTo-Json -Depth 4
"""


def _libre_hardware_monitor_sensor_script(
    sensor_types: tuple[str, ...],
    name_pattern: str = "",
    *,
    dll_path: Path | None = None,
) -> str:
    type_items = ", ".join(f"'{item}'" for item in sensor_types)
    pattern = name_pattern.replace("'", "''")
    escaped_dll_path = str(dll_path or "").replace("'", "''")
    return f"""
$dllPath = '{escaped_dll_path}'
if (-not $dllPath -or -not (Test-Path -LiteralPath $dllPath)) {{
  @() | ConvertTo-Json -Depth 4
  exit 0
}}

Add-Type -LiteralPath $dllPath
$computer = [LibreHardwareMonitor.Hardware.Computer]::new()
$computer.IsCpuEnabled = $true
$computer.IsGpuEnabled = $true
$computer.IsMotherboardEnabled = $true
$computer.IsControllerEnabled = $true
$computer.IsMemoryEnabled = $true
$computer.IsStorageEnabled = $true
$computer.Open()
Start-Sleep -Milliseconds 400
$wanted = @({type_items})
$items = @()
foreach ($hardware in $computer.Hardware) {{
  $hardware.Update()
  foreach ($sub in $hardware.SubHardware) {{ $sub.Update() }}
  foreach ($node in @($hardware) + @($hardware.SubHardware)) {{
    foreach ($sensor in $node.Sensors) {{
      $sensorType = [string]$sensor.SensorType
      if ($wanted -notcontains $sensorType) {{ continue }}
      if ('{pattern}' -ne '' -and $sensor.Name -notmatch '{pattern}' -and $node.Name -notmatch '{pattern}') {{ continue }}
        $items += [pscustomobject]@{{
        Name = $sensor.Name
        SensorType = $sensorType
        Value = $sensor.Value
        HardwareName = $node.Name
        HardwareType = [string]$node.HardwareType
        Identifier = [string]$sensor.Identifier
        Source = 'LibreHardwareMonitorLib'
      }}
    }}
  }}
}}
$computer.Close()
$items | ConvertTo-Json -Depth 4
"""


@lru_cache(maxsize=1)
def _resolve_lhm_dll_path() -> Path | None:
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    candidates = [
        Path(value)
        for value in (
            os.environ.get("LIBREHARDWAREMONITOR_DLL", ""),
            str(program_files / "LibreHardwareMonitor/LibreHardwareMonitorLib.dll"),
            str(program_files_x86 / "LibreHardwareMonitor/LibreHardwareMonitorLib.dll"),
            str(program_files_x86 / "ASUS/GameFirst/LibreHardwareMonitorLib.dll"),
            str(local_app_data / "Programs/LibreHardwareMonitor/LibreHardwareMonitorLib.dll"),
        )
        if value
    ]
    winget_root = local_app_data / "Microsoft/WinGet/Packages"
    if winget_root.is_dir():
        candidates.extend(winget_root.glob("*/LibreHardwareMonitorLib.dll"))
    return next((path.resolve() for path in candidates if path.is_file()), None)


def _direct_lhm_sensor_data() -> list[dict[str, Any]]:
    global _LHM_SENSOR_CACHE

    with _LHM_PROBE_LOCK:
        cached_at, cached_items = _LHM_SENSOR_CACHE
        now = time.monotonic()
        if cached_items and now - cached_at <= _LHM_CACHE_SECONDS:
            return list(cached_items)
        dll_path = _resolve_lhm_dll_path()
        if dll_path is None:
            return []
        data = _run_powershell_json(
            _libre_hardware_monitor_sensor_script(_LHM_SENSOR_TYPES, dll_path=dll_path),
            timeout=12,
        )
        items = [item for item in _as_list(data) if isinstance(item, dict)]
        _LHM_SENSOR_CACHE = (time.monotonic(), items)
        return list(items)


def _filter_sensor_data(
    items: list[dict[str, Any]],
    sensor_types: tuple[str, ...],
    name_pattern: str,
) -> list[dict[str, Any]]:
    wanted = set(sensor_types)
    pattern = re.compile(name_pattern, re.IGNORECASE) if name_pattern else None
    matches: list[dict[str, Any]] = []
    for item in items:
        if str(item.get("SensorType") or "") not in wanted:
            continue
        if pattern is not None:
            text = " ".join(
                str(item.get(key) or "") for key in ("Name", "HardwareName", "HardwareType")
            )
            if pattern.search(text) is None:
                continue
        matches.append(item)
    return matches


def _as_list(data: Any) -> list[Any]:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    return [data]


def _hardware_sensor_data(sensor_types: tuple[str, ...], name_pattern: str = "") -> list[dict[str, Any]]:
    try:
        direct_items = _filter_sensor_data(_direct_lhm_sensor_data(), sensor_types, name_pattern)
    except Exception:  # noqa: BLE001 - WMI remains a useful fallback when the local driver is busy.
        direct_items = []
    if direct_items:
        return direct_items

    items: list[dict[str, Any]] = []
    for sensor_type in sensor_types:
        try:
            data = _run_powershell_json(_hardware_sensor_script(sensor_type, name_pattern), timeout=10)
        except Exception:  # noqa: BLE001 - fall back to direct LibreHardwareMonitorLib probing.
            data = None
        for item in _as_list(data):
            if isinstance(item, dict):
                items.append(item)
    if items:
        return items

    return items


def collect_windows_fan_channels(
    sensor_data_provider=_hardware_sensor_data,  # noqa: ANN001
) -> list[WindowsFanChannel]:
    data = sensor_data_provider(("Fan", "Control"))
    fan_items = [item for item in data if str(item.get("SensorType") or "") == "Fan"]
    control_items = [item for item in data if str(item.get("SensorType") or "") == "Control"]
    controls_by_key = {_sensor_pair_key(item): item for item in control_items}

    channels: list[WindowsFanChannel] = []
    for item in fan_items:
        value = _first_number(item)
        if value is None:
            continue
        control = controls_by_key.get(_sensor_pair_key(item))
        control_value = _first_number(control) if control is not None else None
        control_id = str(control.get("Identifier") or "") if control is not None else ""
        hardware = str(item.get("HardwareName") or "Hardware")
        hardware_type = str(item.get("HardwareType") or "")
        sensor_name = str(item.get("Name") or "Fan")
        control_allowed = bool(control_id) and not _is_gpu_hardware(hardware_type, hardware)
        channels.append(
            WindowsFanChannel(
                name=f"{hardware} {sensor_name}".strip(),
                rpm=round(value),
                percent=round(control_value, 1) if control_value is not None else None,
                control_id=control_id,
                control_available=control_allowed,
                control_reason=(
                    _windows_fan_control_reason(control_id, hardware_type, hardware)
                ),
                hardware_name=hardware,
                hardware_type=hardware_type,
                source=str(item.get("Source") or ""),
            )
        )

    if channels:
        return channels

    for item in control_items:
        value = _first_number(item)
        if value is None:
            continue
        hardware = str(item.get("HardwareName") or "Hardware")
        sensor_name = str(item.get("Name") or "Fan Control")
        control_id = str(item.get("Identifier") or "")
        channels.append(
            WindowsFanChannel(
                name=f"{hardware} {sensor_name}".strip(),
                percent=round(value, 1),
                control_id=control_id,
                control_available=bool(control_id),
                control_reason=(
                    "Control sensor exposed without a matching RPM sensor"
                    if control_id
                    else "Control sensor has no stable identifier"
                ),
                hardware_name=hardware,
                hardware_type=str(item.get("HardwareType") or ""),
                source=str(item.get("Source") or ""),
            )
        )
    return channels


def set_windows_fan_control_percent(
    control_id: str,
    percent: int,
    *,
    runner=_run_powershell_json,  # noqa: ANN001
) -> None:
    resolved_percent = int(percent)
    if not 30 <= resolved_percent <= 100:
        raise ValueError("Windows fan control percent must be between 30 and 100")
    resolved_control_id = str(control_id).strip()
    if not resolved_control_id:
        raise ValueError("Windows fan control requires a LibreHardwareMonitor control identifier")

    dll_path = _resolve_lhm_dll_path()
    if dll_path is None:
        raise RuntimeError("LibreHardwareMonitorLib.dll was not found")

    escaped_id = resolved_control_id.replace("'", "''")
    escaped_dll_path = str(dll_path).replace("'", "''")
    script = f"""
$dllPath = '{escaped_dll_path}'
if (-not (Test-Path -LiteralPath $dllPath)) {{
  throw 'LibreHardwareMonitorLib.dll was not found'
}}

Add-Type -LiteralPath $dllPath
$computer = [LibreHardwareMonitor.Hardware.Computer]::new()
$computer.IsMotherboardEnabled = $true
$computer.IsControllerEnabled = $true
$computer.Open()
Start-Sleep -Milliseconds 400
$target = $null
foreach ($hardware in $computer.Hardware) {{
  $hardware.Update()
  foreach ($sub in $hardware.SubHardware) {{ $sub.Update() }}
  foreach ($node in @($hardware) + @($hardware.SubHardware)) {{
    foreach ($sensor in $node.Sensors) {{
      if ([string]$sensor.Identifier -eq '{escaped_id}' -and [string]$sensor.SensorType -eq 'Control') {{
        $target = $sensor
        break
      }}
    }}
    if ($target) {{ break }}
  }}
  if ($target) {{ break }}
}}
if (-not $target) {{
  $computer.Close()
  throw 'LibreHardwareMonitor control sensor was not found'
}}
if (-not $target.Control) {{
  $computer.Close()
  throw 'LibreHardwareMonitor control sensor is not writable'
}}
$target.Control.SetSoftware({resolved_percent})
$computer.Close()
[pscustomobject]@{{ ok = $true; identifier = '{escaped_id}'; percent = {resolved_percent} }} | ConvertTo-Json
"""
    global _LHM_SENSOR_CACHE
    with _LHM_PROBE_LOCK:
        runner(script, timeout=12)
        _LHM_SENSOR_CACHE = (0.0, [])


def _is_gpu_hardware(hardware_type: str, hardware_name: str) -> bool:
    text = f"{hardware_type} {hardware_name}".casefold()
    return "gpu" in text or "nvidia" in text or "radeon" in text


def _windows_fan_control_reason(control_id: str, hardware_type: str, hardware_name: str) -> str:
    if not control_id:
        return "No matching Control sensor exposed by LibreHardwareMonitor"
    if _is_gpu_hardware(hardware_type, hardware_name):
        return "GPU fan control detected, but the ordinary fan page only writes motherboard/controller fans"
    return "LibreHardwareMonitor control sensor available"


def _sensor_pair_key(item: dict[str, Any]) -> tuple[str, str]:
    hardware = str(item.get("HardwareName") or "").casefold()
    identifier = str(item.get("Identifier") or "")
    index = _trailing_sensor_index(identifier) or _trailing_sensor_index(str(item.get("Name") or "")) or "0"
    return hardware, index


def _trailing_sensor_index(value: str) -> str:
    digits = ""
    for character in reversed(value):
        if character.isdigit():
            digits = character + digits
            continue
        if digits:
            break
    return digits


def _cpu_load_percent() -> float | None:
    script = """
$value = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
[pscustomobject]@{ Value = $value } | ConvertTo-Json
"""
    return _first_number(_run_powershell_json(script))


def _cpu_temperature_from_hardware_monitor() -> float | None:
    readings: list[float] = []
    for item in _hardware_sensor_data(("Temperature",), "CPU|Package|Tctl|Tdie|Ryzen"):
        value = _first_number(item)
        if value is not None and 0 < value < 130:
            readings.append(value)
    return max(readings) if readings else None


def _cpu_power_from_hardware_monitor() -> float | None:
    readings: list[float] = []
    for item in _hardware_sensor_data(("Power",), "CPU|Package|Processor|PPT|Ryzen|Intel"):
        value = _first_number(item)
        if value is None or value < 0 or value > 2000:
            continue
        hardware = str(item.get("HardwareName") or "")
        hardware_type = str(item.get("HardwareType") or "")
        sensor_name = str(item.get("Name") or "")
        text = f"{hardware_type} {hardware} {sensor_name}".casefold()
        if _is_gpu_hardware(hardware_type, hardware) or "gpu" in text:
            continue
        if "cpu" in text or "processor" in text or "package" in text or "ppt" in text:
            readings.append(value)
    return max(readings) if readings else None


def _cpu_temperature_from_acpi() -> float | None:
    script = """
$items = Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue |
  Select-Object CurrentTemperature
$items | ConvertTo-Json -Depth 3
"""
    data = _run_powershell_json(script)
    value = _first_number(data)
    if value is None:
        return None
    celsius = (value / 10.0) - 273.15
    if celsius <= 0 or celsius > 130:
        return None
    return celsius


def collect_windows_cpu_telemetry() -> CpuTelemetry:
    load: float | None = None
    temperature: float | None = None
    power: float | None = None
    errors: list[str] = []

    try:
        load = _cpu_load_percent()
    except Exception as error:  # noqa: BLE001 - telemetry should degrade gracefully.
        errors.append(f"cpu load unavailable: {error}")

    try:
        power = _cpu_power_from_hardware_monitor()
    except Exception as error:  # noqa: BLE001 - telemetry should degrade gracefully.
        errors.append(f"cpu power unavailable: {error}")

    for collector in (_cpu_temperature_from_hardware_monitor, _cpu_temperature_from_acpi):
        try:
            temperature = collector()
        except Exception as error:  # noqa: BLE001
            errors.append(str(error))
            temperature = None
        if temperature is not None:
            break

    available = load is not None or temperature is not None or power is not None
    if not available and not errors:
        errors.append("no Windows CPU telemetry source available")
    if temperature is None:
        errors.append("CPU temperature requires LibreHardwareMonitor/OpenHardwareMonitor or ACPI thermal zone support")

    return CpuTelemetry(
        package_temperature_c=temperature,
        utilization_percent=round(load, 1) if load is not None else None,
        power_w=round(power, 1) if power is not None else None,
        available=available,
        error="; ".join(dict.fromkeys(error for error in errors if error)),
    )


def collect_windows_fans() -> list[FanTelemetry]:
    channels = collect_windows_fan_channels()
    fans = [
        FanTelemetry(
            name=channel.name,
            rpm=channel.rpm,
            percent=channel.percent,
            available=True,
            error="" if channel.control_available else channel.control_reason,
        )
        for channel in channels
    ]

    if fans:
        return fans
    return [
        FanTelemetry(
            name="Windows fan sensors",
            available=False,
            error="no ordinary fan RPM/control sensors exposed through LibreHardwareMonitorLib or WMI",
        )
    ]
