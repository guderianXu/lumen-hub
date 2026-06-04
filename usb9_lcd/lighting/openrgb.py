from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time
from typing import Any

from .effects import effect_aliases, effect_uses_color
from .engine import build_lighting_apply_plan
from .layout import (
    LightingPhysicalLayout,
    LightingTargetLayout,
    apply_layout_direction,
    layout_entry_for_target,
    layout_led_count,
    ordered_layout_entries,
)
from .software_effects import render_software_effect_frame, software_effect_interval_seconds


STATIC_COLOR_REAPPLY_DELAY_SECONDS = 0.08
STATIC_COLOR_REAPPLY_COUNT = 4
_LOGGER = logging.getLogger(__name__)


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
    physical_layout: LightingPhysicalLayout | None = None


@dataclass(frozen=True)
class _SoftwareFrameTarget:
    writer: Any
    led_count: int
    target_id: str
    layout: LightingTargetLayout


class OpenRgbLightingController:
    def __init__(self, host: str = "127.0.0.1", port: int = 6742) -> None:
        self.host = host
        self.port = port
        self.client: Any | None = None
        self.targets: list[LightingTarget] = []
        self._software_effect_stop: threading.Event | None = None
        self._software_effect_thread: threading.Thread | None = None
        self._write_lock = threading.RLock()

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
        self._stop_software_effect()
        if self.client is not None:
            self.client.disconnect()
        self.client = None
        self.targets = []

    def refresh(self) -> list[LightingTarget]:
        self._require_client().update()
        self.targets = self._build_targets()
        return list(self.targets)

    def apply(self, settings: LightingSettings) -> None:
        self._stop_software_effect()
        client = self._require_client()
        target = self._target_by_id(settings.target_id)
        device = client.devices[target.device_index]
        device_name = str(getattr(device, "name", ""))

        if settings.effect == "off":
            self._apply_off(device, target, settings.save)
            return

        plan = build_lighting_apply_plan(
            effect=settings.effect,
            device_name=device_name,
            target_zone_index=target.zone_index,
            requested_zone_size=settings.zone_size,
            default_static_reapply_count=STATIC_COLOR_REAPPLY_COUNT,
        )
        zone_size = plan.zone_size

        self._resize_zone_if_needed(device, target, zone_size)
        color = self._rgb_color(settings.color, settings.brightness_percent)
        mode_result = self._apply_mode(
            device,
            target,
            settings.effect,
            plan.mode_aliases,
            color,
            settings.speed_percent,
            settings.brightness_percent,
            settings.save,
        )
        if mode_result == "none" and plan.supports_software_animation:
            self._apply_software_effect(device, target, settings, color, zone_size)
            return
        if plan.should_apply_explicit_color(mode_result):
            if plan.stable_whole_device_color and zone_size:
                self._apply_stable_static_color(
                    device,
                    target,
                    color,
                    zone_size,
                    plan.color_reapply_count,
                )
            else:
                self._set_target_color(device, target, color, zone_size)
                if settings.effect in {"static", "direct"} and zone_size:
                    time.sleep(STATIC_COLOR_REAPPLY_DELAY_SECONDS)
                    self._set_target_color(device, target, color, zone_size)

    def _apply_software_effect(
        self,
        device: Any,
        target: LightingTarget,
        settings: LightingSettings,
        color: Any,
        zone_size: int | None,
    ) -> None:
        self._prepare_software_frame_mode(device, settings.save)
        self._write_software_effect_frame(
            device,
            target,
            settings.effect,
            color,
            zone_size,
            frame_index=0,
            physical_layout=settings.physical_layout,
        )
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._run_software_effect_loop,
            args=(
                stop_event,
                device,
                target,
                settings.effect,
                color,
                settings.speed_percent,
                zone_size,
                settings.physical_layout,
            ),
            name=f"openrgb-software-{settings.effect}",
            daemon=True,
        )
        self._software_effect_stop = stop_event
        self._software_effect_thread = thread
        thread.start()

    def _run_software_effect_loop(
        self,
        stop_event: threading.Event,
        device: Any,
        target: LightingTarget,
        effect: str,
        color: Any,
        speed_percent: int,
        zone_size: int | None,
        physical_layout: LightingPhysicalLayout | None,
    ) -> None:
        interval = software_effect_interval_seconds(speed_percent)
        frame_index = 1
        while not stop_event.wait(interval):
            try:
                self._write_software_effect_frame(
                    device,
                    target,
                    effect,
                    color,
                    zone_size,
                    frame_index=frame_index,
                    physical_layout=physical_layout,
                )
            except Exception:
                _LOGGER.exception("openrgb software effect frame failed: %s", effect)
                return
            frame_index += 1

    def _stop_software_effect(self) -> None:
        stop_event = self._software_effect_stop
        if stop_event is not None:
            stop_event.set()
        thread = self._software_effect_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        self._software_effect_stop = None
        self._software_effect_thread = None

    def _prepare_software_frame_mode(self, device: Any, save: bool) -> None:
        mode = _find_mode(getattr(device, "modes", []), ("direct", "custom"))
        with self._write_lock:
            if mode is not None:
                device.set_mode(mode, save=save, force=True)
                return
            set_custom_mode = getattr(device, "set_custom_mode", None)
            if callable(set_custom_mode):
                set_custom_mode()

    def _write_software_effect_frame(
        self,
        device: Any,
        target: LightingTarget,
        effect: str,
        color: Any,
        zone_size: int | None,
        *,
        frame_index: int,
        physical_layout: LightingPhysicalLayout | None = None,
    ) -> None:
        targets = self._software_frame_targets(device, target, zone_size, physical_layout)
        total_leds = sum(item.led_count for item in targets)
        if total_leds <= 0:
            self._set_target_color(device, target, color, zone_size)
            return
        base_color = _rgb_tuple(color)
        offset = 0
        with self._write_lock:
            for frame_target in targets:
                frame = render_software_effect_frame(
                    effect,
                    led_count=frame_target.led_count,
                    frame_index=frame_index,
                    base_color=base_color,
                    global_offset=offset,
                    total_leds=total_leds,
                )
                frame = apply_layout_direction(frame, frame_target.layout)
                self._write_color_frame(frame_target.writer, [_rgb_color_from_tuple(color, item) for item in frame])
                offset += frame_target.led_count

    def _software_frame_targets(
        self,
        device: Any,
        target: LightingTarget,
        zone_size: int | None,
        physical_layout: LightingPhysicalLayout | None = None,
    ) -> list[_SoftwareFrameTarget]:
        if target.zone_index is not None:
            zone = device.zones[target.zone_index]
            entry = layout_entry_for_target(physical_layout, target.id)
            target_zone_size = entry.led_count if entry.led_count > 0 else zone_size
            self._resize_zone_object_if_needed(zone, target_zone_size)
            led_count = layout_led_count(physical_layout, target.id, fallback=_object_led_count(zone, zone_size))
            return [_SoftwareFrameTarget(zone, led_count, target.id, entry)]

        zones = list(getattr(device, "zones", []))
        raw_zone_targets: dict[str, tuple[Any, int]] = {}
        raw_zone_ids: list[str] = []
        for zone_index, zone in enumerate(zones):
            zone_target_id = f"device:{target.device_index}:zone:{zone_index}"
            entry = layout_entry_for_target(physical_layout, zone_target_id)
            target_zone_size = entry.led_count if entry.led_count > 0 else zone_size
            self._resize_zone_object_if_needed(zone, target_zone_size)
            led_count = layout_led_count(physical_layout, zone_target_id, fallback=_object_led_count(zone, zone_size))
            if led_count > 0:
                raw_zone_ids.append(zone_target_id)
                raw_zone_targets[zone_target_id] = (zone, led_count)
        frame_targets: list[_SoftwareFrameTarget] = []
        for entry in ordered_layout_entries(physical_layout, raw_zone_ids):
            writer, led_count = raw_zone_targets[entry.target_id]
            frame_targets.append(_SoftwareFrameTarget(writer, led_count, entry.target_id, entry))
        if frame_targets:
            return frame_targets
        entry = layout_entry_for_target(physical_layout, target.id)
        led_count = layout_led_count(physical_layout, target.id, fallback=_object_led_count(device, zone_size))
        return [_SoftwareFrameTarget(device, led_count, target.id, entry)]

    def _write_color_frame(self, target: Any, colors: list[Any]) -> None:
        set_colors = getattr(target, "set_colors", None)
        if callable(set_colors):
            set_colors(colors, fast=True)
            return
        set_color = getattr(target, "set_color", None)
        if callable(set_color) and colors:
            set_color(colors[0], fast=True)
            return
        raise RuntimeError("OpenRGB 目标不支持逐灯颜色写入")

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
        target: LightingTarget,
        effect: str,
        aliases: tuple[str, ...],
        color: Any,
        speed_percent: int,
        brightness_percent: int,
        save: bool,
    ) -> str:
        mode = _find_mode(getattr(device, "modes", []), aliases)
        if mode is None:
            if effect in {"static", "direct"} and hasattr(device, "set_custom_mode"):
                device.set_custom_mode()
                return "custom"
            return "none"

        _set_mode_numeric(mode, "speed", "speed_min", "speed_max", speed_percent)
        _set_mode_numeric(mode, "brightness", "brightness_min", "brightness_max", brightness_percent)
        mode_specific_color = _set_mode_colors(mode, color)
        device.set_mode(mode, save=save, force=True)
        return "mode-color" if mode_specific_color else "mode"

    @staticmethod
    def _should_apply_explicit_color(effect: str, mode_result: str) -> bool:
        if mode_result == "none":
            return True
        if effect in {"static", "direct"}:
            return True
        return effect_uses_color(effect) and mode_result != "mode-color"

    def _set_target_color(
        self,
        device: Any,
        target: LightingTarget,
        color: Any,
        zone_size: int | None = None,
    ) -> None:
        if target.zone_index is None:
            applied = False
            errors: list[Exception] = []
            try:
                device.set_color(color, fast=True)
                applied = True
            except Exception as error:
                errors.append(error)
            for zone in getattr(device, "zones", []):
                self._resize_zone_object_if_needed(zone, zone_size)
                set_color = getattr(zone, "set_color", None)
                if callable(set_color):
                    try:
                        set_color(color, fast=True)
                        applied = True
                    except Exception as error:
                        errors.append(error)
            if not applied and errors:
                raise errors[0]
            return
        self._resize_zone_object_if_needed(device.zones[target.zone_index], zone_size)
        device.zones[target.zone_index].set_color(color, fast=True)

    def _apply_stable_static_color(
        self,
        device: Any,
        target: LightingTarget,
        color: Any,
        zone_size: int,
        reapply_count: int = STATIC_COLOR_REAPPLY_COUNT,
    ) -> None:
        applied = False
        errors: list[Exception] = []
        try:
            device.set_color(color, fast=True)
            applied = True
        except Exception as error:
            errors.append(error)

        zones = list(getattr(device, "zones", []))
        for _attempt in range(max(1, reapply_count)):
            for zone in zones:
                self._resize_zone_object_if_needed(zone, zone_size)
                set_color = getattr(zone, "set_color", None)
                if not callable(set_color):
                    continue
                try:
                    set_color(color, fast=True)
                    applied = True
                except Exception as error:
                    errors.append(error)
            if _attempt < max(1, reapply_count) - 1:
                time.sleep(STATIC_COLOR_REAPPLY_DELAY_SECONDS)

        if not zones:
            try:
                device.set_color(color, fast=True)
                return
            except Exception as error:
                errors.append(error)

        if not applied and errors:
            raise errors[0]

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
        if self._resize_zone_object_if_needed(zone, zone_size):
            self.refresh()

    @staticmethod
    def _resize_zone_object_if_needed(zone: Any, zone_size: int | None) -> bool:
        if zone_size is None or zone_size <= 0:
            return False
        if len(getattr(zone, "leds", [])) > 0:
            return False
        resize = getattr(zone, "resize", None)
        if not callable(resize):
            return False
        resize(zone_size)
        return True

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
    return effect_aliases(effect)


def _find_mode(modes: list[Any], aliases: tuple[str, ...]) -> Any | None:
    normalized = [alias.lower() for alias in aliases]
    named_modes = [(str(getattr(mode, "name", "")).lower(), mode) for mode in modes]
    for alias in normalized:
        for name, mode in named_modes:
            if name == alias:
                return mode
    for alias in normalized:
        for name, mode in named_modes:
            if alias in name:
                return mode
    return None


def _set_mode_colors(mode: Any, color: Any) -> bool:
    if not _mode_uses_specific_color(mode):
        return False
    colors_max = getattr(mode, "colors_max", None)
    colors_min = getattr(mode, "colors_min", None)
    try:
        count = int(colors_max if colors_max is not None else colors_min)
    except (TypeError, ValueError):
        count = 1
    mode.colors = [color] * max(1, count)
    return True


def _mode_uses_specific_color(mode: Any) -> bool:
    color_mode = getattr(mode, "color_mode", None)
    if color_mode is None:
        return False
    value = getattr(color_mode, "value", color_mode)
    if value == 2:
        return True
    return str(color_mode).lower().endswith("mode_specific")


def _set_mode_numeric(mode: Any, attr: str, min_attr: str, max_attr: str, percent: int) -> None:
    minimum = getattr(mode, min_attr, None)
    maximum = getattr(mode, max_attr, None)
    if minimum is None or maximum is None:
        return
    value = int(minimum + (maximum - minimum) * (max(0, min(100, percent)) / 100))
    setattr(mode, attr, value)


def _object_led_count(target: Any, fallback: int | None = None) -> int:
    leds = getattr(target, "leds", None)
    try:
        count = len(leds)
    except TypeError:
        count = 0
    if count > 0:
        return count
    try:
        parsed = int(fallback or 0)
    except (TypeError, ValueError):
        parsed = 0
    return max(1, parsed)


def _rgb_tuple(color: Any) -> tuple[int, int, int]:
    try:
        return (
            max(0, min(255, int(getattr(color, "red")))),
            max(0, min(255, int(getattr(color, "green")))),
            max(0, min(255, int(getattr(color, "blue")))),
        )
    except (TypeError, ValueError):
        pass
    if isinstance(color, (tuple, list)) and len(color) >= 3:
        return (
            max(0, min(255, int(color[0]))),
            max(0, min(255, int(color[1]))),
            max(0, min(255, int(color[2]))),
        )
    return (255, 255, 255)


def _rgb_color_from_tuple(reference: Any, color: tuple[int, int, int]) -> Any:
    try:
        return reference.__class__(*color)
    except Exception:
        return color
