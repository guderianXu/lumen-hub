from __future__ import annotations

import pytest

from usb9_lcd.lianli.lcd import (
    LianLiWirelessLcdBackend,
    WIRELESS_LCD_HEADER_PLAINTEXT_SIZE,
    WIRELESS_LCD_HEADER_SIZE,
    WIRELESS_LCD_PAYLOAD_BUFFER,
    WirelessLcdCommand,
    build_wireless_lcd_packet,
    encrypt_wireless_lcd_header,
    parse_wireless_lcd_version,
    wireless_lcd_command_from_name,
)
from usb9_lcd.lianli.wireless import LianLiWirelessError


class FakeLcdTransport:
    def __init__(self, response: bytes):
        self.response = response
        self.writes: list[bytes] = []
        self.read_sizes: list[int] = []

    def write(self, payload: bytes) -> int:
        self.writes.append(payload)
        return len(payload)

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self.response[:size]


def test_build_wireless_lcd_brightness_header_plaintext():
    packet = build_wireless_lcd_packet(
        WirelessLcdCommand.BRIGHTNESS,
        single_byte=65,
        timestamp_ms=0x01020304,
    )

    assert packet.command == 14
    assert packet.packet_length == WIRELESS_LCD_HEADER_SIZE
    assert len(packet.plaintext_header) == WIRELESS_LCD_HEADER_PLAINTEXT_SIZE
    assert packet.plaintext_header[:9] == bytes.fromhex("0e001a6d0403020141")
    assert packet.encrypted_header is None
    assert packet.encryption_available is False


def test_build_wireless_lcd_push_jpg_header_records_payload_length():
    packet = build_wireless_lcd_packet(
        WirelessLcdCommand.PUSH_JPG,
        payload=b"\xff\xd8jpeg",
        timestamp_ms=1,
    )

    assert packet.command == 101
    assert packet.payload == b"\xff\xd8jpeg"
    assert packet.packet_length == WIRELESS_LCD_PAYLOAD_BUFFER
    assert packet.plaintext_header[:12] == bytes.fromhex("65001a6d0100000000000006")


def test_wireless_lcd_command_from_name_accepts_handshake_alias():
    assert wireless_lcd_command_from_name("handshake") == WirelessLcdCommand.GET_POS_INDEX
    assert wireless_lcd_command_from_name("get-ver") == WirelessLcdCommand.GET_VER


def test_wireless_lcd_rejects_oversized_payload():
    with pytest.raises(LianLiWirelessError, match="too large"):
        build_wireless_lcd_packet(
            WirelessLcdCommand.PUSH_JPG,
            payload=bytes(WIRELESS_LCD_PAYLOAD_BUFFER),
        )


def test_wireless_lcd_encrypt_requires_optional_dependency_when_missing():
    header = build_wireless_lcd_packet(WirelessLcdCommand.GET_VER).plaintext_header
    try:
        encrypted = encrypt_wireless_lcd_header(header)
    except LianLiWirelessError as error:
        assert "pycryptodomex" in str(error)
    else:
        assert len(encrypted) == WIRELESS_LCD_HEADER_SIZE


def test_wireless_lcd_backend_reads_handshake(monkeypatch):
    import usb9_lcd.lianli.lcd as lcd

    monkeypatch.setattr(lcd, "encrypt_wireless_lcd_header", lambda header: bytes([0xAA]) * 512)
    response = bytearray(512)
    response[0] = WirelessLcdCommand.GET_POS_INDEX
    response[8] = 5
    response[9] = 7
    transport = FakeLcdTransport(bytes(response))
    backend = LianLiWirelessLcdBackend(transport=transport, timestamp_provider=lambda: 0x01020304)

    payload = backend.handshake()

    assert payload == {"mode": 5, "frame_index": 7}
    assert len(transport.writes) == 1
    assert len(transport.writes[0]) == WIRELESS_LCD_HEADER_SIZE
    assert transport.writes[0] == bytes([0xAA]) * 512
    assert transport.read_sizes == [WIRELESS_LCD_HEADER_SIZE]


def test_wireless_lcd_backend_reads_firmware(monkeypatch):
    import usb9_lcd.lianli.lcd as lcd

    monkeypatch.setattr(lcd, "encrypt_wireless_lcd_header", lambda header: bytes([0xBB]) * 512)
    response = bytearray(512)
    response[0] = WirelessLcdCommand.GET_VER
    response[8:18] = b"1.2.3\x00xxxx"
    backend = LianLiWirelessLcdBackend(
        transport=FakeLcdTransport(bytes(response)),
        timestamp_provider=lambda: 0,
    )

    assert backend.firmware_version() == {"version": "1.2.3", "build": ""}
    assert parse_wireless_lcd_version(bytes(response)) == "1.2.3"


def test_wireless_lcd_backend_sets_brightness_and_rotation(monkeypatch):
    import usb9_lcd.lianli.lcd as lcd

    encrypted_headers: list[bytes] = []

    def fake_encrypt(header: bytes) -> bytes:
        encrypted_headers.append(header)
        return bytes([len(encrypted_headers)]) * 512

    monkeypatch.setattr(lcd, "encrypt_wireless_lcd_header", fake_encrypt)
    transport = FakeLcdTransport(bytes(512))
    backend = LianLiWirelessLcdBackend(
        transport=transport,
        timestamp_provider=lambda: 0x01020304,
    )

    assert backend.set_brightness(65) == WIRELESS_LCD_HEADER_SIZE
    assert backend.set_rotation(180) == WIRELESS_LCD_HEADER_SIZE

    assert len(transport.writes) == 2
    assert transport.writes[0] == bytes([1]) * 512
    assert transport.writes[1] == bytes([2]) * 512
    assert encrypted_headers[0][:9] == bytes.fromhex("0e001a6d0403020141")
    assert encrypted_headers[1][:9] == bytes.fromhex("0d001a6d0403020102")
    assert transport.read_sizes == [WIRELESS_LCD_HEADER_SIZE, WIRELESS_LCD_HEADER_SIZE]


def test_wireless_lcd_backend_rejects_invalid_rotation():
    backend = LianLiWirelessLcdBackend(transport=FakeLcdTransport(bytes(512)))

    with pytest.raises(LianLiWirelessError, match="rotation"):
        backend.set_rotation(45)
