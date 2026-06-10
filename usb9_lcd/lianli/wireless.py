from __future__ import annotations

import base64
from dataclasses import dataclass
import math
import site
from pathlib import Path
from typing import Any, Iterable, Protocol
import sys
import time
import zlib

from usb9_lcd.lianli.tlv2_official_effects import (
    OFFICIAL_TLV2_LED_COUNT,
    OFFICIAL_TLV2_RGB_ZLIB_B64,
)


RF_SENDER_VID = 0x0416
RF_SENDER_PID = 0x8040
RF_RECEIVER_VID = 0x0416
RF_RECEIVER_PID = 0x8041
TL_CONTROLLER_VID = 0x0416
TL_CONTROLLER_PID = 0x7372
TL_LCD_VID_PID = ((0x04FC, 0x7393), (0x1CBE, 0x0006))

RF_GET_DEV_CMD = 0x10
RF_MASTER_QUERY_CMD = 0x11
RF_PACKET_HEADER = 0x10
RF_CHUNK_SIZE = 60
RF_PAYLOAD_SIZE = 240
RF_PAGE_STRIDE = 434
MAX_DEVICES_PER_PAGE = 10
LED_DATA_CHUNK = 220
FIRST_LED_PACKET_DATA_OFFSET = 34
FIRST_LED_PACKET_DATA_MAX = RF_PAYLOAD_SIZE - FIRST_LED_PACKET_DATA_OFFSET
DEFAULT_TINYUZ_DICT_SIZE = 4096
RGB_FIRST_PAYLOAD_REPEAT_COUNT = 4
RGB_STATIC_SEQUENCE_REPEAT_COUNT = 5
RGB_STATIC_PREAMBLE_DELAY_S = 0.43
RGB_STATIC_FRAME_DELAY_S = 0.020
RGB_STATIC_SEQUENCE_DELAY_S = 0.160
RGB_STATIC_CHUNK_DELAY_S = 0.001

STATIC_RGB_EFFECT_INDEXES = {
    (255, 0, 0): 0x042D671D,
    (254, 0, 0): 0x042D671D,
    (0, 255, 0): 0x042D795F,
    (0, 254, 0): 0x042D795F,
    (0, 0, 255): 0x042D9278,
    (0, 0, 254): 0x042D9278,
    (0, 0, 0): 0x040DA1DE,
}

KNOWN_USB_DEVICES = {
    (RF_SENDER_VID, RF_SENDER_PID): "L-Wireless RF sender / transmitter",
    (RF_RECEIVER_VID, RF_RECEIVER_PID): "L-Wireless RF receiver",
    (TL_CONTROLLER_VID, TL_CONTROLLER_PID): "UNI FAN TL controller HID",
    (0x04FC, 0x7393): "UNI FAN LCD HID",
    (0x1CBE, 0x0006): "UNI FAN TL LCD wireless receiver",
}

UDEV_RULES = tuple(
    (
        f'SUBSYSTEM=="usb", ATTR{{idVendor}}=="{vendor_id:04x}", '
        f'ATTR{{idProduct}}=="{product_id:04x}", MODE="0660", GROUP="plugdev"'
    )
    for vendor_id, product_id in KNOWN_USB_DEVICES
)


class LianLiWirelessError(ValueError):
    pass


class _ReceiverSnapshotShortReadError(LianLiWirelessError):
    pass


class WirelessReceiverTransport(Protocol):
    def write(self, payload: bytes) -> int: ...

    def read(self, size: int) -> bytes: ...


class WirelessSenderTransport(Protocol):
    def write(self, payload: bytes) -> int: ...

    def read(self, size: int) -> bytes: ...

def _resolve_libusb_backend() -> Any | None:
    try:
        import usb.backend.libusb1  # type: ignore[import-not-found]
    except Exception:
        return None

    try:
        backend = usb.backend.libusb1.get_backend()  # type: ignore[attr-defined]
        if backend is not None:
            return backend
    except Exception:
        pass

    candidate_dlls: list[Path] = []
    try:
        for site_dir in site.getsitepackages():
            candidate_dlls.append(Path(site_dir) / "usb1" / "libusb-1.0.dll")
            candidate_dlls.append(Path(site_dir) / "lib" / "site-packages" / "usb1" / "libusb-1.0.dll")
    except Exception:
        pass

    try:
        user_site = site.getusersitepackages()
        if user_site:
            candidate_dlls.append(Path(user_site) / "usb1" / "libusb-1.0.dll")
    except Exception:
        pass

    candidate_dlls.append(Path(sys.executable).resolve().parent / "Lib" / "site-packages" / "usb1" / "libusb-1.0.dll")
    candidate_dlls.append(Path(sys.executable).resolve().parent / "site-packages" / "usb1" / "libusb-1.0.dll")

    seen: set[Path] = set()
    for dll_path in candidate_dlls:
        if not dll_path.is_file():
            continue
        dll_path = dll_path.resolve()
        if dll_path in seen:
            continue
        seen.add(dll_path)
        try:
            backend = usb.backend.libusb1.get_backend(find_library=lambda _name: str(dll_path))  # type: ignore[attr-defined]
            if backend is not None:
                return backend
        except Exception:
            continue
    return None


def _permission_guidance() -> str:
    if sys.platform.startswith("win"):
        return (
            "Run Python as Administrator, and ensure LIAN LI devices are bound to libusbK/WinUSB "
            "(not HID or vendor HID filter driver) using Zadig."
        )
    return (
        "Run with sufficient permissions (root or a user permitted by udev), then replug LIAN LI devices."
    )


def _is_access_denied_error(error: object) -> bool:
    text = str(error).lower()
    if "errn 13" in text:
        return True
    if "access denied" in text:
        return True
    if "insufficient permissions" in text:
        return True
    if "permission denied" in text:
        return True
    errno = getattr(error, "errno", None)
    if isinstance(errno, int) and errno == 13:
        return True
    return False


@dataclass(frozen=True)
class LianLiUsbDevice:
    vendor_id: int
    product_id: int
    label: str
    manufacturer: str = ""
    product: str = ""
    serial: str = ""
    sysfs_path: str = ""
    busnum: str = ""
    devnum: str = ""

    @property
    def vid_pid(self) -> str:
        return f"{self.vendor_id:04x}:{self.product_id:04x}"


@dataclass(frozen=True)
class WirelessDeviceInfo:
    mac: str
    master_mac: str
    channel: int
    rx_type: int
    device_type: int
    fan_count: int
    pwm_values: tuple[int, int, int, int]
    fan_rpm: tuple[int, int, int, int]
    command_sequence: int
    raw: bytes

    @property
    def is_bound(self) -> bool:
        return any(part != "00" for part in self.master_mac.split(":"))


@dataclass(frozen=True)
class WirelessSnapshot:
    devices: list[WirelessDeviceInfo]
    raw: bytes

    @property
    def device_count(self) -> int:
        return len(self.devices)

    @property
    def motherboard_pwm(self) -> int | None:
        return extract_motherboard_pwm(self.raw)


@dataclass(frozen=True)
class Tlv2EffectSpec:
    key: str
    frame_count: int
    interval_ms: int
    default_effect_index: int


@dataclass(frozen=True)
class Tlv2EffectCapability:
    key: str
    uses_primary_color: bool = False
    uses_accent_color: bool = False
    uses_palette: bool = False
    uses_direction: bool = False
    uses_speed: bool = True
    uses_brightness: bool = True
    color_slots: int = 0


TLV2_EFFECT_SPECS: dict[str, Tlv2EffectSpec] = {
    "rainbow": Tlv2EffectSpec("rainbow", 24, 60, 0x02090A05),
    "rainbow-morph": Tlv2EffectSpec("rainbow-morph", 127, 55, 0x020922EA),
    "static": Tlv2EffectSpec("static", 1, 60, 0x02093B20),
    "breathing": Tlv2EffectSpec("breathing", 170, 60, 0x020953C4),
    "runway": Tlv2EffectSpec("runway", 70, 60, 0x02096C54),
    "meteor": Tlv2EffectSpec("meteor", 36, 60, 0x020984D3),
    "color-cycle": Tlv2EffectSpec("color-cycle", 48, 60, 0x02099D4A),
    "staggered": Tlv2EffectSpec("staggered", 48, 60, 0x020B0800),
    "tide": Tlv2EffectSpec("tide", 60, 60, 0x020B0900),
    "mixing": Tlv2EffectSpec("mixing", 72, 60, 0x020B0A00),
    "voice": Tlv2EffectSpec("voice", 48, 55, 0x020B0B00),
    "door": Tlv2EffectSpec("door", 52, 60, 0x020B0C00),
    "render": Tlv2EffectSpec("render", 64, 60, 0x020B0D00),
    "ripple": Tlv2EffectSpec("ripple", 294, 90, 0x0209B5D3),
    "reflect": Tlv2EffectSpec("reflect", 56, 60, 0x020B0F00),
    "tail-chasing": Tlv2EffectSpec("tail-chasing", 72, 60, 0x020B1000),
    "paint": Tlv2EffectSpec("paint", 64, 60, 0x020B1100),
    "ping-pong": Tlv2EffectSpec("ping-pong", 60, 55, 0x020B1200),
    "stack": Tlv2EffectSpec("stack", 64, 60, 0x020B1300),
    "cover-cycle": Tlv2EffectSpec("cover-cycle", 72, 60, 0x020B1400),
    "wave": Tlv2EffectSpec("wave", 24, 60, 0x0209CE48),
    "disco": Tlv2EffectSpec("disco", 48, 60, 0x020B1600),
    "racing": Tlv2EffectSpec("racing", 48, 45, 0x020B1600),
    "lottery": Tlv2EffectSpec("lottery", 80, 55, 0x020B1700),
    "intertwine": Tlv2EffectSpec("intertwine", 72, 60, 0x020B1800),
    "meteor-shower": Tlv2EffectSpec("meteor-shower", 48, 60, 0x0209E6D1),
    "blow-up": Tlv2EffectSpec("blow-up", 64, 60, 0x020B1A00),
    "collide": Tlv2EffectSpec("collide", 64, 60, 0x020B1A00),
    "heartbeat": Tlv2EffectSpec("heartbeat", 72, 60, 0x020B1B00),
    "warning": Tlv2EffectSpec("warning", 48, 60, 0x020B1C00),
    "ocean": Tlv2EffectSpec("ocean", 80, 60, 0x020B1D00),
    "echo": Tlv2EffectSpec("echo", 96, 60, 0x020B1E00),
    "electric-current": Tlv2EffectSpec("electric-current", 80, 60, 0x0209FF5B),
    "kaleidoscope": Tlv2EffectSpec("kaleidoscope", 24, 60, 0x020A182A),
    "twinkle": Tlv2EffectSpec("twinkle", 200, 55, 0x020A31DE),
}

TLV2_EFFECT_SEQUENCE_REPEAT_COUNTS: dict[str, int] = {
    "rainbow": 2,
    "rainbow-morph": 1,
    "static": 6,
    "breathing": 4,
    "runway": 2,
    "meteor": 1,
    "color-cycle": 6,
    "staggered": 6,
    "tide": 6,
    "mixing": 7,
    "voice": 6,
    "door": 6,
    "render": 5,
    "ripple": 3,
    "reflect": 5,
    "tail-chasing": 6,
    "paint": 6,
    "ping-pong": 5,
    "stack": 5,
    "cover-cycle": 6,
    "wave": 2,
    "disco": 6,
    "racing": 6,
    "lottery": 6,
    "intertwine": 6,
    "meteor-shower": 5,
    "blow-up": 6,
    "collide": 6,
    "heartbeat": 6,
    "warning": 6,
    "ocean": 6,
    "echo": 6,
    "electric-current": 6,
    "kaleidoscope": 2,
    "twinkle": 5,
}

TLV2_EFFECT_ALIASES = {
    "gradient-rainbow": "rainbow-morph",
    "rainbow_morph": "rainbow-morph",
    "colorcycle": "color-cycle",
    "meteor_shower": "meteor-shower",
    "electric_current": "electric-current",
    "tail_chasing": "tail-chasing",
    "ping_pong": "ping-pong",
    "cover_cycle": "cover-cycle",
    "blowup": "blow-up",
    "blow_up": "blow-up",
    "collide": "blow-up",
    "starry": "twinkle",
    "star": "twinkle",
    "twinkle": "twinkle",
}

TLV2_EFFECT_CAPABILITIES: dict[str, Tlv2EffectCapability] = {
    "rainbow": Tlv2EffectCapability("rainbow", uses_direction=True),
    "rainbow-morph": Tlv2EffectCapability("rainbow-morph", uses_direction=True),
    "static": Tlv2EffectCapability("static", uses_primary_color=True, uses_speed=False, color_slots=1),
    "breathing": Tlv2EffectCapability("breathing", uses_primary_color=True, uses_direction=False, color_slots=1),
    "runway": Tlv2EffectCapability(
        "runway",
        uses_primary_color=True,
        uses_accent_color=True,
        uses_direction=True,
        color_slots=2,
    ),
    "meteor": Tlv2EffectCapability("meteor", uses_primary_color=True, uses_direction=True, color_slots=1),
    "color-cycle": Tlv2EffectCapability("color-cycle", uses_palette=True, uses_direction=False, color_slots=3),
    "staggered": Tlv2EffectCapability("staggered", uses_palette=True, uses_direction=True, color_slots=2),
    "tide": Tlv2EffectCapability("tide", uses_palette=True, uses_direction=True, color_slots=2),
    "mixing": Tlv2EffectCapability("mixing", uses_palette=True, uses_direction=True, color_slots=3),
    "voice": Tlv2EffectCapability("voice", uses_palette=True, uses_direction=True, color_slots=2),
    "door": Tlv2EffectCapability("door", uses_primary_color=True, uses_palette=True, uses_direction=True, color_slots=2),
    "render": Tlv2EffectCapability("render", uses_palette=True, uses_direction=True, color_slots=4),
    "ripple": Tlv2EffectCapability("ripple", uses_palette=True, uses_direction=True, color_slots=2),
    "reflect": Tlv2EffectCapability(
        "reflect",
        uses_primary_color=True,
        uses_accent_color=True,
        uses_palette=True,
        uses_direction=True,
        color_slots=2,
    ),
    "tail-chasing": Tlv2EffectCapability(
        "tail-chasing",
        uses_primary_color=True,
        uses_palette=True,
        uses_direction=True,
        color_slots=2,
    ),
    "paint": Tlv2EffectCapability("paint", uses_primary_color=True, uses_palette=True, uses_direction=True, color_slots=2),
    "ping-pong": Tlv2EffectCapability(
        "ping-pong",
        uses_primary_color=True,
        uses_palette=True,
        uses_direction=True,
        color_slots=2,
    ),
    "stack": Tlv2EffectCapability("stack", uses_palette=True, uses_direction=True, color_slots=2),
    "cover-cycle": Tlv2EffectCapability("cover-cycle", uses_palette=True, uses_direction=True, color_slots=2),
    "wave": Tlv2EffectCapability("wave", uses_primary_color=True, uses_direction=True, color_slots=1),
    "disco": Tlv2EffectCapability("disco", uses_palette=True, uses_direction=True, color_slots=4),
    "racing": Tlv2EffectCapability(
        "racing",
        uses_primary_color=True,
        uses_accent_color=True,
        uses_palette=True,
        uses_direction=True,
        color_slots=4,
    ),
    "lottery": Tlv2EffectCapability("lottery", uses_accent_color=True, uses_palette=True, uses_direction=True, color_slots=4),
    "intertwine": Tlv2EffectCapability("intertwine", uses_palette=True, uses_direction=True, color_slots=2),
    "meteor-shower": Tlv2EffectCapability("meteor-shower", uses_palette=True, uses_direction=True, color_slots=4),
    "blow-up": Tlv2EffectCapability(
        "blow-up",
        uses_primary_color=True,
        uses_accent_color=True,
        uses_palette=True,
        uses_direction=True,
        color_slots=2,
    ),
    "collide": Tlv2EffectCapability(
        "collide",
        uses_primary_color=True,
        uses_accent_color=True,
        uses_palette=True,
        uses_direction=True,
        color_slots=2,
    ),
    "heartbeat": Tlv2EffectCapability(
        "heartbeat",
        uses_primary_color=True,
        uses_accent_color=True,
        uses_direction=True,
        color_slots=2,
    ),
    "warning": Tlv2EffectCapability(
        "warning",
        uses_primary_color=True,
        uses_accent_color=True,
        uses_direction=True,
        color_slots=2,
    ),
    "ocean": Tlv2EffectCapability("ocean", uses_palette=True, uses_direction=True, color_slots=2),
    "echo": Tlv2EffectCapability("echo", uses_primary_color=True, uses_accent_color=True, uses_direction=True, color_slots=2),
    "electric-current": Tlv2EffectCapability(
        "electric-current",
        uses_primary_color=True,
        uses_accent_color=True,
        uses_direction=True,
        color_slots=2,
    ),
    "kaleidoscope": Tlv2EffectCapability("kaleidoscope", uses_direction=True),
    "twinkle": Tlv2EffectCapability("twinkle", uses_primary_color=True, uses_accent_color=True, color_slots=2),
}

_TLV2_DEFAULT_PRIMARY_COLOR = (255, 0, 0)
_TLV2_DEFAULT_ACCENT_COLOR = (255, 255, 255)
_TLV2_DEFAULT_PALETTE = (
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 255),
)
_TLV2_ACCENT_BASIS_PALETTE = (
    (0, 255, 0),
    (0, 0, 255),
)

_OFFICIAL_TLV2_RGB_CACHE: dict[str, bytes] = {}


class LianLiWirelessBackend:
    def __init__(
        self,
        *,
        sender: WirelessSenderTransport | None = None,
        receiver: WirelessReceiverTransport | None = None,
    ) -> None:
        self.sender = sender
        self.receiver = receiver

    def list_devices(self, *, page_count: int = 1) -> WirelessSnapshot:
        if self.receiver is None:
            raise LianLiWirelessError("receiver transport is not configured")
        page_count = max(1, min(255, int(page_count)))
        request = build_wireless_list_request(page_count)
        snapshot_length = expected_snapshot_length(page_count)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                written = self.receiver.write(request)
                if written != len(request):
                    raise LianLiWirelessError(
                        f"incomplete receiver request write ({written}/{len(request)})"
                    )
                raw = self.receiver.read(snapshot_length)
                if not raw:
                    return WirelessSnapshot(devices=[], raw=raw)
                if raw[0] != RF_GET_DEV_CMD:
                    # Some receiver firmware revisions intermittently return a transient
                    # empty/status frame (e.g. 0x00) before the real snapshot frame.
                    for _ in range(2):
                        follow = self.receiver.read(snapshot_length)
                        if follow and follow[0] == RF_GET_DEV_CMD:
                            raw = follow
                            break
                    if raw[0] != RF_GET_DEV_CMD:
                        raise LianLiWirelessError(f"unexpected RF snapshot header 0x{raw[0]:02x}")
                raw = self._finish_receiver_snapshot_read(raw, snapshot_length)
                return WirelessSnapshot(devices=parse_wireless_snapshot(raw), raw=raw)
            except _ReceiverSnapshotShortReadError as error:
                last_error = error
                break
            except Exception as error:
                last_error = error
                # Receiver reads can transiently return stale/invalid headers (0x00/0xff) or overflow.
                # A short retry is enough in most cases and avoids failing GUI re-discovery immediately.
                if attempt < 2:
                    time.sleep(0.12 * (attempt + 1))
                    continue
                break
        if last_error is None:
            raise LianLiWirelessError("receiver snapshot read failed")
        raise last_error

    def _finish_receiver_snapshot_read(self, raw: bytes, expected_length: int) -> bytes:
        if self.receiver is None:
            raise LianLiWirelessError("receiver transport is not configured")
        while len(raw) < expected_length:
            remaining = expected_length - len(raw)
            chunk = self.receiver.read(remaining)
            if not chunk:
                raise _ReceiverSnapshotShortReadError(
                    f"receiver snapshot read returned no data with {remaining} byte(s) remaining"
                )
            raw += chunk
        return raw

    def query_master_mac(self, *, channel: int = 8) -> tuple[str, int | None] | None:
        if self.sender is None:
            raise LianLiWirelessError("sender transport is not configured")
        request = build_master_query_request(channel)
        written = self.sender.write(request)
        if written != len(request):
            raise LianLiWirelessError(
                f"incomplete master query write ({written}/{len(request)})"
            )
        response = self.sender.read(64)
        return parse_master_query_response(response, channel=channel)

    def build_pwm_packets(
        self,
        target: WirelessDeviceInfo,
        pwm_values: Iterable[int],
        *,
        sequence_index: int | None = None,
    ) -> list[bytes]:
        payload = build_pwm_payload(target, pwm_values, sequence_index=sequence_index)
        return build_rf_chunks(target.channel, target.rx_type, payload)

    def build_motherboard_pwm_sync_packets(
        self,
        target: WirelessDeviceInfo,
        *,
        enable: bool = True,
        fallback_pwm: int = 100,
        sequence_index: int | None = None,
    ) -> list[bytes]:
        pwm_values = (6, 6, 6, 6) if enable else clamp_pwm_values([fallback_pwm] * 4)
        return self.build_pwm_packets(
            target,
            pwm_values,
            sequence_index=sequence_index,
        )

    def build_motherboard_pwm_mirror_packets(
        self,
        target: WirelessDeviceInfo,
        motherboard_pwm: int,
        *,
        sequence_index: int | None = None,
    ) -> list[bytes]:
        pwm_values = clamp_pwm_values([motherboard_pwm] * 4)
        return self.build_pwm_packets(
            target,
            pwm_values,
            sequence_index=sequence_index,
        )

    def build_bind_packets(
        self,
        target: WirelessDeviceInfo,
        *,
        master_mac: str,
        rx_type: int,
        channel: int | None = None,
    ) -> list[bytes]:
        resolved_channel = _resolve_channel(target, channel)
        payload = build_bind_payload(
            target,
            master_mac=master_mac,
            rx_type=rx_type,
            channel=resolved_channel,
        )
        return build_rf_chunks(resolved_channel, target.rx_type or 0, payload)

    def build_unbind_packets(
        self,
        target: WirelessDeviceInfo,
        *,
        channel: int | None = None,
    ) -> list[bytes]:
        resolved_channel = _resolve_channel(target, channel)
        payload = build_unbind_payload(target, channel=resolved_channel)
        return build_rf_chunks(resolved_channel, target.rx_type, payload)

    def send_pwm(
        self,
        target: WirelessDeviceInfo,
        pwm_values: Iterable[int],
        *,
        sequence_index: int | None = None,
    ) -> int:
        if self.sender is None:
            raise LianLiWirelessError("sender transport is not configured")
        packets = self.build_pwm_packets(
            target,
            pwm_values,
            sequence_index=sequence_index,
        )
        for packet in packets:
            written = self.sender.write(packet)
            if written != len(packet):
                raise LianLiWirelessError(
                    f"incomplete sender packet write ({written}/{len(packet)})"
                )
        return len(packets)

    def send_motherboard_pwm_sync(
        self,
        target: WirelessDeviceInfo,
        *,
        enable: bool = True,
        fallback_pwm: int = 100,
        sequence_index: int | None = None,
    ) -> int:
        if self.sender is None:
            raise LianLiWirelessError("sender transport is not configured")
        packets = self.build_motherboard_pwm_sync_packets(
            target,
            enable=enable,
            fallback_pwm=fallback_pwm,
            sequence_index=sequence_index,
        )
        for packet in packets:
            written = self.sender.write(packet)
            if written != len(packet):
                raise LianLiWirelessError(
                    f"incomplete sender packet write ({written}/{len(packet)})"
                )
        return len(packets)

    def send_motherboard_pwm_mirror(
        self,
        target: WirelessDeviceInfo,
        motherboard_pwm: int,
        *,
        sequence_index: int | None = None,
    ) -> int:
        if self.sender is None:
            raise LianLiWirelessError("sender transport is not configured")
        packets = self.build_motherboard_pwm_mirror_packets(
            target,
            motherboard_pwm,
            sequence_index=sequence_index,
        )
        for packet in packets:
            written = self.sender.write(packet)
            if written != len(packet):
                raise LianLiWirelessError(
                    f"incomplete sender packet write ({written}/{len(packet)})"
                )
        return len(packets)

    def send_bind(
        self,
        target: WirelessDeviceInfo,
        *,
        master_mac: str,
        rx_type: int,
        channel: int | None = None,
    ) -> int:
        if self.sender is None:
            raise LianLiWirelessError("sender transport is not configured")
        packets = self.build_bind_packets(
            target,
            master_mac=master_mac,
            rx_type=rx_type,
            channel=channel,
        )
        for packet in packets:
            written = self.sender.write(packet)
            if written != len(packet):
                raise LianLiWirelessError(
                    f"incomplete sender packet write ({written}/{len(packet)})"
                )
        return len(packets)

    def send_unbind(
        self,
        target: WirelessDeviceInfo,
        *,
        channel: int | None = None,
    ) -> int:
        if self.sender is None:
            raise LianLiWirelessError("sender transport is not configured")
        packets = self.build_unbind_packets(target, channel=channel)
        for packet in packets:
            written = self.sender.write(packet)
            if written != len(packet):
                raise LianLiWirelessError(
                    f"incomplete sender packet write ({written}/{len(packet)})"
                )
        return len(packets)

    def build_static_rgb_packets(
        self,
        target: WirelessDeviceInfo,
        color: tuple[int, int, int],
        *,
        interval_ms: int = 60,
        effect_index: int | None = None,
        led_count: int | None = None,
        repeat_first_payload: int = RGB_FIRST_PAYLOAD_REPEAT_COUNT,
    ) -> list[bytes]:
        payloads = build_static_rgb_payloads(
            target,
            color,
            interval_ms=interval_ms,
            effect_index=effect_index,
            led_count=led_count,
        )
        packets: list[bytes] = []
        first_repeat = max(1, int(repeat_first_payload))
        for index, payload in enumerate(payloads):
            repeat = first_repeat if index == 0 else 1
            for _ in range(repeat):
                packets.extend(build_rf_chunks(target.channel, target.rx_type, payload))
        return packets

    def build_rainbow_rgb_packets(
        self,
        target: WirelessDeviceInfo,
        *,
        frame_count: int = 24,
        interval_ms: int = 48,
        effect_index: int = 1,
        led_count: int | None = None,
        brightness: int = 100,
        direction: str = "left",
        repeat_first_payload: int = RGB_FIRST_PAYLOAD_REPEAT_COUNT,
    ) -> list[bytes]:
        payloads = build_rainbow_rgb_payloads(
            target,
            frame_count=frame_count,
            interval_ms=interval_ms,
            effect_index=effect_index,
            led_count=led_count,
            brightness=brightness,
            direction=direction,
        )
        packets: list[bytes] = []
        first_repeat = max(1, int(repeat_first_payload))
        for index, payload in enumerate(payloads):
            repeat = first_repeat if index == 0 else 1
            for _ in range(repeat):
                packets.extend(build_rf_chunks(target.channel, target.rx_type, payload))
        return packets

    def build_tlv2_effect_packets(
        self,
        target: WirelessDeviceInfo,
        effect: str,
        *,
        color: tuple[int, int, int] = (255, 255, 255),
        accent_color: tuple[int, int, int] = (255, 255, 255),
        palette: Iterable[tuple[int, int, int]] | None = None,
        brightness: int = 100,
        direction: str = "left",
        effect_index: int | None = None,
        led_count: int | None = None,
        repeat_first_payload: int = RGB_FIRST_PAYLOAD_REPEAT_COUNT,
    ) -> list[bytes]:
        payloads = build_tlv2_effect_payloads(
            target,
            effect,
            color=color,
            accent_color=accent_color,
            palette=palette,
            brightness=brightness,
            direction=direction,
            effect_index=effect_index,
            led_count=led_count,
        )
        packets: list[bytes] = []
        first_repeat = max(1, int(repeat_first_payload))
        for index, payload in enumerate(payloads):
            repeat = first_repeat if index == 0 else 1
            for _ in range(repeat):
                packets.extend(build_rf_chunks(target.channel, target.rx_type, payload))
        return packets

    def send_static_rgb(
        self,
        target: WirelessDeviceInfo,
        color: tuple[int, int, int],
        *,
        interval_ms: int = 60,
        effect_index: int | None = None,
        led_count: int | None = None,
        repeat_first_payload: int = RGB_FIRST_PAYLOAD_REPEAT_COUNT,
    ) -> int:
        if self.sender is None:
            raise LianLiWirelessError("sender transport is not configured")
        resolved_led_count = infer_led_count(target) if led_count is None else int(led_count)
        payloads = build_static_rgb_payloads(
            target,
            color,
            interval_ms=interval_ms,
            effect_index=effect_index,
            led_count=resolved_led_count,
        )

        packets_written = 0

        def write_packet(packet: bytes) -> None:
            nonlocal packets_written
            written = self.sender.write(packet)
            if written != len(packet):
                raise LianLiWirelessError(
                    f"incomplete sender packet write ({written}/{len(packet)})"
                )
            packets_written += 1

        preamble = build_static_rgb_preamble_payload(target)
        for packet in build_rf_chunks(target.channel, 0xFF, preamble):
            write_packet(packet)
            time.sleep(RGB_STATIC_CHUNK_DELAY_S)
        time.sleep(RGB_STATIC_PREAMBLE_DELAY_S)

        first_repeat = max(1, int(repeat_first_payload))
        for _ in range(RGB_STATIC_SEQUENCE_REPEAT_COUNT):
            for payload_index, payload in enumerate(payloads):
                repeat = first_repeat if payload_index == 0 else 1
                for _repeat_index in range(repeat):
                    for packet in build_rf_chunks(target.channel, target.rx_type, payload):
                        write_packet(packet)
                        time.sleep(RGB_STATIC_CHUNK_DELAY_S)
                    time.sleep(
                        RGB_STATIC_FRAME_DELAY_S
                        if payload_index == 0
                        else RGB_STATIC_SEQUENCE_DELAY_S
                    )
        return packets_written

    def send_rainbow_rgb(
        self,
        target: WirelessDeviceInfo,
        *,
        frame_count: int = 24,
        interval_ms: int = 48,
        effect_index: int = 1,
        led_count: int | None = None,
        brightness: int = 100,
        direction: str = "left",
        repeat_first_payload: int = RGB_FIRST_PAYLOAD_REPEAT_COUNT,
    ) -> int:
        if self.sender is None:
            raise LianLiWirelessError("sender transport is not configured")
        packets = self.build_rainbow_rgb_packets(
            target,
            frame_count=frame_count,
            interval_ms=interval_ms,
            effect_index=effect_index,
            led_count=led_count,
            brightness=brightness,
            direction=direction,
            repeat_first_payload=repeat_first_payload,
        )
        for packet in packets:
            written = self.sender.write(packet)
            if written != len(packet):
                raise LianLiWirelessError(
                    f"incomplete sender packet write ({written}/{len(packet)})"
                )
        return len(packets)

    def send_tlv2_effect(
        self,
        target: WirelessDeviceInfo,
        effect: str,
        *,
        color: tuple[int, int, int] = (255, 255, 255),
        accent_color: tuple[int, int, int] = (255, 255, 255),
        palette: Iterable[tuple[int, int, int]] | None = None,
        brightness: int = 100,
        direction: str = "left",
        effect_index: int | None = None,
        led_count: int | None = None,
        repeat_first_payload: int = RGB_FIRST_PAYLOAD_REPEAT_COUNT,
    ) -> int:
        if self.sender is None:
            raise LianLiWirelessError("sender transport is not configured")
        packets = self.build_tlv2_effect_packets(
            target,
            effect,
            color=color,
            accent_color=accent_color,
            palette=palette,
            brightness=brightness,
            direction=direction,
            effect_index=effect_index,
            led_count=led_count,
            repeat_first_payload=repeat_first_payload,
        )
        effect_key = _normalize_tlv2_effect_key(effect)
        sequence_repeat = max(1, TLV2_EFFECT_SEQUENCE_REPEAT_COUNTS.get(effect_key, 1))
        preamble = build_static_rgb_preamble_payload(target)
        packets_written = 0
        for packet in build_rf_chunks(target.channel, 0xFF, preamble):
            written = self.sender.write(packet)
            if written != len(packet):
                raise LianLiWirelessError(
                    f"incomplete sender packet write ({written}/{len(packet)})"
                )
            packets_written += 1
            time.sleep(RGB_STATIC_CHUNK_DELAY_S)
        time.sleep(RGB_STATIC_PREAMBLE_DELAY_S)
        for _ in range(sequence_repeat):
            for packet in packets:
                written = self.sender.write(packet)
                if written != len(packet):
                    raise LianLiWirelessError(
                        f"incomplete sender packet write ({written}/{len(packet)})"
                    )
                packets_written += 1
                time.sleep(RGB_STATIC_CHUNK_DELAY_S)
            time.sleep(RGB_STATIC_SEQUENCE_DELAY_S)
        return packets_written


class PyUsbEndpointTransport:
    def __init__(
        self,
        vendor_id: int,
        product_id: int,
        *,
        write_endpoint: int = 0x01,
        read_endpoint: int = 0x81,
        interface: int = 0,
        timeout_ms: int = 1000,
    ) -> None:
        try:
            import usb.core  # type: ignore[import-not-found]
            import usb.util  # type: ignore[import-not-found]
        except ImportError as error:
            raise LianLiWirelessError(
                "pyusb is required for live LIAN LI USB access; install pyusb and libusb first"
            ) from error
        self._usb_core = usb.core
        self._usb_util = usb.util
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.timeout_ms = timeout_ms
        backend = _resolve_libusb_backend()
        try:
            self._device = usb.core.find(
                idVendor=vendor_id,
                idProduct=product_id,
                backend=backend,
            )
        except Exception as error:
            raise LianLiWirelessError(
                f"failed to enumerate USB device {vendor_id:04x}:{product_id:04x}; install a working libusb backend"
            ) from error
        if self._device is None:
            raise LianLiWirelessError(
                f"USB device {vendor_id:04x}:{product_id:04x} not found"
            )
        self._ensure_configuration()
        self._interface: int | None = None
        self._claimed_interface: int | None = None
        self._out_endpoint, self._in_endpoint = self._find_endpoints(
            interface,
            write_endpoint,
            read_endpoint,
        )

    def write(self, payload: bytes) -> int:
        try:
            return int(self._out_endpoint.write(payload, self.timeout_ms))
        except Exception as error:
            raise LianLiWirelessError(f"USB write failed: {error}") from error

    def read(self, size: int) -> bytes:
        try:
            return bytes(self._in_endpoint.read(size, self.timeout_ms))
        except Exception as error:
            raise LianLiWirelessError(f"USB read failed: {error}") from error

    def close(self) -> None:
        try:
            if self._claimed_interface is not None:
                self._usb_util.release_interface(self._device, self._claimed_interface)
        except Exception:
            pass
        try:
            self._usb_util.dispose_resources(self._device)
        except Exception:
            pass

    def _ensure_configuration(self) -> None:
        try:
            self._device.get_active_configuration()
        except Exception as error:
            try:
                self._device.set_configuration()
            except Exception as set_error:
                if _is_access_denied_error(set_error):
                    raise LianLiWirelessError(
                        f"USB access denied while setting configuration for "
                        f"{self.vendor_id:04x}:{self.product_id:04x}. {_permission_guidance()}"
                    ) from set_error
                raise
            return

    def _claim_interface(self, interface: int) -> None:
        if self._claimed_interface == interface:
            return
        if self._claimed_interface is not None:
            self._release_interface(self._claimed_interface)
        try:
            if self._device.is_kernel_driver_active(interface):
                self._device.detach_kernel_driver(interface)
        except Exception:
            pass
        self._usb_util.claim_interface(self._device, interface)
        self._claimed_interface = interface

    def _release_interface(self, interface: int) -> None:
        try:
            self._usb_util.release_interface(self._device, interface)
        except Exception:
            pass
        if self._claimed_interface == interface:
            self._claimed_interface = None

    def _iter_candidate_interfaces(
        self,
        configuration: Any,
        requested_interface: int,
    ) -> list[Any]:
        ordered: list[Any] = []
        seen: set[int] = set()

        def _add(candidate: Any) -> None:
            number = int(getattr(candidate, "bInterfaceNumber"))
            if number in seen:
                return
            seen.add(number)
            ordered.append(candidate)

        primary: Any | None = None
        try:
            primary = configuration[(requested_interface, 0)]
        except Exception:
            primary = None
        if primary is not None:
            _add(primary)
        for interface_descriptor in configuration:
            _add(interface_descriptor)

        return ordered

    def _is_bulk_endpoint(self, endpoint: Any) -> bool:
        transfer_type_mask = getattr(self._usb_util, "TRANSFER_TYPE_MASK", 0x03)
        transfer_type_bulk = getattr(self._usb_util, "TRANSFER_TYPE_BULK", 0x02)
        return (getattr(endpoint, "bmAttributes", 0) & transfer_type_mask) == transfer_type_bulk

    def _endpoint_direction(self, endpoint: Any) -> str:
        address = int(endpoint.bEndpointAddress)
        if address & self._usb_util.ENDPOINT_IN:
            return "IN"
        return "OUT"

    def _find_endpoints_for_interface(
        self,
        interface: int,
        write_endpoint: int | None,
        read_endpoint: int | None,
        *,
        require_exact: bool = True,
    ) -> tuple[Any, Any]:
        self._claim_interface(interface)
        descriptor = self._device.get_active_configuration()[(interface, 0)]

        out_endpoint = None
        in_endpoint = None
        for endpoint in descriptor:
            if not self._is_bulk_endpoint(endpoint):
                continue
            address = int(endpoint.bEndpointAddress)
            if write_endpoint is not None and address == write_endpoint:
                out_endpoint = endpoint
            elif read_endpoint is not None and address == read_endpoint:
                in_endpoint = endpoint
            elif not require_exact:
                direction = self._endpoint_direction(endpoint)
                if direction == "OUT" and out_endpoint is None:
                    out_endpoint = endpoint
                elif direction == "IN" and in_endpoint is None:
                    in_endpoint = endpoint

        if write_endpoint is not None and out_endpoint is None:
            raise LianLiWirelessError(
                f"USB OUT endpoint 0x{write_endpoint:02x} not found"
            )
        if read_endpoint is not None and in_endpoint is None:
            raise LianLiWirelessError(
                f"USB IN endpoint 0x{read_endpoint:02x} not found"
            )
        if out_endpoint is None:
            raise LianLiWirelessError("USB OUT endpoint could not be inferred for this interface")
        if in_endpoint is None:
            raise LianLiWirelessError("USB IN endpoint could not be inferred for this interface")
        return out_endpoint, in_endpoint

    def _find_endpoints(
        self,
        interface: int,
        write_endpoint: int,
        read_endpoint: int,
    ) -> tuple[Any, Any]:
        configuration = self._device.get_active_configuration()
        candidates = self._iter_candidate_interfaces(configuration, interface)
        last_error: Exception | None = None
        for require_exact in (True, False):
            for interface_descriptor in candidates:
                candidate_interface = int(interface_descriptor.bInterfaceNumber)
                try:
                    out_endpoint, in_endpoint = self._find_endpoints_for_interface(
                        candidate_interface,
                        write_endpoint=write_endpoint if require_exact else None,
                        read_endpoint=read_endpoint if require_exact else None,
                        require_exact=require_exact,
                    )
                    self._interface = candidate_interface
                    return out_endpoint, in_endpoint
                except Exception as error:
                    last_error = error
                    self._release_interface(candidate_interface)
                    continue
            if require_exact is False:
                break

        if last_error is None:
            raise LianLiWirelessError(
                f"USB endpoints 0x{write_endpoint:02x}/0x{read_endpoint:02x} not found"
            )
        raise LianLiWirelessError(
            f"USB endpoints 0x{write_endpoint:02x}/0x{read_endpoint:02x} not found: {last_error}"
        )


def create_pyusb_backend(timeout_ms: int = 1000) -> LianLiWirelessBackend:
    sender = PyUsbEndpointTransport(
        RF_SENDER_VID,
        RF_SENDER_PID,
        timeout_ms=timeout_ms,
    )
    try:
        receiver = PyUsbEndpointTransport(
            RF_RECEIVER_VID,
            RF_RECEIVER_PID,
            timeout_ms=timeout_ms,
        )
    except Exception:
        close = getattr(sender, "close", None)
        if callable(close):
            close()
        raise
    return LianLiWirelessBackend(sender=sender, receiver=receiver)


def build_wireless_list_request(page_count: int = 1) -> bytes:
    page_count = max(1, min(255, int(page_count)))
    payload = bytearray(64)
    payload[0] = RF_GET_DEV_CMD
    payload[1] = page_count
    return bytes(payload)


def build_master_query_request(channel: int = 8) -> bytes:
    payload = bytearray(64)
    payload[0] = RF_MASTER_QUERY_CMD
    payload[1] = int(channel) & 0xFF
    return bytes(payload)


def parse_master_query_response(
    payload: bytes,
    *,
    channel: int | None = None,
) -> tuple[str, int | None] | None:
    if not payload or payload[0] != RF_MASTER_QUERY_CMD:
        return None
    master_mac = _bytes_to_mac(payload[1:7])
    if set(master_mac.split(":")) == {"00"}:
        return None
    return master_mac, channel


def expected_snapshot_length(page_count: int) -> int:
    return RF_PAGE_STRIDE * max(1, int(page_count))


def _decode_fan_rpm(high_byte: int, low_byte: int) -> int:
    # Receiver snapshots encode RPM as a big-endian 16-bit tach value.
    raw = ((high_byte & 0xFF) << 8) | (low_byte & 0xFF)
    rpm = raw & 0x7FFF
    # L-Wireless fan UI range is 0-1800 RPM; clamp for safety.
    return max(0, min(1800, int(rpm)))


def parse_wireless_snapshot(payload: bytes) -> list[WirelessDeviceInfo]:
    if not payload:
        return []
    if payload[0] != RF_GET_DEV_CMD:
        raise LianLiWirelessError(f"unexpected RF snapshot header 0x{payload[0]:02x}")
    count = payload[1]
    devices: list[WirelessDeviceInfo] = []
    offset = 4
    for _ in range(count):
        if offset + 42 > len(payload):
            break
        record = payload[offset : offset + 42]
        offset += 42
        if record[41] != 28:
            continue
        fan_count = record[19] if record[19] < 10 else record[19] - 10
        devices.append(
            WirelessDeviceInfo(
                mac=_bytes_to_mac(record[0:6]),
                master_mac=_bytes_to_mac(record[6:12]),
                channel=record[12],
                rx_type=record[13],
                device_type=record[18],
                fan_count=fan_count,
                pwm_values=tuple(record[36:40]),  # type: ignore[arg-type]
                fan_rpm=tuple(
                    _decode_fan_rpm(record[28 + index * 2], record[29 + index * 2])
                    for index in range(4)
                ),  # type: ignore[arg-type]
                command_sequence=record[40],
                raw=bytes(record),
            )
        )
    return devices


def extract_motherboard_pwm(payload: bytes) -> int | None:
    if len(payload) < 4 or payload[0] != RF_GET_DEV_CMD:
        return None
    indicator = payload[2]
    value = payload[3]
    if indicator >> 7:
        return None
    denominator = (indicator & 0x7F) + value
    if denominator == 0:
        return None
    return max(0, min(255, int(255.0 * (value / denominator))))


def build_pwm_payload(
    target: WirelessDeviceInfo,
    pwm_values: Iterable[int],
    *,
    sequence_index: int | None = None,
) -> bytes:
    if not target.is_bound:
        raise LianLiWirelessError("receiver is not bound to a master controller")
    payload = bytearray(RF_PAYLOAD_SIZE)
    payload[0] = 0x12
    payload[1] = 0x10
    payload[2:8] = _mac_to_bytes(target.mac)
    payload[8:14] = _mac_to_bytes(target.master_mac)
    payload[14] = target.rx_type & 0xFF
    payload[15] = target.channel & 0xFF
    next_sequence = target.command_sequence + 1 if sequence_index is None else sequence_index
    payload[16] = next_sequence & 0xFF
    payload[17:21] = bytes(clamp_pwm_values(pwm_values))
    return bytes(payload)


def build_bind_payload(
    target: WirelessDeviceInfo,
    *,
    master_mac: str,
    rx_type: int,
    channel: int | None = None,
) -> bytes:
    if target.is_bound:
        raise LianLiWirelessError("receiver is already bound to a master controller")
    if not 0 < int(rx_type) < 16:
        raise LianLiWirelessError("rx_type must be in range 1-15")
    resolved_channel = _resolve_channel(target, channel)
    payload = bytearray(RF_PAYLOAD_SIZE)
    payload[0] = 0x12
    payload[1] = 0x10
    payload[2:8] = _mac_to_bytes(target.mac)
    payload[8:14] = _mac_to_bytes(master_mac)
    payload[14] = int(rx_type) & 0xFF
    payload[15] = resolved_channel & 0xFF
    payload[16] = 1
    payload[17:21] = bytes(clamp_pwm_values(target.pwm_values))
    return bytes(payload)


def build_unbind_payload(
    target: WirelessDeviceInfo,
    *,
    channel: int | None = None,
) -> bytes:
    if not target.is_bound:
        raise LianLiWirelessError("receiver is already unbound")
    resolved_channel = _resolve_channel(target, channel)
    payload = bytearray(RF_PAYLOAD_SIZE)
    payload[0] = 0x12
    payload[1] = 0x10
    payload[2:8] = _mac_to_bytes(target.mac)
    payload[8:14] = bytes(6)
    payload[14] = 0
    payload[15] = resolved_channel & 0xFF
    payload[16] = 0
    payload[17:21] = bytes(clamp_pwm_values(target.pwm_values))
    return bytes(payload)


def build_static_rgb_payloads(
    target: WirelessDeviceInfo,
    color: tuple[int, int, int],
    *,
    interval_ms: int = 60,
    effect_index: int | None = None,
    led_count: int | None = None,
) -> list[bytes]:
    if not target.is_bound:
        raise LianLiWirelessError("receiver is not bound to a master controller")
    resolved_led_count = infer_led_count(target) if led_count is None else led_count
    if resolved_led_count <= 0:
        raise LianLiWirelessError("LED count must be positive")
    rgb = _rgb_bytes(static_rgb_wire_color(color)) * resolved_led_count
    return build_rgb_frame_payloads(
        target,
        rgb,
        led_count=resolved_led_count,
        frame_count=1,
        interval_ms=interval_ms,
        effect_index=static_rgb_effect_index(color) if effect_index is None else effect_index,
        first_packet_data=False,
        compact_compression=True,
    )


def build_static_rgb_preamble_payload(target: WirelessDeviceInfo) -> bytes:
    payload = bytearray(RF_PAYLOAD_SIZE)
    payload[0] = 0x12
    payload[1] = 0x14
    payload[10:16] = _mac_to_bytes(target.master_mac)
    return bytes(payload)


def build_rainbow_rgb_payloads(
    target: WirelessDeviceInfo,
    *,
    frame_count: int = 24,
    interval_ms: int = 48,
    effect_index: int = 1,
    led_count: int | None = None,
    brightness: int = 100,
    direction: str = "left",
) -> list[bytes]:
    if not target.is_bound:
        raise LianLiWirelessError("receiver is not bound to a master controller")
    resolved_led_count = infer_led_count(target) if led_count is None else led_count
    rgb = generate_rainbow_rgb_frames(
        resolved_led_count,
        frame_count=frame_count,
        brightness=brightness,
        direction=direction,
    )
    return build_rgb_frame_payloads(
        target,
        rgb,
        led_count=resolved_led_count,
        frame_count=frame_count,
        interval_ms=interval_ms,
        effect_index=effect_index,
    )


def build_tlv2_effect_payloads(
    target: WirelessDeviceInfo,
    effect: str,
    *,
    color: tuple[int, int, int] = (255, 255, 255),
    accent_color: tuple[int, int, int] = (255, 255, 255),
    palette: Iterable[tuple[int, int, int]] | None = None,
    brightness: int = 100,
    direction: str = "left",
    effect_index: int | None = None,
    led_count: int | None = None,
) -> list[bytes]:
    if not target.is_bound:
        raise LianLiWirelessError("receiver is not bound to a master controller")
    resolved_led_count = infer_led_count(target) if led_count is None else int(led_count)
    raw, spec = generate_tlv2_effect_rgb_frames(
        effect,
        led_count=resolved_led_count,
        color=color,
        accent_color=accent_color,
        palette=palette,
        brightness=brightness,
        direction=direction,
    )
    return build_rgb_frame_payloads(
        target,
        raw,
        led_count=resolved_led_count,
        frame_count=spec.frame_count,
        interval_ms=spec.interval_ms,
        effect_index=spec.default_effect_index if effect_index is None else effect_index,
        first_packet_data=False,
        compact_compression=True,
    )


def build_rgb_frame_payloads(
    target: WirelessDeviceInfo,
    raw_rgb: bytes,
    *,
    led_count: int,
    frame_count: int,
    interval_ms: int = 50,
    effect_index: int = 1,
    first_packet_data: bool = True,
    compact_compression: bool = False,
) -> list[bytes]:
    if not target.is_bound:
        raise LianLiWirelessError("receiver is not bound to a master controller")
    resolved_led_count = int(led_count)
    resolved_frame_count = int(frame_count)
    if not 0 < resolved_led_count <= 255:
        raise LianLiWirelessError("LED count must be in range 1-255")
    if not 0 < resolved_frame_count <= 65535:
        raise LianLiWirelessError("frame count must be in range 1-65535")
    expected_len = resolved_led_count * resolved_frame_count * 3
    if len(raw_rgb) != expected_len:
        raise LianLiWirelessError(
            f"RGB frame data length mismatch (expected {expected_len}, got {len(raw_rgb)})"
        )
    send_interval = int(interval_ms)
    if not 0 <= send_interval <= 65535:
        raise LianLiWirelessError("interval_ms must be in range 0-65535")
    compressed = tinyuz_compress(raw_rgb) if compact_compression else tinyuz_compress_literal(raw_rgb)
    data_packets = _ceil_div(len(compressed), LED_DATA_CHUNK)
    total_packets = 1 + data_packets
    if total_packets > 255:
        raise LianLiWirelessError("LED payload is too large to transmit")

    effect_bytes = int(effect_index).to_bytes(4, "big", signed=False)
    payloads: list[bytes] = []
    data_offset = 0
    for packet_index in range(total_packets):
        payload = bytearray(RF_PAYLOAD_SIZE)
        payload[0] = 0x12
        payload[1] = 0x20
        payload[2:8] = _mac_to_bytes(target.mac)
        payload[8:14] = _mac_to_bytes(target.master_mac)
        payload[14:18] = effect_bytes
        payload[18] = packet_index & 0xFF
        payload[19] = total_packets & 0xFF
        if packet_index == 0:
            data_len = len(compressed)
            payload[20:24] = data_len.to_bytes(4, "big", signed=False)
            payload[24] = 0
            payload[25:27] = resolved_frame_count.to_bytes(2, "big", signed=False)
            payload[27] = resolved_led_count & 0xFF
            payload[32:34] = send_interval.to_bytes(2, "big", signed=False)
            if first_packet_data:
                first_len = min(FIRST_LED_PACKET_DATA_MAX, len(compressed))
                payload[FIRST_LED_PACKET_DATA_OFFSET : FIRST_LED_PACKET_DATA_OFFSET + first_len] = compressed[:first_len]
                data_offset += first_len
        else:
            chunk_len = min(LED_DATA_CHUNK, len(compressed) - data_offset)
            if chunk_len:
                payload[20 : 20 + chunk_len] = compressed[
                    data_offset : data_offset + chunk_len
                ]
                data_offset += chunk_len
        payloads.append(bytes(payload))
    return payloads


def generate_tlv2_effect_rgb_frames(
    effect: str,
    *,
    led_count: int,
    color: tuple[int, int, int] = (255, 255, 255),
    accent_color: tuple[int, int, int] = (255, 255, 255),
    palette: Iterable[tuple[int, int, int]] | None = None,
    brightness: int = 100,
    direction: str = "left",
) -> tuple[bytes, Tlv2EffectSpec]:
    effect_key = _normalize_tlv2_effect_key(effect)
    spec = TLV2_EFFECT_SPECS[effect_key]
    capability = tlv2_effect_capability(effect_key)
    resolved_led_count = int(led_count)
    if not 0 < resolved_led_count <= 255:
        raise LianLiWirelessError("LED count must be in range 1-255")
    brightness_scale = _brightness_scale(brightness)
    primary, accent, colors = _effective_tlv2_color_inputs(
        effect_key,
        color,
        accent_color=accent_color,
        palette=palette,
        brightness_scale=brightness_scale,
    )
    direction_step = _direction_step(direction) if capability.uses_direction else 1
    resolved_direction = direction if capability.uses_direction else "left"
    official_raw = _official_tlv2_rgb_frames(
        effect_key,
        led_count=resolved_led_count,
        primary=primary,
        accent=accent,
        palette=colors,
        direction_step=direction_step,
    )
    if official_raw is not None:
        return official_raw, spec

    if effect_key in {"rainbow", "kaleidoscope"}:
        raw = generate_rainbow_rgb_frames(
            resolved_led_count,
            frame_count=spec.frame_count,
            brightness=int(brightness),
            direction=resolved_direction,
        )
        return raw, spec

    frames: list[list[tuple[int, int, int]]] = []
    for frame_index in range(spec.frame_count):
        if effect_key == "static":
            frame = [primary] * resolved_led_count
        elif effect_key == "rainbow-morph":
            color_index = frame_index if direction_step > 0 else spec.frame_count - frame_index - 1
            frame_color = _hsv_to_rgb(color_index / spec.frame_count, 1.0, brightness_scale)
            frame = [frame_color] * resolved_led_count
        elif effect_key == "breathing":
            phase = (math.sin((frame_index / spec.frame_count) * math.tau - math.pi / 2) + 1.0) / 2.0
            frame = [_scale_rgb(primary, phase) for _ in range(resolved_led_count)]
        elif effect_key == "runway":
            frame = _runway_frame(resolved_led_count, frame_index, spec.frame_count, primary, accent, direction_step)
        elif effect_key == "meteor":
            frame = _meteor_frame(resolved_led_count, frame_index, spec.frame_count, primary, direction_step)
        elif effect_key == "color-cycle":
            frame_color = colors[(frame_index * len(colors)) // spec.frame_count % len(colors)]
            frame = [frame_color] * resolved_led_count
        elif effect_key in _GENERATED_EXTRA_TLV2_EFFECTS:
            frame = _extra_tlv2_effect_frame(
                effect_key,
                resolved_led_count,
                frame_index,
                spec.frame_count,
                colors,
                primary,
                accent,
                direction_step,
            )
        elif effect_key == "ripple":
            frame = _ripple_frame(resolved_led_count, frame_index, spec.frame_count, colors, direction_step)
        elif effect_key == "wave":
            frame = _wave_frame(resolved_led_count, frame_index, spec.frame_count, primary, direction_step)
        elif effect_key == "meteor-shower":
            frame = _meteor_shower_frame(resolved_led_count, frame_index, colors, direction_step)
        elif effect_key == "electric-current":
            frame = _electric_current_frame(resolved_led_count, frame_index, primary, accent, direction_step)
        elif effect_key == "twinkle":
            frame = _twinkle_frame(resolved_led_count, frame_index, spec.frame_count, primary, accent)
        else:
            raise LianLiWirelessError(f"unsupported TLV2 effect: {effect}")
        frames.append(frame)

    raw = bytearray()
    for frame in frames:
        for rgb in frame:
            raw.extend(rgb)
    return bytes(raw), spec


def _official_tlv2_rgb_frames(
    effect_key: str,
    *,
    led_count: int,
    primary: tuple[int, int, int],
    accent: tuple[int, int, int],
    palette: tuple[tuple[int, int, int], ...],
    direction_step: int,
) -> bytes | None:
    entry = OFFICIAL_TLV2_RGB_ZLIB_B64.get(effect_key)
    if entry is None or led_count != OFFICIAL_TLV2_LED_COUNT:
        return None
    raw = _official_tlv2_template(effect_key)
    frame_count = int(entry["frame_count"])
    if direction_step < 0:
        reversed_raw = _reverse_rgb_frame_led_order(raw, led_count=led_count, frame_count=frame_count)
        if reversed_raw == raw:
            reversed_raw = _reverse_rgb_frame_order(raw, led_count=led_count, frame_count=frame_count)
        raw = reversed_raw
    return _remap_official_tlv2_rgb(
        raw,
        effect_key=effect_key,
        primary=primary,
        accent=accent,
        palette=palette,
    )


def _official_tlv2_template(effect_key: str) -> bytes:
    cached = _OFFICIAL_TLV2_RGB_CACHE.get(effect_key)
    if cached is not None:
        return cached
    entry = OFFICIAL_TLV2_RGB_ZLIB_B64[effect_key]
    encoded = str(entry["zlib_b64"])
    raw = zlib.decompress(base64.b64decode(encoded))
    _OFFICIAL_TLV2_RGB_CACHE[effect_key] = raw
    return raw


def _remap_official_tlv2_rgb(
    raw: bytes,
    *,
    effect_key: str,
    primary: tuple[int, int, int],
    accent: tuple[int, int, int],
    palette: tuple[tuple[int, int, int], ...],
) -> bytes:
    basis = _official_tlv2_palette_basis(effect_key=effect_key, primary=primary, accent=accent, palette=palette)
    out = bytearray(len(raw))
    for offset in range(0, len(raw), 3):
        red, green, blue = raw[offset], raw[offset + 1], raw[offset + 2]
        if red == green == blue:
            mapped = _scale_rgb(basis[3], red / 254.0 if red else 0.0)
        elif (
            effect_key == "electric-current"
            and accent != _TLV2_DEFAULT_ACCENT_COLOR
            and green > 0
            and red == 0
            and blue == 0
        ):
            mapped = _scale_rgb(accent, green / 254.0)
        else:
            mapped = _mix_official_palette_color(red, green, blue, basis)
        out[offset : offset + 3] = bytes(mapped)
    return bytes(out)


def _official_tlv2_palette_basis(
    *,
    effect_key: str,
    primary: tuple[int, int, int],
    accent: tuple[int, int, int],
    palette: tuple[tuple[int, int, int], ...],
) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    colors = [_official_rgb_tuple(primary)]
    if effect_key == "runway":
        colors.append(_official_rgb_tuple(accent))
    for color in palette:
        official = _official_rgb_tuple(color)
        if official not in colors:
            colors.append(official)
        if len(colors) >= 4:
            break
    while len(colors) < 3:
        colors.append(_official_rgb_tuple(accent))
    if len(colors) < 4:
        colors.append(_official_rgb_tuple(accent))
    return colors[0], colors[1], colors[2], colors[3]


def _official_rgb_tuple(color: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(min(component, 254) for component in color)  # type: ignore[return-value]


def _mix_official_palette_color(
    red: int,
    green: int,
    blue: int,
    basis: tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]],
) -> tuple[int, int, int]:
    weights = (red / 254.0, green / 254.0, blue / 254.0)
    channels = []
    for channel_index in range(3):
        value = sum(weights[index] * basis[index][channel_index] for index in range(3))
        channels.append(max(0, min(254, int(round(value)))))
    return tuple(channels)  # type: ignore[return-value]


def _reverse_rgb_frame_led_order(raw: bytes, *, led_count: int, frame_count: int) -> bytes:
    frame_size = led_count * 3
    out = bytearray(len(raw))
    for frame_index in range(frame_count):
        frame_start = frame_index * frame_size
        frame = raw[frame_start : frame_start + frame_size]
        reversed_frame = bytearray(frame_size)
        for led_index in range(led_count):
            src = led_index * 3
            dst = (led_count - led_index - 1) * 3
            reversed_frame[dst : dst + 3] = frame[src : src + 3]
        out[frame_start : frame_start + frame_size] = reversed_frame
    return bytes(out)


def _reverse_rgb_frame_order(raw: bytes, *, led_count: int, frame_count: int) -> bytes:
    frame_size = led_count * 3
    out = bytearray(len(raw))
    for frame_index in range(frame_count):
        src_start = frame_index * frame_size
        dst_start = (frame_count - frame_index - 1) * frame_size
        out[dst_start : dst_start + frame_size] = raw[src_start : src_start + frame_size]
    return bytes(out)


def _maybe_reverse_generated_frame(
    frame: list[tuple[int, int, int]],
    direction_step: int,
) -> list[tuple[int, int, int]]:
    if direction_step < 0:
        return list(reversed(frame))
    return frame


def generate_rainbow_rgb_frames(
    led_count: int,
    *,
    frame_count: int = 24,
    saturation: float = 1.0,
    value: float = 1.0,
    brightness: int | None = None,
    direction: str = "left",
) -> bytes:
    resolved_led_count = int(led_count)
    resolved_frame_count = int(frame_count)
    if resolved_led_count <= 0:
        raise LianLiWirelessError("LED count must be positive")
    if resolved_frame_count <= 0:
        raise LianLiWirelessError("frame count must be positive")
    brightness_percent = 100 if brightness is None else int(brightness)
    if not 0 <= brightness_percent <= 100:
        raise LianLiWirelessError("brightness must be in range 0-100")
    normalized_direction = direction.lower().replace("_", "-")
    if normalized_direction in ("right", "reverse", "rtl"):
        step = -1
        start_offset = -1
    elif normalized_direction in ("left", "forward", "ltr"):
        step = 1
        start_offset = 0
    else:
        raise LianLiWirelessError("direction must be left or right")

    value_scale = max(0.0, min(1.0, brightness_percent / 100.0))
    # One full hue cycle across a group, with continuous per-frame phase shift.
    spatial_cycles = 1.0
    frame_step = 1.0 / resolved_frame_count
    frame_bytes = bytearray()
    for frame_index in range(resolved_frame_count):
        for led_index in range(resolved_led_count):
            spatial_phase = (led_index / resolved_led_count) * spatial_cycles
            temporal_phase = frame_index * frame_step
            hue = (temporal_phase + step * spatial_phase + start_offset * frame_step) % 1.0
            r, g, b = _hsv_to_rgb(hue, saturation, value * value_scale)
            frame_bytes.extend((r, g, b))
    return bytes(frame_bytes)


def infer_led_count(device: WirelessDeviceInfo) -> int:
    mapping = {
        1: 116,
        2: 132,
        3: 174,
        4: 88,
        65: 96,
    }
    if device.device_type in mapping:
        led_count = mapping[device.device_type]
    elif device.device_type == 10:
        led_count = 24 + max(device.fan_count, 0) * 24
    elif device.fan_count > 0:
        led_count = device.fan_count * 26
    else:
        led_count = 60

    hint = extract_led_count_hint(device.raw)
    if hint and hint != led_count and (led_count == 60 or device.fan_count == 0):
        return hint
    return led_count


def extract_led_count_hint(raw: bytes) -> int | None:
    if len(raw) < 32:
        return None
    hint = raw[31]
    return hint or None


def build_rf_chunks(channel: int, rx_type: int, payload: bytes) -> list[bytes]:
    if len(payload) != RF_PAYLOAD_SIZE:
        raise LianLiWirelessError(f"RF payload must be {RF_PAYLOAD_SIZE} bytes")
    chunks: list[bytes] = []
    for sequence, offset in enumerate(range(0, len(payload), RF_CHUNK_SIZE)):
        chunk = payload[offset : offset + RF_CHUNK_SIZE]
        if len(chunk) < RF_CHUNK_SIZE:
            chunk += bytes(RF_CHUNK_SIZE - len(chunk))
        packet = bytearray(64)
        packet[0] = RF_PACKET_HEADER
        packet[1] = sequence & 0xFF
        packet[2] = channel & 0xFF
        packet[3] = rx_type & 0xFF
        packet[4 : 4 + RF_CHUNK_SIZE] = chunk
        chunks.append(bytes(packet))
    return chunks


def clamp_pwm_values(values: Iterable[int]) -> tuple[int, int, int, int]:
    result = []
    for value in values:
        result.append(max(0, min(255, int(value))))
    while len(result) < 4:
        result.append(result[-1] if result else 0)
    return tuple(result[:4])  # type: ignore[return-value]


def tinyuz_compress_literal(
    payload: bytes,
    *,
    dict_size: int = DEFAULT_TINYUZ_DICT_SIZE,
) -> bytes:
    if not payload:
        raise LianLiWirelessError("TinyUZ payload cannot be empty")
    if dict_size <= 0 or dict_size >= 2**32:
        raise LianLiWirelessError("TinyUZ dictionary size is out of range")
    state = _TinyUzLiteralState(dict_size)
    for byte in payload:
        state.out_type(1)
        state.code.append(byte)
        state.have_data_back = True
    state.out_type(0)
    state.out_len(3, pack_bit=1)
    if state.have_data_back:
        state.out_type(0)
    state.out_dict_pos(0)
    state.reset_types()
    return bytes(state.code)


def tinyuz_compress(
    payload: bytes,
    *,
    dict_size: int = DEFAULT_TINYUZ_DICT_SIZE,
) -> bytes:
    if not payload:
        raise LianLiWirelessError("TinyUZ payload cannot be empty")
    period = _repeating_period(payload, max_period=32)
    generic = _tinyuz_compress_backrefs(payload, dict_size=dict_size)
    literal = tinyuz_compress_literal(payload, dict_size=dict_size)
    if period is None or len(payload) <= period + 2 or period > dict_size:
        return generic if len(generic) < len(literal) else literal

    state = _TinyUzLiteralState(period)
    for byte in payload[:period]:
        state.out_type(1)
        state.code.append(byte)
        state.have_data_back = True

    remaining = len(payload) - period
    state.out_type(0)
    state.out_len(remaining - 2, pack_bit=1)
    if state.have_data_back:
        state.out_type(0)
    state.out_dict_pos(period)
    state.have_data_back = False

    state.out_type(0)
    state.out_len(3, pack_bit=1)
    state.out_dict_pos(0)
    state.reset_types()
    periodic = bytes(state.code)
    return min((periodic, generic, literal), key=len)


def _tinyuz_compress_backrefs(
    payload: bytes,
    *,
    dict_size: int,
) -> bytes:
    if dict_size <= 0 or dict_size >= 2**32:
        raise LianLiWirelessError("TinyUZ dictionary size is out of range")
    state = _TinyUzLiteralState(dict_size)
    offset = 0
    previous_distance: int | None = None
    max_distance = min(dict_size, 0x7F)

    while offset < len(payload):
        distance, length = _find_tinyuz_match(payload, offset, max_distance=max_distance)
        if length >= 3:
            state.out_type(0)
            state.out_len(length - 2, pack_bit=1)
            if state.have_data_back:
                reuse_previous = previous_distance == distance
                state.out_type(1 if reuse_previous else 0)
                if not reuse_previous:
                    state.out_dict_pos(distance)
            else:
                state.out_dict_pos(distance)
            state.have_data_back = False
            previous_distance = distance
            offset += length
            continue

        state.out_type(1)
        state.code.append(payload[offset])
        state.have_data_back = True
        offset += 1

    state.out_type(0)
    state.out_len(3, pack_bit=1)
    if state.have_data_back:
        state.out_type(0)
    state.out_dict_pos(0)
    state.reset_types()
    return bytes(state.code)


def _find_tinyuz_match(
    payload: bytes,
    offset: int,
    *,
    max_distance: int,
) -> tuple[int, int]:
    best_distance = 0
    best_length = 0
    if offset <= 0:
        return best_distance, best_length
    search_limit = min(max_distance, offset)
    remaining = len(payload) - offset
    for distance in range(1, search_limit + 1):
        length = 0
        while length < remaining and payload[offset + length] == payload[offset + length - distance]:
            length += 1
        if length > best_length:
            best_distance = distance
            best_length = length
    return best_distance, best_length


def _repeating_period(payload: bytes, *, max_period: int) -> int | None:
    limit = min(max_period, len(payload) // 2)
    for period in range(1, limit + 1):
        repeated = (payload[:period] * _ceil_div(len(payload), period))[: len(payload)]
        if payload == repeated:
            return period
    return None


def scan_known_usb_devices(sys_root: Path = Path("/sys")) -> list[LianLiUsbDevice]:
    sys_devices = _scan_known_usb_devices_from_sys(sys_root)
    if sys_devices:
        return sys_devices
    if sys_root.expanduser().resolve() != Path("/sys"):
        return []
    try:
        return _scan_known_usb_devices_from_pyusb()
    except LianLiWirelessError:
        return []


def _scan_known_usb_devices_from_pyusb() -> list[LianLiUsbDevice]:
    try:
        import usb.core  # type: ignore[import-not-found]
        import usb.util  # type: ignore[import-not-found]
    except ImportError:
        return []

    backend = _resolve_libusb_backend()
    devices: list[LianLiUsbDevice] = []
    find_kwargs: dict[str, Any] = {}
    if backend is not None:
        find_kwargs["backend"] = backend
    for device in usb.core.find(find_all=True, **find_kwargs):
        vendor_id = int(getattr(device, "idVendor", 0) or 0)
        product_id = int(getattr(device, "idProduct", 0) or 0)
        label = KNOWN_USB_DEVICES.get((vendor_id, product_id))
        if label is None:
            continue

        busnum = str(getattr(device, "bus", "")) if getattr(device, "bus", None) else ""
        devnum = str(getattr(device, "address", "")) if getattr(device, "address", None) else ""
        serial = _read_usb_string(device, usb.util, getattr(device, "iSerialNumber", 0))
        manufacturer = _read_usb_string(device, usb.util, getattr(device, "iManufacturer", 0))
        product = _read_usb_string(device, usb.util, getattr(device, "iProduct", 0))
        sysfs_path = (
            f"libusb://{busnum}:{devnum}" if busnum and devnum else f"libusb://{vendor_id:04x}:{product_id:04x}"
        )

        devices.append(
            LianLiUsbDevice(
                vendor_id=vendor_id,
                product_id=product_id,
                label=label,
                manufacturer=manufacturer,
                product=product,
                serial=serial,
                sysfs_path=sysfs_path,
                busnum=busnum,
                devnum=devnum,
            )
        )
    devices.sort(
        key=lambda item: (
            item.vendor_id,
            item.product_id,
            item.busnum,
            item.devnum,
            item.serial,
        )
    )
    return devices


def _scan_known_usb_devices_from_sys(sys_root: Path) -> list[LianLiUsbDevice]:
    usb_root = sys_root / "bus" / "usb" / "devices"
    if not usb_root.exists():
        return []
    devices: list[LianLiUsbDevice] = []
    for entry in sorted(usb_root.iterdir()):
        if ":" in entry.name or not entry.is_dir():
            continue
        vendor_id = _read_hex(entry / "idVendor")
        product_id = _read_hex(entry / "idProduct")
        if vendor_id is None or product_id is None:
            continue
        label = KNOWN_USB_DEVICES.get((vendor_id, product_id))
        if label is None:
            continue
        devices.append(
            LianLiUsbDevice(
                vendor_id=vendor_id,
                product_id=product_id,
                label=label,
                manufacturer=_read_text(entry / "manufacturer"),
                product=_read_text(entry / "product"),
                serial=_read_text(entry / "serial"),
                sysfs_path=str(entry),
                busnum=_read_text(entry / "busnum"),
                devnum=_read_text(entry / "devnum"),
            )
        )
    return devices


def _read_usb_string(
    device: Any,
    usb_util: Any,
    index: int,
) -> str:
    if not index:
        return ""
    try:
        return usb_util.get_string(device, index).strip()
    except Exception:
        return ""


@dataclass
class _TinyUzLiteralState:
    dict_size: int

    def __post_init__(self) -> None:
        self.code = [
            (self.dict_size >> (8 * shift)) & 0xFF
            for shift in range(4)
        ]
        self.type_count = 0
        self.types_index: int | None = None
        self.have_data_back = False

    def out_type(self, bit: int) -> None:
        if self.type_count == 0:
            self.types_index = len(self.code)
            self.code.append(0)
        if self.types_index is None:
            raise LianLiWirelessError("TinyUZ type index is not initialised")
        self.code[self.types_index] |= (bit & 1) << self.type_count
        self.type_count = (self.type_count + 1) % 8
        if self.type_count == 0:
            self.types_index = None

    def out_len(self, value: int, pack_bit: int) -> None:
        count, adjusted = self._compute_length_chunks(value, pack_bit)
        for chunk_index in reversed(range(count)):
            for bit_index in range(pack_bit):
                shift = chunk_index * pack_bit + bit_index
                self.out_type((adjusted >> shift) & 1)
            self.out_type(1 if chunk_index > 0 else 0)

    def out_dict_pos(self, position: int) -> None:
        if not 0 <= position < 0x80:
            raise LianLiWirelessError("TinyUZ dictionary position is out of range")
        self.code.append(position & 0x7F)

    def reset_types(self) -> None:
        self.type_count = 0
        self.types_index = None
        self.have_data_back = False

    @staticmethod
    def _compute_length_chunks(value: int, pack_bit: int) -> tuple[int, int]:
        count = 1
        adjusted = value
        original = value
        while adjusted >= 1 << (count * pack_bit):
            adjusted -= 1 << (count * pack_bit)
            count += 1
        return count, original - (original - adjusted)


def _rgb_bytes(color: tuple[int, int, int]) -> bytes:
    if len(color) != 3:
        raise LianLiWirelessError("RGB color must have three components")
    for component in color:
        if not 0 <= int(component) <= 255:
            raise LianLiWirelessError("RGB color values must be between 0 and 255")
    return bytes(int(component) for component in color)


def static_rgb_effect_index(color: tuple[int, int, int]) -> int:
    rgb = tuple(_rgb_bytes(color))
    mapped = STATIC_RGB_EFFECT_INDEXES.get(rgb)
    if mapped is not None:
        return mapped
    return 0x042D0000 | (zlib.crc32(bytes(rgb)) & 0xFFFF)


def tlv2_color_effect_index(
    effect: str,
    color: tuple[int, int, int],
    *,
    accent_color: tuple[int, int, int] = (255, 255, 255),
    palette: Iterable[tuple[int, int, int]] | None = None,
    direction: str = "left",
) -> int:
    effect_key = _normalize_tlv2_effect_key(effect)
    base = TLV2_EFFECT_SPECS[effect_key].default_effect_index
    capability = tlv2_effect_capability(effect_key)
    primary, accent, colors = _effective_tlv2_color_inputs(
        effect_key,
        color,
        accent_color=accent_color,
        palette=palette,
        brightness_scale=1.0,
    )

    signature = bytearray()
    if capability.uses_primary_color:
        signature.extend(_rgb_bytes(primary))
    if capability.uses_accent_color:
        signature.extend(_rgb_bytes(accent))
    if capability.uses_palette:
        for palette_color in colors:
            signature.extend(_rgb_bytes(palette_color))
    direction_is_right = capability.uses_direction and _direction_step(direction) < 0
    if direction_is_right:
        signature.extend(b"direction:right")
    if not signature:
        return base

    low_byte = zlib.crc32(bytes(signature)) & 0xFF
    if direction_is_right:
        low_byte ^= 0x80
    return (base & 0xFFFFFF00) | low_byte


def static_rgb_wire_color(color: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(254 if component == 255 else component for component in _rgb_bytes(color))  # type: ignore[return-value]


def _hsv_to_rgb(hue: float, saturation: float, value: float) -> tuple[int, int, int]:
    h = float(hue) % 1.0
    s = max(0.0, min(1.0, float(saturation)))
    v = max(0.0, min(1.0, float(value)))
    segment = int(h * 6.0)
    fraction = h * 6.0 - segment
    p = v * (1.0 - s)
    q = v * (1.0 - fraction * s)
    t = v * (1.0 - (1.0 - fraction) * s)
    segment %= 6
    if segment == 0:
        red, green, blue = v, t, p
    elif segment == 1:
        red, green, blue = q, v, p
    elif segment == 2:
        red, green, blue = p, v, t
    elif segment == 3:
        red, green, blue = p, q, v
    elif segment == 4:
        red, green, blue = t, p, v
    else:
        red, green, blue = v, p, q
    return int(red * 255) & 0xFF, int(green * 255) & 0xFF, int(blue * 255) & 0xFF


def _normalize_tlv2_effect_key(effect: str) -> str:
    key = str(effect).strip().lower().replace("_", "-")
    key = TLV2_EFFECT_ALIASES.get(key, key)
    if key not in TLV2_EFFECT_SPECS:
        raise LianLiWirelessError(f"unsupported TLV2 effect: {effect}")
    return key


def tlv2_effect_capability(effect: str) -> Tlv2EffectCapability:
    effect_key = _normalize_tlv2_effect_key(effect)
    try:
        return TLV2_EFFECT_CAPABILITIES[effect_key]
    except KeyError as error:
        raise LianLiWirelessError(f"unsupported TLV2 effect capability: {effect}") from error


def _effective_tlv2_color_inputs(
    effect_key: str,
    color: tuple[int, int, int],
    *,
    accent_color: tuple[int, int, int],
    palette: Iterable[tuple[int, int, int]] | None,
    brightness_scale: float,
) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[tuple[int, int, int], ...]]:
    capability = tlv2_effect_capability(effect_key)
    primary_source = color if capability.uses_primary_color else _TLV2_DEFAULT_PRIMARY_COLOR
    accent_source = accent_color if capability.uses_accent_color else _TLV2_DEFAULT_ACCENT_COLOR
    primary = _scale_rgb(_rgb_tuple(primary_source), brightness_scale)
    accent = _scale_rgb(_rgb_tuple(accent_source), brightness_scale)

    if capability.uses_palette:
        palette_source = palette
    elif capability.uses_accent_color:
        palette_source = _TLV2_ACCENT_BASIS_PALETTE
    else:
        palette_source = _TLV2_DEFAULT_PALETTE

    colors = _resolve_tlv2_palette(
        palette_source,
        primary=primary,
        accent=accent,
        brightness_scale=brightness_scale,
    )
    return primary, accent, colors


def _brightness_scale(brightness: int) -> float:
    value = int(brightness)
    if not 0 <= value <= 100:
        raise LianLiWirelessError("brightness must be in range 0-100")
    return value / 100.0


def _rgb_tuple(color: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(_rgb_bytes(color))  # type: ignore[return-value]


def _scale_rgb(color: tuple[int, int, int], scale: float) -> tuple[int, int, int]:
    bounded = max(0.0, min(1.0, float(scale)))
    return tuple(max(0, min(255, int(round(component * bounded)))) for component in color)  # type: ignore[return-value]


def _mix_rgb(
    a: tuple[int, int, int],
    b: tuple[int, int, int],
    amount: float,
) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, float(amount)))
    return tuple(int(round(a[index] * (1.0 - t) + b[index] * t)) for index in range(3))  # type: ignore[return-value]


def _resolve_tlv2_palette(
    palette: Iterable[tuple[int, int, int]] | None,
    *,
    primary: tuple[int, int, int],
    accent: tuple[int, int, int],
    brightness_scale: float,
) -> tuple[tuple[int, int, int], ...]:
    if palette is None:
        colors = (
            primary,
            (0, int(round(214 * brightness_scale)), int(round(255 * brightness_scale))),
            (int(round(107 * brightness_scale)), int(round(255 * brightness_scale)), int(round(92 * brightness_scale))),
            (int(round(255 * brightness_scale)), int(round(214 * brightness_scale)), int(round(10 * brightness_scale))),
            accent,
        )
    else:
        colors = tuple(_scale_rgb(_rgb_tuple(color), brightness_scale) for color in palette)
    return colors or (primary,)


def _direction_step(direction: str) -> int:
    normalized = str(direction).strip().lower().replace("_", "-")
    if normalized in {"right", "reverse", "rtl"}:
        return -1
    if normalized in {"left", "forward", "ltr"}:
        return 1
    raise LianLiWirelessError("direction must be left or right")


def _runway_frame(
    led_count: int,
    frame_index: int,
    frame_count: int,
    color: tuple[int, int, int],
    accent: tuple[int, int, int],
    direction_step: int,
) -> list[tuple[int, int, int]]:
    block = max(2, led_count // 5)
    position = (direction_step * frame_index * led_count // max(1, frame_count // 2)) % led_count
    frame: list[tuple[int, int, int]] = []
    for led_index in range(led_count):
        distance = (led_index - position) % led_count
        if distance < block:
            frame.append(color)
        elif distance < block * 2:
            frame.append(accent)
        else:
            frame.append((0, 0, 0))
    return frame


def _meteor_frame(
    led_count: int,
    frame_index: int,
    frame_count: int,
    color: tuple[int, int, int],
    direction_step: int,
) -> list[tuple[int, int, int]]:
    tail = max(4, led_count // 2)
    position = (direction_step * frame_index * (led_count + tail) // frame_count) % (led_count + tail)
    frame: list[tuple[int, int, int]] = []
    for led_index in range(led_count):
        distance = (position - led_index) % (led_count + tail)
        level = 0.0 if distance >= tail else 1.0 - (distance / tail)
        frame.append(_scale_rgb(color, level))
    return frame


def _ripple_frame(
    led_count: int,
    frame_index: int,
    frame_count: int,
    colors: tuple[tuple[int, int, int], ...],
    direction_step: int,
) -> list[tuple[int, int, int]]:
    center = 0.0 if direction_step > 0 else float(led_count - 1)
    max_radius = max(1.0, float(led_count - 1))
    cycle = (frame_index * 6) / frame_count
    radius = (cycle % 1.0) * max_radius
    color = colors[int(cycle) % len(colors)]
    frame: list[tuple[int, int, int]] = []
    for led_index in range(led_count):
        distance = abs(led_index - center)
        level = max(0.0, 1.0 - abs(distance - radius) / 2.0)
        frame.append(_scale_rgb(color, level))
    return frame


def _wave_frame(
    led_count: int,
    frame_index: int,
    frame_count: int,
    color: tuple[int, int, int],
    direction_step: int,
) -> list[tuple[int, int, int]]:
    frame: list[tuple[int, int, int]] = []
    for led_index in range(led_count):
        phase = ((led_index * direction_step) / led_count) + (frame_index / frame_count)
        level = (math.sin(phase * math.tau) + 1.0) / 2.0
        frame.append(_scale_rgb(color, 0.08 + level * 0.92))
    return frame


_GENERATED_EXTRA_TLV2_EFFECTS = {
    "staggered",
    "tide",
    "mixing",
    "voice",
    "door",
    "render",
    "reflect",
    "tail-chasing",
    "paint",
    "ping-pong",
    "stack",
    "cover-cycle",
    "disco",
    "racing",
    "lottery",
    "intertwine",
    "blow-up",
    "collide",
    "heartbeat",
    "warning",
    "ocean",
    "echo",
}


def _extra_tlv2_effect_frame(
    effect_key: str,
    led_count: int,
    frame_index: int,
    frame_count: int,
    colors: tuple[tuple[int, int, int], ...],
    primary: tuple[int, int, int],
    accent: tuple[int, int, int],
    direction_step: int,
) -> list[tuple[int, int, int]]:
    def palette(index: int) -> tuple[int, int, int]:
        return colors[index % len(colors)]

    progress = frame_index / max(1, frame_count)
    phase = progress * math.tau
    frame: list[tuple[int, int, int]] = []

    if effect_key == "staggered":
        group = max(2, led_count // 6)
        active = (frame_index * 4 // max(1, frame_count)) % 4
        for led_index in range(led_count):
            lane = (led_index // group) % 4
            level = 1.0 if lane == active else 0.18
            frame.append(_scale_rgb(palette(lane), level))
        return _maybe_reverse_generated_frame(frame, direction_step)

    if effect_key == "tide":
        for led_index in range(led_count):
            x = led_index / max(1, led_count - 1)
            wave = (math.sin((x * 2.4 * direction_step + progress) * math.tau) + 1.0) / 2.0
            frame.append(_mix_rgb(palette(0), palette(1), wave))
        return frame

    if effect_key == "mixing":
        for led_index in range(led_count):
            x = led_index / max(1, led_count)
            a = (math.sin((x + progress * direction_step) * math.tau) + 1.0) / 2.0
            b = (math.sin((x * 2.0 - progress * direction_step) * math.tau) + 1.0) / 2.0
            frame.append(_mix_rgb(_mix_rgb(palette(0), palette(1), a), palette(2), b * 0.65))
        return frame

    if effect_key == "voice":
        bars = max(4, min(10, led_count // 3))
        for led_index in range(led_count):
            bar = min(bars - 1, led_index * bars // led_count)
            local = (led_index * bars) % led_count / max(1, led_count)
            height = 0.2 + 0.8 * ((math.sin(phase * (1.0 + bar * 0.17) + bar) + 1.0) / 2.0)
            frame.append(_scale_rgb(palette(bar), 1.0 if local < height else 0.08))
        return _maybe_reverse_generated_frame(frame, direction_step)

    if effect_key == "door":
        center = (led_count - 1) / 2.0
        opening = (math.sin(phase - math.pi / 2) + 1.0) / 2.0
        for led_index in range(led_count):
            distance = abs(led_index - center) / max(1.0, center)
            edge = 1.0 - abs(distance - opening) / 0.18
            source = _mix_rgb(primary, palette(led_index + frame_index // 4), 0.45)
            frame.append(_scale_rgb(source, max(0.04, min(1.0, edge))))
        return _maybe_reverse_generated_frame(frame, direction_step)

    if effect_key == "render":
        for led_index in range(led_count):
            hue = (led_index / max(1, led_count) + progress * direction_step) % 1.0
            frame.append(_mix_rgb(_hsv_to_rgb(hue, 0.9, 1.0), palette(led_index + frame_index // 4), 0.55))
        return frame

    if effect_key == "reflect":
        center = (led_count - 1) / 2.0
        for led_index in range(led_count):
            mirrored = abs(led_index - center) / max(1.0, center)
            level = (math.sin((mirrored * 1.8 - progress * direction_step) * math.tau) + 1.0) / 2.0
            base = _mix_rgb(_scale_rgb(primary, 0.08), palette(led_index + frame_index // 4), 0.5)
            frame.append(_mix_rgb(base, accent, level * 0.65))
        return frame

    if effect_key == "tail-chasing":
        comet_color = _mix_rgb(primary, palette(frame_index // 4), 0.55)
        return _meteor_frame(led_count, frame_index, frame_count, comet_color, direction_step)

    if effect_key == "paint":
        brush = int(progress * led_count) % led_count
        width = max(3, led_count // 5)
        for led_index in range(led_count):
            distance = (direction_step * (brush - led_index)) % led_count
            if distance < width:
                frame.append(_mix_rgb(palette(distance), primary, 1.0 - distance / width))
            else:
                frame.append(_scale_rgb(palette(led_index), 0.12))
        return frame

    if effect_key == "ping-pong":
        span = max(1, led_count - 1)
        cycle = (frame_index * 2 * span // max(1, frame_count)) % (2 * span)
        pos = cycle if cycle <= span else 2 * span - cycle
        if direction_step < 0:
            pos = span - pos
        for led_index in range(led_count):
            level = max(0.0, 1.0 - abs(led_index - pos) / 4.0)
            source = _mix_rgb(primary, palette(led_index + frame_index // 4), 0.5)
            frame.append(_scale_rgb(source, level))
        return frame

    if effect_key == "stack":
        fill = 1 + (frame_index * led_count // max(1, frame_count))
        color = palette(frame_index * len(colors) // max(1, frame_count))
        for led_index in range(led_count):
            frame.append(color if led_index < fill else _scale_rgb(color, 0.08))
        return _maybe_reverse_generated_frame(frame, direction_step)

    if effect_key == "cover-cycle":
        segment = max(2, led_count // len(colors))
        shift = direction_step * frame_index * led_count // max(1, frame_count)
        for led_index in range(led_count):
            frame.append(palette((led_index + shift) // segment))
        return frame

    if effect_key == "disco":
        block = max(2, led_count // 6)
        beat = frame_index // 3
        for led_index in range(led_count):
            block_index = (led_index // block + beat * direction_step) % len(colors)
            strobe = ((led_index * 7 + frame_index * 11) % 23) < 4
            level = 1.0 if strobe or (beat + led_index // block) % 2 == 0 else 0.28
            frame.append(_scale_rgb(palette(block_index), level))
        return frame

    if effect_key == "racing":
        stripe = max(2, led_count // 8)
        shift = direction_step * frame_index * 2
        for led_index in range(led_count):
            active = ((led_index + shift) // stripe) % 2 == 0
            stripe_color = palette((led_index + shift) // stripe)
            frame.append(
                _mix_rgb(primary, stripe_color, 0.5)
                if active
                else _mix_rgb(_scale_rgb(accent, 0.18), stripe_color, 0.25)
            )
        return frame

    if effect_key == "lottery":
        for led_index in range(led_count):
            hit = ((led_index * 17 + frame_index * 23) % 53) < 7
            glow = ((led_index * 5 + frame_index) % 19) == 0
            frame.append(palette(led_index + frame_index) if hit else (_scale_rgb(accent, 0.35) if glow else (0, 0, 0)))
        return _maybe_reverse_generated_frame(frame, direction_step)

    if effect_key == "intertwine":
        for led_index in range(led_count):
            x = led_index / max(1, led_count)
            a = (math.sin((x * 2.0 + progress * direction_step) * math.tau) + 1.0) / 2.0
            b = (math.sin((x * 2.0 - progress * direction_step) * math.tau) + 1.0) / 2.0
            frame.append(_mix_rgb(_scale_rgb(palette(0), a), _scale_rgb(palette(1), b), 0.5))
        return frame

    if effect_key == "blow-up":
        center = (led_count - 1) / 2.0
        radius = (progress * max(1, led_count)) % max(1, led_count)
        for led_index in range(led_count):
            distance = abs(led_index - center)
            ring = max(0.0, 1.0 - abs(distance - radius) / 3.0)
            flash = max(0.0, 1.0 - radius / max(2.0, led_count / 2.0))
            source = primary if led_index <= center else accent
            frame.append(_mix_rgb(_scale_rgb(source, ring), palette(led_index + frame_index // 4), flash * 0.35))
        return _maybe_reverse_generated_frame(frame, direction_step)

    if effect_key == "collide":
        span = max(1, led_count - 1)
        pos = frame_index * span // max(1, frame_count)
        left = pos
        right = span - pos
        for led_index in range(led_count):
            level = max(0.0, 1.0 - min(abs(led_index - left), abs(led_index - right)) / 4.0)
            burst = 0.0
            if abs(left - right) <= 2:
                burst = max(0.0, 1.0 - abs(led_index - span / 2.0) / max(2.0, led_count / 4.0))
            trail = _mix_rgb(_scale_rgb(primary, level), palette(led_index + frame_index // 4), 0.35)
            frame.append(_mix_rgb(trail, accent, burst))
        return _maybe_reverse_generated_frame(frame, direction_step)

    if effect_key == "heartbeat":
        beat_phase = progress * 2.0
        pulse = 0.12
        for center_point in (0.18, 0.34, 1.18, 1.34):
            pulse += max(0.0, 1.0 - abs(beat_phase - center_point) / 0.08) * 0.88
        chase = int(progress * led_count * direction_step) % max(1, led_count)
        for led_index in range(led_count):
            distance = min((led_index - chase) % led_count, (chase - led_index) % led_count)
            local = max(0.45, 1.0 - distance / max(3.0, led_count / 5.0))
            frame.append(_scale_rgb(_mix_rgb(primary, accent, 0.38), min(1.0, pulse * local)))
        return frame

    if effect_key == "warning":
        block = max(2, led_count // 4)
        flash = (frame_index // 6) % 2
        for led_index in range(led_count):
            lane = ((led_index * direction_step) // block) & 1
            color = primary if lane == flash else accent
            frame.append(color if (frame_index // 3) % 2 == 0 else _scale_rgb(color, 0.22))
        return frame

    if effect_key == "ocean":
        for led_index in range(led_count):
            x = led_index / max(1, led_count - 1)
            swell = (math.sin((x * 3.0 * direction_step + progress) * math.tau) + 1.0) / 2.0
            foam = max(0.0, math.sin((x * 8.0 - progress * 2.0 * direction_step) * math.tau))
            frame.append(_mix_rgb(_mix_rgb(palette(0), palette(1), swell * 0.7), (255, 255, 255), foam * 0.22))
        return frame

    if effect_key == "echo":
        origin = 0 if direction_step > 0 else led_count - 1
        span = max(1, led_count - 1)
        for led_index in range(led_count):
            distance = abs(led_index - origin)
            first = (progress * span * 2.0) % span
            second = (first - span * 0.32) % span
            level = max(
                max(0.0, 1.0 - abs(distance - first) / 2.2),
                max(0.0, 0.58 - abs(distance - second) / 3.0),
            )
            frame.append(_mix_rgb(_scale_rgb(primary, level), accent, level * 0.35))
        return frame

    raise LianLiWirelessError(f"unsupported generated TLV2 effect: {effect_key}")


def _meteor_shower_frame(
    led_count: int,
    frame_index: int,
    colors: tuple[tuple[int, int, int], ...],
    direction_step: int,
) -> list[tuple[int, int, int]]:
    frame = [(0, 0, 0)] * led_count
    for comet in range(max(3, led_count // 6)):
        head = (direction_step * (frame_index * (comet + 1) + comet * 7)) % led_count
        color = colors[comet % len(colors)]
        for tail_index in range(4):
            led = (head - direction_step * tail_index) % led_count
            frame[led] = _mix_rgb(frame[led], color, 1.0 - tail_index * 0.22)
    return frame


def _electric_current_frame(
    led_count: int,
    frame_index: int,
    color: tuple[int, int, int],
    accent: tuple[int, int, int],
    direction_step: int,
) -> list[tuple[int, int, int]]:
    base = _scale_rgb(color, 0.12)
    frame: list[tuple[int, int, int]] = []
    for led_index in range(led_count):
        directed_led = led_index if direction_step > 0 else led_count - led_index - 1
        spark = ((directed_led * 17 + frame_index * 11) % 29) in {0, 1, 5}
        surge = ((directed_led * 5 - frame_index * 3) % 23) == 0
        frame.append(accent if spark else (_mix_rgb(base, accent, 0.45) if surge else base))
    return frame


def _twinkle_frame(
    led_count: int,
    frame_index: int,
    frame_count: int,
    color: tuple[int, int, int],
    accent: tuple[int, int, int],
) -> list[tuple[int, int, int]]:
    frame: list[tuple[int, int, int]] = []
    for led_index in range(led_count):
        seed = (led_index * 37 + frame_index * 13) % frame_count
        if seed < 7:
            frame.append(_mix_rgb(color, accent, seed / 6.0))
        elif seed < 20:
            frame.append(_scale_rgb(color, (20 - seed) / 20.0))
        else:
            frame.append((0, 0, 0))
    return frame


def _ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def _resolve_channel(target: WirelessDeviceInfo, channel: int | None) -> int:
    resolved = target.channel if channel is None else int(channel)
    if not 0 <= resolved <= 255:
        raise LianLiWirelessError("channel must be in range 0-255")
    return resolved


def _bytes_to_mac(raw: bytes) -> str:
    return ":".join(f"{byte:02x}" for byte in raw)


def _mac_to_bytes(mac: str) -> bytes:
    parts = mac.split(":")
    if len(parts) != 6:
        raise LianLiWirelessError(f"invalid MAC address: {mac}")
    try:
        return bytes(int(part, 16) for part in parts)
    except ValueError as error:
        raise LianLiWirelessError(f"invalid MAC address: {mac}") from error


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _read_hex(path: Path) -> int | None:
    value = _read_text(path)
    if not value:
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None
