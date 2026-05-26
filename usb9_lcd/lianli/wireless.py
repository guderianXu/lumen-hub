from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol


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


class WirelessReceiverTransport(Protocol):
    def write(self, payload: bytes) -> int: ...

    def read(self, size: int) -> bytes: ...


class WirelessSenderTransport(Protocol):
    def write(self, payload: bytes) -> int: ...

    def read(self, size: int) -> bytes: ...


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
        request = build_wireless_list_request(page_count)
        written = self.receiver.write(request)
        if written != len(request):
            raise LianLiWirelessError(
                f"incomplete receiver request write ({written}/{len(request)})"
            )
        raw = self.receiver.read(expected_snapshot_length(page_count))
        return WirelessSnapshot(devices=parse_wireless_snapshot(raw), raw=raw)

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
        interval_ms: int = 50,
        effect_index: int = 1,
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
        interval_ms: int = 50,
        effect_index: int = 1,
        led_count: int | None = None,
        repeat_first_payload: int = RGB_FIRST_PAYLOAD_REPEAT_COUNT,
    ) -> list[bytes]:
        payloads = build_rainbow_rgb_payloads(
            target,
            frame_count=frame_count,
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

    def send_static_rgb(
        self,
        target: WirelessDeviceInfo,
        color: tuple[int, int, int],
        *,
        interval_ms: int = 50,
        effect_index: int = 1,
        led_count: int | None = None,
        repeat_first_payload: int = RGB_FIRST_PAYLOAD_REPEAT_COUNT,
    ) -> int:
        if self.sender is None:
            raise LianLiWirelessError("sender transport is not configured")
        packets = self.build_static_rgb_packets(
            target,
            color,
            interval_ms=interval_ms,
            effect_index=effect_index,
            led_count=led_count,
            repeat_first_payload=repeat_first_payload,
        )
        for packet in packets:
            written = self.sender.write(packet)
            if written != len(packet):
                raise LianLiWirelessError(
                    f"incomplete sender packet write ({written}/{len(packet)})"
                )
        return len(packets)

    def send_rainbow_rgb(
        self,
        target: WirelessDeviceInfo,
        *,
        frame_count: int = 24,
        interval_ms: int = 50,
        effect_index: int = 1,
        led_count: int | None = None,
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
            repeat_first_payload=repeat_first_payload,
        )
        for packet in packets:
            written = self.sender.write(packet)
            if written != len(packet):
                raise LianLiWirelessError(
                    f"incomplete sender packet write ({written}/{len(packet)})"
                )
        return len(packets)


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
        self._device = usb.core.find(idVendor=vendor_id, idProduct=product_id)
        if self._device is None:
            raise LianLiWirelessError(
                f"USB device {vendor_id:04x}:{product_id:04x} not found"
            )
        self._ensure_configuration()
        self._interface = interface
        self._claim_interface(interface)
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
            self._usb_util.release_interface(self._device, self._interface)
        except Exception:
            pass
        try:
            self._usb_util.dispose_resources(self._device)
        except Exception:
            pass

    def _ensure_configuration(self) -> None:
        try:
            self._device.get_active_configuration()
        except Exception:
            self._device.set_configuration()

    def _claim_interface(self, interface: int) -> None:
        try:
            if self._device.is_kernel_driver_active(interface):
                self._device.detach_kernel_driver(interface)
        except Exception:
            pass
        self._usb_util.claim_interface(self._device, interface)

    def _find_endpoints(
        self,
        interface: int,
        write_endpoint: int,
        read_endpoint: int,
    ) -> tuple[Any, Any]:
        configuration = self._device.get_active_configuration()
        descriptor = configuration[(interface, 0)]
        out_endpoint = None
        in_endpoint = None
        for endpoint in descriptor:
            address = int(endpoint.bEndpointAddress)
            if address == write_endpoint:
                out_endpoint = endpoint
            elif address == read_endpoint:
                in_endpoint = endpoint
        if out_endpoint is None or in_endpoint is None:
            raise LianLiWirelessError(
                f"USB endpoints 0x{write_endpoint:02x}/0x{read_endpoint:02x} not found"
            )
        return out_endpoint, in_endpoint


def create_pyusb_backend(timeout_ms: int = 1000) -> LianLiWirelessBackend:
    return LianLiWirelessBackend(
        sender=PyUsbEndpointTransport(
            RF_SENDER_VID,
            RF_SENDER_PID,
            timeout_ms=timeout_ms,
        ),
        receiver=PyUsbEndpointTransport(
            RF_RECEIVER_VID,
            RF_RECEIVER_PID,
            timeout_ms=timeout_ms,
        ),
    )


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
                    (record[28 + index * 2] << 8) | record[29 + index * 2]
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
    interval_ms: int = 50,
    effect_index: int = 1,
    led_count: int | None = None,
) -> list[bytes]:
    if not target.is_bound:
        raise LianLiWirelessError("receiver is not bound to a master controller")
    resolved_led_count = infer_led_count(target) if led_count is None else led_count
    if resolved_led_count <= 0:
        raise LianLiWirelessError("LED count must be positive")
    rgb = _rgb_bytes(color) * resolved_led_count
    return build_rgb_frame_payloads(
        target,
        rgb,
        led_count=resolved_led_count,
        frame_count=1,
        interval_ms=interval_ms,
        effect_index=effect_index,
    )


def build_rainbow_rgb_payloads(
    target: WirelessDeviceInfo,
    *,
    frame_count: int = 24,
    interval_ms: int = 50,
    effect_index: int = 1,
    led_count: int | None = None,
) -> list[bytes]:
    if not target.is_bound:
        raise LianLiWirelessError("receiver is not bound to a master controller")
    resolved_led_count = infer_led_count(target) if led_count is None else led_count
    rgb = generate_rainbow_rgb_frames(resolved_led_count, frame_count=frame_count)
    return build_rgb_frame_payloads(
        target,
        rgb,
        led_count=resolved_led_count,
        frame_count=frame_count,
        interval_ms=interval_ms,
        effect_index=effect_index,
    )


def build_rgb_frame_payloads(
    target: WirelessDeviceInfo,
    raw_rgb: bytes,
    *,
    led_count: int,
    frame_count: int,
    interval_ms: int = 50,
    effect_index: int = 1,
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
    compressed = tinyuz_compress_literal(raw_rgb)
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
            first_len = min(FIRST_LED_PACKET_DATA_MAX, len(compressed))
            if first_len:
                payload[
                    FIRST_LED_PACKET_DATA_OFFSET : FIRST_LED_PACKET_DATA_OFFSET + first_len
                ] = compressed[:first_len]
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


def generate_rainbow_rgb_frames(
    led_count: int,
    *,
    frame_count: int = 24,
    saturation: float = 1.0,
    value: float = 1.0,
) -> bytes:
    resolved_led_count = int(led_count)
    resolved_frame_count = int(frame_count)
    if resolved_led_count <= 0:
        raise LianLiWirelessError("LED count must be positive")
    if resolved_frame_count <= 0:
        raise LianLiWirelessError("frame count must be positive")
    frame_bytes = bytearray()
    for frame_index in range(resolved_frame_count):
        offset = frame_index / resolved_frame_count
        for led_index in range(resolved_led_count):
            hue = (led_index / max(1, resolved_led_count) + offset) % 1.0
            frame_bytes.extend(_hsv_to_rgb(hue, saturation, value))
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


def scan_known_usb_devices(sys_root: Path = Path("/sys")) -> list[LianLiUsbDevice]:
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
