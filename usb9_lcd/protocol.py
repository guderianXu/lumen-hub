from collections.abc import Iterator
from typing import Protocol

CONTROL_ABORT_COMMAND = bytes.fromhex("ff01")
CONTROL_REPORT_SIZE = 440
DATA_COMMAND = 0x08
DATA_HEADER_SIZE = 4
DATA_REPORT_SIZE = 1024
FIRST_DATA_PACKET_FLAG = 0x8000
SCREEN_TYPE_COMMAND = bytes([0x1F, 0x01, 0x00, 0x80])
DISPLAY_POWER_COMMAND = bytes([0x10, 0x01, 0x00, 0x80])
DISPLAY_BRIGHTNESS_COMMAND = bytes([0x12, 0x01, 0x00, 0x80])
CUSTOM_IMAGE_SCREEN_TYPE = 1
DEFAULT_BRIGHTNESS = 100


class WritableTransport(Protocol):
    def write(self, payload: bytes) -> int:
        ...


def chunk_frame(frame: bytes, report_size: int, header_size: int) -> Iterator[bytes]:
    payload_size = report_size - header_size
    if payload_size <= 0:
        raise ValueError("report_size must be larger than header_size")

    for offset in range(0, len(frame), payload_size):
        yield frame[offset : offset + payload_size]


def _abort_packet(report_size: int = CONTROL_REPORT_SIZE) -> bytes:
    if report_size < len(CONTROL_ABORT_COMMAND):
        raise ValueError("report_size must fit the control command")
    return CONTROL_ABORT_COMMAND + bytes(report_size - len(CONTROL_ABORT_COMMAND))


def _screen_type_packet(screen_type: int = CUSTOM_IMAGE_SCREEN_TYPE, report_size: int = CONTROL_REPORT_SIZE) -> bytes:
    if screen_type < 0 or screen_type > 0xFF:
        raise ValueError("screen_type must fit in one byte")

    payload = SCREEN_TYPE_COMMAND + bytes([screen_type])
    if report_size < len(payload):
        raise ValueError("report_size must fit the screen type command")
    return payload + bytes(report_size - len(payload))


def _display_power_packet(enabled: bool = True, report_size: int = CONTROL_REPORT_SIZE) -> bytes:
    payload = DISPLAY_POWER_COMMAND + bytes([0 if enabled else 1])
    if report_size < len(payload):
        raise ValueError("report_size must fit the display power command")
    return payload + bytes(report_size - len(payload))


def _display_brightness_packet(level: int = DEFAULT_BRIGHTNESS, report_size: int = CONTROL_REPORT_SIZE) -> bytes:
    if level < 0 or level > 0xFF:
        raise ValueError("brightness level must fit in one byte")

    payload = DISPLAY_BRIGHTNESS_COMMAND + bytes([level])
    if report_size < len(payload):
        raise ValueError("report_size must fit the display brightness command")
    return payload + bytes(report_size - len(payload))


def _data_packet(packet_index: int, total_packets: int, chunk: bytes, report_size: int = DATA_REPORT_SIZE) -> bytes:
    max_chunk_size = report_size - DATA_HEADER_SIZE
    if max_chunk_size <= 0:
        raise ValueError("report_size must be larger than data header size")
    if packet_index < 0:
        raise ValueError("packet_index must be non-negative")
    if total_packets < 0:
        raise ValueError("total_packets must be non-negative")
    if len(chunk) > max_chunk_size:
        raise ValueError("data chunk is too large for report size")

    packet_number = total_packets if packet_index == 0 else packet_index
    flags = FIRST_DATA_PACKET_FLAG if packet_index == 0 else 0
    header = bytes([DATA_COMMAND, packet_number & 0xFF]) + flags.to_bytes(2, "little")
    payload = header + chunk
    return payload + bytes(report_size - len(payload))


class LcdProtocol:
    def __init__(
        self,
        control: WritableTransport,
        data: WritableTransport,
        data_report_size: int = DATA_REPORT_SIZE,
        control_report_size: int = CONTROL_REPORT_SIZE,
    ):
        self.control = control
        self.data = data
        self.data_report_size = data_report_size
        self.control_report_size = control_report_size

    def upload_frame(self, frame: bytes) -> None:
        chunks = list(chunk_frame(frame, report_size=self.data_report_size, header_size=DATA_HEADER_SIZE))
        try:
            self.set_display_power(True)
            self.set_display_brightness(DEFAULT_BRIGHTNESS)
            self.set_screen_type(CUSTOM_IMAGE_SCREEN_TYPE)
            for packet_index, chunk in enumerate(chunks):
                self._write_exact(
                    self.data,
                    _data_packet(
                        packet_index=packet_index,
                        total_packets=len(chunks),
                        chunk=chunk,
                        report_size=self.data_report_size,
                    ),
                )
        except OSError:
            self.abort_upload()
            raise

    def abort_upload(self) -> None:
        self._write_exact(self.control, _abort_packet(report_size=self.control_report_size))

    def set_screen_type(self, screen_type: int) -> None:
        self._write_exact(self.control, _screen_type_packet(screen_type=screen_type, report_size=self.control_report_size))

    def set_display_power(self, enabled: bool) -> None:
        self._write_exact(self.control, _display_power_packet(enabled=enabled, report_size=self.control_report_size))

    def set_display_brightness(self, level: int) -> None:
        self._write_exact(self.control, _display_brightness_packet(level=level, report_size=self.control_report_size))

    def _write_exact(self, transport: WritableTransport, payload: bytes) -> None:
        report = b"\x00" + payload
        written = transport.write(report)
        if written != len(report):
            raise OSError(f"short HID write: wrote {written} of {len(report)} bytes")
