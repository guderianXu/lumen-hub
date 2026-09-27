from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
_ASUS_FAN_CONTROL_CLSID = "{14083C53-B8E7-48E4-9320-811F3478C4A4}"
_ASUS_FAN_CONTROL_PREFIX = "asus-fan://"
_ASUS_FAN_LOCK = threading.Lock()
_ASUS_FAN_LAST_ERROR = ""
_ASUS_TEMPERATURE_PROBE_LOGGED = False
_LOGGER = logging.getLogger(__name__)


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
    *,
    asus_channel_provider: Callable[[], list[WindowsFanChannel]] | None = None,
) -> list[WindowsFanChannel]:
    resolved_asus_provider = asus_channel_provider
    if resolved_asus_provider is None and sensor_data_provider is _hardware_sensor_data:
        resolved_asus_provider = _collect_asus_fan_channels
    if resolved_asus_provider is not None:
        try:
            asus_channels = resolved_asus_provider()
        except Exception:  # noqa: BLE001 - generic sensor probing remains the cross-vendor fallback.
            asus_channels = []
        if asus_channels:
            return asus_channels

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
    asus_manager_factory: Callable[[], Any] | None = None,
) -> None:
    resolved_percent = int(percent)
    if not 30 <= resolved_percent <= 100:
        raise ValueError("Windows fan control percent must be between 30 and 100")
    resolved_control_id = str(control_id).strip()
    if not resolved_control_id:
        raise ValueError("Windows fan control requires a control identifier")
    if resolved_control_id.startswith(_ASUS_FAN_CONTROL_PREFIX):
        _set_asus_fan_control_percent(
            resolved_control_id,
            resolved_percent,
            manager_factory=asus_manager_factory,
        )
        return

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


def apply_asus_fan_profile(
    preset: str,
    *,
    manager_factory: Callable[[], Any] | None = None,
) -> int:
    aliases = {
        "quiet": ("silent", "quiet"),
        "normal": ("standard", "normal"),
        "high": ("turbo", "performance", "high"),
        "full": ("fullspeed", "full", "maximum", "max"),
    }
    key = str(preset).strip().casefold()
    if key not in aliases:
        raise ValueError(f"Unsupported ASUS fan profile: {preset}")

    applied = 0
    with _ASUS_FAN_LOCK:
        with _asus_fan_manager(manager_factory) as manager:
            controls = manager.Controls
            for control_index in range(int(controls.Count)):
                control = _com_collection_item(controls, control_index)
                profiles = control.Profiles
                available: list[str] = []
                selected_index: int | None = None
                for profile_index in range(int(profiles.Count)):
                    profile = _com_collection_item(profiles, profile_index)
                    name = str(_safe_com_value(lambda profile=profile: profile.Name, "") or "")
                    available.append(name)
                    normalized = re.sub(r"[^a-z0-9]+", "", name.casefold())
                    if selected_index is None and any(alias in normalized for alias in aliases[key]):
                        selected_index = profile_index
                if selected_index is None:
                    raise RuntimeError(
                        f"ASUS fan channel {control_index} has no {preset} profile; available: {available}"
                    )
                control.EnableManualMode(False)
                control.ApplyIndex(selected_index)
                applied += 1
                _LOGGER.info(
                    "ASUS fan profile applied index=%s preset=%s profile_index=%s profile_name=%s",
                    control_index,
                    key,
                    selected_index,
                    available[selected_index],
                )
    return applied


@contextmanager
def _asus_fan_manager(
    manager_factory: Callable[[], Any] | None = None,
) -> Iterator[Any]:
    if manager_factory is not None:
        yield manager_factory()
        return
    if not sys.platform.startswith("win"):
        raise RuntimeError("ASUS Fan Xpert control is only available on Windows")

    try:
        import comtypes
        import comtypes.client
    except ImportError as exc:
        raise RuntimeError("comtypes is required for ASUS Fan Xpert control") from exc

    logging.getLogger("comtypes").setLevel(logging.WARNING)
    comtypes.CoInitialize()
    manager = None
    try:
        manager = comtypes.client.CreateObject(_ASUS_FAN_CONTROL_CLSID, dynamic=True)
        yield manager
    finally:
        manager = None
        comtypes.CoUninitialize()


def _com_collection_item(collection: Any, index: int) -> Any:
    return _com_indexed_member(collection, "Item", index)


def _com_indexed_member(target: Any, member_name: str, index: int) -> Any:
    accessor = getattr(target, member_name)
    attempts = (
        lambda: accessor(index),
        lambda: accessor[index],
        lambda: target(index),
        lambda: target[index],
    )
    last_error: Exception | None = None
    for attempt in attempts:
        try:
            return attempt()
        except Exception as exc:  # noqa: BLE001 - COM collections vary between wrappers.
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"ASUS COM member {member_name}[{index}] was not found")


def _safe_com_value(reader: Callable[[], Any], default: Any = None) -> Any:
    try:
        return reader()
    except Exception:  # noqa: BLE001 - optional ASUS properties vary by board generation.
        return default


def _collect_asus_fan_channels(
    manager_factory: Callable[[], Any] | None = None,
) -> list[WindowsFanChannel]:
    global _ASUS_FAN_LAST_ERROR

    with _ASUS_FAN_LOCK:
        try:
            with _asus_fan_manager(manager_factory) as manager:
                controls = manager.Controls
                count = int(controls.Count)
                channels: list[WindowsFanChannel] = []
                for index in range(count):
                    control = _com_collection_item(controls, index)
                    raw_duty = _safe_com_value(lambda: int(control.DutyCycle))
                    minimum_duty = _safe_com_value(lambda: int(control.MinimalDuty), 0)
                    service_name = str(_safe_com_value(lambda: control.Name, "") or "")
                    display_name = str(_safe_com_value(lambda: control.DisplayName, "") or "")
                    name = _asus_fan_display_name(display_name or service_name, index)
                    minimum_percent = round(max(0, min(255, minimum_duty)) * 100 / 255)
                    channels.append(
                        WindowsFanChannel(
                            name=name,
                            percent=(
                                round(max(0, min(255, raw_duty)) * 100 / 255, 1)
                                if raw_duty is not None
                                else None
                            ),
                            control_id=f"{_ASUS_FAN_CONTROL_PREFIX}{index}",
                            control_available=True,
                            control_reason=(
                                "ASUS Fan Xpert control available"
                                + (f"; channel minimum {minimum_percent}%" if minimum_percent else "")
                            ),
                            hardware_name="ASUS Fan Xpert",
                            hardware_type="Motherboard",
                            source="AsusFanControlService",
                        )
                    )
            _ASUS_FAN_LAST_ERROR = ""
            return channels
        except Exception as exc:  # noqa: BLE001 - callers fall back to cross-vendor probing.
            _ASUS_FAN_LAST_ERROR = f"{type(exc).__name__}: {exc}"
            return []


def _set_asus_fan_control_percent(
    control_id: str,
    percent: int,
    *,
    manager_factory: Callable[[], Any] | None = None,
) -> None:
    index_text = control_id.removeprefix(_ASUS_FAN_CONTROL_PREFIX).strip().strip("/")
    if not index_text.isdigit():
        raise ValueError(f"Invalid ASUS fan control identifier: {control_id}")
    index = int(index_text)

    with _ASUS_FAN_LOCK:
        with _asus_fan_manager(manager_factory) as manager:
            controls = manager.Controls
            count = int(controls.Count)
            if index < 0 or index >= count:
                raise RuntimeError(f"ASUS fan control index {index} is no longer available")
            control = _com_collection_item(controls, index)
            minimum_duty = int(_safe_com_value(lambda: control.MinimalDuty, 0) or 0)
            control_name = str(_safe_com_value(lambda: control.Name, "") or "")
            if "PUMP" in re.sub(r"[^A-Z0-9]+", "", control_name.upper()):
                minimum_duty = max(minimum_duty, round(0.8 * 255))
            requested_duty = round(percent * 255 / 100)
            safe_duty = max(requested_duty, max(0, min(255, minimum_duty)))
            control.EnableManualMode(True)
            control.DutyCycle = safe_duty
            _LOGGER.info(
                "ASUS fan duty applied index=%s requested_percent=%s duty=%s minimum_duty=%s",
                index,
                percent,
                safe_duty,
                minimum_duty,
            )


def _asus_fan_display_name(value: str, index: int) -> str:
    compact = re.sub(r"[^A-Z0-9]+", "", value.upper())
    names = {
        "CPUFAN": "CPU Fan",
        "CPUOPTFAN": "CPU OPT Fan",
        "CHASSISFAN1": "Chassis Fan 1",
        "CHASSISFAN2": "Chassis Fan 2",
        "CHASSISFAN3": "Chassis Fan 3",
        "ECCHASSISFAN4": "Chassis Fan 4",
        "ECCHASSISFAN5": "Chassis Fan 5",
        "AIOPUMPFAN": "AIO Pump",
        "WPUMP1": "Water Pump 1",
        "WPUMP2": "Water Pump 2",
        "HAMPFAN": "High Amp Fan",
    }
    return names.get(compact, value.strip() or f"ASUS Fan {index + 1}")


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


def _cpu_temperature_from_asus_fan_service(
    manager_factory: Callable[[], Any] | None = None,
) -> float | None:
    global _ASUS_TEMPERATURE_PROBE_LOGGED

    if manager_factory is None and not sys.platform.startswith("win"):
        return None
    with _ASUS_FAN_LOCK:
        try:
            with _asus_fan_manager(manager_factory) as manager:
                count = int(_safe_com_value(lambda: manager.FanCount, 0) or 0)
                readings: list[float] = []
                raw_values: dict[str, list[Any]] = {}
                for member_name in ("AIFanCpuTemperature", "AIFanCpuTempIn"):
                    member_values: list[Any] = []
                    for index in range(count):
                        value = _safe_com_value(
                            lambda index=index, member_name=member_name: _com_indexed_member(
                                manager,
                                member_name,
                                index,
                            )
                        )
                        member_values.append(value)
                        parsed = _normalize_asus_temperature(value)
                        if parsed is not None:
                            readings.append(parsed)
                    raw_values[member_name] = member_values
                if not _ASUS_TEMPERATURE_PROBE_LOGGED:
                    _LOGGER.info("ASUS fan temperature probe raw=%s normalized=%s", raw_values, readings)
                    _ASUS_TEMPERATURE_PROBE_LOGGED = True
                return max(readings) if readings else None
        except Exception:  # noqa: BLE001 - generic hardware monitoring remains the fallback.
            return None


def _normalize_asus_temperature(value: Any) -> float | None:
    parsed = _first_number(value)
    if parsed is None or parsed <= 0:
        return None
    for divisor in (1, 10, 100, 1000):
        candidate = parsed / divisor
        if 5 <= candidate < 130:
            return candidate
    return None


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

    for collector in (
        _cpu_temperature_from_asus_fan_service,
        _cpu_temperature_from_hardware_monitor,
        _cpu_temperature_from_acpi,
    ):
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
