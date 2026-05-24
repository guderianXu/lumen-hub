from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS_PATH = Path.home() / ".config" / "usb9-lcd" / "settings.json"
DEFAULT_OPENRGB_PATH = Path.home() / ".local" / "share" / "openrgb-usb9" / "squashfs-root" / "AppRun"


@dataclass
class LightingUiSettings:
    target_id: str = ""
    target_aliases: dict[str, str] = field(default_factory=dict)
    target_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
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
class GuiSettings:
    lighting: LightingUiSettings = field(default_factory=LightingUiSettings)
    monitor: MonitorUiSettings = field(default_factory=MonitorUiSettings)
    openrgb: OpenRgbUiSettings = field(default_factory=OpenRgbUiSettings)
    keepalive_enabled: bool = True


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> GuiSettings:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return GuiSettings()
    if not isinstance(payload, dict):
        return GuiSettings()
    return GuiSettings(
        lighting=_lighting_from_dict(payload.get("lighting")),
        monitor=_monitor_from_dict(payload.get("monitor")),
        openrgb=_openrgb_from_dict(payload.get("openrgb")),
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
    scenes = value.get("scenes")
    return LightingUiSettings(
        target_id=str(value.get("target_id", defaults.target_id)),
        target_aliases={str(key): str(label) for key, label in aliases.items()} if isinstance(aliases, dict) else {},
        target_profiles=profiles if isinstance(profiles, dict) else {},
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


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))
