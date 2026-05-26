from __future__ import annotations

import json
import lzma

from usb9_lcd.lianli.wireless import (
    LianLiWirelessBackend,
    PyUsbEndpointTransport,
    DEFAULT_TINYUZ_DICT_SIZE,
    FIRST_LED_PACKET_DATA_MAX,
    FIRST_LED_PACKET_DATA_OFFSET,
    LED_DATA_CHUNK,
    RF_GET_DEV_CMD,
    RF_MASTER_QUERY_CMD,
    RF_PACKET_HEADER,
    RF_PAYLOAD_SIZE,
    RGB_FIRST_PAYLOAD_REPEAT_COUNT,
    UDEV_RULES,
    WirelessDeviceInfo,
    build_bind_payload,
    build_master_query_request,
    build_pwm_payload,
    build_rainbow_rgb_payloads,
    build_rgb_frame_payloads,
    build_rf_chunks,
    build_static_rgb_payloads,
    build_unbind_payload,
    build_wireless_list_request,
    extract_led_count_hint,
    extract_motherboard_pwm,
    generate_rainbow_rgb_frames,
    infer_led_count,
    parse_master_query_response,
    parse_wireless_snapshot,
    scan_known_usb_devices,
    tinyuz_compress_literal,
)
import usb9_lcd.lianli.wireless as wireless_module
from usb9_lcd.lianli.artifact import (
    analyze_artifact_file,
    analyze_artifact_tree,
    artifact_evidence_matrix,
    diff_artifact_files,
    extract_hid_js_commands,
    extract_wireless_js_clues,
)
from usb9_lcd.lianli.changelog import analyze_lconnect_changelog_text
from usb9_lcd.lianli.capture import (
    analyze_capture_packets,
    capture_protocol_report_from_analysis,
    capture_protocol_report_file,
    capture_gap_report,
    capture_replay_plan_from_analysis,
    capture_signature_match_packets,
    capture_set_report,
    capture_timeline_report_file,
    capture_triage_report_file,
    capture_transport_report_file,
    compare_capture_packets,
    linux_control_action_plan_report,
    linux_control_manifest_report,
    linux_control_packet_compare_report,
    linux_control_packet_preview_report,
    linux_control_preflight_report,
    linux_control_target_registry_report,
    linux_control_write_gate_report,
    linux_interface_contract_report,
    load_capture_packets,
    protocol_signature_catalog,
    summarize_capture_dir,
    usb_capture_readiness,
    windows_capture_note,
    windows_capture_runbook,
    windows_capture_plan,
)
import usb9_lcd.lianli.capture as capture_module


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


def _snapshot_payload_with_motherboard_pwm(indicator: int, value: int) -> bytes:
    snapshot = bytearray(_snapshot_payload())
    snapshot[2] = indicator
    snapshot[3] = value
    return bytes(snapshot)


def _snapshot_payload_with_pwm_values(pwm_values: tuple[int, int, int, int], sequence: int) -> bytes:
    snapshot = bytearray(_snapshot_payload())
    snapshot[40:44] = bytes(pwm_values)
    snapshot[44] = sequence
    return bytes(snapshot)


def _live_list_payload_from_snapshot(snapshot: bytes, *, operation: str = "live-list") -> dict[str, object]:
    devices = parse_wireless_snapshot(snapshot)
    return {
        "operation": operation,
        "device_count": len(devices),
        "motherboard_pwm": extract_motherboard_pwm(snapshot),
        "devices": [
            {
                "mac": device.mac,
                "master_mac": device.master_mac,
                "is_bound": device.is_bound,
                "channel": device.channel,
                "rx_type": device.rx_type,
                "device_type": device.device_type,
                "fan_count": device.fan_count,
                "pwm_values": list(device.pwm_values),
                "fan_rpm": list(device.fan_rpm),
                "command_sequence": device.command_sequence,
                "raw_hex": device.raw.hex(),
            }
            for device in devices
        ],
    }


def _write_tshark_json_capture(
    path,
    packets: list[bytes],
    *,
    product_ids: list[str] | None = None,
) -> None:
    product_ids = product_ids or ["0x8040"] * len(packets)
    path.write_text(
        json.dumps(
            [
                {
                    "_source": {
                        "layers": {
                            "frame": {
                                "frame.number": str(index + 1),
                                "frame.time_relative": f"{0.1 + index * 0.1:.6f}",
                                "frame.time_delta": f"{0.0 if index == 0 else 0.1:.6f}",
                                "frame.time_delta_displayed": f"{0.0 if index == 0 else 0.1:.6f}",
                                "frame.time_epoch": f"{1700000000.0 + index * 0.1:.6f}",
                            },
                            "usb": {
                                "usb.bus_id": "1",
                                "usb.device_address": "8" if product_id.endswith("8041") else "7",
                                "usb.endpoint_address": "0x01",
                                "usb.endpoint_number": "1",
                                "usb.endpoint_direction": "OUT",
                                "usb.transfer_type": "URB_BULK",
                                "usb.idVendor": "0x0416",
                                "usb.idProduct": product_id,
                            },
                            "usb.capdata": packet.hex(":"),
                        }
                    }
                }
                for index, (packet, product_id) in enumerate(zip(packets, product_ids, strict=True))
            ]
        ),
        encoding="utf-8",
    )


def _write_linux_experiment_summary_inputs(path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "validate-readonly.json").write_text(
        json.dumps(
            {
                "operation": "validate-readonly",
                "output_dir": str(path),
                "step_count": 3,
                "ok_count": 3,
                "error_count": 0,
                "steps": [
                    {"name": "scan", "status": "ok", "error": ""},
                    {"name": "live-list", "status": "ok", "error": ""},
                    {"name": "live-master", "status": "ok", "error": ""},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (path / "safe-pwm-experiment.json").write_text(
        json.dumps(
            {
                "operation": "safe-pwm-experiment",
                "target": "aa:bb:cc:dd:ee:ff",
                "output_dir": str(path),
                "packets_written": 4,
                "likely_effective": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _write_lianli_usb_sysfs_and_dev(sys_root, dev_root) -> None:
    for name, vid, pid, product, busnum, devnum in (
        ("1-1", "0416", "8040", "SLV3TX_V1.6", "001", "002"),
        ("1-2", "0416", "8041", "SLV3RX_V1.6", "001", "003"),
    ):
        device_root = sys_root / "bus" / "usb" / "devices" / name
        device_root.mkdir(parents=True)
        (device_root / "idVendor").write_text(vid, encoding="utf-8")
        (device_root / "idProduct").write_text(pid, encoding="utf-8")
        (device_root / "manufacturer").write_text("LIAN LI", encoding="utf-8")
        (device_root / "product").write_text(product, encoding="utf-8")
        (device_root / "serial").write_text(name, encoding="utf-8")
        (device_root / "busnum").write_text(busnum, encoding="utf-8")
        (device_root / "devnum").write_text(devnum, encoding="utf-8")
        dev_node = dev_root / "bus" / "usb" / busnum / devnum
        dev_node.parent.mkdir(parents=True, exist_ok=True)
        dev_node.write_bytes(b"")
        dev_node.chmod(0o660)


def _mac_bytes(mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", ""))


def _tinyuz_backref_static_rgb(color: tuple[int, int, int], led_count: int) -> bytes:
    raw = bytes(color) * led_count
    state = wireless_module._TinyUzLiteralState(DEFAULT_TINYUZ_DICT_SIZE)
    for byte in bytes(color):
        state.out_type(1)
        state.code.append(byte)
        state.have_data_back = True
    state.out_type(0)
    state.out_len(len(raw) - len(color) - 2, pack_bit=1)
    state.out_type(0)
    state.out_dict_pos(len(color))
    state.have_data_back = False
    state.out_type(0)
    state.out_len(3, pack_bit=1)
    state.out_dict_pos(0)
    state.reset_types()
    return bytes(state.code)


def _tinyuz_literal_line_rgb(colors: list[tuple[int, int, int]]) -> bytes:
    raw = b"".join(bytes(color) for color in colors)
    state = wireless_module._TinyUzLiteralState(DEFAULT_TINYUZ_DICT_SIZE)
    state.out_type(0)
    state.out_len(1, pack_bit=1)
    state.out_dict_pos(0)
    state.out_len(len(raw) - 15, pack_bit=2)
    state.code.extend(raw)
    state.have_data_back = True
    state.out_type(0)
    state.out_len(3, pack_bit=1)
    state.out_type(0)
    state.out_dict_pos(0)
    state.reset_types()
    return bytes(state.code)


def test_changelog_analyzer_ranks_wireless_versions_and_downloads():
    html = """
    <h2>L3 v2.1.23</h2>
    <p>發表於：04-24-2026</p>
    <a href="https://example.test/L-Connect-v2.1.23.zip">下載</a>
    <p>增加對 H2 OLED 曲面螢幕的支持</p>
    <h2>L3 v2.0.33</h2>
    <p>發表於：08-25-2025</p>
    <a href="https://example.test/L-Connect-v2.0.33.exe">下載</a>
    <p>修復了影響無線風扇控制的無線控制器（加密狗）TX 崩潰問題。</p>
    <p>修正了無線風扇隨機遺失之前的 L-Connect 3 設定的問題。</p>
    <h2>L3 v2.0.29</h2>
    <p>Released on: 2025-05-29</p>
    <p>改進了 L-Wireless Utility 中的設備識別：先前在綁定區域中列出的未知設備現在具有標記的設備類型。</p>
    """

    analysis = analyze_lconnect_changelog_text(html, source="fixture.html", top=2)
    entries = {entry["version"]: entry for entry in analysis["entries"]}

    assert analysis["operation"] == "analyze-changelog"
    assert analysis["entry_count"] == 3
    assert analysis["wireless_entry_count"] == 2
    assert analysis["summary"]["top_versions"] == ["2.0.33", "2.0.29"]
    assert entries["2.0.33"]["release_date"] == "2025-08-25"
    assert entries["2.0.33"]["download_urls"] == ["https://example.test/L-Connect-v2.0.33.exe"]
    assert {"wireless-controller", "wireless-fan"} <= set(entries["2.0.33"]["matched_keywords"])
    assert {"binding", "device-identification", "l-wireless"} <= set(entries["2.0.29"]["matched_keywords"])
    assert "changelog" in analysis["warnings"][0].lower()


def _rgb_payloads_with_compressed(
    target: WirelessDeviceInfo,
    compressed: bytes,
    *,
    led_count: int,
    effect_index: int = 1,
    interval_ms: int = 50,
) -> list[bytes]:
    data_packets = (len(compressed) + LED_DATA_CHUNK - 1) // LED_DATA_CHUNK
    total_packets = 1 + data_packets
    payloads: list[bytes] = []
    data_offset = 0
    for packet_index in range(total_packets):
        payload = bytearray(RF_PAYLOAD_SIZE)
        payload[0] = 0x12
        payload[1] = 0x20
        payload[2:8] = _mac_bytes(target.mac)
        payload[8:14] = _mac_bytes(target.master_mac)
        payload[14:18] = int(effect_index).to_bytes(4, "big")
        payload[18] = packet_index & 0xFF
        payload[19] = total_packets & 0xFF
        if packet_index == 0:
            payload[20:24] = len(compressed).to_bytes(4, "big")
            payload[25:27] = (1).to_bytes(2, "big")
            payload[27] = led_count & 0xFF
            payload[32:34] = int(interval_ms).to_bytes(2, "big")
            first_len = min(FIRST_LED_PACKET_DATA_MAX, len(compressed))
            payload[FIRST_LED_PACKET_DATA_OFFSET : FIRST_LED_PACKET_DATA_OFFSET + first_len] = compressed[:first_len]
            data_offset += first_len
        else:
            chunk_len = min(LED_DATA_CHUNK, len(compressed) - data_offset)
            payload[20 : 20 + chunk_len] = compressed[data_offset : data_offset + chunk_len]
            data_offset += chunk_len
        payloads.append(bytes(payload))
    return payloads


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


def test_extract_motherboard_pwm_from_receiver_snapshot():
    payload = _snapshot_payload_with_motherboard_pwm(10, 10)
    receiver = FakeReceiverTransport(payload)
    backend = LianLiWirelessBackend(receiver=receiver)

    snapshot = backend.list_devices()

    assert extract_motherboard_pwm(payload) == 127
    assert snapshot.motherboard_pwm == 127
    assert extract_motherboard_pwm(_snapshot_payload_with_motherboard_pwm(0x80, 10)) is None
    assert extract_motherboard_pwm(b"snapshot") is None


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


def test_backend_builds_motherboard_pwm_mirror_packets():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    sender = FakeSenderTransport()
    backend = LianLiWirelessBackend(sender=sender)

    packets = backend.build_motherboard_pwm_mirror_packets(target, 127)
    sent_count = backend.send_motherboard_pwm_mirror(target, 127)

    assert len(packets) == 4
    assert sent_count == 4
    assert packets[0].hex().startswith("100008031210aabbccddeeff1020304050600308087f7f7f7f")
    assert sender.writes[0] == packets[0]


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


def test_infer_led_count_uses_snapshot_hint_when_heuristic_is_ambiguous():
    raw = bytearray(_snapshot_payload())
    record = bytearray(raw[4:46])
    record[18] = 99
    record[19] = 0
    record[31] = 96
    raw[4:46] = record
    target = parse_wireless_snapshot(bytes(raw))[0]

    assert extract_led_count_hint(target.raw) == 96
    assert infer_led_count(target) == 96


def test_static_rgb_payload_accepts_led_count_override():
    target = parse_wireless_snapshot(_snapshot_payload())[0]

    payloads = build_static_rgb_payloads(target, (0, 0, 255), led_count=12)

    assert payloads[0][27] == 12
    assert int.from_bytes(payloads[0][20:24], "big") == len(tinyuz_compress_literal(bytes([0, 0, 255]) * 12))


def test_rainbow_rgb_payload_builds_multi_frame_led_effect_packets():
    target = parse_wireless_snapshot(_snapshot_payload())[0]

    raw = generate_rainbow_rgb_frames(led_count=132, frame_count=3)
    payloads = build_rgb_frame_payloads(
        target,
        raw,
        led_count=132,
        frame_count=3,
        interval_ms=40,
        effect_index=0x01020304,
    )

    assert len(raw) == 132 * 3 * 3
    assert raw[:9] == bytes([255, 0, 0, 255, 11, 0, 255, 23, 0])
    assert payloads == build_rainbow_rgb_payloads(
        target,
        frame_count=3,
        interval_ms=40,
        effect_index=0x01020304,
    )
    first = payloads[0]
    assert first[:20] == bytes.fromhex("1220aabbccddeeff1020304050600102030400") + bytes([len(payloads)])
    assert first[25:27] == bytes([0, 3])
    assert first[27] == 132
    assert first[32:34] == bytes([0, 40])


def test_backend_builds_static_rgb_rf_chunks():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    backend = LianLiWirelessBackend()

    packets = backend.build_static_rgb_packets(target, (255, 0, 0), effect_index=1)
    payloads = build_static_rgb_payloads(target, (255, 0, 0), effect_index=1)

    assert len(packets) == (len(payloads) + RGB_FIRST_PAYLOAD_REPEAT_COUNT - 1) * 4
    assert packets[0][:4] == bytes([RF_PACKET_HEADER, 0, 8, 3])
    assert packets[3][:4] == bytes([RF_PACKET_HEADER, 3, 8, 3])
    assert packets[0:4] == packets[4:8]


def test_backend_builds_rainbow_rgb_rf_chunks_and_analyzer_decodes_frames():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    backend = LianLiWirelessBackend()

    packets = backend.build_rainbow_rgb_packets(
        target,
        frame_count=3,
        interval_ms=40,
        effect_index=2,
    )
    analysis = analyze_capture_packets(packets, source="rainbow.txt")

    assert analysis["summary"]["rf_operations"] == {"live-rgb": 1}
    first = analysis["rf_frames"][0]
    assert first["frame_count"] == 3
    assert first["interval_ms"] == 40
    assert first["rgb_payload"]["decoded_frame_count"] == 3
    assert first["rgb_payload"]["decoded_led_count"] == 132
    assert first["rgb_payload"]["unique_color_count"] >= 132
    assert first["rgb_payload"]["sequence_rf_frame_count"] == len(packets) // 4
    assert "static_color" not in first["rgb_payload"]


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


def test_artifact_analyzer_finds_static_usb_and_crypto_clues(tmp_path):
    path = tmp_path / "artifact.bin"
    path.write_bytes(
        b"MZ"
        + bytes(30)
        + b"L-Connect 3 references 0416:8040 and slv3tuzx"
        + bytes.fromhex("16044180")
        + "LIAN LI".encode("utf-16le")
    )

    analysis = analyze_artifact_file(path)
    labels = {match["label"] for match in analysis["matches"]}

    assert analysis["operation"] == "analyze-artifact"
    assert analysis["file_type"] == "pe"
    assert analysis["size"] == path.stat().st_size
    assert len(analysis["sha256"]) == 64
    assert "RF sender VID:PID text" in labels
    assert "RF receiver VID/PID little-endian" in labels
    assert "wireless LCD DES key" in labels
    assert "LIAN LI company name UTF-16LE" in labels
    assert analysis["summary"]["categories"]["usb-id"] == 2
    assert "RF sender VID:PID text" in analysis["summary"]["high_confidence_patterns"]
    assert analysis["warnings"] == []


def test_artifact_analyzer_finds_official_hid_code_clues(tmp_path):
    path = tmp_path / "index.js"
    path.write_bytes(
        b"const Fa=41220,H1=[41219,41221];"
        b"loadHidDevices(fa);"
        b"syncSLV2FanRpm2MotherBoard();"
        b"device.write([[224,16,98,17]]);"
        b"controller.getFanRpmCmdData(0,120);"
    )

    analysis = analyze_artifact_file(path)
    labels = {match["label"] for match in analysis["matches"]}

    assert "HID device loader call" in labels
    assert "SL V2 fan motherboard PWM sync action" in labels
    assert "HID motherboard RPM sync bytes" in labels
    assert "fan RPM command builder" in labels
    assert "SL V2 fan HID PID decimal" in labels
    assert "AL V2 fan HID PID decimal" in labels
    assert "SL V2 A105 fan HID PID decimal" in labels
    assert analysis["summary"]["categories"]["hid-code"] == 3
    assert analysis["summary"]["categories"]["hid-command"] == 1
    assert analysis["summary"]["categories"]["usb-id"] == 3


def test_artifact_diff_reports_changed_ranges_and_new_static_clues(tmp_path):
    before = tmp_path / "before.bin"
    after = tmp_path / "after.bin"
    before.write_bytes(
        b"stable-prefix-"
        + b"\x00" * 32
        + b"0416:8041"
        + b"\x11" * 64
        + b"stable-suffix"
    )
    after.write_bytes(
        b"stable-prefix-"
        + b"\x22" * 48
        + b"0416:8040 L-Wireless"
        + b"PK\x03\x04"
        + b"\x33" * 64
        + b"stable-suffix"
    )

    analysis = diff_artifact_files(before, after, block_size=16)

    assert analysis["operation"] == "diff-artifacts"
    assert analysis["common_prefix_bytes"] == len(b"stable-prefix-")
    assert analysis["common_suffix_bytes"] == len(b"stable-suffix")
    assert analysis["size_delta"] == after.stat().st_size - before.stat().st_size
    added_labels = {item["label"] for item in analysis["static_match_delta"]["added"]}
    removed_labels = {item["label"] for item in analysis["static_match_delta"]["removed"]}
    assert "RF sender VID:PID text" in added_labels
    assert "L-Wireless text" in added_labels
    assert "RF receiver VID:PID text" in removed_labels
    assert any(item["label"] == "ZIP local file header" for item in analysis["after_changed_magic"])
    assert analysis["block_similarity"]["block_size"] == 16


def test_hid_js_extractor_structures_official_fan_commands(tmp_path):
    path = tmp_path / "index.js"
    path.write_text(
        "const Fa=41220,H1=[41219,41221];"
        "function loadALV2FanHidDevices(){loadHidDevices(Fa)}"
        "function loadSLV2FanHidDevices(){loadHidDevices(H1)}"
        "syncALV2FanController2MotherBoard(){Ge.write(t,[[224,16,97,r?1:0,0,0]])}"
        "syncSLV2FanRpm2MotherBoard(){Ge.write(t,[[224,16,98,r?17:16],[224,16,98,r?34:32]])}"
        "checkingSLV2FanControllerRpm(){Ge.write(t,[[224,80,0,0,0,0,0,0]]);x.device.getInputReport(224,65)}"
        "checkingSLV2FanControllerVersion(){x.device.write([224,80,1,0,0,0,0,0])}"
        "getFanRpmCmdData(r,t){const n=max;return[224,32+this.roads[r].id-1,0,Math.floor(t*100/n)]}"
        "getRoadSortCmd(){const r=[224,16,99];return r}"
        "getFanRoadPositionLedCmd(){i.push([224,16,96,this.roads[r].id,this.roads[r].lightNum,0,0])}"
        "getLedEffectData(){return[224,16+2*s+(t?0:1),n.modeIndex,0,0,0]}"
        "locateSLV2FanControllerLed(){Ge.write(t,[[224,16,47,r.id-1,0,0]])}",
        encoding="utf-8",
    )

    analysis = extract_hid_js_commands(path)
    pids = {item["decimal"] for item in analysis["product_ids"]}
    command_names = {item["name"] for item in analysis["command_templates"]}

    assert analysis["operation"] == "extract-hid-js"
    assert analysis["matched_file_count"] == 1
    assert pids == {41219, 41220, 41221}
    assert "hid-device-loader" in command_names
    assert "motherboard-lighting-sync" in command_names
    assert "motherboard-rpm-sync" in command_names
    assert "fan-rpm-poll" in command_names
    assert "fan-input-report" in command_names
    assert "fan-rpm-set" in command_names
    assert "led-road-config" in command_names
    assert "Extracted commands are for official AL V2" in analysis["warnings"][0]


def test_wireless_js_extractor_summarizes_usb_ipc_and_product_clues(tmp_path):
    path = tmp_path / "wireless.js"
    path.write_text(
        "const sender='0416:8040';"
        "const receiver='0416:8041';"
        "const name='L-Wireless';"
        "const key='slv3tuzx';"
        "ipcRenderer.send('message-queue', JSON.stringify({event:'scanWireless'}));"
        "pipe.writeSettings('WirelessConfig', name);",
        encoding="utf-8",
    )

    analysis = extract_wireless_js_clues(path)
    clue_names = {item["name"] for item in analysis["clues"]}
    ipc_names = {item["name"] for item in analysis["ipc_events"]}
    settings_by_key = {item["settings_key"]: item for item in analysis["settings_keys"]}
    capture_hint_names = {item["name"] for item in analysis["capture_hints"]}

    assert analysis["operation"] == "extract-wireless-js"
    assert analysis["matched_file_count"] == 1
    assert {"rf-sender-usb-id", "rf-receiver-usb-id", "l-wireless-product", "wireless-lcd-des-key"} <= clue_names
    assert ipc_names == {"scanWireless"}
    assert settings_by_key["WirelessConfig"]["operation"] == "writeSettings"
    assert {"device-discovery", "wireless-config-settings"} <= capture_hint_names
    assert analysis["summary"]["categories"]["usb-id"] == 2
    assert analysis["summary"]["categories"]["ipc"] == 3
    assert analysis["summary"]["ipc_event_occurrences"] == 1
    assert analysis["summary"]["settings_key_occurrences"] == 1
    assert analysis["summary"]["capture_hint_scenarios"]["baseline"] == 2
    assert analysis["summary"]["top_ipc_events"] == ["scanWireless"]
    assert analysis["summary"]["top_settings_keys"] == ["writeSettings:WirelessConfig"]
    assert analysis["summary"]["confidence"]["high"] >= 4
    assert analysis["warnings"] == [
        "Generic wireless/receiver/sender terms may come from UI or library code; prioritize high-confidence USB/product/API clues."
    ]


def test_artifact_evidence_matrix_ranks_versions_by_protocol_value(tmp_path):
    rf_artifact = tmp_path / "L-Connect-v2.1.17.exe"
    rf_artifact.write_bytes(b"MZ L-Connect 3 L-Wireless 0416:8040 0416:8041")
    hid_js = tmp_path / "assets-v2.1.23.js"
    hid_js.write_text(
        "const H1=[41219,41221];"
        "function loadSLV2FanHidDevices(){loadHidDevices(H1)}"
        "syncSLV2FanRpm2MotherBoard(){x.write(t,[[224,16,98,r?17:16]])}",
        encoding="utf-8",
    )
    wireless_js = tmp_path / "assets-v2.1.23-wireless.js"
    wireless_js.write_text(
        "ipcRenderer.send('message-queue', JSON.stringify({event:'updateControllerVersion'}));"
        "pipe.readSettingsLike('ALV2Controller-%');",
        encoding="utf-8",
    )
    (tmp_path / "analyze-artifact-v2.1.17-exe.json").write_text(
        json.dumps(analyze_artifact_file(rf_artifact)),
        encoding="utf-8",
    )
    (tmp_path / "extract-hid-js-v2.1.23.json").write_text(
        json.dumps(extract_hid_js_commands(hid_js)),
        encoding="utf-8",
    )
    (tmp_path / "extract-wireless-js-v2.1.23.json").write_text(
        json.dumps(extract_wireless_js_clues(wireless_js)),
        encoding="utf-8",
    )
    (tmp_path / "analyze-artifact-v2.0.32-nsis-payload.json").write_text(
        json.dumps(
            {
                "operation": "analyze-artifact",
                "path": "L-Connect-v2.0.32-[0]",
                "entropy_sample": 7.99,
                "nsis_header": {"signature": "DEADBEEF NullsoftInst"},
                "summary": {
                    "categories": {"usb-id": 1},
                    "confidence": {"medium": 1},
                    "high_confidence_patterns": [],
                },
                "matches": [
                    {
                        "label": "RF sender VID/PID little-endian",
                        "category": "usb-id",
                        "confidence": "medium",
                        "count": 1,
                    }
                ],
                "warnings": ["High entropy input with raw medium-confidence hits."],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "analyze-changelog-official.json").write_text(
        json.dumps(
            {
                "operation": "analyze-changelog",
                "source": "fixture-changelog.html",
                "entries": [
                    {
                        "version": "2.1.17",
                        "release_date": "2026-03-02",
                        "wireless_score": 48,
                        "download_urls": ["https://example.invalid/L-Connect-v2.1.17.exe"],
                        "matched_keywords": ["rf", "binding", "rpm-pwm"],
                        "category_scores": {"transport": 14, "binding": 12, "fan": 10},
                        "matched_lines": [
                            {
                                "text": "RF unbind/rebind fan speed settings behavior changed.",
                                "keywords": ["rf", "binding", "rpm-pwm"],
                                "score": 18,
                            }
                        ],
                    },
                    {
                        "version": "2.0.34",
                        "release_date": "2025-09-19",
                        "wireless_score": 24,
                        "matched_keywords": ["motherboard-sync", "rpm-pwm"],
                        "category_scores": {"fan": 19},
                        "matched_lines": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    matrix = artifact_evidence_matrix(tmp_path)
    versions = {item["version"]: item for item in matrix["versions"]}

    assert matrix["operation"] == "artifact-evidence-matrix"
    assert matrix["version_count"] == 4
    assert matrix["summary"]["high_priority_capture_versions"] == ["v2.1.17"]
    assert matrix["summary"]["rf_low_confidence_versions"] == ["v2.0.32"]
    assert matrix["summary"]["changelog_top_versions"][:2] == ["v2.1.17", "v2.0.34"]
    assert matrix["summary"]["recommended_capture_versions"][0]["version"] == "v2.1.17"
    assert versions["v2.0.32"]["assessment"] == "rf-usb-low-confidence-lead"
    assert versions["v2.0.32"]["capture_priority"] == "medium"
    assert versions["v2.0.32"]["rf_high_confidence_static_labels"] == {}
    assert versions["v2.0.32"]["rf_low_confidence_static_labels"] == {"RF sender VID/PID little-endian": 1}
    assert versions["v2.1.17"]["assessment"] == "rf-usb-protocol-lead"
    assert versions["v2.1.17"]["capture_priority"] == "high"
    assert versions["v2.1.17"]["changelog_score"] == 48
    assert versions["v2.1.17"]["changelog_keywords"] == {"binding": 1, "rf": 1, "rpm-pwm": 1}
    assert versions["v2.1.17"]["capture_recommendation_score"] == 1048
    assert versions["v2.1.17"]["changelog_evidence"][0]["score"] == 18
    assert set(versions["v2.1.17"]["rf_static_labels"]) == {
        "RF receiver VID:PID text",
        "RF sender VID:PID text",
    }
    assert any("USBPcap" in step for step in versions["v2.1.17"]["recommended_next_steps"])
    assert any("Official changelog wireless score is 48" in step for step in versions["v2.1.17"]["recommended_next_steps"])
    assert versions["v2.0.34"]["changelog_score"] == 24
    assert versions["v2.1.23"]["assessment"] == "wired-hid-fan-lead"
    assert versions["v2.1.23"]["hid_command_categories"] == {"discovery": 1, "sync": 1}
    assert versions["v2.1.23"]["wireless_js_ipc_events"] == {"updateControllerVersion": 1}
    assert versions["v2.1.23"]["wireless_js_settings_keys"] == {"readSettingsLike:ALV2Controller-%": 1}
    assert versions["v2.1.23"]["wireless_js_capture_hints"] == {
        "device-discovery": 1,
        "fan-controller-settings": 2,
    }
    assert versions["v2.1.23"]["wireless_js_capture_hint_scenarios"] == {
        "baseline": 1,
        "direct-fan-speed": 2,
        "motherboard-pwm-sync": 2,
        "sort-quick-sync": 2,
    }
    assert "v2.1.23" in matrix["summary"]["wired_hid_fan_versions"]
    assert matrix["summary"]["wireless_js_interface_versions"] == ["v2.1.23"]
    assert matrix["summary"]["wireless_js_capture_hint_versions"] == ["v2.1.23"]
    assert matrix["summary"]["wireless_js_capture_hint_scenarios"]["direct-fan-speed"] == 2


def test_artifact_analyzer_finds_slv3_sensor_asset_models(tmp_path):
    path = tmp_path / "SENSOR_6_Blue.data"
    path.write_bytes(
        b"slv3.models, Version=1.0.0.0"
        b"slv3.models.SensorVideoInfo"
        b"lianli.ThemeEngine"
        b"ThemeEngine.Theme"
    )

    analysis = analyze_artifact_file(path)
    labels = {match["label"] for match in analysis["matches"]}

    assert "SL V3 sensor video model" in labels
    assert "SL V3 sensor model assembly" in labels
    assert "LIAN LI theme engine model" in labels
    assert "ThemeEngine theme type" in labels
    assert analysis["summary"]["categories"]["asset-model"] == 4


def test_artifact_analyzer_identifies_nsis_payload_header(tmp_path):
    path = tmp_path / "payload.nsis"
    path.write_bytes(
        (112).to_bytes(4, "little")
        + bytes.fromhex("efbeadde")
        + b"NullsoftInst"
        + (102848).to_bytes(4, "little")
        + (2048).to_bytes(4, "little")
        + b"\x00" * 64
    )

    analysis = analyze_artifact_file(path)

    assert analysis["file_type"] == "nsis"
    assert analysis["nsis_header"]["signature"] == "DEADBEEF NullsoftInst"
    assert analysis["nsis_header"]["flags"] == 112
    assert analysis["nsis_header"]["unsupported_flags"] == 112
    assert analysis["nsis_header"]["standard_flags"] == []
    assert analysis["nsis_header"]["header_size"] == 102848
    assert analysis["nsis_header"]["data_size"] == 2048
    assert analysis["nsis_probe"]["standard_flags_supported"] is False
    assert analysis["nsis_probe"]["direct_decompression_hits"] == []


def test_artifact_analyzer_probes_nsis_raw_lzma_payload(tmp_path):
    plain = b"LianLiHeader\x00payload-metadata"
    filters = [
        {
            "id": lzma.FILTER_LZMA1,
            "dict_size": 1 << 20,
            "lc": 3,
            "lp": 0,
            "pb": 2,
        }
    ]
    compressed = lzma.compress(plain, format=lzma.FORMAT_RAW, filters=filters)
    lzma_props = bytes([(2 * 5 + 0) * 9 + 3]) + (1 << 20).to_bytes(4, "little")
    body = lzma_props + compressed
    data_size = 28 + len(body)
    path = tmp_path / "payload.nsis"
    path.write_bytes(
        (0).to_bytes(4, "little")
        + bytes.fromhex("efbeadde")
        + b"NullsoftInst"
        + (64).to_bytes(4, "little")
        + data_size.to_bytes(4, "little")
        + body
    )

    analysis = analyze_artifact_file(path)
    hits = analysis["nsis_probe"]["direct_decompression_hits"]

    assert analysis["nsis_header"]["unsupported_flags"] == 0
    assert analysis["nsis_header"]["size_delta"] == 0
    assert analysis["nsis_probe"]["standard_flags_supported"] is True
    assert hits[0]["offset"] == 28
    assert hits[0]["method"] == "nsis-lzma-raw"
    assert hits[0]["output_size"] == len(plain)
    assert "LianLiHeader" in hits[0]["output_prefix_text"]


def test_artifact_tree_scans_directory_and_marks_large_nsis_payload(tmp_path):
    root = tmp_path / "extract"
    root.mkdir()
    (root / "version.txt").write_bytes(b"L-Connect 3 0416:8040 slv3tuzx")
    (root / "payload.nsis").write_bytes(
        (112).to_bytes(4, "little")
        + bytes.fromhex("efbeadde")
        + b"NullsoftInst"
        + (102848).to_bytes(4, "little")
        + (2048).to_bytes(4, "little")
        + b"x" * 32
    )

    analysis = analyze_artifact_tree(root, max_file_size=32)

    assert analysis["operation"] == "analyze-artifact-tree"
    assert analysis["file_count"] == 2
    assert analysis["scanned_file_count"] == 1
    assert analysis["skipped_file_count"] == 1
    assert analysis["matched_file_count"] == 1
    assert analysis["summary"]["categories"]["usb-id"] == 1
    assert analysis["summary"]["nsis_file_count"] == 1
    skipped = next(file for file in analysis["files"] if file.get("skipped"))
    assert skipped["file_type"] == "nsis"
    assert skipped["nsis_header"]["header_size"] == 102848
    assert any("skipped" in warning.lower() for warning in analysis["warnings"])


def test_artifact_tree_reports_asset_path_clues(tmp_path):
    root = tmp_path / "assets"
    sensor_dir = root / "assets" / "wireless-sensor"
    sensor_dir.mkdir(parents=True)
    (sensor_dir / "SENSOR_6_GPULoad.data").write_bytes(b"binary")
    (sensor_dir / "Clock1.turtheme").write_bytes(b"theme")
    slv3_dir = root / "assets" / "slv3" / "animation"
    slv3_dir.mkdir(parents=True)
    (slv3_dir / "demo.gif").write_bytes(b"GIF89a")

    analysis = analyze_artifact_tree(root)

    assert analysis["matched_file_count"] == 0
    assert analysis["path_matched_file_count"] == 3
    assert analysis["summary"]["categories"]["asset"] >= 4
    top_labels = {entry["label"] for entry in analysis["summary"]["top_matches"]}
    assert "wireless sensor asset path" in top_labels
    assert "wireless sensor data filename" in top_labels
    assert "wireless sensor theme filename" in top_labels
    assert "SL V3 asset path" in top_labels


def test_capture_analyzer_decodes_pwm_rf_chunks():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    payload = build_pwm_payload(target, [120])
    packets = build_rf_chunks(target.channel, target.rx_type, payload)

    analysis = analyze_capture_packets(packets, source="unit-test")

    assert analysis["operation"] == "analyze-capture"
    assert analysis["packet_count"] == 4
    assert analysis["summary"]["kinds"] == {"rf-chunk": 4}
    assert analysis["summary"]["rf_operations"] == {"live-pwm": 1}
    assert analysis["summary"]["receiver_macs"] == ["aa:bb:cc:dd:ee:ff"]
    frame = analysis["rf_frames"][0]
    assert frame["operation"] == "live-pwm"
    assert frame["target_mac"] == "aa:bb:cc:dd:ee:ff"
    assert frame["master_mac"] == "10:20:30:40:50:60"
    assert frame["pwm_values"] == [120, 120, 120, 120]


def test_capture_analyzer_decodes_literal_static_rgb_color():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    packets = LianLiWirelessBackend().build_static_rgb_packets(target, (255, 0, 0), effect_index=1)

    analysis = analyze_capture_packets(packets, source="official-rgb.txt")

    assert analysis["summary"]["rf_operations"] == {"live-rgb": 1}
    assert analysis["summary"]["rf_frame_operations"] == {"live-rgb": 7}
    assert analysis["summary"]["replay_hint_count"] == 1
    assert analysis["summary"]["rgb_sequence_count"] == 1
    first = analysis["rf_frames"][0]
    assert first["operation"] == "live-rgb"
    assert first["packet_index"] == 0
    assert first["rgb_sequence_primary_index"] == 0
    assert first["rgb_sequence_member_index"] == 0
    assert first["rgb_decode_status"] == "decoded-literal"
    assert first["rgb_payload"]["decode_status"] == "decoded-literal"
    assert first["rgb_payload"]["static_color"] == [255, 0, 0]
    assert first["rgb_payload"]["static_color_hex"] == "#ff0000"
    assert first["rgb_payload"]["decoded_length"] == 132 * 3
    assert first["rgb_payload"]["expected_decoded_length"] == 132 * 3
    assert first["rgb_payload"]["sequence_rf_frame_count"] == 7
    assert first["rgb_payload"]["sequence_frame_indexes"] == list(range(7))
    assert first["rgb_payload"]["first_packet_retransmit_count"] == RGB_FIRST_PAYLOAD_REPEAT_COUNT
    assert first["rgb_payload"]["first_packet_frame_indexes"] == list(range(RGB_FIRST_PAYLOAD_REPEAT_COUNT))
    assert first["rgb_payload"]["unique_color_count"] == 1
    assert first["rgb_payload"]["sample_colors_hex"] == ["#ff0000"]
    assert len(first["rgb_payload"]["decoded_sha256"]) == 64
    assert analysis["rf_frames"][1]["rgb_sequence_primary_index"] == 0
    assert analysis["rf_frames"][1]["rgb_sequence_member_index"] == 1
    assert "replay_hint" not in analysis["rf_frames"][1]
    hint = first["replay_hint"]
    assert hint["dry_run"]["argv"][2] == "dry-run-rgb"
    assert hint["dry_run"]["argv"][-4:] == ["--color", "255,0,0", "--effect-index", "1"]
    assert "--led-count" in hint["dry_run"]["argv"]
    assert hint["dry_run"]["argv"][hint["dry_run"]["argv"].index("--led-count") + 1] == "132"
    assert hint["compare_capture"]["argv"][2:5] == ["compare-capture", "official-rgb.txt", "rgb"]
    assert "--led-count" in hint["compare_capture"]["argv"]
    assert hint["compare_capture"]["argv"][hint["compare_capture"]["argv"].index("--led-count") + 1] == "132"

    report = capture_protocol_report_from_analysis(analysis)
    rgb = report["operations"]["live-rgb"]
    assert report["devices"]["aa:bb:cc:dd:ee:ff"]["rf_frame_operations"] == {"live-rgb": 7}
    assert report["devices"]["aa:bb:cc:dd:ee:ff"]["operations"] == {"live-rgb": 1}
    assert rgb["count"] == 1
    assert rgb["rgb_sequence_count"] == 1
    assert rgb["rgb_sequence_frame_counts"] == [7]
    assert rgb["rgb_first_packet_retransmit_counts"] == [RGB_FIRST_PAYLOAD_REPEAT_COUNT]
    assert rgb["rgb_decode_statuses"] == {"decoded-literal": 1}
    assert rgb["rgb_static_colors"] == {"#ff0000": 1}
    assert rgb["rgb_decoded_lengths"] == [132 * 3]
    assert rgb["rgb_unique_color_counts"] == [1]
    assert list(rgb["rgb_decoded_hashes"].values()) == [1]


def test_tinyuz_decoder_handles_dictionary_backrefs():
    raw = bytes((0, 0, 255)) * 132
    compressed = _tinyuz_backref_static_rgb((0, 0, 255), 132)

    decoded, info = capture_module._decode_tinyuz_literal(compressed, len(raw))

    assert decoded == raw
    assert info["decode_status"] == "decoded-backref"
    assert info["stream_end_found"] is True
    assert info["literal_count"] == 3
    assert info["backref_count"] == 1
    assert info["backref_bytes"] == len(raw) - 3
    assert info["max_backref_distance"] == 3


def test_tinyuz_decoder_handles_literal_line_blocks():
    colors = [(index, index + 1, index + 2) for index in range(0, 24, 3)]
    raw = b"".join(bytes(color) for color in colors)
    compressed = _tinyuz_literal_line_rgb(colors)

    decoded, info = capture_module._decode_tinyuz_literal(compressed, len(raw))

    assert decoded == raw
    assert info["decode_status"] == "decoded-literal"
    assert info["stream_end_found"] is True
    assert info["literal_count"] == len(raw)
    assert info["literal_line_count"] == 1
    assert info["backref_count"] == 0


def test_capture_analyzer_decodes_backref_static_rgb_color():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    compressed = _tinyuz_backref_static_rgb((0, 0, 255), 132)
    payloads = _rgb_payloads_with_compressed(
        target,
        compressed,
        led_count=132,
        effect_index=2,
    )
    packets = [
        packet
        for payload in payloads
        for packet in build_rf_chunks(target.channel, target.rx_type, payload)
    ]

    analysis = analyze_capture_packets(packets, source="official-rgb-backref.txt")

    assert analysis["summary"]["rf_operations"] == {"live-rgb": 1}
    assert analysis["summary"]["rf_frame_operations"] == {"live-rgb": 2}
    assert analysis["summary"]["replay_hint_count"] == 1
    first = analysis["rf_frames"][0]
    assert first["rgb_decode_status"] == "decoded-backref"
    assert first["rgb_payload"]["decode_status"] == "decoded-backref"
    assert first["rgb_payload"]["static_color"] == [0, 0, 255]
    assert first["rgb_payload"]["static_color_hex"] == "#0000ff"
    assert first["rgb_payload"]["backref_count"] == 1
    assert first["rgb_payload"]["sequence_rf_frame_count"] == 2
    assert first["replay_hint"]["dry_run"]["argv"][-4:] == ["--color", "0,0,255", "--effect-index", "2"]

    report = capture_protocol_report_from_analysis(analysis)
    rgb = report["operations"]["live-rgb"]
    assert rgb["count"] == 1
    assert rgb["rgb_sequence_frame_counts"] == [2]
    assert rgb["rgb_decode_statuses"] == {"decoded-backref": 1}
    assert rgb["rgb_static_colors"] == {"#0000ff": 1}


def test_capture_analyzer_summarizes_literal_line_rgb_palette():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    colors = [(index, index + 1, index + 2) for index in range(0, 24, 3)]
    compressed = _tinyuz_literal_line_rgb(colors)
    payloads = _rgb_payloads_with_compressed(
        target,
        compressed,
        led_count=len(colors),
        effect_index=3,
    )
    packets = [
        packet
        for payload in payloads
        for packet in build_rf_chunks(target.channel, target.rx_type, payload)
    ]

    analysis = analyze_capture_packets(packets, source="official-rgb-literal-line.txt")

    assert analysis["summary"]["rf_operations"] == {"live-rgb": 1}
    assert analysis["summary"]["rf_frame_operations"] == {"live-rgb": 2}
    assert analysis["summary"]["replay_hint_count"] == 1
    first = analysis["rf_frames"][0]
    assert first["rgb_payload"]["decode_status"] == "decoded-literal"
    assert first["rgb_payload"]["literal_line_count"] == 1
    assert first["rgb_payload"]["unique_color_count"] == len(colors)
    assert first["rgb_payload"]["sample_colors_hex"] == [
        "#000102",
        "#030405",
        "#060708",
        "#090a0b",
        "#0c0d0e",
        "#0f1011",
        "#121314",
        "#151617",
    ]
    assert "static_color" not in first["rgb_payload"]
    assert "dry_run" not in first["replay_hint"]
    assert "compressed" in first["replay_hint"]["note"]

    report = capture_protocol_report_from_analysis(analysis)
    rgb = report["operations"]["live-rgb"]
    assert rgb["count"] == 1
    assert rgb["rgb_static_colors"] == {}
    assert rgb["rgb_unique_color_counts"] == [len(colors)]
    assert rgb["rgb_sequence_frame_counts"] == [2]


def test_capture_analyzer_classifies_requests_and_snapshots(tmp_path):
    master_response = bytes([RF_MASTER_QUERY_CMD]) + bytes.fromhex("102030405060") + bytes(57)
    path = tmp_path / "capture.json"
    path.write_text(
        "[\n"
        f'  "{build_wireless_list_request().hex()}",\n'
        f'  "{_snapshot_payload().hex()}",\n'
        f'  "{build_master_query_request(8).hex()}",\n'
        f'  "{master_response.hex()}"\n'
        "]\n",
        encoding="utf-8",
    )

    analysis = analyze_capture_packets(load_capture_packets(path), source=str(path))

    assert analysis["summary"]["kinds"] == {
        "master-query-request": 1,
        "master-query-response": 1,
        "receiver-list-request": 1,
        "receiver-snapshot": 1,
    }
    assert analysis["summary"]["snapshot_count"] == 1
    assert analysis["summary"]["master_response_count"] == 1
    assert analysis["summary"]["receiver_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert analysis["summary"]["master_macs"] == ["10:20:30:40:50:60"]
    snapshot = next(record for record in analysis["records"] if record["kind"] == "receiver-snapshot")
    assert snapshot["motherboard_pwm"] is None
    assert snapshot["motherboard_pwm_raw"] == {"indicator": 0, "value": 0, "valid": False}


def test_capture_analyzer_reports_motherboard_pwm_from_snapshot(tmp_path):
    path = tmp_path / "capture.json"
    path.write_text(
        json.dumps([_snapshot_payload_with_motherboard_pwm(10, 10).hex()]) + "\n",
        encoding="utf-8",
    )

    analysis = analyze_capture_packets(load_capture_packets(path), source=str(path))
    snapshot = analysis["records"][0]

    assert snapshot["kind"] == "receiver-snapshot"
    assert snapshot["motherboard_pwm"] == 127
    assert snapshot["motherboard_pwm_raw"] == {"indicator": 10, "value": 10, "valid": True}


def test_capture_analyzer_infers_pwm_mirror_from_prior_snapshot():
    snapshot = _snapshot_payload_with_motherboard_pwm(10, 10)
    target = parse_wireless_snapshot(snapshot)[0]
    packets = [snapshot] + build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [127]))

    analysis = analyze_capture_packets(packets, source="unit-test")

    assert analysis["summary"]["kinds"] == {"receiver-snapshot": 1, "rf-chunk": 4}
    assert analysis["summary"]["rf_operations"] == {"live-pwm-mirror": 1}
    frame = analysis["rf_frames"][0]
    assert frame["operation"] == "live-pwm-mirror"
    assert frame["original_operation"] == "live-pwm"
    assert frame["motherboard_pwm"] == 127
    assert frame["motherboard_pwm_snapshot_index"] == 0
    assert frame["inferred_from_snapshot"] is True
    assert frame["pwm_values"] == [127, 127, 127, 127]
    assert analysis["summary"]["replay_hint_count"] == 1
    hint = analysis["replay_hints"][0]
    assert hint["operation"] == "live-pwm-mirror"
    assert hint["dry_run"]["argv"][-2:] == ["--motherboard-pwm", "127"]
    assert hint["compare_capture"]["expected_operation"] == "pwm-mirror"
    assert hint["compare_capture"]["argv"][2:5] == ["compare-capture", "unit-test", "pwm-mirror"]
    assert "--device-type" in hint["compare_capture"]["argv"]
    assert frame["replay_hint"]["target"]["device_type"] == 2


def test_capture_analyzer_keeps_direct_pwm_when_snapshot_value_differs():
    snapshot = _snapshot_payload_with_motherboard_pwm(10, 10)
    target = parse_wireless_snapshot(snapshot)[0]
    packets = [snapshot] + build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [120]))

    analysis = analyze_capture_packets(packets, source="unit-test")

    assert analysis["summary"]["rf_operations"] == {"live-pwm": 1}
    assert analysis["rf_frames"][0]["operation"] == "live-pwm"


def test_capture_analyzer_replay_hint_preserves_non_uniform_pwm_tuple():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    packets = build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [80, 90, 100, 110]))

    analysis = analyze_capture_packets(packets, source="official.txt")

    hint = analysis["replay_hints"][0]
    assert hint["operation"] == "live-pwm"
    assert hint["dry_run"]["argv"][-2:] == ["--pwm-values", "80,90,100,110"]
    assert hint["compare_capture"]["argv"][2:5] == ["compare-capture", "official.txt", "pwm"]


def test_capture_replay_plan_summarizes_unique_commands():
    snapshot = _snapshot_payload_with_motherboard_pwm(10, 10)
    target = parse_wireless_snapshot(snapshot)[0]
    packets = [snapshot]
    packets.extend(build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [127])))
    packets.extend(build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [127])))
    analysis = analyze_capture_packets(packets, source="l-connect.txt")

    plan = capture_replay_plan_from_analysis(analysis)

    assert plan["operation"] == "capture-replay-plan"
    assert plan["source"] == "l-connect.txt"
    assert plan["replay_hint_count"] == 2
    assert plan["operation_counts"] == {"live-pwm-mirror": 2}
    assert len(plan["dry_run_commands"]) == 1
    assert "dry-run-pwm-mirror" in plan["dry_run_commands"][0]
    assert "--motherboard-pwm 127" in plan["dry_run_commands"][0]
    assert len(plan["compare_capture_commands"]) == 1
    assert "compare-capture l-connect.txt pwm-mirror" in plan["compare_capture_commands"][0]
    assert plan["items"][0]["dry_run"]["argv"][2] == "dry-run-pwm-mirror"


def test_windows_capture_plan_prefers_vm_and_targets_v2117(monkeypatch, tmp_path):
    installer = tmp_path / "L-Connect-v2.1.17.exe"
    installer.write_bytes(b"installer")

    def fake_which(name):
        return "/usr/bin/qemu-system-x86_64" if name == "qemu-system-x86_64" else None

    monkeypatch.setattr(capture_module.shutil, "which", fake_which)

    plan = windows_capture_plan(
        version="2.1.17",
        installer=installer,
        capture_base="lianli-v2117",
    )

    assert plan["operation"] == "windows-capture-plan"
    assert plan["recommended_environment"] == "vm-usb-passthrough"
    assert plan["local_tools"]["qemu"]["available"] is True
    assert plan["local_tools"]["wine"]["available"] is False
    assert plan["installer"]["sha256"] == "9c0d294c05fc1d88d698034609bb81c0c69196327594e4c69d2915c80fd9850c"
    assert any(target["vid_pid"] == "0416:8040" for target in plan["usb_targets"])
    scenario_ids = {scenario["id"] for scenario in plan["scenarios"]}
    assert {"rf-rebind", "sort-quick-sync", "motherboard-pwm-sync"} <= scenario_ids
    rebind = next(scenario for scenario in plan["scenarios"] if scenario["id"] == "rf-rebind")
    assert rebind["capture_file"] == "lianli-v2117-03-rf-rebind.pcapng"
    assert any("compare-capture" in command and "unbind" in command for command in rebind["linux_commands"])
    baseline = next(scenario for scenario in plan["scenarios"] if scenario["id"] == "baseline")
    assert any("capture-timeline-report lianli-v2117-00-baseline.pcapng" in command for command in baseline["linux_commands"])
    assert any(
        "capture-timeline-report lianli-v2117-00-baseline.pcapng" in command
        for command in plan["post_capture"]["preferred_linux_flow"]
    )
    wine = next(item for item in plan["environment_matrix"] if item["environment"] == "wine")
    assert "RF protocol proof" in wine["limits"]


def test_usb_capture_readiness_reports_sender_receiver_and_next_steps(monkeypatch, tmp_path):
    for name, vid, pid, product, busnum, devnum in (
        ("1-1", "0416", "8040", "SLV3TX_V1.6", "001", "002"),
        ("1-2", "0416", "8041", "SLV3RX_V1.6", "001", "003"),
    ):
        device_root = tmp_path / "bus" / "usb" / "devices" / name
        device_root.mkdir(parents=True)
        (device_root / "idVendor").write_text(vid, encoding="utf-8")
        (device_root / "idProduct").write_text(pid, encoding="utf-8")
        (device_root / "manufacturer").write_text("LIAN LI", encoding="utf-8")
        (device_root / "product").write_text(product, encoding="utf-8")
        (device_root / "serial").write_text(name, encoding="utf-8")
        (device_root / "busnum").write_text(busnum, encoding="utf-8")
        (device_root / "devnum").write_text(devnum, encoding="utf-8")

    def fake_which(name):
        return f"/usr/bin/{name}" if name in {"tshark", "usbipd"} else None

    monkeypatch.setattr(capture_module.shutil, "which", fake_which)

    readiness = usb_capture_readiness(sys_root=tmp_path)

    assert readiness["operation"] == "usb-capture-readiness"
    assert readiness["status"] == "linux-live-ready"
    assert readiness["known_device_count"] == 2
    assert readiness["present_vid_pids"] == ["0416:8040", "0416:8041"]
    assert readiness["missing_l_wireless_vid_pids"] == []
    assert readiness["tools"]["tshark"]["available"] is True
    sender = next(target for target in readiness["targets"] if target["vid_pid"] == "0416:8040")
    assert sender["present"] is True
    assert sender["capture_priority"] == "highest"
    assert sender["devices"][0]["usbmon_hint"] == "capture usb bus 001; device address 002"
    assert any("validate-readonly" in command for command in readiness["linux_live_commands"])
    assert any('ATTR{idProduct}=="8040"' in rule for rule in readiness["udev_rules"])


def test_usb_capture_readiness_explains_missing_hardware(monkeypatch, tmp_path):
    monkeypatch.setattr(capture_module.shutil, "which", lambda name: None)

    readiness = usb_capture_readiness(sys_root=tmp_path)

    assert readiness["status"] == "no-l-wireless-hardware"
    assert {"0416:8040", "0416:8041"} == set(readiness["missing_l_wireless_vid_pids"])
    assert any("No 0416:8040/0416:8041" in blocker for blocker in readiness["blockers"])
    assert any("tshark is missing" in blocker for blocker in readiness["blockers"])
    assert not any("validate-readonly" in command for command in readiness["linux_live_commands"])


def test_summarize_capture_dir_ranks_protocol_rich_captures(tmp_path):
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    baseline = tmp_path / "00-baseline.txt"
    baseline.write_text(_snapshot_payload().hex(), encoding="utf-8")
    pwm = tmp_path / "01-direct-pwm.txt"
    pwm.write_text(
        "\n".join(packet.hex() for packet in build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [120]))),
        encoding="utf-8",
    )
    noise = tmp_path / "notes.txt"
    noise.write_text("no usb payload here", encoding="utf-8")

    summary = summarize_capture_dir(tmp_path)

    assert summary["operation"] == "summarize-captures"
    assert summary["file_count"] == 3
    assert summary["error_count"] == 0
    assert summary["candidate_count"] == 2
    assert summary["summary"]["rf_operations"] == {"live-pwm": 1}
    assert summary["summary"]["receiver_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert summary["top_candidates"][0]["path"] == "01-direct-pwm.txt"
    assert summary["top_candidates"][0]["rf_operations"] == {"live-pwm": 1}
    assert any("capture-replay-plan" in command for command in summary["top_candidates"][0]["recommended_commands"])
    assert any("capture-timeline-report" in command for command in summary["top_candidates"][0]["recommended_commands"])
    note_item = next(item for item in summary["files"] if item["path"] == "notes.txt")
    assert note_item["packet_count"] == 0
    assert "No supported" in note_item["note"]


def test_capture_protocol_report_aggregates_devices_operations_and_parameters():
    snapshot = _snapshot_payload_with_motherboard_pwm(10, 10)
    target = parse_wireless_snapshot(snapshot)[0]
    packets = [snapshot]
    packets.extend(build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [127])))
    packets.extend(build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [80, 90, 100, 110])))
    analysis = analyze_capture_packets(packets, source="l-connect.txt")

    report = capture_protocol_report_from_analysis(analysis)

    assert report["operation"] == "capture-protocol-report"
    assert report["source"] == "l-connect.txt"
    assert report["packet_count"] == 9
    assert report["rf_frame_count"] == 2
    assert report["receiver_snapshot_count"] == 1
    assert report["motherboard_pwm_values"] == [127]
    assert report["summary"]["device_count"] == 1
    assert report["summary"]["operation_count"] == 2
    device = report["devices"]["aa:bb:cc:dd:ee:ff"]
    assert device["snapshot_count"] == 1
    assert device["rf_frame_count"] == 2
    assert device["channels"] == [8]
    assert device["rx_types"] == [3]
    assert device["device_types"] == [2]
    assert device["fan_counts"] == [3]
    assert device["operations"] == {"live-pwm": 1, "live-pwm-mirror": 1}
    assert device["pwm_values"]["127,127,127,127"] == 1
    assert device["pwm_values"]["80,90,100,110"] == 2
    mirror = report["operations"]["live-pwm-mirror"]
    assert mirror["count"] == 1
    assert mirror["motherboard_pwm_values"] == [127]
    assert mirror["motherboard_pwm_snapshot_indexes"] == [0]
    assert mirror["pwm_values"] == {"127,127,127,127": 1}


def test_capture_protocol_report_file_aggregates_usb_operation_metadata(tmp_path):
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    packets = [
        _snapshot_payload(),
        *LianLiWirelessBackend().build_pwm_packets(target, [77, 88, 99, 111]),
    ]
    capture_path = tmp_path / "l-connect-protocol-usb.json"
    _write_tshark_json_capture(
        capture_path,
        packets,
        product_ids=["0x8041", "0x8040", "0x8040", "0x8040", "0x8040"],
    )

    report = capture_protocol_report_file(capture_path)

    assert report["operation"] == "capture-protocol-report"
    device = report["devices"]["aa:bb:cc:dd:ee:ff"]
    assert device["usb_device_counts"] == {"0416:8040": 4}
    assert device["usb_endpoint_counts"] == {"1/7/0x01/OUT/URB_BULK": 4}
    assert device["usb_frame_numbers"] == ["2", "3", "4", "5"]
    assert device["usb_time_relative_start_s"] == 0.2
    assert device["usb_time_relative_end_s"] == 0.5
    assert device["usb_time_relative_span_s"] == 0.3
    operation = report["operations"]["live-pwm"]
    assert operation["usb_device_counts"] == {"0416:8040": 4}
    assert operation["usb_endpoint_counts"] == {"1/7/0x01/OUT/URB_BULK": 4}
    assert operation["usb_target_counts"] == {"0416:8040|1/7/0x01/OUT/URB_BULK": 4}
    assert operation["usb_frame_numbers"] == ["2", "3", "4", "5"]
    assert operation["usb_time_relative_start_s"] == 0.2
    assert operation["usb_time_relative_end_s"] == 0.5
    assert operation["usb_time_relative_span_s"] == 0.3
    assert operation["linux_live_write_targets"] == [
        {
            "vid_pid": "0416:8040",
            "role": "sender",
            "label": "L-Wireless RF sender / transmitter",
            "bus": "1",
            "device_address": "7",
            "endpoint_key": "1/7/0x01/OUT/URB_BULK",
            "write_endpoint": "0x01",
            "read_endpoint": "0x81",
            "transfer_type": "URB_BULK",
            "packet_count": 4,
            "confidence": "high",
            "correlation": "same-packet-device-endpoint",
            "linux_hint": "PyUsbEndpointTransport(vid=0x0416, pid=0x8040, write_endpoint=0x01, read_endpoint=0x81)",
        }
    ]
    assert report["linux_live_write_targets"][0]["operation"] == "live-pwm"
    assert report["summary"]["linux_live_write_target_count"] == 1


def test_capture_protocol_report_counts_snapshot_pwm_and_rpm_once():
    analysis = analyze_capture_packets([_snapshot_payload()], source="snapshot-only")

    report = capture_protocol_report_from_analysis(analysis)

    device = report["devices"]["aa:bb:cc:dd:ee:ff"]
    assert device["snapshot_count"] == 1
    assert device["rf_frame_count"] == 0
    assert device["pwm_values"] == {"80,90,100,110": 1}
    assert device["fan_rpm_values"] == {"1234,1500,0,0": 1}


def test_capture_timeline_report_orders_queries_frames_and_snapshot_changes(tmp_path):
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    packets = [
        build_wireless_list_request(),
        build_master_query_request(8),
        _snapshot_payload(),
        *LianLiWirelessBackend().build_pwm_packets(target, [77, 88, 99, 111]),
        _snapshot_payload_with_pwm_values((77, 88, 99, 111), 8),
    ]
    capture_path = tmp_path / "l-connect-timeline.json"
    _write_tshark_json_capture(
        capture_path,
        packets,
        product_ids=["0x8041", "0x8040", "0x8041", "0x8040", "0x8040", "0x8040", "0x8040", "0x8041"],
    )

    report = capture_timeline_report_file(capture_path)
    events = report["events"]

    assert report["operation"] == "capture-timeline-report"
    assert report["packet_count"] == 8
    assert report["event_count"] == 5
    assert report["skipped_rf_chunk_count"] == 4
    assert [event["event_type"] for event in events] == [
        "receiver-list-request",
        "master-query-request",
        "receiver-snapshot",
        "rf-frame",
        "receiver-snapshot",
    ]
    assert events[2]["device_changes"] == [{"mac": "aa:bb:cc:dd:ee:ff", "change": "first-seen"}]
    assert events[2]["usb"]["frame_number"] == "3"
    assert events[2]["time_relative_s"] == 0.3
    assert events[2]["delta_from_previous_s"] == 0.1
    assert events[2]["usb"]["vid_pid"] == "0416:8041"
    assert events[2]["usb"]["known_device"] == "L-Wireless RF receiver"
    assert events[3]["packet_index"] == 3
    assert events[3]["chunk_packet_indexes"] == [3, 4, 5, 6]
    assert [item["frame_number"] for item in events[3]["chunk_usb"]] == ["4", "5", "6", "7"]
    assert [item["frame_time_relative"] for item in events[3]["chunk_usb"]] == [
        "0.400000",
        "0.500000",
        "0.600000",
        "0.700000",
    ]
    assert events[3]["time_relative_s"] == 0.4
    assert events[3]["chunk_time_span_s"] == 0.3
    assert events[3]["usb"]["vid_pid"] == "0416:8040"
    assert events[3]["usb"]["known_device"] == "L-Wireless RF sender / transmitter"
    assert events[3]["operation"] == "live-pwm"
    assert events[3]["pwm_values"] == [77, 88, 99, 111]
    assert events[4]["device_changes"][0]["change"] == "updated"
    assert events[4]["device_changes"][0]["changed_fields"] == ["pwm_values", "command_sequence"]
    assert report["summary"]["event_types"] == {
        "master-query-request": 1,
        "receiver-list-request": 1,
        "receiver-snapshot": 2,
        "rf-frame": 1,
    }
    assert report["summary"]["timed_event_count"] == 5
    assert report["summary"]["time_span_s"] == 0.7
    assert report["summary"]["max_event_gap_s"] == 0.4
    assert report["summary"]["logical_rf_operations"] == {"live-pwm": 1}
    assert report["warnings"] == []


def test_capture_loader_accepts_wireshark_hexdump_text(tmp_path):
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    packets = build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [120]))
    lines: list[str] = []
    for packet_index, packet in enumerate(packets, start=1):
        lines.append(f"Frame {packet_index}: 64 bytes")
        for offset in range(0, len(packet), 16):
            chunk = packet[offset : offset + 16]
            lines.append(f"{offset:04x}   {' '.join(f'{byte:02x}' for byte in chunk)}")
        lines.append("")
    path = tmp_path / "wireshark-hexdump.txt"
    path.write_text("\n".join(lines), encoding="utf-8")

    analysis = analyze_capture_packets(load_capture_packets(path), source=str(path))

    assert analysis["packet_count"] == 4
    assert analysis["summary"]["rf_operations"] == {"live-pwm": 1}
    assert analysis["rf_frames"][0]["pwm_values"] == [120, 120, 120, 120]


def test_capture_loader_accepts_nested_tshark_json_fields(tmp_path):
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    packets = build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [6]))
    path = tmp_path / "tshark.json"
    path.write_text(
        "["
        f'{{"_source": {{"layers": {{"usb.capdata": "{packets[0].hex(":")}"}}}}}},'
        f'{{"_source": {{"layers": {{"usbhid.data": "00:{packets[1].hex(":")}"}}}}}},'
        f'{{"_source": {{"layers": {{"data": "{packets[2].hex()}"}}}}}},'
        f'{{"_source": {{"layers": {{"usb.capdata": {list(packets[3])}}}}}}}'
        "]",
        encoding="utf-8",
    )

    analysis = analyze_capture_packets(load_capture_packets(path), source=str(path))

    assert analysis["packet_count"] == 4
    assert analysis["summary"]["rf_operations"] == {"live-pwm-sync": 1}
    assert analysis["rf_frames"][0]["pwm_values"] == [6, 6, 6, 6]


def test_capture_transport_report_keeps_tshark_json_metadata(tmp_path):
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    packets = build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [6]))
    path = tmp_path / "tshark.json"
    path.write_text(
        json.dumps(
            [
                {
                    "_source": {
                        "layers": {
                            "frame": {"frame.number": "12"},
                            "usb": {
                                "usb.bus_id": "1",
                                "usb.device_address": "7",
                                "usb.endpoint_address": "0x01",
                                "usb.endpoint_number": "1",
                                "usb.endpoint_direction": "OUT",
                                "usb.transfer_type": "URB_BULK",
                                "usb.idVendor": "0x0416",
                                "usb.idProduct": "0x8040",
                            },
                            "usb.capdata": packets[0].hex(":"),
                        }
                    }
                },
                {
                    "_source": {
                        "layers": {
                            "frame": {"frame.number": "13"},
                            "usbhid.data": "00:" + packets[1].hex(":"),
                        }
                    }
                },
                {"_source": {"layers": {"data.data": packets[2].hex()}}},
                {"_source": {"layers": {"usb.capdata": list(packets[3])}}},
            ]
        ),
        encoding="utf-8",
    )

    report = capture_transport_report_file(path)

    assert report["operation"] == "capture-transport-report"
    assert report["packet_candidate_count"] == 4
    assert report["protocol_candidate_count"] == 4
    assert report["field_counts"] == {"data.data": 1, "usb.capdata": 2, "usbhid.data": 1}
    assert report["kind_counts"] == {"rf-chunk": 4}
    first = report["first_protocol_candidates"][0]
    assert first["frame_number"] == "12"
    assert first["usb_bus"] == "1"
    assert first["usb_endpoint_address"] == "0x01"
    assert first["usb_vendor_id"] == "0x0416"
    assert first["usb_product_id"] == "0x8040"
    assert first["kind"] == "rf-chunk"
    assert first["channel"] == 8
    assert report["usb_device_counts"] == {"0416:8040": 1}
    assert report["usb_endpoint_counts"] == {"1/7/0x01/OUT/URB_BULK": 1}
    assert report["known_usb_devices"][0]["role"] == "sender"
    assert report["lianli_usb_targets"]["sender_seen"] is True
    assert report["lianli_usb_targets"]["receiver_seen"] is False
    assert any("0416:8041" in note for note in report["notes"])
    assert any("analyze-capture" in command for command in report["recommended_commands"])
    assert any("capture-timeline-report" in command for command in report["recommended_commands"])


def test_capture_loader_reads_pcapng_with_tshark(monkeypatch, tmp_path):
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    packets = build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [120]))
    path = tmp_path / "capture.pcapng"
    path.write_bytes(b"\x0a\x0d\x0d\x0a" + bytes(32))
    seen = {}

    class FakeTsharkResult:
        returncode = 0
        stderr = ""
        stdout = (
            f"{packets[0].hex(':')}\t\t\n"
            f"\t00:{packets[1].hex(':')}\t\n"
            f"\t\t{packets[2].hex()}\n"
            f"{packets[3].hex()},{bytes(12).hex()}\t\t\n"
        )

    def fake_run(command, **kwargs):  # noqa: ANN001
        seen["command"] = command
        seen["timeout"] = kwargs["timeout"]
        return FakeTsharkResult()

    monkeypatch.setattr(capture_module.shutil, "which", lambda name: "/usr/bin/tshark" if name == "tshark" else None)
    monkeypatch.setattr(capture_module.subprocess, "run", fake_run)

    analysis = analyze_capture_packets(load_capture_packets(path), source=str(path))

    assert seen["command"][:5] == ["/usr/bin/tshark", "-r", str(path), "-T", "fields"]
    assert "usb.capdata" in seen["command"]
    assert "usbhid.data" in seen["command"]
    assert "data.data" in seen["command"]
    assert seen["timeout"] == 180
    assert analysis["packet_count"] == 4
    assert analysis["summary"]["rf_operations"] == {"live-pwm": 1}


def test_capture_transport_report_reads_pcapng_with_tshark_metadata(monkeypatch, tmp_path):
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    packets = build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [120]))
    path = tmp_path / "capture.pcapng"
    path.write_bytes(b"\x0a\x0d\x0d\x0a" + bytes(32))
    seen = {}

    class FakeTsharkResult:
        returncode = 0
        stderr = ""
        stdout = (
            f"42\t{packets[0].hex(':')}\t\t\t0.125000\t0.000000\t0.000000\t1700000000.125000\t1\t7\t0x01\t1\tOUT\tURB_BULK\t0x00\t0x0416\t0x8040\t1.7.1\thost\n"
            f"43\t\t00:{packets[1].hex(':')}\t\n"
            f"44\t\t\t{packets[2].hex()}\n"
        )

    def fake_run(command, **kwargs):  # noqa: ANN001
        seen["command"] = command
        return FakeTsharkResult()

    monkeypatch.setattr(capture_module.shutil, "which", lambda name: "/usr/bin/tshark" if name == "tshark" else None)
    monkeypatch.setattr(capture_module.subprocess, "run", fake_run)

    report = capture_transport_report_file(path)

    assert "-e" in seen["command"]
    assert "frame.number" in seen["command"]
    assert "frame.time_relative" in seen["command"]
    assert "usb.idVendor" in seen["command"]
    assert "usb.idProduct" in seen["command"]
    assert report["packet_candidate_count"] == 3
    assert report["field_counts"] == {"data.data": 1, "usb.capdata": 1, "usbhid.data": 1}
    assert report["first_protocol_candidates"][0]["frame_number"] == "42"
    assert report["first_protocol_candidates"][0]["frame_time_relative"] == "0.125000"
    assert report["first_protocol_candidates"][0]["usb_vendor_id"] == "0x0416"
    assert report["usb_device_counts"] == {"0416:8040": 1}


def test_protocol_signature_catalog_exports_searchable_operation_fingerprints():
    catalog = protocol_signature_catalog(led_count=12, rainbow_frames=3, interval_ms=40)
    items = {item["operation"]: item for item in catalog["items"]}

    assert catalog["operation"] == "protocol-signatures"
    assert catalog["parameters"] == {
        "led_count": 12,
        "rainbow_frames": 3,
        "interval_ms": 40,
        "effect_index": 1,
    }
    assert catalog["usb_targets"]["sender"]["vid_pid"] == "0416:8040"
    assert catalog["usb_targets"]["receiver"]["vid_pid"] == "0416:8041"
    assert {
        "receiver-list-request",
        "master-query-request",
        "pwm",
        "pwm-sync-enable",
        "pwm-sync-disable",
        "pwm-mirror",
        "bind",
        "unbind",
        "rgb-static-red",
        "rgb-off",
        "rainbow",
    } <= set(items)

    receiver_request = items["receiver-list-request"]
    assert receiver_request["target_usb"]["role"] == "receiver"
    assert receiver_request["summary"]["kinds"] == {"receiver-list-request": 1}
    assert receiver_request["rf_frame_count"] == 0

    pwm_sync = items["pwm-sync-enable"]
    assert pwm_sync["expected_operation"] == "pwm-sync"
    assert pwm_sync["summary"]["rf_operations"] == {"live-pwm-sync": 1}
    assert pwm_sync["rf_payload_prefixes"][0].startswith("1210aabbccddeeff10203040506003080806060606")
    assert "compare-capture '<capture>' pwm-sync" in pwm_sync["commands"]["compare_capture"]["command"]

    bind = items["bind"]
    assert bind["summary"]["rf_operations"] == {"live-bind": 1}
    assert bind["device_context"]["is_bound"] is False
    assert "--current-pwm" in bind["commands"]["compare_capture"]["argv"]

    rgb_off = items["rgb-off"]
    assert rgb_off["expected_operation"] == "rgb"
    assert rgb_off["summary"]["rf_operations"] == {"live-rgb": 1}
    assert rgb_off["rf_payload_sha256"]
    assert "--color" in rgb_off["commands"]["dry_run"]["argv"]

    rainbow = items["rainbow"]
    assert rainbow["expected_operation"] == "rainbow"
    assert rainbow["summary"]["rgb_sequence_count"] == 1
    assert "--frame-count" in rainbow["commands"]["compare_capture"]["argv"]
    assert catalog["summary"]["signature_count"] == len(items)
    assert any("compare-capture '<capture>' rainbow" in command for command in catalog["compare_capture_commands"])


def test_capture_signature_match_finds_catalog_operations_in_capture():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    packets = [
        build_wireless_list_request(),
        build_master_query_request(8),
        *LianLiWirelessBackend().build_motherboard_pwm_sync_packets(target),
    ]

    report = capture_signature_match_packets(
        packets,
        source="l-connect-sync.txt",
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )
    items = {item["operation"]: item for item in report["items"]}

    assert report["operation"] == "capture-signature-match"
    assert report["packet_count"] == 6
    assert report["normalized_packet_count"] == 6
    assert report["rf_frame_count"] == 1
    assert report["matched_operations"] == [
        "receiver-list-request",
        "master-query-request",
        "pwm-sync-enable",
    ]
    assert items["receiver-list-request"]["matched"] is True
    assert items["receiver-list-request"]["packet_sequence_match"]["start_indices"] == [0]
    assert items["master-query-request"]["matched"] is True
    assert items["pwm-sync-enable"]["matched"] is True
    assert items["pwm-sync-enable"]["semantic_match"] is True
    assert items["pwm-sync-enable"]["exact_match"] is True
    assert items["pwm-sync-enable"]["score"] == 100
    assert "l-connect-sync.txt" in items["pwm-sync-enable"]["commands"]["compare_capture"]["command"]
    assert items["pwm"]["matched"] is False
    assert items["rgb-off"]["matched"] is False
    assert any("capture-protocol-report l-connect-sync.txt" in command for command in report["recommended_commands"])
    assert any("capture-timeline-report l-connect-sync.txt" in command for command in report["recommended_commands"])


def test_capture_triage_report_summarizes_signature_replay_and_protocol(tmp_path):
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    packets = [
        build_wireless_list_request(),
        build_master_query_request(8),
        _snapshot_payload(),
        *LianLiWirelessBackend().build_pwm_packets(target, [77, 88, 99, 111]),
    ]
    capture_path = tmp_path / "l-connect-direct-pwm.json"
    capture_path.write_text(
        json.dumps(
            [
                {
                    "_source": {
                        "layers": {
                            "frame": {"frame.number": str(index + 1)},
                            "usb": {
                                "usb.bus_id": "1",
                                "usb.device_address": "8" if index == 0 else "7",
                                "usb.endpoint_address": "0x01",
                                "usb.endpoint_direction": "OUT",
                                "usb.transfer_type": "URB_BULK",
                                "usb.idVendor": "0x0416",
                                "usb.idProduct": "0x8041" if index == 0 else "0x8040",
                            },
                            "usb.capdata": packet.hex(":"),
                        }
                    }
                }
                for index, packet in enumerate(packets)
            ]
        ),
        encoding="utf-8",
    )

    report = capture_triage_report_file(capture_path, led_count=12, rainbow_frames=3, interval_ms=40)

    assert report["operation"] == "capture-triage-report"
    assert report["status"] == "protocol-signature-match"
    assert report["summary"]["packet_count"] == 7
    assert report["summary"]["rf_frame_count"] == 1
    assert report["summary"]["receiver_snapshot_count"] == 1
    assert report["summary"]["matched_operations"] == [
        "receiver-list-request",
        "master-query-request",
        "pwm",
    ]
    assert report["signature_match"]["matched_signature_count"] == 3
    assert [item["operation"] for item in report["signature_match"]["items"]] == [
        "receiver-list-request",
        "master-query-request",
        "pwm",
    ]
    assert any("--pwm-values 77,88,99,111" in command for command in report["signature_match"]["matched_commands"])
    assert any("compare-capture" in command and "pwm" in command for command in report["replay"]["compare_capture_commands"])
    assert report["protocol"]["operations"]["live-pwm"]["count"] == 1
    assert report["transport"]["protocol_candidate_count"] == 7
    assert report["transport"]["lianli_usb_targets"]["sender_seen"] is True
    assert report["transport"]["lianli_usb_targets"]["receiver_seen"] is True
    assert report["transport"]["usb_device_counts"] == {"0416:8040": 6, "0416:8041": 1}
    assert {device["role"] for device in report["transport"]["known_usb_devices"]} == {"sender", "receiver"}
    assert report["summary"]["linux_live_write_target_count"] == 1
    assert report["linux_live_write_targets"][0]["operation"] == "live-pwm"
    assert report["linux_live_write_targets"][0]["vid_pid"] == "0416:8040"
    assert report["linux_live_write_targets"][0]["write_endpoint"] == "0x01"
    assert report["linux_live_write_targets"][0]["target_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert any("validate-readonly" in command for command in report["recommended_commands"])
    assert any("safe-pwm-experiment --mac aa:bb:cc:dd:ee:ff" in command for command in report["recommended_commands"])
    assert "High-confidence Linux RF sender target" in report["next_steps"][0]
    assert any("capture-signature-match" in command for command in report["recommended_commands"])
    assert any("capture-timeline-report" in command for command in report["recommended_commands"])


def test_capture_set_report_audits_planned_windows_scenarios(tmp_path):
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    base = "lianli-v2117"
    baseline_packets = [
        build_wireless_list_request(),
        build_master_query_request(8),
        _snapshot_payload(),
    ]
    direct_packets = LianLiWirelessBackend().build_pwm_packets(target, [77, 88, 99, 111])
    _write_tshark_json_capture(
        tmp_path / f"{base}-00-baseline.json",
        baseline_packets,
        product_ids=["0x8041", "0x8040", "0x8041"],
    )
    _write_tshark_json_capture(
        tmp_path / f"{base}-01-direct-fan-speed.json",
        direct_packets,
        product_ids=["0x8040"] * len(direct_packets),
    )
    experiment_dir = tmp_path / "linux-experiments"
    _write_linux_experiment_summary_inputs(experiment_dir)

    report = capture_set_report(
        tmp_path,
        capture_base=base,
        experiment_dir=experiment_dir,
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )
    scenarios = {scenario["id"]: scenario for scenario in report["scenarios"]}

    assert report["operation"] == "capture-set-report"
    assert report["status"] == "partial-capture-set"
    assert report["scenario_count"] == 7
    assert report["found_capture_count"] == 2
    assert report["evidence_found_count"] == 2
    assert report["partial_evidence_count"] == 0
    assert report["status_counts"] == {"evidence-found": 2, "missing-capture": 5}
    assert report["sender_seen_count"] == 2
    assert report["receiver_seen_count"] == 1
    assert report["aggregate_rf_operations"] == {"live-pwm": 1}
    assert report["aggregate_matched_signatures"] == {
        "master-query-request": 1,
        "pwm": 1,
        "receiver-list-request": 1,
    }
    deltas = report["cross_scenario_deltas"]
    assert deltas["status"] == "ready"
    assert deltas["found_scenario_count"] == 2
    assert deltas["rf_operation_index"] == [
        {
            "operation": "live-pwm",
            "total_count": 1,
            "scenario_count": 1,
            "scenario_ids": ["direct-fan-speed"],
            "capture_paths": [f"{base}-01-direct-fan-speed.json"],
            "scenario_counts": {"direct-fan-speed": 1},
            "unique_to_scenario": "direct-fan-speed",
        }
    ]
    assert deltas["parameter_index"] == [
        {
            "operation": "live-pwm",
            "field": "pwm_values",
            "value": "77,88,99,111",
            "total_count": 1,
            "scenario_count": 1,
            "scenario_ids": ["direct-fan-speed"],
            "capture_paths": [f"{base}-01-direct-fan-speed.json"],
            "scenario_counts": {"direct-fan-speed": 1},
            "unique_to_scenario": "direct-fan-speed",
        }
    ]
    delta_by_id = {item["id"]: item for item in deltas["scenario_deltas"]}
    assert delta_by_id["direct-fan-speed"]["unique_rf_operations"] == ["live-pwm"]
    assert delta_by_id["direct-fan-speed"]["unique_parameter_evidence"] == [
        {
            "operation": "live-pwm",
            "field": "pwm_values",
            "value": "77,88,99,111",
            "count": 1,
        }
    ]
    assert delta_by_id["direct-fan-speed"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert delta_by_id["direct-fan-speed"]["unique_matched_signatures"] == ["pwm"]
    assert delta_by_id["direct-fan-speed"]["next_focus"] == (
        "compare unique RF operation(s): live-pwm with live-pwm.pwm_values=77,88,99,111"
    )
    assert set(delta_by_id["baseline"]["unique_matched_signatures"]) == {
        "receiver-list-request",
        "master-query-request",
    }
    assert report["linux_live_write_targets"][0]["operation"] == "live-pwm"
    assert report["linux_live_write_targets"][0]["vid_pid"] == "0416:8040"
    assert report["linux_live_write_targets"][0]["write_endpoint"] == "0x01"
    assert report["linux_live_write_targets"][0]["scenario_ids"] == ["direct-fan-speed"]
    assert report["linux_live_write_targets"][0]["target_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert report["linux_live_write_targets"][0]["channels"] == [8]
    assert report["linux_live_write_targets"][0]["rx_types"] == [3]
    assert report["linux_live_write_targets"][0]["master_macs"] == ["10:20:30:40:50:60"]
    runtime_context = report["linux_live_write_targets"][0]["runtime_contexts"][0]
    assert runtime_context["mac"] == "aa:bb:cc:dd:ee:ff"
    assert runtime_context["channel"] == 8
    assert runtime_context["rx_type"] == 3
    assert runtime_context["master_mac"] == "10:20:30:40:50:60"
    assert runtime_context["confidence"] == "high"
    assert runtime_context["source"] == "capture-protocol-report"
    assert runtime_context["snapshot_source"] == "receiver-snapshot"
    assert runtime_context["device_type"] == 2
    assert runtime_context["fan_count"] == 3
    assert runtime_context["pwm_values"] == [80, 90, 100, 110]
    assert runtime_context["fan_rpm"] == [1234, 1500, 0, 0]
    assert runtime_context["command_sequence"] == 7
    assert runtime_context["snapshot_scenario_id"] == "baseline"
    assert len(runtime_context["raw_hex"]) == 84
    assert report["experiment_dir"] == str(experiment_dir)
    assert report["hardware_validation"] == {
        "status": "readonly-and-write-observed",
        "validation_run_count": 1,
        "validation_error_count": 0,
        "safe_experiment_count": 1,
        "safe_effective_count": 1,
        "targets": ["aa:bb:cc:dd:ee:ff"],
    }
    assert report["experiment_summary"]["hardware_validation"]["status"] == "readonly-and-write-observed"
    assert report["linux_validation_plan"]["status"] == "linux-readonly-and-guarded-write-observed"
    assert report["linux_validation_plan"]["hardware_validation"] == report["hardware_validation"]
    matrix = {item["operation"]: item for item in report["linux_control_matrix"]}
    assert matrix["receiver-snapshot"]["overall_status"] == "linux-validated"
    assert matrix["live-pwm"]["windows_evidence_status"] == "evidence-found"
    assert matrix["live-pwm"]["linux_target_status"] == "high-confidence"
    assert matrix["live-pwm"]["experiment_status"] == "validated"
    assert matrix["live-pwm"]["overall_status"] == "linux-validated"
    assert any("safe-pwm-experiment --mac aa:bb:cc:dd:ee:ff" in command for command in matrix["live-pwm"]["recommended_commands"])
    assert any(f"summarize-experiments {experiment_dir}" in command for command in matrix["live-pwm"]["recommended_commands"])
    assert matrix["live-rgb"]["overall_status"] == "needs-windows-capture"
    contract = report["linux_interface_contract"]
    assert contract["status"] == "linux-control-partially-validated"
    assert contract["schema_version"] == "lianli-linux-interface-contract/v1"
    assert contract["protocol_delta_summary"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert contract["transport"]["sender"]["vid_pid"] == "0416:8040"
    assert contract["transport"]["sender"]["write_endpoint"] == "0x01"
    assert contract["transport"]["sender"]["confidence"] == "high"
    assert contract["transport"]["receiver"]["vid_pid"] == "0416:8041"
    contracts = {item["operation"]: item for item in contract["operation_contracts"]}
    assert contracts["receiver-snapshot"]["backend"]["read_method"] == "list_devices"
    assert contracts["live-pwm"]["backend"]["builder"] == "build_pwm_packets"
    assert contracts["live-pwm"]["backend"]["send_method"] == "send_pwm"
    assert contracts["live-pwm"]["transport"]["write_endpoint"] == "0x01"
    assert contracts["live-pwm"]["transport"]["channels"] == [8]
    assert contracts["live-pwm"]["transport"]["rx_types"] == [3]
    assert contracts["live-pwm"]["transport"]["runtime_contexts"][0]["channel"] == 8
    assert contracts["live-pwm"]["protocol_deltas"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert contracts["live-rgb"]["protocol_deltas"]["status"] == "none"
    assert contracts["live-pwm"]["required_runtime_fields"] == [
        "target.mac",
        "target.channel",
        "target.rx_type",
        "pwm_values",
    ]
    assert contracts["live-rgb"]["backend"]["safe_cli"] == "safe-rgb-experiment"
    assert contracts["live-rgb"]["status"] == "needs-windows-capture"
    standalone_contract = linux_interface_contract_report(
        tmp_path,
        capture_base=base,
        experiment_dir=experiment_dir,
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )
    assert standalone_contract["operation"] == "linux-interface-contract"
    assert standalone_contract["schema_version"] == "lianli-linux-interface-contract/v1"
    assert standalone_contract["source_capture_set_status"] == "partial-capture-set"
    assert standalone_contract["status"] == "linux-control-partially-validated"
    assert standalone_contract["source"]["linux_live_write_target_count"] == 1
    assert standalone_contract["protocol_delta_summary"]["next_focus"] == [
        "compare unique RF operation(s): live-pwm with live-pwm.pwm_values=77,88,99,111",
    ]
    assert standalone_contract["hardware_validation"] == report["hardware_validation"]
    summary = {item["operation"]: item for item in standalone_contract["control_matrix_summary"]}
    assert summary["live-pwm"]["overall_status"] == "linux-validated"
    assert any(
        "safe-pwm-experiment --mac aa:bb:cc:dd:ee:ff" in command
        for command in standalone_contract["recommended_commands"]
    )
    manifest = linux_control_manifest_report(
        tmp_path,
        capture_base=base,
        experiment_dir=experiment_dir,
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )
    assert manifest["operation"] == "linux-control-manifest"
    assert manifest["schema_version"] == "lianli-linux-control-manifest/v1"
    assert manifest["contract_schema_version"] == "lianli-linux-interface-contract/v1"
    assert manifest["status"] == "linux-control-partially-validated"
    assert manifest["target_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert manifest["protocol_delta_summary"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert manifest["linux_permissions"]["default_write_access"] is False
    assert "SUBSYSTEM" in manifest["linux_permissions"]["udev_rules"][0]
    assert manifest["safety_gates"]["writes_disabled_by_default"] is True
    operations = {item["operation"]: item for item in manifest["operations"]}
    assert operations["receiver-snapshot"]["enabled_by_default"] is True
    assert operations["live-pwm"]["capability"] == "fan-speed"
    assert operations["live-pwm"]["write_enabled_by_default"] is False
    assert operations["live-pwm"]["runtime_context"]["status"] == "complete"
    assert operations["live-pwm"]["runtime_context"]["contexts"][0]["rx_type"] == 3
    assert operations["live-pwm"]["observed_parameters"]["default_pwm_values"] == [77, 88, 99, 111]
    assert operations["live-pwm"]["protocol_deltas"]["next_focus"] == [
        "compare unique RF operation(s): live-pwm with live-pwm.pwm_values=77,88,99,111"
    ]
    assert operations["live-pwm"]["safety"]["confirmation_token"] == "WRITE-LIANLI"
    assert operations["live-rainbow"]["missing_scenarios"][0]["id"] == "lighting-generated-rainbow"
    assert operations["live-rainbow"]["missing_scenarios"][0]["capture_file"] == f"{base}-06-lighting-generated-rainbow.pcapng"
    assert operations["live-pwm"]["evidence"] == {
        "windows": "evidence-found",
        "linux_target": "high-confidence",
        "experiment": "validated",
    }
    pwm_fields = {item["name"]: item for item in operations["live-pwm"]["input_schema"]}
    assert pwm_fields["target.mac"]["source"] == "receiver-snapshot"
    assert pwm_fields["pwm_values"]["kind"] == "pwm-tuple"
    assert operations["live-rgb"]["safety"]["visual_confirmation_required"] is True
    assert operations["live-bind"]["safety"]["pairing_recovery_required"] is True

    gap_report = capture_gap_report(
        tmp_path,
        capture_base=base,
        experiment_dir=experiment_dir,
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )

    assert gap_report["operation"] == "capture-gap-report"
    assert gap_report["status"] == "needs-windows-capture"
    assert gap_report["source_capture_set_status"] == "partial-capture-set"
    assert gap_report["missing_capture_count"] == 5
    assert gap_report["next_capture"]["id"] == "motherboard-pwm-sync"
    assert gap_report["next_capture"]["priority"] == 20
    assert gap_report["next_capture"]["risk"] == "guarded fan-speed write"
    assert gap_report["scenario_gaps"][-1]["id"] == "rf-rebind"
    assert gap_report["scenario_gaps"][-1]["priority"] == 90
    gate_statuses = {item["name"]: item["status"] for item in gap_report["proof_gates"]}
    assert gate_statuses["baseline-before-writes"] == "ok"
    assert gate_statuses["pwm-before-lighting"] == "ok"
    assert gate_statuses["lighting-before-pairing"] == "blocked"
    assert any(
        command == f"capture next scenario: {base}-02-mb-pwm-sync.pcapng"
        for command in gap_report["recommended_commands"]
    )
    operation_gaps = {item["operation"]: item for item in gap_report["operation_gaps"]}
    assert "live-pwm" not in operation_gaps
    assert operation_gaps["live-rgb"]["missing_scenarios"][0]["id"] == "lighting-static-and-off"
    sys_root = tmp_path / "sys"
    dev_root = tmp_path / "dev"
    _write_lianli_usb_sysfs_and_dev(sys_root, dev_root)
    preflight = linux_control_preflight_report(
        tmp_path,
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        experiment_dir=experiment_dir,
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )
    assert preflight["operation"] == "linux-control-preflight"
    assert preflight["schema_version"] == "lianli-linux-control-preflight/v1"
    assert preflight["manifest_schema_version"] == "lianli-linux-control-manifest/v1"
    assert preflight["status"] == "ready-for-safe-experiments"
    assert preflight["hardware_status"] == "linux-live-ready"
    assert preflight["permission_status"] == "read-write-ok"
    assert preflight["protocol_delta_summary"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert "live-pwm" in preflight["ready_operations"]
    preflight_operations = {item["operation"]: item for item in preflight["operations"]}
    assert preflight_operations["receiver-snapshot"]["required_vid_pid"] == "0416:8041"
    assert preflight_operations["receiver-snapshot"]["can_run_now"] is True
    assert preflight_operations["live-pwm"]["preflight_status"] == "ready"
    assert preflight_operations["live-pwm"]["required_vid_pid"] == "0416:8040"
    assert preflight_operations["live-pwm"]["device_access"]["read_write_count"] == 1
    assert preflight_operations["live-pwm"]["runtime_context"]["status"] == "complete"
    assert preflight_operations["live-pwm"]["observed_parameters"]["default_pwm_values"] == [77, 88, 99, 111]
    assert preflight_operations["live-pwm"]["protocol_deltas"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert preflight_operations["live-pwm"]["source_capture_paths"] == [
        str(tmp_path / f"{base}-01-direct-fan-speed.json")
    ]
    assert preflight_operations["live-rgb"]["preflight_status"] == "needs-capture-evidence"
    assert preflight_operations["live-rainbow"]["missing_scenarios"][0]["id"] == "lighting-generated-rainbow"
    assert preflight["device_access"]["summary"]["0416:8040"]["read_write_count"] == 1
    action_plan = linux_control_action_plan_report(
        tmp_path,
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        experiment_dir=experiment_dir,
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )
    assert action_plan["operation"] == "linux-control-action-plan"
    assert action_plan["schema_version"] == "lianli-linux-control-action-plan/v1"
    assert action_plan["preflight_schema_version"] == "lianli-linux-control-preflight/v1"
    assert action_plan["status"] == "ready-for-safe-experiments"
    assert action_plan["protocol_delta_summary"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert action_plan["ready_action_count"] >= 2
    assert action_plan["guarded_write_readiness"]["status"] == "needs-pre-write-validation"
    assert action_plan["guarded_write_readiness"]["guarded_write_ready_count"] == 0
    assert action_plan["guarded_write_readiness"]["needs_pre_write_validation_action_ids"] == [
        "safe-experiment:live-pwm"
    ]
    assert any("linux-control-packet-preview" in command for command in action_plan["next_commands"])
    actions = {item["id"]: item for item in action_plan["actions"]}
    assert actions["validate-readonly:receiver-snapshot"]["status"] == "ready"
    assert "validate-readonly" in actions["validate-readonly:receiver-snapshot"]["command"]
    assert actions["safe-experiment:live-pwm"]["status"] == "ready"
    assert actions["safe-experiment:live-pwm"]["requires_confirmation"] is True
    assert actions["safe-experiment:live-pwm"]["confirmation_token"] == "WRITE-LIANLI"
    assert actions["safe-experiment:live-pwm"]["runtime_context"]["contexts"][0]["channel"] == 8
    assert actions["safe-experiment:live-pwm"]["observed_parameters"]["default_pwm_values"] == [77, 88, 99, 111]
    assert actions["safe-experiment:live-pwm"]["protocol_deltas"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert actions["safe-experiment:live-pwm"]["source_capture_paths"] == [
        str(tmp_path / f"{base}-01-direct-fan-speed.json")
    ]
    pre_write = actions["safe-experiment:live-pwm"]["pre_write_validation_commands"]
    assert len(pre_write) == 2
    assert "linux-control-packet-preview" in pre_write[0]
    assert "linux-control-packet-compare" in pre_write[1]
    assert str(tmp_path / f"{base}-01-direct-fan-speed.json") in pre_write[1]
    assert "--target-id aa:bb:cc:dd:ee:ff@ch8/rx3" in pre_write[0]
    pre_write_gate = actions["safe-experiment:live-pwm"]["pre_write_validation"]
    assert pre_write_gate["required"] is True
    assert pre_write_gate["validation_status"] == "needs-run"
    assert pre_write_gate["allows_guarded_write"] is False
    assert pre_write_gate["minimum_required_match"] == "exact-match"
    assert pre_write_gate["required_write_gate_status"] == "pass"
    assert pre_write_gate["required_allows_guarded_write"] is True
    assert pre_write_gate["semantic_only_action"] == "refresh-live-snapshot-and-recompare"
    assert pre_write_gate["compare_commands"] == [pre_write[1]]
    assert pre_write_gate["expected_compare_results"][0]["required_exact_match"] is True
    assert pre_write_gate["expected_compare_results"][0]["required_write_gate_status"] == "pass"
    execution = actions["safe-experiment:live-pwm"]["execution"]
    assert execution["status"] == "pre-write-validation-required"
    assert execution["write_command_enabled"] is False
    assert execution["blocked_by_pre_write_validation"] is True
    assert execution["next_command"] == pre_write[0]
    assert execution["write_command"] == actions["safe-experiment:live-pwm"]["command"]
    write_gate = linux_control_write_gate_report(
        tmp_path,
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        experiment_dir=experiment_dir,
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )
    assert write_gate["operation"] == "linux-control-write-gate"
    assert write_gate["schema_version"] == "lianli-linux-control-write-gate/v1"
    assert write_gate["action_plan_schema_version"] == "lianli-linux-control-action-plan/v1"
    assert write_gate["status"] == "needs-packet-compare"
    assert write_gate["allows_any_guarded_write"] is False
    assert write_gate["write_confirmation_token"] == "WRITE-LIANLI"
    assert write_gate["next_command"] == pre_write[0]
    assert write_gate["blocked_action_ids"] == ["safe-experiment:live-pwm"]
    assert write_gate["actions"][0]["id"] == "safe-experiment:live-pwm"
    assert write_gate["actions"][0]["ready_for_guarded_write"] is False
    assert write_gate["actions"][0]["validation_status"] == "needs-run"
    assert write_gate["actions"][0]["blocker"] == "run packet preview/compare before WRITE-LIANLI"
    assert write_gate["actions"][0]["target_state"]["status"] == "missing"
    assert "safe-pwm-experiment" in actions["safe-experiment:live-pwm"]["command"]
    assert actions["capture-evidence:live-rgb"]["status"] == "needs-evidence"
    assert actions["capture-evidence:live-rainbow"]["status"] == "needs-evidence"
    assert actions["capture-evidence:live-rainbow"]["missing_scenarios"][0]["capture_file"] == (
        f"{base}-06-lighting-generated-rainbow.pcapng"
    )
    assert "Select a generated rainbow" in actions["capture-evidence:live-rainbow"]["windows_capture_actions"][0]["windows_actions"][1]
    assert any(
        f"compare-capture {base}-06-lighting-generated-rainbow.pcapng rainbow" in command
        for command in actions["capture-evidence:live-rainbow"]["post_capture_commands"]
    )
    assert any(
        f"capture missing scenario: {base}-06-lighting-generated-rainbow.pcapng" == command
        for command in actions["capture-evidence:live-rainbow"]["commands"]
    )
    assert any("linux-control-packet-compare" in command for command in action_plan["commands"])
    assert any("safe-pwm-experiment" in command for command in action_plan["commands"])
    assert any(f"capture missing scenario: {base}-06-lighting-generated-rainbow.pcapng" == command for command in action_plan["commands"])
    registry = linux_control_target_registry_report(
        tmp_path,
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        experiment_dir=experiment_dir,
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )
    assert registry["operation"] == "linux-control-target-registry"
    assert registry["schema_version"] == "lianli-linux-control-target-registry/v1"
    assert registry["action_plan_schema_version"] == "lianli-linux-control-action-plan/v1"
    assert registry["status"] == "packet-build-ready"
    assert registry["target_count"] == 1
    assert registry["packet_build_ready_count"] == 1
    target_entry = registry["targets"][0]
    assert target_entry["id"] == "aa:bb:cc:dd:ee:ff@ch8/rx3"
    assert target_entry["packet_build_ready"] is True
    assert target_entry["requires_live_snapshot_before_write"] is True
    assert target_entry["wireless_device_info_template"]["kwargs"]["channel"] == 8
    assert target_entry["wireless_device_info_template"]["kwargs"]["rx_type"] == 3
    assert target_entry["wireless_device_info_template"]["kwargs"]["device_type"] == 2
    assert target_entry["wireless_device_info_template"]["kwargs"]["fan_count"] == 3
    assert target_entry["wireless_device_info_template"]["kwargs"]["fan_rpm"] == [1234, 1500, 0, 0]
    assert target_entry["wireless_device_info_template"]["kwargs"]["command_sequence"] == 7
    assert len(target_entry["wireless_device_info_template"]["kwargs"]["raw_hex"]) == 84
    assert target_entry["snapshot_device_state"]["snapshot_scenario_id"] == "baseline"
    assert target_entry["observed_parameters"]["live-pwm"]["default_pwm_values"] == [77, 88, 99, 111]
    assert target_entry["ready_operations"] == ["live-pwm"]
    assert target_entry["needs_evidence_operations"] == []
    assert target_entry["missing_packet_fields"] == []
    assert "safe-pwm-experiment" in target_entry["commands"][0]
    preview = linux_control_packet_preview_report(
        tmp_path,
        control_operation="live-pwm",
        target_id=target_entry["id"],
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        experiment_dir=experiment_dir,
        led_count=12,
        frame_count=3,
        interval_ms=40,
    )
    assert preview["operation"] == "linux-control-packet-preview"
    assert preview["schema_version"] == "lianli-linux-control-packet-preview/v1"
    assert preview["status"] == "packet-preview-ready"
    assert preview["target_id"] == "aa:bb:cc:dd:ee:ff@ch8/rx3"
    assert preview["target"]["missing_packet_fields"] == []
    assert preview["target_state"]["status"] == "capture-backed-target-state"
    assert preview["target_state"]["missing_packet_fields"] == []
    assert preview["target_state"]["placeholder_fields"] == []
    assert preview["target_state"]["snapshot_metadata_available"] is True
    assert preview["target_state"]["snapshot_state_available"] is True
    assert preview["target_state"]["raw_hex_available"] is True
    assert preview["target_state"]["live_snapshot_refresh_required"] is True
    assert preview["parameters"]["pwm_values"] == [77, 88, 99, 111]
    assert preview["parameters"]["pwm_values_source"] == "capture-evidence"
    assert preview["packet_preview"]["packet_count"] == 4
    assert preview["packet_preview"]["rf_operations"] == {"live-pwm": 1}
    assert preview["packet_preview"]["first_packet_hex"].startswith(
        "100008031210aabbccddeeff1020304050600308084d58636f"
    )
    assert len(preview["packet_preview"]["packets"]) == 4
    assert preview["packet_preview"]["packets"][0]["kind"] == "rf-chunk"
    assert preview["packet_preview"]["packets"][0]["rf_chunk"] == {
        "sequence": 0,
        "channel": 8,
        "rx_type": 3,
        "data_prefix_hex": "1210aabbccddeeff1020304050600308",
    }
    assert len(preview["packet_preview"]["packets"][0]["sha256"]) == 64
    assert preview["packet_preview"]["packets"][0]["rf_frames"][0]["operation"] == "live-pwm"
    assert preview["packet_preview"]["packets"][0]["rf_frames"][0]["pwm_values"] == [77, 88, 99, 111]
    assert (
        preview["packet_preview"]["rf_frames"][0]["payload_sha256"]
        == preview["packet_preview"]["packets"][0]["rf_frames"][0]["payload_sha256"]
    )
    comparison = linux_control_packet_compare_report(
        tmp_path,
        tmp_path / f"{base}-01-direct-fan-speed.json",
        control_operation="live-pwm",
        target_id=target_entry["id"],
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        experiment_dir=experiment_dir,
        led_count=12,
        frame_count=3,
        interval_ms=40,
    )
    assert comparison["operation"] == "linux-control-packet-compare"
    assert comparison["schema_version"] == "lianli-linux-control-packet-compare/v1"
    assert comparison["status"] == "matched"
    assert comparison["matched"] is True
    assert comparison["exact_match"] is True
    assert comparison["semantic_match"] is True
    assert comparison["target_state"]["status"] == "capture-backed-target-state"
    assert comparison["target_state"]["placeholder_fields"] == []
    assert comparison["parameters"]["pwm_values_source"] == "capture-evidence"
    assert comparison["expected_packet_count"] == 4
    assert comparison["comparison"]["expected_source"] == "linux-control-packet-preview:live-pwm"
    assert comparison["match_diagnostics"]["status"] == "exact-match"
    assert comparison["write_gate"]["status"] == "pass"
    assert comparison["write_gate"]["allows_guarded_write"] is True
    assert comparison["write_gate"]["minimum_required_match"] == "exact-match"
    compare_log = experiment_dir / "linux-control-packet-compare-live-pwm.json"
    compare_log.write_text(
        json.dumps(
            {
                **comparison,
                "observed_capture": f"{base}-01-direct-fan-speed.json",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    validated_plan = linux_control_action_plan_report(
        tmp_path,
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        experiment_dir=experiment_dir,
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )
    validated_actions = {item["id"]: item for item in validated_plan["actions"]}
    validated_pre_write = validated_actions["safe-experiment:live-pwm"]["pre_write_validation"]
    assert validated_plan["packet_compare_validation"]["pass_count"] == 1
    assert validated_plan["packet_compare_validation"]["valid_schema_count"] == 1
    assert validated_plan["packet_compare_validation"]["invalid_schema_count"] == 0
    assert validated_plan["packet_compare_validation"]["target_state_status_counts"] == {
        "capture-backed-target-state": 1
    }
    assert validated_plan["packet_compare_validation"]["target_state_placeholder_field_counts"] == {}
    assert validated_plan["packet_compare_validation"]["target_state_snapshot_state_available_count"] == 1
    assert validated_plan["packet_compare_validation"]["target_state_raw_hex_available_count"] == 1
    assert validated_plan["packet_compare_validation"]["target_state_live_snapshot_refresh_required_count"] == 1
    assert validated_plan["guarded_write_readiness"]["status"] == "guarded-write-ready"
    assert validated_plan["guarded_write_readiness"]["ready_action_ids"] == ["safe-experiment:live-pwm"]
    assert any("safe-pwm-experiment" in command for command in validated_plan["next_commands"])
    assert validated_pre_write["validation_status"] == "passed"
    assert validated_pre_write["allows_guarded_write"] is True
    assert validated_actions["safe-experiment:live-pwm"]["execution"]["status"] == "write-enabled"
    assert validated_actions["safe-experiment:live-pwm"]["execution"]["write_command_enabled"] is True
    assert validated_pre_write["observed_results"][0]["write_gate_status"] == "pass"
    assert validated_pre_write["observed_results"][0]["target_state_status"] == "capture-backed-target-state"
    assert validated_pre_write["observed_results"][0]["target_state_placeholder_fields"] == []
    assert validated_pre_write["observed_results"][0]["target_state_snapshot_state_available"] is True
    assert validated_pre_write["observed_results"][0]["observed_capture_match"] == "path-suffix"
    assert validated_pre_write["observed_results"][0]["source_capture_path"] == str(
        tmp_path / f"{base}-01-direct-fan-speed.json"
    )
    validated_write_gate = linux_control_write_gate_report(
        tmp_path,
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        experiment_dir=experiment_dir,
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )
    assert validated_write_gate["status"] == "write-enabled"
    assert validated_write_gate["allows_any_guarded_write"] is True
    assert validated_write_gate["ready_action_ids"] == ["safe-experiment:live-pwm"]
    assert "safe-pwm-experiment" in validated_write_gate["next_command"]
    assert validated_write_gate["actions"][0]["ready_for_guarded_write"] is True
    assert validated_write_gate["actions"][0]["write_command_enabled"] is True
    assert validated_write_gate["actions"][0]["passed_compare_count"] == 1
    assert validated_write_gate["actions"][0]["target_state"]["status"] == "capture-backed-target-state"
    assert validated_write_gate["actions"][0]["target_state"]["placeholder_fields"] == []
    assert validated_write_gate["actions"][0]["target_state"]["raw_hex_available"] is True

    refresh_dir = tmp_path / "refresh-experiments"
    _write_linux_experiment_summary_inputs(refresh_dir)
    refresh_compare = {
        **comparison,
        "exact_match": False,
        "match_diagnostics": {"status": "semantic-match-exact-mismatch"},
        "write_gate": {
            "status": "refresh-live-snapshot",
            "allows_guarded_write": False,
        },
    }
    (refresh_dir / "linux-control-packet-compare-live-pwm.json").write_text(
        json.dumps(refresh_compare) + "\n",
        encoding="utf-8",
    )
    refresh_plan = linux_control_action_plan_report(
        tmp_path,
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        experiment_dir=refresh_dir,
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )
    refresh_actions = {item["id"]: item for item in refresh_plan["actions"]}
    refresh_execution = refresh_actions["safe-experiment:live-pwm"]["execution"]
    assert refresh_plan["guarded_write_readiness"]["status"] == "refresh-live-snapshot"
    assert refresh_execution["status"] == "refresh-live-snapshot"
    assert refresh_execution["write_command_enabled"] is False
    assert "--save-json" in refresh_execution["next_command"]
    assert str(refresh_dir / "live-list-refresh.json") in refresh_execution["next_command"]
    assert refresh_execution["next_command"].endswith(" live-list")
    assert refresh_actions["safe-experiment:live-pwm"]["pre_write_validation"]["live_snapshot_refresh"]["save_path"] == str(
        refresh_dir / "live-list-refresh.json"
    )
    assert refresh_execution["required_before_write"][0].endswith(" live-list")

    refreshed_snapshot = _snapshot_payload_with_pwm_values((101, 102, 103, 104), sequence=40)
    (refresh_dir / "live-list-refresh.json").write_text(
        json.dumps(_live_list_payload_from_snapshot(refreshed_snapshot)) + "\n",
        encoding="utf-8",
    )
    recompare_plan = linux_control_action_plan_report(
        tmp_path,
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        experiment_dir=refresh_dir,
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )
    recompare_actions = {item["id"]: item for item in recompare_plan["actions"]}
    recompare_pre_write = recompare_actions["safe-experiment:live-pwm"]["pre_write_validation"]
    recompare_execution = recompare_actions["safe-experiment:live-pwm"]["execution"]
    assert recompare_plan["live_snapshot_context"]["device_count"] == 1
    assert recompare_pre_write["validation_status"] == "needs-recompare-after-refresh"
    assert recompare_pre_write["live_snapshot_refresh"]["command_sequence"] == 40
    assert recompare_execution["status"] == "needs-recompare-after-refresh"
    assert "linux-control-packet-preview" in recompare_execution["next_command"]
    refreshed_registry = linux_control_target_registry_report(
        tmp_path,
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        experiment_dir=refresh_dir,
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )
    refreshed_target = refreshed_registry["target_map"][target_entry["id"]]
    assert refreshed_target["wireless_device_info_template"]["kwargs"]["command_sequence"] == 40
    assert refreshed_target["wireless_device_info_template"]["kwargs"]["pwm_values"] == [101, 102, 103, 104]
    assert refreshed_target["snapshot_device_state"]["snapshot_source"] == "live-list"
    assert refreshed_target["snapshot_device_state"]["snapshot_path"].endswith("live-list-refresh.json")
    refreshed_preview = linux_control_packet_preview_report(
        tmp_path,
        control_operation="live-pwm",
        target_id=target_entry["id"],
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        experiment_dir=refresh_dir,
        led_count=12,
        frame_count=3,
        interval_ms=40,
    )
    assert refreshed_preview["parameters"]["pwm_values"] == [77, 88, 99, 111]
    assert "0308294d58636f" in refreshed_preview["packet_preview"]["first_packet_hex"]

    mismatch_capture = tmp_path.parent / f"{tmp_path.name}-direct-fan-speed-mismatch.json"
    mismatch_packets = LianLiWirelessBackend().build_pwm_packets(target, [78, 88, 99, 111])
    _write_tshark_json_capture(
        mismatch_capture,
        mismatch_packets,
        product_ids=["0x8040"] * len(mismatch_packets),
    )
    mismatch = linux_control_packet_compare_report(
        tmp_path,
        mismatch_capture,
        control_operation="live-pwm",
        target_id=target_entry["id"],
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        experiment_dir=experiment_dir,
        led_count=12,
        frame_count=3,
        interval_ms=40,
    )
    assert mismatch["status"] == "mismatch"
    assert mismatch["match_diagnostics"]["status"] == "semantic-mismatch"
    assert mismatch["match_diagnostics"]["primary"] == "semantic"
    assert mismatch["match_diagnostics"]["semantic"]["missing_count"] == 1
    assert mismatch["match_diagnostics"]["semantic"]["unmatched_observed_count"] == 1
    assert mismatch["write_gate"]["status"] == "fail"
    assert mismatch["write_gate"]["allows_guarded_write"] is False
    assert mismatch["write_gate"]["comparison_status"] == "semantic-mismatch"
    assert {
        "field": "pwm_values",
        "expected": [77, 88, 99, 111],
        "observed": [78, 88, 99, 111],
    } in mismatch["match_diagnostics"]["nearest_differences"][0]["differing_fields"]
    assert report["linux_validation_plan"]["high_confidence_target_count"] == 1
    assert any("usb-capture-readiness" in command for command in report["linux_validation_plan"]["commands"])
    assert any("validate-readonly" in command for command in report["linux_validation_plan"]["commands"])
    assert any(
        "safe-pwm-experiment --mac aa:bb:cc:dd:ee:ff" in command
        for command in report["linux_validation_plan"]["commands"]
    )
    assert any(f"summarize-experiments {experiment_dir}" in command for command in report["linux_validation_plan"]["commands"])
    assert {item["id"] for item in report["linux_validation_plan"]["missing_scenarios"]} == {
        "motherboard-pwm-sync",
        "rf-rebind",
        "sort-quick-sync",
        "lighting-static-and-off",
        "lighting-generated-rainbow",
    }
    assert scenarios["baseline"]["status"] == "evidence-found"
    assert scenarios["baseline"]["matched_evidence"] == [
        "receiver-list-request",
        "receiver-snapshot",
        "master-query",
    ]
    assert scenarios["baseline"]["usb"]["lianli_usb_targets"]["receiver_seen"] is True
    assert scenarios["direct-fan-speed"]["status"] == "evidence-found"
    assert scenarios["direct-fan-speed"]["matched_evidence"] == [
        "direct PWM RF frame",
        "non-sync PWM tuple",
    ]
    assert scenarios["direct-fan-speed"]["summary"]["linux_live_write_target_count"] == 1
    assert scenarios["direct-fan-speed"]["linux_live_write_targets"][0]["confidence"] == "high"
    assert scenarios["direct-fan-speed"]["linux_live_write_targets"][0]["target_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert scenarios["motherboard-pwm-sync"]["status"] == "missing-capture"
    assert any(
        "capture missing scenario: lianli-v2117-02-mb-pwm-sync.pcapng" == command
        for command in report["recommended_commands"]
    )
    assert any(
        "capture-timeline-report" in command
        for command in scenarios["baseline"]["recommended_commands"]
    )


def test_pre_write_validation_requires_each_source_capture_to_pass(tmp_path):
    operation = {
        "operation": "live-pwm",
        "runtime_context": {
            "contexts": [
                {
                    "mac": "aa:bb:cc:dd:ee:ff",
                    "channel": 8,
                    "rx_type": 3,
                }
            ],
        },
        "source_capture_paths": [
            str(tmp_path / "lianli-v2117-01-direct-fan-speed.json"),
            str(tmp_path / "lianli-v2117-04-sort-quick-sync.json"),
        ],
    }
    commands = [
        "python tools/lianli_wireless_probe.py linux-control-packet-compare "
        f"{tmp_path} live-pwm {tmp_path / 'lianli-v2117-01-direct-fan-speed.json'}",
        "python tools/lianli_wireless_probe.py linux-control-packet-compare "
        f"{tmp_path} live-pwm {tmp_path / 'lianli-v2117-04-sort-quick-sync.json'}",
    ]
    direct_summary = {
        "path": str(tmp_path / "direct-a.json"),
        "schema_version": capture_module.LINUX_CONTROL_PACKET_COMPARE_SCHEMA_VERSION,
        "control_operation": "live-pwm",
        "target_id": "aa:bb:cc:dd:ee:ff@ch8/rx3",
        "observed_capture": "lianli-v2117-01-direct-fan-speed.json",
        "status": "matched",
        "matched": True,
        "exact_match": True,
        "semantic_match": True,
        "diagnostics_status": "exact-match",
        "write_gate_status": "pass",
        "allows_guarded_write": True,
    }
    duplicate_direct_summary = {
        **direct_summary,
        "path": str(tmp_path / "direct-b.json"),
    }

    partial = capture_module._linux_control_action_plan_pre_write_validation(
        commands,
        operation,
        experiment_summary={
            "packet_compare_runs": [
                direct_summary,
                duplicate_direct_summary,
            ]
        },
    )

    assert partial["validation_status"] == "incomplete"
    assert partial["allows_guarded_write"] is False
    assert partial["source_capture_coverage"]["expected_count"] == 2
    assert partial["source_capture_coverage"]["passed_count"] == 1
    assert partial["source_capture_coverage"]["missing_source_capture_paths"] == [
        str(tmp_path / "lianli-v2117-04-sort-quick-sync.json")
    ]

    legacy_schema = {
        **direct_summary,
        "schema_version": "",
    }
    legacy = capture_module._linux_control_action_plan_pre_write_validation(
        commands[:1],
        {
            **operation,
            "source_capture_paths": [str(tmp_path / "lianli-v2117-01-direct-fan-speed.json")],
        },
        experiment_summary={"packet_compare_runs": [legacy_schema]},
    )

    assert legacy["validation_status"] == "invalid-schema"
    assert legacy["allows_guarded_write"] is False
    assert legacy["observed_results"][0]["schema_version_valid"] is False
    assert legacy["observed_results"][0]["schema_version_required"] == (
        capture_module.LINUX_CONTROL_PACKET_COMPARE_SCHEMA_VERSION
    )
    legacy_summary = capture_module._linux_control_action_plan_packet_compare_validation(
        {"packet_compare_runs": [legacy_schema]}
    )
    assert legacy_summary["schema_version_required"] == capture_module.LINUX_CONTROL_PACKET_COMPARE_SCHEMA_VERSION
    assert legacy_summary["valid_schema_count"] == 0
    assert legacy_summary["invalid_schema_count"] == 1
    assert legacy_summary["pass_count"] == 0
    legacy_execution = capture_module._linux_control_action_plan_execution(
        phase="safe-experiment",
        command="python tools/lianli_wireless_probe.py safe-pwm-experiment --confirm WRITE-LIANLI",
        writes_usb=True,
        pre_write_validation=legacy,
    )
    assert legacy_execution["status"] == "invalid-pre-write-validation-schema"
    assert legacy_execution["write_command_enabled"] is False
    legacy_readiness = capture_module._linux_control_action_plan_guarded_write_readiness(
        [
            {
                "id": "safe-experiment:live-pwm",
                "phase": "safe-experiment",
                "status": "ready",
                "pre_write_validation": legacy,
            }
        ]
    )
    assert legacy_readiness["status"] == "invalid-pre-write-validation-schema"
    assert legacy_readiness["invalid_schema_pre_write_validation_action_ids"] == ["safe-experiment:live-pwm"]

    sort_pass = {
        **direct_summary,
        "path": str(tmp_path / "sort.json"),
        "observed_capture": "lianli-v2117-04-sort-quick-sync.json",
    }
    complete = capture_module._linux_control_action_plan_pre_write_validation(
        commands,
        operation,
        experiment_summary={
            "packet_compare_runs": [
                direct_summary,
                sort_pass,
            ]
        },
    )

    assert complete["validation_status"] == "passed"
    assert complete["allows_guarded_write"] is True
    assert complete["source_capture_coverage"]["missing_source_capture_paths"] == []


def test_capture_gap_report_prioritizes_baseline_when_no_captures_exist(tmp_path):
    base = "lianli-v2117"
    report = capture_gap_report(tmp_path, capture_base=base)

    assert report["operation"] == "capture-gap-report"
    assert report["status"] == "needs-all-windows-captures"
    assert report["source_capture_set_status"] == "no-captures-found"
    assert report["scenario_count"] == 7
    assert report["found_capture_count"] == 0
    assert report["missing_capture_count"] == 7
    assert report["next_capture"]["id"] == "baseline"
    assert report["next_capture"]["phase"] == "identity-and-readonly"
    assert report["next_capture"]["capture_file"] == f"{base}-00-baseline.pcapng"
    assert report["proof_gates"][0]["name"] == "baseline-before-writes"
    assert report["proof_gates"][0]["status"] == "blocked"
    assert report["operation_gaps"][0]["operation"] == "receiver-snapshot"
    assert any(
        command == f"capture next scenario: {base}-00-baseline.pcapng"
        for command in report["recommended_commands"]
    )


def test_capture_gap_report_fills_next_compare_command_from_capture_note(tmp_path):
    base = "lianli-v2117"
    baseline_packets = [
        build_wireless_list_request(),
        build_master_query_request(8),
        _snapshot_payload(),
    ]
    _write_tshark_json_capture(
        tmp_path / f"{base}-00-baseline.json",
        baseline_packets,
        product_ids=["0x8041", "0x8040", "0x8041"],
    )
    note = windows_capture_note(
        "direct-fan-speed",
        capture_base=base,
        receiver_mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        led_count=132,
        pwm_values="77,88,99,111",
        mark_actions_done=True,
    )
    (tmp_path / note["capture_note_file"]).write_text(json.dumps(note), encoding="utf-8")

    report = capture_gap_report(tmp_path, capture_base=base)
    compare_commands = [
        command for command in report["recommended_commands"] if "compare-capture" in command
    ]

    assert report["next_capture"]["id"] == "direct-fan-speed"
    assert compare_commands
    assert any("aa:bb:cc:dd:ee:ff" in command for command in compare_commands)
    assert all("<receiver-mac>" not in command for command in compare_commands)
    assert any("--channel '8'" in command and "--rx-type '3'" in command for command in compare_commands)
    assert any("--pwm-values '77,88,99,111'" in command for command in compare_commands)
    assert all("<captured-or-expected-pwm-tuple>" not in command for command in compare_commands)


def test_windows_capture_runbook_combines_plan_with_current_gaps(tmp_path):
    base = "lianli-v2117"
    report = windows_capture_runbook(tmp_path, capture_base=base)
    first_task = report["tasks"][0]

    assert report["operation"] == "windows-capture-runbook"
    assert report["status"] == "needs-all-windows-captures"
    assert report["scenario_count"] == 7
    assert report["missing_task_count"] == 7
    assert report["next_task"]["id"] == "baseline"
    assert report["next_task"]["priority"] == 0
    assert report["capture_note_sidecar_command_count"] == 7
    assert first_task["capture_file"] == f"{base}-00-baseline.pcapng"
    assert first_task["capture_path"] == str(tmp_path / f"{base}-00-baseline.pcapng")
    assert first_task["capture_note_file"] == f"{base}-00-baseline.notes.json"
    assert first_task["capture_note_status"] == "missing"
    assert first_task["capture_note_template"]["schema_version"] == "lianli-windows-capture-note/v1"
    assert first_task["capture_note_template"]["scenario_id"] == "baseline"
    assert first_task["capture_note_template"]["target_context"]["receiver_mac"] == ""
    assert f"{base}-00-baseline.notes.json" in first_task["capture_note_command"]
    assert "windows-capture-note baseline" in first_task["capture_note_command"]
    assert first_task["windows_actions"][0] == "Start USBPcap capture."
    assert "receiver-list-request" in first_task["expected_evidence"]
    assert "0416:8040" in first_task["acceptance_checks"][1]
    assert "tshark -r" in first_task["manual_tshark_export_command"]
    assert str(tmp_path / f"{base}-00-baseline.pcapng") in first_task["linux_analysis_commands"][0]
    assert any("capture-gap-report" in command for command in report["post_batch_commands"])
    assert report["recommended_commands"][3] == f"capture next scenario: {base}-00-baseline.pcapng"
    assert "windows-capture-note baseline" in report["recommended_commands"][4]
    assert [item["id"] for item in report["capture_note_sidecar_queue"][:3]] == [
        "baseline",
        "direct-fan-speed",
        "motherboard-pwm-sync",
    ]
    assert "windows-capture-note direct-fan-speed" in report["capture_note_sidecar_commands"][1]
    assert any("windows-capture-note direct-fan-speed" in command for command in report["recommended_commands"])


def test_capture_runbook_prioritizes_target_changelog_scenarios(tmp_path):
    base = "lianli-v2117"
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "analyze-changelog-official.json").write_text(
        json.dumps(
            {
                "operation": "analyze-changelog",
                "source": "fixture-changelog.html",
                "entries": [
                    {
                        "version": "2.1.17",
                        "release_date": "2026-03-02",
                        "wireless_score": 48,
                        "matched_keywords": ["rf", "binding", "rpm-pwm", "l-wireless", "lighting"],
                        "category_scores": {"transport": 14, "binding": 12, "fan": 10},
                        "matched_lines": [
                            {
                                "text": "L-Wireless Utility fan settings switch to quick sync after sort settings.",
                                "keywords": ["l-wireless", "lighting"],
                                "score": 20,
                            },
                            {
                                "text": "RF unbind/rebind fan speed settings behavior changed.",
                                "keywords": ["rf", "binding", "rpm-pwm"],
                                "score": 18,
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    wireless_js = artifact_dir / "assets-v2.1.17-wireless.js"
    wireless_js.write_text(
        "ipcRenderer.send('message-queue', JSON.stringify({event:'updateControllerVersion'}));"
        "pipe.writeSettings('ALV2Controller-', data);"
        "pipe.writeSettings('ALV2LightEfferct-', data);",
        encoding="utf-8",
    )
    (artifact_dir / "extract-wireless-js-v2.1.17.json").write_text(
        json.dumps(extract_wireless_js_clues(wireless_js)),
        encoding="utf-8",
    )

    runbook = windows_capture_runbook(tmp_path, capture_base=base, artifact_dir=artifact_dir)
    tasks = {item["id"]: item for item in runbook["tasks"]}
    gap_report = capture_gap_report(tmp_path, capture_base=base, artifact_dir=artifact_dir)
    gap_ids = [item["id"] for item in gap_report["scenario_gaps"]]

    assert runbook["artifact_capture_context_status"] == "target-found"
    assert runbook["artifact_capture_changelog_score"] == 48
    assert tasks["sort-quick-sync"]["base_priority"] == 50
    assert tasks["sort-quick-sync"]["priority"] == 15
    assert tasks["sort-quick-sync"]["changelog_focus"]["matched"] is True
    assert "quick sync" in tasks["sort-quick-sync"]["changelog_focus"]["evidence"][0]["text"]
    assert tasks["direct-fan-speed"]["interface_focus"]["matched"] is True
    assert tasks["direct-fan-speed"]["interface_focus"]["source"] == "target-version"
    assert tasks["direct-fan-speed"]["interface_focus"]["matched_hints"] == ["fan-controller-settings"]
    assert tasks["direct-fan-speed"]["interface_capture_actions"][0]["hint"] == "fan-controller-settings"
    assert "Apply two fixed manual fan speeds" in tasks["direct-fan-speed"]["interface_capture_actions"][0]["ui_actions"][2]
    assert tasks["direct-fan-speed"]["capture_note_template"]["interface_capture_actions"][0]["label"] == "Apply fan-controller speed settings"
    assert tasks["direct-fan-speed"]["capture_note_template"]["interface_actions_completed"][0] == {
        "hint": "fan-controller-settings",
        "label": "Apply fan-controller speed settings",
        "source": "target-version",
        "step": 1,
        "action": "Open the target wireless fan group controller settings.",
        "done": False,
        "operator_observation": "",
        "observation_prompts": [
            "Record the exact percent or RPM values used and whether all fans changed together.",
            "Record any UI apply/saved status after each change.",
        ],
        "capture_note": "Maps to official JS controller settings keys such as ALV2Controller- / SLV2Controller-.",
    }
    assert "--artifact-dir" in tasks["direct-fan-speed"]["capture_note_command"]
    assert runbook["capture_note_sidecar_queue"][1]["id"] == "direct-fan-speed"
    assert runbook["capture_note_sidecar_queue"][1]["interface_action_count"] == 1
    assert "--artifact-dir" in runbook["capture_note_sidecar_queue"][1]["capture_note_command"]
    assert runbook["capture_note_sidecar_queue"][2]["id"] == "sort-quick-sync"
    assert tasks["lighting-static-and-off"]["interface_focus"]["matched_hints"] == ["lighting-effect-settings"]
    assert "lighting effect settings" in tasks["lighting-static-and-off"]["interface_capture_actions"][0]["ui_actions"][0]
    assert tasks["rf-rebind"]["base_priority"] == 90
    assert tasks["rf-rebind"]["priority"] == 70
    assert tasks["rf-rebind"]["changelog_focus"]["matched_keywords"] == ["binding", "rf"]
    assert [item["id"] for item in runbook["tasks"][:4]] == [
        "baseline",
        "direct-fan-speed",
        "sort-quick-sync",
        "motherboard-pwm-sync",
    ]
    assert runbook["tasks"][0]["priority_order"] == 1
    assert gap_ids[:4] == ["baseline", "direct-fan-speed", "sort-quick-sync", "motherboard-pwm-sync"]
    assert gap_report["artifact_capture_context_status"] == "target-found"
    direct_gap = next(item for item in gap_report["scenario_gaps"] if item["id"] == "direct-fan-speed")
    assert direct_gap["interface_focus"]["matched_hints"] == ["fan-controller-settings"]
    assert direct_gap["interface_capture_actions"][0]["observations"][0].startswith("Record the exact percent")
    assert "--artifact-dir" in gap_report["recommended_commands"][0]

    note = windows_capture_note(
        "direct-fan-speed",
        capture_base=base,
        artifact_dir=artifact_dir,
        receiver_mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        led_count=132,
        pwm_values="77,88,99,111",
        mark_actions_done=True,
    )

    assert note["artifact_capture_context_status"] == "target-found"
    assert note["interface_focus"]["matched_hints"] == ["fan-controller-settings"]
    assert len(note["interface_actions_completed"]) == 3
    assert all(item["done"] for item in note["interface_actions_completed"])


def test_windows_capture_note_generates_filled_sidecar_template():
    base = "lianli-v2117"
    note = windows_capture_note(
        "direct-fan-speed",
        capture_base=base,
        captured_at="2026-05-26T21:00:00+08:00",
        operator="xjw",
        receiver_mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        led_count=132,
        pwm_values="77,88,99,111",
        observations=["Applied 55% then 75%; fans audibly changed."],
        mark_actions_done=True,
    )

    assert note["operation"] == "windows-capture-note"
    assert note["schema_version"] == "lianli-windows-capture-note/v1"
    assert note["status"] == "ready"
    assert note["capture_file"] == f"{base}-01-direct-fan-speed.pcapng"
    assert note["capture_note_file"] == f"{base}-01-direct-fan-speed.notes.json"
    assert note["operator"] == "xjw"
    assert note["target_context"]["receiver_mac"] == "aa:bb:cc:dd:ee:ff"
    assert note["target_context"]["channel"] == 8
    assert note["target_context"]["led_count"] == 132
    assert note["expected_parameters"]["pwm_values"] == "77,88,99,111"
    assert all(item["done"] for item in note["windows_actions_completed"])
    assert "live-pwm RF frames" in note["expected_evidence"]
    assert note["observations"] == ["Applied 55% then 75%; fans audibly changed."]


def test_capture_set_report_fills_lighting_parameters_from_capture_note(tmp_path):
    base = "lianli-v2117"
    note = windows_capture_note(
        "lighting-generated-rainbow",
        capture_base=base,
        receiver_mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        led_count=132,
        frame_count=24,
        interval_ms=50,
        effect_index=1,
        mark_actions_done=True,
    )
    (tmp_path / note["capture_note_file"]).write_text(json.dumps(note), encoding="utf-8")

    report = capture_set_report(tmp_path, capture_base=base)
    scenario = {item["id"]: item for item in report["scenarios"]}["lighting-generated-rainbow"]
    commands = scenario["contextual_planned_linux_commands"]

    assert any("--frame-count '24'" in command for command in commands)
    assert any("--interval-ms '50'" in command for command in commands)
    assert any("--effect-index '1'" in command for command in commands)
    assert any("--led-count '132'" in command for command in commands)
    assert all("<frame-count>" not in command for command in commands)
    assert all("<interval-ms>" not in command for command in commands)
    assert all("<effect-index>" not in command for command in commands)


def test_capture_set_report_reads_capture_note_sidecar_without_treating_it_as_capture(tmp_path):
    base = "lianli-v2117"
    note_path = tmp_path / f"{base}-01-direct-fan-speed.notes.json"
    note_path.write_text(
        json.dumps(
            {
                "operation": "windows-capture-note",
                "schema_version": "lianli-windows-capture-note/v1",
                "version": "2.1.17",
                "capture_base": base,
                "scenario_id": "direct-fan-speed",
                "capture_file": f"{base}-01-direct-fan-speed.pcapng",
                "captured_at": "2026-05-26T21:00:00+08:00",
                "operator": "xjw",
                "environment": "windows-vm-usb-passthrough",
                "lconnect_version": "2.1.17",
                "usbpcap_interfaces": ["0416:8040", "0416:8041"],
                "target_context": {
                    "receiver_mac": "aa:bb:cc:dd:ee:ff",
                    "master_mac": "10:20:30:40:50:60",
                    "channel": 8,
                    "rx_type": 3,
                    "device_type": 2,
                    "fan_count": 3,
                    "led_count": 132,
                },
                "expected_parameters": {
                    "pwm_values": "77,88,99,111",
                },
                "windows_actions_completed": [{"action": "Applied 55% fan speed", "done": True}],
                "observations": ["Fans audibly changed after apply."],
                "expected_evidence": ["live-pwm RF frames"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = capture_set_report(tmp_path, capture_base=base)
    gap_report = capture_gap_report(tmp_path, capture_base=base)
    runbook = windows_capture_runbook(tmp_path, capture_base=base)
    scenarios = {scenario["id"]: scenario for scenario in report["scenarios"]}
    note = scenarios["direct-fan-speed"]["capture_note"]
    direct_contextual = scenarios["direct-fan-speed"]["contextual_planned_linux_commands"]
    direct_gap = next(item for item in gap_report["scenario_gaps"] if item["id"] == "direct-fan-speed")
    direct_task = next(item for item in runbook["tasks"] if item["id"] == "direct-fan-speed")

    assert report["status_counts"] == {"missing-capture": 7}
    assert report["capture_note_status_counts"] == {"missing": 6, "ok": 1}
    assert report["capture_note_present_count"] == 1
    note_context = report["capture_note_context_summary"]
    note_operator = report["capture_note_operator_summary"]
    assert note_context["status"] == "consistent-target-context"
    assert note_context["target_context_count"] == 1
    assert note_context["complete_target_context_count"] == 1
    assert note_context["common_target_context"] == {
        "receiver_mac": "aa:bb:cc:dd:ee:ff",
        "master_mac": "10:20:30:40:50:60",
        "channel": "8",
        "rx_type": "3",
        "device_type": "2",
        "fan_count": "3",
        "led_count": "132",
    }
    assert note_context["common_target_args"] == [
        "--mac",
        "aa:bb:cc:dd:ee:ff",
        "--master-mac",
        "10:20:30:40:50:60",
        "--channel",
        "8",
        "--rx-type",
        "3",
        "--device-type",
        "2",
    ]
    assert note_operator["status"] == "ready"
    assert note_operator["ready_count"] == 1
    assert note_operator["scenarios"][0]["windows_action_count"] == 1
    assert note_operator["scenarios"][0]["windows_actions_done_count"] == 1
    assert scenarios["direct-fan-speed"]["found"] is False
    assert scenarios["direct-fan-speed"]["status"] == "missing-capture"
    assert any("aa:bb:cc:dd:ee:ff" in command and "<receiver-mac>" not in command for command in direct_contextual)
    assert any("--channel '8'" in command and "--rx-type '3'" in command for command in direct_contextual)
    assert any("--pwm-values '77,88,99,111'" in command for command in direct_contextual)
    assert all("<captured-or-expected-pwm-tuple>" not in command for command in direct_contextual)
    assert direct_gap["contextual_planned_linux_commands"] == direct_contextual
    assert direct_task["linux_analysis_commands"][-1] == direct_contextual[-1].replace(
        f"{base}-01-direct-fan-speed.pcapng",
        str(tmp_path / f"{base}-01-direct-fan-speed.pcapng"),
    )
    assert note["status"] == "ok"
    assert note["operator_status"] == "ready"
    assert note["windows_actions_all_done"] is True
    assert note["operator"] == "xjw"
    assert note["target_context"]["receiver_mac"] == "aa:bb:cc:dd:ee:ff"
    assert note["expected_parameters"] == {"pwm_values": "77,88,99,111"}
    assert note["usbpcap_interfaces"] == ["0416:8040", "0416:8041"]
    assert note["observations"] == ["Fans audibly changed after apply."]


def test_capture_set_report_flags_capture_note_target_context_conflicts(tmp_path):
    base = "lianli-v2117"
    baseline_note = windows_capture_note(
        "baseline",
        capture_base=base,
        receiver_mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        led_count=132,
        mark_actions_done=True,
    )
    direct_note = windows_capture_note(
        "direct-fan-speed",
        capture_base=base,
        receiver_mac="aa:bb:cc:dd:ee:00",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        led_count=132,
        mark_actions_done=True,
    )
    (tmp_path / baseline_note["capture_note_file"]).write_text(json.dumps(baseline_note), encoding="utf-8")
    (tmp_path / direct_note["capture_note_file"]).write_text(json.dumps(direct_note), encoding="utf-8")

    report = capture_set_report(tmp_path, capture_base=base)
    note_context = report["capture_note_context_summary"]
    gap_report = capture_gap_report(tmp_path, capture_base=base)

    assert note_context["status"] == "target-context-conflict"
    assert note_context["target_context_count"] == 2
    assert note_context["conflicts"] == [
        {
            "field": "receiver_mac",
            "values": [
                {"value": "aa:bb:cc:dd:ee:00", "scenario_ids": ["direct-fan-speed"]},
                {"value": "aa:bb:cc:dd:ee:ff", "scenario_ids": ["baseline"]},
            ],
        }
    ]
    assert "receiver_mac" not in note_context["common_target_context"]
    assert note_context["common_target_context"]["channel"] == "8"
    assert gap_report["capture_note_context_status"] == "target-context-conflict"
    assert gap_report["capture_note_context_summary"]["conflicts"] == note_context["conflicts"]


def test_capture_set_report_summarizes_capture_note_operator_confirmation(tmp_path):
    base = "lianli-v2117"
    note = windows_capture_note(
        "direct-fan-speed",
        capture_base=base,
        receiver_mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        led_count=132,
        mark_actions_done=False,
    )
    (tmp_path / note["capture_note_file"]).write_text(json.dumps(note), encoding="utf-8")

    report = capture_set_report(tmp_path, capture_base=base)
    gap_report = capture_gap_report(tmp_path, capture_base=base)
    note_payload = {scenario["id"]: scenario for scenario in report["scenarios"]}["direct-fan-speed"]["capture_note"]
    operator_summary = report["capture_note_operator_summary"]

    assert note_payload["operator_status"] == "needs-action-confirmation"
    assert note_payload["windows_action_count"] == 4
    assert note_payload["windows_actions_done_count"] == 0
    assert note_payload["windows_actions_all_done"] is False
    assert operator_summary["status"] == "needs-action-confirmation"
    assert operator_summary["status_counts"] == {"needs-action-confirmation": 1, "missing": 6}
    assert gap_report["capture_note_operator_status"] == "needs-action-confirmation"
    assert gap_report["capture_note_operator_summary"]["status"] == "needs-action-confirmation"


def test_capture_set_report_requires_interface_action_confirmation(tmp_path):
    base = "lianli-v2117"
    note = windows_capture_note(
        "direct-fan-speed",
        capture_base=base,
        receiver_mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        led_count=132,
        pwm_values="77,88,99,111",
        mark_actions_done=True,
    )
    note["interface_actions_completed"] = [
        {
            "hint": "fan-controller-settings",
            "label": "Apply fan-controller speed settings",
            "source": "target-version",
            "step": 1,
            "action": "Open the target wireless fan group controller settings.",
            "done": False,
            "operator_observation": "",
        }
    ]
    note["status"] = "ready"
    (tmp_path / note["capture_note_file"]).write_text(json.dumps(note), encoding="utf-8")

    report = capture_set_report(tmp_path, capture_base=base)
    gap_report = capture_gap_report(tmp_path, capture_base=base)
    note_payload = {scenario["id"]: scenario for scenario in report["scenarios"]}["direct-fan-speed"]["capture_note"]
    operator_summary = report["capture_note_operator_summary"]

    assert note_payload["operator_status"] == "needs-action-confirmation"
    assert note_payload["windows_actions_all_done"] is True
    assert note_payload["interface_action_count"] == 1
    assert note_payload["interface_actions_done_count"] == 0
    assert note_payload["interface_actions_all_done"] is False
    assert operator_summary["status"] == "needs-action-confirmation"
    assert operator_summary["scenarios"][0]["interface_action_count"] == 1
    assert operator_summary["scenarios"][0]["interface_actions_done_count"] == 0
    assert gap_report["capture_note_operator_status"] == "needs-action-confirmation"


def test_capture_set_report_feeds_static_rgb_observed_parameters_to_packet_preview(tmp_path):
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    base = "lianli-v2117"
    baseline_packets = [
        build_wireless_list_request(),
        build_master_query_request(8),
        _snapshot_payload(),
    ]
    rgb_packets = LianLiWirelessBackend().build_static_rgb_packets(
        target,
        (0, 0, 0),
        interval_ms=40,
        effect_index=2,
        led_count=132,
    )
    _write_tshark_json_capture(
        tmp_path / f"{base}-00-baseline.json",
        baseline_packets,
        product_ids=["0x8041", "0x8040", "0x8041"],
    )
    rgb_capture = tmp_path / f"{base}-05-lighting-static-off.json"
    _write_tshark_json_capture(
        rgb_capture,
        rgb_packets,
        product_ids=["0x8040"] * len(rgb_packets),
    )
    sys_root = tmp_path / "sys"
    dev_root = tmp_path / "dev"
    _write_lianli_usb_sysfs_and_dev(sys_root, dev_root)

    report = capture_set_report(
        tmp_path,
        capture_base=base,
        led_count=132,
        rainbow_frames=3,
        interval_ms=40,
        effect_index=2,
    )

    assert report["aggregate_rf_operations"] == {"live-rgb": 1}
    parameter_index = {
        (item["operation"], item["field"], item["value"]): item
        for item in report["cross_scenario_deltas"]["parameter_index"]
    }
    assert parameter_index[("live-rgb", "rgb_static_colors", "#000000")]["unique_to_scenario"] == "lighting-static-and-off"
    assert parameter_index[("live-rgb", "led_counts", "132")]["unique_to_scenario"] == "lighting-static-and-off"
    assert parameter_index[("live-rgb", "effect_indexes", "2")]["unique_to_scenario"] == "lighting-static-and-off"
    assert report["linux_live_write_targets"][0]["operation"] == "live-rgb"
    assert report["linux_live_write_targets"][0]["rgb_static_colors"] == {"#000000": 1}
    assert report["linux_live_write_targets"][0]["led_counts"] == [132]
    assert report["linux_live_write_targets"][0]["effect_indexes"] == [2]
    assert report["linux_live_write_targets"][0]["interval_ms_values"] == [40]
    matrix = {item["operation"]: item for item in report["linux_control_matrix"]}
    assert matrix["live-rgb"]["overall_status"] == "ready-for-guarded-experiment"
    assert matrix["live-rainbow"]["overall_status"] == "needs-windows-capture"

    manifest = linux_control_manifest_report(
        tmp_path,
        capture_base=base,
        led_count=132,
        rainbow_frames=3,
        interval_ms=40,
        effect_index=2,
    )
    operations = {item["operation"]: item for item in manifest["operations"]}
    rgb_observed = operations["live-rgb"]["observed_parameters"]
    assert rgb_observed["default_color"] == [0, 0, 0]
    assert rgb_observed["default_color_hex"] == "#000000"
    assert rgb_observed["default_led_count"] == 132
    assert rgb_observed["default_effect_index"] == 2
    assert rgb_observed["default_interval_ms"] == 40

    registry = linux_control_target_registry_report(
        tmp_path,
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        led_count=132,
        rainbow_frames=3,
        interval_ms=40,
        effect_index=2,
    )
    target_entry = registry["targets"][0]
    assert target_entry["ready_operations"] == ["live-rgb"]
    assert target_entry["observed_parameters"]["live-rgb"]["default_color"] == [0, 0, 0]
    assert target_entry["observed_parameters"]["live-rgb"]["default_led_count"] == 132

    preview = linux_control_packet_preview_report(
        tmp_path,
        control_operation="live-rgb",
        target_id=target_entry["id"],
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
    )
    assert preview["parameters"]["color"] == [0, 0, 0]
    assert preview["parameters"]["color_source"] == "capture-evidence"
    assert preview["parameters"]["led_count"] == 132
    assert preview["parameters"]["led_count_source"] == "capture-evidence"
    assert preview["parameters"]["effect_index"] == 2
    assert preview["parameters"]["effect_index_source"] == "capture-evidence"
    assert preview["parameters"]["interval_ms"] == 40
    assert preview["parameters"]["interval_ms_source"] == "capture-evidence"
    assert preview["packet_preview"]["rf_operations"] == {"live-rgb": 1}

    comparison = linux_control_packet_compare_report(
        tmp_path,
        rgb_capture,
        control_operation="live-rgb",
        target_id=target_entry["id"],
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
    )
    assert comparison["status"] == "matched"
    assert comparison["exact_match"] is True
    assert comparison["semantic_match"] is True
    assert comparison["parameters"]["color_source"] == "capture-evidence"


def test_capture_set_report_feeds_generated_rainbow_parameters_to_packet_preview(tmp_path):
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    base = "lianli-v2117"
    baseline_packets = [
        build_wireless_list_request(),
        build_master_query_request(8),
        _snapshot_payload(),
    ]
    rainbow_packets = LianLiWirelessBackend().build_rainbow_rgb_packets(
        target,
        frame_count=3,
        interval_ms=40,
        effect_index=2,
        led_count=132,
    )
    _write_tshark_json_capture(
        tmp_path / f"{base}-00-baseline.json",
        baseline_packets,
        product_ids=["0x8041", "0x8040", "0x8041"],
    )
    rainbow_capture = tmp_path / f"{base}-06-lighting-generated-rainbow.json"
    _write_tshark_json_capture(
        rainbow_capture,
        rainbow_packets,
        product_ids=["0x8040"] * len(rainbow_packets),
    )
    sys_root = tmp_path / "sys"
    dev_root = tmp_path / "dev"
    _write_lianli_usb_sysfs_and_dev(sys_root, dev_root)

    report = capture_set_report(
        tmp_path,
        capture_base=base,
        led_count=132,
        rainbow_frames=3,
        interval_ms=40,
        effect_index=2,
    )

    assert report["aggregate_rf_operations"] == {"live-rgb": 1}
    parameter_index = {
        (item["operation"], item["field"], item["value"]): item
        for item in report["cross_scenario_deltas"]["parameter_index"]
    }
    assert parameter_index[("live-rgb", "rgb_rainbow_generated", "132,3,40,2")]["unique_to_scenario"] == (
        "lighting-generated-rainbow"
    )
    assert parameter_index[("live-rgb", "frame_counts", "3")]["unique_to_scenario"] == "lighting-generated-rainbow"
    scenarios = {scenario["id"]: scenario for scenario in report["scenarios"]}
    assert scenarios["lighting-generated-rainbow"]["status"] == "evidence-found"
    assert scenarios["lighting-generated-rainbow"]["matched_evidence"] == [
        "generated rainbow RGB RF frame",
        "decoded rainbow timing/LED parameters",
    ]
    assert scenarios["lighting-static-and-off"]["status"] == "missing-capture"
    live_target = report["linux_live_write_targets"][0]
    assert live_target["operation"] == "live-rgb"
    assert live_target["rgb_rainbow_generated"] == {"132,3,40,2": 1}
    assert "rgb_static_colors" not in live_target
    matrix = {item["operation"]: item for item in report["linux_control_matrix"]}
    assert matrix["live-rainbow"]["overall_status"] == "ready-for-guarded-experiment"
    assert matrix["live-rgb"]["overall_status"] != "ready-for-guarded-experiment"

    manifest = linux_control_manifest_report(
        tmp_path,
        capture_base=base,
        led_count=132,
        rainbow_frames=3,
        interval_ms=40,
        effect_index=2,
    )
    operations = {item["operation"]: item for item in manifest["operations"]}
    rainbow_observed = operations["live-rainbow"]["observed_parameters"]
    assert rainbow_observed["default_led_count"] == 132
    assert rainbow_observed["default_frame_count"] == 3
    assert rainbow_observed["default_interval_ms"] == 40
    assert rainbow_observed["default_effect_index"] == 2
    assert rainbow_observed["generated_rainbow_matches"] == [
        {
            "led_count": 132,
            "frame_count": 3,
            "interval_ms": 40,
            "effect_index": 2,
            "count": 1,
        }
    ]
    assert operations["live-rgb"]["observed_parameters"] == {}

    registry = linux_control_target_registry_report(
        tmp_path,
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        led_count=132,
        rainbow_frames=3,
        interval_ms=40,
        effect_index=2,
    )
    target_entry = registry["targets"][0]
    assert target_entry["ready_operations"] == ["live-rainbow"]
    assert target_entry["observed_parameters"]["live-rainbow"]["default_led_count"] == 132
    assert "live-rgb" not in target_entry["observed_parameters"]

    action_plan = linux_control_action_plan_report(
        tmp_path,
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
        led_count=132,
        rainbow_frames=3,
        interval_ms=40,
        effect_index=2,
    )
    actions = {item["id"]: item for item in action_plan["actions"]}
    pre_write = actions["safe-experiment:live-rainbow"]["pre_write_validation_commands"]
    assert action_plan["preflight"]["led_count"] == 132
    assert action_plan["preflight"]["rainbow_frames"] == 3
    assert action_plan["preflight"]["interval_ms"] == 40
    assert action_plan["preflight"]["effect_index"] == 2
    assert len(pre_write) == 2
    assert "linux-control-packet-preview" in pre_write[0]
    assert "linux-control-packet-compare" in pre_write[1]
    assert str(rainbow_capture) in pre_write[1]
    for command in pre_write:
        assert "--led-count 132" in command
        assert "--frame-count 3" in command
        assert "--interval-ms 40" in command
        assert "--effect-index 2" in command

    preview = linux_control_packet_preview_report(
        tmp_path,
        control_operation="live-rainbow",
        target_id=target_entry["id"],
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
    )
    assert preview["parameters"]["color_source"] == "default"
    assert preview["parameters"]["led_count"] == 132
    assert preview["parameters"]["led_count_source"] == "capture-evidence"
    assert preview["parameters"]["frame_count"] == 3
    assert preview["parameters"]["frame_count_source"] == "capture-evidence"
    assert preview["parameters"]["interval_ms"] == 40
    assert preview["parameters"]["interval_ms_source"] == "capture-evidence"
    assert preview["parameters"]["effect_index"] == 2
    assert preview["parameters"]["effect_index_source"] == "capture-evidence"
    assert preview["packet_preview"]["rf_operations"] == {"live-rgb": 1}

    comparison = linux_control_packet_compare_report(
        tmp_path,
        rainbow_capture,
        control_operation="live-rainbow",
        target_id=target_entry["id"],
        sys_root=sys_root,
        dev_root=dev_root,
        capture_base=base,
    )
    assert comparison["status"] == "matched"
    assert comparison["exact_match"] is True
    assert comparison["semantic_match"] is True
    assert comparison["parameters"]["led_count_source"] == "capture-evidence"
    assert comparison["parameters"]["frame_count_source"] == "capture-evidence"
    assert comparison["parameters"]["interval_ms_source"] == "capture-evidence"
    assert comparison["parameters"]["effect_index_source"] == "capture-evidence"


def test_capture_set_live_target_commands_match_operation_type():
    targets = [
        {
            "operation": operation,
            "target_macs": ["aa:bb:cc:dd:ee:ff"],
        }
        for operation in (
            "live-pwm",
            "live-pwm-sync",
            "live-pwm-mirror",
            "live-bind",
            "live-unbind",
            "live-rgb",
            "live-rainbow",
        )
    ]

    commands = capture_module._capture_set_live_target_experiment_commands(targets)

    assert any("safe-pwm-experiment --mac aa:bb:cc:dd:ee:ff" in command for command in commands)
    assert any("safe-sync-experiment --mac aa:bb:cc:dd:ee:ff" in command for command in commands)
    assert any("safe-pwm-mirror-experiment --mac aa:bb:cc:dd:ee:ff" in command for command in commands)
    assert any("safe-bind-experiment --mac aa:bb:cc:dd:ee:ff --rx-type" in command for command in commands)
    assert any("safe-unbind-experiment --mac aa:bb:cc:dd:ee:ff" in command for command in commands)
    assert any("safe-rgb-experiment --mac aa:bb:cc:dd:ee:ff --color 255,0,0" in command for command in commands)
    assert any("safe-rainbow-experiment --mac aa:bb:cc:dd:ee:ff" in command for command in commands)
    rgb_only = capture_module._capture_set_live_target_experiment_commands(targets, operation_filter="live-rgb")
    assert len(rgb_only) == 1
    assert "safe-rgb-experiment" in rgb_only[0]
    assert "safe-pwm-experiment" not in rgb_only[0]


def test_capture_signature_match_uses_shape_when_capture_mac_differs():
    target = WirelessDeviceInfo(
        mac="de:ad:be:ef:00:01",
        master_mac="01:02:03:04:05:06",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(45, 55, 65, 75),
        fan_rpm=(900, 1000, 0, 0),
        command_sequence=19,
        raw=bytes(42),
    )
    packets = LianLiWirelessBackend().build_motherboard_pwm_sync_packets(target)

    report = capture_signature_match_packets(
        packets,
        source="real-mac-sync.txt",
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )
    items = {item["operation"]: item for item in report["items"]}

    assert report["matched_operations"] == ["pwm-sync-enable"]
    sync = items["pwm-sync-enable"]
    assert sync["matched"] is True
    assert sync["packet_sequence_match"]["matched"] is False
    assert sync["semantic_match"] is False
    assert sync["shape_match"] is True
    assert sync["shape"]["matches"][0]["target_mac"] == "de:ad:be:ef:00:01"
    assert sync["shape"]["matches"][0]["pwm_values"] == [6, 6, 6, 6]
    assert "--mac de:ad:be:ef:00:01" in sync["observed_commands"]["compare_capture_commands"][0]
    assert "--master-mac 01:02:03:04:05:06" in sync["observed_commands"]["compare_capture_commands"][0]
    assert report["matched_commands"] == sync["observed_commands"]["compare_capture_commands"]
    assert sync["score"] == 70


def test_capture_signature_match_detects_arbitrary_direct_pwm_tuple():
    target = WirelessDeviceInfo(
        mac="de:ad:be:ef:00:02",
        master_mac="01:02:03:04:05:06",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(45, 55, 65, 75),
        fan_rpm=(900, 1000, 0, 0),
        command_sequence=19,
        raw=bytes(42),
    )
    packets = LianLiWirelessBackend().build_pwm_packets(target, [77, 88, 99, 111])

    report = capture_signature_match_packets(
        packets,
        source="real-direct-pwm.txt",
        led_count=12,
        rainbow_frames=3,
        interval_ms=40,
    )
    items = {item["operation"]: item for item in report["items"]}

    assert report["matched_operations"] == ["pwm"]
    pwm = items["pwm"]
    assert pwm["matched"] is True
    assert pwm["semantic_match"] is False
    assert pwm["shape_match"] is True
    assert pwm["shape"]["matches"][0]["reason"] == "direct PWM tuple observed"
    assert pwm["shape"]["matches"][0]["pwm_values"] == [77, 88, 99, 111]
    observed_compare = pwm["observed_commands"]["compare_capture_commands"][0]
    assert "--mac de:ad:be:ef:00:02" in observed_compare
    assert "--pwm-values 77,88,99,111" in observed_compare
    assert report["matched_commands"] == [observed_compare]


def test_capture_loader_rejects_raw_pcapng_without_tshark(monkeypatch, tmp_path):
    path = tmp_path / "capture.pcapng"
    path.write_bytes(b"\x0a\x0d\x0d\x0a" + bytes(32))
    monkeypatch.setattr(capture_module.shutil, "which", lambda name: None)

    try:
        load_capture_packets(path)
    except ValueError as error:
        assert "raw pcap/pcapng" in str(error)
        assert "tshark -r" in str(error)
        assert "usb.capdata" in str(error)
    else:
        raise AssertionError("raw pcapng should be rejected")


def test_compare_capture_reports_exact_match_with_report_id_prefix():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    expected = build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [120]))
    observed = [bytes([0]) + packet for packet in expected]

    comparison = compare_capture_packets(
        observed,
        expected,
        source="observed",
        expected_source="expected-pwm",
    )

    assert comparison["operation"] == "compare-capture"
    assert comparison["matched"] is True
    assert comparison["exact_match"] is True
    assert comparison["semantic_match"] is True
    assert comparison["exact"]["matched_count"] == 1
    assert comparison["semantic"]["matches"][0]["operation"] == "live-pwm"


def test_compare_capture_semantic_match_ignores_pwm_sequence_byte():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    expected = build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [120]))
    observed = build_rf_chunks(
        target.channel,
        target.rx_type,
        build_pwm_payload(target, [120], sequence_index=99),
    )

    comparison = compare_capture_packets(observed, expected)

    assert comparison["matched"] is True
    assert comparison["exact_match"] is False
    assert comparison["diagnostics"]["status"] == "semantic-match-exact-mismatch"
    assert comparison["diagnostics"]["primary"] == "exact"
    closest = comparison["exact"]["missing"][0]["closest_observed"]
    assert closest["observed_index"] == 0
    assert {
        "field": "sequence",
        "expected": 8,
        "observed": 99,
    } in closest["differing_fields"]
    assert comparison["diagnostics"]["nearest_differences"][0]["differing_fields"] == closest["differing_fields"]


def test_compare_capture_matches_rainbow_rgb_sequence():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    packets = LianLiWirelessBackend().build_rainbow_rgb_packets(
        target,
        frame_count=3,
        interval_ms=40,
        effect_index=2,
    )

    comparison = compare_capture_packets(
        packets,
        packets,
        source="observed-rainbow",
        expected_source="expected-rainbow",
    )

    assert comparison["matched"] is True
    assert comparison["exact_match"] is True
    assert comparison["semantic_match"] is True
    assert comparison["observed"]["summary"]["rf_operations"] == {"live-rgb": 1}
    assert comparison["observed"]["summary"]["rf_frame_operations"] == {"live-rgb": 11}
    assert comparison["exact"]["missing"] == []
    assert comparison["exact"]["unmatched_observed"] == []
    assert comparison["semantic"]["matched_count"] == 11
    assert comparison["semantic"]["missing"] == []
    assert comparison["semantic"]["unmatched_observed"] == []


def test_capture_replay_plan_recognizes_generated_rainbow_rgb_sequence():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    packets = LianLiWirelessBackend().build_rainbow_rgb_packets(
        target,
        frame_count=3,
        interval_ms=40,
        effect_index=2,
    )
    analysis = analyze_capture_packets(packets, source="rainbow-capture.txt")

    plan = capture_replay_plan_from_analysis(analysis)

    assert plan["replay_hint_count"] == 1
    assert plan["operation_counts"] == {"live-rgb": 1}
    item = plan["items"][0]
    assert item["compare_capture"]["expected_operation"] == "rainbow"
    assert item["decoded_args"]["rainbow_generated_match"] is True
    dry_run_argv = item["dry_run"]["argv"]
    compare_argv = item["compare_capture"]["argv"]
    assert dry_run_argv[2] == "dry-run-rainbow"
    assert compare_argv[2:5] == ["compare-capture", "rainbow-capture.txt", "rainbow"]
    for argv in (dry_run_argv, compare_argv):
        assert "--frame-count" in argv
        assert argv[argv.index("--frame-count") + 1] == "3"
        assert "--interval-ms" in argv
        assert argv[argv.index("--interval-ms") + 1] == "40"
        assert "--led-count" in argv
        assert argv[argv.index("--led-count") + 1] == "132"
        assert "--effect-index" in argv
        assert argv[argv.index("--effect-index") + 1] == "2"


def test_compare_capture_matches_observed_pwm_mirror_against_direct_builder():
    snapshot = _snapshot_payload_with_motherboard_pwm(10, 10)
    target = parse_wireless_snapshot(snapshot)[0]
    expected = build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [127]))
    observed = [snapshot] + expected

    comparison = compare_capture_packets(observed, expected)

    assert comparison["matched"] is True
    assert comparison["exact_match"] is True
    assert comparison["semantic_match"] is True
    assert comparison["observed"]["summary"]["rf_operations"] == {"live-pwm-mirror": 1}
    assert comparison["expected"]["summary"]["rf_operations"] == {"live-pwm": 1}
    assert comparison["semantic"]["matches"][0]["operation"] == "live-pwm"
    assert comparison["semantic"]["unmatched_observed"] == []


def test_compare_capture_reports_semantic_mismatch():
    target = parse_wireless_snapshot(_snapshot_payload())[0]
    expected = build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [120]))
    observed = build_rf_chunks(target.channel, target.rx_type, build_pwm_payload(target, [130]))

    comparison = compare_capture_packets(observed, expected)

    assert comparison["matched"] is False
    assert comparison["exact_match"] is False
    assert comparison["semantic_match"] is False
    assert comparison["diagnostics"]["status"] == "semantic-mismatch"
    assert comparison["diagnostics"]["primary"] == "semantic"
    assert comparison["semantic"]["missing"][0]["pwm_values"] == [120, 120, 120, 120]
    assert comparison["semantic"]["unmatched_observed"][0]["pwm_values"] == [130, 130, 130, 130]
    closest = comparison["semantic"]["missing"][0]["closest_observed"]
    assert closest["observed_index"] == 0
    assert closest["pwm_values"] == [130, 130, 130, 130]
    assert {
        "field": "pwm_values",
        "expected": [120, 120, 120, 120],
        "observed": [130, 130, 130, 130],
    } in closest["differing_fields"]
