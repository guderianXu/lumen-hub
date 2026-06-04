from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from usb9_lcd.gui.fan_curve_model import (
    DEFAULT_FAN_CURVE_POINTS,
    normalize_fan_curve_preset,
    normalize_fan_curve_sensor_source,
    sanitize_fan_curve_points,
)
from usb9_lcd.platforms import current_platform

_PLATFORM = current_platform()
DEFAULT_SETTINGS_PATH = _PLATFORM.settings_path()
DEFAULT_OPENRGB_PATH = _PLATFORM.default_openrgb_path()
CONFIG_VERSION = 2


@dataclass
class LightingUiSettings:
    target_id: str = ""
    target_aliases: dict[str, str] = field(default_factory=dict)
    target_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    device_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_scene: str = ""
    scenes: dict[str, dict[str, Any]] = field(default_factory=dict)
    palette: str = "neon"
    effect: str = "off"
    color: str = "#000000"
    brightness_percent: int = 0
    speed: int = 5
    argb_zone_size: int = 30
    save_mode: bool = False
    sync_mode: int = 0
    temperature_limit: int = 75


@dataclass
class OpenRgbUiSettings:
    host: str = "127.0.0.1"
    port: int = 6742
    app_path: str = str(DEFAULT_OPENRGB_PATH)
    auto_start_server: bool = True


@dataclass
class MonitorUiSettings:
    live_interval_seconds: int = 1
    active_profile: str = ""
    palette: str = "neon"
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class HostFanUiSettings:
    curve_enabled: bool = False
    curve_interval_seconds: int = 3
    curve_preset: str = "normal"
    curve_points: list[list[int]] = field(default_factory=lambda: [list(point) for point in DEFAULT_FAN_CURVE_POINTS])
    curve_sensor_source: str = "cpu"
    curve_hysteresis_c: int = 2
    curve_minimum_percent: int = 20
    curve_fallback_percent: int = 100


@dataclass
class LianLiWirelessTargetSettings:
    mac: str = ""
    master_mac: str = ""
    channel: int = 8
    rx_type: int = 1
    device_type: int = 0
    fan_count: int = 4
    led_count: int = 26
    label: str = ""


@dataclass
class LianLiWirelessUiSettings:
    auto_connect: bool = True
    active_target_mac: str = ""
    targets: dict[str, LianLiWirelessTargetSettings] = field(default_factory=dict)
    effect: str = "off"
    color: str = "#00fe00"
    accent_color: str = "#ffffff"
    rotation_colors: str = "#fe0000,#00fe00,#0000fe,#ffd60a"
    brightness: int = 100
    speed: int = 75
    direction: str = "left"
    fan_mode: str = "full"
    fan_rpm: int = 1800
    pwm: int = 120
    fan_curve_points: list[list[int]] = field(default_factory=lambda: [[30, 450], [50, 900], [70, 1440], [85, 1800]])


@dataclass
class GuiSettings:
    config_version: int = CONFIG_VERSION
    lighting: LightingUiSettings = field(default_factory=LightingUiSettings)
    monitor: MonitorUiSettings = field(default_factory=MonitorUiSettings)
    host_fan: HostFanUiSettings = field(default_factory=HostFanUiSettings)
    openrgb: OpenRgbUiSettings = field(default_factory=OpenRgbUiSettings)
    lianli_wireless: LianLiWirelessUiSettings = field(default_factory=LianLiWirelessUiSettings)
    keepalive_enabled: bool = True


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> GuiSettings:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return GuiSettings()
    if not isinstance(payload, dict):
        return GuiSettings()
    payload = _migrate_settings_payload(payload)
    return GuiSettings(
        config_version=CONFIG_VERSION,
        lighting=_lighting_from_dict(payload.get("lighting")),
        monitor=_monitor_from_dict(payload.get("monitor")),
        host_fan=_host_fan_from_dict(payload.get("host_fan")),
        openrgb=_openrgb_from_dict(payload.get("openrgb")),
        lianli_wireless=_lianli_wireless_from_dict(payload.get("lianli_wireless")),
        keepalive_enabled=bool(payload.get("keepalive_enabled", True)),
    )


def save_settings(settings: GuiSettings, path: Path = DEFAULT_SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _lighting_from_dict(value: Any) -> LightingUiSettings:
    if not isinstance(value, dict):
        return LightingUiSettings()
    defaults = LightingUiSettings()
    aliases = value.get("target_aliases")
    profiles = value.get("target_profiles")
    device_profiles = value.get("device_profiles")
    scenes = value.get("scenes")
    return LightingUiSettings(
        target_id=str(value.get("target_id", defaults.target_id)),
        target_aliases={str(key): str(label) for key, label in aliases.items()} if isinstance(aliases, dict) else {},
        target_profiles=profiles if isinstance(profiles, dict) else {},
        device_profiles=device_profiles if isinstance(device_profiles, dict) else {},
        active_scene=str(value.get("active_scene", defaults.active_scene)),
        scenes=scenes if isinstance(scenes, dict) else {},
        palette=str(value.get("palette", defaults.palette)),
        effect=str(value.get("effect", defaults.effect)),
        color=str(value.get("color", defaults.color)),
        brightness_percent=_clamp_int(value.get("brightness_percent"), 0, 100, defaults.brightness_percent),
        speed=_clamp_int(value.get("speed"), 1, 10, defaults.speed),
        argb_zone_size=_clamp_int(value.get("argb_zone_size"), 1, 500, defaults.argb_zone_size),
        save_mode=bool(value.get("save_mode", defaults.save_mode)),
        sync_mode=_clamp_int(value.get("sync_mode"), 0, 5, defaults.sync_mode),
        temperature_limit=_clamp_int(value.get("temperature_limit"), 40, 100, defaults.temperature_limit),
    )


def _migrate_settings_payload(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(payload)
    version = _clamp_int(migrated.get("config_version"), 0, CONFIG_VERSION, 1)
    lighting = migrated.get("lighting")
    if not isinstance(lighting, dict):
        lighting = {}
    else:
        lighting = dict(lighting)
    if version < 2:
        lighting.setdefault("device_profiles", {})
    migrated["lighting"] = lighting
    migrated["config_version"] = CONFIG_VERSION
    return migrated


def _monitor_from_dict(value: Any) -> MonitorUiSettings:
    if not isinstance(value, dict):
        return MonitorUiSettings()
    defaults = MonitorUiSettings()
    return MonitorUiSettings(
        live_interval_seconds=_clamp_int(
            value.get("live_interval_seconds"),
            1,
            10,
            defaults.live_interval_seconds,
        ),
        active_profile=str(value.get("active_profile", "")),
        palette=str(value.get("palette", defaults.palette)),
        profiles=value.get("profiles") if isinstance(value.get("profiles"), dict) else {},
    )


def _host_fan_from_dict(value: Any) -> HostFanUiSettings:
    if not isinstance(value, dict):
        return HostFanUiSettings()
    defaults = HostFanUiSettings()
    return HostFanUiSettings(
        curve_enabled=bool(value.get("curve_enabled", defaults.curve_enabled)),
        curve_interval_seconds=_clamp_int(
            value.get("curve_interval_seconds"),
            1,
            60,
            defaults.curve_interval_seconds,
        ),
        curve_preset=normalize_fan_curve_preset(value.get("curve_preset", defaults.curve_preset)),
        curve_points=sanitize_fan_curve_points(value.get("curve_points"), defaults.curve_points),
        curve_sensor_source=normalize_fan_curve_sensor_source(
            value.get("curve_sensor_source", defaults.curve_sensor_source)
        ),
        curve_hysteresis_c=_clamp_int(value.get("curve_hysteresis_c"), 0, 20, defaults.curve_hysteresis_c),
        curve_minimum_percent=_clamp_int(
            value.get("curve_minimum_percent"),
            0,
            100,
            defaults.curve_minimum_percent,
        ),
        curve_fallback_percent=_clamp_int(
            value.get("curve_fallback_percent"),
            0,
            100,
            defaults.curve_fallback_percent,
        ),
    )


def _openrgb_from_dict(value: Any) -> OpenRgbUiSettings:
    if not isinstance(value, dict):
        return OpenRgbUiSettings()
    defaults = OpenRgbUiSettings()
    return OpenRgbUiSettings(
        host=str(value.get("host", defaults.host)),
        port=_clamp_int(value.get("port"), 1, 65535, defaults.port),
        app_path=str(value.get("app_path", defaults.app_path)),
        auto_start_server=bool(value.get("auto_start_server", defaults.auto_start_server)),
    )


def _lianli_wireless_from_dict(value: Any) -> LianLiWirelessUiSettings:
    if not isinstance(value, dict):
        return LianLiWirelessUiSettings()
    defaults = LianLiWirelessUiSettings()
    targets_payload = value.get("targets")
    targets: dict[str, LianLiWirelessTargetSettings] = {}
    if isinstance(targets_payload, dict):
        for mac, target_payload in targets_payload.items():
            if isinstance(target_payload, dict):
                target = _lianli_wireless_target_from_dict(target_payload)
                if target.mac:
                    targets[target.mac] = target
                else:
                    targets[str(mac)] = LianLiWirelessTargetSettings(**{**asdict(target), "mac": str(mac)})
    fan_rpm = _clamp_int(value.get("fan_rpm"), 0, 1800, defaults.fan_rpm)
    fan_mode = str(value.get("fan_mode", "")).strip().lower()
    if fan_mode not in {"quiet", "normal", "high", "full", "custom"}:
        fan_mode = _lianli_fan_mode_for_rpm(fan_rpm)
    return LianLiWirelessUiSettings(
        auto_connect=bool(value.get("auto_connect", defaults.auto_connect)),
        active_target_mac=str(value.get("active_target_mac", defaults.active_target_mac)),
        targets=targets,
        effect=str(value.get("effect", defaults.effect)),
        color=str(value.get("color", defaults.color)),
        accent_color=str(value.get("accent_color", defaults.accent_color)),
        rotation_colors=str(value.get("rotation_colors", defaults.rotation_colors)),
        brightness=_clamp_int(value.get("brightness"), 0, 100, defaults.brightness),
        speed=_clamp_int(value.get("speed"), 0, 100, defaults.speed),
        direction=str(value.get("direction", defaults.direction)),
        fan_mode=fan_mode,
        fan_rpm=fan_rpm,
        pwm=_clamp_int(value.get("pwm"), 40, 255, defaults.pwm),
        fan_curve_points=_lianli_fan_curve_points_from_dict(value.get("fan_curve_points"), defaults.fan_curve_points),
    )


def _lianli_wireless_target_from_dict(value: Any) -> LianLiWirelessTargetSettings:
    if not isinstance(value, dict):
        return LianLiWirelessTargetSettings()
    defaults = LianLiWirelessTargetSettings()
    return LianLiWirelessTargetSettings(
        mac=str(value.get("mac", defaults.mac)),
        master_mac=str(value.get("master_mac", defaults.master_mac)),
        channel=_clamp_int(value.get("channel"), 0, 255, defaults.channel),
        rx_type=_clamp_int(value.get("rx_type"), 0, 255, defaults.rx_type),
        device_type=_clamp_int(value.get("device_type"), 0, 255, defaults.device_type),
        fan_count=_clamp_int(value.get("fan_count"), 0, 16, defaults.fan_count),
        led_count=_clamp_int(value.get("led_count"), 1, 255, defaults.led_count),
        label=str(value.get("label", defaults.label)),
    )


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _lianli_fan_mode_for_rpm(rpm: int) -> str:
    presets = {
        600: "quiet",
        1000: "normal",
        1400: "high",
        1800: "full",
    }
    return presets.get(int(rpm), "custom")


def _lianli_fan_curve_points_from_dict(value: Any, default: list[list[int]]) -> list[list[int]]:
    if not isinstance(value, list):
        return [list(point) for point in default]
    points: list[list[int]] = []
    for item in value[:12]:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        temp = _clamp_int(item[0], 0, 100, 25)
        rpm = _clamp_int(item[1], 0, 1800, 600)
        points.append([temp, rpm])
    while len(points) < 2:
        points.append(list(default[len(points)]))
    return sorted(points, key=lambda point: point[0])
