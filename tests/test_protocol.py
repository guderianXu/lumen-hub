import pytest

from usb9_lcd.protocol import (
    CONTROL_REPORT_SIZE,
    DATA_HEADER_SIZE,
    DATA_REPORT_SIZE,
    LcdProtocol,
    _abort_packet,
    _data_packet,
    _display_power_packet,
    _display_brightness_packet,
    _screen_type_packet,
    chunk_frame,
)


class FakeTransport:
    def __init__(self, short_by: int = 0):
        self.writes: list[bytes] = []
        self.short_by = short_by

    def write(self, payload: bytes) -> int:
        self.writes.append(payload)
        return len(payload) - self.short_by


def test_chunk_frame_uses_payload_size_after_header():
    chunks = list(chunk_frame(bytes(range(10)), report_size=8, header_size=4))

    assert chunks == [bytes(range(4)), bytes(range(4, 8)), bytes(range(8, 10))]


def test_chunk_frame_keeps_exact_boundaries():
    chunks = list(chunk_frame(bytes(range(8)), report_size=8, header_size=4))

    assert chunks == [bytes(range(4)), bytes(range(4, 8))]


def test_chunk_frame_rejects_invalid_report_size():
    with pytest.raises(ValueError, match="report_size must be larger than header_size"):
        list(chunk_frame(b"abc", report_size=4, header_size=4))


def test_abort_packet_encodes_control_abort_command_and_padding():
    assert _abort_packet(report_size=8) == bytes.fromhex("ff01") + bytes(6)


def test_abort_packet_uses_default_control_report_size():
    assert len(_abort_packet()) == CONTROL_REPORT_SIZE


def test_screen_type_packet_encodes_custom_image_mode_and_padding():
    assert _screen_type_packet(screen_type=1, report_size=8) == bytes([0x1F, 0x01, 0x00, 0x80, 0x01]) + bytes(3)


def test_screen_type_packet_rejects_values_outside_one_byte():
    with pytest.raises(ValueError, match="screen_type must fit in one byte"):
        _screen_type_packet(screen_type=256, report_size=8)


def test_display_power_packet_encodes_on_state_and_padding():
    assert _display_power_packet(enabled=True, report_size=8) == bytes([0x10, 0x01, 0x00, 0x80, 0x00]) + bytes(3)
    assert _display_power_packet(enabled=False, report_size=8) == bytes([0x10, 0x01, 0x00, 0x80, 0x01]) + bytes(3)


def test_display_brightness_packet_encodes_level_and_padding():
    assert _display_brightness_packet(level=100, report_size=8) == bytes([0x12, 0x01, 0x00, 0x80, 0x64]) + bytes(3)


def test_display_brightness_packet_rejects_values_outside_one_byte():
    with pytest.raises(ValueError, match="brightness level must fit in one byte"):
        _display_brightness_packet(level=256, report_size=8)


def test_upload_frame_prefixes_hidraw_writes_with_zero_report_id():
    control = FakeTransport()
    data = FakeTransport()
    protocol = LcdProtocol(control=control, data=data, data_report_size=16)

    protocol.upload_frame(b"abcdef")

    assert control.writes[0][0] == 0
    assert control.writes[0][1:6] == bytes([0x10, 0x01, 0x00, 0x80, 0x00])
    assert len(control.writes[0]) == 441
    assert data.writes[0][0] == 0
    assert data.writes[0][1:5] == bytes([0x08, 0x01, 0x00, 0x80])
    assert len(data.writes[0]) == 17


def test_data_packet_encodes_first_packet_total_count_flag_and_padding():
    assert _data_packet(packet_index=0, total_packets=2, chunk=b"abcdef", report_size=16) == (
        bytes([0x08, 0x02, 0x00, 0x80]) + b"abcdef" + bytes(6)
    )


def test_data_packet_encodes_followup_packet_index_and_no_flag():
    assert _data_packet(packet_index=1, total_packets=2, chunk=b"abcdef", report_size=16) == (
        bytes([0x08, 0x01, 0x00, 0x00]) + b"abcdef" + bytes(6)
    )


def test_data_packet_wraps_large_packet_numbers_to_one_byte_like_infohub():
    assert _data_packet(packet_index=0, total_packets=452, chunk=b"", report_size=8)[:4] == bytes([0x08, 0xC4, 0x00, 0x80])
    assert _data_packet(packet_index=300, total_packets=452, chunk=b"", report_size=8)[:4] == bytes([0x08, 0x2C, 0x00, 0x00])


def test_data_packet_rejects_chunks_larger_than_report_payload():
    with pytest.raises(ValueError, match="data chunk is too large for report size"):
        _data_packet(packet_index=0, total_packets=1, chunk=b"abcdef", report_size=9)


def test_data_packet_rejects_invalid_report_size():
    with pytest.raises(ValueError, match="report_size must be larger than data header size"):
        _data_packet(packet_index=0, total_packets=1, chunk=b"", report_size=DATA_HEADER_SIZE)


def test_data_packet_rejects_negative_packet_index():
    with pytest.raises(ValueError, match="packet_index must be non-negative"):
        _data_packet(packet_index=-1, total_packets=1, chunk=b"a", report_size=16)


def test_upload_frame_writes_data_reports_without_start_finish_control_packets():
    control = FakeTransport()
    data = FakeTransport()
    protocol = LcdProtocol(control=control, data=data, data_report_size=16)

    protocol.upload_frame(b"abcdef")

    assert control.writes == [
        b"\x00" + _display_power_packet(enabled=True),
        b"\x00" + _display_brightness_packet(level=100),
        b"\x00" + _screen_type_packet(screen_type=1),
    ]
    assert data.writes == [b"\x00" + bytes([0x08, 0x01, 0x00, 0x80]) + b"abcdef" + bytes(6)]


def test_upload_frame_pads_final_multi_report():
    control = FakeTransport()
    data = FakeTransport()
    protocol = LcdProtocol(control=control, data=data, data_report_size=10)

    protocol.upload_frame(b"abcdef")

    assert data.writes == [
        b"\x00" + bytes([0x08, 0x01, 0x00, 0x80]) + b"abcdef",
    ]


def test_upload_frame_sends_abort_and_raises_on_short_data_write():
    control = FakeTransport()
    data = FakeTransport(short_by=1)
    protocol = LcdProtocol(control=control, data=data, data_report_size=16)

    with pytest.raises(OSError, match="short HID write: wrote 16 of 17 bytes"):
        protocol.upload_frame(b"abcdef")

    assert control.writes == [
        b"\x00" + _display_power_packet(enabled=True),
        b"\x00" + _display_brightness_packet(level=100),
        b"\x00" + _screen_type_packet(screen_type=1),
        b"\x00" + _abort_packet(),
    ]


def test_abort_upload_raises_on_short_control_write():
    control = FakeTransport(short_by=1)
    data = FakeTransport()
    protocol = LcdProtocol(control=control, data=data, data_report_size=16, control_report_size=8)

    with pytest.raises(OSError, match="short HID write: wrote 8 of 9 bytes"):
        protocol.abort_upload()


def test_upload_frame_accepts_default_320_jpeg_sized_frame():
    control = FakeTransport()
    data = FakeTransport()
    protocol = LcdProtocol(control=control, data=data)

    protocol.upload_frame(bytes(320 * 320 * 2))

    assert control.writes == [
        b"\x00" + _display_power_packet(enabled=True),
        b"\x00" + _display_brightness_packet(level=100),
        b"\x00" + _screen_type_packet(screen_type=1),
    ]
    assert len(data.writes) == 201
    assert data.writes[0][1:5] == bytes([0x08, 0xC9, 0x00, 0x80])
    assert data.writes[1][1:5] == bytes([0x08, 0x01, 0x00, 0x00])
    assert len(data.writes[0]) == DATA_REPORT_SIZE + 1


def test_upload_frame_accepts_more_than_255_packets_by_wrapping_low_byte_header():
    control = FakeTransport()
    data = FakeTransport()
    protocol = LcdProtocol(control=control, data=data, data_report_size=5)

    protocol.upload_frame(bytes(260))

    assert len(data.writes) == 260
    assert data.writes[0][1:5] == bytes([0x08, 0x04, 0x00, 0x80])
    assert data.writes[256][1:5] == bytes([0x08, 0x00, 0x00, 0x00])
