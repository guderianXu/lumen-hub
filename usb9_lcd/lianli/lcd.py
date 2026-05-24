from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from collections.abc import Callable
from typing import Protocol

from usb9_lcd.lianli.wireless import LianLiWirelessError, PyUsbEndpointTransport


WIRELESS_LCD_KEY = b"slv3tuzx"
WIRELESS_LCD_HEADER_PLAINTEXT_SIZE = 504
WIRELESS_LCD_HEADER_SIZE = 512
WIRELESS_LCD_PAYLOAD_BUFFER = 102400
TL_WIRELESS_LCD_VID = 0x1CBE
TL_WIRELESS_LCD_PID = 0x0006


class WirelessLcdCommand(IntEnum):
    GET_VER = 10
    REBOOT = 11
    ROTATE = 13
    BRIGHTNESS = 14
    PUSH_JPG = 101
    GET_POS_INDEX = 201


@dataclass(frozen=True)
class WirelessLcdPacket:
    command: int
    timestamp_ms: int
    plaintext_header: bytes
    encrypted_header: bytes | None
    payload: bytes
    packet_length: int

    @property
    def encryption_available(self) -> bool:
        return self.encrypted_header is not None

    def to_transfer_bytes(self) -> bytes:
        if self.encrypted_header is None:
            raise LianLiWirelessError("wireless LCD transfer requires encrypted header")
        packet = bytearray(self.packet_length)
        packet[: len(self.encrypted_header)] = self.encrypted_header
        if self.payload:
            start = WIRELESS_LCD_HEADER_SIZE
            packet[start : start + len(self.payload)] = self.payload
        return bytes(packet)


class WirelessLcdTransport(Protocol):
    def write(self, payload: bytes) -> int: ...

    def read(self, size: int) -> bytes: ...


class LianLiWirelessLcdBackend:
    def __init__(
        self,
        *,
        transport: WirelessLcdTransport,
        timestamp_provider: Callable[[], int] | None = None,
    ) -> None:
        self.transport = transport
        self.timestamp_provider = timestamp_provider or (lambda: 0)

    def handshake(self) -> dict[str, int]:
        response = self.send_read_command(WirelessLcdCommand.GET_POS_INDEX)
        if not response or response[0] not in (
            WirelessLcdCommand.GET_POS_INDEX,
            WirelessLcdCommand.GET_VER,
        ):
            raise LianLiWirelessError("no valid wireless LCD handshake response")
        if response[0] == WirelessLcdCommand.GET_VER:
            return {"mode": 0, "frame_index": 0}
        return {
            "mode": response[8] if len(response) > 8 else 0,
            "frame_index": response[9] if len(response) > 9 else 0,
        }

    def firmware_version(self) -> dict[str, str]:
        response = self.send_read_command(WirelessLcdCommand.GET_VER)
        if not response or response[0] != WirelessLcdCommand.GET_VER:
            raise LianLiWirelessError("firmware request did not return expected data")
        return {"version": parse_wireless_lcd_version(response) or "unknown", "build": ""}

    def set_brightness(self, brightness: int) -> int:
        value = max(0, min(100, int(brightness)))
        return self.send_single_byte_command(WirelessLcdCommand.BRIGHTNESS, value)

    def set_rotation(self, degrees: int) -> int:
        mapping = {0: 0, 90: 1, 180: 2, 270: 3}
        if int(degrees) not in mapping:
            raise LianLiWirelessError("LCD rotation must be one of 0, 90, 180, 270")
        return self.send_single_byte_command(
            WirelessLcdCommand.ROTATE,
            mapping[int(degrees)],
        )

    def send_read_command(self, command: int | WirelessLcdCommand) -> bytes:
        packet = build_wireless_lcd_packet(
            command,
            timestamp_ms=int(self.timestamp_provider()) & 0xFFFFFFFF,
            encrypt=True,
        ).to_transfer_bytes()
        written = self.transport.write(packet)
        if written != len(packet):
            raise LianLiWirelessError(
                f"incomplete wireless LCD write ({written}/{len(packet)})"
            )
        return self.transport.read(WIRELESS_LCD_HEADER_SIZE)

    def send_single_byte_command(
        self,
        command: int | WirelessLcdCommand,
        value: int,
        *,
        drain_response: bool = True,
    ) -> int:
        packet = build_wireless_lcd_packet(
            command,
            single_byte=value,
            timestamp_ms=int(self.timestamp_provider()) & 0xFFFFFFFF,
            encrypt=True,
        ).to_transfer_bytes()
        written = self.transport.write(packet)
        if written != len(packet):
            raise LianLiWirelessError(
                f"incomplete wireless LCD write ({written}/{len(packet)})"
            )
        if drain_response:
            try:
                self.transport.read(WIRELESS_LCD_HEADER_SIZE)
            except Exception:
                pass
        return written


def create_pyusb_lcd_backend(timeout_ms: int = 5000) -> LianLiWirelessLcdBackend:
    return LianLiWirelessLcdBackend(
        transport=PyUsbEndpointTransport(
            TL_WIRELESS_LCD_VID,
            TL_WIRELESS_LCD_PID,
            timeout_ms=timeout_ms,
        )
    )


def build_wireless_lcd_packet(
    command: int | WirelessLcdCommand,
    *,
    payload: bytes = b"",
    single_byte: int | None = None,
    timestamp_ms: int = 0,
    encrypt: bool = False,
) -> WirelessLcdPacket:
    resolved_command = int(command)
    if payload and single_byte is not None:
        raise LianLiWirelessError("wireless LCD command cannot use payload and single_byte together")
    if len(payload) > WIRELESS_LCD_PAYLOAD_BUFFER - WIRELESS_LCD_HEADER_SIZE:
        raise LianLiWirelessError("wireless LCD payload is too large")

    header = bytearray(WIRELESS_LCD_HEADER_PLAINTEXT_SIZE)
    header[0] = resolved_command & 0xFF
    header[2] = 26
    header[3] = 109
    header[4:8] = int(timestamp_ms).to_bytes(4, "little", signed=False)
    if payload:
        header[8:12] = len(payload).to_bytes(4, "big", signed=False)
    elif single_byte is not None:
        header[8] = int(single_byte) & 0xFF

    encrypted_header = encrypt_wireless_lcd_header(bytes(header)) if encrypt else None
    packet_length = (
        WIRELESS_LCD_HEADER_SIZE
        if not payload
        else max(WIRELESS_LCD_PAYLOAD_BUFFER, WIRELESS_LCD_HEADER_SIZE + len(payload))
    )
    return WirelessLcdPacket(
        command=resolved_command,
        timestamp_ms=int(timestamp_ms) & 0xFFFFFFFF,
        plaintext_header=bytes(header),
        encrypted_header=encrypted_header,
        payload=payload,
        packet_length=packet_length,
    )


def encrypt_wireless_lcd_header(header: bytes) -> bytes:
    if len(header) != WIRELESS_LCD_HEADER_PLAINTEXT_SIZE:
        raise LianLiWirelessError(
            f"wireless LCD header must be {WIRELESS_LCD_HEADER_PLAINTEXT_SIZE} bytes"
        )
    des = _load_des_cipher()
    padded = _pkcs7_pad(header, 8)
    cipher = des.new(WIRELESS_LCD_KEY, des.MODE_CBC, iv=WIRELESS_LCD_KEY)
    return bytes(cipher.encrypt(padded))


def parse_wireless_lcd_version(packet: bytes) -> str:
    raw = packet[8 : 8 + 32]
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="ignore").strip()


def wireless_lcd_command_from_name(name: str) -> WirelessLcdCommand:
    normalized = name.strip().lower().replace("_", "-")
    mapping = {
        "get-ver": WirelessLcdCommand.GET_VER,
        "reboot": WirelessLcdCommand.REBOOT,
        "rotate": WirelessLcdCommand.ROTATE,
        "brightness": WirelessLcdCommand.BRIGHTNESS,
        "push-jpg": WirelessLcdCommand.PUSH_JPG,
        "get-pos-index": WirelessLcdCommand.GET_POS_INDEX,
        "handshake": WirelessLcdCommand.GET_POS_INDEX,
    }
    try:
        return mapping[normalized]
    except KeyError as error:
        raise LianLiWirelessError(f"unknown wireless LCD command: {name}") from error


def wireless_lcd_encryption_available() -> bool:
    try:
        _load_des_cipher()
    except LianLiWirelessError:
        return False
    return True


def _pkcs7_pad(payload: bytes, block_size: int) -> bytes:
    pad_len = block_size - (len(payload) % block_size)
    if pad_len == 0:
        pad_len = block_size
    return payload + bytes([pad_len]) * pad_len


def _load_des_cipher():  # noqa: ANN202
    try:
        from Cryptodome.Cipher import DES  # type: ignore[import-not-found]

        return DES
    except ImportError:
        try:
            from Crypto.Cipher import DES  # type: ignore[import-not-found]

            return DES
        except ImportError as error:
            raise LianLiWirelessError(
                "pycryptodomex is required to encrypt wireless LCD headers"
            ) from error
