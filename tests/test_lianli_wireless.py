from __future__ import annotations

from usb9_lcd.lianli.wireless import (
    LianLiWirelessBackend,
    PyUsbEndpointTransport,
    DEFAULT_TINYUZ_DICT_SIZE,
    RF_GET_DEV_CMD,
    RF_MASTER_QUERY_CMD,
    RF_PACKET_HEADER,
    RF_PAYLOAD_SIZE,
    UDEV_RULES,
    WirelessDeviceInfo,
    build_bind_payload,
    build_master_query_request,
    build_pwm_payload,
    build_rf_chunks,
    build_static_rgb_payloads,
    build_unbind_payload,
    build_wireless_list_request,
    infer_led_count,
    parse_master_query_response,
    parse_wireless_snapshot,
    scan_known_usb_devices,
    tinyuz_compress_literal,
)


def _snapshot_payload() -> bytes:
    snapshot = bytearray(434)
    snapshot[0] = RF_GET_DEV_CMD
    snapshot[1] = 1
    record = bytearray(42)
    record[0:6] = bytes.fromhex("aabbccddeeff")
    record[6:12] = bytes.fromhex("102030405060")
    record[12] = 8
    record[13] = 3
    record[18] = 2
    record[19] = 3
    record[28:36] = bytes([0x04, 0xD2, 0x05, 0xDC, 0x00, 0x00, 0x00, 0x00])
    record[36:40] = bytes([80, 90, 100, 110])
    record[40] = 7
    record[41] = 28
    snapshot[4:46] = record
    return bytes(snapshot)


class FakeReceiverTransport:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.writes: list[bytes] = []
        self.read_sizes: list[int] = []

    def write(self, payload: bytes) -> int:
        self.writes.append(payload)
        return len(payload)

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self.payload[:size]


class FakeSenderTransport:
    def __init__(self, read_payload: bytes = b""):
        self.writes: list[bytes] = []
        self.read_payload = read_payload
        self.read_sizes: list[int] = []

    def write(self, payload: bytes) -> int:
        self.writes.append(payload)
        return len(payload)

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self.read_payload[:size]


def test_wireless_list_request_is_64_bytes():
    payload = build_wireless_list_request(2)

    assert len(payload) == 64
    assert payload[:2] == bytes([RF_GET_DEV_CMD, 2])


def test_master_query_request_and_response():
    request = build_master_query_request(8)
    response = bytes([RF_MASTER_QUERY_CMD]) + bytes.fromhex("102030405060") + bytes(57)

    parsed = parse_master_query_response(response, channel=8)

    assert len(request) == 64
    assert request[:2] == bytes([RF_MASTER_QUERY_CMD, 8])
    assert parsed == ("10:20:30:40:50:60", 8)
    assert parse_master_query_response(bytes([RF_MASTER_QUERY_CMD]) + bytes(63)) is None


def test_parse_wireless_snapshot_and_build_pwm_command():
    devices = parse_wireless_snapshot(_snapshot_payload())

    assert len(devices) == 1
    device = devices[0]
    assert device.mac == "aa:bb:cc:dd:ee:ff"
    assert device.master_mac == "10:20:30:40:50:60"
    assert device.is_bound is True
    assert device.fan_count == 3
    assert device.fan_rpm == (1234, 1500, 0, 0)
    assert device.pwm_values == (80, 90, 100, 110)

    pwm_payload = build_pwm_payload(device, [120])

    assert len(pwm_payload) == RF_PAYLOAD_SIZE
    assert pwm_payload[:17] == bytes.fromhex("1210aabbccddeeff102030405060030808")
    assert pwm_payload[17:21] == bytes([120, 120, 120, 120])

    chunks = build_rf_chunks(device.channel, device.rx_type, pwm_payload)

    assert len(chunks) == 4
    assert chunks[0][:4] == bytes([RF_PACKET_HEADER, 0, 8, 3])
    assert chunks[1][:4] == bytes([RF_PACKET_HEADER, 1, 8, 3])


def test_backend_lists_devices_and_sends_pwm_packets():
    receiver = FakeReceiverTransport(_snapshot_payload())
    sender = FakeSenderTransport()
    backend = LianLiWirelessBackend(sender=sender, receiver=receiver)

    snapshot = backend.list_devices()
    target = snapshot.devices[0]
    sent_count = backend.send_pwm(target, [120])

    assert snapshot.device_count == 1
    assert receiver.writes == [build_wireless_list_request()]
    assert receiver.read_sizes == [434]
    assert sent_count == 4
    assert len(sender.writes) == 4
    assert sender.writes[0][:4] == bytes([RF_PACKET_HEADER, 0, 8, 3])
    assert sender.writes[-1][:4] == bytes([RF_PACKET_HEADER, 3, 8, 3])


def test_backend_queries_master_mac_from_sender():
    response = bytes([RF_MASTER_QUERY_CMD]) + bytes.fromhex("102030405060") + bytes(57)
    sender = FakeSenderTransport(read_payload=response)
    backend = LianLiWirelessBackend(sender=sender)

    result = backend.query_master_mac(channel=8)

    assert result == ("10:20:30:40:50:60", 8)
    assert sender.writes == [build_master_query_request(8)]
    assert sender.read_sizes == [64]


def test_backend_builds_motherboard_pwm_sync_packets():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    backend = LianLiWirelessBackend()

    packets = backend.build_motherboard_pwm_sync_packets(target)

    assert len(packets) == 4
    assert packets[0].hex().startswith("100008031210aabbccddeeff10203040506003080806060606")


def test_build_bind_packets_for_unbound_receiver():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    target = WirelessDeviceInfo(
        mac=target.mac,
        master_mac="00:00:00:00:00:00",
        channel=target.channel,
        rx_type=0,
        device_type=target.device_type,
        fan_count=target.fan_count,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=target.fan_rpm,
        command_sequence=target.command_sequence,
        raw=target.raw,
    )

    payload = build_bind_payload(
        target,
        master_mac="10:20:30:40:50:60",
        rx_type=3,
    )
    packets = LianLiWirelessBackend().build_bind_packets(
        target,
        master_mac="10:20:30:40:50:60",
        rx_type=3,
    )

    assert payload[:21] == bytes.fromhex(
        "1210aabbccddeeff102030405060030801505a646e"
    )
    assert len(packets) == 4
    assert packets[0].hex().startswith(
        "100008001210aabbccddeeff102030405060030801505a646e"
    )


def test_build_unbind_packets_for_bound_receiver():
    target = parse_wireless_snapshot(_snapshot_payload())[0]

    payload = build_unbind_payload(target)
    packets = LianLiWirelessBackend().build_unbind_packets(target)

    assert payload[:21] == bytes.fromhex(
        "1210aabbccddeeff000000000000000800505a646e"
    )
    assert len(packets) == 4
    assert packets[0].hex().startswith(
        "100008031210aabbccddeeff000000000000000800505a646e"
    )


def test_static_rgb_payload_builds_led_effect_packets():
    target = parse_wireless_snapshot(_snapshot_payload())[0]

    payloads = build_static_rgb_payloads(
        target,
        (0, 0, 0),
        interval_ms=50,
        effect_index=0x01020304,
    )

    assert infer_led_count(target) == 132
    assert len(payloads) >= 2
    first = payloads[0]
    assert len(first) == RF_PAYLOAD_SIZE
    assert first[:20] == bytes.fromhex("1220aabbccddeeff1020304050600102030400") + bytes([len(payloads)])
    assert int.from_bytes(first[20:24], "big") == len(tinyuz_compress_literal(bytes(132 * 3)))
    assert first[25:27] == bytes([0, 1])
    assert first[27] == 132
    assert first[32:34] == bytes([0, 50])
    assert first[34:38] == DEFAULT_TINYUZ_DICT_SIZE.to_bytes(4, "little")


def test_backend_builds_static_rgb_rf_chunks():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    backend = LianLiWirelessBackend()

    packets = backend.build_static_rgb_packets(target, (255, 0, 0), effect_index=1)

    assert len(packets) % 4 == 0
    assert packets[0][:4] == bytes([RF_PACKET_HEADER, 0, 8, 3])
    assert packets[3][:4] == bytes([RF_PACKET_HEADER, 3, 8, 3])


def test_pyusb_transport_import_is_lazy():
    assert PyUsbEndpointTransport.__name__ == "PyUsbEndpointTransport"


def test_udev_rules_cover_l_wireless_receiver():
    assert any('ATTR{idProduct}=="8041"' in rule for rule in UDEV_RULES)


def test_scan_known_usb_devices_from_sysfs(tmp_path):
    device_root = tmp_path / "bus" / "usb" / "devices" / "1-1"
    device_root.mkdir(parents=True)
    (device_root / "idVendor").write_text("0416", encoding="utf-8")
    (device_root / "idProduct").write_text("8040", encoding="utf-8")
    (device_root / "manufacturer").write_text("Winbond", encoding="utf-8")
    (device_root / "product").write_text("SLV3TX_V1.6", encoding="utf-8")
    (device_root / "serial").write_text("abc", encoding="utf-8")
    (device_root / "busnum").write_text("001", encoding="utf-8")
    (device_root / "devnum").write_text("002", encoding="utf-8")

    devices = scan_known_usb_devices(tmp_path)

    assert len(devices) == 1
    assert devices[0].vid_pid == "0416:8040"
    assert devices[0].product == "SLV3TX_V1.6"
