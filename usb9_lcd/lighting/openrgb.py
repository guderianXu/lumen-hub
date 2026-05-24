from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class OpenRgbUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class LightingTarget:
    id: str
    name: str
    device_index: int
    zone_index: int | None
    modes: tuple[str, ...]


@dataclass(frozen=True)
class LightingSettings:
    target_id: str
    effect: str
    color: str
    brightness_percent: int
    speed_percent: int
    zone_size: int | None = None
    save: bool = False


class OpenRgbLightingController:
    def __init__(self, host: str = "127.0.0.1", port: int = 6742) -> None:
        self.host = host
        self.port = port
        self.client: Any | None = None
        self.targets: list[LightingTarget] = []

    @property
    def connected(self) -> bool:
        return self.client is not None

    def connect(self) -> list[LightingTarget]:
        try:
            from openrgb import OpenRGBClient
        except ImportError as error:
            raise OpenRgbUnavailableError(
                "缺少 openrgb-python。请运行 python -m pip install -e '.[dev]' 或 python -m pip install openrgb-python"
            ) from error

        try:
            self.client = OpenRGBClient(self.host, self.port, name="usb9-lcd")
        except OSError as error:
            self.client = None
            raise OpenRgbUnavailableError(
                f"无法连接 OpenRGB SDK Server {self.host}:{self.port}。请先启动 OpenRGB 并启用 SDK Server。"
            ) from error

        self.targets = self._build_targets()
        return list(self.targets)

    def disconnect(self) -> None:
        if self.client is not None:
            self.client.disconnect()
        self.client = None
        self.targets = []

    def refresh(self) -> list[LightingTarget]:
        self._require_client().update()
        self.targets = self._build_targets()
        return list(self.targets)

    def apply(self, settings: LightingSettings) -> None:
        client = self._require_client()
        target = self._target_by_id(settings.target_id)
        device = client.devices[target.device_index]

        if settings.effect == "off":
            self._apply_off(device, target, settings.save)
            return

        self._resize_zone_if_needed(device, target, settings.zone_size)
        color = self._rgb_color(settings.color, settings.brightness_percent)
        self._apply_mode(device, settings.effect, color, settings.speed_percent, settings.brightness_percent, settings.save)
        if target.zone_index is None:
            device.set_color(color, fast=True)
        else:
            device.zones[target.zone_index].set_color(color, fast=True)

    def _build_targets(self) -> list[LightingTarget]:
        client = self._require_client()
        targets: list[LightingTarget] = []
        for device_index, device in enumerate(client.devices):
            modes = tuple(mode.name for mode in getattr(device, "modes", []))
            targets.append(
                LightingTarget(
                    id=f"device:{device_index}",
                    name=f"{device.name} / 全部",
                    device_index=device_index,
                    zone_index=None,
                    modes=modes,
                )
            )
            for zone_index, zone in enumerate(getattr(device, "zones", [])):
                targets.append(
                    LightingTarget(
                        id=f"device:{device_index}:zone:{zone_index}",
                        name=f"{device.name} / {zone.name}",
                        device_index=device_index,
                        zone_index=zone_index,
                        modes=modes,
                    )
                )
        return targets

    def _apply_mode(
        self,
        device: Any,
        effect: str,
        color: Any,
        speed_percent: int,
        brightness_percent: int,
        save: bool,
    ) -> None:
        mode = _find_mode(getattr(device, "modes", []), _mode_aliases(effect))
        if mode is None:
            if effect in {"static", "direct"} and hasattr(device, "set_custom_mode"):
                device.set_custom_mode()
            return

        _set_mode_numeric(mode, "speed", "speed_min", "speed_max", speed_percent)
        _set_mode_numeric(mode, "brightness", "brightness_min", "brightness_max", brightness_percent)
        colors_max = getattr(mode, "colors_max", None)
        if getattr(mode, "colors", None) is not None and colors_max:
            mode.colors = [color] * max(1, int(colors_max))
        device.set_mode(mode, save=save, force=True)

    def _apply_off(self, device: Any, target: LightingTarget, save: bool) -> None:
        mode = _find_mode(getattr(device, "modes", []), ("off",))
        if mode is not None:
            device.set_mode(mode, save=save, force=True)
            return

        black = self._rgb_color("#000000", 0)
        if target.zone_index is None:
            device.set_color(black, fast=True)
        else:
            device.zones[target.zone_index].set_color(black, fast=True)

    def _resize_zone_if_needed(self, device: Any, target: LightingTarget, zone_size: int | None) -> None:
        if target.zone_index is None or zone_size is None or zone_size <= 0:
            return
        zone = device.zones[target.zone_index]
        if len(getattr(zone, "leds", [])) > 0:
            return
        resize = getattr(zone, "resize", None)
        if not callable(resize):
            return
        resize(zone_size)
        self.refresh()

    def _target_by_id(self, target_id: str) -> LightingTarget:
        for target in self.targets:
            if target.id == target_id:
                return target
        raise ValueError("请选择有效的 OpenRGB 目标")

    def _require_client(self) -> Any:
        if self.client is None:
            raise OpenRgbUnavailableError("尚未连接 OpenRGB SDK Server")
        return self.client

    @staticmethod
    def _rgb_color(color: str, brightness_percent: int) -> Any:
        try:
            from openrgb.utils import RGBColor
        except ImportError as error:
            raise OpenRgbUnavailableError("缺少 openrgb-python") from error

        red, green, blue = _parse_hex_color(color)
        scale = max(0, min(100, brightness_percent)) / 100
        return RGBColor(round(red * scale), round(green * scale), round(blue * scale))


def _parse_hex_color(value: str) -> tuple[int, int, int]:
    if len(value) != 7 or not value.startswith("#"):
        raise ValueError("颜色必须是 #RRGGBB 格式")
    try:
        return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
    except ValueError as error:
        raise ValueError("颜色必须是 #RRGGBB 格式") from error


def _mode_aliases(effect: str) -> tuple[str, ...]:
    return {
        "static": ("static", "direct", "custom", "fixed"),
        "breathing": ("breathing", "breath"),
        "rainbow": ("rainbow", "spectrum cycle", "spectrum"),
        "spectrum": ("spectrum cycle", "spectrum", "rainbow"),
        "chase": ("chase", "chase fade"),
        "star": ("star", "starry night", "sparkle"),
        "direct": ("direct", "custom"),
        "off": ("off", "static", "direct"),
    }.get(effect, (effect,))


def _find_mode(modes: list[Any], aliases: tuple[str, ...]) -> Any | None:
    normalized = [alias.lower() for alias in aliases]
    for mode in modes:
        name = str(getattr(mode, "name", "")).lower()
        if name in normalized:
            return mode
    for mode in modes:
        name = str(getattr(mode, "name", "")).lower()
        if any(alias in name for alias in normalized):
            return mode
    return None


def _set_mode_numeric(mode: Any, attr: str, min_attr: str, max_attr: str, percent: int) -> None:
    minimum = getattr(mode, min_attr, None)
    maximum = getattr(mode, max_attr, None)
    if minimum is None or maximum is None:
        return
    value = int(minimum + (maximum - minimum) * (max(0, min(100, percent)) / 100))
    setattr(mode, attr, value)
