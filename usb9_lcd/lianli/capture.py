from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

from usb9_lcd.lianli.analysis import summarize_experiment_dir
from usb9_lcd.lianli.wireless import (
    FIRST_LED_PACKET_DATA_MAX,
    FIRST_LED_PACKET_DATA_OFFSET,
    KNOWN_USB_DEVICES,
    LED_DATA_CHUNK,
    RF_GET_DEV_CMD,
    RF_MASTER_QUERY_CMD,
    RF_PACKET_HEADER,
    RF_PAYLOAD_SIZE,
    LianLiWirelessError,
    RF_RECEIVER_PID,
    RF_RECEIVER_VID,
    RF_SENDER_PID,
    RF_SENDER_VID,
    UDEV_RULES,
    LianLiWirelessBackend,
    WirelessDeviceInfo,
    build_master_query_request,
    build_wireless_list_request,
    extract_motherboard_pwm,
    generate_rainbow_rgb_frames,
    parse_master_query_response,
    parse_wireless_snapshot,
    scan_known_usb_devices,
)


PCAPNG_MAGIC = b"\x0a\x0d\x0d\x0a"
PCAP_MAGIC_VALUES = {
    b"\xd4\xc3\xb2\xa1",
    b"\xa1\xb2\xc3\xd4",
    b"\x4d\x3c\xb2\xa1",
    b"\xa1\xb2\x3c\x4d",
}
CAPTURE_JSON_HEX_KEYS = {
    "hex",
    "data",
    "data.data",
    "payload",
    "packet",
    "bytes",
    "usb.capdata",
    "usbhid.data",
}
CAPTURE_TRANSPORT_META_KEYS = {
    "frame.number": "frame_number",
    "frame.time_relative": "frame_time_relative",
    "frame.time_delta": "frame_time_delta",
    "frame.time_delta_displayed": "frame_time_delta_displayed",
    "frame.time_epoch": "frame_time_epoch",
    "usb.bus_id": "usb_bus",
    "usb.device_address": "usb_device_address",
    "usb.endpoint_address": "usb_endpoint_address",
    "usb.endpoint_number": "usb_endpoint_number",
    "usb.endpoint_direction": "usb_endpoint_direction",
    "usb.transfer_type": "usb_transfer_type",
    "usb.irp_info": "usb_irp_info",
    "usb.idVendor": "usb_vendor_id",
    "usb.idProduct": "usb_product_id",
    "usb.src": "usb_source",
    "usb.dst": "usb_destination",
}
CAPTURE_SUMMARY_SUFFIXES = {
    ".cap",
    ".hex",
    ".json",
    ".jsonl",
    ".log",
    ".pcap",
    ".pcapng",
    ".txt",
    ".tsv",
}
TINYUZ_CTRL_LITERAL_LINE = 1
TINYUZ_CTRL_CLIP_END = 2
TINYUZ_CTRL_STREAM_END = 3
TINYUZ_MIN_LITERAL_LINE = 15
TINYUZ_MIN_DICT_MATCH = 2
TINYUZ_BIG_POS_FOR_LEN = (1 << 11) + (1 << 9) + (1 << 7) - 1
LINUX_INTERFACE_CONTRACT_SCHEMA_VERSION = "lianli-linux-interface-contract/v1"
LINUX_CONTROL_MANIFEST_SCHEMA_VERSION = "lianli-linux-control-manifest/v1"
LINUX_CONTROL_PREFLIGHT_SCHEMA_VERSION = "lianli-linux-control-preflight/v1"
LINUX_CONTROL_ACTION_PLAN_SCHEMA_VERSION = "lianli-linux-control-action-plan/v1"
LINUX_CONTROL_WRITE_GATE_SCHEMA_VERSION = "lianli-linux-control-write-gate/v1"
LINUX_CONTROL_TARGET_REGISTRY_SCHEMA_VERSION = "lianli-linux-control-target-registry/v1"
LINUX_CONTROL_PACKET_PREVIEW_SCHEMA_VERSION = "lianli-linux-control-packet-preview/v1"
LINUX_CONTROL_PACKET_COMPARE_SCHEMA_VERSION = "lianli-linux-control-packet-compare/v1"
LINUX_WRITE_CONFIRM_TOKEN = "WRITE-LIANLI"


@dataclass(frozen=True)
class RfChunk:
    packet_index: int
    sequence: int
    channel: int
    rx_type: int
    data: bytes
    raw: bytes


@dataclass(frozen=True)
class RfFrame:
    first_packet_index: int
    channel: int
    rx_type: int
    chunks: tuple[RfChunk, ...]
    payload: bytes


class _TinyUzDecodeError(ValueError):
    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class _TinyUzBitReader:
    def __init__(self, data: bytes, cursor: int) -> None:
        self._data = data
        self.cursor = cursor
        self.types = 0
        self.type_count = 0

    def read_bit(self) -> int:
        return self.read_lowbits(1) & 1

    def read_lowbits(self, bit_count: int) -> int:
        if bit_count <= 0:
            return 0
        if bit_count > 8:
            raise _TinyUzDecodeError("invalid-code", "TinyUZ bit request is larger than one type byte")
        if self.type_count >= bit_count:
            mask = (1 << bit_count) - 1
            result = self.types & mask
            self.types >>= bit_count
            self.type_count -= bit_count
            return result
        result = self.types
        existing_count = self.type_count
        if self.cursor >= len(self._data):
            raise _TinyUzDecodeError("truncated-literal", "TinyUZ stream ended while reading type bits")
        type_byte = self._data[self.cursor]
        self.cursor += 1
        remaining_count = bit_count - existing_count
        result |= (type_byte & ((1 << remaining_count) - 1)) << existing_count
        self.types = type_byte >> remaining_count
        self.type_count = 8 - remaining_count
        return result

    def read_byte(self, *, status: str, reason: str) -> int:
        if self.cursor >= len(self._data):
            raise _TinyUzDecodeError(status, reason)
        byte = self._data[self.cursor]
        self.cursor += 1
        return byte

    def reset_types(self) -> None:
        self.types = 0
        self.type_count = 0


def analyze_capture_file(path: Path) -> dict[str, Any]:
    return analyze_capture_packets(load_capture_packets(path), source=str(path))


def compare_capture_file(
    path: Path,
    expected_packets: Iterable[bytes],
    *,
    expected_source: str = "expected",
) -> dict[str, Any]:
    return compare_capture_packets(
        load_capture_packets(path),
        expected_packets,
        source=str(path),
        expected_source=expected_source,
    )


def capture_replay_plan_file(path: Path) -> dict[str, Any]:
    return capture_replay_plan_from_analysis(analyze_capture_file(path))


def capture_protocol_report_file(path: Path) -> dict[str, Any]:
    source = str(path)
    items = _capture_transport_items(path)
    packets = [bytes(item["packet"]) for item in items if isinstance(item.get("packet"), (bytes, bytearray))]
    analysis = analyze_capture_packets(packets, source=source)
    return capture_protocol_report_from_analysis(
        analysis,
        packet_metadata=_timeline_packet_metadata(items),
    )


def capture_timeline_report_file(path: Path) -> dict[str, Any]:
    source = str(path)
    items = _capture_transport_items(path)
    packets = [bytes(item["packet"]) for item in items if isinstance(item.get("packet"), (bytes, bytearray))]
    analysis = analyze_capture_packets(packets, source=source)
    return capture_timeline_report_from_analysis(
        analysis,
        packet_metadata=_timeline_packet_metadata(items),
    )


def capture_transport_report_file(path: Path) -> dict[str, Any]:
    return capture_transport_report_items(_capture_transport_items(path), source=str(path))


def capture_triage_report_file(
    path: Path,
    *,
    led_count: int = 12,
    rainbow_frames: int = 3,
    interval_ms: int = 40,
    effect_index: int = 1,
) -> dict[str, Any]:
    source = str(path)
    items = _capture_transport_items(path)
    packets = [bytes(item["packet"]) for item in items if isinstance(item.get("packet"), (bytes, bytearray))]
    packet_metadata = _timeline_packet_metadata(items)
    analysis = analyze_capture_packets(packets, source=source)
    signature_match = capture_signature_match_packets(
        packets,
        source=source,
        led_count=led_count,
        rainbow_frames=rainbow_frames,
        interval_ms=interval_ms,
        effect_index=effect_index,
    )
    replay_plan = capture_replay_plan_from_analysis(analysis)
    protocol_report = capture_protocol_report_from_analysis(analysis, packet_metadata=packet_metadata)
    transport_report = capture_transport_report_file(path)
    recommended_commands = _capture_triage_recommended_commands(
        source,
        signature_match,
        replay_plan,
        protocol_report,
        transport_report,
    )
    live_write_targets = _capture_triage_live_write_targets(protocol_report)
    return {
        "operation": "capture-triage-report",
        "source": source,
        "status": _capture_triage_status(signature_match, replay_plan, protocol_report, transport_report),
        "summary": _capture_triage_summary(
            analysis,
            signature_match,
            replay_plan,
            protocol_report,
            transport_report,
        ),
        "recommended_commands": recommended_commands,
        "next_steps": _capture_triage_next_steps(signature_match, replay_plan, protocol_report, transport_report),
        "linux_live_write_targets": live_write_targets,
        "transport": _capture_triage_transport_summary(transport_report),
        "signature_match": _capture_triage_signature_summary(signature_match),
        "protocol": protocol_report,
        "replay": _capture_triage_replay_summary(replay_plan),
    }


def _capture_triage_status(
    signature_match: dict[str, Any],
    replay_plan: dict[str, Any],
    protocol_report: dict[str, Any],
    transport_report: dict[str, Any],
) -> str:
    if int(signature_match.get("matched_signature_count", 0) or 0) > 0:
        return "protocol-signature-match"
    if int(replay_plan.get("replay_hint_count", 0) or 0) > 0:
        return "rf-replay-hints"
    if int(protocol_report.get("rf_frame_count", 0) or 0) > 0:
        return "decoded-rf-frames"
    if int(protocol_report.get("receiver_snapshot_count", 0) or 0) > 0:
        return "receiver-snapshots"
    if int(transport_report.get("protocol_candidate_count", 0) or 0) > 0:
        return "transport-protocol-candidates"
    if int(transport_report.get("packet_candidate_count", 0) or 0) > 0:
        return "payload-sized-candidates"
    return "no-known-l-wireless-payloads"


def _capture_triage_summary(
    analysis: dict[str, Any],
    signature_match: dict[str, Any],
    replay_plan: dict[str, Any],
    protocol_report: dict[str, Any],
    transport_report: dict[str, Any],
) -> dict[str, Any]:
    analysis_summary = analysis.get("summary", {}) if isinstance(analysis.get("summary"), dict) else {}
    protocol_summary = protocol_report.get("summary", {}) if isinstance(protocol_report.get("summary"), dict) else {}
    return {
        "packet_count": int(analysis.get("packet_count", 0) or 0),
        "rf_frame_count": int(analysis.get("rf_frame_count", 0) or 0),
        "receiver_snapshot_count": int(protocol_report.get("receiver_snapshot_count", 0) or 0),
        "master_response_count": int(protocol_report.get("master_response_count", 0) or 0),
        "replay_hint_count": int(replay_plan.get("replay_hint_count", 0) or 0),
        "packet_candidate_count": int(transport_report.get("packet_candidate_count", 0) or 0),
        "protocol_candidate_count": int(transport_report.get("protocol_candidate_count", 0) or 0),
        "matched_signature_count": int(signature_match.get("matched_signature_count", 0) or 0),
        "matched_operations": list(signature_match.get("matched_operations", [])),
        "decoded_kinds": dict(sorted(_counter_from_mapping(analysis_summary.get("kinds")).items())),
        "rf_operations": dict(sorted(_counter_from_mapping(analysis_summary.get("rf_operations")).items())),
        "rf_frame_operations": dict(sorted(_counter_from_mapping(analysis_summary.get("rf_frame_operations")).items())),
        "receiver_macs": sorted(_strings_from_list(analysis_summary.get("receiver_macs"))),
        "master_macs": sorted(_strings_from_list(analysis_summary.get("master_macs"))),
        "device_count": int(protocol_summary.get("device_count", 0) or 0),
        "operation_count": int(protocol_summary.get("operation_count", 0) or 0),
        "linux_live_write_target_count": int(protocol_summary.get("linux_live_write_target_count", 0) or 0),
    }


def _capture_triage_transport_summary(transport_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "packet_candidate_count": int(transport_report.get("packet_candidate_count", 0) or 0),
        "protocol_candidate_count": int(transport_report.get("protocol_candidate_count", 0) or 0),
        "field_counts": dict(transport_report.get("field_counts", {}) if isinstance(transport_report.get("field_counts"), dict) else {}),
        "size_counts": dict(transport_report.get("size_counts", {}) if isinstance(transport_report.get("size_counts"), dict) else {}),
        "kind_counts": dict(transport_report.get("kind_counts", {}) if isinstance(transport_report.get("kind_counts"), dict) else {}),
        "usb_device_counts": dict(transport_report.get("usb_device_counts", {}) if isinstance(transport_report.get("usb_device_counts"), dict) else {}),
        "usb_endpoint_counts": dict(transport_report.get("usb_endpoint_counts", {}) if isinstance(transport_report.get("usb_endpoint_counts"), dict) else {}),
        "known_usb_devices": list(transport_report.get("known_usb_devices", []) if isinstance(transport_report.get("known_usb_devices"), list) else []),
        "lianli_usb_targets": dict(transport_report.get("lianli_usb_targets", {}) if isinstance(transport_report.get("lianli_usb_targets"), dict) else {}),
        "first_protocol_candidates": _capture_triage_transport_candidates(transport_report),
        "notes": list(transport_report.get("notes", []) if isinstance(transport_report.get("notes"), list) else []),
    }


def _capture_triage_transport_candidates(transport_report: dict[str, Any]) -> list[dict[str, Any]]:
    items = transport_report.get("first_protocol_candidates")
    if not isinstance(items, list):
        return []
    keys = (
        "index",
        "field",
        "frame_number",
        "endpoint_address",
        "transfer_type",
        "usb_bus",
        "usb_device_address",
        "usb_endpoint_address",
        "usb_endpoint_number",
        "usb_endpoint_direction",
        "usb_transfer_type",
        "usb_vendor_id",
        "usb_product_id",
        "usb_source",
        "usb_destination",
        "size",
        "kind",
        "channel",
        "rx_type",
        "mac",
        "master_mac",
        "motherboard_pwm",
        "device_count",
        "receiver_macs",
        "data_prefix",
    )
    result: list[dict[str, Any]] = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        summary = {key: item[key] for key in keys if key in item}
        if summary:
            result.append(summary)
    return result


def _capture_triage_live_write_targets(protocol_report: dict[str, Any]) -> list[dict[str, Any]]:
    targets = protocol_report.get("linux_live_write_targets")
    if not isinstance(targets, list):
        return []
    result: list[dict[str, Any]] = []
    for target in targets[:8]:
        if not isinstance(target, dict):
            continue
        result.append(
            {
                key: target[key]
                for key in (
                    "operation",
                    "vid_pid",
                    "role",
                    "label",
                    "bus",
                    "device_address",
                    "endpoint_key",
                    "write_endpoint",
                    "read_endpoint",
                    "transfer_type",
                    "packet_count",
                    "confidence",
                    "target_macs",
                    "pwm_values",
                    "effect_indexes",
                    "led_counts",
                    "frame_counts",
                    "interval_ms_values",
                    "rgb_sequence_frame_counts",
                    "rgb_static_colors",
                    "rgb_rainbow_generated",
                    "channels",
                    "rx_types",
                    "outer_rx_types",
                    "payload_rx_types",
                    "payload_channels",
                    "master_macs",
                    "runtime_contexts",
                    "linux_hint",
                )
                if key in target
            }
        )
    return result


def _capture_triage_signature_summary(signature_match: dict[str, Any]) -> dict[str, Any]:
    matched_items = [
        _capture_triage_signature_item(item)
        for item in signature_match.get("items", [])
        if isinstance(item, dict) and item.get("matched")
    ]
    return {
        "signature_count": int(signature_match.get("signature_count", 0) or 0),
        "matched_signature_count": int(signature_match.get("matched_signature_count", 0) or 0),
        "matched_operations": list(signature_match.get("matched_operations", [])),
        "matched_commands": list(signature_match.get("matched_commands", [])),
        "items": matched_items,
    }


def _capture_triage_signature_item(item: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "operation": item.get("operation"),
        "score": item.get("score"),
        "exact_match": item.get("exact_match"),
        "semantic_match": item.get("semantic_match"),
        "shape_match": item.get("shape_match"),
        "commands": _capture_triage_item_commands(item),
    }
    shape = item.get("shape")
    if isinstance(shape, dict):
        matches = shape.get("matches")
        result["shape"] = {
            "match_count": len(matches) if isinstance(matches, list) else 0,
            "matched_operations": sorted(_strings_from_list(shape.get("matched_operations"))),
        }
        if isinstance(matches, list) and matches:
            result["shape"]["first_match"] = matches[0]
    return result


def _capture_triage_item_commands(item: dict[str, Any]) -> dict[str, list[str]]:
    observed = item.get("observed_commands")
    commands = item.get("commands")
    result: dict[str, list[str]] = {}
    if isinstance(observed, dict):
        for key in ("dry_run_commands", "compare_capture_commands"):
            values = [str(value) for value in observed.get(key, []) if isinstance(value, str)]
            if values:
                result[key] = _unique_preserve_order(values)
    if isinstance(commands, dict):
        for key in ("dry_run", "compare_capture"):
            command = _command_payload(commands.get(key))
            if command is not None:
                result.setdefault(f"{key}_commands", [])
                result[f"{key}_commands"].append(str(command["command"]))
    return {key: _unique_preserve_order(values) for key, values in result.items()}


def _capture_triage_replay_summary(replay_plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "rf_frame_count": int(replay_plan.get("rf_frame_count", 0) or 0),
        "replay_hint_count": int(replay_plan.get("replay_hint_count", 0) or 0),
        "operation_counts": dict(replay_plan.get("operation_counts", {}) if isinstance(replay_plan.get("operation_counts"), dict) else {}),
        "dry_run_commands": list(replay_plan.get("dry_run_commands", [])),
        "compare_capture_commands": list(replay_plan.get("compare_capture_commands", [])),
        "notes": list(replay_plan.get("notes", []) if isinstance(replay_plan.get("notes"), list) else []),
        "items": list(replay_plan.get("items", []) if isinstance(replay_plan.get("items"), list) else [])[:12],
    }


def _capture_triage_live_write_commands(protocol_report: dict[str, Any]) -> list[str]:
    targets = protocol_report.get("linux_live_write_targets")
    if not isinstance(targets, list) or not targets:
        return []
    commands = [
        _tool_command("validate-readonly", "--output-dir", ".cache/lianli/validation-live")
    ]
    for target in targets:
        if not isinstance(target, dict):
            continue
        if target.get("confidence") != "high" or target.get("role") != "sender":
            continue
        macs = target.get("target_macs")
        mac = str(macs[0]) if isinstance(macs, list) and macs else ""
        if not mac:
            continue
        operation = str(target.get("operation") or "")
        if operation == "live-pwm-mirror":
            commands.append(
                _tool_command(
                    "safe-pwm-mirror-experiment",
                    "--mac",
                    mac,
                    "--output-dir",
                    ".cache/lianli/pwm-mirror-experiment",
                    "--confirm",
                    "WRITE-LIANLI",
                )
            )
        else:
            commands.append(
                _tool_command(
                    "safe-pwm-experiment",
                    "--mac",
                    mac,
                    "--pwm",
                    "120",
                    "--output-dir",
                    ".cache/lianli/pwm-experiment",
                    "--confirm",
                    "WRITE-LIANLI",
                )
            )
    return _unique_preserve_order(commands)


def _capture_triage_recommended_commands(
    source: str,
    signature_match: dict[str, Any],
    replay_plan: dict[str, Any],
    protocol_report: dict[str, Any],
    transport_report: dict[str, Any],
) -> list[str]:
    commands: list[str] = []
    commands.extend(str(command) for command in signature_match.get("matched_commands", []) if isinstance(command, str))
    commands.extend(str(command) for command in replay_plan.get("compare_capture_commands", []) if isinstance(command, str))
    commands.extend(str(command) for command in replay_plan.get("dry_run_commands", []) if isinstance(command, str))
    params = signature_match.get("parameters") if isinstance(signature_match.get("parameters"), dict) else {}
    if source:
        commands.extend(
            [
                _tool_command(
                    "capture-signature-match",
                    source,
                    "--led-count",
                    str(params.get("led_count", 12)),
                    "--rainbow-frames",
                    str(params.get("rainbow_frames", 3)),
                    "--interval-ms",
                    str(params.get("interval_ms", 40)),
                ),
                _tool_command("capture-protocol-report", source),
                _tool_command("capture-timeline-report", source),
                _tool_command("capture-replay-plan", source),
                _tool_command("capture-transport-report", source),
                _tool_command("analyze-capture", source),
            ]
        )
    commands.extend(str(command) for command in transport_report.get("recommended_commands", []) if isinstance(command, str))
    commands.extend(_capture_triage_live_write_commands(protocol_report))
    return _unique_preserve_order(commands)


def _capture_triage_next_steps(
    signature_match: dict[str, Any],
    replay_plan: dict[str, Any],
    protocol_report: dict[str, Any],
    transport_report: dict[str, Any],
) -> list[str]:
    matched_count = int(signature_match.get("matched_signature_count", 0) or 0)
    replay_count = int(replay_plan.get("replay_hint_count", 0) or 0)
    rf_count = int(protocol_report.get("rf_frame_count", 0) or 0)
    protocol_candidates = int(transport_report.get("protocol_candidate_count", 0) or 0)
    packet_candidates = int(transport_report.get("packet_candidate_count", 0) or 0)
    live_targets = _capture_triage_live_write_targets(protocol_report)
    high_confidence_target = next(
        (
            target
            for target in live_targets
            if target.get("confidence") == "high" and target.get("role") == "sender"
        ),
        None,
    )
    if high_confidence_target:
        macs = high_confidence_target.get("target_macs")
        mac_text = f" for {', '.join(macs)}" if isinstance(macs, list) and macs else ""
        return [
            (
                "High-confidence Linux RF sender target found"
                f"{mac_text}: {high_confidence_target.get('vid_pid')} "
                f"{high_confidence_target.get('write_endpoint')} OUT."
            ),
            "Run validate-readonly on Linux before any write, then use the guarded safe experiment command from recommended_commands.",
            "Keep the Windows USBPcap trace and compare it with capture-replay-plan before changing PWM/RGB values.",
        ]
    if matched_count:
        return [
            "Run the matched compare-capture commands first; they use capture-derived MAC/PWM/RGB parameters when available.",
            "Open capture-protocol-report for the device/operation table and capture-replay-plan for per-frame argv details.",
        ]
    if replay_count:
        return [
            "No local catalog signature matched, but decoded RF replay hints exist; inspect replay.compare_capture_commands.",
            "Add a new protocol signature if the decoded operation is confirmed against official L-Connect behavior.",
        ]
    if rf_count:
        return [
            "Decoded RF frames exist without replay hints; inspect protocol.operations for unknown frame shapes.",
            "Compare the payload prefixes against protocol-signatures output and extend the decoder if needed.",
        ]
    if protocol_candidates:
        return [
            "Transport-level L-Wireless candidates exist; run analyze-capture and verify tshark exported usb.capdata/usbhid.data.",
            "If RF reassembly is empty, confirm the capture includes the 0416:8040 sender interface during the actual UI action.",
        ]
    if packet_candidates:
        return [
            "Payload-sized records exist but do not match known L-Wireless shapes.",
            "Recapture with USBPcap on the LIAN LI 0416:8040/0416:8041 devices and export usb.capdata/usbhid.data fields.",
        ]
    return [
        "No supported L-Wireless payloads were found.",
        "Capture a short Windows USBPcap trace around one L-Connect action, then rerun capture-triage-report.",
    ]


def capture_signature_match_file(
    path: Path,
    *,
    led_count: int = 12,
    rainbow_frames: int = 3,
    interval_ms: int = 40,
    effect_index: int = 1,
) -> dict[str, Any]:
    return capture_signature_match_packets(
        load_capture_packets(path),
        source=str(path),
        led_count=led_count,
        rainbow_frames=rainbow_frames,
        interval_ms=interval_ms,
        effect_index=effect_index,
    )


def capture_signature_match_packets(
    packets: Iterable[bytes],
    *,
    source: str = "",
    led_count: int = 12,
    rainbow_frames: int = 3,
    interval_ms: int = 40,
    effect_index: int = 1,
) -> dict[str, Any]:
    observed_packets = list(packets)
    observed_analysis = analyze_capture_packets(observed_packets, source=source)
    catalog = protocol_signature_catalog(
        led_count=led_count,
        rainbow_frames=rainbow_frames,
        interval_ms=interval_ms,
        effect_index=effect_index,
    )
    normalized_packets = _normalized_capture_packets(observed_packets)
    observed_packet_hashes = Counter(
        hashlib.sha256(packet).hexdigest()
        for _index, packet in normalized_packets
    )
    items: list[dict[str, Any]] = []
    for signature in catalog["items"]:
        match = _capture_signature_match_item(
            signature,
            observed_analysis,
            normalized_packets,
            observed_packet_hashes,
            source=source,
        )
        items.append(match)

    matched_items = [item for item in items if item["matched"]]
    return {
        "operation": "capture-signature-match",
        "source": source,
        "parameters": catalog["parameters"],
        "packet_count": len(observed_packets),
        "normalized_packet_count": len(normalized_packets),
        "rf_frame_count": observed_analysis["rf_frame_count"],
        "observed_summary": observed_analysis["summary"],
        "signature_count": len(items),
        "matched_signature_count": len(matched_items),
        "matched_operations": [str(item["operation"]) for item in matched_items],
        "matched_commands": _capture_signature_matched_commands(matched_items),
        "recommended_commands": [
            _tool_command("capture-transport-report", source or "<capture>"),
            _tool_command("capture-replay-plan", source or "<capture>"),
            _tool_command("capture-protocol-report", source or "<capture>"),
            _tool_command("capture-timeline-report", source or "<capture>"),
            _tool_command("protocol-signatures", "--led-count", str(catalog["parameters"]["led_count"])),
        ],
        "items": items,
    }


def protocol_signature_catalog(
    *,
    led_count: int = 12,
    rainbow_frames: int = 3,
    interval_ms: int = 40,
    effect_index: int = 1,
) -> dict[str, Any]:
    resolved_led_count = max(1, min(255, int(led_count)))
    resolved_rainbow_frames = max(1, int(rainbow_frames))
    resolved_interval_ms = max(0, min(65535, int(interval_ms)))
    resolved_effect_index = int(effect_index)
    backend = LianLiWirelessBackend()
    target = _protocol_catalog_target(bound=True)
    unbound_target = _protocol_catalog_target(bound=False)
    target_args = _protocol_catalog_target_args(target)
    current_pwm_args = ["--current-pwm", _pwm_values_arg(list(target.pwm_values))]
    rgb_common_args = [
        *target_args,
        "--led-count",
        str(resolved_led_count),
        "--effect-index",
        str(resolved_effect_index),
    ]
    rainbow_common_args = [
        *target_args,
        "--frame-count",
        str(resolved_rainbow_frames),
        "--interval-ms",
        str(resolved_interval_ms),
        "--led-count",
        str(resolved_led_count),
        "--effect-index",
        str(resolved_effect_index),
    ]
    items = [
        _protocol_signature_item(
            "receiver-list-request",
            [build_wireless_list_request(1)],
            usb_role="receiver",
            commands={
                "dry_run": _command_detail(_tool_argv("scan")),
                "analyze_capture": _command_detail(_tool_argv("analyze-capture", "<capture>")),
            },
            notes=[
                "This is the 64-byte 0416:8041 list request that precedes receiver snapshots.",
                "It is not an RF frame, so compare-capture is not applicable.",
            ],
        ),
        _protocol_signature_item(
            "master-query-request",
            [build_master_query_request(target.channel)],
            usb_role="sender",
            commands={
                "dry_run": _command_detail(_tool_argv("dry-run-master-query", "--channel", str(target.channel))),
                "analyze_capture": _command_detail(_tool_argv("analyze-capture", "<capture>")),
            },
            notes=[
                "This 64-byte 0416:8040 query is used to discover the active master MAC for a channel.",
                "It is not an RF frame, so compare-capture is not applicable.",
            ],
        ),
        _protocol_signature_item(
            "pwm",
            backend.build_pwm_packets(target, [120]),
            expected_operation="pwm",
            commands={
                "dry_run": _command_detail(_tool_argv("dry-run-pwm", *target_args, "--pwm", "120")),
                "compare_capture": _command_detail(_tool_argv("compare-capture", "<capture>", "pwm", *target_args, "--pwm", "120")),
            },
        ),
        _protocol_signature_item(
            "pwm-sync-enable",
            backend.build_motherboard_pwm_sync_packets(target, enable=True),
            expected_operation="pwm-sync",
            commands={
                "dry_run": _command_detail(_tool_argv("dry-run-pwm-sync", *target_args)),
                "compare_capture": _command_detail(_tool_argv("compare-capture", "<capture>", "pwm-sync", *target_args)),
            },
            notes=["The decoded RF payload has PWM tuple [6,6,6,6], which is the current motherboard-sync signature."],
        ),
        _protocol_signature_item(
            "pwm-sync-disable",
            backend.build_motherboard_pwm_sync_packets(target, enable=False, fallback_pwm=100),
            expected_operation="pwm-sync",
            commands={
                "dry_run": _command_detail(_tool_argv("dry-run-pwm-sync", *target_args, "--disable", "--fallback-pwm", "100")),
                "compare_capture": _command_detail(
                    _tool_argv("compare-capture", "<capture>", "pwm-sync", *target_args, "--disable", "--fallback-pwm", "100")
                ),
            },
            notes=["Disable falls back to a direct PWM tuple and therefore decodes as live-pwm, not live-pwm-sync."],
        ),
        _protocol_signature_item(
            "pwm-mirror",
            backend.build_motherboard_pwm_mirror_packets(target, 127),
            expected_operation="pwm-mirror",
            commands={
                "dry_run": _command_detail(_tool_argv("dry-run-pwm-mirror", *target_args, "--motherboard-pwm", "127")),
                "compare_capture": _command_detail(
                    _tool_argv("compare-capture", "<capture>", "pwm-mirror", *target_args, "--motherboard-pwm", "127")
                ),
            },
            notes=["A capture can also infer this operation when a preceding receiver snapshot exposes motherboard_pwm=127."],
        ),
        _protocol_signature_item(
            "bind",
            backend.build_bind_packets(
                unbound_target,
                master_mac=target.master_mac,
                rx_type=target.rx_type,
                channel=target.channel,
            ),
            expected_operation="bind",
            device=unbound_target,
            commands={
                "dry_run": _command_detail(
                    _tool_argv("dry-run-bind", *target_args, *current_pwm_args)
                ),
                "compare_capture": _command_detail(
                    _tool_argv("compare-capture", "<capture>", "bind", *target_args, *current_pwm_args)
                ),
            },
            notes=["The receiver context is unbound, but the emitted command includes the desired master MAC and rx_type."],
        ),
        _protocol_signature_item(
            "unbind",
            backend.build_unbind_packets(target, channel=target.channel),
            expected_operation="unbind",
            commands={
                "dry_run": _command_detail(_tool_argv("dry-run-unbind", *target_args, *current_pwm_args)),
                "compare_capture": _command_detail(_tool_argv("compare-capture", "<capture>", "unbind", *target_args, *current_pwm_args)),
            },
        ),
        _protocol_signature_item(
            "rgb-static-red",
            backend.build_static_rgb_packets(
                target,
                (255, 0, 0),
                effect_index=resolved_effect_index,
                led_count=resolved_led_count,
            ),
            expected_operation="rgb",
            commands={
                "dry_run": _command_detail(_tool_argv("dry-run-rgb", *rgb_common_args, "--color", "255,0,0")),
                "compare_capture": _command_detail(_tool_argv("compare-capture", "<capture>", "rgb", *rgb_common_args, "--color", "255,0,0")),
            },
        ),
        _protocol_signature_item(
            "rgb-off",
            backend.build_static_rgb_packets(
                target,
                (0, 0, 0),
                effect_index=resolved_effect_index,
                led_count=resolved_led_count,
            ),
            expected_operation="rgb",
            commands={
                "dry_run": _command_detail(_tool_argv("dry-run-rgb", *rgb_common_args, "--color", "0,0,0")),
                "compare_capture": _command_detail(_tool_argv("compare-capture", "<capture>", "rgb", *rgb_common_args, "--color", "0,0,0")),
            },
        ),
        _protocol_signature_item(
            "rainbow",
            backend.build_rainbow_rgb_packets(
                target,
                frame_count=resolved_rainbow_frames,
                interval_ms=resolved_interval_ms,
                effect_index=resolved_effect_index,
                led_count=resolved_led_count,
            ),
            expected_operation="rainbow",
            commands={
                "dry_run": _command_detail(_tool_argv("dry-run-rainbow", *rainbow_common_args)),
                "compare_capture": _command_detail(_tool_argv("compare-capture", "<capture>", "rainbow", *rainbow_common_args)),
            },
        ),
    ]
    return {
        "operation": "protocol-signatures",
        "parameters": {
            "led_count": resolved_led_count,
            "rainbow_frames": resolved_rainbow_frames,
            "interval_ms": resolved_interval_ms,
            "effect_index": resolved_effect_index,
        },
        "target": _device_payload(target),
        "usb_targets": {
            "sender": _usb_target_payload("sender"),
            "receiver": _usb_target_payload("receiver"),
        },
        "summary": {
            "signature_count": len(items),
            "operations": [str(item["operation"]) for item in items],
            "packet_count": sum(int(item["packet_count"]) for item in items),
            "rf_operation_counts": dict(
                sorted(
                    Counter(
                        operation
                        for item in items
                        for operation, count in _counter_from_mapping(item["summary"].get("rf_operations")).items()
                        for _ in range(count)
                    ).items()
                )
            ),
        },
        "dry_run_commands": _unique_preserve_order(
            command["command"]
            for item in items
            for command in [item.get("commands", {}).get("dry_run")]
            if isinstance(command, dict)
        ),
        "compare_capture_commands": _unique_preserve_order(
            command["command"]
            for item in items
            for command in [item.get("commands", {}).get("compare_capture")]
            if isinstance(command, dict)
        ),
        "items": items,
    }


def summarize_capture_dir(path: Path) -> dict[str, Any]:
    root = path.expanduser()
    files = _capture_summary_files(root)
    results: list[dict[str, Any]] = []
    aggregate_kinds: Counter[str] = Counter()
    aggregate_operations: Counter[str] = Counter()
    aggregate_frame_operations: Counter[str] = Counter()
    receiver_macs: set[str] = set()
    master_macs: set[str] = set()
    error_count = 0

    for file_path in files:
        try:
            analysis = analyze_capture_file(file_path)
            item = _capture_summary_item(root, file_path, analysis)
            summary = analysis.get("summary", {}) if isinstance(analysis.get("summary"), dict) else {}
            aggregate_kinds.update(_counter_from_mapping(summary.get("kinds")))
            aggregate_operations.update(_counter_from_mapping(summary.get("rf_operations")))
            aggregate_frame_operations.update(_counter_from_mapping(summary.get("rf_frame_operations")))
            receiver_macs.update(_strings_from_list(summary.get("receiver_macs")))
            master_macs.update(_strings_from_list(summary.get("master_macs")))
        except Exception as error:
            error_count += 1
            item = {
                "path": _capture_summary_display_path(root, file_path),
                "size": _safe_file_size(file_path),
                "error": str(error),
                "candidate_score": 0,
                "recommended_commands": [
                    _tool_command("analyze-capture", str(file_path)),
                ],
            }
        results.append(item)

    candidates = [
        item
        for item in results
        if int(item.get("candidate_score", 0)) > 0 or item.get("replay_hint_count", 0)
    ]
    top_candidates = sorted(
        candidates,
        key=lambda item: (
            -int(item.get("candidate_score", 0)),
            str(item.get("path", "")),
        ),
    )
    return {
        "operation": "summarize-captures",
        "path": str(root),
        "is_directory": root.is_dir(),
        "file_count": len(files),
        "analyzed_file_count": len(results) - error_count,
        "error_count": error_count,
        "candidate_count": len(candidates),
        "top_candidates": top_candidates[:10],
        "files": results,
        "summary": {
            "kinds": dict(sorted(aggregate_kinds.items())),
            "rf_operations": dict(sorted(aggregate_operations.items())),
            "rf_frame_operations": dict(sorted(aggregate_frame_operations.items())),
            "receiver_macs": sorted(receiver_macs),
            "master_macs": sorted(master_macs),
        },
    }


def capture_set_report(
    path: Path,
    *,
    version: str = "2.1.17",
    capture_base: str | None = None,
    experiment_dir: Path | None = None,
    led_count: int = 12,
    rainbow_frames: int = 3,
    interval_ms: int = 40,
    effect_index: int = 1,
) -> dict[str, Any]:
    root = path.expanduser()
    base = capture_base or f"l-connect-v{version}"
    scenarios = _windows_capture_scenarios(base)
    capture_files = _capture_summary_files(root) if root.exists() else []
    triage_cache: dict[Path, dict[str, Any]] = {}
    scenario_reports: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    aggregate_operations: Counter[str] = Counter()
    aggregate_matched_signatures: Counter[str] = Counter()
    aggregate_live_targets: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    aggregate_snapshot_devices: dict[str, dict[str, Any]] = {}
    sender_seen_count = 0
    receiver_seen_count = 0

    for scenario in scenarios:
        scenario_report = _capture_set_scenario_report(
            root,
            capture_files,
            scenario,
            triage_cache,
            led_count=led_count,
            rainbow_frames=rainbow_frames,
            interval_ms=interval_ms,
            effect_index=effect_index,
        )
        scenario_reports.append(scenario_report)
        status_counts[str(scenario_report["status"])] += 1
        if scenario_report.get("found"):
            triage = scenario_report.get("triage")
            if isinstance(triage, dict):
                summary = triage.get("summary", {}) if isinstance(triage.get("summary"), dict) else {}
                aggregate_operations.update(_counter_from_mapping(summary.get("rf_operations")))
                signature = (
                    triage.get("signature_match", {})
                    if isinstance(triage.get("signature_match"), dict)
                    else {}
                )
                aggregate_matched_signatures.update(
                    str(item)
                    for item in signature.get("matched_operations", [])
                    if isinstance(item, str)
                )
                transport = triage.get("transport", {}) if isinstance(triage.get("transport"), dict) else {}
                targets = transport.get("lianli_usb_targets", {})
                if isinstance(targets, dict):
                    sender_seen_count += 1 if targets.get("sender_seen") else 0
                    receiver_seen_count += 1 if targets.get("receiver_seen") else 0
                protocol = triage.get("protocol", {}) if isinstance(triage.get("protocol"), dict) else {}
                _capture_set_update_snapshot_devices(
                    aggregate_snapshot_devices,
                    protocol.get("devices"),
                    scenario_id=str(scenario_report.get("id") or ""),
                    scenario_path=str(scenario_report.get("path") or ""),
                )
                _capture_set_update_live_targets(
                    aggregate_live_targets,
                    scenario_id=str(scenario_report.get("id") or ""),
                    scenario_path=str(scenario_report.get("path") or ""),
                    targets=triage.get("linux_live_write_targets", []),
                )

    found_count = sum(1 for item in scenario_reports if item.get("found"))
    evidence_found_count = status_counts.get("evidence-found", 0)
    partial_count = status_counts.get("partial-evidence", 0)
    error_count = status_counts.get("analysis-error", 0)
    overall_status = _capture_set_overall_status(
        scenario_count=len(scenarios),
        found_count=found_count,
        evidence_found_count=evidence_found_count,
        partial_count=partial_count,
        error_count=error_count,
    )
    live_write_targets = _capture_set_live_target_list(aggregate_live_targets)
    _capture_set_enrich_live_targets_with_snapshot_devices(live_write_targets, aggregate_snapshot_devices)
    experiment_summary = _capture_set_experiment_summary(experiment_dir)
    hardware_validation = _capture_set_hardware_validation(experiment_summary)
    linux_control_matrix = _capture_set_linux_control_matrix(
        live_write_targets,
        scenario_reports,
        experiment_summary,
    )
    cross_scenario_deltas = _capture_set_cross_scenario_deltas(scenario_reports)
    return {
        "operation": "capture-set-report",
        "path": str(root),
        "version": version,
        "capture_base": base,
        "experiment_dir": str(experiment_dir.expanduser()) if experiment_dir is not None else "",
        "status": overall_status,
        "scenario_count": len(scenarios),
        "found_capture_count": found_count,
        "evidence_found_count": evidence_found_count,
        "partial_evidence_count": partial_count,
        "error_count": error_count,
        "status_counts": dict(sorted(status_counts.items())),
        "sender_seen_count": sender_seen_count,
        "receiver_seen_count": receiver_seen_count,
        "aggregate_rf_operations": dict(sorted(aggregate_operations.items())),
        "aggregate_matched_signatures": dict(sorted(aggregate_matched_signatures.items())),
        "cross_scenario_deltas": cross_scenario_deltas,
        "linux_live_write_targets": live_write_targets,
        "hardware_validation": hardware_validation,
        "experiment_summary": experiment_summary,
        "linux_control_matrix": linux_control_matrix,
        "linux_interface_contract": _capture_set_linux_interface_contract(
            live_write_targets,
            linux_control_matrix,
            cross_scenario_deltas,
        ),
        "linux_validation_plan": _capture_set_linux_validation_plan(
            live_write_targets,
            scenario_reports,
            experiment_summary,
        ),
        "recommended_commands": _capture_set_recommended_commands(root, base, scenario_reports),
        "scenarios": scenario_reports,
    }


def capture_gap_report(
    path: Path,
    *,
    version: str = "2.1.17",
    capture_base: str | None = None,
    experiment_dir: Path | None = None,
    led_count: int = 12,
    rainbow_frames: int = 3,
    interval_ms: int = 40,
    effect_index: int = 1,
) -> dict[str, Any]:
    capture_report = capture_set_report(
        path,
        version=version,
        capture_base=capture_base,
        experiment_dir=experiment_dir,
        led_count=led_count,
        rainbow_frames=rainbow_frames,
        interval_ms=interval_ms,
        effect_index=effect_index,
    )
    scenarios = capture_report.get("scenarios")
    matrix = capture_report.get("linux_control_matrix")
    scenario_gaps = _capture_gap_scenario_items(scenarios if isinstance(scenarios, list) else [])
    operation_gaps = _capture_gap_operation_items(matrix if isinstance(matrix, list) else [])
    next_capture = scenario_gaps[0] if scenario_gaps else {}
    status = _capture_gap_status(capture_report, scenario_gaps, operation_gaps)
    return {
        "operation": "capture-gap-report",
        "path": str(path.expanduser()),
        "version": version,
        "capture_base": str(capture_report.get("capture_base") or capture_base or f"l-connect-v{version}"),
        "experiment_dir": str(experiment_dir.expanduser()) if experiment_dir is not None else "",
        "status": status,
        "source_capture_set_status": str(capture_report.get("status") or ""),
        "scenario_count": int(capture_report.get("scenario_count") or 0),
        "found_capture_count": int(capture_report.get("found_capture_count") or 0),
        "evidence_found_count": int(capture_report.get("evidence_found_count") or 0),
        "missing_capture_count": int(
            (capture_report.get("status_counts") or {}).get("missing-capture", 0)
            if isinstance(capture_report.get("status_counts"), dict)
            else 0
        ),
        "partial_evidence_count": int(capture_report.get("partial_evidence_count") or 0),
        "error_count": int(capture_report.get("error_count") or 0),
        "sender_seen_count": int(capture_report.get("sender_seen_count") or 0),
        "receiver_seen_count": int(capture_report.get("receiver_seen_count") or 0),
        "next_capture": next_capture,
        "scenario_gaps": scenario_gaps,
        "operation_gaps": operation_gaps,
        "proof_gates": _capture_gap_proof_gates(capture_report, scenario_gaps, operation_gaps),
        "recommended_commands": _capture_gap_recommended_commands(
            path.expanduser(),
            str(capture_report.get("capture_base") or capture_base or f"l-connect-v{version}"),
            next_capture,
            scenario_gaps,
            operation_gaps,
        ),
    }


def linux_interface_contract_report(
    path: Path,
    *,
    version: str = "2.1.17",
    capture_base: str | None = None,
    experiment_dir: Path | None = None,
    led_count: int = 12,
    rainbow_frames: int = 3,
    interval_ms: int = 40,
    effect_index: int = 1,
) -> dict[str, Any]:
    capture_report = capture_set_report(
        path,
        version=version,
        capture_base=capture_base,
        experiment_dir=experiment_dir,
        led_count=led_count,
        rainbow_frames=rainbow_frames,
        interval_ms=interval_ms,
        effect_index=effect_index,
    )
    contract = capture_report.get("linux_interface_contract")
    contract_payload = dict(contract) if isinstance(contract, dict) else {}
    result = {
        **contract_payload,
        "operation": "linux-interface-contract",
        "schema_version": LINUX_INTERFACE_CONTRACT_SCHEMA_VERSION,
        "path": str(capture_report.get("path") or ""),
        "version": str(capture_report.get("version") or version),
        "capture_base": str(capture_report.get("capture_base") or ""),
        "experiment_dir": str(capture_report.get("experiment_dir") or ""),
        "source_capture_set_status": str(capture_report.get("status") or ""),
        "source": _linux_interface_contract_source_payload(capture_report),
        "protocol_delta_summary": _capture_set_protocol_delta_summary(
            capture_report.get("cross_scenario_deltas")
        ),
        "hardware_validation": capture_report.get("hardware_validation", {}),
        "control_matrix_summary": _linux_interface_contract_matrix_summary(capture_report),
        "recommended_commands": _linux_interface_contract_recommended_commands(capture_report),
    }
    return result


def linux_control_manifest_report(
    path: Path,
    *,
    version: str = "2.1.17",
    capture_base: str | None = None,
    experiment_dir: Path | None = None,
    led_count: int = 12,
    rainbow_frames: int = 3,
    interval_ms: int = 40,
    effect_index: int = 1,
) -> dict[str, Any]:
    contract = linux_interface_contract_report(
        path,
        version=version,
        capture_base=capture_base,
        experiment_dir=experiment_dir,
        led_count=led_count,
        rainbow_frames=rainbow_frames,
        interval_ms=interval_ms,
        effect_index=effect_index,
    )
    operation_contracts = contract.get("operation_contracts")
    operations = _linux_control_manifest_operations(operation_contracts)
    return {
        "operation": "linux-control-manifest",
        "schema_version": LINUX_CONTROL_MANIFEST_SCHEMA_VERSION,
        "contract_schema_version": str(contract.get("schema_version") or ""),
        "status": str(contract.get("status") or ""),
        "path": str(contract.get("path") or ""),
        "version": str(contract.get("version") or version),
        "capture_base": str(contract.get("capture_base") or ""),
        "experiment_dir": str(contract.get("experiment_dir") or ""),
        "source_capture_set_status": str(contract.get("source_capture_set_status") or ""),
        "source": contract.get("source", {}),
        "protocol_delta_summary": contract.get("protocol_delta_summary", {}),
        "transport": contract.get("transport", {}),
        "python_entrypoints": contract.get("python_entrypoints", {}),
        "linux_permissions": _linux_control_manifest_permissions(),
        "safety_gates": _linux_control_manifest_safety_gates(),
        "operations": operations,
        "operation_map": {str(item.get("operation") or ""): item for item in operations},
        "validated_operations": list(contract.get("validated_operations", []) if isinstance(contract.get("validated_operations"), list) else []),
        "ready_operations": list(contract.get("ready_operations", []) if isinstance(contract.get("ready_operations"), list) else []),
        "target_macs": _linux_control_manifest_target_macs(operations),
        "hardware_validation": contract.get("hardware_validation", {}),
        "control_matrix_summary": contract.get("control_matrix_summary", []),
        "recommended_commands": list(contract.get("recommended_commands", []) if isinstance(contract.get("recommended_commands"), list) else []),
    }


def linux_control_preflight_report(
    path: Path,
    *,
    sys_root: Path = Path("/sys"),
    dev_root: Path = Path("/dev"),
    version: str = "2.1.17",
    capture_base: str | None = None,
    experiment_dir: Path | None = None,
    led_count: int = 12,
    rainbow_frames: int = 3,
    interval_ms: int = 40,
    effect_index: int = 1,
) -> dict[str, Any]:
    manifest = linux_control_manifest_report(
        path,
        version=version,
        capture_base=capture_base,
        experiment_dir=experiment_dir,
        led_count=led_count,
        rainbow_frames=rainbow_frames,
        interval_ms=interval_ms,
        effect_index=effect_index,
    )
    readiness = usb_capture_readiness(sys_root=sys_root)
    device_access = _linux_control_preflight_device_access(readiness, dev_root.expanduser())
    permission_status = _linux_control_preflight_permission_status(device_access)
    operations = _linux_control_preflight_operations(manifest, device_access)
    blockers = _linux_control_preflight_blockers(readiness, permission_status, operations)
    return {
        "operation": "linux-control-preflight",
        "schema_version": LINUX_CONTROL_PREFLIGHT_SCHEMA_VERSION,
        "manifest_schema_version": str(manifest.get("schema_version") or ""),
        "status": _linux_control_preflight_status(readiness, permission_status, operations),
        "path": str(manifest.get("path") or ""),
        "version": str(manifest.get("version") or version),
        "capture_base": str(manifest.get("capture_base") or ""),
        "experiment_dir": str(manifest.get("experiment_dir") or ""),
        "sys_root": str(sys_root.expanduser()),
        "dev_root": str(dev_root.expanduser()),
        "led_count": int(led_count),
        "rainbow_frames": int(rainbow_frames),
        "interval_ms": int(interval_ms),
        "effect_index": int(effect_index),
        "hardware_status": str(readiness.get("status") or ""),
        "permission_status": permission_status,
        "blockers": blockers,
        "device_access": device_access,
        "operations": operations,
        "ready_operations": [
            str(item.get("operation") or "")
            for item in operations
            if item.get("preflight_status") in {"ready", "ready-for-readonly-validation"}
        ],
        "manifest": manifest,
        "protocol_delta_summary": manifest.get("protocol_delta_summary", {}),
        "usb_readiness": readiness,
        "recommended_commands": _linux_control_preflight_commands(
            manifest,
            readiness,
            permission_status,
        ),
    }


def linux_control_action_plan_report(
    path: Path,
    *,
    sys_root: Path = Path("/sys"),
    dev_root: Path = Path("/dev"),
    version: str = "2.1.17",
    capture_base: str | None = None,
    experiment_dir: Path | None = None,
    led_count: int = 12,
    rainbow_frames: int = 3,
    interval_ms: int = 40,
    effect_index: int = 1,
) -> dict[str, Any]:
    preflight = linux_control_preflight_report(
        path,
        sys_root=sys_root,
        dev_root=dev_root,
        version=version,
        capture_base=capture_base,
        experiment_dir=experiment_dir,
        led_count=led_count,
        rainbow_frames=rainbow_frames,
        interval_ms=interval_ms,
        effect_index=effect_index,
    )
    experiment_summary = _capture_set_experiment_summary(experiment_dir)
    actions = _linux_control_action_plan_actions(preflight, experiment_summary)
    return {
        "operation": "linux-control-action-plan",
        "schema_version": LINUX_CONTROL_ACTION_PLAN_SCHEMA_VERSION,
        "preflight_schema_version": str(preflight.get("schema_version") or ""),
        "status": _linux_control_action_plan_status(preflight, actions),
        "path": str(preflight.get("path") or ""),
        "version": str(preflight.get("version") or version),
        "capture_base": str(preflight.get("capture_base") or ""),
        "experiment_dir": str(preflight.get("experiment_dir") or ""),
        "hardware_status": str(preflight.get("hardware_status") or ""),
        "permission_status": str(preflight.get("permission_status") or ""),
        "blockers": list(preflight.get("blockers", []) if isinstance(preflight.get("blockers"), list) else []),
        "action_count": len(actions),
        "ready_action_count": sum(1 for item in actions if item.get("status") == "ready"),
        "guarded_write_readiness": _linux_control_action_plan_guarded_write_readiness(actions),
        "actions": actions,
        "commands": _linux_control_action_plan_commands(actions),
        "next_commands": _linux_control_action_plan_next_commands(actions),
        "packet_compare_validation": _linux_control_action_plan_packet_compare_validation(experiment_summary),
        "live_snapshot_context": _linux_control_action_plan_live_snapshot_context(experiment_summary),
        "protocol_delta_summary": preflight.get("protocol_delta_summary", {}),
        "preflight": preflight,
    }


def linux_control_write_gate_report(
    path: Path,
    *,
    sys_root: Path = Path("/sys"),
    dev_root: Path = Path("/dev"),
    version: str = "2.1.17",
    capture_base: str | None = None,
    experiment_dir: Path | None = None,
    led_count: int = 12,
    rainbow_frames: int = 3,
    interval_ms: int = 40,
    effect_index: int = 1,
) -> dict[str, Any]:
    action_plan = linux_control_action_plan_report(
        path,
        sys_root=sys_root,
        dev_root=dev_root,
        version=version,
        capture_base=capture_base,
        experiment_dir=experiment_dir,
        led_count=led_count,
        rainbow_frames=rainbow_frames,
        interval_ms=interval_ms,
        effect_index=effect_index,
    )
    actions = [
        item
        for item in action_plan.get("actions", [])
        if isinstance(item, dict)
    ]
    action_gates = _linux_control_write_gate_actions(actions)
    ready_actions = [
        gate
        for gate in action_gates
        if gate.get("ready_for_guarded_write")
    ]
    blocked_actions = [
        gate
        for gate in action_gates
        if not gate.get("ready_for_guarded_write")
    ]
    return {
        "operation": "linux-control-write-gate",
        "schema_version": LINUX_CONTROL_WRITE_GATE_SCHEMA_VERSION,
        "action_plan_schema_version": str(action_plan.get("schema_version") or ""),
        "status": _linux_control_write_gate_status(action_plan, action_gates),
        "allows_any_guarded_write": bool(ready_actions),
        "write_confirmation_token": LINUX_WRITE_CONFIRM_TOKEN,
        "path": str(action_plan.get("path") or ""),
        "version": str(action_plan.get("version") or version),
        "capture_base": str(action_plan.get("capture_base") or ""),
        "experiment_dir": str(action_plan.get("experiment_dir") or ""),
        "hardware_status": str(action_plan.get("hardware_status") or ""),
        "permission_status": str(action_plan.get("permission_status") or ""),
        "blockers": list(action_plan.get("blockers", []) if isinstance(action_plan.get("blockers"), list) else []),
        "guarded_write_readiness": action_plan.get("guarded_write_readiness", {}),
        "packet_compare_validation": action_plan.get("packet_compare_validation", {}),
        "live_snapshot_context": action_plan.get("live_snapshot_context", {}),
        "ready_action_count": len(ready_actions),
        "blocked_action_count": len(blocked_actions),
        "ready_action_ids": [str(gate.get("id") or "") for gate in ready_actions],
        "blocked_action_ids": [str(gate.get("id") or "") for gate in blocked_actions],
        "next_command": _linux_control_write_gate_next_command(action_gates, action_plan),
        "actions": action_gates,
        "action_plan": action_plan,
    }


def _linux_control_write_gate_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for action in actions:
        if action.get("phase") != "safe-experiment" or not action.get("writes_usb"):
            continue
        validation = action.get("pre_write_validation") if isinstance(action.get("pre_write_validation"), dict) else {}
        execution = action.get("execution") if isinstance(action.get("execution"), dict) else {}
        observed_results = [
            item
            for item in validation.get("observed_results", [])
            if isinstance(item, dict)
        ] if isinstance(validation.get("observed_results"), list) else []
        target_state = _linux_control_write_gate_target_state_summary(observed_results)
        ready = bool(execution.get("write_command_enabled"))
        gates.append(
            {
                "id": str(action.get("id") or ""),
                "operation": str(action.get("operation") or ""),
                "capability": str(action.get("capability") or ""),
                "status": str(action.get("status") or ""),
                "preflight_status": str(action.get("preflight_status") or ""),
                "ready_for_guarded_write": ready,
                "allows_guarded_write": bool(validation.get("allows_guarded_write")),
                "write_command_enabled": ready,
                "write_command": str(execution.get("write_command") or ""),
                "next_command": str(execution.get("next_command") or action.get("command") or ""),
                "required_before_write": list(
                    execution.get("required_before_write", [])
                    if isinstance(execution.get("required_before_write"), list)
                    else []
                ),
                "validation_status": str(validation.get("validation_status") or ""),
                "execution_status": str(execution.get("status") or ""),
                "blocker": _linux_control_write_gate_action_blocker(action, validation, execution),
                "confirmation_token": str(action.get("confirmation_token") or ""),
                "requires_confirmation": bool(action.get("requires_confirmation")),
                "visual_confirmation_required": bool(action.get("visual_confirmation_required")),
                "pairing_recovery_required": bool(action.get("pairing_recovery_required")),
                "source_capture_coverage": validation.get("source_capture_coverage", {}),
                "compare_command_count": len(
                    validation.get("compare_commands", [])
                    if isinstance(validation.get("compare_commands"), list)
                    else []
                ),
                "observed_compare_count": len(observed_results),
                "passed_compare_count": sum(
                    1
                    for item in observed_results
                    if _linux_control_action_plan_compare_result_passes_gate(item)
                ),
                "target_state": target_state,
                "preview_commands": list(
                    validation.get("preview_commands", [])
                    if isinstance(validation.get("preview_commands"), list)
                    else []
                ),
                "compare_commands": list(
                    validation.get("compare_commands", [])
                    if isinstance(validation.get("compare_commands"), list)
                    else []
                ),
                "observed_results": observed_results,
            }
        )
    return gates


def _linux_control_write_gate_target_state_summary(observed_results: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = _unique_preserve_order(
        [
            str(item.get("target_state_status") or "")
            for item in observed_results
            if str(item.get("target_state_status") or "")
        ]
    )
    missing_fields = _unique_preserve_order(
        [
            field
            for item in observed_results
            for field in _ordered_strings_from_list(item.get("target_state_missing_packet_fields"))
        ]
    )
    placeholder_fields = _unique_preserve_order(
        [
            field
            for item in observed_results
            for field in _ordered_strings_from_list(item.get("target_state_placeholder_fields"))
        ]
    )
    return {
        "status": statuses[0] if len(statuses) == 1 else ("mixed" if statuses else "missing"),
        "statuses": statuses,
        "missing_packet_fields": missing_fields,
        "placeholder_fields": placeholder_fields,
        "snapshot_metadata_available": any(
            bool(item.get("target_state_snapshot_metadata_available")) for item in observed_results
        ),
        "snapshot_state_available": any(
            bool(item.get("target_state_snapshot_state_available")) for item in observed_results
        ),
        "raw_hex_available": any(
            bool(item.get("target_state_raw_hex_available")) for item in observed_results
        ),
        "live_snapshot_refresh_required": any(
            bool(item.get("target_state_live_snapshot_refresh_required")) for item in observed_results
        ),
    }


def _linux_control_write_gate_action_blocker(
    action: dict[str, Any],
    validation: dict[str, Any],
    execution: dict[str, Any],
) -> str:
    if execution.get("write_command_enabled"):
        return ""
    validation_status = str(validation.get("validation_status") or "")
    if validation_status == "needs-run":
        return "run packet preview/compare before WRITE-LIANLI"
    if validation_status == "refresh-live-snapshot":
        return "run live-list, save the refreshed receiver snapshot, then recompare"
    if validation_status == "needs-recompare-after-refresh":
        return "re-run packet compare with refreshed live-list state"
    if validation_status == "incomplete":
        return "not all source captures have passing exact packet compares"
    if validation_status == "invalid-schema":
        return "packet compare artifact schema is stale or invalid"
    if validation_status == "failed":
        return "packet compare failed"
    if str(action.get("preflight_status") or "") == "needs-capture-evidence":
        return "capture official L-Connect evidence first"
    return str(action.get("reason") or "guarded write is not ready")


def _linux_control_write_gate_status(action_plan: dict[str, Any], action_gates: list[dict[str, Any]]) -> str:
    if any(gate.get("ready_for_guarded_write") for gate in action_gates):
        return "write-enabled"
    if action_plan.get("blockers"):
        return "blocked-by-preflight"
    validation_statuses = {
        str(gate.get("validation_status") or "")
        for gate in action_gates
    }
    if "refresh-live-snapshot" in validation_statuses:
        return "refresh-live-snapshot"
    if "needs-recompare-after-refresh" in validation_statuses:
        return "needs-recompare-after-refresh"
    if "needs-run" in validation_statuses:
        return "needs-packet-compare"
    if "incomplete" in validation_statuses:
        return "incomplete-packet-compare"
    if "failed" in validation_statuses:
        return "packet-compare-failed"
    if "invalid-schema" in validation_statuses:
        return "invalid-packet-compare-schema"
    if not action_gates and any(
        isinstance(action, dict) and action.get("phase") == "capture-evidence"
        for action in action_plan.get("actions", [])
        if isinstance(action, dict)
    ):
        return "needs-capture-evidence"
    return str(action_plan.get("guarded_write_readiness", {}).get("status") or action_plan.get("status") or "not-ready")


def _linux_control_write_gate_next_command(
    action_gates: list[dict[str, Any]],
    action_plan: dict[str, Any],
) -> str:
    for gate in action_gates:
        if not gate.get("ready_for_guarded_write") and str(gate.get("next_command") or ""):
            return str(gate.get("next_command") or "")
    for gate in action_gates:
        if gate.get("ready_for_guarded_write") and str(gate.get("write_command") or ""):
            return str(gate.get("write_command") or "")
    next_commands = action_plan.get("next_commands")
    if isinstance(next_commands, list) and next_commands:
        return str(next_commands[0])
    return ""


def linux_control_target_registry_report(
    path: Path,
    *,
    sys_root: Path = Path("/sys"),
    dev_root: Path = Path("/dev"),
    version: str = "2.1.17",
    capture_base: str | None = None,
    experiment_dir: Path | None = None,
    led_count: int = 12,
    rainbow_frames: int = 3,
    interval_ms: int = 40,
    effect_index: int = 1,
) -> dict[str, Any]:
    action_plan = linux_control_action_plan_report(
        path,
        sys_root=sys_root,
        dev_root=dev_root,
        version=version,
        capture_base=capture_base,
        experiment_dir=experiment_dir,
        led_count=led_count,
        rainbow_frames=rainbow_frames,
        interval_ms=interval_ms,
        effect_index=effect_index,
    )
    targets = _linux_control_target_registry_targets(action_plan)
    return {
        "operation": "linux-control-target-registry",
        "schema_version": LINUX_CONTROL_TARGET_REGISTRY_SCHEMA_VERSION,
        "action_plan_schema_version": str(action_plan.get("schema_version") or ""),
        "status": _linux_control_target_registry_status(action_plan, targets),
        "path": str(action_plan.get("path") or ""),
        "version": str(action_plan.get("version") or version),
        "capture_base": str(action_plan.get("capture_base") or ""),
        "experiment_dir": str(action_plan.get("experiment_dir") or ""),
        "target_count": len(targets),
        "complete_target_count": sum(1 for item in targets if item.get("runtime_context_status") == "complete"),
        "packet_build_ready_count": sum(1 for item in targets if item.get("packet_build_ready")),
        "targets": targets,
        "target_map": {str(item.get("id") or ""): item for item in targets},
        "action_plan": action_plan,
    }


def linux_control_packet_preview_report(
    path: Path,
    *,
    control_operation: str = "live-pwm",
    target_id: str = "",
    sys_root: Path = Path("/sys"),
    dev_root: Path = Path("/dev"),
    version: str = "2.1.17",
    capture_base: str | None = None,
    experiment_dir: Path | None = None,
    pwm_values: Iterable[int] | None = None,
    motherboard_pwm: int = 120,
    color: tuple[int, int, int] | None = None,
    led_count: int | None = None,
    frame_count: int | None = None,
    interval_ms: int | None = None,
    effect_index: int | None = None,
) -> dict[str, Any]:
    registry_led_count = led_count if led_count is not None else 12
    registry_frame_count = frame_count if frame_count is not None else 3
    registry_interval_ms = interval_ms if interval_ms is not None else 40
    registry_effect_index = effect_index if effect_index is not None else 1
    registry = linux_control_target_registry_report(
        path,
        sys_root=sys_root,
        dev_root=dev_root,
        version=version,
        capture_base=capture_base,
        experiment_dir=experiment_dir,
        led_count=registry_led_count,
        rainbow_frames=registry_frame_count,
        interval_ms=registry_interval_ms,
        effect_index=registry_effect_index,
    )
    target = _linux_control_packet_preview_target(registry, target_id)
    if target is None:
        return {
            "operation": "linux-control-packet-preview",
            "schema_version": LINUX_CONTROL_PACKET_PREVIEW_SCHEMA_VERSION,
            "status": "no-target",
            "control_operation": control_operation,
            "target_id": target_id,
            "available_target_ids": [
                str(item.get("id") or "")
                for item in registry.get("targets", [])
                if isinstance(item, dict)
            ],
            "registry": registry,
        }
    effective_pwm_values = _linux_control_packet_preview_pwm_values(
        target,
        control_operation,
        pwm_values,
    )
    rgb_parameters = _linux_control_packet_preview_rgb_parameters(
        target,
        control_operation,
        requested_color=color,
        requested_led_count=led_count,
        requested_frame_count=frame_count,
        requested_interval_ms=interval_ms,
        requested_effect_index=effect_index,
    )
    packets = _linux_control_packet_preview_packets(
        target,
        control_operation,
        pwm_values=effective_pwm_values,
        motherboard_pwm=motherboard_pwm,
        color=rgb_parameters["color"],
        led_count=rgb_parameters["led_count"],
        frame_count=rgb_parameters["frame_count"],
        interval_ms=rgb_parameters["interval_ms"],
        effect_index=rgb_parameters["effect_index"],
    )
    return {
        "operation": "linux-control-packet-preview",
        "schema_version": LINUX_CONTROL_PACKET_PREVIEW_SCHEMA_VERSION,
        "target_registry_schema_version": str(registry.get("schema_version") or ""),
        "status": "packet-preview-ready",
        "control_operation": control_operation,
        "target_id": str(target.get("id") or ""),
        "target": {
            key: target[key]
            for key in (
                "id",
                "mac",
                "channel",
                "rx_type",
                "master_mac",
                "runtime_context_status",
                "packet_build_ready",
                "requires_live_snapshot_before_write",
                "missing_packet_fields",
            )
            if key in target
        },
        "target_state": _linux_control_packet_preview_target_state(target),
        "parameters": {
            "pwm_values": list(effective_pwm_values),
            "pwm_values_source": _linux_control_packet_preview_pwm_source(
                target,
                control_operation,
                pwm_values,
            ),
            "motherboard_pwm": motherboard_pwm,
            "color": list(rgb_parameters["color"]),
            "color_source": rgb_parameters["color_source"],
            "led_count": rgb_parameters["led_count"],
            "led_count_source": rgb_parameters["led_count_source"],
            "frame_count": rgb_parameters["frame_count"],
            "frame_count_source": rgb_parameters["frame_count_source"],
            "interval_ms": rgb_parameters["interval_ms"],
            "interval_ms_source": rgb_parameters["interval_ms_source"],
            "effect_index": rgb_parameters["effect_index"],
            "effect_index_source": rgb_parameters["effect_index_source"],
        },
        "packet_preview": _linux_control_packet_preview_summary(packets),
        "registry": registry,
    }


def linux_control_packet_compare_report(
    path: Path,
    observed_capture: Path,
    *,
    control_operation: str = "live-pwm",
    target_id: str = "",
    sys_root: Path = Path("/sys"),
    dev_root: Path = Path("/dev"),
    version: str = "2.1.17",
    capture_base: str | None = None,
    experiment_dir: Path | None = None,
    pwm_values: Iterable[int] | None = None,
    motherboard_pwm: int = 120,
    color: tuple[int, int, int] | None = None,
    led_count: int | None = None,
    frame_count: int | None = None,
    interval_ms: int | None = None,
    effect_index: int | None = None,
) -> dict[str, Any]:
    preview = linux_control_packet_preview_report(
        path,
        control_operation=control_operation,
        target_id=target_id,
        sys_root=sys_root,
        dev_root=dev_root,
        version=version,
        capture_base=capture_base,
        experiment_dir=experiment_dir,
        pwm_values=pwm_values,
        motherboard_pwm=motherboard_pwm,
        color=color,
        led_count=led_count,
        frame_count=frame_count,
        interval_ms=interval_ms,
        effect_index=effect_index,
    )
    if preview.get("status") != "packet-preview-ready":
        return {
            "operation": "linux-control-packet-compare",
            "schema_version": LINUX_CONTROL_PACKET_COMPARE_SCHEMA_VERSION,
            "packet_preview_schema_version": str(preview.get("schema_version") or ""),
            "status": str(preview.get("status") or "packet-preview-unavailable"),
            "observed_capture": str(observed_capture),
            "control_operation": control_operation,
            "target_id": target_id,
            "write_gate": _linux_control_packet_compare_write_gate(preview, None),
            "preview": preview,
        }
    expected_packets = _linux_control_packet_compare_expected_packets(preview)
    if not expected_packets:
        return {
            "operation": "linux-control-packet-compare",
            "schema_version": LINUX_CONTROL_PACKET_COMPARE_SCHEMA_VERSION,
            "packet_preview_schema_version": str(preview.get("schema_version") or ""),
            "status": "no-expected-packets",
            "observed_capture": str(observed_capture),
            "control_operation": control_operation,
            "target_id": str(preview.get("target_id") or target_id),
            "write_gate": _linux_control_packet_compare_write_gate(preview, None),
            "preview": preview,
        }
    comparison = compare_capture_file(
        observed_capture,
        expected_packets,
        expected_source=f"linux-control-packet-preview:{control_operation}",
    )
    return {
        "operation": "linux-control-packet-compare",
        "schema_version": LINUX_CONTROL_PACKET_COMPARE_SCHEMA_VERSION,
        "packet_preview_schema_version": str(preview.get("schema_version") or ""),
        "compare_capture_operation": comparison.get("operation"),
        "status": "matched" if comparison.get("matched") else "mismatch",
        "matched": bool(comparison.get("matched")),
        "exact_match": bool(comparison.get("exact_match")),
        "semantic_match": bool(comparison.get("semantic_match")),
        "observed_capture": str(observed_capture),
        "control_operation": control_operation,
        "target_id": str(preview.get("target_id") or target_id),
        "target": preview.get("target", {}),
        "target_state": preview.get("target_state", {}),
        "parameters": preview.get("parameters", {}),
        "expected_packet_count": len(expected_packets),
        "packet_preview": preview.get("packet_preview", {}),
        "match_diagnostics": comparison.get("diagnostics", {}),
        "write_gate": _linux_control_packet_compare_write_gate(preview, comparison),
        "comparison": comparison,
    }


def _linux_control_packet_compare_write_gate(
    preview: dict[str, Any],
    comparison: dict[str, Any] | None,
) -> dict[str, Any]:
    target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
    requires_live_snapshot = bool(target.get("requires_live_snapshot_before_write"))
    if not isinstance(comparison, dict):
        return {
            "status": "blocked",
            "allows_guarded_write": False,
            "minimum_required_match": "exact-match",
            "comparison_status": "unavailable",
            "requires_live_snapshot_before_write": requires_live_snapshot,
            "reason": "Packet preview or expected packets are unavailable.",
            "required_before_write": [
                "Resolve packet preview errors.",
                "Run linux-control-packet-compare again and require exact-match.",
            ],
        }
    diagnostics = comparison.get("diagnostics") if isinstance(comparison.get("diagnostics"), dict) else {}
    comparison_status = str(diagnostics.get("status") or "")
    if bool(comparison.get("exact_match")):
        return {
            "status": "pass",
            "allows_guarded_write": True,
            "minimum_required_match": "exact-match",
            "comparison_status": comparison_status or "exact-match",
            "requires_live_snapshot_before_write": requires_live_snapshot,
            "reason": "Official capture matches the packet preview exactly.",
            "required_before_write": [],
        }
    if bool(comparison.get("semantic_match")):
        required = ["Run linux-control-packet-compare again and require exact-match."]
        status = "review-required"
        reason = "Official capture matches semantically but not byte-for-byte."
        if requires_live_snapshot:
            status = "refresh-live-snapshot"
            reason = (
                "Official capture matches semantically but byte differences remain; "
                "refresh receiver state before any guarded write."
            )
            required.insert(0, "Run live-list to refresh command_sequence and raw receiver state.")
        return {
            "status": status,
            "allows_guarded_write": False,
            "minimum_required_match": "exact-match",
            "comparison_status": comparison_status or "semantic-match-exact-mismatch",
            "requires_live_snapshot_before_write": requires_live_snapshot,
            "reason": reason,
            "required_before_write": required,
        }
    return {
        "status": "fail",
        "allows_guarded_write": False,
        "minimum_required_match": "exact-match",
        "comparison_status": comparison_status or "semantic-mismatch",
        "requires_live_snapshot_before_write": requires_live_snapshot,
        "reason": "Official capture does not match the packet preview semantically.",
        "required_before_write": [
            "Inspect match_diagnostics.nearest_differences.",
            "Fix target context or parameters before any WRITE-LIANLI operation.",
        ],
    }


def windows_capture_plan(
    *,
    version: str = "2.1.17",
    installer: Path | None = None,
    capture_base: str | None = None,
    environment: str = "auto",
) -> dict[str, Any]:
    base = capture_base or f"l-connect-v{version}"
    installer_path = installer.expanduser() if installer is not None else None
    tools = _local_windows_capture_tools()
    return {
        "operation": "windows-capture-plan",
        "version": version,
        "recommended_environment": "vm-usb-passthrough",
        "requested_environment": environment,
        "local_tools": tools,
        "installer": _capture_installer_payload(installer_path),
        "usb_targets": [
            {
                "vid_pid": f"{vendor_id:04x}:{product_id:04x}",
                "label": label,
                "purpose": _usb_target_purpose(vendor_id, product_id),
            }
            for (vendor_id, product_id), label in sorted(KNOWN_USB_DEVICES.items())
        ],
        "environment_matrix": _windows_capture_environment_matrix(tools),
        "host_setup": _windows_capture_host_setup_commands(base),
        "scenarios": _windows_capture_scenarios(base),
        "post_capture": {
            "preferred_linux_flow": [
                f"python tools/lianli_wireless_probe.py analyze-capture {base}-00-baseline.pcapng",
                f"python tools/lianli_wireless_probe.py capture-replay-plan {base}-00-baseline.pcapng",
                f"python tools/lianli_wireless_probe.py capture-protocol-report {base}-00-baseline.pcapng",
                f"python tools/lianli_wireless_probe.py capture-timeline-report {base}-00-baseline.pcapng",
            ],
            "manual_export_if_tshark_missing": (
                f"tshark -r {base}-00-baseline.pcapng -T fields -E separator=\\t "
                f"-E occurrence=a -e usb.capdata -e usbhid.data -e data.data > {base}-00-baseline-hex.txt"
            ),
            "after_export": [
                f"python tools/lianli_wireless_probe.py analyze-capture {base}-00-baseline-hex.txt",
                f"python tools/lianli_wireless_probe.py capture-replay-plan {base}-00-baseline-hex.txt",
                f"python tools/lianli_wireless_probe.py capture-protocol-report {base}-00-baseline-hex.txt",
                f"python tools/lianli_wireless_probe.py capture-timeline-report {base}-00-baseline-hex.txt",
            ],
        },
        "decision_rules": [
            "If analyze-capture reports only unknown/enumeration traffic, recapture the RF sender 0416:8040 interface rather than only the receiver.",
            "If replay hints appear for live-bind/live-unbind/live-pwm-sync, use the emitted compare-capture command before implementing any Linux live write.",
            "If Wine runs the GUI but no USB RF frames appear, treat it only as an installation/static extraction path, not protocol proof.",
            "Docker is useful for running this analyzer on exported captures; it is not a substitute for Windows USBPcap or VM USB passthrough.",
        ],
    }


def usb_capture_readiness(*, sys_root: Path = Path("/sys")) -> dict[str, Any]:
    root = sys_root.expanduser()
    devices = scan_known_usb_devices(root)
    by_vid_pid: dict[str, list[dict[str, Any]]] = {}
    for device in devices:
        by_vid_pid.setdefault(device.vid_pid, []).append(_usb_device_payload(device))

    sender_key = f"{RF_SENDER_VID:04x}:{RF_SENDER_PID:04x}"
    receiver_key = f"{RF_RECEIVER_VID:04x}:{RF_RECEIVER_PID:04x}"
    sender_present = bool(by_vid_pid.get(sender_key))
    receiver_present = bool(by_vid_pid.get(receiver_key))
    tools = _local_windows_capture_tools()
    status, blockers = _usb_readiness_status(sender_present, receiver_present, tools)

    return {
        "operation": "usb-capture-readiness",
        "sys_root": str(root),
        "status": status,
        "known_device_count": len(devices),
        "present_vid_pids": sorted(by_vid_pid),
        "missing_l_wireless_vid_pids": [
            key
            for key in (sender_key, receiver_key)
            if key not in by_vid_pid
        ],
        "blockers": blockers,
        "tools": {
            name: tools[name]
            for name in ("tshark", "usbipd", "qemu", "virtualbox", "wine", "docker", "podman")
            if name in tools
        },
        "usbmon": _usbmon_payload(root),
        "targets": [
            {
                "vid_pid": vid_pid,
                "label": label,
                "purpose": _usb_target_purpose(vendor_id, product_id),
                "present": vid_pid in by_vid_pid,
                "device_count": len(by_vid_pid.get(vid_pid, [])),
                "devices": by_vid_pid.get(vid_pid, []),
                "capture_priority": _usb_capture_priority(vendor_id, product_id),
            }
            for (vendor_id, product_id), label in sorted(KNOWN_USB_DEVICES.items())
            for vid_pid in (f"{vendor_id:04x}:{product_id:04x}",)
        ],
        "linux_live_commands": _usb_linux_live_commands(sender_present, receiver_present),
        "windows_capture_commands": [
            _tool_command("windows-capture-plan", "--version", "2.1.17", "--capture-base", "lianli-v2117"),
            _tool_command("summarize-captures", "./lianli-v2117-captures"),
        ],
        "udev_rules": list(UDEV_RULES),
        "decision_rules": [
            "0416:8040 is the RF command sender; prioritize it for USBPcap when validating writes.",
            "0416:8041 is the receiver/snapshot path; it proves enumeration and read-only state but not all write opcodes by itself.",
            "If both sender and receiver are present on Linux, run validate-readonly before any live write experiment.",
            "If no L-Wireless devices are present, continue with static/capture analysis only; Linux live control cannot be validated yet.",
        ],
    }


def capture_transport_report_items(
    items: Iterable[dict[str, Any]],
    *,
    source: str = "",
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    field_counts: Counter[str] = Counter()
    size_counts: Counter[str] = Counter()
    kind_counts: Counter[str] = Counter()
    protocol_items: list[dict[str, Any]] = []

    for index, item in enumerate(items):
        packet = item.get("packet")
        if not isinstance(packet, (bytes, bytearray)):
            continue
        packet_bytes = bytes(packet)
        field = str(item.get("field") or item.get("source") or "unknown")
        field_counts[field] += 1
        size_counts[str(len(packet_bytes))] += 1
        record = {
            key: value
            for key, value in item.items()
            if key != "packet" and value not in ("", None)
        }
        record.update(
            {
                "index": index,
                "field": field,
                "size": len(packet_bytes),
                "hex_prefix": packet_bytes[:12].hex(),
            }
        )
        try:
            normalized = normalize_lianli_packet(packet_bytes)
            classified = classify_lianli_packet(normalized)
        except LianLiWirelessError as error:
            record["kind"] = "unknown"
            record["error"] = str(error)
        else:
            record["kind"] = str(classified.get("kind", "unknown"))
            _attach_transport_classification(record, classified)
        kind_counts[str(record["kind"])] += 1
        if record["kind"] != "unknown":
            protocol_items.append(record)
        records.append(record)

    protocol_count = len(protocol_items)
    notes: list[str] = []
    if not records:
        notes.append("No 64/65-byte HID payloads or receiver snapshots were found in this capture/export.")
    elif not protocol_items:
        notes.append("Payload-sized records were found, but none matched known L-Wireless RF/snapshot/master-query shapes.")
        notes.append("For official L-Connect proof, recapture the 0416:8040 RF sender interface and include usb.capdata/usbhid.data fields.")
    usb_summary = _capture_transport_usb_summary(records, protocol_items)
    notes.extend(_capture_transport_usb_notes(usb_summary, records))

    return {
        "operation": "capture-transport-report",
        "source": source,
        "packet_candidate_count": len(records),
        "protocol_candidate_count": protocol_count,
        "field_counts": dict(sorted(field_counts.items())),
        "size_counts": dict(sorted(size_counts.items(), key=lambda item: int(item[0]) if item[0].isdigit() else item[0])),
        "kind_counts": dict(sorted(kind_counts.items())),
        **usb_summary,
        "first_protocol_candidates": protocol_items[:20],
        "first_packets": records[:20],
        "recommended_commands": _capture_transport_recommended_commands(source, protocol_count),
        "notes": notes,
    }


def capture_protocol_report_from_analysis(
    analysis: dict[str, Any],
    *,
    packet_metadata: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    records = [record for record in analysis.get("records", []) if isinstance(record, dict)]
    frames = [frame for frame in analysis.get("rf_frames", []) if isinstance(frame, dict)]
    device_reports: dict[str, dict[str, Any]] = {}
    operation_reports: dict[str, dict[str, Any]] = {}
    motherboard_pwm_values: set[int] = set()
    metadata = packet_metadata or {}

    for record in records:
        if record.get("kind") != "receiver-snapshot":
            continue
        motherboard_pwm = record.get("motherboard_pwm")
        if isinstance(motherboard_pwm, int):
            motherboard_pwm_values.add(max(0, min(255, motherboard_pwm)))
        snapshot_index = record.get("index")
        for device in record.get("devices", []) if isinstance(record.get("devices"), list) else []:
            if not isinstance(device, dict):
                continue
            mac = str(device.get("mac") or "").lower()
            if not mac:
                continue
            report = _device_report(device_reports, mac)
            report["snapshot_count"] += 1
            if isinstance(snapshot_index, int):
                report["snapshot_indexes"].add(snapshot_index)
            _add_device_snapshot_fields(report, device)
            snapshot_state = _snapshot_device_state_payload(device, snapshot_index)
            if snapshot_state:
                report["latest_snapshot_device"] = snapshot_state

    for frame_index, frame in enumerate(frames):
        operation = str(frame.get("operation") or "unknown")
        target_mac = str(frame.get("target_mac") or "").lower()
        if target_mac:
            report = _device_report(device_reports, target_mac)
            report["rf_frame_count"] += 1
            report["rf_frame_indexes"].add(frame_index)
            report["rf_frame_operations"][operation] += 1
            _add_device_frame_fields(report, frame, metadata)
            if _counts_as_logical_operation(frame):
                report["operations"][operation] += 1
        if not _counts_as_logical_operation(frame):
            continue
        operation_report = _operation_report(operation_reports, operation)
        _add_operation_frame_fields(operation_report, frame_index, frame, metadata)

    finalized_devices = {
        mac: _finalize_device_report(report)
        for mac, report in sorted(device_reports.items())
    }
    finalized_operations = {
        operation: _finalize_operation_report(report)
        for operation, report in sorted(operation_reports.items())
    }
    live_write_targets = _protocol_linux_live_write_targets(finalized_operations, finalized_devices)

    return {
        "operation": "capture-protocol-report",
        "source": analysis.get("source", ""),
        "packet_count": analysis.get("packet_count", 0),
        "rf_frame_count": analysis.get("rf_frame_count", 0),
        "receiver_snapshot_count": _summary_count(analysis, "snapshot_count"),
        "master_response_count": _summary_count(analysis, "master_response_count"),
        "replay_hint_count": _summary_count(analysis, "replay_hint_count"),
        "motherboard_pwm_values": sorted(motherboard_pwm_values),
        "devices": finalized_devices,
        "operations": finalized_operations,
        "linux_live_write_targets": live_write_targets,
        "summary": {
            **dict(analysis.get("summary", {}) if isinstance(analysis.get("summary"), dict) else {}),
            "device_count": len(device_reports),
            "operation_count": len(operation_reports),
            "linux_live_write_target_count": len(live_write_targets),
        },
    }


def capture_timeline_report_from_analysis(
    analysis: dict[str, Any],
    *,
    packet_metadata: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    records = [record for record in analysis.get("records", []) if isinstance(record, dict)]
    frames = [frame for frame in analysis.get("rf_frames", []) if isinstance(frame, dict)]
    events: list[dict[str, Any]] = []
    previous_devices: dict[str, dict[str, Any]] = {}
    skipped_rf_chunk_count = 0
    metadata = packet_metadata or {}

    for record in records:
        if record.get("kind") == "rf-chunk":
            skipped_rf_chunk_count += 1
            continue
        event = _timeline_event_from_record(record, previous_devices, metadata)
        if event is not None:
            events.append(event)
        if record.get("kind") == "receiver-snapshot":
            _timeline_update_devices(previous_devices, record)

    for frame_index, frame in enumerate(frames):
        events.append(_timeline_event_from_frame(frame_index, frame, metadata))

    events.sort(key=_timeline_event_sort_key)
    for event_index, event in enumerate(events):
        event["event_index"] = event_index
    _timeline_annotate_event_times(events)

    return {
        "operation": "capture-timeline-report",
        "source": analysis.get("source", ""),
        "packet_count": analysis.get("packet_count", 0),
        "event_count": len(events),
        "skipped_rf_chunk_count": skipped_rf_chunk_count,
        "summary": _capture_timeline_summary(events, analysis),
        "events": events,
        "warnings": _capture_timeline_warnings(events, skipped_rf_chunk_count, analysis),
    }


def _timeline_event_from_record(
    record: dict[str, Any],
    previous_devices: dict[str, dict[str, Any]],
    packet_metadata: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    kind = str(record.get("kind") or "")
    if not kind:
        return None
    packet_index = record.get("index")
    event: dict[str, Any] = {
        "event_type": kind,
        "packet_index": packet_index,
        "kind": kind,
        "size": record.get("size"),
    }
    if isinstance(packet_index, int):
        usb = _timeline_usb_metadata(packet_index, packet_metadata)
        if usb:
            event["usb"] = usb
    if kind == "receiver-snapshot":
        devices = [
            _timeline_device_payload(device)
            for device in record.get("devices", [])
            if isinstance(device, dict)
        ]
        event.update(
            {
                "device_count": record.get("device_count", len(devices)),
                "motherboard_pwm": record.get("motherboard_pwm"),
                "motherboard_pwm_raw": record.get("motherboard_pwm_raw"),
                "devices": devices,
                "device_changes": _timeline_snapshot_changes(previous_devices, devices),
            }
        )
    elif kind == "receiver-list-request":
        event["page_count"] = record.get("page_count")
    elif kind in {"master-query-request", "master-query-response"}:
        event["channel"] = record.get("channel")
        if "master_mac" in record:
            event["master_mac"] = record.get("master_mac")
    elif kind == "master-query-empty-response":
        event["master_mac"] = None
    elif kind == "unknown":
        event["error"] = record.get("error")
        event["hex_prefix"] = str(record.get("hex") or "")[:64]
    elif kind == "unknown-master-packet":
        event["hex_prefix"] = str(record.get("hex") or "")[:64]
    return event


def _timeline_event_from_frame(
    frame_index: int,
    frame: dict[str, Any],
    packet_metadata: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    summary = _frame_summary(frame)
    chunk_indexes = frame.get("chunk_packet_indexes")
    first_packet_index = frame.get("first_packet_index")
    if not isinstance(chunk_indexes, list) and isinstance(first_packet_index, int):
        chunk_indexes = list(range(first_packet_index, first_packet_index + 4))
    chunk_usb = []
    for index in chunk_indexes or []:
        if not isinstance(index, int):
            continue
        usb = _timeline_usb_metadata(index, packet_metadata)
        if usb:
            chunk_usb.append(usb)
    event = {
        "event_type": "rf-frame",
        "packet_index": first_packet_index,
        "rf_frame_index": frame_index,
        "logical_operation": _counts_as_logical_operation(frame),
        "chunk_packet_indexes": chunk_indexes or [],
        **summary,
    }
    if chunk_usb:
        event["usb"] = chunk_usb[0]
        event["chunk_usb"] = chunk_usb
    return event


def _timeline_packet_metadata(items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    metadata: dict[int, dict[str, Any]] = {}
    for index, item in enumerate(items):
        if not isinstance(item.get("packet"), (bytes, bytearray)):
            continue
        usb = {
            key: item[key]
            for key in (
                "field",
                "frame_number",
                "frame_time_relative",
                "frame_time_delta",
                "frame_time_delta_displayed",
                "frame_time_epoch",
                "usb_bus",
                "usb_device_address",
                "usb_endpoint_address",
                "usb_endpoint_number",
                "usb_endpoint_direction",
                "usb_transfer_type",
                "usb_status",
                "usb_vendor_id",
                "usb_product_id",
                "usb_source",
                "usb_destination",
            )
            if key in item and item[key] not in ("", None)
        }
        if usb:
            metadata[index] = usb
    return metadata


def _timeline_usb_metadata(index: int, packet_metadata: dict[int, dict[str, Any]]) -> dict[str, Any]:
    metadata = dict(packet_metadata.get(index, {}))
    vendor = _normalize_usb_id(str(metadata.get("usb_vendor_id") or ""))
    product = _normalize_usb_id(str(metadata.get("usb_product_id") or ""))
    if vendor and product:
        metadata["vid_pid"] = f"{vendor}:{product}"
        known = KNOWN_USB_DEVICES.get((int(vendor, 16), int(product, 16)))
        if known:
            metadata["known_device"] = known
    return metadata


def _timeline_device_payload(device: dict[str, Any]) -> dict[str, Any]:
    return {
        key: device[key]
        for key in (
            "mac",
            "master_mac",
            "channel",
            "rx_type",
            "device_type",
            "fan_count",
            "fan_rpm",
            "pwm_values",
            "command_sequence",
        )
        if key in device
    }


def _timeline_snapshot_changes(
    previous_devices: dict[str, dict[str, Any]],
    devices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    current_macs = {str(device.get("mac") or "").lower() for device in devices if device.get("mac")}
    previous_macs = set(previous_devices)
    for device in devices:
        mac = str(device.get("mac") or "").lower()
        if not mac:
            continue
        previous = previous_devices.get(mac)
        if previous is None:
            changes.append({"mac": mac, "change": "first-seen"})
            continue
        changed_fields = [
            field
            for field in (
                "master_mac",
                "channel",
                "rx_type",
                "device_type",
                "fan_count",
                "fan_rpm",
                "pwm_values",
                "command_sequence",
            )
            if previous.get(field) != device.get(field)
        ]
        if changed_fields:
            changes.append(
                {
                    "mac": mac,
                    "change": "updated",
                    "changed_fields": changed_fields,
                    "before": {field: previous.get(field) for field in changed_fields},
                    "after": {field: device.get(field) for field in changed_fields},
                }
            )
    for missing_mac in sorted(previous_macs - current_macs):
        changes.append({"mac": missing_mac, "change": "missing-from-snapshot"})
    return changes


def _timeline_update_devices(previous_devices: dict[str, dict[str, Any]], record: dict[str, Any]) -> None:
    for device in record.get("devices", []) if isinstance(record.get("devices"), list) else []:
        if not isinstance(device, dict):
            continue
        mac = str(device.get("mac") or "").lower()
        if mac:
            previous_devices[mac] = _timeline_device_payload(device)


def _timeline_event_sort_key(event: dict[str, Any]) -> tuple[int, int, str]:
    packet_index = event.get("packet_index")
    packet_sort = packet_index if isinstance(packet_index, int) else 10**12
    priority = 1 if event.get("event_type") == "rf-frame" else 0
    return (packet_sort, priority, str(event.get("event_type") or ""))


def _timeline_annotate_event_times(events: list[dict[str, Any]]) -> None:
    previous_key = ""
    previous_value: float | None = None
    for event in events:
        chunk_span = _timeline_chunk_time_span(event)
        if chunk_span is not None and chunk_span > 0:
            event["chunk_time_span_s"] = _round_time_seconds(chunk_span)

        time_key, time_value = _timeline_event_time_value(event)
        if time_value is None:
            continue
        event[time_key] = _round_time_seconds(time_value)
        if previous_value is not None and previous_key == time_key:
            event["delta_from_previous_s"] = _round_time_seconds(time_value - previous_value)
        previous_key = time_key
        previous_value = time_value


def _timeline_event_time_value(event: dict[str, Any]) -> tuple[str, float | None]:
    usb = event.get("usb")
    if not isinstance(usb, dict):
        return "", None
    relative = _parse_float(usb.get("frame_time_relative"))
    if relative is not None:
        return "time_relative_s", relative
    epoch = _parse_float(usb.get("frame_time_epoch"))
    if epoch is not None:
        return "time_epoch_s", epoch
    return "", None


def _timeline_chunk_time_span(event: dict[str, Any]) -> float | None:
    chunk_usb = event.get("chunk_usb")
    if not isinstance(chunk_usb, list):
        return None
    relative_values = [
        value
        for item in chunk_usb
        if isinstance(item, dict)
        for value in [_parse_float(item.get("frame_time_relative"))]
        if value is not None
    ]
    if len(relative_values) >= 2:
        return max(relative_values) - min(relative_values)
    epoch_values = [
        value
        for item in chunk_usb
        if isinstance(item, dict)
        for value in [_parse_float(item.get("frame_time_epoch"))]
        if value is not None
    ]
    if len(epoch_values) >= 2:
        return max(epoch_values) - min(epoch_values)
    return None


def _parse_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _round_time_seconds(value: float) -> float:
    rounded = round(float(value), 6)
    return 0.0 if rounded == -0.0 else rounded


def _capture_timeline_summary(events: list[dict[str, Any]], analysis: dict[str, Any]) -> dict[str, Any]:
    event_types = Counter(str(event.get("event_type") or "unknown") for event in events)
    rf_operations = Counter(
        str(event.get("operation"))
        for event in events
        if event.get("event_type") == "rf-frame" and event.get("operation")
    )
    logical_operations = Counter(
        str(event.get("operation"))
        for event in events
        if event.get("event_type") == "rf-frame"
        and event.get("operation")
        and event.get("logical_operation")
    )
    device_macs = sorted(
        {
            str(device.get("mac")).lower()
            for event in events
            for device in event.get("devices", [])
            if isinstance(device, dict) and device.get("mac")
        }
    )
    snapshot_change_counts = Counter(
        str(change.get("change"))
        for event in events
        for change in event.get("device_changes", [])
        if isinstance(change, dict) and change.get("change")
    )
    time_values = [
        float(event["time_relative_s"])
        for event in events
        if isinstance(event.get("time_relative_s"), (int, float))
    ]
    deltas = [
        float(event["delta_from_previous_s"])
        for event in events
        if isinstance(event.get("delta_from_previous_s"), (int, float))
    ]
    summary = {
        "event_types": dict(sorted(event_types.items())),
        "rf_operations": dict(sorted(rf_operations.items())),
        "logical_rf_operations": dict(sorted(logical_operations.items())),
        "device_macs": device_macs,
        "snapshot_change_counts": dict(sorted(snapshot_change_counts.items())),
        "analysis_summary": analysis.get("summary", {}),
    }
    if time_values:
        summary["timed_event_count"] = len(time_values)
        summary["time_span_s"] = _round_time_seconds(max(time_values) - min(time_values)) if len(time_values) > 1 else 0.0
    if deltas:
        summary["max_event_gap_s"] = _round_time_seconds(max(deltas))
    return summary


def _capture_timeline_warnings(
    events: list[dict[str, Any]],
    skipped_rf_chunk_count: int,
    analysis: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    rf_frame_count = int(analysis.get("rf_frame_count", 0) or 0)
    if skipped_rf_chunk_count and rf_frame_count == 0:
        warnings.append("RF chunks were present but no complete 4-chunk RF frame was reassembled.")
    if not events:
        warnings.append("No known L-Wireless timeline events were decoded.")
    if not any(event.get("event_type") == "receiver-snapshot" for event in events):
        warnings.append("No receiver snapshot event was present; target device context may be incomplete.")
    return warnings


def capture_replay_plan_from_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    hints = [
        hint
        for hint in analysis.get("replay_hints", [])
        if isinstance(hint, dict)
    ]
    items: list[dict[str, Any]] = []
    dry_run_commands: list[str] = []
    compare_commands: list[str] = []
    notes: list[dict[str, Any]] = []
    operation_counts = Counter(str(hint.get("operation") or "") for hint in hints)
    for hint in hints:
        dry_run = _command_payload(hint.get("dry_run"))
        compare_capture = _command_payload(hint.get("compare_capture"))
        item: dict[str, Any] = {
            "rf_frame_index": hint.get("rf_frame_index"),
            "operation": hint.get("operation"),
            "target": hint.get("target"),
        }
        if dry_run is not None:
            item["dry_run"] = dry_run
            dry_run_commands.append(str(dry_run["command"]))
        if compare_capture is not None:
            item["compare_capture"] = compare_capture
            compare_commands.append(str(compare_capture["command"]))
        if "decoded_args" in hint:
            item["decoded_args"] = hint["decoded_args"]
        if "note" in hint:
            note = {
                "rf_frame_index": hint.get("rf_frame_index"),
                "operation": hint.get("operation"),
                "note": hint["note"],
            }
            item["note"] = hint["note"]
            notes.append(note)
        items.append(item)
    return {
        "operation": "capture-replay-plan",
        "source": analysis.get("source", ""),
        "rf_frame_count": analysis.get("rf_frame_count", 0),
        "replay_hint_count": len(hints),
        "operation_counts": dict(sorted(operation_counts.items())),
        "dry_run_commands": _unique_preserve_order(dry_run_commands),
        "compare_capture_commands": _unique_preserve_order(compare_commands),
        "notes": notes,
        "items": items,
        "summary": analysis.get("summary", {}),
    }


def load_capture_packets(path: Path) -> list[bytes]:
    if _is_pcap_file(path):
        return _packets_from_pcap_file(path)
    text = _read_capture_text(path)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _packets_from_text(text)
    packets = _packets_from_json(payload)
    if packets:
        return packets
    return _packets_from_text(text)


def analyze_capture_packets(packets: Iterable[bytes], *, source: str = "") -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    rf_chunks: list[RfChunk] = []
    snapshots: list[dict[str, Any]] = []
    master_responses: list[dict[str, Any]] = []

    for index, packet in enumerate(packets):
        try:
            normalized = normalize_lianli_packet(packet)
            record = classify_lianli_packet(normalized)
        except LianLiWirelessError as error:
            record = {
                "index": index,
                "kind": "unknown",
                "size": len(packet),
                "error": str(error),
                "hex": packet.hex(),
            }
            records.append(record)
            continue
        record = {"index": index, **record}
        records.append(record)
        if record["kind"] == "rf-chunk":
            rf_chunks.append(
                RfChunk(
                    packet_index=index,
                    sequence=int(record["sequence"]),
                    channel=int(record["channel"]),
                    rx_type=int(record["rx_type"]),
                    data=bytes.fromhex(str(record["data_hex"])),
                    raw=normalized,
                )
            )
        elif record["kind"] == "receiver-snapshot":
            snapshots.append(record)
        elif record["kind"] == "master-query-response":
            master_responses.append(record)

    frames = reassemble_rf_frames(rf_chunks)
    decoded_frames = _annotate_pwm_mirror_frames(
        [decode_rf_frame(frame) for frame in frames],
        snapshots,
    )
    decoded_frames = _annotate_rgb_payloads(decoded_frames)
    decoded_frames = _attach_replay_hints(decoded_frames, snapshots, source=source)
    replay_hints = [
        {"rf_frame_index": index, **frame["replay_hint"]}
        for index, frame in enumerate(decoded_frames)
        if isinstance(frame.get("replay_hint"), dict)
    ]
    receiver_macs = _unique_strings(
        _record_mac_values(records, "mac") + _record_mac_values(decoded_frames, "target_mac")
    )
    master_macs = _unique_strings(
        _record_mac_values(records, "master_mac") + _record_mac_values(decoded_frames, "master_mac")
    )
    kinds = Counter(str(record["kind"]) for record in records)
    frame_operations = Counter(str(frame["operation"]) for frame in decoded_frames)
    operations = Counter(
        str(frame["operation"])
        for frame in decoded_frames
        if _counts_as_logical_operation(frame)
    )
    rgb_sequence_count = sum(
        1
        for frame in decoded_frames
        if frame.get("operation") == "live-rgb" and isinstance(frame.get("rgb_payload"), dict)
    )
    return {
        "operation": "analyze-capture",
        "source": source,
        "packet_count": len(records),
        "records": records,
        "rf_frame_count": len(decoded_frames),
        "rf_frames": decoded_frames,
        "replay_hints": replay_hints,
        "summary": {
            "kinds": dict(sorted(kinds.items())),
            "rf_operations": dict(sorted(operations.items())),
            "rf_frame_operations": dict(sorted(frame_operations.items())),
            "receiver_macs": receiver_macs,
            "master_macs": master_macs,
            "snapshot_count": len(snapshots),
            "master_response_count": len(master_responses),
            "replay_hint_count": len(replay_hints),
            "rgb_sequence_count": rgb_sequence_count,
        },
    }


def compare_capture_packets(
    observed_packets: Iterable[bytes],
    expected_packets: Iterable[bytes],
    *,
    source: str = "",
    expected_source: str = "expected",
) -> dict[str, Any]:
    observed_analysis = analyze_capture_packets(list(observed_packets), source=source)
    expected_analysis = analyze_capture_packets(
        list(expected_packets),
        source=expected_source,
    )
    observed_frames = observed_analysis["rf_frames"]
    expected_frames = expected_analysis["rf_frames"]
    exact = _match_decoded_frames(expected_frames, observed_frames, semantic=False)
    semantic = _match_decoded_frames(expected_frames, observed_frames, semantic=True)
    diagnostics = _compare_capture_diagnostics(exact, semantic)
    return {
        "operation": "compare-capture",
        "source": source,
        "expected_source": expected_source,
        "matched": semantic["matched"],
        "exact_match": exact["matched"],
        "semantic_match": semantic["matched"],
        "diagnostics": diagnostics,
        "observed": {
            "packet_count": observed_analysis["packet_count"],
            "rf_frame_count": observed_analysis["rf_frame_count"],
            "summary": observed_analysis["summary"],
        },
        "expected": {
            "packet_count": expected_analysis["packet_count"],
            "rf_frame_count": expected_analysis["rf_frame_count"],
            "summary": expected_analysis["summary"],
        },
        "exact": exact,
        "semantic": semantic,
    }


def normalize_lianli_packet(packet: bytes) -> bytes:
    raw = bytes(packet)
    if len(raw) >= 65 and raw[0] == 0 and raw[1] in (RF_GET_DEV_CMD, RF_MASTER_QUERY_CMD):
        raw = raw[1:65]
    if len(raw) == 64 or len(raw) >= 434:
        return raw
    raise LianLiWirelessError(f"unsupported packet size: {len(raw)}")


def classify_lianli_packet(packet: bytes) -> dict[str, Any]:
    if len(packet) >= 434 and packet[0] == RF_GET_DEV_CMD:
        devices = parse_wireless_snapshot(packet)
        motherboard_pwm = extract_motherboard_pwm(packet)
        return {
            "kind": "receiver-snapshot",
            "size": len(packet),
            "device_count": len(devices),
            "motherboard_pwm": motherboard_pwm,
            "motherboard_pwm_raw": {
                "indicator": packet[2],
                "value": packet[3],
                "valid": motherboard_pwm is not None,
            },
            "devices": [_device_payload(device) for device in devices],
        }
    if len(packet) != 64:
        raise LianLiWirelessError(f"unsupported normalized packet size: {len(packet)}")
    if packet[0] == RF_GET_DEV_CMD and packet[2:] == bytes(62):
        return {
            "kind": "receiver-list-request",
            "size": 64,
            "page_count": packet[1],
            "hex": packet.hex(),
        }
    if packet[0] == RF_MASTER_QUERY_CMD:
        if packet[2:] == bytes(62) and packet[1] != 0:
            return {
                "kind": "master-query-request",
                "size": 64,
                "channel": packet[1],
                "hex": packet.hex(),
            }
        if packet[1:7] == bytes(6):
            return {
                "kind": "master-query-empty-response",
                "size": 64,
                "master_mac": None,
                "hex": packet.hex(),
            }
        parsed = parse_master_query_response(packet)
        if parsed is not None:
            master_mac, channel = parsed
            return {
                "kind": "master-query-response",
                "size": 64,
                "master_mac": master_mac,
                "channel": channel,
                "hex": packet.hex(),
            }
        return {"kind": "unknown-master-packet", "size": 64, "hex": packet.hex()}
    if packet[0] == RF_PACKET_HEADER:
        return {
            "kind": "rf-chunk",
            "size": 64,
            "sequence": packet[1],
            "channel": packet[2],
            "rx_type": packet[3],
            "data_hex": packet[4:64].hex(),
            "hex": packet.hex(),
        }
    return {"kind": "unknown", "size": len(packet), "hex": packet.hex()}


def reassemble_rf_frames(chunks: Iterable[RfChunk]) -> list[RfFrame]:
    frames: list[RfFrame] = []
    pending: list[RfChunk] = []
    for chunk in chunks:
        if chunk.sequence == 0:
            pending = [chunk]
        elif (
            pending
            and chunk.sequence == len(pending)
            and chunk.channel == pending[0].channel
            and chunk.rx_type == pending[0].rx_type
        ):
            pending.append(chunk)
        else:
            pending = []
        if len(pending) == 4:
            payload = b"".join(item.data for item in pending)
            if len(payload) == RF_PAYLOAD_SIZE:
                frames.append(
                    RfFrame(
                        first_packet_index=pending[0].packet_index,
                        channel=pending[0].channel,
                        rx_type=pending[0].rx_type,
                        chunks=tuple(pending),
                        payload=payload,
                    )
                )
            pending = []
    return frames


def decode_rf_frame(frame: RfFrame) -> dict[str, Any]:
    payload = frame.payload
    base: dict[str, Any] = {
        "first_packet_index": frame.first_packet_index,
        "chunk_packet_indexes": [chunk.packet_index for chunk in frame.chunks],
        "chunk_sequences": [chunk.sequence for chunk in frame.chunks],
        "channel": frame.channel,
        "outer_rx_type": frame.rx_type,
        "command_hex": payload[:2].hex(),
        "payload_hex": payload.hex(),
    }
    if payload[:2] == bytes([0x12, 0x10]):
        target_mac = _bytes_to_mac(payload[2:8])
        master_mac = _bytes_to_mac(payload[8:14])
        payload_rx_type = payload[14]
        payload_channel = payload[15]
        sequence = payload[16]
        pwm_values = list(payload[17:21])
        operation = "live-pwm"
        if set(master_mac.split(":")) == {"00"} and payload_rx_type == 0:
            operation = "live-unbind"
        elif frame.rx_type == 0 and set(master_mac.split(":")) != {"00"}:
            operation = "live-bind"
        elif pwm_values == [6, 6, 6, 6]:
            operation = "live-pwm-sync"
        return {
            **base,
            "operation": operation,
            "target_mac": target_mac,
            "master_mac": master_mac,
            "rx_type": payload_rx_type,
            "payload_channel": payload_channel,
            "sequence": sequence,
            "pwm_values": pwm_values,
        }
    if payload[:2] == bytes([0x12, 0x20]):
        return {
            **base,
            "operation": "live-rgb",
            "target_mac": _bytes_to_mac(payload[2:8]),
            "master_mac": _bytes_to_mac(payload[8:14]),
            "effect_index": int.from_bytes(payload[14:18], "big", signed=False),
            "packet_index": payload[18],
            "packet_count": payload[19],
            "compressed_length": int.from_bytes(payload[20:24], "big", signed=False),
            "frame_count": int.from_bytes(payload[25:27], "big", signed=False),
            "led_count": payload[27],
            "interval_ms": int.from_bytes(payload[32:34], "big", signed=False),
        }
    return {**base, "operation": "unknown-rf-payload"}


def _annotate_pwm_mirror_frames(
    frames: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    snapshot_pwm_values: list[tuple[int, int]] = []
    for snapshot in snapshots:
        index = snapshot.get("index")
        motherboard_pwm = snapshot.get("motherboard_pwm")
        if not isinstance(index, int) or not isinstance(motherboard_pwm, int):
            continue
        snapshot_pwm_values.append((index, max(0, min(255, motherboard_pwm))))
    if not snapshot_pwm_values:
        return frames

    snapshot_pwm_values.sort()
    annotated: list[dict[str, Any]] = []
    for frame in frames:
        if frame.get("operation") != "live-pwm":
            annotated.append(frame)
            continue
        pwm_values = list(frame.get("pwm_values") or ())
        if len(pwm_values) != 4 or len(set(pwm_values)) != 1:
            annotated.append(frame)
            continue
        frame_index = frame.get("first_packet_index")
        if not isinstance(frame_index, int):
            annotated.append(frame)
            continue
        prior_snapshot: tuple[int, int] | None = None
        for snapshot_index, motherboard_pwm in snapshot_pwm_values:
            if snapshot_index >= frame_index:
                break
            prior_snapshot = (snapshot_index, motherboard_pwm)
        if prior_snapshot is None or pwm_values != [prior_snapshot[1]] * 4:
            annotated.append(frame)
            continue
        annotated.append(
            {
                **frame,
                "operation": "live-pwm-mirror",
                "original_operation": "live-pwm",
                "motherboard_pwm": prior_snapshot[1],
                "motherboard_pwm_snapshot_index": prior_snapshot[0],
                "inferred_from_snapshot": True,
            }
        )
    return annotated


def _annotate_rgb_payloads(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    annotated = [dict(frame) for frame in frames]
    processed_indexes: set[int] = set()
    for index, frame in enumerate(annotated):
        if index in processed_indexes:
            continue
        if frame.get("operation") != "live-rgb" or frame.get("packet_index") != 0:
            continue
        summary, group_indexes = _decode_rgb_sequence(index, annotated)
        processed_indexes.update(group_indexes)
        summary = {
            **summary,
            "sequence_frame_indexes": list(group_indexes),
            "sequence_rf_frame_count": len(group_indexes),
        }
        sequence_id = _rgb_sequence_id(frame)
        for member_index, group_index in enumerate(group_indexes):
            annotated[group_index]["rgb_sequence_id"] = sequence_id
            annotated[group_index]["rgb_sequence_primary_index"] = index
            annotated[group_index]["rgb_sequence_member_index"] = member_index
            annotated[group_index]["rgb_decode_status"] = summary["decode_status"]
        annotated[index]["rgb_payload"] = summary
    return annotated


def _counts_as_logical_operation(frame: dict[str, Any]) -> bool:
    if frame.get("operation") != "live-rgb":
        return True
    if "rgb_sequence_id" not in frame:
        return True
    return isinstance(frame.get("rgb_payload"), dict)


def _decode_rgb_sequence(first_index: int, frames: list[dict[str, Any]]) -> tuple[dict[str, Any], list[int]]:
    first = frames[first_index]
    packet_count = first.get("packet_count")
    compressed_length = first.get("compressed_length")
    frame_count = first.get("frame_count")
    led_count = first.get("led_count")
    base_summary = {
        "decode_status": "invalid-header",
        "packet_count": packet_count,
        "compressed_length": compressed_length,
        "frame_count": frame_count,
        "led_count": led_count,
        "packet_indexes": [0],
    }
    if (
        not isinstance(packet_count, int)
        or not isinstance(compressed_length, int)
        or not isinstance(frame_count, int)
        or not isinstance(led_count, int)
        or packet_count <= 0
        or compressed_length <= 0
        or frame_count <= 0
        or led_count <= 0
    ):
        return base_summary, [first_index]

    candidates: dict[int, tuple[int, dict[str, Any]]] = {}
    first_packet_frame_indexes: list[int] = []
    for candidate_index in range(first_index, len(frames)):
        candidate = frames[candidate_index]
        if not _same_rgb_sequence(first, candidate):
            continue
        packet_index = candidate.get("packet_index")
        if not isinstance(packet_index, int) or not 0 <= packet_index < packet_count:
            continue
        if packet_index == 0 and candidate_index != first_index:
            if set(candidates) <= {0}:
                first_packet_frame_indexes.append(candidate_index)
                candidates[0] = (candidate_index, candidate)
                continue
            break
        if packet_index == 0:
            first_packet_frame_indexes.append(candidate_index)
        candidates.setdefault(packet_index, (candidate_index, candidate))
        if len(candidates) == packet_count:
            break

    candidate_indexes = [index for index, _frame in candidates.values()]
    group_indexes = _unique_preserve_order(first_packet_frame_indexes + candidate_indexes) or [first_index]
    summary = {
        **base_summary,
        "decode_status": "incomplete",
        "expected_decoded_length": led_count * frame_count * 3,
        "packet_indexes": sorted(candidates),
        "collected_length": 0,
    }
    if len(first_packet_frame_indexes) > 1:
        summary["first_packet_retransmit_count"] = len(first_packet_frame_indexes)
        summary["first_packet_frame_indexes"] = list(first_packet_frame_indexes)
    if len(candidates) < packet_count:
        summary["missing_packet_indexes"] = [
            packet_index for packet_index in range(packet_count) if packet_index not in candidates
        ]
        return summary, group_indexes

    compressed = _collect_rgb_compressed_bytes(candidates, compressed_length)
    summary["collected_length"] = len(compressed)
    if len(compressed) < compressed_length:
        summary["missing_compressed_bytes"] = compressed_length - len(compressed)
        return summary, group_indexes

    raw, decode_info = _decode_tinyuz_literal(compressed, summary["expected_decoded_length"])
    summary.update(decode_info)
    if raw is None:
        return summary, group_indexes

    summary.update(_decoded_rgb_summary(raw, led_count=led_count, frame_count=frame_count))
    static_color = _static_rgb_color(raw, led_count=led_count, frame_count=frame_count)
    if static_color is not None:
        summary["static_color"] = list(static_color)
        summary["static_color_hex"] = _rgb_color_hex(static_color)
    rainbow = _rgb_payload_rainbow_args(summary, first)
    if rainbow is not None:
        summary["rainbow_generated_match"] = True
        summary["rainbow_led_count"] = rainbow["led_count"]
        summary["rainbow_frame_count"] = rainbow["frame_count"]
        summary["rainbow_interval_ms"] = rainbow["interval_ms"]
    return summary, group_indexes


def _same_rgb_sequence(first: dict[str, Any], candidate: dict[str, Any]) -> bool:
    if candidate.get("operation") != "live-rgb":
        return False
    return all(
        first.get(key) == candidate.get(key)
        for key in ("target_mac", "master_mac", "effect_index", "packet_count")
    )


def _rgb_sequence_id(frame: dict[str, Any]) -> str:
    return ":".join(
        "" if frame.get(key) is None else str(frame.get(key))
        for key in ("first_packet_index", "target_mac", "master_mac", "effect_index")
    )


def _collect_rgb_compressed_bytes(
    candidates: dict[int, tuple[int, dict[str, Any]]],
    compressed_length: int,
) -> bytes:
    collected = bytearray()
    for packet_index in sorted(candidates):
        payload = _frame_payload_bytes(candidates[packet_index][1])
        if payload is None:
            break
        remaining = compressed_length - len(collected)
        if remaining <= 0:
            break
        if packet_index == 0:
            offset = FIRST_LED_PACKET_DATA_OFFSET
            limit = min(FIRST_LED_PACKET_DATA_MAX, len(payload) - offset, remaining)
        else:
            offset = 20
            limit = min(LED_DATA_CHUNK, len(payload) - offset, remaining)
        if limit <= 0:
            break
        collected.extend(payload[offset : offset + limit])
    return bytes(collected)


def _frame_payload_bytes(frame: dict[str, Any]) -> bytes | None:
    payload_hex = frame.get("payload_hex")
    if not isinstance(payload_hex, str):
        return None
    try:
        payload = bytes.fromhex(payload_hex)
    except ValueError:
        return None
    if len(payload) < RF_PAYLOAD_SIZE:
        return None
    return payload[:RF_PAYLOAD_SIZE]


def _decode_tinyuz_literal(data: bytes, expected_length: int) -> tuple[bytes | None, dict[str, Any]]:
    if expected_length <= 0:
        return None, {
            "decode_status": "invalid-expected-length",
            "decoded_length": 0,
        }
    if len(data) < 5:
        return None, {
            "decode_status": "truncated-literal",
            "decoded_length": 0,
            "reason": "TinyUZ stream is shorter than dictionary size plus one type byte",
        }
    dict_size = int.from_bytes(data[:4], "little", signed=False)
    if dict_size <= 0:
        return None, {
            "decode_status": "invalid-dict-size",
            "dict_size": dict_size,
            "decoded_length": 0,
            "reason": "TinyUZ dictionary size must be positive",
        }
    if dict_size > 1 << 30:
        return None, {
            "decode_status": "invalid-dict-size",
            "dict_size": dict_size,
            "decoded_length": 0,
            "reason": "TinyUZ dictionary size exceeds the upstream format limit",
        }

    reader = _TinyUzBitReader(data, 4)
    dictionary = bytearray(dict_size)
    dict_cur = 0
    output = bytearray()
    literal_count = 0
    literal_line_count = 0
    backref_count = 0
    backref_bytes = 0
    max_backref_distance = 0
    max_backref_length = 0
    control_count = 0
    stream_end_found = False
    dict_pos_back = 1
    have_data_back = False

    def emit_byte(value: int, *, source: str) -> None:
        nonlocal dict_cur
        if len(output) >= expected_length:
            raise _TinyUzDecodeError(
                "extra-output",
                f"TinyUZ {source} produced data beyond the expected RGB length",
            )
        byte = value & 0xFF
        dictionary[dict_cur] = byte
        dict_cur = (dict_cur + 1) % dict_size
        output.append(byte)

    def unpack_len(read_bit: int) -> int:
        value = 0
        while True:
            lowbits = reader.read_lowbits(read_bit)
            value = (value << (read_bit - 1)) + (lowbits & ((1 << (read_bit - 1)) - 1))
            if not lowbits & (1 << (read_bit - 1)):
                return value
            value += 1

    def unpack_dict_pos() -> int:
        first = reader.read_byte(
            status="truncated-backref",
            reason="TinyUZ stream ended while reading dictionary position",
        )
        if first < 0x80:
            return first
        return ((first & 0x7F) | (unpack_len(3) << 7)) + 0x80

    def success_info() -> dict[str, Any]:
        status = "decoded-backref" if backref_count else "decoded-literal"
        return {
            "decode_status": status,
            "dict_size": dict_size,
            "decoded_length": len(output),
            "consumed_compressed_length": reader.cursor,
            "stream_end_found": stream_end_found,
            "literal_count": literal_count,
            "literal_line_count": literal_line_count,
            "backref_count": backref_count,
            "backref_bytes": backref_bytes,
            "max_backref_distance": max_backref_distance,
            "max_backref_length": max_backref_length,
            "control_count": control_count,
            "remaining_type_bits": reader.type_count,
        }

    try:
        while True:
            try:
                code_type = reader.read_bit()
            except _TinyUzDecodeError as error:
                if len(output) == expected_length:
                    return bytes(output), {
                        **success_info(),
                        "reason": "TinyUZ code ended after expected RGB bytes without a streamEnd marker",
                    }
                raise error

            if code_type:
                value = reader.read_byte(
                    status="truncated-literal",
                    reason="TinyUZ literal type bit has no following byte",
                )
                emit_byte(value, source="literal")
                literal_count += 1
                have_data_back = True
                continue

            saved_len = unpack_len(2)
            if have_data_back and reader.read_bit():
                saved_dict_pos = dict_pos_back
            else:
                saved_dict_pos = unpack_dict_pos()
                if saved_dict_pos > TINYUZ_BIG_POS_FOR_LEN:
                    saved_len += 1
            have_data_back = False

            if saved_dict_pos:
                if saved_dict_pos > dict_size:
                    raise _TinyUzDecodeError(
                        "invalid-dict-pos",
                        "TinyUZ dictionary position is larger than the dictionary size",
                    )
                if len(output) < dict_size and saved_dict_pos > len(output):
                    raise _TinyUzDecodeError(
                        "invalid-dict-pos",
                        "TinyUZ dictionary back-reference points before available output",
                    )
                match_len = saved_len + TINYUZ_MIN_DICT_MATCH
                dict_pos_back = saved_dict_pos
                dict_type_pos = dict_size - saved_dict_pos
                for _ in range(match_len):
                    index_pos = dict_cur + dict_type_pos
                    if dict_type_pos >= dict_size - dict_cur:
                        index_pos -= dict_size
                    emit_byte(dictionary[index_pos], source="back-reference")
                backref_count += 1
                backref_bytes += match_len
                max_backref_distance = max(max_backref_distance, saved_dict_pos)
                max_backref_length = max(max_backref_length, match_len)
                continue

            control_count += 1
            dict_pos_back = 1
            if saved_len == TINYUZ_CTRL_LITERAL_LINE:
                literal_line_len = unpack_len(3) + TINYUZ_MIN_LITERAL_LINE
                literal_line_count += 1
                for _ in range(literal_line_len):
                    value = reader.read_byte(
                        status="truncated-literal-line",
                        reason="TinyUZ literal-line control has too few following bytes",
                    )
                    emit_byte(value, source="literal-line")
                literal_count += literal_line_len
                have_data_back = True
                continue
            reader.reset_types()
            if saved_len == TINYUZ_CTRL_CLIP_END:
                continue
            if saved_len == TINYUZ_CTRL_STREAM_END:
                stream_end_found = True
                if len(output) != expected_length:
                    return None, {
                        **success_info(),
                        "decode_status": "stream-end-before-expected",
                        "reason": "TinyUZ streamEnd was reached before the expected RGB byte count",
                    }
                return bytes(output), success_info()
            raise _TinyUzDecodeError(
                "unknown-control",
                f"TinyUZ control value {saved_len} is not supported",
            )
    except _TinyUzDecodeError as error:
        return None, {
            "decode_status": error.status,
            "dict_size": dict_size,
            "decoded_length": len(output),
            "consumed_compressed_length": reader.cursor,
            "stream_end_found": stream_end_found,
            "literal_count": literal_count,
            "literal_line_count": literal_line_count,
            "backref_count": backref_count,
            "backref_bytes": backref_bytes,
            "max_backref_distance": max_backref_distance,
            "max_backref_length": max_backref_length,
            "control_count": control_count,
            "remaining_type_bits": reader.type_count,
            "reason": error.reason,
        }


def _static_rgb_color(raw: bytes, *, led_count: int, frame_count: int) -> tuple[int, int, int] | None:
    expected_length = led_count * frame_count * 3
    if len(raw) != expected_length or len(raw) % 3:
        return None
    triples = {bytes(raw[index : index + 3]) for index in range(0, len(raw), 3)}
    if len(triples) != 1:
        return None
    color = next(iter(triples))
    return color[0], color[1], color[2]


def _decoded_rgb_summary(raw: bytes, *, led_count: int, frame_count: int) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "decoded_sha256": hashlib.sha256(raw).hexdigest(),
        "decoded_frame_count": frame_count,
        "decoded_led_count": led_count,
    }
    if not raw or len(raw) % 3:
        return summary

    triples = [bytes(raw[index : index + 3]) for index in range(0, len(raw), 3)]
    if triples:
        first = triples[0]
        last = triples[-1]
        summary["first_color"] = [first[0], first[1], first[2]]
        summary["first_color_hex"] = _rgb_color_hex((first[0], first[1], first[2]))
        summary["last_color"] = [last[0], last[1], last[2]]
        summary["last_color_hex"] = _rgb_color_hex((last[0], last[1], last[2]))

    unique_ordered: dict[bytes, None] = {}
    overflow = False
    for triple in triples:
        unique_ordered.setdefault(triple, None)
        if len(unique_ordered) > 256:
            overflow = True
            break
    summary["unique_color_count"] = len(set(triples)) if not overflow else ">256"
    summary["sample_colors_hex"] = [
        _rgb_color_hex((color[0], color[1], color[2]))
        for color in list(unique_ordered)[:8]
    ]
    return summary


def _rgb_color_hex(color: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)


def _attach_replay_hints(
    frames: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    *,
    source: str,
) -> list[dict[str, Any]]:
    hinted: list[dict[str, Any]] = []
    for frame in frames:
        hint = _frame_replay_hint(frame, snapshots, source=source)
        if hint is None:
            hinted.append(frame)
        else:
            hinted.append({**frame, "replay_hint": hint})
    return hinted


def _frame_replay_hint(
    frame: dict[str, Any],
    snapshots: list[dict[str, Any]],
    *,
    source: str,
) -> dict[str, Any] | None:
    operation = str(frame.get("operation") or "")
    context = _prior_snapshot_device_context(frame, snapshots)
    target_args = _target_cli_args(frame, context, include_master=operation != "live-unbind")
    capture_path = source or "<capture>"
    base_hint: dict[str, Any] = {
        "operation": operation,
        "target": _target_payload(frame, context),
    }
    if context is not None:
        base_hint["context"] = {
            "source": "prior-receiver-snapshot",
            "snapshot_index": context.get("snapshot_index"),
            "device": {
                key: context.get(key)
                for key in ("mac", "master_mac", "channel", "rx_type", "device_type", "fan_count")
                if key in context
            },
        }

    if operation == "live-pwm":
        pwm_values = _frame_pwm_values(frame)
        if not pwm_values:
            return None
        value_arg = _pwm_values_arg(pwm_values)
        return {
            **base_hint,
            "dry_run": {
                "argv": _tool_argv("dry-run-pwm", *target_args, "--pwm-values", value_arg),
            },
            "compare_capture": {
                "expected_operation": "pwm",
                "argv": _tool_argv(
                    "compare-capture",
                    capture_path,
                    "pwm",
                    *target_args,
                    "--pwm-values",
                    value_arg,
                ),
            },
        }

    if operation == "live-pwm-mirror":
        motherboard_pwm = frame.get("motherboard_pwm")
        if not isinstance(motherboard_pwm, int):
            return None
        value = str(max(0, min(255, motherboard_pwm)))
        return {
            **base_hint,
            "dry_run": {
                "argv": _tool_argv("dry-run-pwm-mirror", *target_args, "--motherboard-pwm", value),
            },
            "compare_capture": {
                "expected_operation": "pwm-mirror",
                "argv": _tool_argv(
                    "compare-capture",
                    capture_path,
                    "pwm-mirror",
                    *target_args,
                    "--motherboard-pwm",
                    value,
                ),
            },
        }

    if operation == "live-pwm-sync":
        return {
            **base_hint,
            "dry_run": {"argv": _tool_argv("dry-run-pwm-sync", *target_args)},
            "compare_capture": {
                "expected_operation": "pwm-sync",
                "argv": _tool_argv("compare-capture", capture_path, "pwm-sync", *target_args),
            },
        }

    if operation == "live-bind":
        pwm_values = _frame_pwm_values(frame)
        bind_args = [
            *target_args,
            "--master-mac",
            str(frame.get("master_mac") or ""),
            "--rx-type",
            str(frame.get("rx_type") or frame.get("outer_rx_type") or 0),
        ]
        if pwm_values:
            bind_args.extend(("--current-pwm", _pwm_values_arg(pwm_values)))
        return {
            **base_hint,
            "dry_run": {"argv": _tool_argv("dry-run-bind", *bind_args)},
            "compare_capture": {
                "expected_operation": "bind",
                "argv": _tool_argv("compare-capture", capture_path, "bind", *bind_args),
            },
        }

    if operation == "live-unbind":
        pwm_values = _frame_pwm_values(frame)
        unbind_args = _target_cli_args(frame, context, include_master=True)
        if pwm_values:
            unbind_args.extend(("--current-pwm", _pwm_values_arg(pwm_values)))
        return {
            **base_hint,
            "dry_run": {"argv": _tool_argv("dry-run-unbind", *unbind_args)},
            "compare_capture": {
                "expected_operation": "unbind",
                "argv": _tool_argv("compare-capture", capture_path, "unbind", *unbind_args),
            },
        }

    if operation == "live-rgb":
        rgb_payload = frame.get("rgb_payload")
        if not isinstance(rgb_payload, dict):
            return None
        decoded_args = {
            key: frame.get(key)
            for key in (
                "effect_index",
                "packet_index",
                "packet_count",
                "compressed_length",
                "frame_count",
                "led_count",
                "interval_ms",
            )
            if key in frame
        }
        decoded_args["rgb_decode_status"] = rgb_payload.get("decode_status")
        for key in ("sequence_rf_frame_count", "first_packet_retransmit_count"):
            if key in rgb_payload:
                decoded_args[key] = rgb_payload[key]
        if "static_color" in rgb_payload:
            decoded_args["static_color"] = rgb_payload["static_color"]
        color = _rgb_payload_color_arg(rgb_payload)
        rainbow_args = _rgb_payload_rainbow_args(rgb_payload, frame)
        effect_index = frame.get("effect_index")
        if rainbow_args is not None and isinstance(effect_index, int):
            decoded_args["rainbow_generated_match"] = True
            rainbow_cli_args = [
                "--frame-count",
                str(rainbow_args["frame_count"]),
                "--interval-ms",
                str(rainbow_args["interval_ms"]),
                "--led-count",
                str(rainbow_args["led_count"]),
                "--effect-index",
                str(effect_index),
            ]
            return {
                **base_hint,
                "decoded_args": decoded_args,
                "dry_run": {
                    "argv": _tool_argv(
                        "dry-run-rainbow",
                        *target_args,
                        *rainbow_cli_args,
                    ),
                },
                "compare_capture": {
                    "expected_operation": "rainbow",
                    "argv": _tool_argv(
                        "compare-capture",
                        capture_path,
                        "rainbow",
                        *target_args,
                        *rainbow_cli_args,
                    ),
                },
            }
        if color is not None and isinstance(effect_index, int):
            led_count_cli_args = _rgb_payload_led_count_cli_args(rgb_payload, frame)
            return {
                **base_hint,
                "decoded_args": decoded_args,
                "dry_run": {
                    "argv": _tool_argv(
                        "dry-run-rgb",
                        *target_args,
                        *led_count_cli_args,
                        "--color",
                        color,
                        "--effect-index",
                        str(effect_index),
                    ),
                },
                "compare_capture": {
                    "expected_operation": "rgb",
                    "argv": _tool_argv(
                        "compare-capture",
                        capture_path,
                        "rgb",
                        *target_args,
                        *led_count_cli_args,
                        "--color",
                        color,
                        "--effect-index",
                        str(effect_index),
                    ),
                },
            }
        return {
            **base_hint,
            "decoded_args": decoded_args,
            "note": "RGB color payload is compressed; infer --color from visual intent or decompressed LED payload before exact local replay.",
        }

    return None


def _rgb_payload_led_count_cli_args(
    rgb_payload: dict[str, Any],
    frame: dict[str, Any],
) -> list[str]:
    led_count = _positive_int(rgb_payload.get("decoded_led_count")) or _positive_int(frame.get("led_count"))
    return ["--led-count", str(led_count)] if led_count else []


def _rgb_payload_rainbow_args(
    rgb_payload: dict[str, Any],
    frame: dict[str, Any],
) -> dict[str, int] | None:
    if rgb_payload.get("decode_status") != "decoded-literal":
        return None
    decoded_sha = rgb_payload.get("decoded_sha256")
    if not isinstance(decoded_sha, str):
        return None
    led_count = _positive_int(rgb_payload.get("decoded_led_count")) or _positive_int(frame.get("led_count"))
    frame_count = _positive_int(rgb_payload.get("decoded_frame_count")) or _positive_int(frame.get("frame_count"))
    interval_ms = _nonnegative_int(frame.get("interval_ms"))
    if led_count is None or frame_count is None or interval_ms is None:
        return None
    try:
        generated = generate_rainbow_rgb_frames(led_count, frame_count=frame_count)
    except LianLiWirelessError:
        return None
    if hashlib.sha256(generated).hexdigest() != decoded_sha.lower():
        return None
    return {
        "led_count": led_count,
        "frame_count": frame_count,
        "interval_ms": interval_ms,
    }


def _positive_int(value: Any) -> int | None:
    if not isinstance(value, int) or value <= 0:
        return None
    return value


def _nonnegative_int(value: Any) -> int | None:
    if not isinstance(value, int) or value < 0:
        return None
    return value


def _rgb_payload_color_arg(rgb_payload: dict[str, Any]) -> str | None:
    color = rgb_payload.get("static_color")
    if not isinstance(color, list) or len(color) != 3:
        return None
    if not all(isinstance(component, int) for component in color):
        return None
    return ",".join(str(max(0, min(255, component))) for component in color)


def _normalized_capture_packets(packets: Iterable[bytes]) -> list[tuple[int, bytes]]:
    normalized: list[tuple[int, bytes]] = []
    for index, packet in enumerate(packets):
        try:
            normalized.append((index, normalize_lianli_packet(packet)))
        except LianLiWirelessError:
            continue
    return normalized


def _capture_signature_matched_commands(items: list[dict[str, Any]]) -> list[str]:
    commands: list[str] = []
    for item in items:
        item_commands: list[str] = []
        observed = item.get("observed_commands")
        if isinstance(observed, dict):
            item_commands.extend(str(command) for command in _strings_from_list(observed.get("compare_capture_commands")))
        if item_commands:
            commands.extend(item_commands)
            continue
        static_commands = item.get("commands")
        if isinstance(static_commands, dict):
            command = static_commands.get("compare_capture")
            if isinstance(command, dict) and isinstance(command.get("command"), str):
                commands.append(command["command"])
    return _unique_preserve_order(commands)


def _capture_signature_match_item(
    signature: dict[str, Any],
    observed_analysis: dict[str, Any],
    normalized_packets: list[tuple[int, bytes]],
    observed_packet_hashes: Counter[str],
    *,
    source: str,
) -> dict[str, Any]:
    operation = str(signature.get("operation", ""))
    expected_packets = _signature_expected_packets(signature)
    expected_analysis = analyze_capture_packets(expected_packets, source=f"signature:{operation}")
    sequence_match = _packet_sequence_match(normalized_packets, expected_packets)
    packet_sha256 = [str(value) for value in signature.get("packet_sha256", []) if isinstance(value, str)]
    packet_hash_match_count = sum(observed_packet_hashes.get(value, 0) for value in packet_sha256)
    unique_packet_hash_match_count = sum(1 for value in set(packet_sha256) if value in observed_packet_hashes)
    exact = _match_decoded_frames(
        expected_analysis["rf_frames"],
        observed_analysis["rf_frames"],
        semantic=False,
    )
    semantic = _match_decoded_frames(
        expected_analysis["rf_frames"],
        observed_analysis["rf_frames"],
        semantic=True,
    )
    exact_match = bool(exact["matched"]) if expected_analysis["rf_frame_count"] else False
    semantic_match = bool(semantic["matched"]) if expected_analysis["rf_frame_count"] else False
    shape = _signature_shape_match(signature, expected_analysis, observed_analysis)
    shape_match = bool(shape["matched"])
    matched = bool(sequence_match["matched"] or semantic_match or shape_match)
    commands = _signature_commands_for_capture(signature.get("commands"), source)
    observed_commands = _signature_observed_commands(observed_analysis, exact, semantic, shape)
    return {
        "operation": operation,
        "expected_operation": signature.get("expected_operation"),
        "target_usb": signature.get("target_usb"),
        "matched": matched,
        "score": _capture_signature_match_score(
            sequence_matched=bool(sequence_match["matched"]),
            exact_match=exact_match,
            semantic_match=semantic_match,
            shape_match=shape_match,
            packet_hash_match_count=packet_hash_match_count,
            expected_packet_count=len(expected_packets),
        ),
        "packet_sequence_match": sequence_match,
        "packet_hash_match_count": packet_hash_match_count,
        "unique_packet_hash_match_count": unique_packet_hash_match_count,
        "expected_packet_count": len(expected_packets),
        "expected_rf_frame_count": expected_analysis["rf_frame_count"],
        "exact_match": exact_match,
        "semantic_match": semantic_match,
        "exact": _compact_frame_match(exact),
        "semantic": _compact_frame_match(semantic),
        "shape_match": shape_match,
        "shape": shape,
        "summary": signature.get("summary", {}),
        "commands": commands,
        "observed_commands": observed_commands,
    }


def _signature_expected_packets(signature: dict[str, Any]) -> list[bytes]:
    packet_hexes = signature.get("packet_hexes")
    if not isinstance(packet_hexes, list):
        return []
    packets: list[bytes] = []
    for value in packet_hexes:
        if not isinstance(value, str):
            continue
        try:
            packets.append(bytes.fromhex(value))
        except ValueError:
            continue
    return packets


def _packet_sequence_match(
    normalized_packets: list[tuple[int, bytes]],
    expected_packets: list[bytes],
) -> dict[str, Any]:
    if not expected_packets:
        return {"matched": False, "start_indices": [], "match_count": 0}
    observed = [packet for _index, packet in normalized_packets]
    original_indices = [index for index, _packet in normalized_packets]
    expected = list(expected_packets)
    start_indices: list[int] = []
    if len(expected) <= len(observed):
        for offset in range(0, len(observed) - len(expected) + 1):
            if observed[offset : offset + len(expected)] == expected:
                start_indices.append(original_indices[offset])
    return {
        "matched": bool(start_indices),
        "start_indices": start_indices[:12],
        "match_count": len(start_indices),
    }


def _capture_signature_match_score(
    *,
    sequence_matched: bool,
    exact_match: bool,
    semantic_match: bool,
    shape_match: bool,
    packet_hash_match_count: int,
    expected_packet_count: int,
) -> int:
    if sequence_matched:
        return 100
    if exact_match:
        return 95
    if semantic_match:
        return 90
    if shape_match:
        return 70
    if packet_hash_match_count and expected_packet_count:
        ratio = min(1.0, packet_hash_match_count / max(1, expected_packet_count))
        return max(10, round(ratio * 60))
    return 0


def _signature_shape_match(
    signature: dict[str, Any],
    expected_analysis: dict[str, Any],
    observed_analysis: dict[str, Any],
) -> dict[str, Any]:
    operation = str(signature.get("operation") or "")
    expected_frames = _logical_signature_frames(expected_analysis.get("rf_frames"))
    observed_frames = _logical_signature_frames(observed_analysis.get("rf_frames"))
    if not expected_frames:
        return {"matched": False, "match_count": 0, "matches": []}
    matches: list[dict[str, Any]] = []
    for expected_index, expected in enumerate(expected_frames):
        for observed_index, observed in enumerate(observed_frames):
            reason = _frame_shape_match_reason(operation, expected, observed)
            if reason is None:
                continue
            matches.append(
                {
                    "expected_index": expected_index,
                    "observed_index": observed_index,
                    "operation": observed.get("operation"),
                    "reason": reason,
                    **_shape_frame_parameter_summary(observed),
                }
            )
    return {
        "matched": bool(matches),
        "match_count": len(matches),
        "matches": matches[:12],
    }


def _logical_signature_frames(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    frames = [frame for frame in value if isinstance(frame, dict)]
    logical = [frame for frame in frames if _counts_as_logical_operation(frame)]
    return logical or frames


def _frame_shape_match_reason(
    signature_operation: str,
    expected: dict[str, Any],
    observed: dict[str, Any],
) -> str | None:
    observed_operation = str(observed.get("operation") or "")
    expected_operation = str(expected.get("operation") or "")
    expected_pwm = _frame_pwm_values(expected)
    observed_pwm = _frame_pwm_values(observed)

    if signature_operation == "pwm-sync-enable":
        if observed_operation == "live-pwm-sync" or observed_pwm == [6, 6, 6, 6]:
            return "operation live-pwm-sync / PWM tuple [6,6,6,6]"
        return None
    if signature_operation == "pwm-sync-disable":
        if observed_operation == "live-pwm" and expected_pwm and observed_pwm == expected_pwm:
            return "direct fallback PWM tuple matched"
        return None
    if signature_operation == "pwm":
        if observed_operation == "live-pwm" and observed_pwm and observed_pwm != [6, 6, 6, 6]:
            return "direct PWM tuple observed"
        return None
    if signature_operation == "pwm-mirror":
        if observed_operation == "live-pwm-mirror":
            return "operation live-pwm-mirror"
        if observed_operation == "live-pwm" and expected_pwm and observed_pwm == expected_pwm:
            return "direct PWM tuple matched mirror value"
        return None
    if signature_operation in {"bind", "unbind"}:
        if observed_operation == expected_operation:
            return f"operation {observed_operation}"
        return None
    if signature_operation in {"rgb-static-red", "rgb-off"}:
        expected_color = _frame_static_color(expected)
        observed_color = _frame_static_color(observed)
        if expected_color is not None and observed_color == expected_color:
            return f"static RGB color {expected_color}"
        return None
    if signature_operation == "rainbow":
        expected_rainbow = _frame_rainbow_args(expected)
        observed_rainbow = _frame_rainbow_args(observed)
        if expected_rainbow is not None and observed_rainbow == expected_rainbow:
            return "generated rainbow parameters matched"
        return None
    return None


def _shape_frame_parameter_summary(frame: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in (
        "target_mac",
        "master_mac",
        "channel",
        "outer_rx_type",
        "payload_channel",
        "rx_type",
        "pwm_values",
        "motherboard_pwm",
        "effect_index",
        "frame_count",
        "led_count",
        "interval_ms",
    ):
        if key in frame:
            summary[key] = frame[key]
    static_color = _frame_static_color(frame)
    if static_color is not None:
        summary["static_color"] = static_color
    rainbow = _frame_rainbow_args(frame)
    if rainbow is not None:
        summary["rainbow"] = rainbow
    return summary


def _frame_static_color(frame: dict[str, Any]) -> list[int] | None:
    rgb_payload = frame.get("rgb_payload")
    if not isinstance(rgb_payload, dict):
        return None
    color = rgb_payload.get("static_color")
    if isinstance(color, list) and len(color) == 3 and all(isinstance(component, int) for component in color):
        return [max(0, min(255, int(component))) for component in color]
    return None


def _frame_rainbow_args(frame: dict[str, Any]) -> dict[str, int] | None:
    rgb_payload = frame.get("rgb_payload")
    if not isinstance(rgb_payload, dict):
        return None
    return _rgb_payload_rainbow_args(rgb_payload, frame)


def _compact_frame_match(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "matched": bool(match.get("matched")),
        "matched_count": int(match.get("matched_count", 0)) if isinstance(match.get("matched_count", 0), int) else 0,
        "missing_count": len(match.get("missing", [])) if isinstance(match.get("missing"), list) else 0,
        "unmatched_observed_count": len(match.get("unmatched_observed", [])) if isinstance(match.get("unmatched_observed"), list) else 0,
    }


def _signature_commands_for_capture(value: Any, source: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    commands: dict[str, Any] = {}
    for key, command in value.items():
        payload = _command_payload(command)
        if payload is None:
            continue
        argv = [
            (source or "<capture>") if part == "<capture>" else part
            for part in payload["argv"]
        ]
        commands[str(key)] = _command_detail(argv)
    return commands


def _signature_observed_commands(
    observed_analysis: dict[str, Any],
    exact: dict[str, Any],
    semantic: dict[str, Any],
    shape: dict[str, Any],
) -> dict[str, Any]:
    frames = observed_analysis.get("rf_frames")
    if not isinstance(frames, list):
        return {"items": [], "dry_run_commands": [], "compare_capture_commands": []}
    indexes = _matched_observed_indexes(exact, semantic, shape)
    items: list[dict[str, Any]] = []
    dry_run_commands: list[str] = []
    compare_capture_commands: list[str] = []
    for index in indexes:
        if index < 0 or index >= len(frames):
            continue
        frame = frames[index]
        if not isinstance(frame, dict):
            continue
        hint = frame.get("replay_hint")
        if not isinstance(hint, dict):
            continue
        item: dict[str, Any] = {
            "observed_index": index,
            "operation": frame.get("operation"),
        }
        dry_run = _command_payload(hint.get("dry_run"))
        if dry_run is not None:
            item["dry_run"] = dry_run
            dry_run_commands.append(str(dry_run["command"]))
        compare_capture = _command_payload(hint.get("compare_capture"))
        if compare_capture is not None:
            item["compare_capture"] = compare_capture
            compare_capture_commands.append(str(compare_capture["command"]))
        decoded_args = hint.get("decoded_args")
        if isinstance(decoded_args, dict):
            item["decoded_args"] = decoded_args
        target = hint.get("target")
        if isinstance(target, dict):
            item["target"] = target
        items.append(item)
    return {
        "items": items,
        "dry_run_commands": _unique_preserve_order(dry_run_commands),
        "compare_capture_commands": _unique_preserve_order(compare_capture_commands),
    }


def _matched_observed_indexes(
    exact: dict[str, Any],
    semantic: dict[str, Any],
    shape: dict[str, Any],
) -> list[int]:
    indexes: list[int] = []
    for match in _dict_list(exact.get("matches")):
        index = match.get("observed_index")
        if isinstance(index, int):
            indexes.append(index)
    for match in _dict_list(semantic.get("matches")):
        index = match.get("observed_index")
        if isinstance(index, int):
            indexes.append(index)
    for match in _dict_list(shape.get("matches")):
        index = match.get("observed_index")
        if isinstance(index, int):
            indexes.append(index)
    return _unique_preserve_order(indexes)


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _protocol_catalog_target(*, bound: bool) -> WirelessDeviceInfo:
    return WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60" if bound else "00:00:00:00:00:00",
        channel=8,
        rx_type=3 if bound else 0,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )


def _protocol_catalog_target_args(target: WirelessDeviceInfo) -> list[str]:
    return [
        "--mac",
        target.mac,
        "--master-mac",
        target.master_mac,
        "--channel",
        str(target.channel),
        "--rx-type",
        str(target.rx_type),
        "--device-type",
        str(target.device_type),
        "--fan-count",
        str(target.fan_count),
        "--sequence",
        str(target.command_sequence),
    ]


def _protocol_signature_item(
    operation: str,
    packets: Iterable[bytes],
    *,
    usb_role: str = "sender",
    expected_operation: str | None = None,
    device: WirelessDeviceInfo | None = None,
    commands: dict[str, dict[str, Any]] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    packet_list = list(packets)
    analysis = analyze_capture_packets(packet_list, source=f"catalog:{operation}")
    rf_frames = analysis.get("rf_frames") if isinstance(analysis.get("rf_frames"), list) else []
    item: dict[str, Any] = {
        "operation": operation,
        "expected_operation": expected_operation,
        "target_usb": _usb_target_payload(usb_role),
        "packet_count": len(packet_list),
        "packet_sizes": [len(packet) for packet in packet_list],
        "packet_size_counts": dict(sorted(Counter(len(packet) for packet in packet_list).items())),
        "combined_sha256": hashlib.sha256(b"".join(packet_list)).hexdigest(),
        "packet_sha256": [hashlib.sha256(packet).hexdigest() for packet in packet_list],
        "packet_hexes": [packet.hex() for packet in packet_list],
        "packet_hex_prefixes": _packet_hex_prefixes(packet_list),
        "packet_hex_suffixes": _packet_hex_suffixes(packet_list),
        "first_packet_hex": packet_list[0].hex() if packet_list else "",
        "last_packet_hex": packet_list[-1].hex() if packet_list else "",
        "rf_frame_count": len(rf_frames),
        "rf_payload_prefixes": _rf_payload_prefixes(rf_frames),
        "rf_payload_sha256": _rf_payload_sha256s(rf_frames),
        "summary": analysis.get("summary", {}),
    }
    if device is not None:
        item["device_context"] = _device_payload(device)
    if commands:
        item["commands"] = commands
    if notes:
        item["notes"] = notes
    return item


def _usb_target_payload(role: str) -> dict[str, Any]:
    if role == "receiver":
        vid, pid = RF_RECEIVER_VID, RF_RECEIVER_PID
    else:
        role = "sender"
        vid, pid = RF_SENDER_VID, RF_SENDER_PID
    return {
        "role": role,
        "vid_pid": f"{vid:04x}:{pid:04x}",
        "label": KNOWN_USB_DEVICES.get((vid, pid), ""),
    }


def _command_detail(argv: list[str]) -> dict[str, Any]:
    return {
        "argv": list(argv),
        "command": " ".join(shlex.quote(str(part)) for part in argv),
    }


def _packet_hex_prefixes(packets: Iterable[bytes], *, byte_count: int = 32) -> list[str]:
    return _unique_preserve_order(packet[:byte_count].hex() for packet in packets)


def _packet_hex_suffixes(packets: Iterable[bytes], *, byte_count: int = 16) -> list[str]:
    return _unique_preserve_order(packet[-byte_count:].hex() for packet in packets if packet)


def _rf_payload_prefixes(frames: list[Any], *, byte_count: int = 32) -> list[str]:
    prefixes: list[str] = []
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        payload_hex = frame.get("payload_hex")
        if isinstance(payload_hex, str):
            prefixes.append(payload_hex[: byte_count * 2])
    return _unique_preserve_order(prefixes)


def _rf_payload_sha256s(frames: list[Any]) -> list[str]:
    hashes: list[str] = []
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        payload_hex = frame.get("payload_hex")
        if not isinstance(payload_hex, str):
            continue
        try:
            payload = bytes.fromhex(payload_hex)
        except ValueError:
            continue
        hashes.append(hashlib.sha256(payload).hexdigest())
    return _unique_preserve_order(hashes)


def _tool_argv(*args: str) -> list[str]:
    return ["python", "tools/lianli_wireless_probe.py", *[str(arg) for arg in args if str(arg)]]


def _target_cli_args(
    frame: dict[str, Any],
    context: dict[str, Any] | None,
    *,
    include_master: bool,
) -> list[str]:
    args: list[str] = []
    values = _target_payload(frame, context)
    field_args = (
        ("mac", "--mac"),
        ("master_mac", "--master-mac"),
        ("channel", "--channel"),
        ("rx_type", "--rx-type"),
        ("device_type", "--device-type"),
        ("fan_count", "--fan-count"),
        ("sequence", "--sequence"),
    )
    for field, cli_arg in field_args:
        if field == "master_mac" and not include_master:
            continue
        value = values.get(field)
        if value is None:
            continue
        args.extend((cli_arg, str(value)))
    return args


def _target_payload(frame: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    sequence = frame.get("sequence")
    if isinstance(sequence, int):
        sequence = max(0, sequence - 1)
    else:
        sequence = None
    master_mac = frame.get("master_mac")
    if _is_zero_mac(master_mac) and context is not None:
        master_mac = context.get("master_mac")
    elif _is_zero_mac(master_mac):
        master_mac = None
    return {
        "mac": frame.get("target_mac") or (context or {}).get("mac"),
        "master_mac": master_mac,
        "channel": frame.get("payload_channel") or frame.get("channel") or (context or {}).get("channel"),
        "rx_type": frame.get("rx_type") or frame.get("outer_rx_type") or (context or {}).get("rx_type"),
        "device_type": (context or {}).get("device_type"),
        "fan_count": (context or {}).get("fan_count"),
        "sequence": sequence,
    }


def _prior_snapshot_device_context(
    frame: dict[str, Any],
    snapshots: list[dict[str, Any]],
) -> dict[str, Any] | None:
    target_mac = str(frame.get("target_mac") or "").lower()
    frame_index = frame.get("first_packet_index")
    if not target_mac or not isinstance(frame_index, int):
        return None
    match: dict[str, Any] | None = None
    for snapshot in snapshots:
        snapshot_index = snapshot.get("index")
        if not isinstance(snapshot_index, int) or snapshot_index >= frame_index:
            continue
        for device in snapshot.get("devices", []) if isinstance(snapshot.get("devices"), list) else []:
            if not isinstance(device, dict):
                continue
            if str(device.get("mac") or "").lower() != target_mac:
                continue
            match = {"snapshot_index": snapshot_index, **device}
    return match


def _frame_pwm_values(frame: dict[str, Any]) -> list[int]:
    values = frame.get("pwm_values")
    if not isinstance(values, list):
        return []
    parsed: list[int] = []
    for value in values:
        if not isinstance(value, int):
            return []
        parsed.append(max(0, min(255, value)))
    return parsed


def _pwm_values_arg(values: list[int]) -> str:
    return ",".join(str(max(0, min(255, value))) for value in values)


def _is_zero_mac(value: Any) -> bool:
    return isinstance(value, str) and value.lower() == "00:00:00:00:00:00"


def _command_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    argv = value.get("argv")
    if not isinstance(argv, list) or not all(isinstance(part, str) for part in argv):
        return None
    payload = {"argv": list(argv), "command": shlex.join(argv)}
    expected_operation = value.get("expected_operation")
    if isinstance(expected_operation, str):
        payload["expected_operation"] = expected_operation
    return payload


def _unique_preserve_order(values: Iterable[Any]) -> list[Any]:
    seen: set[Any] = set()
    result: list[Any] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _summary_count(analysis: dict[str, Any], key: str) -> int:
    summary = analysis.get("summary")
    if not isinstance(summary, dict):
        return 0
    value = summary.get(key)
    return int(value) if isinstance(value, int) else 0


def _device_report(reports: dict[str, dict[str, Any]], mac: str) -> dict[str, Any]:
    if mac not in reports:
        reports[mac] = {
            "mac": mac,
            "snapshot_count": 0,
            "rf_frame_count": 0,
            "snapshot_indexes": set(),
            "rf_frame_indexes": set(),
            "master_macs": set(),
            "channels": set(),
            "rx_types": set(),
            "device_types": set(),
            "fan_counts": set(),
            "pwm_values": Counter(),
            "fan_rpm_values": Counter(),
            "rf_frame_operations": Counter(),
            "operations": Counter(),
            "usb_device_counts": Counter(),
            "usb_endpoint_counts": Counter(),
            "usb_target_counts": Counter(),
            "usb_frame_numbers": set(),
            "usb_time_relative_values": set(),
            "latest_snapshot_device": None,
        }
    return reports[mac]


def _operation_report(reports: dict[str, dict[str, Any]], operation: str) -> dict[str, Any]:
    if operation not in reports:
        reports[operation] = {
            "operation": operation,
            "count": 0,
            "frame_indexes": set(),
            "target_macs": set(),
            "master_macs": set(),
            "channels": set(),
            "outer_rx_types": set(),
            "payload_rx_types": set(),
            "payload_channels": set(),
            "sequence_values": set(),
            "pwm_values": Counter(),
            "motherboard_pwm_values": set(),
            "motherboard_pwm_snapshot_indexes": set(),
            "effect_indexes": set(),
            "packet_counts": set(),
            "led_counts": set(),
            "frame_counts": set(),
            "interval_ms_values": set(),
            "compressed_lengths": set(),
            "rgb_decode_statuses": Counter(),
            "rgb_static_colors": Counter(),
            "rgb_rainbow_generated": Counter(),
            "rgb_decoded_lengths": set(),
            "rgb_decoded_hashes": Counter(),
            "rgb_unique_color_counts": set(),
            "rgb_sequence_ids": set(),
            "rgb_sequence_frame_counts": set(),
            "rgb_first_packet_retransmit_counts": set(),
            "usb_device_counts": Counter(),
            "usb_endpoint_counts": Counter(),
            "usb_target_counts": Counter(),
            "usb_frame_numbers": set(),
            "usb_time_relative_values": set(),
        }
    return reports[operation]


def _add_device_snapshot_fields(report: dict[str, Any], device: dict[str, Any]) -> None:
    _add_if_int(report["channels"], device.get("channel"))
    _add_if_int(report["rx_types"], device.get("rx_type"))
    _add_if_int(report["device_types"], device.get("device_type"))
    _add_if_int(report["fan_counts"], device.get("fan_count"))
    master_mac = device.get("master_mac")
    if isinstance(master_mac, str) and master_mac:
        report["master_macs"].add(master_mac.lower())
    pwm_values = _int_tuple(device.get("pwm_values"))
    if pwm_values:
        report["pwm_values"][_tuple_key(pwm_values)] += 1
    fan_rpm = _int_tuple(device.get("fan_rpm"))
    if fan_rpm:
        report["fan_rpm_values"][_tuple_key(fan_rpm)] += 1


def _snapshot_device_state_payload(
    device: dict[str, Any],
    snapshot_index: Any = None,
) -> dict[str, Any]:
    mac = str(device.get("mac") or "").lower()
    if not mac:
        return {}
    payload: dict[str, Any] = {
        "mac": mac,
        "source": "receiver-snapshot",
    }
    master_mac = str(device.get("master_mac") or "").lower()
    if master_mac:
        payload["master_mac"] = master_mac
    for key in ("channel", "rx_type", "device_type", "fan_count", "command_sequence"):
        value = device.get(key)
        if isinstance(value, int):
            payload[key] = value
    for key in ("pwm_values", "fan_rpm"):
        values = _int_tuple(device.get(key))
        if values:
            payload[key] = list(values)
    raw_hex = str(device.get("raw_hex") or "")
    if raw_hex:
        payload["raw_hex"] = raw_hex
    effective_snapshot_index = snapshot_index if isinstance(snapshot_index, int) else device.get("snapshot_index")
    if isinstance(effective_snapshot_index, int):
        payload["snapshot_index"] = effective_snapshot_index
    return payload


def _add_device_frame_fields(
    report: dict[str, Any],
    frame: dict[str, Any],
    packet_metadata: dict[int, dict[str, Any]] | None = None,
) -> None:
    _add_if_int(report["channels"], frame.get("payload_channel") or frame.get("channel"))
    _add_if_int(report["rx_types"], frame.get("rx_type") or frame.get("outer_rx_type"))
    master_mac = frame.get("master_mac")
    if isinstance(master_mac, str) and master_mac and not _is_zero_mac(master_mac):
        report["master_macs"].add(master_mac.lower())
    pwm_values = _int_tuple(frame.get("pwm_values"))
    if pwm_values:
        report["pwm_values"][_tuple_key(pwm_values)] += 1
    _add_usb_metadata_report_fields(report, frame, packet_metadata or {})


def _add_operation_frame_fields(
    report: dict[str, Any],
    frame_index: int,
    frame: dict[str, Any],
    packet_metadata: dict[int, dict[str, Any]] | None = None,
) -> None:
    report["count"] += 1
    report["frame_indexes"].add(frame_index)
    target_mac = frame.get("target_mac")
    if isinstance(target_mac, str) and target_mac:
        report["target_macs"].add(target_mac.lower())
    master_mac = frame.get("master_mac")
    if isinstance(master_mac, str) and master_mac and not _is_zero_mac(master_mac):
        report["master_macs"].add(master_mac.lower())
    for source_key, target_key in (
        ("channel", "channels"),
        ("outer_rx_type", "outer_rx_types"),
        ("rx_type", "payload_rx_types"),
        ("payload_channel", "payload_channels"),
        ("sequence", "sequence_values"),
        ("motherboard_pwm", "motherboard_pwm_values"),
        ("motherboard_pwm_snapshot_index", "motherboard_pwm_snapshot_indexes"),
        ("effect_index", "effect_indexes"),
        ("packet_count", "packet_counts"),
        ("led_count", "led_counts"),
        ("frame_count", "frame_counts"),
        ("interval_ms", "interval_ms_values"),
        ("compressed_length", "compressed_lengths"),
    ):
        _add_if_int(report[target_key], frame.get(source_key))
    pwm_values = _int_tuple(frame.get("pwm_values"))
    if pwm_values:
        report["pwm_values"][_tuple_key(pwm_values)] += 1
    rgb_payload = frame.get("rgb_payload")
    if isinstance(rgb_payload, dict):
        sequence_id = frame.get("rgb_sequence_id")
        if isinstance(sequence_id, str) and sequence_id:
            report["rgb_sequence_ids"].add(sequence_id)
        status = rgb_payload.get("decode_status")
        if isinstance(status, str) and status:
            report["rgb_decode_statuses"][status] += 1
        static_color_hex = rgb_payload.get("static_color_hex")
        if isinstance(static_color_hex, str) and static_color_hex:
            report["rgb_static_colors"][static_color_hex] += 1
        rainbow_key = _rgb_rainbow_key_from_frame(frame, rgb_payload)
        if rainbow_key:
            report["rgb_rainbow_generated"][rainbow_key] += 1
        _add_if_int(report["rgb_decoded_lengths"], rgb_payload.get("decoded_length"))
        decoded_sha256 = rgb_payload.get("decoded_sha256")
        if isinstance(decoded_sha256, str) and decoded_sha256:
            report["rgb_decoded_hashes"][decoded_sha256] += 1
        unique_color_count = rgb_payload.get("unique_color_count")
        if isinstance(unique_color_count, int):
            report["rgb_unique_color_counts"].add(unique_color_count)
        elif isinstance(unique_color_count, str) and unique_color_count:
            report["rgb_unique_color_counts"].add(unique_color_count)
        _add_if_int(report["rgb_sequence_frame_counts"], rgb_payload.get("sequence_rf_frame_count"))
        _add_if_int(
            report["rgb_first_packet_retransmit_counts"],
            rgb_payload.get("first_packet_retransmit_count"),
        )
    _add_usb_metadata_report_fields(report, frame, packet_metadata or {})


def _add_usb_metadata_report_fields(
    report: dict[str, Any],
    frame: dict[str, Any],
    packet_metadata: dict[int, dict[str, Any]],
) -> None:
    for usb in _frame_usb_metadata(frame, packet_metadata):
        device_key = _transport_usb_device_key(usb)
        if device_key:
            report["usb_device_counts"][device_key] += 1
        endpoint_key = _transport_usb_endpoint_key(usb)
        if endpoint_key:
            report["usb_endpoint_counts"][endpoint_key] += 1
        if device_key and endpoint_key:
            report["usb_target_counts"][f"{device_key}|{endpoint_key}"] += 1
        frame_number = str(usb.get("frame_number") or "").strip()
        if frame_number:
            report["usb_frame_numbers"].add(frame_number)
        relative = _parse_float(usb.get("frame_time_relative"))
        if relative is not None:
            report["usb_time_relative_values"].add(relative)


def _rgb_rainbow_key_from_frame(frame: dict[str, Any], rgb_payload: dict[str, Any]) -> str:
    if rgb_payload.get("rainbow_generated_match") is not True:
        return ""
    led_count = _first_present_int(
        _positive_int(rgb_payload.get("rainbow_led_count")),
        _positive_int(rgb_payload.get("decoded_led_count")),
        _positive_int(frame.get("led_count")),
    )
    frame_count = _first_present_int(
        _positive_int(rgb_payload.get("rainbow_frame_count")),
        _positive_int(rgb_payload.get("decoded_frame_count")),
        _positive_int(frame.get("frame_count")),
    )
    interval_ms = _first_present_int(
        _nonnegative_int(rgb_payload.get("rainbow_interval_ms")),
        _nonnegative_int(frame.get("interval_ms")),
    )
    effect_index = _nonnegative_int(frame.get("effect_index"))
    if led_count is None or frame_count is None or interval_ms is None or effect_index is None:
        return ""
    return f"{led_count},{frame_count},{interval_ms},{effect_index}"


def _first_present_int(*values: int | None) -> int | None:
    for value in values:
        if value is not None:
            return value
    return None


def _frame_usb_metadata(
    frame: dict[str, Any],
    packet_metadata: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    chunk_indexes = frame.get("chunk_packet_indexes")
    first_packet_index = frame.get("first_packet_index")
    if not isinstance(chunk_indexes, list) and isinstance(first_packet_index, int):
        chunk_indexes = list(range(first_packet_index, first_packet_index + 4))
    items: list[dict[str, Any]] = []
    for index in chunk_indexes or []:
        if not isinstance(index, int):
            continue
        usb = _timeline_usb_metadata(index, packet_metadata)
        if usb:
            items.append(usb)
    return items


def _finalize_device_report(report: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "mac": report["mac"],
        "snapshot_count": report["snapshot_count"],
        "rf_frame_count": report["rf_frame_count"],
        "snapshot_indexes": sorted(report["snapshot_indexes"]),
        "rf_frame_indexes": sorted(report["rf_frame_indexes"]),
        "master_macs": sorted(report["master_macs"]),
        "channels": sorted(report["channels"]),
        "rx_types": sorted(report["rx_types"]),
        "device_types": sorted(report["device_types"]),
        "fan_counts": sorted(report["fan_counts"]),
        "pwm_values": dict(sorted(report["pwm_values"].items())),
        "fan_rpm_values": dict(sorted(report["fan_rpm_values"].items())),
        "rf_frame_operations": dict(sorted(report["rf_frame_operations"].items())),
        "operations": dict(sorted(report["operations"].items())),
        "usb_device_counts": dict(sorted(report["usb_device_counts"].items())),
        "usb_endpoint_counts": dict(sorted(report["usb_endpoint_counts"].items())),
        "usb_target_counts": dict(sorted(report["usb_target_counts"].items())),
        "usb_frame_numbers": _sorted_numeric_strings(report["usb_frame_numbers"]),
        **_time_range_payload(report["usb_time_relative_values"], prefix="usb_time_relative"),
    }
    latest_snapshot = report.get("latest_snapshot_device")
    if isinstance(latest_snapshot, dict) and latest_snapshot:
        payload["latest_snapshot_device"] = dict(latest_snapshot)
    return payload


def _finalize_operation_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "count": report["count"],
        "frame_indexes": sorted(report["frame_indexes"]),
        "target_macs": sorted(report["target_macs"]),
        "master_macs": sorted(report["master_macs"]),
        "channels": sorted(report["channels"]),
        "outer_rx_types": sorted(report["outer_rx_types"]),
        "payload_rx_types": sorted(report["payload_rx_types"]),
        "payload_channels": sorted(report["payload_channels"]),
        "sequence_values": sorted(report["sequence_values"]),
        "pwm_values": dict(sorted(report["pwm_values"].items())),
        "motherboard_pwm_values": sorted(report["motherboard_pwm_values"]),
        "motherboard_pwm_snapshot_indexes": sorted(report["motherboard_pwm_snapshot_indexes"]),
        "effect_indexes": sorted(report["effect_indexes"]),
        "packet_counts": sorted(report["packet_counts"]),
        "led_counts": sorted(report["led_counts"]),
        "frame_counts": sorted(report["frame_counts"]),
        "interval_ms_values": sorted(report["interval_ms_values"]),
        "compressed_length_min": min(report["compressed_lengths"]) if report["compressed_lengths"] else None,
        "compressed_length_max": max(report["compressed_lengths"]) if report["compressed_lengths"] else None,
        "rgb_decode_statuses": dict(sorted(report["rgb_decode_statuses"].items())),
        "rgb_static_colors": dict(sorted(report["rgb_static_colors"].items())),
        "rgb_rainbow_generated": dict(sorted(report["rgb_rainbow_generated"].items())),
        "rgb_decoded_lengths": sorted(report["rgb_decoded_lengths"]),
        "rgb_decoded_hashes": dict(sorted(report["rgb_decoded_hashes"].items())),
        "rgb_unique_color_counts": sorted(report["rgb_unique_color_counts"], key=str),
        "rgb_sequence_count": len(report["rgb_sequence_ids"]),
        "rgb_sequence_frame_counts": sorted(report["rgb_sequence_frame_counts"]),
        "rgb_first_packet_retransmit_counts": sorted(report["rgb_first_packet_retransmit_counts"]),
        "usb_device_counts": dict(sorted(report["usb_device_counts"].items())),
        "usb_endpoint_counts": dict(sorted(report["usb_endpoint_counts"].items())),
        "usb_target_counts": dict(sorted(report["usb_target_counts"].items())),
        "usb_frame_numbers": _sorted_numeric_strings(report["usb_frame_numbers"]),
        "linux_live_write_targets": _operation_linux_live_write_targets(report),
        **_time_range_payload(report["usb_time_relative_values"], prefix="usb_time_relative"),
    }


def _protocol_linux_live_write_targets(
    operations: dict[str, dict[str, Any]],
    devices: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for operation, report in sorted(operations.items()):
        for target in report.get("linux_live_write_targets", []) if isinstance(report.get("linux_live_write_targets"), list) else []:
            if not isinstance(target, dict):
                continue
            key = (
                operation,
                str(target.get("vid_pid") or ""),
                str(target.get("endpoint_key") or ""),
                str(target.get("write_endpoint") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            payload = dict(target)
            payload["operation"] = operation
            target_macs = report.get("target_macs")
            if isinstance(target_macs, list) and target_macs:
                payload["target_macs"] = list(target_macs)
            channels = report.get("channels")
            if isinstance(channels, list) and channels:
                payload["channels"] = list(channels)
            outer_rx_types = report.get("outer_rx_types")
            if isinstance(outer_rx_types, list) and outer_rx_types:
                payload["rx_types"] = list(outer_rx_types)
                payload["outer_rx_types"] = list(outer_rx_types)
            payload_rx_types = report.get("payload_rx_types")
            if isinstance(payload_rx_types, list) and payload_rx_types:
                payload["payload_rx_types"] = list(payload_rx_types)
                if "rx_types" not in payload:
                    payload["rx_types"] = list(payload_rx_types)
            payload_channels = report.get("payload_channels")
            if isinstance(payload_channels, list) and payload_channels:
                payload["payload_channels"] = list(payload_channels)
            master_macs = report.get("master_macs")
            if isinstance(master_macs, list) and master_macs:
                payload["master_macs"] = list(master_macs)
            pwm_values = report.get("pwm_values")
            if isinstance(pwm_values, dict) and pwm_values:
                payload["pwm_values"] = dict(pwm_values)
            for field in ("effect_indexes", "led_counts", "frame_counts", "interval_ms_values", "rgb_sequence_frame_counts"):
                values = report.get(field)
                if isinstance(values, list) and values:
                    payload[field] = list(values)
            rgb_static_colors = report.get("rgb_static_colors")
            if isinstance(rgb_static_colors, dict) and rgb_static_colors:
                payload["rgb_static_colors"] = dict(rgb_static_colors)
            rgb_rainbow_generated = report.get("rgb_rainbow_generated")
            if isinstance(rgb_rainbow_generated, dict) and rgb_rainbow_generated:
                payload["rgb_rainbow_generated"] = dict(rgb_rainbow_generated)
            contexts = _linux_live_write_runtime_contexts(payload, devices)
            if contexts:
                payload["runtime_contexts"] = contexts
            targets.append(payload)
    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        targets,
        key=lambda item: (
            confidence_rank.get(str(item.get("confidence") or ""), 9),
            -int(item.get("packet_count", 0) or 0),
            str(item.get("operation") or ""),
            str(item.get("endpoint_key") or ""),
        ),
    )


def _operation_linux_live_write_targets(report: dict[str, Any]) -> list[dict[str, Any]]:
    target_counts = report.get("usb_target_counts")
    if isinstance(target_counts, Counter):
        pairs = list(target_counts.items())
    elif isinstance(target_counts, dict):
        pairs = [(str(key), int(value)) for key, value in target_counts.items() if isinstance(value, int)]
    else:
        pairs = []
    targets = [
        _linux_live_write_target_from_pair(str(pair_key), int(count))
        for pair_key, count in pairs
    ]
    targets = [target for target in targets if target is not None]
    if not targets:
        targets = _linux_live_write_targets_from_separate_counts(report)
    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        targets,
        key=lambda item: (
            confidence_rank.get(str(item.get("confidence") or ""), 9),
            -int(item.get("packet_count", 0) or 0),
            str(item.get("vid_pid") or ""),
            str(item.get("endpoint_key") or ""),
        ),
    )


def _linux_live_write_target_from_pair(pair_key: str, count: int) -> dict[str, Any] | None:
    if "|" not in pair_key:
        return None
    device_key, endpoint_key = pair_key.split("|", 1)
    return _linux_live_write_target_payload(device_key, endpoint_key, count)


def _linux_live_write_targets_from_separate_counts(report: dict[str, Any]) -> list[dict[str, Any]]:
    device_counts = report.get("usb_device_counts")
    endpoint_counts = report.get("usb_endpoint_counts")
    if not isinstance(device_counts, (dict, Counter)) or not isinstance(endpoint_counts, (dict, Counter)):
        return []
    sender_key = f"{RF_SENDER_VID:04x}:{RF_SENDER_PID:04x}"
    ordered_devices = sorted(
        ((str(key), int(value)) for key, value in device_counts.items() if isinstance(value, int)),
        key=lambda item: (item[0] != sender_key, -item[1], item[0]),
    )
    if not ordered_devices:
        return []
    device_key, device_count = ordered_devices[0]
    targets: list[dict[str, Any]] = []
    for endpoint_key, count in endpoint_counts.items():
        target = _linux_live_write_target_payload(str(device_key), str(endpoint_key), min(int(count), device_count))
        if target is not None:
            target["correlation"] = "inferred-from-separate-device-endpoint-counts"
            targets.append(target)
    return targets


def _linux_live_write_target_payload(device_key: str, endpoint_key: str, count: int) -> dict[str, Any] | None:
    endpoint = _parse_transport_endpoint_key(endpoint_key)
    direction = str(endpoint.get("direction") or "").upper()
    if direction and direction != "OUT":
        return None
    write_endpoint = _normalize_endpoint_address(endpoint.get("endpoint_address"), direction=direction)
    if not write_endpoint:
        return None
    vid, pid = _transport_usb_vid_pid(device_key)
    role = _lianli_usb_role(device_key)
    confidence = "low"
    if role == "sender" and write_endpoint == "0x01":
        confidence = "high"
    elif role == "sender" or direction == "OUT":
        confidence = "medium"
    payload = {
        "vid_pid": device_key,
        "role": role,
        "label": KNOWN_USB_DEVICES.get((vid, pid), ""),
        "bus": endpoint.get("bus", ""),
        "device_address": endpoint.get("device_address", ""),
        "endpoint_key": endpoint_key,
        "write_endpoint": write_endpoint,
        "read_endpoint": "0x81" if role == "sender" else "",
        "transfer_type": endpoint.get("transfer_type", ""),
        "packet_count": max(0, int(count)),
        "confidence": confidence,
        "correlation": "same-packet-device-endpoint",
        "linux_hint": _linux_live_write_hint(device_key, write_endpoint, role),
    }
    if role != "sender":
        payload["note"] = "Not the known 0416:8040 RF sender; validate before attempting live writes."
    return payload


def _linux_live_write_runtime_contexts(
    target: dict[str, Any],
    devices: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    macs = sorted(_strings_from_list(target.get("target_macs")))
    channels = _ints_from_list(target.get("channels"))
    rx_types = _ints_from_list(target.get("rx_types"))
    master_macs = sorted(_strings_from_list(target.get("master_macs")))
    if not macs:
        return []
    context: dict[str, Any] = {"mac": macs[0]}
    if len(channels) == 1:
        context["channel"] = channels[0]
    if len(rx_types) == 1:
        context["rx_type"] = rx_types[0]
    if len(master_macs) == 1:
        context["master_mac"] = master_macs[0]
    snapshot_state = _snapshot_device_state_for_mac(str(context.get("mac") or ""), devices)
    if snapshot_state:
        _merge_runtime_context_snapshot_fields(context, snapshot_state)
    context["confidence"] = "high" if {"mac", "channel", "rx_type"} <= set(context) else "partial"
    context["source"] = "capture-protocol-report"
    if len(macs) > 1:
        context["alternate_macs"] = macs[1:]
    if len(channels) > 1:
        context["candidate_channels"] = channels
    if len(rx_types) > 1:
        context["candidate_rx_types"] = rx_types
    if len(master_macs) > 1:
        context["candidate_master_macs"] = master_macs
    return [context]


def _snapshot_device_state_for_mac(
    mac: str,
    devices: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    if not mac or not isinstance(devices, dict):
        return {}
    device = devices.get(mac.lower())
    if not isinstance(device, dict):
        return {}
    snapshot = device.get("latest_snapshot_device")
    if isinstance(snapshot, dict) and snapshot:
        return dict(snapshot)
    if isinstance(device.get("device_type"), int) or device.get("raw_hex"):
        return dict(device)
    return _snapshot_device_state_payload(device)


def _merge_runtime_context_snapshot_fields(
    context: dict[str, Any],
    snapshot_state: dict[str, Any],
) -> None:
    if not snapshot_state:
        return
    for key in (
        "device_type",
        "fan_count",
        "pwm_values",
        "fan_rpm",
        "command_sequence",
        "raw_hex",
        "snapshot_index",
    ):
        if key in snapshot_state and snapshot_state[key] not in (None, "", []):
            context[key] = snapshot_state[key]
    source = str(snapshot_state.get("source") or "")
    if source:
        context["snapshot_source"] = source


def _parse_transport_endpoint_key(endpoint_key: str) -> dict[str, str]:
    parts = (endpoint_key.split("/") + ["", "", "", "", ""])[:5]
    return {
        "bus": parts[0],
        "device_address": parts[1],
        "endpoint_address": parts[2],
        "direction": parts[3],
        "transfer_type": parts[4],
    }


def _normalize_endpoint_address(value: Any, *, direction: str = "") -> str:
    text = str(value or "").strip().lower()
    if not text or text == "?":
        return ""
    try:
        number = int(text, 16) if text.startswith("0x") else int(text)
    except ValueError:
        return text
    if not text.startswith("0x") and direction.upper() == "IN" and number < 0x80:
        number |= 0x80
    return f"0x{max(0, min(0xFF, number)):02x}"


def _lianli_usb_role(device_key: str) -> str:
    vid, pid = _transport_usb_vid_pid(device_key)
    if (vid, pid) == (RF_SENDER_VID, RF_SENDER_PID):
        return "sender"
    if (vid, pid) == (RF_RECEIVER_VID, RF_RECEIVER_PID):
        return "receiver"
    return ""


def _linux_live_write_hint(device_key: str, write_endpoint: str, role: str) -> str:
    vid, pid = _transport_usb_vid_pid(device_key)
    if not vid or not pid:
        return ""
    read_endpoint = "0x81" if role == "sender" else "<read-endpoint>"
    return (
        "PyUsbEndpointTransport("
        f"vid=0x{vid:04x}, pid=0x{pid:04x}, "
        f"write_endpoint={write_endpoint}, read_endpoint={read_endpoint})"
    )


def _add_if_int(values: set[int], value: Any) -> None:
    if isinstance(value, int):
        values.add(value)


def _int_tuple(value: Any) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[int] = []
    for item in value:
        if not isinstance(item, int):
            return ()
        result.append(max(0, min(65535, item)))
    return tuple(result)


def _tuple_key(values: tuple[int, ...]) -> str:
    return ",".join(str(value) for value in values)


def _sorted_numeric_strings(values: Iterable[Any]) -> list[str]:
    return sorted(
        (str(value) for value in values),
        key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value),
    )


def _time_range_payload(values: Iterable[Any], *, prefix: str) -> dict[str, Any]:
    parsed = sorted(value for value in (_parse_float(value) for value in values) if value is not None)
    if not parsed:
        return {}
    start = parsed[0]
    end = parsed[-1]
    return {
        f"{prefix}_start_s": _round_time_seconds(start),
        f"{prefix}_end_s": _round_time_seconds(end),
        f"{prefix}_span_s": _round_time_seconds(end - start),
    }


def _read_capture_text(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise LianLiWirelessError(f"unable to read capture file {path}: {error}") from error
    if raw.startswith(PCAPNG_MAGIC):
        raise LianLiWirelessError(_pcap_tshark_missing_message(path))
    if raw[:4] in PCAP_MAGIC_VALUES:
        raise LianLiWirelessError(_pcap_tshark_missing_message(path))
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise LianLiWirelessError(
        "capture analyzer expects a text/JSON hex export, not a raw binary file"
    )


def _capture_summary_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        raise LianLiWirelessError(f"capture path does not exist: {path}")
    if not path.is_dir():
        raise LianLiWirelessError(f"capture path is not a file or directory: {path}")
    return sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file() and candidate.suffix.lower() in CAPTURE_SUMMARY_SUFFIXES
    )


def _capture_summary_item(root: Path, path: Path, analysis: dict[str, Any]) -> dict[str, Any]:
    summary = analysis.get("summary", {}) if isinstance(analysis.get("summary"), dict) else {}
    score = _capture_candidate_score(summary)
    commands = [
        _tool_command("capture-triage-report", str(path)),
        _tool_command("analyze-capture", str(path)),
        _tool_command("capture-replay-plan", str(path)),
        _tool_command("capture-protocol-report", str(path)),
        _tool_command("capture-timeline-report", str(path)),
    ]
    item: dict[str, Any] = {
        "path": _capture_summary_display_path(root, path),
        "size": _safe_file_size(path),
        "packet_count": int(analysis.get("packet_count", 0) or 0),
        "rf_frame_count": int(analysis.get("rf_frame_count", 0) or 0),
        "receiver_snapshot_count": _summary_count(analysis, "snapshot_count"),
        "master_response_count": _summary_count(analysis, "master_response_count"),
        "replay_hint_count": _summary_count(analysis, "replay_hint_count"),
        "kinds": dict(sorted(_counter_from_mapping(summary.get("kinds")).items())),
        "rf_operations": dict(sorted(_counter_from_mapping(summary.get("rf_operations")).items())),
        "rf_frame_operations": dict(sorted(_counter_from_mapping(summary.get("rf_frame_operations")).items())),
        "receiver_macs": sorted(_strings_from_list(summary.get("receiver_macs"))),
        "master_macs": sorted(_strings_from_list(summary.get("master_macs"))),
        "candidate_score": score,
        "recommended_commands": commands,
    }
    if score == 0 and item["packet_count"] == 0:
        item["note"] = "No supported L-Wireless USB payloads were found in this file."
    return item


def _capture_candidate_score(summary: dict[str, Any]) -> int:
    operations = _counter_from_mapping(summary.get("rf_operations"))
    frame_operations = _counter_from_mapping(summary.get("rf_frame_operations"))
    kinds = _counter_from_mapping(summary.get("kinds"))
    replay_hint_count = int(summary.get("replay_hint_count", 0) or 0)
    score = 0
    score += sum(frame_operations.values()) * 4
    score += sum(operations.values()) * 8
    score += replay_hint_count * 6
    score += int(summary.get("snapshot_count", 0) or 0) * 3
    score += int(summary.get("master_response_count", 0) or 0) * 2
    score += kinds.get("receiver-list-request", 0)
    for operation, weight in (
        ("live-bind", 30),
        ("live-unbind", 30),
        ("live-pwm-sync", 24),
        ("live-pwm-mirror", 22),
        ("live-pwm", 18),
        ("live-rgb", 14),
    ):
        if operations.get(operation, 0):
            score += weight
    return score


def _capture_summary_display_path(root: Path, path: Path) -> str:
    if root.is_dir():
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)
    return str(path)


def _capture_set_scenario_report(
    root: Path,
    capture_files: list[Path],
    scenario: dict[str, Any],
    triage_cache: dict[Path, dict[str, Any]],
    *,
    led_count: int,
    rainbow_frames: int,
    interval_ms: int,
    effect_index: int,
) -> dict[str, Any]:
    scenario_id = str(scenario.get("id") or "")
    capture_file = str(scenario.get("capture_file") or "")
    match = _capture_set_find_scenario_file(root, capture_files, scenario_id, capture_file)
    requirements = _capture_set_scenario_requirements(scenario_id)
    base_report: dict[str, Any] = {
        "id": scenario_id,
        "capture_file": capture_file,
        "goal": scenario.get("goal", ""),
        "expected_evidence": list(scenario.get("expected_evidence", []) if isinstance(scenario.get("expected_evidence"), list) else []),
        "windows_actions": list(scenario.get("windows_actions", []) if isinstance(scenario.get("windows_actions"), list) else []),
        "planned_linux_commands": list(scenario.get("linux_commands", []) if isinstance(scenario.get("linux_commands"), list) else []),
        "required_evidence": [requirement["label"] for requirement in requirements],
        "found": match is not None,
    }
    if match is None:
        base_report.update(
            {
                "status": "missing-capture",
                "matched_evidence": [],
                "missing_evidence": [requirement["label"] for requirement in requirements],
                "recommended_commands": [_tool_command("windows-capture-plan", "--capture-base", _capture_set_base_from_capture_file(capture_file))],
            }
        )
        return base_report

    base_report["path"] = _capture_summary_display_path(root, match)
    try:
        triage = triage_cache.get(match)
        if triage is None:
            triage = capture_triage_report_file(
                match,
                led_count=led_count,
                rainbow_frames=rainbow_frames,
                interval_ms=interval_ms,
                effect_index=effect_index,
            )
            triage_cache[match] = triage
    except Exception as error:
        base_report.update(
            {
                "status": "analysis-error",
                "error": str(error),
                "matched_evidence": [],
                "missing_evidence": [requirement["label"] for requirement in requirements],
                "recommended_commands": [_tool_command("capture-triage-report", str(match))],
            }
        )
        return base_report

    evidence_flags = _capture_set_evidence_flags(triage)
    matched, missing = _capture_set_match_requirements(requirements, evidence_flags)
    if requirements and not missing:
        status = "evidence-found"
    elif matched:
        status = "partial-evidence"
    else:
        status = "no-evidence"
    triage_summary = triage.get("summary", {}) if isinstance(triage.get("summary"), dict) else {}
    transport = triage.get("transport", {}) if isinstance(triage.get("transport"), dict) else {}
    live_write_targets = _capture_set_scenario_live_targets(triage)
    base_report.update(
        {
            "status": status,
            "triage_status": triage.get("status"),
            "matched_evidence": matched,
            "missing_evidence": missing,
            "summary": {
                "packet_count": triage_summary.get("packet_count", 0),
                "rf_frame_count": triage_summary.get("rf_frame_count", 0),
                "receiver_snapshot_count": triage_summary.get("receiver_snapshot_count", 0),
                "matched_operations": triage_summary.get("matched_operations", []),
                "rf_operations": triage_summary.get("rf_operations", {}),
                "linux_live_write_target_count": triage_summary.get("linux_live_write_target_count", 0),
            },
            "usb": {
                "lianli_usb_targets": transport.get("lianli_usb_targets", {}),
                "usb_device_counts": transport.get("usb_device_counts", {}),
                "usb_endpoint_counts": transport.get("usb_endpoint_counts", {}),
            },
            "linux_live_write_targets": live_write_targets,
            "recommended_commands": _capture_set_scenario_commands(match, triage),
            "triage": triage,
        }
    )
    return base_report


def _capture_set_cross_scenario_deltas(scenario_reports: list[dict[str, Any]]) -> dict[str, Any]:
    found_reports = [
        report
        for report in scenario_reports
        if isinstance(report, dict) and report.get("found") and isinstance(report.get("summary"), dict)
    ]
    operation_index = _capture_set_cross_scenario_index(found_reports, "rf_operations")
    signature_index = _capture_set_cross_scenario_signature_index(found_reports)
    parameter_index = _capture_set_cross_scenario_parameter_index(found_reports)
    scenario_deltas = [
        _capture_set_scenario_delta(report, operation_index, signature_index, parameter_index)
        for report in found_reports
    ]
    notes: list[str] = []
    if len(found_reports) < 2:
        notes.append("Need at least two captured scenarios to compare protocol deltas.")
    if not operation_index:
        notes.append("No decoded RF operation deltas are available yet.")
    else:
        unique_count = sum(1 for item in operation_index if item.get("scenario_count") == 1)
        notes.append(f"{unique_count} RF operation(s) are unique to a single captured scenario.")
    if parameter_index:
        unique_parameter_count = sum(1 for item in parameter_index if item.get("scenario_count") == 1)
        notes.append(f"{unique_parameter_count} protocol parameter value(s) are unique to a single captured scenario.")
    return {
        "status": "ready" if len(found_reports) >= 2 else "needs-more-captures",
        "found_scenario_count": len(found_reports),
        "rf_operation_index": operation_index,
        "signature_index": signature_index,
        "parameter_index": parameter_index,
        "scenario_deltas": scenario_deltas,
        "notes": notes,
    }


def _capture_set_cross_scenario_index(
    scenario_reports: list[dict[str, Any]],
    summary_key: str,
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for report in scenario_reports:
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        counts = _counter_from_mapping(summary.get(summary_key))
        for name, count in counts.items():
            bucket = buckets.setdefault(
                name,
                {
                    "operation": name,
                    "total_count": 0,
                    "scenario_ids": [],
                    "capture_paths": [],
                    "scenario_counts": {},
                },
            )
            scenario_id = str(report.get("id") or "")
            path = str(report.get("path") or report.get("capture_file") or "")
            bucket["total_count"] += count
            bucket["scenario_counts"][scenario_id] = bucket["scenario_counts"].get(scenario_id, 0) + count
            if scenario_id:
                bucket["scenario_ids"].append(scenario_id)
            if path:
                bucket["capture_paths"].append(path)
    result: list[dict[str, Any]] = []
    for name, bucket in buckets.items():
        scenario_ids = _unique_preserve_order(bucket["scenario_ids"])
        capture_paths = _unique_preserve_order(bucket["capture_paths"])
        result.append(
            {
                "operation": name,
                "total_count": int(bucket["total_count"]),
                "scenario_count": len(scenario_ids),
                "scenario_ids": scenario_ids,
                "capture_paths": capture_paths,
                "scenario_counts": dict(sorted(bucket["scenario_counts"].items())),
                "unique_to_scenario": scenario_ids[0] if len(scenario_ids) == 1 else "",
            }
        )
    return sorted(result, key=lambda item: (-int(item["scenario_count"]), str(item["operation"])))


def _capture_set_cross_scenario_signature_index(scenario_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for report in scenario_reports:
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        operations = _ordered_strings_from_list(summary.get("matched_operations"))
        for name in operations:
            bucket = buckets.setdefault(
                name,
                {
                    "signature": name,
                    "scenario_ids": [],
                    "capture_paths": [],
                },
            )
            scenario_id = str(report.get("id") or "")
            path = str(report.get("path") or report.get("capture_file") or "")
            if scenario_id:
                bucket["scenario_ids"].append(scenario_id)
            if path:
                bucket["capture_paths"].append(path)
    result: list[dict[str, Any]] = []
    for name, bucket in buckets.items():
        scenario_ids = _unique_preserve_order(bucket["scenario_ids"])
        result.append(
            {
                "signature": name,
                "scenario_count": len(scenario_ids),
                "scenario_ids": scenario_ids,
                "capture_paths": _unique_preserve_order(bucket["capture_paths"]),
                "unique_to_scenario": scenario_ids[0] if len(scenario_ids) == 1 else "",
            }
        )
    return sorted(result, key=lambda item: (-int(item["scenario_count"]), str(item["signature"])))


def _capture_set_cross_scenario_parameter_index(scenario_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for report in scenario_reports:
        scenario_id = str(report.get("id") or "")
        path = str(report.get("path") or report.get("capture_file") or "")
        for item in _capture_set_scenario_parameter_items(report):
            key = (
                str(item.get("operation") or ""),
                str(item.get("field") or ""),
                str(item.get("value") or ""),
            )
            if not all(key):
                continue
            bucket = buckets.setdefault(
                key,
                {
                    "operation": key[0],
                    "field": key[1],
                    "value": key[2],
                    "total_count": 0,
                    "scenario_ids": [],
                    "capture_paths": [],
                    "scenario_counts": {},
                },
            )
            count = int(item.get("count", 1) or 1)
            bucket["total_count"] += count
            bucket["scenario_counts"][scenario_id] = bucket["scenario_counts"].get(scenario_id, 0) + count
            if scenario_id:
                bucket["scenario_ids"].append(scenario_id)
            if path:
                bucket["capture_paths"].append(path)
    result: list[dict[str, Any]] = []
    for (_operation, _field, _value), bucket in buckets.items():
        scenario_ids = _unique_preserve_order(bucket["scenario_ids"])
        result.append(
            {
                "operation": bucket["operation"],
                "field": bucket["field"],
                "value": bucket["value"],
                "total_count": int(bucket["total_count"]),
                "scenario_count": len(scenario_ids),
                "scenario_ids": scenario_ids,
                "capture_paths": _unique_preserve_order(bucket["capture_paths"]),
                "scenario_counts": dict(sorted(bucket["scenario_counts"].items())),
                "unique_to_scenario": scenario_ids[0] if len(scenario_ids) == 1 else "",
            }
        )
    return sorted(
        result,
        key=lambda item: (
            str(item["operation"]),
            str(item["field"]),
            -int(item["scenario_count"]),
            str(item["value"]),
        ),
    )


def _capture_set_scenario_parameter_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    triage = report.get("triage") if isinstance(report.get("triage"), dict) else {}
    protocol = triage.get("protocol") if isinstance(triage.get("protocol"), dict) else {}
    operations = protocol.get("operations") if isinstance(protocol.get("operations"), dict) else {}
    items: list[dict[str, Any]] = []
    for operation, operation_report in operations.items():
        if not isinstance(operation_report, dict):
            continue
        operation_name = str(operation)
        for field in ("pwm_values", "rgb_static_colors", "rgb_rainbow_generated"):
            values = operation_report.get(field)
            if not isinstance(values, dict):
                continue
            for value, count in values.items():
                try:
                    parsed_count = int(count)
                except (TypeError, ValueError):
                    parsed_count = 1
                if str(value):
                    items.append(
                        {
                            "operation": operation_name,
                            "field": field,
                            "value": str(value),
                            "count": max(1, parsed_count),
                        }
                    )
        for field in (
            "motherboard_pwm_values",
            "effect_indexes",
            "led_counts",
            "frame_counts",
            "interval_ms_values",
            "rgb_sequence_frame_counts",
        ):
            values = operation_report.get(field)
            if not isinstance(values, list):
                continue
            for value in values:
                if value in (None, ""):
                    continue
                items.append(
                    {
                        "operation": operation_name,
                        "field": field,
                        "value": str(value),
                        "count": 1,
                    }
                )
    return items


def _capture_set_scenario_delta(
    report: dict[str, Any],
    operation_index: list[dict[str, Any]],
    signature_index: list[dict[str, Any]],
    parameter_index: list[dict[str, Any]],
) -> dict[str, Any]:
    scenario_id = str(report.get("id") or "")
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    operations = dict(sorted(_counter_from_mapping(summary.get("rf_operations")).items()))
    signatures = _ordered_strings_from_list(summary.get("matched_operations"))
    unique_operations = [
        item["operation"]
        for item in operation_index
        if item.get("unique_to_scenario") == scenario_id
    ]
    shared_operations = [
        operation
        for operation in operations
        if operation not in set(unique_operations)
    ]
    unique_signatures = [
        item["signature"]
        for item in signature_index
        if item.get("unique_to_scenario") == scenario_id
    ]
    shared_signatures = [
        signature
        for signature in signatures
        if signature not in set(unique_signatures)
    ]
    parameter_items = _capture_set_scenario_parameter_items(report)
    unique_parameter_keys = {
        (
            str(item.get("operation") or ""),
            str(item.get("field") or ""),
            str(item.get("value") or ""),
        )
        for item in parameter_index
        if item.get("unique_to_scenario") == scenario_id
    }
    unique_parameters = [
        item
        for item in parameter_items
        if (
            str(item.get("operation") or ""),
            str(item.get("field") or ""),
            str(item.get("value") or ""),
        )
        in unique_parameter_keys
    ]
    unique_parameter_labels = _capture_set_unique_parameter_labels(unique_parameters)
    return {
        "id": scenario_id,
        "status": str(report.get("status") or ""),
        "path": str(report.get("path") or report.get("capture_file") or ""),
        "rf_operations": operations,
        "unique_rf_operations": unique_operations,
        "shared_rf_operations": shared_operations,
        "matched_signatures": signatures,
        "unique_matched_signatures": unique_signatures,
        "shared_matched_signatures": shared_signatures,
        "parameter_evidence": parameter_items,
        "unique_parameter_evidence": unique_parameters,
        "unique_parameter_labels": unique_parameter_labels,
        "matched_evidence": list(report.get("matched_evidence", []) if isinstance(report.get("matched_evidence"), list) else []),
        "missing_evidence": list(report.get("missing_evidence", []) if isinstance(report.get("missing_evidence"), list) else []),
        "next_focus": _capture_set_scenario_delta_focus(report, unique_operations, unique_signatures, unique_parameters),
    }


def _capture_set_unique_parameter_labels(unique_parameters: list[dict[str, Any]]) -> list[str]:
    labels = [
        f"{item['operation']}.{item['field']}={item['value']}"
        for item in unique_parameters
        if item.get("operation") and item.get("field") and item.get("value")
    ]
    return _unique_preserve_order(labels)


def _capture_set_scenario_delta_focus(
    report: dict[str, Any],
    unique_operations: list[str],
    unique_signatures: list[str],
    unique_parameters: list[dict[str, Any]],
) -> str:
    parameter_labels = _capture_set_unique_parameter_labels(unique_parameters)
    if unique_operations:
        focus = "compare unique RF operation(s): " + ", ".join(unique_operations)
        if parameter_labels:
            focus += " with " + ", ".join(parameter_labels[:3])
        return focus
    if parameter_labels:
        return "inspect unique protocol parameter(s): " + ", ".join(parameter_labels[:3])
    if unique_signatures:
        return "inspect unique signature match(es): " + ", ".join(unique_signatures)
    missing = list(report.get("missing_evidence", []) if isinstance(report.get("missing_evidence"), list) else [])
    if missing:
        return "recapture or inspect missing evidence: " + ", ".join(str(item) for item in missing[:3])
    return "no scenario-specific delta yet; compare timeline metadata and USB endpoints"


def _capture_set_find_scenario_file(
    root: Path,
    capture_files: list[Path],
    scenario_id: str,
    capture_file: str,
) -> Path | None:
    expected = Path(capture_file)
    if root.is_file():
        return root if _capture_set_file_matches(root, expected, scenario_id) else None
    exact = root / capture_file
    if exact.exists() and exact.is_file():
        return exact
    matches = [
        path
        for path in capture_files
        if _capture_set_file_matches(path, expected, scenario_id)
    ]
    if not matches:
        return None
    return sorted(matches, key=_capture_set_file_sort_key)[0]


def _capture_set_file_matches(path: Path, expected: Path, scenario_id: str) -> bool:
    if path.name == expected.name:
        return True
    expected_stem = expected.stem
    path_stem = path.stem
    return bool(
        (expected_stem and path_stem == expected_stem)
        or (expected_stem and expected_stem in path_stem)
        or (scenario_id and scenario_id in path_stem)
    )


def _capture_set_file_sort_key(path: Path) -> tuple[int, str]:
    suffix_priority = {
        ".pcapng": 0,
        ".pcap": 1,
        ".json": 2,
        ".jsonl": 3,
        ".txt": 4,
        ".hex": 5,
        ".tsv": 6,
    }
    return (suffix_priority.get(path.suffix.lower(), 99), str(path))


def _capture_set_base_from_capture_file(capture_file: str) -> str:
    stem = Path(capture_file).stem
    match = re.match(r"(.+)-\d{2}-", stem)
    return match.group(1) if match else "lianli-v2117"


def _capture_set_scenario_requirements(scenario_id: str) -> list[dict[str, Any]]:
    return {
        "baseline": [
            {"label": "receiver-list-request", "any": {"kind:receiver-list-request", "signature:receiver-list-request"}},
            {"label": "receiver-snapshot", "any": {"kind:receiver-snapshot"}},
            {"label": "master-query", "any": {"kind:master-query-request", "kind:master-query-response", "signature:master-query-request"}},
        ],
        "direct-fan-speed": [
            {"label": "direct PWM RF frame", "any": {"operation:live-pwm", "signature:pwm"}},
            {"label": "non-sync PWM tuple", "any": {"pwm:non-sync"}},
        ],
        "motherboard-pwm-sync": [
            {"label": "motherboard PWM sync or fallback", "any": {"operation:live-pwm-sync", "signature:pwm-sync-enable", "operation:live-pwm"}},
        ],
        "rf-rebind": [
            {"label": "RF unbind", "any": {"operation:live-unbind", "signature:unbind"}},
            {"label": "RF bind", "any": {"operation:live-bind", "signature:bind"}},
        ],
        "sort-quick-sync": [
            {"label": "sort-triggered fan rewrite", "any": {"operation:live-pwm-sync", "operation:live-pwm", "operation:live-pwm-mirror", "signature:pwm-sync-enable", "signature:pwm"}},
        ],
        "lighting-static-and-off": [
            {"label": "RGB RF frame", "any": {"operation:live-rgb", "signature:rgb-static-red", "signature:rgb-off"}},
            {"label": "decoded static/off color", "any": {"rgb:static-color"}},
        ],
        "lighting-generated-rainbow": [
            {"label": "generated rainbow RGB RF frame", "any": {"rgb:generated-rainbow", "operation:live-rainbow"}},
            {"label": "decoded rainbow timing/LED parameters", "any": {"rgb:rainbow-parameters"}},
        ],
    }.get(scenario_id, [])


def _capture_set_evidence_flags(triage: dict[str, Any]) -> set[str]:
    flags: set[str] = set()
    summary = triage.get("summary", {}) if isinstance(triage.get("summary"), dict) else {}
    for kind in _counter_from_mapping(summary.get("decoded_kinds")):
        flags.add(f"kind:{kind}")
    for operation in _counter_from_mapping(summary.get("rf_operations")):
        flags.add(f"operation:{operation}")
    signature = triage.get("signature_match", {}) if isinstance(triage.get("signature_match"), dict) else {}
    for operation in signature.get("matched_operations", []) if isinstance(signature.get("matched_operations"), list) else []:
        if isinstance(operation, str):
            flags.add(f"signature:{operation}")
    protocol = triage.get("protocol", {}) if isinstance(triage.get("protocol"), dict) else {}
    operations = protocol.get("operations", {}) if isinstance(protocol.get("operations"), dict) else {}
    for operation, report in operations.items():
        if not isinstance(report, dict):
            continue
        flags.add(f"operation:{operation}")
        pwm_values = report.get("pwm_values", {})
        if isinstance(pwm_values, dict):
            for key in pwm_values:
                if key and key != "6,6,6,6":
                    flags.add("pwm:non-sync")
                if key == "6,6,6,6":
                    flags.add("pwm:sync-tuple")
        rgb_colors = report.get("rgb_static_colors", {})
        if isinstance(rgb_colors, dict) and rgb_colors:
            flags.add("rgb:static-color")
        rainbow_generated = report.get("rgb_rainbow_generated", {})
        if isinstance(rainbow_generated, dict) and rainbow_generated:
            flags.add("rgb:generated-rainbow")
            flags.add("rgb:rainbow-parameters")
            flags.add("operation:live-rainbow")
    transport = triage.get("transport", {}) if isinstance(triage.get("transport"), dict) else {}
    targets = transport.get("lianli_usb_targets", {}) if isinstance(transport.get("lianli_usb_targets"), dict) else {}
    if targets.get("sender_seen"):
        flags.add("usb:sender-seen")
    if targets.get("receiver_seen"):
        flags.add("usb:receiver-seen")
    return flags


def _capture_set_match_requirements(requirements: list[dict[str, Any]], flags: set[str]) -> tuple[list[str], list[str]]:
    matched: list[str] = []
    missing: list[str] = []
    for requirement in requirements:
        label = str(requirement.get("label") or "")
        any_flags = requirement.get("any")
        if isinstance(any_flags, set) and any(flag in flags for flag in any_flags):
            matched.append(label)
        else:
            missing.append(label)
    return matched, missing


def _capture_set_scenario_live_targets(triage: dict[str, Any]) -> list[dict[str, Any]]:
    targets = triage.get("linux_live_write_targets")
    if not isinstance(targets, list):
        return []
    return [
        {
            key: target[key]
            for key in (
                "operation",
                "vid_pid",
                "role",
                "endpoint_key",
                "write_endpoint",
                "read_endpoint",
                "packet_count",
                "confidence",
                "target_macs",
            )
            if isinstance(target, dict) and key in target
        }
        for target in targets
        if isinstance(target, dict)
    ]


def _capture_set_update_snapshot_devices(
    aggregate_devices: dict[str, dict[str, Any]],
    devices: Any,
    *,
    scenario_id: str,
    scenario_path: str,
) -> None:
    if not isinstance(devices, dict):
        return
    for mac, report in devices.items():
        if not isinstance(report, dict):
            continue
        state = report.get("latest_snapshot_device")
        if not isinstance(state, dict) or not state:
            state = _snapshot_device_state_payload(report)
        if not isinstance(state, dict) or not state:
            continue
        payload = dict(state)
        payload["mac"] = str(payload.get("mac") or mac).lower()
        if scenario_id:
            payload["snapshot_scenario_id"] = scenario_id
        if scenario_path:
            payload["snapshot_scenario_path"] = scenario_path
        existing = aggregate_devices.get(payload["mac"])
        if existing is not None and _snapshot_state_sort_key(existing) > _snapshot_state_sort_key(payload):
            continue
        aggregate_devices[payload["mac"]] = payload


def _snapshot_state_sort_key(state: dict[str, Any]) -> tuple[str, int]:
    snapshot_index = state.get("snapshot_index")
    return (
        "1" if state.get("raw_hex") and isinstance(state.get("device_type"), int) else "0",
        int(snapshot_index if isinstance(snapshot_index, int) else -1),
    )


def _capture_set_enrich_live_targets_with_snapshot_devices(
    live_write_targets: list[dict[str, Any]],
    snapshot_devices: dict[str, dict[str, Any]],
) -> None:
    if not snapshot_devices:
        return
    for target in live_write_targets:
        if not isinstance(target, dict):
            continue
        contexts = target.get("runtime_contexts")
        if not isinstance(contexts, list) or not contexts:
            contexts = _linux_live_write_runtime_contexts(target)
            target["runtime_contexts"] = contexts
        for context in contexts:
            if not isinstance(context, dict):
                continue
            state = _snapshot_device_state_for_mac(str(context.get("mac") or ""), snapshot_devices)
            if not state:
                continue
            _merge_runtime_context_snapshot_fields(context, state)
            for key in ("snapshot_scenario_id", "snapshot_scenario_path"):
                if key in state:
                    context[key] = state[key]


def _capture_set_update_live_targets(
    aggregate_targets: dict[tuple[str, str, str, str, str], dict[str, Any]],
    *,
    scenario_id: str,
    scenario_path: str,
    targets: Any,
) -> None:
    if not isinstance(targets, list):
        return
    for target in targets:
        if not isinstance(target, dict):
            continue
        key = (
            str(target.get("operation") or ""),
            str(target.get("vid_pid") or ""),
            str(target.get("endpoint_key") or ""),
            str(target.get("write_endpoint") or ""),
            str(target.get("confidence") or ""),
        )
        if not all(key):
            continue
        existing = aggregate_targets.setdefault(
            key,
            {
                "operation": key[0],
                "vid_pid": key[1],
                "endpoint_key": key[2],
                "write_endpoint": key[3],
                "confidence": key[4],
                "role": target.get("role", ""),
                "read_endpoint": target.get("read_endpoint", ""),
                "transfer_type": target.get("transfer_type", ""),
                "packet_count": 0,
                "scenario_ids": set(),
                "scenario_paths": set(),
                "target_macs": set(),
                "channels": set(),
                "rx_types": set(),
                "outer_rx_types": set(),
                "payload_rx_types": set(),
                "payload_channels": set(),
                "master_macs": set(),
                "runtime_contexts": [],
                "pwm_values": Counter(),
                "effect_indexes": set(),
                "led_counts": set(),
                "frame_counts": set(),
                "interval_ms_values": set(),
                "rgb_sequence_frame_counts": set(),
                "rgb_static_colors": Counter(),
                "rgb_rainbow_generated": Counter(),
                "linux_hint": target.get("linux_hint", ""),
            },
        )
        existing["packet_count"] = int(existing.get("packet_count", 0) or 0) + int(target.get("packet_count", 0) or 0)
        if scenario_id:
            existing["scenario_ids"].add(scenario_id)
        if scenario_path:
            existing["scenario_paths"].add(scenario_path)
        macs = target.get("target_macs")
        if isinstance(macs, list):
            existing["target_macs"].update(str(mac) for mac in macs if isinstance(mac, str) and mac)
        for key in ("channels", "rx_types", "outer_rx_types", "payload_rx_types", "payload_channels"):
            values = target.get(key)
            if isinstance(values, list):
                existing[key].update(value for value in _ints_from_list(values))
        master_macs = target.get("master_macs")
        if isinstance(master_macs, list):
            existing["master_macs"].update(
                str(mac).lower() for mac in master_macs if isinstance(mac, str) and mac
            )
        contexts = target.get("runtime_contexts")
        if isinstance(contexts, list):
            _capture_set_extend_runtime_contexts(existing["runtime_contexts"], contexts)
        pwm_values = target.get("pwm_values")
        if isinstance(pwm_values, dict):
            for key_text, count in pwm_values.items():
                try:
                    existing["pwm_values"][str(key_text)] += int(count)
                except (TypeError, ValueError):
                    continue
        for key in ("effect_indexes", "led_counts", "frame_counts", "interval_ms_values", "rgb_sequence_frame_counts"):
            values = target.get(key)
            if isinstance(values, list):
                existing[key].update(value for value in _ints_from_list(values))
        rgb_static_colors = target.get("rgb_static_colors")
        if isinstance(rgb_static_colors, dict):
            for key_text, count in rgb_static_colors.items():
                try:
                    existing["rgb_static_colors"][str(key_text)] += int(count)
                except (TypeError, ValueError):
                    continue
        rgb_rainbow_generated = target.get("rgb_rainbow_generated")
        if isinstance(rgb_rainbow_generated, dict):
            for key_text, count in rgb_rainbow_generated.items():
                try:
                    existing["rgb_rainbow_generated"][str(key_text)] += int(count)
                except (TypeError, ValueError):
                    continue


def _capture_set_extend_runtime_contexts(
    existing: list[dict[str, Any]],
    contexts: list[Any],
) -> None:
    seen = {
        (
            str(item.get("mac") or ""),
            str(item.get("channel") or ""),
            str(item.get("rx_type") or ""),
            str(item.get("master_mac") or ""),
        ): item
        for item in existing
        if isinstance(item, dict)
    }
    for context in contexts:
        if not isinstance(context, dict):
            continue
        payload = {
            key: context[key]
            for key in (
                "mac",
                "channel",
                "rx_type",
                "master_mac",
                "confidence",
                "source",
                "candidate_channels",
                "candidate_rx_types",
                "candidate_master_macs",
                "alternate_macs",
                "device_type",
                "fan_count",
                "pwm_values",
                "fan_rpm",
                "command_sequence",
                "raw_hex",
                "snapshot_index",
                "snapshot_source",
                "snapshot_scenario_id",
                "snapshot_scenario_path",
            )
            if key in context
        }
        key = (
            str(payload.get("mac") or ""),
            str(payload.get("channel") or ""),
            str(payload.get("rx_type") or ""),
            str(payload.get("master_mac") or ""),
        )
        if key in seen:
            _merge_runtime_context_snapshot_fields(seen[key], payload)
            for snapshot_key in ("snapshot_scenario_id", "snapshot_scenario_path"):
                if snapshot_key in payload:
                    seen[key][snapshot_key] = payload[snapshot_key]
            continue
        seen[key] = payload
        existing.append(payload)


def _capture_set_live_target_list(
    aggregate_targets: dict[tuple[str, str, str, str, str], dict[str, Any]]
) -> list[dict[str, Any]]:
    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    result: list[dict[str, Any]] = []
    for target in aggregate_targets.values():
        payload = {
            key: value
            for key, value in target.items()
            if key not in {
                "scenario_ids",
                "scenario_paths",
                "target_macs",
                "channels",
                "rx_types",
                "outer_rx_types",
                "payload_rx_types",
                "payload_channels",
                "master_macs",
                "pwm_values",
                "effect_indexes",
                "led_counts",
                "frame_counts",
                "interval_ms_values",
                "rgb_sequence_frame_counts",
                "rgb_static_colors",
                "rgb_rainbow_generated",
            }
        }
        payload["scenario_ids"] = sorted(target.get("scenario_ids", set()))
        payload["scenario_paths"] = sorted(target.get("scenario_paths", set()))
        payload["target_macs"] = sorted(target.get("target_macs", set()))
        for key in ("channels", "rx_types", "outer_rx_types", "payload_rx_types", "payload_channels"):
            values = target.get(key, set())
            if values:
                payload[key] = sorted(values)
        master_macs = target.get("master_macs", set())
        if master_macs:
            payload["master_macs"] = sorted(master_macs)
        pwm_values = target.get("pwm_values")
        if isinstance(pwm_values, Counter) and pwm_values:
            payload["pwm_values"] = dict(sorted(pwm_values.items()))
        for key in ("effect_indexes", "led_counts", "frame_counts", "interval_ms_values", "rgb_sequence_frame_counts"):
            values = target.get(key, set())
            if values:
                payload[key] = sorted(values)
        rgb_static_colors = target.get("rgb_static_colors")
        if isinstance(rgb_static_colors, Counter) and rgb_static_colors:
            payload["rgb_static_colors"] = dict(sorted(rgb_static_colors.items()))
        rgb_rainbow_generated = target.get("rgb_rainbow_generated")
        if isinstance(rgb_rainbow_generated, Counter) and rgb_rainbow_generated:
            payload["rgb_rainbow_generated"] = dict(sorted(rgb_rainbow_generated.items()))
        result.append(payload)
    return sorted(
        result,
        key=lambda item: (
            confidence_rank.get(str(item.get("confidence") or ""), 9),
            str(item.get("operation") or ""),
            str(item.get("endpoint_key") or ""),
        ),
    )


def _capture_set_linux_validation_plan(
    live_write_targets: list[dict[str, Any]],
    scenario_reports: list[dict[str, Any]],
    experiment_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    high_confidence = [
        target
        for target in live_write_targets
        if target.get("confidence") == "high" and target.get("role") == "sender"
    ]
    missing_scenarios = [
        {
            "id": str(report.get("id") or ""),
            "capture_file": str(report.get("capture_file") or ""),
            "missing_evidence": list(report.get("missing_evidence", []) if isinstance(report.get("missing_evidence"), list) else []),
        }
        for report in scenario_reports
        if report.get("status") == "missing-capture"
    ]
    hardware_validation = _capture_set_hardware_validation(experiment_summary or {})
    experiment_path = str((experiment_summary or {}).get("path") or ".cache/lianli")
    commands = [_tool_command("usb-capture-readiness")]
    if high_confidence:
        commands.append(_tool_command("validate-readonly", "--output-dir", ".cache/lianli/validation-live"))
        commands.extend(_capture_set_live_target_experiment_commands(high_confidence))
        commands.append(_tool_command("summarize-experiments", experiment_path))
        status = "ready-for-linux-readonly-and-guarded-write"
    elif live_write_targets:
        commands.append(_tool_command("validate-readonly", "--output-dir", ".cache/lianli/validation-live"))
        commands.append(_tool_command("summarize-experiments", experiment_path))
        status = "needs-high-confidence-sender-target"
    else:
        status = "needs-windows-capture-evidence"
    hardware_status = str(hardware_validation.get("status") or "")
    if hardware_status == "errors":
        status = "linux-validation-errors"
    elif hardware_status == "readonly-and-write-observed":
        status = "linux-readonly-and-guarded-write-observed"
    elif hardware_status == "readonly-and-write-gate-ready" and high_confidence:
        status = "linux-write-gate-ready-for-guarded-write"
    elif hardware_status == "readonly-observed" and high_confidence:
        status = "linux-readonly-observed-ready-for-guarded-write"
    elif hardware_status == "write-observed" and high_confidence:
        status = "linux-guarded-write-observed-needs-readonly"
    return {
        "status": status,
        "live_write_target_count": len(live_write_targets),
        "high_confidence_target_count": len(high_confidence),
        "hardware_validation": hardware_validation,
        "commands": _unique_preserve_order(commands),
        "missing_scenarios": missing_scenarios,
        "notes": _capture_set_linux_validation_notes(high_confidence, missing_scenarios, hardware_validation),
    }


def _capture_set_experiment_summary(experiment_dir: Path | None) -> dict[str, Any]:
    if experiment_dir is None:
        return {}
    path = experiment_dir.expanduser()
    try:
        return summarize_experiment_dir(path)
    except Exception as error:  # noqa: BLE001 - report generation should preserve the capture audit.
        return {
            "operation": "summarize-experiments",
            "path": str(path),
            "status": "error",
            "error": str(error),
            "hardware_validation": {
                "status": "summary-error",
                "validation_run_count": 0,
                "validation_error_count": 1,
                "safe_experiment_count": 0,
                "safe_effective_count": 0,
                "targets": [],
            },
        }


def _capture_set_hardware_validation(experiment_summary: dict[str, Any] | None) -> dict[str, Any]:
    if not experiment_summary:
        return {
            "status": "not-provided",
            "validation_run_count": 0,
            "validation_error_count": 0,
            "safe_experiment_count": 0,
            "safe_effective_count": 0,
            "targets": [],
            "receiver_validation_bundle_count": 0,
            "receiver_validation_bundle_error_count": 0,
            "write_gate_ready_count": 0,
            "write_gate_statuses": [],
        }
    hardware = experiment_summary.get("hardware_validation")
    if isinstance(hardware, dict):
        result = {
            "status": str(hardware.get("status") or "unknown"),
            "validation_run_count": int(hardware.get("validation_run_count") or 0),
            "validation_error_count": int(hardware.get("validation_error_count") or 0),
            "safe_experiment_count": int(hardware.get("safe_experiment_count") or 0),
            "safe_effective_count": int(hardware.get("safe_effective_count") or 0),
            "targets": [
                str(target)
                for target in hardware.get("targets", [])
                if isinstance(target, str) and target
            ],
        }
        if "receiver_validation_bundle_count" in hardware:
            result["receiver_validation_bundle_count"] = int(hardware.get("receiver_validation_bundle_count") or 0)
            result["receiver_validation_bundle_error_count"] = int(
                hardware.get("receiver_validation_bundle_error_count") or 0
            )
            result["write_gate_ready_count"] = int(hardware.get("write_gate_ready_count") or 0)
            result["write_gate_statuses"] = [
                str(status)
                for status in hardware.get("write_gate_statuses", [])
                if isinstance(status, str) and status
            ]
        return result
    return {
        "status": "missing-summary",
        "validation_run_count": 0,
        "validation_error_count": 1,
        "safe_experiment_count": 0,
        "safe_effective_count": 0,
        "targets": [],
        "receiver_validation_bundle_count": 0,
        "receiver_validation_bundle_error_count": 0,
        "write_gate_ready_count": 0,
        "write_gate_statuses": [],
    }


def _capture_set_linux_control_matrix(
    live_write_targets: list[dict[str, Any]],
    scenario_reports: list[dict[str, Any]],
    experiment_summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    scenarios_by_id = {
        str(report.get("id") or ""): report
        for report in scenario_reports
        if isinstance(report, dict)
    }
    experiment_summary = experiment_summary or {}
    operation_stats = (
        experiment_summary.get("operation_stats")
        if isinstance(experiment_summary.get("operation_stats"), dict)
        else {}
    )
    safe_runs_by_operation = _capture_set_safe_runs_by_live_operation(experiment_summary)
    result: list[dict[str, Any]] = []
    for spec in _capture_set_control_specs():
        operation = str(spec["operation"])
        scenario_ids = [str(item) for item in spec.get("scenario_ids", [])]
        scenario_items = [
            _capture_set_matrix_scenario_payload(scenarios_by_id[scenario_id])
            for scenario_id in scenario_ids
            if scenario_id in scenarios_by_id
        ]
        targets = [
            target
            for target in live_write_targets
            if _capture_set_target_matches_control_spec(spec, target)
        ]
        high_confidence_targets = [
            target
            for target in targets
            if target.get("confidence") == "high" and target.get("role") == "sender"
        ]
        experiment = _capture_set_matrix_experiment_payload(
            operation,
            experiment_summary,
            operation_stats,
            safe_runs_by_operation,
        )
        commands = _capture_set_matrix_commands(
            operation,
            scenario_items,
            high_confidence_targets,
            experiment_summary,
        )
        windows_status = _capture_set_matrix_windows_status(scenario_items)
        target_status = "high-confidence" if high_confidence_targets else ("candidate" if targets else "missing")
        result.append(
            {
                "operation": operation,
                "label": spec["label"],
                "risk": spec["risk"],
                "scenario_ids": scenario_ids,
                "windows_evidence_status": windows_status,
                "linux_target_status": target_status,
                "experiment_status": experiment["status"],
                "overall_status": _capture_set_matrix_overall_status(
                    risk=str(spec["risk"]),
                    windows_status=windows_status,
                    target_status=target_status,
                    experiment_status=experiment["status"],
                ),
                "scenario_statuses": scenario_items,
                "linux_targets": [
                    {
                        key: target[key]
                        for key in (
                            "operation",
                            "vid_pid",
                            "role",
                            "write_endpoint",
                            "read_endpoint",
                            "confidence",
                            "target_macs",
                            "channels",
                            "rx_types",
                            "runtime_contexts",
                            "pwm_values",
                            "rgb_static_colors",
                            "rgb_rainbow_generated",
                            "scenario_ids",
                        )
                        if key in target
                    }
                    for target in targets
                ],
                "experiment": experiment,
                "recommended_commands": commands,
            }
        )
    return result


def _capture_set_target_matches_control_spec(spec: dict[str, Any], target: dict[str, Any]) -> bool:
    operation = str(spec.get("operation") or "")
    target_operation = str(target.get("operation") or "")
    if operation == "live-rgb":
        return target_operation == "live-rgb" and bool(target.get("rgb_static_colors"))
    if operation == "live-rainbow":
        return (
            target_operation == "live-rainbow"
            or (target_operation == "live-rgb" and bool(target.get("rgb_rainbow_generated")))
        )
    return target_operation in set(spec.get("target_operations", [operation]))


def _capture_set_control_specs() -> list[dict[str, Any]]:
    return [
        {
            "operation": "receiver-snapshot",
            "label": "Receiver read/list",
            "risk": "readonly",
            "scenario_ids": ["baseline"],
            "target_operations": [],
        },
        {
            "operation": "live-pwm",
            "label": "Direct fan PWM",
            "risk": "guarded-write",
            "scenario_ids": ["direct-fan-speed", "sort-quick-sync"],
        },
        {
            "operation": "live-pwm-sync",
            "label": "Motherboard PWM sync",
            "risk": "guarded-write",
            "scenario_ids": ["motherboard-pwm-sync", "sort-quick-sync"],
        },
        {
            "operation": "live-pwm-mirror",
            "label": "Motherboard PWM mirror",
            "risk": "guarded-write",
            "scenario_ids": ["sort-quick-sync"],
        },
        {
            "operation": "live-bind",
            "label": "RF bind",
            "risk": "pairing-write",
            "scenario_ids": ["rf-rebind"],
        },
        {
            "operation": "live-unbind",
            "label": "RF unbind",
            "risk": "pairing-write",
            "scenario_ids": ["rf-rebind"],
        },
        {
            "operation": "live-rgb",
            "label": "Static RGB/off",
            "risk": "visual-write",
            "scenario_ids": ["lighting-static-and-off"],
        },
        {
            "operation": "live-rainbow",
            "label": "Generated RGB animation",
            "risk": "visual-write",
            "scenario_ids": ["lighting-generated-rainbow"],
        },
    ]


def _capture_set_matrix_scenario_payload(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(report.get("id") or ""),
        "status": str(report.get("status") or ""),
        "capture_file": str(report.get("capture_file") or ""),
        "goal": str(report.get("goal") or ""),
        "path": str(report.get("path") or ""),
        "expected_evidence": list(report.get("expected_evidence", []) if isinstance(report.get("expected_evidence"), list) else []),
        "required_evidence": list(report.get("required_evidence", []) if isinstance(report.get("required_evidence"), list) else []),
        "matched_evidence": list(report.get("matched_evidence", []) if isinstance(report.get("matched_evidence"), list) else []),
        "missing_evidence": list(report.get("missing_evidence", []) if isinstance(report.get("missing_evidence"), list) else []),
        "windows_actions": list(report.get("windows_actions", []) if isinstance(report.get("windows_actions"), list) else []),
        "planned_linux_commands": list(report.get("planned_linux_commands", []) if isinstance(report.get("planned_linux_commands"), list) else []),
    }


def _capture_set_matrix_windows_status(scenario_items: list[dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "") for item in scenario_items}
    if "evidence-found" in statuses:
        return "evidence-found"
    if "partial-evidence" in statuses:
        return "partial-evidence"
    if "analysis-error" in statuses:
        return "analysis-error"
    if "no-evidence" in statuses:
        return "no-evidence"
    if "missing-capture" in statuses:
        return "missing-capture"
    return "not-planned"


def _capture_set_safe_runs_by_live_operation(experiment_summary: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    runs = experiment_summary.get("safe_experiment_runs")
    if not isinstance(runs, list):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        if not isinstance(run, dict):
            continue
        live_operation = _capture_set_safe_operation_live_operation(str(run.get("operation") or ""))
        if not live_operation:
            continue
        result.setdefault(live_operation, []).append(run)
    return result


def _capture_set_safe_operation_live_operation(operation: str) -> str:
    return {
        "safe-pwm-experiment": "live-pwm",
        "safe-sync-experiment": "live-pwm-sync",
        "safe-pwm-mirror-experiment": "live-pwm-mirror",
        "safe-bind-experiment": "live-bind",
        "safe-unbind-experiment": "live-unbind",
        "safe-rgb-experiment": "live-rgb",
        "safe-rainbow-experiment": "live-rainbow",
    }.get(operation, "")


def _capture_set_matrix_experiment_payload(
    operation: str,
    experiment_summary: dict[str, Any],
    operation_stats: Any,
    safe_runs_by_operation: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    if operation == "receiver-snapshot":
        hardware = _capture_set_hardware_validation(experiment_summary)
        validation_count = int(hardware.get("validation_run_count") or 0)
        error_count = int(hardware.get("validation_error_count") or 0)
        if error_count:
            status = "errors"
        elif validation_count:
            status = "validated"
        else:
            status = "not-run"
        return {
            "status": status,
            "validation_run_count": validation_count,
            "validation_error_count": error_count,
            "safe_experiment_count": 0,
            "safe_effective_count": 0,
            "operation_stats": {},
        }

    stats = operation_stats.get(operation) if isinstance(operation_stats, dict) else None
    stats = stats if isinstance(stats, dict) else {}
    safe_runs = safe_runs_by_operation.get(operation, [])
    safe_effective = [
        run
        for run in safe_runs
        if run.get("likely_effective") is True or run.get("visual_confirmation_required") is True
    ]
    visual_required = any(run.get("visual_confirmation_required") is True for run in safe_runs)
    expected_matched = int(stats.get("expected_matched_count") or 0)
    changed = int(stats.get("changed_count") or 0)
    attempted = int(stats.get("count") or 0) or len(safe_runs)
    if expected_matched or changed or any(run.get("likely_effective") is True for run in safe_runs):
        status = "validated"
    elif visual_required:
        status = "visual-confirmation-needed"
    elif attempted:
        status = "attempted"
    else:
        status = "not-run"
    return {
        "status": status,
        "validation_run_count": 0,
        "validation_error_count": 0,
        "safe_experiment_count": len(safe_runs),
        "safe_effective_count": len(safe_effective),
        "operation_stats": stats,
        "safe_experiment_runs": safe_runs,
    }


def _capture_set_matrix_overall_status(
    *,
    risk: str,
    windows_status: str,
    target_status: str,
    experiment_status: str,
) -> str:
    if experiment_status == "errors":
        return "linux-validation-errors"
    if experiment_status == "validated":
        return "linux-validated"
    if experiment_status == "visual-confirmation-needed":
        return "needs-visual-confirmation"
    if risk == "readonly":
        return "run-readonly-validation" if windows_status in {"evidence-found", "partial-evidence"} else "needs-windows-capture"
    if windows_status not in {"evidence-found", "partial-evidence"}:
        return "needs-windows-capture"
    if target_status == "high-confidence":
        return "ready-for-guarded-experiment"
    if target_status == "candidate":
        return "needs-target-validation"
    return "needs-linux-target"


def _capture_set_matrix_commands(
    operation: str,
    scenario_items: list[dict[str, Any]],
    high_confidence_targets: list[dict[str, Any]],
    experiment_summary: dict[str, Any],
) -> list[str]:
    experiment_path = str(experiment_summary.get("path") or ".cache/lianli")
    commands: list[str] = []
    if operation == "receiver-snapshot":
        commands.append(_tool_command("validate-readonly", "--output-dir", ".cache/lianli/validation-live"))
        commands.append(_tool_command("summarize-experiments", experiment_path))
        return commands
    commands.extend(_capture_set_live_target_experiment_commands(high_confidence_targets, operation_filter=operation))
    if commands:
        commands.append(_tool_command("summarize-experiments", experiment_path))
        return _unique_preserve_order(commands)
    for scenario in scenario_items:
        if scenario.get("status") == "missing-capture" and scenario.get("capture_file"):
            commands.append(f"capture missing scenario: {scenario['capture_file']}")
    return _unique_preserve_order(commands)


def _capture_set_linux_interface_contract(
    live_write_targets: list[dict[str, Any]],
    control_matrix: list[dict[str, Any]],
    cross_scenario_deltas: dict[str, Any] | None = None,
) -> dict[str, Any]:
    high_confidence_sender_targets = [
        target
        for target in live_write_targets
        if target.get("confidence") == "high" and target.get("role") == "sender"
    ]
    matrix_by_operation = {
        str(item.get("operation") or ""): item
        for item in control_matrix
        if isinstance(item, dict)
    }
    protocol_delta_summary = _capture_set_protocol_delta_summary(cross_scenario_deltas)
    operation_contracts = [
        _capture_set_operation_contract(
            spec,
            matrix_by_operation,
            live_write_targets,
            protocol_delta_summary,
        )
        for spec in _capture_set_control_specs()
    ]
    validated = [item["operation"] for item in operation_contracts if item["status"] == "linux-validated"]
    ready = [item["operation"] for item in operation_contracts if item["status"] == "ready-for-guarded-experiment"]
    if validated:
        status = "linux-control-partially-validated"
    elif ready:
        status = "ready-for-guarded-experiment"
    elif high_confidence_sender_targets:
        status = "sender-endpoint-known"
    else:
        status = "needs-capture-evidence"
    return {
        "schema_version": LINUX_INTERFACE_CONTRACT_SCHEMA_VERSION,
        "status": status,
        "transport": _capture_set_transport_contract(high_confidence_sender_targets),
        "python_entrypoints": {
            "backend": "usb9_lcd.lianli.wireless.LianLiWirelessBackend",
            "pyusb_transport": "usb9_lcd.lianli.wireless.PyUsbEndpointTransport",
            "factory": "usb9_lcd.lianli.wireless.create_pyusb_backend",
        },
        "operation_contracts": operation_contracts,
        "protocol_delta_summary": protocol_delta_summary,
        "validated_operations": validated,
        "ready_operations": ready,
        "safety_model": [
            "Run validate-readonly before any live write.",
            "Use safe-*-experiment commands first; they save before/write/after logs and summarize expected effects.",
            "Treat RGB writes as requiring visual confirmation when receiver snapshots do not change.",
            "Do not run bind/unbind commands without confirming target MAC, channel, rx_type, and recovery path.",
        ],
    }


def _capture_set_protocol_delta_summary(cross_scenario_deltas: Any) -> dict[str, Any]:
    if not isinstance(cross_scenario_deltas, dict):
        return {
            "status": "not-available",
            "found_scenario_count": 0,
            "scenario_delta_count": 0,
            "unique_parameter_labels": [],
            "next_focus": [],
            "scenario_deltas": [],
            "notes": [],
        }
    scenario_deltas: list[dict[str, Any]] = []
    unique_parameter_labels: list[str] = []
    next_focus_items: list[str] = []
    for item in cross_scenario_deltas.get("scenario_deltas", []):
        if not isinstance(item, dict):
            continue
        labels = _ordered_strings_from_list(item.get("unique_parameter_labels"))
        unique_operations = _ordered_strings_from_list(item.get("unique_rf_operations"))
        next_focus = str(item.get("next_focus") or "")
        payload = {
            "id": str(item.get("id") or ""),
            "status": str(item.get("status") or ""),
            "path": str(item.get("path") or ""),
            "unique_rf_operations": unique_operations,
            "unique_parameter_labels": labels,
            "next_focus": next_focus,
        }
        if unique_operations or labels:
            scenario_deltas.append(payload)
            unique_parameter_labels.extend(labels)
            if next_focus:
                next_focus_items.append(next_focus)
    return {
        "status": str(cross_scenario_deltas.get("status") or ""),
        "found_scenario_count": int(cross_scenario_deltas.get("found_scenario_count") or 0),
        "scenario_delta_count": len(scenario_deltas),
        "unique_parameter_labels": _unique_preserve_order(unique_parameter_labels),
        "next_focus": _unique_preserve_order(next_focus_items),
        "scenario_deltas": scenario_deltas,
        "notes": [
            str(item)
            for item in cross_scenario_deltas.get("notes", [])
            if isinstance(item, str) and item
        ],
    }


def _linux_interface_contract_source_payload(capture_report: dict[str, Any]) -> dict[str, Any]:
    live_targets = capture_report.get("linux_live_write_targets")
    scenarios = capture_report.get("scenarios")
    return {
        "operation": "capture-set-report",
        "scenario_count": int(capture_report.get("scenario_count") or 0),
        "found_capture_count": int(capture_report.get("found_capture_count") or 0),
        "evidence_found_count": int(capture_report.get("evidence_found_count") or 0),
        "partial_evidence_count": int(capture_report.get("partial_evidence_count") or 0),
        "linux_live_write_target_count": len(live_targets) if isinstance(live_targets, list) else 0,
        "scenario_ids": _capture_set_source_scenario_ids(scenarios),
    }


def _capture_set_source_scenario_ids(scenarios: Any) -> list[str]:
    if not isinstance(scenarios, list):
        return []
    return [
        str(item.get("id") or "")
        for item in scenarios
        if isinstance(item, dict) and item.get("id")
    ]


def _linux_interface_contract_matrix_summary(capture_report: dict[str, Any]) -> list[dict[str, Any]]:
    matrix = capture_report.get("linux_control_matrix")
    if not isinstance(matrix, list):
        return []
    result: list[dict[str, Any]] = []
    for item in matrix:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "operation": str(item.get("operation") or ""),
                "label": str(item.get("label") or ""),
                "risk": str(item.get("risk") or ""),
                "overall_status": str(item.get("overall_status") or ""),
                "windows_evidence_status": str(item.get("windows_evidence_status") or ""),
                "linux_target_status": str(item.get("linux_target_status") or ""),
                "experiment_status": str(item.get("experiment_status") or ""),
            }
        )
    return result


def _linux_interface_contract_recommended_commands(capture_report: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    validation_plan = capture_report.get("linux_validation_plan")
    if isinstance(validation_plan, dict):
        plan_commands = validation_plan.get("commands")
        if isinstance(plan_commands, list):
            commands.extend(str(command) for command in plan_commands if command)
    matrix = capture_report.get("linux_control_matrix")
    if isinstance(matrix, list):
        for item in matrix:
            if not isinstance(item, dict):
                continue
            row_commands = item.get("recommended_commands")
            if isinstance(row_commands, list):
                commands.extend(str(command) for command in row_commands if command)
    return _unique_preserve_order(commands)


def _linux_control_manifest_operations(operation_contracts: Any) -> list[dict[str, Any]]:
    if not isinstance(operation_contracts, list):
        return []
    return [
        _linux_control_manifest_operation(item)
        for item in operation_contracts
        if isinstance(item, dict)
    ]


def _linux_control_manifest_operation(contract: dict[str, Any]) -> dict[str, Any]:
    operation = str(contract.get("operation") or "")
    risk = str(contract.get("risk") or "")
    backend = contract.get("backend") if isinstance(contract.get("backend"), dict) else {}
    transport = contract.get("transport") if isinstance(contract.get("transport"), dict) else {}
    required_fields = [
        str(field)
        for field in contract.get("required_runtime_fields", [])
        if isinstance(field, str) and field
    ]
    safe_cli = str(backend.get("safe_cli") or "")
    dry_run_cli = str(backend.get("dry_run_cli") or "")
    return {
        "operation": operation,
        "label": str(contract.get("label") or ""),
        "capability": _linux_control_manifest_capability(operation, risk),
        "risk": risk,
        "readiness": str(contract.get("status") or ""),
        "enabled_by_default": risk == "readonly",
        "write_enabled_by_default": False,
        "backend": backend,
        "transport": transport,
        "runtime_context": _linux_control_manifest_runtime_context(transport),
        "observed_parameters": _linux_control_manifest_observed_parameters(operation, transport),
        "protocol_deltas": contract.get("protocol_deltas", {}),
        "scenario_statuses": _linux_control_manifest_scenario_statuses(contract.get("scenario_statuses")),
        "missing_scenarios": _linux_control_manifest_missing_scenarios(contract.get("scenario_statuses")),
        "input_schema": _linux_control_manifest_input_schema(required_fields),
        "required_runtime_fields": required_fields,
        "safety": _linux_control_manifest_operation_safety(risk, safe_cli, dry_run_cli),
        "evidence": {
            "windows": str(contract.get("windows_evidence_status") or ""),
            "linux_target": str(contract.get("linux_target_status") or ""),
            "experiment": str(contract.get("experiment_status") or ""),
        },
        "commands": list(contract.get("recommended_commands", []) if isinstance(contract.get("recommended_commands"), list) else []),
    }


def _linux_control_manifest_capability(operation: str, risk: str) -> str:
    if risk == "readonly":
        return "read"
    return {
        "live-pwm": "fan-speed",
        "live-pwm-sync": "motherboard-pwm-sync",
        "live-pwm-mirror": "motherboard-pwm-mirror",
        "live-bind": "pair",
        "live-unbind": "unpair",
        "live-rgb": "static-lighting",
        "live-rainbow": "animated-lighting",
    }.get(operation, "write")


def _linux_control_manifest_scenario_statuses(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "id": str(item.get("id") or ""),
                "status": str(item.get("status") or ""),
                "capture_file": str(item.get("capture_file") or ""),
                "goal": str(item.get("goal") or ""),
                "expected_evidence": list(item.get("expected_evidence", []) if isinstance(item.get("expected_evidence"), list) else []),
                "required_evidence": list(item.get("required_evidence", []) if isinstance(item.get("required_evidence"), list) else []),
                "matched_evidence": list(item.get("matched_evidence", []) if isinstance(item.get("matched_evidence"), list) else []),
                "missing_evidence": list(item.get("missing_evidence", []) if isinstance(item.get("missing_evidence"), list) else []),
                "windows_actions": list(item.get("windows_actions", []) if isinstance(item.get("windows_actions"), list) else []),
                "planned_linux_commands": list(item.get("planned_linux_commands", []) if isinstance(item.get("planned_linux_commands"), list) else []),
            }
        )
    return result


def _linux_control_manifest_missing_scenarios(value: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in _linux_control_manifest_scenario_statuses(value)
        if item["status"] in {"missing-capture", "partial-evidence", "no-evidence", "analysis-error"}
    ]


def _linux_control_manifest_runtime_context(transport: dict[str, Any]) -> dict[str, Any]:
    contexts = transport.get("runtime_contexts")
    context_items = [dict(item) for item in contexts if isinstance(item, dict)] if isinstance(contexts, list) else []
    target_macs = sorted(_strings_from_list(transport.get("target_macs")))
    channels = _ints_from_list(transport.get("channels"))
    rx_types = _ints_from_list(transport.get("rx_types"))
    master_macs = sorted(_strings_from_list(transport.get("master_macs")))
    return {
        "status": _linux_control_manifest_runtime_context_status(context_items, target_macs, channels, rx_types),
        "target_macs": target_macs,
        "channels": channels,
        "rx_types": rx_types,
        "master_macs": master_macs,
        "contexts": context_items,
        "source": "capture-evidence" if context_items or target_macs else "runtime-readonly-snapshot",
    }


def _linux_control_manifest_runtime_context_status(
    contexts: list[dict[str, Any]],
    target_macs: list[str],
    channels: list[int],
    rx_types: list[int],
) -> str:
    if any({"mac", "channel", "rx_type"} <= set(context) for context in contexts):
        return "complete"
    if target_macs and channels and rx_types:
        return "candidate-complete"
    if target_macs:
        return "needs-readonly-snapshot"
    return "missing"


def _linux_control_manifest_observed_parameters(
    operation: str,
    transport: dict[str, Any],
) -> dict[str, Any]:
    if operation in {"live-pwm", "live-pwm-mirror", "live-pwm-sync"}:
        pwm_values = _linux_control_observed_pwm_values(transport.get("pwm_values"))
        if pwm_values:
            return {
                "source": "capture-evidence",
                "pwm_values": pwm_values,
                "default_pwm_values": pwm_values[0]["values"],
            }
    if operation == "live-rgb":
        return _linux_control_observed_static_rgb_parameters(transport)
    if operation == "live-rainbow":
        return _linux_control_observed_rainbow_parameters(transport)
    return {}


def _linux_control_observed_pwm_values(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    result: list[dict[str, Any]] = []
    for key_text, count in value.items():
        values = _pwm_tuple_from_key(str(key_text))
        if values is None:
            continue
        try:
            observed_count = int(count)
        except (TypeError, ValueError):
            observed_count = 0
        result.append({"values": list(values), "count": observed_count})
    return sorted(result, key=lambda item: (-int(item["count"]), item["values"]))


def _linux_control_observed_static_rgb_parameters(transport: dict[str, Any]) -> dict[str, Any]:
    colors = _linux_control_observed_rgb_static_colors(transport.get("rgb_static_colors"))
    effect_indexes = _ints_from_list(transport.get("effect_indexes"))
    led_counts = _ints_from_list(transport.get("led_counts"))
    frame_counts = _ints_from_list(transport.get("frame_counts"))
    interval_ms_values = _ints_from_list(transport.get("interval_ms_values"))
    sequence_frame_counts = _ints_from_list(transport.get("rgb_sequence_frame_counts"))
    if not any((colors, effect_indexes, led_counts, frame_counts, interval_ms_values, sequence_frame_counts)):
        return {}
    payload: dict[str, Any] = {"source": "capture-evidence"}
    if colors:
        payload["static_colors"] = colors
        payload["default_color"] = colors[0]["color"]
        payload["default_color_hex"] = colors[0]["hex"]
    if effect_indexes:
        payload["effect_indexes"] = effect_indexes
        payload["default_effect_index"] = effect_indexes[0]
    if led_counts:
        payload["led_counts"] = led_counts
        payload["default_led_count"] = led_counts[0]
    if frame_counts:
        payload["frame_counts"] = frame_counts
        payload["default_frame_count"] = frame_counts[0]
    if interval_ms_values:
        payload["interval_ms_values"] = interval_ms_values
        payload["default_interval_ms"] = interval_ms_values[0]
    if sequence_frame_counts:
        payload["rgb_sequence_frame_counts"] = sequence_frame_counts
    return payload


def _linux_control_observed_rainbow_parameters(transport: dict[str, Any]) -> dict[str, Any]:
    matches = _linux_control_observed_rainbow_matches(transport.get("rgb_rainbow_generated"))
    if not matches:
        return {}
    default = matches[0]
    return {
        "source": "capture-evidence",
        "generated_rainbow_matches": matches,
        "default_led_count": default["led_count"],
        "default_frame_count": default["frame_count"],
        "default_interval_ms": default["interval_ms"],
        "default_effect_index": default["effect_index"],
    }


def _linux_control_observed_rainbow_matches(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    result: list[dict[str, Any]] = []
    for key_text, count in value.items():
        values = _rainbow_tuple_from_key(str(key_text))
        if values is None:
            continue
        try:
            observed_count = int(count)
        except (TypeError, ValueError):
            observed_count = 0
        led_count, frame_count, interval_ms, effect_index = values
        result.append(
            {
                "led_count": led_count,
                "frame_count": frame_count,
                "interval_ms": interval_ms,
                "effect_index": effect_index,
                "count": observed_count,
            }
        )
    return sorted(
        result,
        key=lambda item: (
            -int(item["count"]),
            item["led_count"],
            item["frame_count"],
            item["interval_ms"],
            item["effect_index"],
        ),
    )


def _rainbow_tuple_from_key(value: str) -> tuple[int, int, int, int] | None:
    parts = value.split(",")
    if len(parts) != 4:
        return None
    try:
        led_count, frame_count, interval_ms, effect_index = (int(part.strip()) for part in parts)
    except ValueError:
        return None
    if led_count <= 0 or frame_count <= 0 or interval_ms < 0 or effect_index < 0:
        return None
    return led_count, frame_count, interval_ms, effect_index


def _linux_control_observed_rgb_static_colors(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    result: list[dict[str, Any]] = []
    for color_hex, count in value.items():
        color = _rgb_tuple_from_hex(str(color_hex))
        if color is None:
            continue
        try:
            observed_count = int(count)
        except (TypeError, ValueError):
            observed_count = 0
        result.append({"hex": _rgb_color_hex(color), "color": list(color), "count": observed_count})
    return sorted(result, key=lambda item: (-int(item["count"]), item["hex"]))


def _rgb_tuple_from_hex(value: str) -> tuple[int, int, int] | None:
    text = value.strip().lower()
    if text.startswith("#"):
        text = text[1:]
    if not re.fullmatch(r"[0-9a-f]{6}", text):
        return None
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def _pwm_tuple_from_key(value: str) -> tuple[int, int, int, int] | None:
    parts = value.split(",")
    if len(parts) != 4:
        return None
    try:
        items = tuple(max(0, min(255, int(part.strip()))) for part in parts)
    except ValueError:
        return None
    return items  # type: ignore[return-value]


def _linux_control_manifest_input_schema(required_fields: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "name": field,
            "kind": _linux_control_manifest_field_kind(field),
            "required": True,
            "source": _linux_control_manifest_field_source(field),
        }
        for field in required_fields
    ]


def _linux_control_manifest_field_kind(field: str) -> str:
    return {
        "receiver_transport": "pyusb-receiver",
        "target.mac": "mac-address",
        "target.channel": "integer",
        "target.rx_type": "integer",
        "pwm_values": "pwm-tuple",
        "enable": "boolean",
        "fallback_pwm": "pwm",
        "motherboard_pwm": "pwm",
        "master_mac": "mac-address",
        "rx_type": "integer",
        "channel": "integer",
        "color": "rgb",
        "effect_index": "integer",
        "led_count": "integer",
        "frame_count": "integer",
        "interval_ms": "integer",
    }.get(field, "value")


def _linux_control_manifest_field_source(field: str) -> str:
    if field.startswith("target."):
        return "receiver-snapshot"
    if field == "receiver_transport":
        return "pyusb-factory"
    if field in {"motherboard_pwm", "master_mac"}:
        return "receiver-query"
    return "user-input"


def _linux_control_manifest_operation_safety(
    risk: str,
    safe_cli: str,
    dry_run_cli: str,
) -> dict[str, Any]:
    if risk == "readonly":
        return {
            "writes_usb": False,
            "requires_confirmation": False,
            "preferred_mode": "readonly",
            "dry_run_first": False,
            "visual_confirmation_required": False,
            "pairing_recovery_required": False,
        }
    return {
        "writes_usb": True,
        "requires_confirmation": True,
        "confirmation_token": LINUX_WRITE_CONFIRM_TOKEN,
        "preferred_mode": "safe-experiment" if safe_cli else "dry-run",
        "safe_cli": safe_cli,
        "dry_run_cli": dry_run_cli,
        "dry_run_first": True,
        "visual_confirmation_required": risk == "visual-write",
        "pairing_recovery_required": risk == "pairing-write",
    }


def _linux_control_manifest_permissions() -> dict[str, Any]:
    return {
        "requires_pyusb": True,
        "requires_hidraw": False,
        "default_write_access": False,
        "udev_rules": list(UDEV_RULES),
        "notes": [
            "Install udev rules before running without sudo.",
            "Receiver snapshot is read-only; safe experiments still require explicit write confirmation.",
        ],
    }


def _linux_control_manifest_safety_gates() -> dict[str, Any]:
    return {
        "readonly_validation_before_write": True,
        "write_confirmation_token": LINUX_WRITE_CONFIRM_TOKEN,
        "safe_experiment_first": True,
        "writes_disabled_by_default": True,
        "pairing_requires_recovery_plan": True,
        "visual_effects_require_human_confirmation": True,
    }


def _linux_control_manifest_target_macs(operations: list[dict[str, Any]]) -> list[str]:
    macs: set[str] = set()
    for operation in operations:
        transport = operation.get("transport")
        if not isinstance(transport, dict):
            continue
        for mac in transport.get("target_macs", []):
            if isinstance(mac, str) and mac:
                macs.add(mac.lower())
    return sorted(macs)


def _linux_control_preflight_device_access(
    readiness: dict[str, Any],
    dev_root: Path,
) -> dict[str, Any]:
    devices: list[dict[str, Any]] = []
    summary: dict[str, dict[str, Any]] = {}
    targets = readiness.get("targets")
    if not isinstance(targets, list):
        targets = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        vid_pid = str(target.get("vid_pid") or "")
        for device in target.get("devices", []):
            if not isinstance(device, dict):
                continue
            access = _linux_control_preflight_single_device_access(device, dev_root)
            devices.append(access)
            item = summary.setdefault(
                vid_pid,
                {
                    "vid_pid": vid_pid,
                    "present": False,
                    "device_count": 0,
                    "read_write_count": 0,
                    "missing_node_count": 0,
                    "devices": [],
                },
            )
            item["present"] = True
            item["device_count"] += 1
            if access["read_write"]:
                item["read_write_count"] += 1
            if not access["exists"]:
                item["missing_node_count"] += 1
            item["devices"].append(access)
    return {
        "dev_root": str(dev_root),
        "devices": devices,
        "summary": dict(sorted(summary.items())),
    }


def _linux_control_preflight_single_device_access(
    device: dict[str, Any],
    dev_root: Path,
) -> dict[str, Any]:
    busnum = str(device.get("busnum") or "")
    devnum = str(device.get("devnum") or "")
    node = dev_root / "bus" / "usb" / busnum / devnum if busnum and devnum else None
    exists = False
    readable = False
    writable = False
    mode = ""
    error = ""
    if node is None:
        error = "missing busnum/devnum"
    else:
        try:
            exists = node.exists()
            readable = os.access(node, os.R_OK)
            writable = os.access(node, os.W_OK)
            if exists:
                mode = oct(node.stat().st_mode & 0o777)
        except OSError as exc:
            error = str(exc)
    return {
        "vid_pid": str(device.get("vid_pid") or ""),
        "label": str(device.get("label") or ""),
        "busnum": busnum,
        "devnum": devnum,
        "dev_node": str(node) if node is not None else "",
        "exists": exists,
        "readable": readable,
        "writable": writable,
        "read_write": bool(readable and writable),
        "mode": mode,
        "error": error,
    }


def _linux_control_preflight_permission_status(device_access: dict[str, Any]) -> str:
    devices = device_access.get("devices")
    if not isinstance(devices, list) or not devices:
        return "no-known-usb-devices"
    if any(isinstance(item, dict) and not item.get("exists") for item in devices):
        return "missing-dev-node"
    if all(isinstance(item, dict) and item.get("read_write") for item in devices):
        return "read-write-ok"
    return "needs-usb-permission"


def _linux_control_preflight_operations(
    manifest: dict[str, Any],
    device_access: dict[str, Any],
) -> list[dict[str, Any]]:
    operations = manifest.get("operations")
    if not isinstance(operations, list):
        return []
    access_summary = (
        device_access.get("summary")
        if isinstance(device_access.get("summary"), dict)
        else {}
    )
    return [
        _linux_control_preflight_operation(
            item,
            access_summary,
            capture_root=Path(str(manifest.get("path") or ".")),
        )
        for item in operations
        if isinstance(item, dict)
    ]


def _linux_control_preflight_operation(
    operation: dict[str, Any],
    access_summary: dict[str, Any],
    *,
    capture_root: Path,
) -> dict[str, Any]:
    name = str(operation.get("operation") or "")
    transport = operation.get("transport") if isinstance(operation.get("transport"), dict) else {}
    required_vid_pid = _linux_control_preflight_required_vid_pid(operation)
    access = access_summary.get(required_vid_pid) if isinstance(access_summary, dict) else None
    access = access if isinstance(access, dict) else {}
    present = bool(access.get("present"))
    read_write = int(access.get("read_write_count") or 0) > 0
    contract_status = str(operation.get("readiness") or "")
    preflight_status = _linux_control_preflight_operation_status(
        name,
        contract_status,
        present,
        read_write,
    )
    return {
        "operation": name,
        "capability": str(operation.get("capability") or ""),
        "risk": str(operation.get("risk") or ""),
        "contract_status": contract_status,
        "preflight_status": preflight_status,
        "can_run_now": preflight_status in {"ready", "ready-for-readonly-validation"},
        "required_vid_pid": required_vid_pid,
        "device_access": {
            "present": present,
            "device_count": int(access.get("device_count") or 0),
            "read_write_count": int(access.get("read_write_count") or 0),
            "missing_node_count": int(access.get("missing_node_count") or 0),
        },
        "runtime_context": operation.get("runtime_context", {}),
        "observed_parameters": operation.get("observed_parameters", {}),
        "protocol_deltas": operation.get("protocol_deltas", {}),
        "scenario_statuses": list(operation.get("scenario_statuses", []) if isinstance(operation.get("scenario_statuses"), list) else []),
        "missing_scenarios": list(operation.get("missing_scenarios", []) if isinstance(operation.get("missing_scenarios"), list) else []),
        "source_scenario_ids": _ordered_strings_from_list(transport.get("scenario_ids")),
        "source_capture_paths": _linux_control_preflight_source_capture_paths(
            transport.get("scenario_paths"),
            capture_root,
        ),
        "safety": operation.get("safety", {}),
        "commands": list(operation.get("commands", []) if isinstance(operation.get("commands"), list) else []),
    }


def _linux_control_preflight_source_capture_paths(value: Any, capture_root: Path) -> list[str]:
    paths = []
    for item in _ordered_strings_from_list(value):
        path = Path(item)
        if not path.is_absolute():
            path = capture_root / path
        paths.append(str(path))
    return _unique_preserve_order(paths)


def _linux_control_preflight_required_vid_pid(operation: dict[str, Any]) -> str:
    if str(operation.get("operation") or "") == "receiver-snapshot":
        return f"{RF_RECEIVER_VID:04x}:{RF_RECEIVER_PID:04x}"
    transport = operation.get("transport")
    if isinstance(transport, dict) and transport.get("vid_pid"):
        return str(transport.get("vid_pid"))
    return f"{RF_SENDER_VID:04x}:{RF_SENDER_PID:04x}"


def _linux_control_preflight_operation_status(
    operation: str,
    contract_status: str,
    present: bool,
    read_write: bool,
) -> str:
    if not present:
        return "missing-hardware"
    if not read_write:
        return "needs-usb-permission"
    if operation == "receiver-snapshot":
        if contract_status == "linux-validated":
            return "ready"
        return "ready-for-readonly-validation"
    if contract_status in {"linux-validated", "ready-for-guarded-experiment", "needs-visual-confirmation"}:
        return "ready"
    if contract_status == "needs-windows-capture":
        return "needs-capture-evidence"
    if contract_status in {"needs-linux-target", "needs-target-validation"}:
        return "needs-linux-target"
    return contract_status or "not-evaluated"


def _linux_control_preflight_status(
    readiness: dict[str, Any],
    permission_status: str,
    operations: list[dict[str, Any]],
) -> str:
    hardware_status = str(readiness.get("status") or "")
    if hardware_status == "no-l-wireless-hardware":
        return "no-l-wireless-hardware"
    if hardware_status in {"receiver-only", "sender-only"}:
        return "partial-hardware"
    if permission_status in {"missing-dev-node", "needs-usb-permission"}:
        return permission_status
    if any(
        item.get("preflight_status") == "ready" and item.get("risk") != "readonly"
        for item in operations
    ):
        return "ready-for-safe-experiments"
    if any(item.get("preflight_status") == "ready-for-readonly-validation" for item in operations):
        return "ready-for-readonly-validation"
    return "needs-capture-evidence"


def _linux_control_preflight_blockers(
    readiness: dict[str, Any],
    permission_status: str,
    operations: list[dict[str, Any]],
) -> list[str]:
    blockers = [
        str(item)
        for item in readiness.get("blockers", [])
        if isinstance(item, str) and item
    ]
    if permission_status == "missing-dev-node":
        blockers.append("A visible USB device is missing its /dev/bus/usb node; replug the device or check udev.")
    elif permission_status == "needs-usb-permission":
        blockers.append("Current user lacks read/write access to a LIAN LI USB device node; install udev rules and replug.")
    if not any(item.get("preflight_status") == "ready" for item in operations):
        if any(item.get("preflight_status") == "needs-capture-evidence" for item in operations):
            blockers.append("Some operations still need official Windows USBPcap evidence before Linux writes should be trusted.")
    return _unique_preserve_order(blockers)


def _linux_control_preflight_commands(
    manifest: dict[str, Any],
    readiness: dict[str, Any],
    permission_status: str,
) -> list[str]:
    commands: list[str] = []
    if permission_status in {"missing-dev-node", "needs-usb-permission"}:
        commands.append(_tool_command("udev-rules"))
    linux_commands = readiness.get("linux_live_commands")
    if isinstance(linux_commands, list):
        commands.extend(str(command) for command in linux_commands if command)
    manifest_commands = manifest.get("recommended_commands")
    if isinstance(manifest_commands, list):
        commands.extend(str(command) for command in manifest_commands if command)
    return _unique_preserve_order(commands)


def _linux_control_action_plan_actions(
    preflight: dict[str, Any],
    experiment_summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    permission_status = str(preflight.get("permission_status") or "")
    if permission_status in {"missing-dev-node", "needs-usb-permission"}:
        actions.append(
            {
                "id": "setup-usb-permissions",
                "phase": "setup",
                "operation": "",
                "title": "Install LIAN LI USB udev rules and replug the receiver/sender",
                "status": "blocked",
                "reason": permission_status,
                "risk": "permission-change",
                "requires_confirmation": False,
                "writes_usb": False,
                "command": _tool_command("udev-rules"),
                "commands": [_tool_command("udev-rules")],
            }
        )
    operations = preflight.get("operations")
    if not isinstance(operations, list):
        return actions
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        action = _linux_control_action_plan_operation_action(operation, preflight, experiment_summary)
        if action:
            actions.append(action)
    return actions


def _linux_control_action_plan_operation_action(
    operation: dict[str, Any],
    preflight: dict[str, Any],
    experiment_summary: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    name = str(operation.get("operation") or "")
    status = str(operation.get("preflight_status") or "")
    risk = str(operation.get("risk") or "")
    commands = list(operation.get("commands", []) if isinstance(operation.get("commands"), list) else [])
    pre_write_validation_commands = _linux_control_action_plan_packet_validation_commands(operation, preflight)
    commands = _unique_preserve_order([*pre_write_validation_commands, *commands])
    missing_scenarios = list(operation.get("missing_scenarios", []) if isinstance(operation.get("missing_scenarios"), list) else [])
    if status == "needs-capture-evidence":
        commands = _unique_preserve_order(
            [
                *_linux_control_action_plan_capture_commands(missing_scenarios, preflight),
                *commands,
            ]
        )
    safety = operation.get("safety") if isinstance(operation.get("safety"), dict) else {}
    command = _linux_control_action_plan_primary_command(name, commands, safety)
    pre_write_validation = _linux_control_action_plan_pre_write_validation(
        pre_write_validation_commands,
        operation,
        experiment_dir=str(preflight.get("experiment_dir") or ""),
        experiment_summary=experiment_summary,
    )
    if risk == "readonly":
        phase = "validate-readonly"
        title = "Validate receiver snapshot/list reads"
    elif status == "needs-capture-evidence":
        phase = "capture-evidence"
        title = f"Capture official L-Connect evidence for {name}"
    elif status in {"ready", "needs-visual-confirmation"}:
        phase = "safe-experiment"
        title = f"Run guarded Linux experiment for {name}"
    else:
        phase = "blocked"
        title = f"Resolve {name} prerequisite"
    return {
        "id": _linux_control_action_id(phase, name),
        "phase": phase,
        "operation": name,
        "capability": str(operation.get("capability") or ""),
        "title": title,
        "status": _linux_control_action_plan_action_status(status),
        "preflight_status": status,
        "contract_status": str(operation.get("contract_status") or ""),
        "reason": _linux_control_action_plan_reason(status),
        "risk": risk,
        "required_vid_pid": str(operation.get("required_vid_pid") or ""),
        "runtime_context": operation.get("runtime_context", {}),
        "observed_parameters": operation.get("observed_parameters", {}),
        "protocol_deltas": operation.get("protocol_deltas", {}),
        "missing_scenarios": missing_scenarios,
        "windows_capture_actions": _linux_control_action_plan_windows_actions(missing_scenarios),
        "post_capture_commands": _linux_control_action_plan_post_capture_commands(missing_scenarios),
        "source_scenario_ids": list(operation.get("source_scenario_ids", []) if isinstance(operation.get("source_scenario_ids"), list) else []),
        "source_capture_paths": list(operation.get("source_capture_paths", []) if isinstance(operation.get("source_capture_paths"), list) else []),
        "requires_confirmation": bool(safety.get("requires_confirmation")),
        "confirmation_token": str(safety.get("confirmation_token") or ""),
        "writes_usb": bool(safety.get("writes_usb")),
        "visual_confirmation_required": bool(safety.get("visual_confirmation_required")),
        "pairing_recovery_required": bool(safety.get("pairing_recovery_required")),
        "pre_write_validation_commands": pre_write_validation_commands,
        "pre_write_validation": pre_write_validation,
        "execution": _linux_control_action_plan_execution(
            phase=phase,
            command=command,
            writes_usb=bool(safety.get("writes_usb")),
            pre_write_validation=pre_write_validation,
        ),
        "command": command,
        "commands": commands,
    }


def _linux_control_action_plan_execution(
    *,
    phase: str,
    command: str,
    writes_usb: bool,
    pre_write_validation: dict[str, Any],
) -> dict[str, Any]:
    validation_required = bool(pre_write_validation.get("required"))
    validation_status = str(pre_write_validation.get("validation_status") or "")
    validation_commands = list(
        pre_write_validation.get("commands", [])
        if isinstance(pre_write_validation.get("commands"), list)
        else []
    )
    blocked = bool(writes_usb and validation_required and validation_status != "passed")
    if blocked:
        if validation_status == "refresh-live-snapshot":
            status = "refresh-live-snapshot"
            refresh_context = pre_write_validation.get("live_snapshot_refresh")
            refresh_command = (
                str(refresh_context.get("command") or "")
                if isinstance(refresh_context, dict)
                else ""
            ) or _tool_command("live-list")
            required_before_write = _unique_preserve_order([refresh_command, *validation_commands])
            next_command = refresh_command
        elif validation_status == "failed":
            status = "blocked-by-failed-pre-write-validation"
            required_before_write = validation_commands
            next_command = validation_commands[0] if validation_commands else ""
        elif validation_status == "incomplete":
            status = "incomplete-pre-write-validation"
            required_before_write = validation_commands
            next_command = validation_commands[0] if validation_commands else ""
        elif validation_status == "needs-recompare-after-refresh":
            status = "needs-recompare-after-refresh"
            required_before_write = validation_commands
            next_command = validation_commands[0] if validation_commands else ""
        elif validation_status == "invalid-schema":
            status = "invalid-pre-write-validation-schema"
            required_before_write = validation_commands
            next_command = validation_commands[0] if validation_commands else ""
        else:
            status = "pre-write-validation-required"
            required_before_write = validation_commands
            next_command = validation_commands[0] if validation_commands else ""
    else:
        status = "write-enabled" if writes_usb else ("ready" if command else "no-command")
        next_command = command
        required_before_write = []
    return {
        "status": status,
        "phase": phase,
        "writes_usb": writes_usb,
        "write_command_enabled": bool(writes_usb and command and not blocked),
        "blocked_by_pre_write_validation": blocked,
        "next_command": next_command,
        "write_command": command if writes_usb else "",
        "validation_status": validation_status,
        "required_before_write": required_before_write,
    }


def _linux_control_action_plan_pre_write_validation(
    commands: list[str],
    operation: dict[str, Any],
    *,
    experiment_dir: str = "",
    experiment_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    compare_commands = [
        command
        for command in commands
        if "linux-control-packet-compare" in command
    ]
    preview_commands = [
        command
        for command in commands
        if "linux-control-packet-preview" in command
    ]
    source_capture_paths = list(
        operation.get("source_capture_paths", [])
        if isinstance(operation.get("source_capture_paths"), list)
        else []
    )
    required = bool(compare_commands)
    observed_results = _linux_control_action_plan_observed_pre_write_results(
        operation,
        compare_commands,
        experiment_summary or {},
    )
    live_snapshot_refresh = _linux_control_action_plan_live_snapshot_refresh(
        operation,
        experiment_summary or {},
        experiment_dir=experiment_dir,
    )
    source_coverage = _linux_control_action_plan_compare_source_coverage(
        source_capture_paths,
        observed_results,
    )
    validation_status = _linux_control_action_plan_pre_write_validation_status(
        required=required,
        compare_command_count=len(compare_commands),
        observed_results=observed_results,
        source_coverage=source_coverage,
        live_snapshot_refresh_available=live_snapshot_refresh.get("available") is True,
    )
    return {
        "required": required,
        "status": "required" if required else "not-required",
        "validation_status": validation_status,
        "allows_guarded_write": validation_status == "passed",
        "reason": (
            "Exact packet comparison must pass before WRITE-LIANLI."
            if required
            else "No packet-compare evidence is available for this action."
        ),
        "minimum_required_match": "exact-match" if required else "",
        "required_write_gate_status": "pass" if required else "",
        "required_allows_guarded_write": True if required else False,
        "semantic_only_action": "refresh-live-snapshot-and-recompare" if required else "",
        "live_snapshot_refresh": live_snapshot_refresh,
        "commands": list(commands),
        "preview_commands": preview_commands,
        "compare_commands": compare_commands,
        "observed_results": observed_results,
        "source_capture_coverage": source_coverage,
        "expected_compare_results": [
            {
                "command": command,
                "schema_version": LINUX_CONTROL_PACKET_COMPARE_SCHEMA_VERSION,
                "required_status": "matched",
                "required_exact_match": True,
                "required_semantic_match": True,
                "required_write_gate_status": "pass",
                "required_allows_guarded_write": True,
                "on_semantic_only": "run live-list, refresh receiver state, then compare again",
            }
            for command in compare_commands
        ],
        "source_capture_paths": source_capture_paths,
    }


def _linux_control_action_plan_observed_pre_write_results(
    operation: dict[str, Any],
    compare_commands: list[str],
    experiment_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    runs = experiment_summary.get("packet_compare_runs")
    if not isinstance(runs, list) or not compare_commands:
        return []
    operation_name = str(operation.get("operation") or "")
    target_id = _linux_control_action_plan_target_id(operation)
    source_paths = [
        str(path)
        for path in operation.get("source_capture_paths", [])
        if isinstance(path, str) and path
    ]
    results: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        if operation_name and str(run.get("control_operation") or "") != operation_name:
            continue
        if target_id and str(run.get("target_id") or "") != target_id:
            continue
        observed_capture = str(run.get("observed_capture") or "")
        source_match = _capture_path_match(observed_capture, source_paths)
        if source_paths and not source_match:
            continue
        results.append(
            {
                "path": str(run.get("path") or ""),
                "schema_version": str(run.get("schema_version") or ""),
                "schema_version_required": LINUX_CONTROL_PACKET_COMPARE_SCHEMA_VERSION,
                "schema_version_valid": str(run.get("schema_version") or "") == LINUX_CONTROL_PACKET_COMPARE_SCHEMA_VERSION,
                "control_operation": str(run.get("control_operation") or ""),
                "target_id": str(run.get("target_id") or ""),
                "observed_capture": observed_capture,
                "observed_capture_match": source_match.get("match", "") if source_match else "",
                "source_capture_path": source_match.get("source", "") if source_match else "",
                "status": str(run.get("status") or ""),
                "matched": bool(run.get("matched")),
                "exact_match": bool(run.get("exact_match")),
                "semantic_match": bool(run.get("semantic_match")),
                "diagnostics_status": str(run.get("diagnostics_status") or ""),
                "write_gate_status": str(run.get("write_gate_status") or ""),
                "allows_guarded_write": bool(run.get("allows_guarded_write")),
                "target_state_status": str(run.get("target_state_status") or ""),
                "target_state_missing_packet_fields": _ordered_strings_from_list(
                    run.get("target_state_missing_packet_fields")
                ),
                "target_state_placeholder_fields": _ordered_strings_from_list(
                    run.get("target_state_placeholder_fields")
                ),
                "target_state_snapshot_metadata_available": bool(
                    run.get("target_state_snapshot_metadata_available")
                ),
                "target_state_snapshot_state_available": bool(
                    run.get("target_state_snapshot_state_available")
                ),
                "target_state_raw_hex_available": bool(run.get("target_state_raw_hex_available")),
                "target_state_live_snapshot_refresh_required": bool(
                    run.get("target_state_live_snapshot_refresh_required")
                ),
            }
        )
    return sorted(results, key=lambda item: (item["observed_capture"], item["path"]))


def _capture_path_match(observed_capture: str, source_paths: list[str]) -> dict[str, str]:
    observed = _normalized_capture_path_text(observed_capture)
    if not observed:
        return {}
    for source in source_paths:
        source_text = _normalized_capture_path_text(source)
        if source_text and observed == source_text:
            return {"match": "exact", "source": source}
    for source in source_paths:
        source_text = _normalized_capture_path_text(source)
        if source_text and (
            _capture_path_suffix_matches(observed, source_text)
            or _capture_path_suffix_matches(source_text, observed)
        ):
            return {"match": "path-suffix", "source": source}
    return {}


def _normalized_capture_path_text(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    text = re.sub(r"/+", "/", text)
    if len(text) > 1:
        text = text.rstrip("/")
    return text


def _capture_path_suffix_matches(short_path: str, long_path: str) -> bool:
    short_parts = [part for part in short_path.split("/") if part and part != "."]
    long_parts = [part for part in long_path.split("/") if part and part != "."]
    if not short_parts or len(short_parts) > len(long_parts):
        return False
    return short_parts == long_parts[-len(short_parts) :]


def _linux_control_action_plan_pre_write_validation_status(
    *,
    required: bool,
    compare_command_count: int,
    observed_results: list[dict[str, Any]],
    source_coverage: dict[str, Any] | None = None,
    live_snapshot_refresh_available: bool = False,
) -> str:
    if not required:
        return "not-required"
    if not observed_results:
        return "needs-run"
    pass_count = sum(
        1
        for item in observed_results
        if _linux_control_action_plan_compare_result_passes_gate(item)
    )
    invalid_schema_count = sum(
        1
        for item in observed_results
        if str(item.get("schema_version") or "") != LINUX_CONTROL_PACKET_COMPARE_SCHEMA_VERSION
    )
    if invalid_schema_count and pass_count == 0:
        return "invalid-schema"
    coverage = source_coverage if isinstance(source_coverage, dict) else {}
    expected_source_count = int(coverage.get("expected_count") or 0)
    missing_source_count = int(coverage.get("missing_count") or 0)
    if expected_source_count:
        if missing_source_count == 0:
            return "passed"
    elif pass_count >= compare_command_count:
        return "passed"
    if invalid_schema_count and pass_count < compare_command_count:
        return "invalid-schema"
    if any(item.get("write_gate_status") == "fail" for item in observed_results):
        return "failed"
    if any(item.get("write_gate_status") == "refresh-live-snapshot" for item in observed_results):
        if live_snapshot_refresh_available:
            return "needs-recompare-after-refresh"
        return "refresh-live-snapshot"
    return "incomplete"


def _linux_control_action_plan_compare_result_passes_gate(item: dict[str, Any]) -> bool:
    return bool(
        item.get("schema_version") == LINUX_CONTROL_PACKET_COMPARE_SCHEMA_VERSION
        and item.get("exact_match") is True
        and item.get("write_gate_status") == "pass"
        and item.get("allows_guarded_write") is True
    )


def _linux_control_action_plan_compare_source_coverage(
    source_capture_paths: list[Any],
    observed_results: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = _unique_preserve_order(
        [
            str(path)
            for path in source_capture_paths
            if isinstance(path, str) and path
        ]
    )
    passed: list[str] = []
    for source in expected:
        if any(_linux_control_action_plan_compare_result_passes_source(item, source) for item in observed_results):
            passed.append(source)
    missing = [source for source in expected if source not in set(passed)]
    return {
        "expected_count": len(expected),
        "passed_count": len(passed),
        "missing_count": len(missing),
        "expected_source_capture_paths": expected,
        "passed_source_capture_paths": passed,
        "missing_source_capture_paths": missing,
    }


def _linux_control_action_plan_compare_result_passes_source(item: dict[str, Any], source: str) -> bool:
    if not _linux_control_action_plan_compare_result_passes_gate(item):
        return False
    observed_source = str(item.get("source_capture_path") or "")
    if observed_source and _capture_path_match(observed_source, [source]):
        return True
    observed_capture = str(item.get("observed_capture") or "")
    return bool(observed_capture and _capture_path_match(observed_capture, [source]))


def _linux_control_action_plan_guarded_write_readiness(actions: list[dict[str, Any]]) -> dict[str, Any]:
    safe_actions = [
        action
        for action in actions
        if isinstance(action, dict) and action.get("phase") == "safe-experiment"
    ]
    ready_actions = [
        action
        for action in safe_actions
        if action.get("status") == "ready"
    ]
    ready_write_ids: list[str] = []
    needs_validation_ids: list[str] = []
    refresh_ids: list[str] = []
    failed_ids: list[str] = []
    invalid_schema_ids: list[str] = []
    incomplete_ids: list[str] = []
    unavailable_ids: list[str] = []
    for action in ready_actions:
        action_id = str(action.get("id") or "")
        validation = action.get("pre_write_validation")
        if not isinstance(validation, dict):
            unavailable_ids.append(action_id)
            continue
        status = str(validation.get("validation_status") or "")
        if status == "passed" and validation.get("allows_guarded_write") is True:
            ready_write_ids.append(action_id)
        elif status == "needs-run":
            needs_validation_ids.append(action_id)
        elif status == "needs-recompare-after-refresh":
            needs_validation_ids.append(action_id)
        elif status == "refresh-live-snapshot":
            refresh_ids.append(action_id)
        elif status == "failed":
            failed_ids.append(action_id)
        elif status == "invalid-schema":
            invalid_schema_ids.append(action_id)
        else:
            incomplete_ids.append(action_id)
    if not safe_actions:
        status = "no-guarded-write-actions"
    elif failed_ids:
        status = "blocked-by-failed-pre-write-validation"
    elif invalid_schema_ids:
        status = "invalid-pre-write-validation-schema"
    elif refresh_ids:
        status = "refresh-live-snapshot"
    elif needs_validation_ids:
        status = "needs-pre-write-validation"
    elif incomplete_ids or unavailable_ids:
        status = "incomplete-pre-write-validation"
    elif ready_write_ids and len(ready_write_ids) == len(ready_actions):
        status = "guarded-write-ready"
    elif ready_write_ids:
        status = "partially-guarded-write-ready"
    else:
        status = "no-ready-guarded-write-actions"
    return {
        "status": status,
        "safe_action_count": len(safe_actions),
        "preflight_ready_action_count": len(ready_actions),
        "guarded_write_ready_count": len(ready_write_ids),
        "needs_pre_write_validation_count": len(needs_validation_ids),
        "refresh_live_snapshot_count": len(refresh_ids),
        "failed_pre_write_validation_count": len(failed_ids),
        "invalid_schema_pre_write_validation_count": len(invalid_schema_ids),
        "incomplete_pre_write_validation_count": len(incomplete_ids) + len(unavailable_ids),
        "ready_action_ids": ready_write_ids,
        "needs_pre_write_validation_action_ids": needs_validation_ids,
        "refresh_live_snapshot_action_ids": refresh_ids,
        "failed_pre_write_validation_action_ids": failed_ids,
        "invalid_schema_pre_write_validation_action_ids": invalid_schema_ids,
        "incomplete_pre_write_validation_action_ids": [*incomplete_ids, *unavailable_ids],
    }


def _linux_control_action_plan_packet_compare_validation(experiment_summary: dict[str, Any]) -> dict[str, Any]:
    runs = experiment_summary.get("packet_compare_runs") if isinstance(experiment_summary, dict) else None
    run_items = runs if isinstance(runs, list) else []
    valid_run_items = [
        item
        for item in run_items
        if isinstance(item, dict)
        and item.get("schema_version") == LINUX_CONTROL_PACKET_COMPARE_SCHEMA_VERSION
    ]
    target_state_status_counts = Counter(
        str(item.get("target_state_status") or "missing")
        for item in valid_run_items
    )
    missing_field_counts = Counter(
        field
        for item in valid_run_items
        for field in _ordered_strings_from_list(item.get("target_state_missing_packet_fields"))
    )
    placeholder_field_counts = Counter(
        field
        for item in valid_run_items
        for field in _ordered_strings_from_list(item.get("target_state_placeholder_fields"))
    )
    pass_count = sum(
        1
        for item in valid_run_items
        if _linux_control_action_plan_compare_result_passes_gate(item)
    )
    return {
        "run_count": len(run_items),
        "schema_version_required": LINUX_CONTROL_PACKET_COMPARE_SCHEMA_VERSION,
        "valid_schema_count": len(valid_run_items),
        "invalid_schema_count": len(run_items) - len(valid_run_items),
        "pass_count": pass_count,
        "failed_count": sum(
            1
            for item in valid_run_items
            if item.get("write_gate_status") == "fail"
        ),
        "refresh_live_snapshot_count": sum(
            1
            for item in valid_run_items
            if item.get("write_gate_status") == "refresh-live-snapshot"
        ),
        "target_state_status_counts": dict(sorted(target_state_status_counts.items())),
        "target_state_missing_field_counts": dict(sorted(missing_field_counts.items())),
        "target_state_placeholder_field_counts": dict(sorted(placeholder_field_counts.items())),
        "target_state_snapshot_metadata_available_count": sum(
            1 for item in valid_run_items if item.get("target_state_snapshot_metadata_available")
        ),
        "target_state_snapshot_state_available_count": sum(
            1 for item in valid_run_items if item.get("target_state_snapshot_state_available")
        ),
        "target_state_raw_hex_available_count": sum(
            1 for item in valid_run_items if item.get("target_state_raw_hex_available")
        ),
        "target_state_live_snapshot_refresh_required_count": sum(
            1 for item in valid_run_items if item.get("target_state_live_snapshot_refresh_required")
        ),
        "target_state_placeholder_run_count": sum(
            1
            for item in valid_run_items
            if _ordered_strings_from_list(item.get("target_state_placeholder_fields"))
        ),
        "runs": [
            item
            for item in run_items
            if isinstance(item, dict)
        ],
    }


def _linux_control_action_plan_live_snapshot_context(experiment_summary: dict[str, Any]) -> dict[str, Any]:
    context = experiment_summary.get("live_snapshot_context") if isinstance(experiment_summary, dict) else None
    if isinstance(context, dict):
        devices = context.get("devices") if isinstance(context.get("devices"), dict) else {}
        device_map = {
            str(mac): dict(device)
            for mac, device in devices.items()
            if isinstance(device, dict)
        }
        return {
            "status": str(context.get("status") or ("available" if device_map else "missing")),
            "run_count": int(context.get("run_count", 0) or 0),
            "device_count": int(context.get("device_count", len(device_map)) or 0),
            "devices": dict(sorted(device_map.items())),
        }
    devices = experiment_summary.get("live_snapshot_devices") if isinstance(experiment_summary, dict) else None
    device_map = {
        str(mac): dict(device)
        for mac, device in devices.items()
        if isinstance(devices, dict) and isinstance(device, dict)
    } if isinstance(devices, dict) else {}
    runs = experiment_summary.get("live_snapshot_runs") if isinstance(experiment_summary, dict) else None
    run_items = runs if isinstance(runs, list) else []
    return {
        "status": "available" if device_map else "missing",
        "run_count": len(run_items),
        "device_count": len(device_map),
        "devices": dict(sorted(device_map.items())),
    }


def _linux_control_action_plan_live_snapshot_refresh(
    operation: dict[str, Any],
    experiment_summary: dict[str, Any],
    *,
    experiment_dir: str = "",
) -> dict[str, Any]:
    context = _linux_control_action_plan_live_snapshot_context(experiment_summary)
    devices = context.get("devices") if isinstance(context.get("devices"), dict) else {}
    target_id = _linux_control_action_plan_target_id(operation)
    target_mac = target_id.split("@", 1)[0].lower() if target_id else ""
    device = devices.get(target_mac) if target_mac else None
    command = _linux_control_action_plan_live_snapshot_refresh_command(experiment_dir)
    return {
        "available": isinstance(device, dict),
        "target_id": target_id,
        "target_mac": target_mac,
        "command": command,
        "save_path": _linux_control_action_plan_live_snapshot_refresh_path(experiment_dir),
        "snapshot_path": str(device.get("snapshot_path") or "") if isinstance(device, dict) else "",
        "snapshot_source": str(device.get("snapshot_source") or "") if isinstance(device, dict) else "",
        "command_sequence": device.get("command_sequence") if isinstance(device, dict) else None,
        "raw_hex_available": bool(isinstance(device, dict) and device.get("raw_hex")),
    }


def _linux_control_action_plan_live_snapshot_refresh_path(experiment_dir: str) -> str:
    if not experiment_dir:
        return ""
    return str(Path(experiment_dir).expanduser() / "live-list-refresh.json")


def _linux_control_action_plan_live_snapshot_refresh_command(experiment_dir: str) -> str:
    save_path = _linux_control_action_plan_live_snapshot_refresh_path(experiment_dir)
    if save_path:
        return _tool_command("--save-json", save_path, "live-list")
    return _tool_command("live-list")


def _linux_control_action_plan_capture_commands(
    missing_scenarios: list[dict[str, Any]],
    preflight: dict[str, Any],
) -> list[str]:
    capture_base = str(preflight.get("capture_base") or "")
    commands: list[str] = []
    if capture_base:
        commands.append(_tool_command("windows-capture-plan", "--capture-base", capture_base))
    for scenario in missing_scenarios:
        capture_file = str(scenario.get("capture_file") or "")
        if capture_file:
            commands.append(f"capture missing scenario: {capture_file}")
    return _unique_preserve_order(commands)


def _linux_control_action_plan_windows_actions(missing_scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for scenario in missing_scenarios:
        if not isinstance(scenario, dict):
            continue
        result.append(
            {
                "id": str(scenario.get("id") or ""),
                "capture_file": str(scenario.get("capture_file") or ""),
                "goal": str(scenario.get("goal") or ""),
                "expected_evidence": list(scenario.get("expected_evidence", []) if isinstance(scenario.get("expected_evidence"), list) else []),
                "windows_actions": list(scenario.get("windows_actions", []) if isinstance(scenario.get("windows_actions"), list) else []),
            }
        )
    return result


def _linux_control_action_plan_post_capture_commands(missing_scenarios: list[dict[str, Any]]) -> list[str]:
    commands: list[str] = []
    for scenario in missing_scenarios:
        planned = scenario.get("planned_linux_commands")
        if isinstance(planned, list):
            commands.extend(str(command) for command in planned if command)
    return _unique_preserve_order(commands)


def _linux_control_action_plan_packet_validation_commands(
    operation: dict[str, Any],
    preflight: dict[str, Any],
) -> list[str]:
    name = str(operation.get("operation") or "")
    if name not in {"live-pwm", "live-pwm-sync", "live-pwm-mirror", "live-bind", "live-unbind", "live-rgb", "live-rainbow"}:
        return []
    if str(operation.get("preflight_status") or "") != "ready":
        return []
    path = str(preflight.get("path") or "")
    if not path:
        return []
    target_id = _linux_control_action_plan_target_id(operation)
    common_options = _linux_control_action_plan_packet_validation_common_options(
        preflight,
        target_id,
        operation=name,
    )
    commands = [
        _tool_command("linux-control-packet-preview", path, name, *common_options)
    ]
    for capture_path in operation.get("source_capture_paths", []):
        capture_text = str(capture_path or "")
        if not capture_text:
            continue
        commands.append(
            _tool_command(
                "linux-control-packet-compare",
                path,
                name,
                capture_text,
                *common_options,
            )
        )
    return _unique_preserve_order(commands)


def _linux_control_action_plan_packet_validation_common_options(
    preflight: dict[str, Any],
    target_id: str,
    *,
    operation: str = "",
) -> list[str]:
    options: list[str] = []
    if target_id:
        options.extend(("--target-id", target_id))
    for key, flag in (
        ("capture_base", "--capture-base"),
        ("experiment_dir", "--experiment-dir"),
        ("sys_root", "--sys-root"),
        ("dev_root", "--dev-root"),
    ):
        value = str(preflight.get(key) or "")
        if value:
            options.extend((flag, value))
    if operation in {"live-rgb", "live-rainbow"}:
        _append_int_option(options, "--led-count", preflight.get("led_count"))
        _append_int_option(options, "--effect-index", preflight.get("effect_index"))
    if operation == "live-rainbow":
        _append_int_option(options, "--frame-count", preflight.get("rainbow_frames"))
        _append_int_option(options, "--interval-ms", preflight.get("interval_ms"))
    return options


def _append_int_option(options: list[str], flag: str, value: Any) -> None:
    if isinstance(value, bool):
        return
    try:
        number = int(value)
    except (TypeError, ValueError):
        return
    options.extend((flag, str(number)))


def _linux_control_action_plan_target_id(operation: dict[str, Any]) -> str:
    runtime_context = operation.get("runtime_context")
    if not isinstance(runtime_context, dict):
        return ""
    contexts = runtime_context.get("contexts")
    if isinstance(contexts, list):
        for context in contexts:
            if not isinstance(context, dict):
                continue
            mac = str(context.get("mac") or "")
            channel = context.get("channel")
            rx_type = context.get("rx_type")
            if mac and channel is not None and rx_type is not None:
                return _linux_control_target_registry_id(mac, channel, rx_type)
    target_macs = runtime_context.get("target_macs")
    channels = runtime_context.get("channels")
    rx_types = runtime_context.get("rx_types")
    if isinstance(target_macs, list) and isinstance(channels, list) and isinstance(rx_types, list):
        mac = next((str(item) for item in target_macs if isinstance(item, str) and item), "")
        channel = next((item for item in channels if isinstance(item, int)), None)
        rx_type = next((item for item in rx_types if isinstance(item, int)), None)
        if mac and channel is not None and rx_type is not None:
            return _linux_control_target_registry_id(mac, channel, rx_type)
    return ""


def _linux_control_action_id(phase: str, operation: str) -> str:
    return f"{phase}:{operation}" if operation else phase


def _linux_control_action_plan_action_status(preflight_status: str) -> str:
    if preflight_status in {"ready", "ready-for-readonly-validation"}:
        return "ready"
    if preflight_status in {"missing-hardware", "needs-usb-permission"}:
        return "blocked"
    if preflight_status in {"needs-capture-evidence", "needs-linux-target"}:
        return "needs-evidence"
    return "pending"


def _linux_control_action_plan_reason(preflight_status: str) -> str:
    return {
        "ready": "Local USB access and evidence are sufficient for the guarded command.",
        "ready-for-readonly-validation": "Local receiver access is available; run readonly validation before writes.",
        "needs-usb-permission": "The current user cannot read/write the required USB node.",
        "missing-hardware": "The required L-Wireless USB device is not visible.",
        "needs-capture-evidence": "Official Windows USBPcap evidence is still missing for this operation.",
        "needs-linux-target": "The capture does not yet identify a high-confidence Linux sender target.",
    }.get(preflight_status, preflight_status or "not evaluated")


def _linux_control_action_plan_primary_command(
    operation: str,
    commands: list[str],
    safety: dict[str, Any],
) -> str:
    safe_cli = str(safety.get("safe_cli") or "")
    if safe_cli:
        for command in commands:
            if safe_cli in command:
                return command
    if operation == "receiver-snapshot":
        for command in commands:
            if "validate-readonly" in command:
                return command
    return commands[0] if commands else ""


def _linux_control_action_plan_status(
    preflight: dict[str, Any],
    actions: list[dict[str, Any]],
) -> str:
    preflight_status = str(preflight.get("status") or "")
    if preflight_status in {"no-l-wireless-hardware", "partial-hardware", "missing-dev-node", "needs-usb-permission"}:
        return preflight_status
    if any(item.get("phase") == "safe-experiment" and item.get("status") == "ready" for item in actions):
        return "ready-for-safe-experiments"
    if any(item.get("phase") == "validate-readonly" and item.get("status") == "ready" for item in actions):
        return "ready-for-readonly-validation"
    if any(item.get("status") == "needs-evidence" for item in actions):
        return "needs-capture-evidence"
    return preflight_status or "not-evaluated"


def _linux_control_action_plan_commands(actions: list[dict[str, Any]]) -> list[str]:
    commands: list[str] = []
    for action in actions:
        command = action.get("command")
        if isinstance(command, str) and command:
            commands.append(command)
        action_commands = action.get("commands")
        if isinstance(action_commands, list):
            commands.extend(str(item) for item in action_commands if item)
    return _unique_preserve_order(commands)


def _linux_control_action_plan_next_commands(actions: list[dict[str, Any]]) -> list[str]:
    commands: list[str] = []
    for action in actions:
        execution = action.get("execution")
        if isinstance(execution, dict):
            next_command = execution.get("next_command")
            if isinstance(next_command, str) and next_command:
                commands.append(next_command)
                continue
        command = action.get("command")
        if isinstance(command, str) and command:
            commands.append(command)
    return _unique_preserve_order(commands)


def _linux_control_target_registry_targets(action_plan: dict[str, Any]) -> list[dict[str, Any]]:
    actions = action_plan.get("actions")
    if not isinstance(actions, list):
        return []
    registry: dict[tuple[str, str, str], dict[str, Any]] = {}
    for action in actions:
        if not isinstance(action, dict) or not action.get("operation"):
            continue
        runtime_context = action.get("runtime_context")
        if not isinstance(runtime_context, dict):
            continue
        for context in _linux_control_target_context_candidates(runtime_context):
            key = (
                str(context.get("mac") or ""),
                str(context.get("channel") or ""),
                str(context.get("rx_type") or ""),
            )
            if not key[0]:
                continue
            target = registry.setdefault(key, _linux_control_target_registry_base(context))
            _linux_control_target_registry_merge_action(target, action)
    _linux_control_target_registry_merge_live_snapshot_context(registry, action_plan)
    return sorted(
        (_linux_control_target_registry_finalize(target) for target in registry.values()),
        key=lambda item: (
            item.get("runtime_context_status") != "complete",
            str(item.get("mac") or ""),
            str(item.get("channel") or ""),
            str(item.get("rx_type") or ""),
        ),
    )


def _linux_control_target_registry_merge_live_snapshot_context(
    registry: dict[tuple[str, str, str], dict[str, Any]],
    action_plan: dict[str, Any],
) -> None:
    live_context = action_plan.get("live_snapshot_context")
    devices = live_context.get("devices") if isinstance(live_context, dict) and isinstance(live_context.get("devices"), dict) else {}
    if not devices:
        return
    for target in registry.values():
        mac = str(target.get("mac") or "").lower()
        snapshot = devices.get(mac)
        if not isinstance(snapshot, dict):
            continue
        master_mac = str(snapshot.get("master_mac") or "").lower()
        if master_mac and not _is_zero_mac(master_mac):
            target["master_mac"] = master_mac
        for field in ("channel", "rx_type"):
            if not isinstance(target.get(field), int) and isinstance(snapshot.get(field), int):
                target[field] = snapshot[field]
        _merge_target_registry_snapshot_fields(target, snapshot)
        target["id"] = _linux_control_target_registry_id(
            str(target.get("mac") or ""),
            target.get("channel"),
            target.get("rx_type"),
        )


def _linux_control_target_context_candidates(runtime_context: dict[str, Any]) -> list[dict[str, Any]]:
    contexts = runtime_context.get("contexts")
    result = [dict(item) for item in contexts if isinstance(item, dict)] if isinstance(contexts, list) else []
    if result:
        return result
    macs = sorted(_strings_from_list(runtime_context.get("target_macs")))
    channels = _ints_from_list(runtime_context.get("channels"))
    rx_types = _ints_from_list(runtime_context.get("rx_types"))
    master_macs = sorted(_strings_from_list(runtime_context.get("master_macs")))
    if not macs:
        return []
    return [
        {
            "mac": macs[0],
            "channel": channels[0] if len(channels) == 1 else None,
            "rx_type": rx_types[0] if len(rx_types) == 1 else None,
            "master_mac": master_macs[0] if len(master_macs) == 1 else None,
            "confidence": "partial",
            "source": str(runtime_context.get("source") or "manifest-runtime-context"),
            "candidate_channels": channels if len(channels) != 1 else [],
            "candidate_rx_types": rx_types if len(rx_types) != 1 else [],
            "candidate_master_macs": master_macs if len(master_macs) != 1 else [],
        }
    ]


def _linux_control_target_registry_base(context: dict[str, Any]) -> dict[str, Any]:
    mac = str(context.get("mac") or "").lower()
    channel = context.get("channel")
    rx_type = context.get("rx_type")
    target = {
        "id": _linux_control_target_registry_id(mac, channel, rx_type),
        "mac": mac,
        "channel": channel if isinstance(channel, int) else None,
        "rx_type": rx_type if isinstance(rx_type, int) else None,
        "master_mac": str(context.get("master_mac") or "").lower(),
        "context_confidence": str(context.get("confidence") or "partial"),
        "context_source": str(context.get("source") or ""),
        "candidate_channels": list(context.get("candidate_channels", []) if isinstance(context.get("candidate_channels"), list) else []),
        "candidate_rx_types": list(context.get("candidate_rx_types", []) if isinstance(context.get("candidate_rx_types"), list) else []),
        "candidate_master_macs": list(context.get("candidate_master_macs", []) if isinstance(context.get("candidate_master_macs"), list) else []),
        "operations": set(),
        "capabilities": set(),
        "risks": set(),
        "required_vid_pids": set(),
        "ready_operations": set(),
        "needs_evidence_operations": set(),
        "action_statuses": {},
        "observed_parameters": {},
        "commands": [],
        "requires_confirmation": False,
        "writes_usb": False,
        "visual_confirmation_required": False,
        "pairing_recovery_required": False,
    }
    _merge_target_registry_snapshot_fields(target, context)
    return target


def _merge_target_registry_snapshot_fields(target: dict[str, Any], context: dict[str, Any]) -> None:
    for key in (
        "device_type",
        "fan_count",
        "pwm_values",
        "fan_rpm",
        "command_sequence",
        "raw_hex",
        "snapshot_index",
        "snapshot_source",
        "snapshot_scenario_id",
        "snapshot_scenario_path",
        "snapshot_path",
    ):
        if key in context and context[key] not in (None, "", []):
            target[key] = context[key]


def _linux_control_target_registry_id(mac: str, channel: Any, rx_type: Any) -> str:
    channel_text = str(channel) if isinstance(channel, int) else "unknown-channel"
    rx_type_text = str(rx_type) if isinstance(rx_type, int) else "unknown-rxtype"
    return f"{mac}@ch{channel_text}/rx{rx_type_text}"


def _linux_control_target_registry_merge_action(target: dict[str, Any], action: dict[str, Any]) -> None:
    operation = str(action.get("operation") or "")
    if not operation:
        return
    target["operations"].add(operation)
    capability = str(action.get("capability") or "")
    if capability:
        target["capabilities"].add(capability)
    risk = str(action.get("risk") or "")
    if risk:
        target["risks"].add(risk)
    required_vid_pid = str(action.get("required_vid_pid") or "")
    if required_vid_pid:
        target["required_vid_pids"].add(required_vid_pid)
    status = str(action.get("status") or "")
    target["action_statuses"][operation] = {
        "status": status,
        "preflight_status": str(action.get("preflight_status") or ""),
        "phase": str(action.get("phase") or ""),
    }
    if status == "ready":
        target["ready_operations"].add(operation)
    if status == "needs-evidence":
        target["needs_evidence_operations"].add(operation)
    observed_parameters = action.get("observed_parameters")
    if isinstance(observed_parameters, dict) and observed_parameters:
        target["observed_parameters"][operation] = dict(observed_parameters)
    command = action.get("command")
    if isinstance(command, str) and command:
        target["commands"].append(command)
    target["requires_confirmation"] = bool(target["requires_confirmation"] or action.get("requires_confirmation"))
    target["writes_usb"] = bool(target["writes_usb"] or action.get("writes_usb"))
    target["visual_confirmation_required"] = bool(
        target["visual_confirmation_required"] or action.get("visual_confirmation_required")
    )
    target["pairing_recovery_required"] = bool(
        target["pairing_recovery_required"] or action.get("pairing_recovery_required")
    )


def _linux_control_target_registry_finalize(target: dict[str, Any]) -> dict[str, Any]:
    runtime_status = _linux_control_target_registry_runtime_status(target)
    packet_ready = runtime_status == "complete" and bool(target.get("master_mac"))
    payload = {
        "id": target["id"],
        "mac": target["mac"],
        "channel": target["channel"],
        "rx_type": target["rx_type"],
        "master_mac": target["master_mac"],
        "runtime_context_status": runtime_status,
        "packet_build_ready": packet_ready,
        "requires_live_snapshot_before_write": True,
        "missing_packet_fields": _linux_control_target_registry_missing_fields(target, packet_ready),
        "wireless_device_info_template": _linux_control_target_wireless_device_template(target),
        "context_confidence": target["context_confidence"],
        "context_source": target["context_source"],
        "candidate_channels": target["candidate_channels"],
        "candidate_rx_types": target["candidate_rx_types"],
        "candidate_master_macs": target["candidate_master_macs"],
        "operations": sorted(target["operations"]),
        "capabilities": sorted(target["capabilities"]),
        "risks": sorted(target["risks"]),
        "required_vid_pids": sorted(target["required_vid_pids"]),
        "ready_operations": sorted(target["ready_operations"]),
        "needs_evidence_operations": sorted(target["needs_evidence_operations"]),
        "action_statuses": dict(sorted(target["action_statuses"].items())),
        "observed_parameters": dict(sorted(target["observed_parameters"].items())),
        "commands": _unique_preserve_order(target["commands"]),
        "requires_confirmation": bool(target["requires_confirmation"]),
        "writes_usb": bool(target["writes_usb"]),
        "visual_confirmation_required": bool(target["visual_confirmation_required"]),
        "pairing_recovery_required": bool(target["pairing_recovery_required"]),
    }
    snapshot = _linux_control_target_snapshot_payload(target)
    if snapshot:
        payload["snapshot_device_state"] = snapshot
    return payload


def _linux_control_target_snapshot_payload(target: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in (
        "device_type",
        "fan_count",
        "pwm_values",
        "fan_rpm",
        "command_sequence",
        "raw_hex",
        "snapshot_index",
        "snapshot_source",
        "snapshot_scenario_id",
        "snapshot_scenario_path",
        "snapshot_path",
    ):
        value = target.get(key)
        if value not in (None, "", []):
            payload[key] = value
    return payload


def _linux_control_target_registry_runtime_status(target: dict[str, Any]) -> str:
    if target.get("mac") and isinstance(target.get("channel"), int) and isinstance(target.get("rx_type"), int):
        return "complete"
    if target.get("mac"):
        return "partial"
    return "missing"


def _linux_control_target_registry_missing_fields(
    target: dict[str, Any],
    packet_ready: bool,
) -> list[str]:
    missing: list[str] = []
    if not target.get("mac"):
        missing.append("mac")
    if not isinstance(target.get("channel"), int):
        missing.append("channel")
    if not isinstance(target.get("rx_type"), int):
        missing.append("rx_type")
    if not target.get("master_mac"):
        missing.append("master_mac")
    if packet_ready:
        for field in ("device_type", "fan_count", "pwm_values", "fan_rpm", "command_sequence"):
            if not _linux_control_target_has_packet_field(target, field):
                missing.append(field)
        if not _linux_control_target_has_packet_field(target, "raw_hex"):
            missing.append("raw")
    return missing


def _linux_control_target_has_packet_field(target: dict[str, Any], field: str) -> bool:
    value = target.get(field)
    if field in {"device_type", "fan_count", "command_sequence"}:
        return isinstance(value, int)
    if field in {"pwm_values", "fan_rpm"}:
        return len(_ordered_ints_from_list(value)) == 4
    if field == "raw_hex":
        raw_hex = str(value or "")
        return bool(raw_hex)
    return value not in (None, "", [])


def _linux_control_target_wireless_device_template(target: dict[str, Any]) -> dict[str, Any]:
    pwm_values = _ordered_ints_from_list(target.get("pwm_values"))
    fan_rpm = _ordered_ints_from_list(target.get("fan_rpm"))
    notes = [
        "Use live-list before real writes to refresh command_sequence and raw receiver state.",
        "This template is sufficient for packet dry-runs only when packet_build_ready is true.",
    ]
    if target.get("raw_hex"):
        notes.append("Snapshot state came from capture evidence; live writes should still refresh it from the real receiver.")
    return {
        "class": "usb9_lcd.lianli.wireless.WirelessDeviceInfo",
        "kwargs": {
            "mac": target.get("mac") or "",
            "master_mac": target.get("master_mac") or "00:00:00:00:00:00",
            "channel": target.get("channel"),
            "rx_type": target.get("rx_type"),
            "device_type": target.get("device_type") if isinstance(target.get("device_type"), int) else None,
            "fan_count": target.get("fan_count") if isinstance(target.get("fan_count"), int) else None,
            "pwm_values": (pwm_values + [0, 0, 0, 0])[:4],
            "fan_rpm": (fan_rpm + [0, 0, 0, 0])[:4],
            "command_sequence": target.get("command_sequence") if isinstance(target.get("command_sequence"), int) else 0,
            "raw_hex": str(target.get("raw_hex") or ""),
        },
        "notes": notes,
    }


def _linux_control_target_registry_status(
    action_plan: dict[str, Any],
    targets: list[dict[str, Any]],
) -> str:
    if not targets:
        return str(action_plan.get("status") or "no-target-context")
    if any(target.get("packet_build_ready") for target in targets):
        return "packet-build-ready"
    if any(target.get("runtime_context_status") == "partial" for target in targets):
        return "partial-target-context"
    return "no-target-context"


def _linux_control_packet_preview_target(
    registry: dict[str, Any],
    target_id: str,
) -> dict[str, Any] | None:
    targets = registry.get("targets")
    if not isinstance(targets, list):
        return None
    if target_id:
        return next(
            (
                target
                for target in targets
                if isinstance(target, dict) and str(target.get("id") or "") == target_id
            ),
            None,
        )
    return next(
        (
            target
            for target in targets
            if isinstance(target, dict) and target.get("packet_build_ready")
        ),
        next((target for target in targets if isinstance(target, dict)), None),
    )


def _linux_control_packet_preview_packets(
    target: dict[str, Any],
    control_operation: str,
    *,
    pwm_values: tuple[int, int, int, int],
    motherboard_pwm: int,
    color: tuple[int, int, int],
    led_count: int,
    frame_count: int,
    interval_ms: int,
    effect_index: int,
) -> list[bytes]:
    backend = LianLiWirelessBackend()
    device = _linux_control_packet_preview_device(target)
    if control_operation == "live-pwm":
        return backend.build_pwm_packets(device, pwm_values)
    if control_operation == "live-pwm-sync":
        return backend.build_motherboard_pwm_sync_packets(device)
    if control_operation == "live-pwm-mirror":
        return backend.build_motherboard_pwm_mirror_packets(device, motherboard_pwm)
    if control_operation == "live-bind":
        desired_master = device.master_mac if not _is_zero_mac(device.master_mac) else "10:20:30:40:50:60"
        unbound = WirelessDeviceInfo(
            mac=device.mac,
            master_mac="00:00:00:00:00:00",
            channel=device.channel,
            rx_type=0,
            device_type=device.device_type,
            fan_count=device.fan_count,
            pwm_values=device.pwm_values,
            fan_rpm=device.fan_rpm,
            command_sequence=0,
            raw=device.raw,
        )
        return backend.build_bind_packets(
            unbound,
            master_mac=desired_master,
            rx_type=device.rx_type or 3,
            channel=device.channel,
        )
    if control_operation == "live-unbind":
        return backend.build_unbind_packets(device, channel=device.channel)
    if control_operation == "live-rgb":
        return backend.build_static_rgb_packets(
            device,
            color,
            interval_ms=interval_ms,
            effect_index=effect_index,
            led_count=led_count,
        )
    if control_operation == "live-rainbow":
        return backend.build_rainbow_rgb_packets(
            device,
            frame_count=frame_count,
            interval_ms=interval_ms,
            effect_index=effect_index,
            led_count=led_count,
        )
    raise LianLiWirelessError(f"unsupported packet preview operation: {control_operation}")


def _linux_control_packet_preview_target_state(target: dict[str, Any]) -> dict[str, Any]:
    missing_fields = _ordered_strings_from_list(target.get("missing_packet_fields"))
    snapshot_state = target.get("snapshot_device_state")
    snapshot_metadata_available = isinstance(snapshot_state, dict) and bool(snapshot_state)
    snapshot_available = _linux_control_packet_preview_snapshot_state_available(snapshot_state)
    placeholder_fields = [
        field
        for field in ("device_type", "fan_count", "pwm_values", "fan_rpm", "command_sequence", "raw")
        if field in missing_fields
    ]
    if placeholder_fields:
        status = "dry-run-uses-placeholders"
    elif snapshot_available:
        status = "capture-backed-target-state"
    elif target.get("packet_build_ready"):
        status = "minimal-target-state"
    else:
        status = "incomplete-target-state"
    return {
        "status": status,
        "packet_build_ready": bool(target.get("packet_build_ready")),
        "runtime_context_status": str(target.get("runtime_context_status") or ""),
        "requires_live_snapshot_before_write": bool(target.get("requires_live_snapshot_before_write")),
        "missing_packet_fields": missing_fields,
        "placeholder_fields": placeholder_fields,
        "snapshot_metadata_available": snapshot_metadata_available,
        "snapshot_state_available": snapshot_available,
        "raw_hex_available": bool(isinstance(snapshot_state, dict) and snapshot_state.get("raw_hex")),
        "live_snapshot_refresh_required": bool(
            target.get("requires_live_snapshot_before_write") or placeholder_fields
        ),
        "notes": _linux_control_packet_preview_target_state_notes(status, placeholder_fields),
    }


def _linux_control_packet_preview_snapshot_state_available(snapshot_state: Any) -> bool:
    if not isinstance(snapshot_state, dict):
        return False
    for field in ("device_type", "fan_count", "pwm_values", "fan_rpm", "command_sequence", "raw_hex"):
        value = snapshot_state.get(field)
        if value not in (None, "", []):
            return True
    return False


def _linux_control_packet_preview_target_state_notes(
    status: str,
    placeholder_fields: list[str],
) -> list[str]:
    if placeholder_fields:
        return [
            "Packet preview can be generated, but missing receiver-state fields are filled with placeholders.",
            "Run live-list and then linux-control-packet-compare before any WRITE-LIANLI guarded write.",
        ]
    if status == "capture-backed-target-state":
        return [
            "Target state includes capture-derived receiver snapshot fields.",
            "Refresh with live-list before real writes so command_sequence/raw receiver state are current.",
        ]
    if status == "minimal-target-state":
        return [
            "Packet preview has MAC/channel/rx_type/master MAC, but no receiver snapshot payload.",
            "Use this for dry-run structure inspection only until live-list refreshes receiver state.",
        ]
    return ["Target context is incomplete; collect receiver snapshot evidence before packet preview can be trusted."]


def _linux_control_packet_preview_pwm_values(
    target: dict[str, Any],
    control_operation: str,
    requested: Iterable[int] | None,
) -> tuple[int, int, int, int]:
    if requested is not None:
        return _pwm_tuple_from_values(requested)
    if control_operation == "live-pwm":
        observed = _linux_control_packet_preview_observed_pwm_values(target)
        if observed is not None:
            return observed
    return (120, 120, 120, 120)


def _linux_control_packet_preview_pwm_source(
    target: dict[str, Any],
    control_operation: str,
    requested: Iterable[int] | None,
) -> str:
    if requested is not None:
        return "user"
    if control_operation == "live-pwm" and _linux_control_packet_preview_observed_pwm_values(target) is not None:
        return "capture-evidence"
    return "default"


def _linux_control_packet_preview_rgb_parameters(
    target: dict[str, Any],
    control_operation: str,
    *,
    requested_color: tuple[int, int, int] | None,
    requested_led_count: int | None,
    requested_frame_count: int | None,
    requested_interval_ms: int | None,
    requested_effect_index: int | None,
) -> dict[str, Any]:
    observed = _linux_control_packet_preview_observed_rgb_parameters(target, control_operation)
    color, color_source = _linux_control_packet_preview_rgb_color(requested_color, observed)
    led_count, led_count_source = _linux_control_packet_preview_int_parameter(
        requested_led_count,
        observed,
        "default_led_count",
        12,
    )
    frame_count, frame_count_source = _linux_control_packet_preview_int_parameter(
        requested_frame_count,
        observed,
        "default_frame_count",
        3,
    )
    interval_ms, interval_ms_source = _linux_control_packet_preview_int_parameter(
        requested_interval_ms,
        observed,
        "default_interval_ms",
        40,
        minimum=0,
        maximum=65535,
    )
    effect_index, effect_index_source = _linux_control_packet_preview_int_parameter(
        requested_effect_index,
        observed,
        "default_effect_index",
        1,
        minimum=0,
    )
    return {
        "color": color,
        "color_source": color_source,
        "led_count": led_count,
        "led_count_source": led_count_source,
        "frame_count": frame_count,
        "frame_count_source": frame_count_source,
        "interval_ms": interval_ms,
        "interval_ms_source": interval_ms_source,
        "effect_index": effect_index,
        "effect_index_source": effect_index_source,
    }


def _linux_control_packet_preview_observed_rgb_parameters(
    target: dict[str, Any],
    control_operation: str,
) -> dict[str, Any]:
    observed_parameters = target.get("observed_parameters")
    if not isinstance(observed_parameters, dict):
        return {}
    observed = observed_parameters.get(control_operation)
    return dict(observed) if isinstance(observed, dict) else {}


def _linux_control_packet_preview_rgb_color(
    requested_color: tuple[int, int, int] | None,
    observed: dict[str, Any],
) -> tuple[tuple[int, int, int], str]:
    if requested_color is not None:
        color = _rgb_tuple_from_values(requested_color)
        return (color if color is not None else (255, 0, 0)), "user"
    observed_color = _rgb_tuple_from_values(observed.get("default_color"))
    if observed_color is not None:
        return observed_color, "capture-evidence"
    return (255, 0, 0), "default"


def _linux_control_packet_preview_int_parameter(
    requested: int | None,
    observed: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> tuple[int, str]:
    if requested is not None:
        return _clamp_int(requested, minimum=minimum, maximum=maximum), "user"
    observed_value = observed.get(key)
    if isinstance(observed_value, int):
        return _clamp_int(observed_value, minimum=minimum, maximum=maximum), "capture-evidence"
    return _clamp_int(default, minimum=minimum, maximum=maximum), "default"


def _clamp_int(value: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    result = max(minimum, int(value))
    if maximum is not None:
        result = min(maximum, result)
    return result


def _rgb_tuple_from_values(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return tuple(max(0, min(255, int(component))) for component in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _linux_control_packet_preview_observed_pwm_values(target: dict[str, Any]) -> tuple[int, int, int, int] | None:
    observed_parameters = target.get("observed_parameters")
    if not isinstance(observed_parameters, dict):
        return None
    pwm = observed_parameters.get("live-pwm")
    if not isinstance(pwm, dict):
        return None
    values = pwm.get("default_pwm_values")
    if not isinstance(values, list):
        return None
    return _pwm_tuple_from_values(values)


def _pwm_tuple_from_values(values: Iterable[int]) -> tuple[int, int, int, int]:
    result = [max(0, min(255, int(value))) for value in values]
    while len(result) < 4:
        result.append(result[-1] if result else 0)
    return tuple(result[:4])  # type: ignore[return-value]


def _linux_control_packet_preview_device(target: dict[str, Any]) -> WirelessDeviceInfo:
    template = target.get("wireless_device_info_template")
    kwargs = template.get("kwargs") if isinstance(template, dict) and isinstance(template.get("kwargs"), dict) else {}
    return WirelessDeviceInfo(
        mac=str(kwargs.get("mac") or target.get("mac") or ""),
        master_mac=str(kwargs.get("master_mac") or target.get("master_mac") or "00:00:00:00:00:00"),
        channel=int(kwargs.get("channel") if kwargs.get("channel") is not None else target.get("channel") or 0),
        rx_type=int(kwargs.get("rx_type") if kwargs.get("rx_type") is not None else target.get("rx_type") or 0),
        device_type=int(kwargs.get("device_type") or 0),
        fan_count=int(kwargs.get("fan_count") or 0),
        pwm_values=tuple(_ordered_ints_from_list(kwargs.get("pwm_values")) or [0, 0, 0, 0])[:4],
        fan_rpm=tuple(_ordered_ints_from_list(kwargs.get("fan_rpm")) or [0, 0, 0, 0])[:4],
        command_sequence=int(kwargs.get("command_sequence") or 0),
        raw=bytes.fromhex(str(kwargs.get("raw_hex") or "")) if kwargs.get("raw_hex") else bytes(42),
    )


def _linux_control_packet_preview_summary(packets: list[bytes]) -> dict[str, Any]:
    analysis = analyze_capture_packets(packets, source="linux-control-packet-preview")
    rf_frames = [
        _frame_summary(frame)
        for frame in analysis.get("rf_frames", [])
        if isinstance(frame, dict)
    ]
    return {
        "packet_count": len(packets),
        "packet_size": len(packets[0]) if packets else 0,
        "first_packet_hex": packets[0].hex() if packets else "",
        "last_packet_hex": packets[-1].hex() if packets else "",
        "first_packet_prefix_hex": packets[0][:32].hex() if packets else "",
        "packets": _linux_control_packet_preview_packet_items(packets, analysis),
        "rf_frames": rf_frames,
        "rf_operations": analysis.get("summary", {}).get("rf_operations", {})
        if isinstance(analysis.get("summary"), dict)
        else {},
    }


def _linux_control_packet_preview_packet_items(
    packets: list[bytes],
    analysis: dict[str, Any],
) -> list[dict[str, Any]]:
    records = analysis.get("records")
    records_by_index = {
        int(record["index"]): record
        for record in records
        if isinstance(record, dict) and isinstance(record.get("index"), int)
    } if isinstance(records, list) else {}
    frame_links = _linux_control_packet_preview_frame_links(analysis)
    items: list[dict[str, Any]] = []
    for index, packet in enumerate(packets):
        record = records_by_index.get(index, {})
        item: dict[str, Any] = {
            "index": index,
            "size": len(packet),
            "sha256": hashlib.sha256(packet).hexdigest(),
            "hex": packet.hex(),
            "prefix_hex": packet[:32].hex(),
            "kind": str(record.get("kind") or ""),
        }
        chunk = _linux_control_packet_preview_chunk(record)
        if chunk:
            item["rf_chunk"] = chunk
        linked_frames = frame_links.get(index, [])
        if linked_frames:
            item["rf_frames"] = linked_frames
        items.append(item)
    return items


def _linux_control_packet_compare_expected_packets(preview: dict[str, Any]) -> list[bytes]:
    packet_preview = preview.get("packet_preview")
    packet_items = packet_preview.get("packets") if isinstance(packet_preview, dict) else None
    if not isinstance(packet_items, list):
        return []
    packets: list[bytes] = []
    for item in packet_items:
        if not isinstance(item, dict):
            continue
        hex_text = str(item.get("hex") or "")
        try:
            packet = bytes.fromhex(hex_text)
        except ValueError:
            continue
        if packet:
            packets.append(packet)
    return packets


def _linux_control_packet_preview_frame_links(analysis: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    frames = analysis.get("rf_frames")
    result: dict[int, list[dict[str, Any]]] = {}
    if not isinstance(frames, list):
        return result
    for frame_index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            continue
        indexes = frame.get("chunk_packet_indexes")
        if not isinstance(indexes, list):
            continue
        sequences = frame.get("chunk_sequences")
        for member_index, packet_index in enumerate(indexes):
            if not isinstance(packet_index, int):
                continue
            link = {
                "rf_frame_index": frame_index,
                "operation": str(frame.get("operation") or ""),
                "target_mac": str(frame.get("target_mac") or ""),
                "master_mac": str(frame.get("master_mac") or ""),
                "channel": frame.get("channel"),
                "outer_rx_type": frame.get("outer_rx_type"),
                "payload_sha256": _payload_sha256(frame),
                "logical_operation": _counts_as_logical_operation(frame),
            }
            if isinstance(sequences, list) and member_index < len(sequences):
                link["chunk_sequence"] = sequences[member_index]
            for key in ("rx_type", "payload_channel", "pwm_values", "effect_index", "packet_index", "packet_count"):
                if key in frame:
                    link[key] = frame[key]
            result.setdefault(packet_index, []).append(link)
    return result


def _linux_control_packet_preview_chunk(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("kind") != "rf-chunk":
        return {}
    data_hex = str(record.get("data_hex") or "")
    return {
        "sequence": record.get("sequence"),
        "channel": record.get("channel"),
        "rx_type": record.get("rx_type"),
        "data_prefix_hex": data_hex[:32],
    }


def _capture_set_transport_contract(high_confidence_sender_targets: list[dict[str, Any]]) -> dict[str, Any]:
    sender = {
        "vid_pid": f"{RF_SENDER_VID:04x}:{RF_SENDER_PID:04x}",
        "role": "sender",
        "write_endpoint": "0x01",
        "read_endpoint": "0x81",
        "confidence": "default",
        "linux_hint": _linux_live_write_hint(f"{RF_SENDER_VID:04x}:{RF_SENDER_PID:04x}", "0x01", "sender"),
    }
    if high_confidence_sender_targets:
        target = high_confidence_sender_targets[0]
        sender.update(
            {
                "write_endpoint": str(target.get("write_endpoint") or "0x01"),
                "read_endpoint": str(target.get("read_endpoint") or "0x81"),
                "confidence": "high",
                "linux_hint": str(target.get("linux_hint") or ""),
                "source_operations": sorted(
                    {
                        str(item.get("operation") or "")
                        for item in high_confidence_sender_targets
                        if item.get("operation")
                    }
                ),
            }
        )
    return {
        "sender": sender,
        "receiver": {
            "vid_pid": f"{RF_RECEIVER_VID:04x}:{RF_RECEIVER_PID:04x}",
            "role": "receiver",
            "write_endpoint": "0x01",
            "read_endpoint": "0x81",
            "confidence": "default",
            "purpose": "receiver snapshot/list reads via LianLiWirelessBackend.list_devices",
        },
    }


def _capture_set_operation_contract(
    spec: dict[str, Any],
    matrix_by_operation: dict[str, dict[str, Any]],
    live_write_targets: list[dict[str, Any]],
    protocol_delta_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    operation = str(spec["operation"])
    matrix = matrix_by_operation.get(operation, {})
    targets = [
        target
        for target in live_write_targets
        if _capture_set_target_matches_control_spec(spec, target)
    ]
    transport_target = next(
        (
            target
            for target in targets
            if target.get("confidence") == "high" and target.get("role") == "sender"
        ),
        targets[0] if targets else None,
    )
    return {
        "operation": operation,
        "label": str(spec["label"]),
        "risk": str(spec["risk"]),
        "status": str(matrix.get("overall_status") or "not-evaluated"),
        "windows_evidence_status": str(matrix.get("windows_evidence_status") or ""),
        "linux_target_status": str(matrix.get("linux_target_status") or ""),
        "experiment_status": str(matrix.get("experiment_status") or ""),
        "backend": _capture_set_operation_backend_contract(operation),
        "required_runtime_fields": _capture_set_operation_required_fields(operation),
        "transport": _capture_set_operation_transport_payload(transport_target),
        "protocol_deltas": _capture_set_operation_protocol_deltas(spec, protocol_delta_summary),
        "scenario_statuses": list(matrix.get("scenario_statuses", []) if isinstance(matrix.get("scenario_statuses"), list) else []),
        "recommended_commands": list(matrix.get("recommended_commands", []) if isinstance(matrix.get("recommended_commands"), list) else []),
    }


def _capture_set_operation_protocol_deltas(
    spec: dict[str, Any],
    protocol_delta_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = protocol_delta_summary if isinstance(protocol_delta_summary, dict) else {}
    wanted_ids = {
        str(item)
        for item in spec.get("scenario_ids", [])
        if str(item)
    }
    matched: list[dict[str, Any]] = []
    unique_parameter_labels: list[str] = []
    next_focus: list[str] = []
    for item in summary.get("scenario_deltas", []):
        if not isinstance(item, dict):
            continue
        scenario_id = str(item.get("id") or "")
        if scenario_id not in wanted_ids:
            continue
        payload = {
            "id": scenario_id,
            "status": str(item.get("status") or ""),
            "path": str(item.get("path") or ""),
            "unique_rf_operations": _ordered_strings_from_list(item.get("unique_rf_operations")),
            "unique_parameter_labels": _ordered_strings_from_list(item.get("unique_parameter_labels")),
            "next_focus": str(item.get("next_focus") or ""),
        }
        matched.append(payload)
        unique_parameter_labels.extend(payload["unique_parameter_labels"])
        if payload["next_focus"]:
            next_focus.append(payload["next_focus"])
    return {
        "status": "matched" if matched else "none",
        "scenario_ids": sorted(wanted_ids),
        "unique_parameter_labels": _unique_preserve_order(unique_parameter_labels),
        "next_focus": _unique_preserve_order(next_focus),
        "scenario_deltas": matched,
    }


def _capture_set_operation_backend_contract(operation: str) -> dict[str, str]:
    return {
        "receiver-snapshot": {
            "read_method": "list_devices",
            "live_cli": "live-list",
            "validation_cli": "validate-readonly",
        },
        "live-pwm": {
            "builder": "build_pwm_packets",
            "send_method": "send_pwm",
            "dry_run_cli": "dry-run-pwm",
            "safe_cli": "safe-pwm-experiment",
        },
        "live-pwm-sync": {
            "builder": "build_motherboard_pwm_sync_packets",
            "send_method": "send_motherboard_pwm_sync",
            "dry_run_cli": "dry-run-pwm-sync",
            "safe_cli": "safe-sync-experiment",
        },
        "live-pwm-mirror": {
            "builder": "build_motherboard_pwm_mirror_packets",
            "send_method": "send_motherboard_pwm_mirror",
            "dry_run_cli": "dry-run-pwm-mirror",
            "safe_cli": "safe-pwm-mirror-experiment",
        },
        "live-bind": {
            "builder": "build_bind_packets",
            "send_method": "send_bind",
            "dry_run_cli": "dry-run-bind",
            "safe_cli": "safe-bind-experiment",
        },
        "live-unbind": {
            "builder": "build_unbind_packets",
            "send_method": "send_unbind",
            "dry_run_cli": "dry-run-unbind",
            "safe_cli": "safe-unbind-experiment",
        },
        "live-rgb": {
            "builder": "build_static_rgb_packets",
            "send_method": "send_static_rgb",
            "dry_run_cli": "dry-run-rgb",
            "safe_cli": "safe-rgb-experiment",
        },
        "live-rainbow": {
            "builder": "build_rainbow_rgb_packets",
            "send_method": "send_rainbow_rgb",
            "dry_run_cli": "dry-run-rainbow",
            "safe_cli": "safe-rainbow-experiment",
        },
    }.get(operation, {})


def _capture_set_operation_required_fields(operation: str) -> list[str]:
    return {
        "receiver-snapshot": ["receiver_transport"],
        "live-pwm": ["target.mac", "target.channel", "target.rx_type", "pwm_values"],
        "live-pwm-sync": ["target.mac", "target.channel", "target.rx_type", "enable", "fallback_pwm"],
        "live-pwm-mirror": ["target.mac", "target.channel", "target.rx_type", "motherboard_pwm"],
        "live-bind": ["target.mac", "master_mac", "rx_type", "channel"],
        "live-unbind": ["target.mac", "channel"],
        "live-rgb": ["target.mac", "target.channel", "target.rx_type", "color", "effect_index", "led_count"],
        "live-rainbow": ["target.mac", "target.channel", "target.rx_type", "frame_count", "interval_ms", "effect_index", "led_count"],
    }.get(operation, [])


def _capture_set_operation_transport_payload(target: dict[str, Any] | None) -> dict[str, Any]:
    if not target:
        return {}
    return {
        key: target[key]
        for key in (
            "vid_pid",
            "role",
            "write_endpoint",
            "read_endpoint",
            "confidence",
            "linux_hint",
            "target_macs",
            "channels",
            "rx_types",
            "outer_rx_types",
            "payload_rx_types",
            "payload_channels",
            "master_macs",
            "runtime_contexts",
            "pwm_values",
            "effect_indexes",
            "led_counts",
            "frame_counts",
            "interval_ms_values",
            "rgb_sequence_frame_counts",
            "rgb_static_colors",
            "rgb_rainbow_generated",
            "scenario_ids",
            "scenario_paths",
        )
        if key in target
    }


def _capture_set_live_target_experiment_commands(
    targets: list[dict[str, Any]],
    *,
    operation_filter: str = "",
) -> list[str]:
    commands: list[str] = []
    for target in targets:
        macs = target.get("target_macs")
        if not isinstance(macs, list):
            continue
        operation = str(target.get("operation") or "")
        if operation_filter and operation != operation_filter:
            continue
        for mac in macs:
            if not isinstance(mac, str) or not mac:
                continue
            command = _capture_set_live_target_experiment_command(operation, mac)
            if command:
                commands.append(command)
    return _unique_preserve_order(commands)


def _capture_set_live_target_experiment_command(operation: str, mac: str) -> str:
    common = ("--mac", mac)
    if operation == "live-pwm":
        return _tool_command(
            "safe-pwm-experiment",
            *common,
            "--pwm",
            "120",
            "--output-dir",
            ".cache/lianli/pwm-experiment",
            "--confirm",
            "WRITE-LIANLI",
        )
    if operation == "live-pwm-sync":
        return _tool_command(
            "safe-sync-experiment",
            *common,
            "--output-dir",
            ".cache/lianli/sync-experiment",
            "--confirm",
            "WRITE-LIANLI",
        )
    if operation == "live-pwm-mirror":
        return _tool_command(
            "safe-pwm-mirror-experiment",
            *common,
            "--output-dir",
            ".cache/lianli/pwm-mirror-experiment",
            "--confirm",
            "WRITE-LIANLI",
        )
    if operation == "live-bind":
        return _tool_command(
            "safe-bind-experiment",
            *common,
            "--rx-type",
            "<rx-type>",
            "--output-dir",
            ".cache/lianli/bind-experiment",
            "--confirm",
            "WRITE-LIANLI",
        )
    if operation == "live-unbind":
        return _tool_command(
            "safe-unbind-experiment",
            *common,
            "--output-dir",
            ".cache/lianli/unbind-experiment",
            "--confirm",
            "WRITE-LIANLI",
        )
    if operation == "live-rgb":
        return _tool_command(
            "safe-rgb-experiment",
            *common,
            "--color",
            "255,0,0",
            "--output-dir",
            ".cache/lianli/rgb-experiment",
            "--confirm",
            "WRITE-LIANLI",
        )
    if operation == "live-rainbow":
        return _tool_command(
            "safe-rainbow-experiment",
            *common,
            "--output-dir",
            ".cache/lianli/rainbow-experiment",
            "--confirm",
            "WRITE-LIANLI",
        )
    return ""


def _capture_set_linux_validation_notes(
    high_confidence_targets: list[dict[str, Any]],
    missing_scenarios: list[dict[str, Any]],
    hardware_validation: dict[str, Any] | None = None,
) -> list[str]:
    notes: list[str] = []
    hardware_status = str((hardware_validation or {}).get("status") or "not-provided")
    if high_confidence_targets:
        operations = sorted({str(target.get("operation") or "") for target in high_confidence_targets if target.get("operation")})
        notes.append(
            "High-confidence Linux write evidence exists for: " + ", ".join(operations)
        )
        notes.append("Run usb-capture-readiness and validate-readonly before any guarded write command.")
    else:
        notes.append("No high-confidence 0416:8040 sender live-write target has been proven by the capture set yet.")
    if hardware_status == "readonly-and-write-observed":
        notes.append("Linux readonly validation and at least one guarded write experiment are already represented in the attached experiment logs.")
    elif hardware_status == "readonly-and-write-gate-ready":
        notes.append("Attached experiment logs show readonly validation plus a passing write-gate; run exactly one guarded write experiment against a confirmed MAC.")
    elif hardware_status == "readonly-observed":
        notes.append("Attached experiment logs prove readonly Linux access; guarded write validation is still pending.")
    elif hardware_status == "write-observed":
        notes.append("Attached experiment logs include a guarded write observation; run validate-readonly to close the hardware checklist.")
    elif hardware_status in {"errors", "summary-error", "missing-summary"}:
        notes.append("Attached experiment logs contain errors or could not be summarized; inspect experiment_summary before trusting live control.")
    if missing_scenarios:
        notes.append(f"{len(missing_scenarios)} planned Windows USBPcap scenario(s) are still missing.")
    return notes


def _capture_set_scenario_commands(path: Path, triage: dict[str, Any]) -> list[str]:
    commands = [_tool_command("capture-triage-report", str(path))]
    commands.extend(str(command) for command in triage.get("recommended_commands", []) if isinstance(command, str))
    return _unique_preserve_order(commands)


def _capture_set_overall_status(
    *,
    scenario_count: int,
    found_count: int,
    evidence_found_count: int,
    partial_count: int,
    error_count: int,
) -> str:
    if found_count == 0:
        return "no-captures-found"
    if error_count:
        return "analysis-errors"
    if evidence_found_count == scenario_count:
        return "complete-capture-set"
    if evidence_found_count or partial_count:
        return "partial-capture-set"
    return "no-expected-evidence"


def _capture_set_recommended_commands(root: Path, base: str, scenario_reports: list[dict[str, Any]]) -> list[str]:
    commands = [
        _tool_command("windows-capture-plan", "--capture-base", base),
        _tool_command("summarize-captures", str(root)),
    ]
    for report in scenario_reports:
        if report.get("status") == "missing-capture":
            capture_file = str(report.get("capture_file") or "")
            if capture_file:
                commands.append(f"capture missing scenario: {capture_file}")
            continue
        commands.extend(str(command) for command in report.get("recommended_commands", []) if isinstance(command, str))
    return _unique_preserve_order(commands)


def _capture_gap_status(
    capture_report: dict[str, Any],
    scenario_gaps: list[dict[str, Any]],
    operation_gaps: list[dict[str, Any]],
) -> str:
    if int(capture_report.get("error_count") or 0):
        return "analysis-errors"
    if int(capture_report.get("found_capture_count") or 0) == 0:
        return "needs-all-windows-captures"
    if any(item.get("id") == "baseline" for item in scenario_gaps):
        return "needs-baseline-capture"
    if scenario_gaps:
        return "needs-windows-capture"
    if operation_gaps:
        return "windows-captures-complete-needs-linux-validation"
    return "capture-matrix-complete"


def _capture_gap_scenario_items(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        status = str(scenario.get("status") or "")
        if status == "evidence-found":
            continue
        scenario_id = str(scenario.get("id") or "")
        meta = _capture_gap_scenario_meta(scenario_id)
        gaps.append(
            {
                "id": scenario_id,
                "status": status,
                "priority": int(meta["priority"]),
                "phase": str(meta["phase"]),
                "risk": str(meta["risk"]),
                "capture_file": str(scenario.get("capture_file") or ""),
                "path": str(scenario.get("path") or ""),
                "goal": str(scenario.get("goal") or ""),
                "missing_evidence": _ordered_strings_from_list(scenario.get("missing_evidence")),
                "matched_evidence": _ordered_strings_from_list(scenario.get("matched_evidence")),
                "expected_evidence": _ordered_strings_from_list(scenario.get("expected_evidence")),
                "windows_actions": _ordered_strings_from_list(scenario.get("windows_actions")),
                "planned_linux_commands": _ordered_strings_from_list(scenario.get("planned_linux_commands")),
                "recommended_commands": _ordered_strings_from_list(scenario.get("recommended_commands")),
            }
        )
    return sorted(gaps, key=lambda item: (int(item["priority"]), str(item["capture_file"])))


def _capture_gap_scenario_meta(scenario_id: str) -> dict[str, Any]:
    return {
        "baseline": {
            "priority": 0,
            "phase": "identity-and-readonly",
            "risk": "readonly",
        },
        "direct-fan-speed": {
            "priority": 10,
            "phase": "core-fan-speed-write",
            "risk": "guarded fan-speed write",
        },
        "motherboard-pwm-sync": {
            "priority": 20,
            "phase": "motherboard-pwm-sync",
            "risk": "guarded fan-speed write",
        },
        "lighting-static-and-off": {
            "priority": 30,
            "phase": "static-lighting-write",
            "risk": "visual lighting write",
        },
        "lighting-generated-rainbow": {
            "priority": 40,
            "phase": "animated-lighting-write",
            "risk": "visual lighting write",
        },
        "sort-quick-sync": {
            "priority": 50,
            "phase": "utility-settings-and-sort",
            "risk": "settings rewrite",
        },
        "rf-rebind": {
            "priority": 90,
            "phase": "pairing-state-change",
            "risk": "bind/unbind changes receiver pairing state",
        },
    }.get(
        scenario_id,
        {
            "priority": 100,
            "phase": "unknown",
            "risk": "unknown",
        },
    )


def _capture_gap_operation_items(matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for operation in matrix:
        if not isinstance(operation, dict):
            continue
        overall = str(operation.get("overall_status") or "")
        if overall == "linux-validated":
            continue
        name = str(operation.get("operation") or "")
        gaps.append(
            {
                "operation": name,
                "label": str(operation.get("label") or ""),
                "risk": str(operation.get("risk") or ""),
                "priority": _capture_gap_operation_priority(name),
                "overall_status": overall,
                "windows_evidence_status": str(operation.get("windows_evidence_status") or ""),
                "linux_target_status": str(operation.get("linux_target_status") or ""),
                "experiment_status": str(operation.get("experiment_status") or ""),
                "required_runtime_fields": _ordered_strings_from_list(operation.get("required_runtime_fields")),
                "missing_scenarios": [
                    _capture_gap_operation_scenario_payload(item)
                    for item in operation.get("scenario_statuses", [])
                    if isinstance(item, dict) and str(item.get("status") or "") != "evidence-found"
                ],
                "recommended_commands": _ordered_strings_from_list(operation.get("recommended_commands")),
            }
        )
    return sorted(gaps, key=lambda item: (int(item["priority"]), str(item["operation"])))


def _capture_gap_operation_scenario_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "status": str(item.get("status") or ""),
        "capture_file": str(item.get("capture_file") or ""),
        "goal": str(item.get("goal") or ""),
        "missing_evidence": _ordered_strings_from_list(item.get("missing_evidence")),
    }


def _capture_gap_operation_priority(operation: str) -> int:
    return {
        "receiver-snapshot": 0,
        "live-pwm": 10,
        "live-pwm-sync": 20,
        "live-pwm-mirror": 25,
        "live-rgb": 30,
        "live-rainbow": 40,
        "live-bind": 90,
        "live-unbind": 90,
    }.get(operation, 100)


def _capture_gap_proof_gates(
    capture_report: dict[str, Any],
    scenario_gaps: list[dict[str, Any]],
    operation_gaps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    gap_ids = {str(item.get("id") or "") for item in scenario_gaps}
    operation_status = {
        str(item.get("operation") or ""): str(item.get("overall_status") or "")
        for item in operation_gaps
    }
    found_capture_count = int(capture_report.get("found_capture_count") or 0)
    return [
        {
            "name": "baseline-before-writes",
            "status": "ok" if "baseline" not in gap_ids and found_capture_count else "blocked",
            "reason": "Receiver identity, master MAC, channel, rx_type, and idle snapshots must be captured before trusting write traffic.",
        },
        {
            "name": "pwm-before-lighting",
            "status": "ok" if operation_status.get("live-pwm", "linux-validated") == "linux-validated" else "blocked",
            "reason": "Direct PWM is the lowest-risk write path and should be matched before RGB/rainbow writes are treated as actionable.",
        },
        {
            "name": "lighting-before-pairing",
            "status": (
                "ok"
                if operation_status.get("live-rgb", "linux-validated") == "linux-validated"
                and operation_status.get("live-rainbow", "linux-validated") == "linux-validated"
                else "blocked"
            ),
            "reason": "Bind/unbind should remain deferred until PWM plus both static and generated lighting writes have evidence.",
        },
        {
            "name": "pairing-last",
            "status": "blocked" if {"rf-rebind", "baseline"} & gap_ids else "manual-review",
            "reason": "RF bind/unbind changes receiver pairing state; execute it only after lower-risk capture scenarios are complete.",
        },
    ]


def _capture_gap_recommended_commands(
    root: Path,
    base: str,
    next_capture: dict[str, Any],
    scenario_gaps: list[dict[str, Any]],
    operation_gaps: list[dict[str, Any]],
) -> list[str]:
    commands = [
        _tool_command("windows-capture-plan", "--capture-base", base),
        _tool_command("capture-set-report", str(root), "--capture-base", base),
    ]
    if next_capture:
        capture_file = str(next_capture.get("capture_file") or "")
        if capture_file:
            commands.append(f"capture next scenario: {capture_file}")
        commands.extend(_ordered_strings_from_list(next_capture.get("planned_linux_commands")))
    for gap in scenario_gaps[:2]:
        commands.extend(_ordered_strings_from_list(gap.get("recommended_commands")))
    for gap in operation_gaps[:2]:
        commands.extend(_ordered_strings_from_list(gap.get("recommended_commands")))
    return _unique_preserve_order(commands)


def _safe_file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _counter_from_mapping(value: Any) -> Counter[str]:
    result: Counter[str] = Counter()
    if isinstance(value, dict):
        for key, count in value.items():
            try:
                result[str(key)] += int(count)
            except (TypeError, ValueError):
                continue
    return result


def _strings_from_list(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        item.lower()
        for item in value
        if isinstance(item, str) and item
    }


def _ordered_strings_from_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return _unique_preserve_order(str(item) for item in value if isinstance(item, str) and item)


def _ints_from_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: set[int] = set()
    for item in value:
        if isinstance(item, int):
            result.add(item)
            continue
        if isinstance(item, str) and item.strip():
            try:
                result.add(int(item, 0))
            except ValueError:
                continue
    return sorted(result)


def _ordered_ints_from_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        if isinstance(item, int):
            result.append(item)
            continue
        if isinstance(item, str) and item.strip():
            try:
                result.append(int(item, 0))
            except ValueError:
                continue
    return result


def _is_pcap_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            magic = handle.read(4)
    except OSError:
        return False
    return magic == PCAPNG_MAGIC or magic in PCAP_MAGIC_VALUES


def _capture_transport_items(path: Path) -> list[dict[str, Any]]:
    if _is_pcap_file(path):
        return _transport_items_from_pcap_file(path)
    text = _read_capture_text(path)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _transport_items_from_packets(_packets_from_text(text), field="text")
    items = _transport_items_from_json(payload)
    if items:
        return items
    return _transport_items_from_packets(_packets_from_text(text), field="text")


def _transport_items_from_packets(
    packets: Iterable[bytes],
    *,
    field: str,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for packet in packets:
        item = {"field": field, "packet": packet}
        if metadata:
            item.update({key: value for key, value in metadata.items() if value not in ("", None)})
        result.append(item)
    return result


def _transport_items_from_pcap_file(path: Path) -> list[dict[str, Any]]:
    tshark = shutil.which("tshark")
    if not tshark:
        raise LianLiWirelessError(_pcap_tshark_missing_message(path))
    payload_fields = ["usb.capdata", "usbhid.data", "data.data"]
    metadata_fields = [
        key
        for key in CAPTURE_TRANSPORT_META_KEYS
        if key != "frame.number"
    ]
    fields = ["frame.number", *payload_fields, *metadata_fields]
    command = [
        tshark,
        "-r",
        str(path),
        "-T",
        "fields",
        "-E",
        "separator=\t",
        "-E",
        "occurrence=a",
    ]
    for field in fields:
        command.extend(["-e", field])
    result = _run_tshark_command(command, path)
    items: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        columns = line.split("\t")
        columns.extend([""] * (len(fields) - len(columns)))
        metadata = {"frame_number": _transport_metadata_cell(columns[0])}
        metadata_offset = 1 + len(payload_fields)
        for field, value in zip(metadata_fields, columns[metadata_offset:]):
            output_key = CAPTURE_TRANSPORT_META_KEYS[field]
            metadata[output_key] = _transport_metadata_cell(value)
        metadata = {key: value for key, value in metadata.items() if value}
        for field, value in zip(payload_fields, columns[1:metadata_offset]):
            for part in value.split(","):
                packet = _packet_from_hex(part.strip())
                if packet:
                    items.append({"field": field, "packet": packet, **metadata})
    return items


def _transport_metadata_cell(value: str) -> str:
    for part in str(value).split(","):
        text = part.strip()
        if text:
            return text
    return ""


def _packets_from_pcap_file(path: Path) -> list[bytes]:
    tshark = shutil.which("tshark")
    if not tshark:
        raise LianLiWirelessError(_pcap_tshark_missing_message(path))
    command = [
        tshark,
        "-r",
        str(path),
        "-T",
        "fields",
        "-E",
        "separator=\t",
        "-E",
        "occurrence=a",
        "-e",
        "usb.capdata",
        "-e",
        "usbhid.data",
        "-e",
        "data.data",
    ]
    result = _run_tshark_command(command, path)
    packets = _packets_from_tshark_fields(result.stdout)
    if not packets:
        raise LianLiWirelessError(
            "tshark extracted no 64/65-byte or receiver-snapshot USB payloads; "
            "check that the capture includes L-Wireless USB transfers and not only enumeration traffic"
        )
    return packets


def _run_tshark_command(command: list[str], path: Path) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as error:
        raise LianLiWirelessError(f"tshark timed out while reading capture {path}") from error
    except OSError as error:
        raise LianLiWirelessError(f"unable to run tshark for capture {path}: {error}") from error
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise LianLiWirelessError(
            f"tshark failed to read capture {path}: {message or f'exit {result.returncode}'}"
        )
    return result


def _pcap_tshark_missing_message(path: Path) -> str:
    return (
        f"{path} is a raw pcap/pcapng capture. Install tshark or export USB payload fields first, for example:\n"
        f"tshark -r {shlex.quote(str(path))} -T fields -E separator=\\t "
        "-e usb.capdata -e usbhid.data -e data.data > l-connect-usb-hex.txt"
    )


def _local_windows_capture_tools() -> dict[str, dict[str, Any]]:
    aliases = {
        "wine": ("wine", "wine64"),
        "wineboot": ("wineboot",),
        "xvfb": ("xvfb-run",),
        "qemu": ("qemu-system-x86_64",),
        "virt-manager": ("virt-manager", "virt-install"),
        "virtualbox": ("VBoxManage",),
        "docker": ("docker",),
        "podman": ("podman",),
        "tshark": ("tshark",),
        "usbipd": ("usbipd",),
    }
    return {
        name: {
            "available": found is not None,
            "path": found or "",
            "candidates": list(candidates),
        }
        for name, candidates in aliases.items()
        for found in (_first_available_tool(candidates),)
    }


def _first_available_tool(candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _capture_installer_payload(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "path": "",
            "exists": False,
            "note": "Pass --installer to include size and SHA256 in the capture plan.",
        }
    payload: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
    }
    if not path.exists():
        return payload
    try:
        stat = path.stat()
    except OSError as error:
        payload["error"] = str(error)
        return payload
    payload["size"] = stat.st_size
    payload["sha256"] = _sha256_file(path)
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _usb_target_purpose(vendor_id: int, product_id: int) -> str:
    if (vendor_id, product_id) == (0x0416, 0x8040):
        return "RF command write/read path; highest-priority USBPcap target"
    if (vendor_id, product_id) == (0x0416, 0x8041):
        return "RF receiver identity/state path; capture together with sender when possible"
    if (vendor_id, product_id) == (0x1CBE, 0x0006):
        return "TL LCD wireless receiver candidate"
    return "Related LIAN LI HID/USB path; capture if L-Connect touches it during the scenario"


def _usb_capture_priority(vendor_id: int, product_id: int) -> str:
    if (vendor_id, product_id) == (RF_SENDER_VID, RF_SENDER_PID):
        return "highest"
    if (vendor_id, product_id) == (RF_RECEIVER_VID, RF_RECEIVER_PID):
        return "high"
    if (vendor_id, product_id) in ((0x0416, 0x7372), (0x04FC, 0x7393), (0x1CBE, 0x0006)):
        return "medium"
    return "low"


def _usb_device_payload(device: Any) -> dict[str, Any]:
    busnum = str(getattr(device, "busnum", "") or "")
    devnum = str(getattr(device, "devnum", "") or "")
    payload = {
        "vid_pid": str(getattr(device, "vid_pid", "")),
        "label": str(getattr(device, "label", "")),
        "manufacturer": str(getattr(device, "manufacturer", "")),
        "product": str(getattr(device, "product", "")),
        "serial": str(getattr(device, "serial", "")),
        "sysfs_path": str(getattr(device, "sysfs_path", "")),
        "busnum": busnum,
        "devnum": devnum,
    }
    if busnum and devnum:
        payload["usbmon_hint"] = f"capture usb bus {busnum}; device address {devnum}"
    return payload


def _usb_readiness_status(
    sender_present: bool,
    receiver_present: bool,
    tools: dict[str, dict[str, Any]],
) -> tuple[str, list[str]]:
    blockers: list[str] = []
    if sender_present and receiver_present:
        status = "linux-live-ready"
    elif receiver_present:
        status = "receiver-only"
        blockers.append("0416:8040 RF sender is not visible; write opcode validation is incomplete.")
    elif sender_present:
        status = "sender-only"
        blockers.append("0416:8041 receiver is not visible; read-only device snapshot validation is incomplete.")
    else:
        status = "no-l-wireless-hardware"
        blockers.append("No 0416:8040/0416:8041 L-Wireless USB devices are visible under sysfs.")
    if not tools.get("tshark", {}).get("available"):
        blockers.append("tshark is missing; raw pcap/pcapng decoding will require Wireshark field export.")
    return status, blockers


def _usbmon_payload(sys_root: Path) -> dict[str, Any]:
    path = sys_root / "kernel" / "debug" / "usb" / "usbmon"
    error = ""
    try:
        available = path.exists()
    except OSError as exc:
        available = False
        error = str(exc)
    return {
        "path": str(path),
        "available": available,
        "error": error,
        "note": (
            "Linux usbmon can help inspect Linux-side PyUSB traffic, but official L-Connect proof still requires Windows USBPcap."
        ),
    }


def _usb_linux_live_commands(sender_present: bool, receiver_present: bool) -> list[str]:
    commands = [_tool_command("scan")]
    if receiver_present:
        commands.append(_tool_command("live-list"))
    if sender_present:
        commands.append(_tool_command("live-master", "--channel", "8"))
    if sender_present and receiver_present:
        commands.append(_tool_command("validate-readonly", "--output-dir", ".cache/lianli/validation-live"))
    commands.extend(
        [
            _tool_command("dry-run-master-query", "--channel", "8"),
            _tool_command("dry-run-pwm", "--help"),
        ]
    )
    return commands


def _windows_capture_environment_matrix(tools: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "environment": "vm-usb-passthrough",
            "fit": "best",
            "local_available": tools["qemu"]["available"] or tools["virtualbox"]["available"],
            "what_it_proves": "Official Windows L-Connect traffic through the real Windows USB stack.",
            "limits": "Requires a Windows VM, USBPcap/Wireshark, and direct USB passthrough of the L-Wireless receiver/sender.",
        },
        {
            "environment": "wine",
            "fit": "limited",
            "local_available": tools["wine"]["available"],
            "what_it_proves": "Installer behavior, installed files, and possibly front-end/runtime loading.",
            "limits": "Wine does not reproduce the Windows kernel USBPcap/HID/WinUSB path reliably; do not treat it as RF protocol proof.",
        },
        {
            "environment": "docker",
            "fit": "analyzer-only",
            "local_available": tools["docker"]["available"] or tools["podman"]["available"],
            "what_it_proves": "Can run Linux-side analyzers on exported text/pcapng captures.",
            "limits": "Linux containers cannot run Windows L-Connect GUI plus USBPcap as a real Windows USB environment.",
        },
    ]


def _windows_capture_host_setup_commands(base: str) -> list[dict[str, Any]]:
    return [
        {
            "target": "windows-vm",
            "commands": [
                "Install L-Connect 3 v2.1.17 inside the Windows VM.",
                "Install Wireshark with USBPcap support inside the Windows VM.",
                "Pass through USB devices 0416:8040 and 0416:8041 to the VM; also pass through 0416:7372, 04fc:7393, or 1cbe:0006 if present.",
                "Start a USBPcap capture before opening L-Connect; stop it immediately after each scenario.",
            ],
        },
        {
            "target": "linux-host-after-capture",
            "commands": [
                f"python tools/lianli_wireless_probe.py capture-triage-report {base}-SCENARIO.pcapng",
                f"python tools/lianli_wireless_probe.py analyze-capture {base}-SCENARIO.pcapng",
                f"python tools/lianli_wireless_probe.py capture-replay-plan {base}-SCENARIO.pcapng",
                f"python tools/lianli_wireless_probe.py capture-protocol-report {base}-SCENARIO.pcapng",
                f"python tools/lianli_wireless_probe.py capture-timeline-report {base}-SCENARIO.pcapng",
            ],
        },
        {
            "target": "wine-smoke-test-only",
            "commands": [
                f"export WINEPREFIX=$PWD/.cache/lianli/wine-{base}",
                "wineboot -u",
                'wine start /wait "" "<L-Connect-installer.exe>" /S',
                'find "$WINEPREFIX/drive_c" -iname "*L-Connect*" -o -iname "*.asar"',
            ],
        },
    ]


def _windows_capture_scenarios(base: str) -> list[dict[str, Any]]:
    target = [
        "--mac",
        "<receiver-mac>",
        "--master-mac",
        "<master-mac>",
        "--channel",
        "<channel>",
        "--rx-type",
        "<rx-type>",
        "--device-type",
        "<device-type>",
    ]
    return [
        {
            "id": "baseline",
            "capture_file": f"{base}-00-baseline.pcapng",
            "goal": "Identify active receiver snapshots, master MAC, channel, receiver type, device type, fan count, and idle polling cadence.",
            "windows_actions": [
                "Start USBPcap capture.",
                "Open L-Connect 3 and wait until the L-Wireless devices are listed.",
                "Open the L-Wireless Utility page but do not change settings.",
                "Stop capture after 10-20 seconds of idle polling.",
            ],
            "expected_evidence": ["receiver-list-request", "receiver-snapshot", "master-query-request/response"],
            "linux_commands": _scenario_linux_commands(f"{base}-00-baseline.pcapng"),
        },
        {
            "id": "direct-fan-speed",
            "capture_file": f"{base}-01-direct-fan-speed.pcapng",
            "goal": "Capture direct per-receiver PWM writes for non-uniform fan speeds.",
            "windows_actions": [
                "Start USBPcap capture.",
                "In L-Connect, set the target wireless fan group to manual/fixed speed.",
                "Apply a clearly different value such as 55%, then 75%.",
                "Stop capture immediately after the fans visibly react or the UI reports apply success.",
            ],
            "expected_evidence": ["live-pwm RF frames", "pwm_values not equal to [6,6,6,6]"],
            "linux_commands": [
                *_scenario_linux_commands(f"{base}-01-direct-fan-speed.pcapng"),
                _tool_command(
                    "compare-capture",
                    f"{base}-01-direct-fan-speed.pcapng",
                    "pwm",
                    *target,
                    "--pwm-values",
                    "<captured-or-expected-pwm-tuple>",
                ),
            ],
        },
        {
            "id": "motherboard-pwm-sync",
            "capture_file": f"{base}-02-mb-pwm-sync.pcapng",
            "goal": "Capture the official RF command used when L-Connect enables motherboard PWM sync.",
            "windows_actions": [
                "Start USBPcap capture.",
                "Enable motherboard PWM sync for the target L-Wireless fan group.",
                "Disable it once, then enable it again so both transitions are present.",
                "Stop capture.",
            ],
            "expected_evidence": ["live-pwm-sync frames with PWM tuple [6,6,6,6] or direct fallback PWM frames"],
            "linux_commands": [
                *_scenario_linux_commands(f"{base}-02-mb-pwm-sync.pcapng"),
                _tool_command("compare-capture", f"{base}-02-mb-pwm-sync.pcapng", "pwm-sync", *target),
                _tool_command(
                    "compare-capture",
                    f"{base}-02-mb-pwm-sync.pcapng",
                    "pwm-sync",
                    *target,
                    "--disable",
                    "--fallback-pwm",
                    "<fallback-pwm>",
                ),
            ],
        },
        {
            "id": "rf-rebind",
            "capture_file": f"{base}-03-rf-rebind.pcapng",
            "goal": "Target the v2.1.17 changelog fix: RF unbind/rebind affecting fan speed display/control.",
            "windows_actions": [
                "Start USBPcap capture with the receiver already bound.",
                "Unbind the target RF receiver in L-Connect.",
                "Wait for the device list to refresh.",
                "Bind the receiver back to the same master/controller.",
                "Apply one manual fan speed after rebinding.",
                "Stop capture.",
            ],
            "expected_evidence": ["live-unbind", "receiver snapshot with changed master MAC/bind state", "live-bind", "post-bind live-pwm"],
            "linux_commands": [
                *_scenario_linux_commands(f"{base}-03-rf-rebind.pcapng"),
                _tool_command("compare-capture", f"{base}-03-rf-rebind.pcapng", "unbind", *target, "--current-pwm", "<pre-unbind-pwm-tuple>"),
                _tool_command("compare-capture", f"{base}-03-rf-rebind.pcapng", "bind", *target, "--current-pwm", "<post-bind-pwm-tuple>"),
            ],
        },
        {
            "id": "sort-quick-sync",
            "capture_file": f"{base}-04-sort-quick-sync.pcapng",
            "goal": "Target the v2.1.17 changelog fix: L-Wireless Utility fan settings switch to quick sync after sort settings.",
            "windows_actions": [
                "Start USBPcap capture.",
                "Configure L-Wireless Utility fan settings for SL/TL wireless fans.",
                "Change sort/order settings.",
                "Observe whether quick sync is toggled or fan settings are rewritten.",
                "Stop capture.",
            ],
            "expected_evidence": ["live-pwm-sync if quick sync is toggled", "live-pwm or live-pwm-mirror if fan settings are rewritten", "receiver snapshots before and after sorting"],
            "linux_commands": [
                *_scenario_linux_commands(f"{base}-04-sort-quick-sync.pcapng"),
                _tool_command("compare-capture", f"{base}-04-sort-quick-sync.pcapng", "pwm-sync", *target),
                _tool_command("compare-capture", f"{base}-04-sort-quick-sync.pcapng", "pwm-mirror", *target, "--motherboard-pwm", "<decoded-motherboard-pwm>"),
            ],
        },
        {
            "id": "lighting-static-and-off",
            "capture_file": f"{base}-05-lighting-static-off.pcapng",
            "goal": "Capture static RGB and all-off lighting packets for L-Wireless fans.",
            "windows_actions": [
                "Start USBPcap capture.",
                "Set one obvious static color such as red.",
                "Set lighting off / black.",
                "Stop capture.",
            ],
            "expected_evidence": ["live-rgb frames with decoded static_color", "TinyUZ decoded literal/backref payloads"],
            "linux_commands": [
                *_scenario_linux_commands(f"{base}-05-lighting-static-off.pcapng"),
                _tool_command("compare-capture", f"{base}-05-lighting-static-off.pcapng", "rgb", *target, "--color", "255,0,0", "--led-count", "<led-count>"),
                _tool_command("compare-capture", f"{base}-05-lighting-static-off.pcapng", "rgb", *target, "--color", "0,0,0", "--led-count", "<led-count>"),
            ],
        },
        {
            "id": "lighting-generated-rainbow",
            "capture_file": f"{base}-06-lighting-generated-rainbow.pcapng",
            "goal": "Capture generated rainbow / animated RGB lighting packets for L-Wireless fans.",
            "windows_actions": [
                "Start USBPcap capture.",
                "Select a generated rainbow / spectrum animation for the same target wireless fan group.",
                "Set a known speed if L-Connect exposes one, then apply the effect and wait for the visual update.",
                "Stop capture after one clean apply cycle; avoid mixing static color changes in this file.",
            ],
            "expected_evidence": ["live-rgb frames whose decoded RGB payload matches the generated rainbow sequence", "decoded LED count, frame count, interval, and effect index"],
            "linux_commands": [
                *_scenario_linux_commands(f"{base}-06-lighting-generated-rainbow.pcapng"),
                _tool_command(
                    "compare-capture",
                    f"{base}-06-lighting-generated-rainbow.pcapng",
                    "rainbow",
                    *target,
                    "--frame-count",
                    "<frame-count>",
                    "--interval-ms",
                    "<interval-ms>",
                    "--led-count",
                    "<led-count>",
                    "--effect-index",
                    "<effect-index>",
                ),
            ],
        },
    ]


def _scenario_linux_commands(capture_file: str) -> list[str]:
    return [
        _tool_command("capture-triage-report", capture_file),
        _tool_command("analyze-capture", capture_file),
        _tool_command("capture-replay-plan", capture_file),
        _tool_command("capture-protocol-report", capture_file),
        _tool_command("capture-timeline-report", capture_file),
    ]


def _tool_command(*args: str) -> str:
    return " ".join(["python", "tools/lianli_wireless_probe.py", *(shlex.quote(str(arg)) for arg in args)])


def _attach_transport_classification(record: dict[str, Any], classified: dict[str, Any]) -> None:
    kind = str(classified.get("kind", "unknown"))
    for key in (
        "sequence",
        "channel",
        "rx_type",
        "mac",
        "master_mac",
        "motherboard_pwm",
        "page_count",
        "reported_device_count",
    ):
        value = classified.get(key)
        if value not in ("", None):
            record[key] = value
    if kind == "receiver-snapshot":
        devices = classified.get("devices")
        if isinstance(devices, list):
            record["device_count"] = len(devices)
            record["receiver_macs"] = [
                str(device.get("mac"))
                for device in devices
                if isinstance(device, dict) and device.get("mac")
            ]
    if kind == "rf-chunk":
        data_hex = str(classified.get("data_hex") or "")
        record["data_prefix"] = data_hex[:24]


def _capture_transport_recommended_commands(source: str, protocol_count: int) -> list[str]:
    if not source:
        return []
    commands = [_tool_command("capture-transport-report", source)]
    if protocol_count:
        commands.extend(
            [
                _tool_command("capture-triage-report", source),
                _tool_command("analyze-capture", source),
                _tool_command("capture-protocol-report", source),
                _tool_command("capture-timeline-report", source),
                _tool_command("capture-replay-plan", source),
            ]
        )
    return commands


def _capture_transport_usb_summary(
    records: Iterable[dict[str, Any]],
    protocol_items: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    record_list = list(records)
    protocol_ids = {id(item) for item in protocol_items}
    device_counts: Counter[str] = Counter()
    protocol_device_counts: Counter[str] = Counter()
    endpoint_counts: Counter[str] = Counter()
    protocol_endpoint_counts: Counter[str] = Counter()
    for record in record_list:
        device_key = _transport_usb_device_key(record)
        if device_key:
            device_counts[device_key] += 1
            if id(record) in protocol_ids:
                protocol_device_counts[device_key] += 1
        endpoint_key = _transport_usb_endpoint_key(record)
        if endpoint_key:
            endpoint_counts[endpoint_key] += 1
            if id(record) in protocol_ids:
                protocol_endpoint_counts[endpoint_key] += 1
    known_devices = [
        _transport_known_usb_device_payload(device_key, count, protocol_device_counts.get(device_key, 0))
        for device_key, count in sorted(device_counts.items())
        if _transport_known_usb_label(device_key)
    ]
    sender_key = f"{RF_SENDER_VID:04x}:{RF_SENDER_PID:04x}"
    receiver_key = f"{RF_RECEIVER_VID:04x}:{RF_RECEIVER_PID:04x}"
    return {
        "usb_device_counts": dict(sorted(device_counts.items())),
        "usb_protocol_device_counts": dict(sorted(protocol_device_counts.items())),
        "usb_endpoint_counts": dict(sorted(endpoint_counts.items())),
        "usb_protocol_endpoint_counts": dict(sorted(protocol_endpoint_counts.items())),
        "known_usb_devices": known_devices,
        "lianli_usb_targets": {
            "sender_vid_pid": sender_key,
            "receiver_vid_pid": receiver_key,
            "sender_seen": device_counts.get(sender_key, 0) > 0,
            "receiver_seen": device_counts.get(receiver_key, 0) > 0,
            "sender_protocol_candidate_count": protocol_device_counts.get(sender_key, 0),
            "receiver_protocol_candidate_count": protocol_device_counts.get(receiver_key, 0),
        },
    }


def _capture_transport_usb_notes(usb_summary: dict[str, Any], records: list[dict[str, Any]]) -> list[str]:
    if not any("usb_vendor_id" in record or "usb_product_id" in record for record in records):
        return []
    targets = usb_summary.get("lianli_usb_targets", {})
    if not isinstance(targets, dict):
        return []
    sender_seen = bool(targets.get("sender_seen"))
    receiver_seen = bool(targets.get("receiver_seen"))
    if sender_seen and receiver_seen:
        return []
    if sender_seen:
        return ["USB metadata shows the 0416:8040 RF sender but not the 0416:8041 receiver; include both devices for full official evidence."]
    if receiver_seen:
        return ["USB metadata shows the 0416:8041 receiver but not the 0416:8040 RF sender; RF write traffic usually requires the sender capture."]
    return ["USB metadata is present, but no known LIAN LI L-Wireless VID:PID (0416:8040/0416:8041) was seen."]


def _transport_known_usb_device_payload(device_key: str, count: int, protocol_count: int) -> dict[str, Any]:
    vid, pid = _transport_usb_vid_pid(device_key)
    role = ""
    if (vid, pid) == (RF_SENDER_VID, RF_SENDER_PID):
        role = "sender"
    elif (vid, pid) == (RF_RECEIVER_VID, RF_RECEIVER_PID):
        role = "receiver"
    return {
        "vid_pid": device_key,
        "role": role,
        "label": KNOWN_USB_DEVICES.get((vid, pid), ""),
        "packet_candidate_count": count,
        "protocol_candidate_count": protocol_count,
    }


def _transport_known_usb_label(device_key: str) -> str:
    vid, pid = _transport_usb_vid_pid(device_key)
    return KNOWN_USB_DEVICES.get((vid, pid), "")


def _transport_usb_vid_pid(device_key: str) -> tuple[int, int]:
    try:
        vid_text, pid_text = device_key.split(":", 1)
        return int(vid_text, 16), int(pid_text, 16)
    except ValueError:
        return 0, 0


def _transport_usb_device_key(record: dict[str, Any]) -> str:
    vid = _normalize_usb_id(record.get("usb_vendor_id"))
    pid = _normalize_usb_id(record.get("usb_product_id"))
    return f"{vid}:{pid}" if vid and pid else ""


def _transport_usb_endpoint_key(record: dict[str, Any]) -> str:
    parts = [
        str(record.get("usb_bus") or "?"),
        str(record.get("usb_device_address") or "?"),
        str(record.get("usb_endpoint_address") or record.get("usb_endpoint_number") or "?"),
        str(record.get("usb_endpoint_direction") or "?"),
        str(record.get("usb_transfer_type") or "?"),
    ]
    if all(part == "?" for part in parts):
        return ""
    return "/".join(parts)


def _normalize_usb_id(value: Any) -> str:
    if isinstance(value, int):
        return f"{max(0, min(0xFFFF, value)):04x}"
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    if not text:
        return ""
    text = text.split(",", 1)[0].strip()
    try:
        if text.startswith("0x"):
            return f"{int(text, 16) & 0xFFFF:04x}"
        compact = re.sub(r"[^0-9a-f]", "", text)
        if len(compact) == 4:
            return compact
        if compact and any(char in "abcdef" for char in compact):
            return f"{int(compact, 16) & 0xFFFF:04x}"
        if text.isdigit():
            return f"{int(text, 10) & 0xFFFF:04x}"
    except ValueError:
        return ""
    return ""


def _transport_items_from_json(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items: list[dict[str, Any]] = []
        metadata = _transport_metadata_from_json(payload)
        for key, value in payload.items():
            if key in CAPTURE_JSON_HEX_KEYS:
                items.extend(_transport_items_from_packets(_packets_from_json_value(value), field=key, metadata=metadata))
            else:
                items.extend(_transport_items_from_json(value))
        return items
    if isinstance(payload, list):
        items: list[dict[str, Any]] = []
        if all(isinstance(part, int) for part in payload):
            return _transport_items_from_packets(_packets_from_json_value(payload), field="json-list")
        for value in payload:
            items.extend(_transport_items_from_json(value))
        return items
    if isinstance(payload, str):
        packet = _packet_from_hex(payload)
        return [{"field": "json-string", "packet": packet}] if packet else []
    return []


def _transport_metadata_from_json(payload: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for tshark_key, output_key in CAPTURE_TRANSPORT_META_KEYS.items():
        value = _first_json_scalar_for_key(payload, tshark_key)
        if value not in ("", None):
            metadata[output_key] = str(value)
    return metadata


def _first_json_scalar_for_key(payload: Any, key: str) -> str:
    if isinstance(payload, dict):
        if key in payload:
            value = _json_scalar_text(payload[key])
            if value:
                return value
        for value in payload.values():
            found = _first_json_scalar_for_key(value, key)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _first_json_scalar_for_key(value, key)
            if found:
                return found
    return ""


def _json_scalar_text(value: Any) -> str:
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, list):
        for item in value:
            text = _json_scalar_text(item)
            if text:
                return text
    return ""


def _packets_from_tshark_fields(text: str) -> list[bytes]:
    packets: list[bytes] = []
    for line in text.splitlines():
        for field in line.split("\t"):
            for value in field.split(","):
                packet = _packet_from_hex(value.strip())
                if packet:
                    packets.append(packet)
    return packets


def _packets_from_json(payload: Any) -> list[bytes]:
    if isinstance(payload, dict):
        packets: list[bytes] = []
        for key, value in payload.items():
            if key in CAPTURE_JSON_HEX_KEYS:
                packets.extend(_packets_from_json_value(value))
        if packets:
            return packets
        for value in payload.values():
            packets.extend(_packets_from_json(value))
        return packets
    if isinstance(payload, list):
        packets: list[bytes] = []
        for item in payload:
            packets.extend(_packets_from_json_value(item))
        return packets
    if isinstance(payload, str):
        packet = _packet_from_hex(payload)
        return [packet] if packet else []
    return []


def _packets_from_json_value(value: Any) -> list[bytes]:
    if isinstance(value, str):
        packet = _packet_from_hex(value)
        return [packet] if packet else []
    if isinstance(value, list) and all(isinstance(part, int) for part in value):
        packet = _packet_from_ints(value)
        return [packet] if packet else []
    return _packets_from_json(value)


def _packet_from_ints(value: Iterable[int]) -> bytes:
    raw = bytes(max(0, min(255, int(part))) for part in value)
    if len(raw) in (64, 65) or len(raw) >= 434:
        return raw
    return b""


def _packets_from_text(text: str) -> list[bytes]:
    packets: list[bytes] = []
    hexdump_block = bytearray()

    def flush_hexdump_block() -> None:
        if not hexdump_block:
            return
        packets.extend(_packets_from_block(bytes(hexdump_block)))
        hexdump_block.clear()

    for line in text.splitlines():
        cleaned = line.split("#", 1)[0].strip()
        if not cleaned:
            flush_hexdump_block()
            continue
        hexdump_line = _hexdump_line_bytes(cleaned)
        if hexdump_line is not None:
            offset, data = hexdump_line
            if offset == 0 and hexdump_block:
                flush_hexdump_block()
            hexdump_block.extend(data)
            continue
        flush_hexdump_block()
        packet = _packet_from_hex(cleaned)
        if packet:
            packets.append(packet)
    flush_hexdump_block()
    return packets


def _packets_from_block(raw: bytes) -> list[bytes]:
    packet = _packet_from_bytes(raw)
    if packet:
        return [packet]
    packets: list[bytes] = []
    for size in (65, 64):
        if len(raw) >= size and len(raw) % size == 0:
            for offset in range(0, len(raw), size):
                packet = _packet_from_bytes(raw[offset : offset + size])
                if packet:
                    packets.append(packet)
            if packets:
                return packets
    return packets


def _packet_from_hex(value: str) -> bytes:
    compact = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(compact) < 2 or len(compact) % 2:
        return b""
    try:
        raw = bytes.fromhex(compact)
    except ValueError:
        return b""
    return _packet_from_bytes(raw)


def _packet_from_bytes(raw: bytes) -> bytes:
    if len(raw) in (64, 65) or len(raw) >= 434:
        return raw
    return b""


def _hexdump_line_bytes(line: str) -> tuple[int, bytes] | None:
    match = re.match(r"^\s*([0-9a-fA-F]{4,8})\s+(.+)$", line)
    if match is None:
        return None
    byte_values: list[int] = []
    for token in match.group(2).split():
        if re.fullmatch(r"[0-9a-fA-F]{2}", token):
            byte_values.append(int(token, 16))
            continue
        if byte_values:
            break
        return None
    if not byte_values:
        return None
    return int(match.group(1), 16), bytes(byte_values)


def _device_payload(device: Any) -> dict[str, Any]:
    payload = {
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
    }
    raw = getattr(device, "raw", b"")
    if isinstance(raw, (bytes, bytearray)) and raw:
        payload["raw_hex"] = bytes(raw).hex()
    return payload


def _match_decoded_frames(
    expected_frames: list[dict[str, Any]],
    observed_frames: list[dict[str, Any]],
    *,
    semantic: bool,
) -> dict[str, Any]:
    matched: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    used_observed: set[int] = set()
    key = _semantic_frame_key if semantic else _exact_frame_key
    for expected_index, expected in enumerate(expected_frames):
        expected_key = key(expected)
        observed_index = next(
            (
                candidate_index
                for candidate_index, observed in enumerate(observed_frames)
                if candidate_index not in used_observed and key(observed) == expected_key
            ),
            None,
        )
        if observed_index is None:
            missing_item = {
                "expected_index": expected_index,
                **_frame_summary(expected),
            }
            closest = _closest_frame_mismatch(
                expected,
                observed_frames,
                used_observed,
                semantic=semantic,
            )
            if closest is not None:
                missing_item["closest_observed"] = closest
            missing.append(missing_item)
            continue
        used_observed.add(observed_index)
        matched.append(
            {
                "expected_index": expected_index,
                "observed_index": observed_index,
                "operation": expected.get("operation"),
                "target_mac": expected.get("target_mac"),
                "payload_sha256": _payload_sha256(expected),
            }
        )
    unmatched_observed = [
        {"observed_index": index, **_frame_summary(frame)}
        for index, frame in enumerate(observed_frames)
        if index not in used_observed
    ]
    return {
        "matched": bool(expected_frames) and not missing,
        "matched_count": len(matched),
        "expected_frame_count": len(expected_frames),
        "observed_frame_count": len(observed_frames),
        "missing": missing,
        "unmatched_observed": unmatched_observed,
        "matches": matched,
    }


def _compare_capture_diagnostics(
    exact: dict[str, Any],
    semantic: dict[str, Any],
) -> dict[str, Any]:
    if not int(semantic.get("expected_frame_count") or 0):
        status = "no-expected-frames"
        primary = "none"
    elif bool(semantic.get("matched")) and bool(exact.get("matched")):
        status = "exact-match"
        primary = "none"
    elif bool(semantic.get("matched")):
        status = "semantic-match-exact-mismatch"
        primary = "exact"
    else:
        status = "semantic-mismatch"
        primary = "semantic"
    primary_summary = (
        _compare_capture_match_diagnostics(exact)
        if primary == "exact"
        else _compare_capture_match_diagnostics(semantic)
        if primary == "semantic"
        else {}
    )
    return {
        "status": status,
        "primary": primary,
        "nearest_differences": list(primary_summary.get("nearest_mismatches", [])),
        "exact": _compare_capture_match_diagnostics(exact),
        "semantic": _compare_capture_match_diagnostics(semantic),
    }


def _compare_capture_match_diagnostics(match: dict[str, Any]) -> dict[str, Any]:
    missing = match.get("missing")
    unmatched = match.get("unmatched_observed")
    missing_items = missing if isinstance(missing, list) else []
    unmatched_items = unmatched if isinstance(unmatched, list) else []
    return {
        "matched": bool(match.get("matched")),
        "matched_count": int(match.get("matched_count") or 0),
        "expected_frame_count": int(match.get("expected_frame_count") or 0),
        "observed_frame_count": int(match.get("observed_frame_count") or 0),
        "missing_count": len(missing_items),
        "unmatched_observed_count": len(unmatched_items),
        "nearest_mismatches": _compare_capture_nearest_mismatches(missing_items),
    }


def _compare_capture_nearest_mismatches(missing_items: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in missing_items:
        if not isinstance(item, dict):
            continue
        closest = item.get("closest_observed")
        if not isinstance(closest, dict):
            continue
        result.append(
            {
                "expected_index": item.get("expected_index"),
                "expected_operation": item.get("operation"),
                "expected_target_mac": item.get("target_mac"),
                "observed_index": closest.get("observed_index"),
                "observed_operation": closest.get("operation"),
                "observed_target_mac": closest.get("target_mac"),
                "similarity_score": closest.get("similarity_score"),
                "differing_fields": list(
                    closest.get("differing_fields", [])
                    if isinstance(closest.get("differing_fields"), list)
                    else []
                ),
            }
        )
    return result


def _closest_frame_mismatch(
    expected: dict[str, Any],
    observed_frames: list[dict[str, Any]],
    used_observed: set[int],
    *,
    semantic: bool,
) -> dict[str, Any] | None:
    candidates: list[tuple[int, int, dict[str, Any], list[dict[str, Any]]]] = []
    fields = _frame_comparison_fields(expected, semantic=semantic)
    for observed_index, observed in enumerate(observed_frames):
        if observed_index in used_observed:
            continue
        diffs = _frame_field_differences(expected, observed, fields)
        score = len(fields) - len(diffs)
        if score <= 0:
            continue
        if _semantic_operation(str(expected.get("operation") or "")) == _semantic_operation(
            str(observed.get("operation") or "")
        ):
            score += 3
        if expected.get("target_mac") and expected.get("target_mac") == observed.get("target_mac"):
            score += 2
        candidates.append((score, -observed_index, observed, diffs))
    if not candidates:
        return None
    score, negative_index, observed, diffs = max(
        candidates,
        key=lambda item: (item[0], item[1]),
    )
    observed_index = -negative_index
    return {
        "observed_index": observed_index,
        "similarity_score": score,
        "compared_fields": fields,
        "differing_fields": diffs,
        **_frame_summary(observed),
    }


def _frame_comparison_fields(frame: dict[str, Any], *, semantic: bool) -> list[str]:
    operation = _semantic_operation(str(frame.get("operation") or ""))
    fields = [
        "operation",
        "target_mac",
        "master_mac",
        "channel",
        "outer_rx_type",
        "command_hex",
    ]
    if operation in {"live-pwm", "live-pwm-sync", "live-bind", "live-unbind"}:
        fields.extend(("rx_type", "payload_channel", "pwm_values"))
    elif operation == "live-rgb":
        fields.extend(
            (
                "effect_index",
                "packet_index",
                "packet_count",
                "compressed_length",
                "frame_count",
                "led_count",
                "interval_ms",
            )
        )
    if not semantic:
        fields.extend(("sequence", "payload_sha256"))
    return _unique_preserve_order(fields)


def _frame_field_differences(
    expected: dict[str, Any],
    observed: dict[str, Any],
    fields: list[str],
) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    for field in fields:
        expected_value = _frame_comparison_value(expected, field)
        observed_value = _frame_comparison_value(observed, field)
        if expected_value == observed_value:
            continue
        diffs.append(
            {
                "field": field,
                "expected": expected_value,
                "observed": observed_value,
            }
        )
    return diffs


def _frame_comparison_value(frame: dict[str, Any], field: str) -> Any:
    if field == "operation":
        return _semantic_operation(str(frame.get("operation") or ""))
    if field == "payload_sha256":
        return _payload_sha256(frame)
    return frame.get(field)


def _exact_frame_key(frame: dict[str, Any]) -> tuple[Any, ...]:
    return (frame.get("payload_hex"), frame.get("channel"), frame.get("outer_rx_type"))


def _semantic_frame_key(frame: dict[str, Any]) -> tuple[Any, ...]:
    operation = _semantic_operation(str(frame.get("operation") or ""))
    common = (
        operation,
        frame.get("target_mac"),
        frame.get("master_mac"),
        frame.get("channel"),
        frame.get("outer_rx_type"),
        frame.get("command_hex"),
    )
    if operation in {"live-pwm", "live-pwm-sync", "live-bind", "live-unbind"}:
        return common + (
            frame.get("rx_type"),
            frame.get("payload_channel"),
            tuple(frame.get("pwm_values") or ()),
        )
    if operation == "live-rgb":
        return common + (
            frame.get("effect_index"),
            frame.get("packet_index"),
            frame.get("packet_count"),
            frame.get("compressed_length"),
            frame.get("frame_count"),
            frame.get("led_count"),
            frame.get("interval_ms"),
        )
    return common + (frame.get("payload_hex"),)


def _semantic_operation(operation: str) -> str:
    if operation == "live-pwm-mirror":
        return "live-pwm"
    return operation


def _frame_summary(frame: dict[str, Any]) -> dict[str, Any]:
    payload_hex = str(frame.get("payload_hex") or "")
    summary: dict[str, Any] = {
        "operation": frame.get("operation"),
        "target_mac": frame.get("target_mac"),
        "master_mac": frame.get("master_mac"),
        "channel": frame.get("channel"),
        "outer_rx_type": frame.get("outer_rx_type"),
        "payload_sha256": _payload_sha256(frame),
        "payload_first32_hex": payload_hex[:64],
    }
    for key in (
        "rx_type",
        "payload_channel",
        "sequence",
        "pwm_values",
        "effect_index",
        "packet_index",
        "packet_count",
        "compressed_length",
        "frame_count",
        "led_count",
        "interval_ms",
        "original_operation",
        "motherboard_pwm",
        "motherboard_pwm_snapshot_index",
        "inferred_from_snapshot",
        "chunk_packet_indexes",
        "chunk_sequences",
        "rgb_sequence_id",
        "rgb_sequence_primary_index",
        "rgb_sequence_member_index",
        "rgb_decode_status",
    ):
        if key in frame:
            summary[key] = frame[key]
    rgb_payload = frame.get("rgb_payload")
    if isinstance(rgb_payload, dict):
        summary["rgb_payload"] = {
            key: rgb_payload[key]
            for key in (
                "decode_status",
                "static_color",
                "static_color_hex",
                "decoded_length",
                "expected_decoded_length",
                "decoded_sha256",
                "unique_color_count",
                "sample_colors_hex",
                "sequence_rf_frame_count",
                "sequence_frame_indexes",
                "first_packet_retransmit_count",
                "first_packet_frame_indexes",
                "backref_count",
                "literal_line_count",
            )
            if key in rgb_payload
        }
    return summary


def _payload_sha256(frame: dict[str, Any]) -> str:
    payload_hex = str(frame.get("payload_hex") or "")
    try:
        payload = bytes.fromhex(payload_hex)
    except ValueError:
        payload = payload_hex.encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def _record_mac_values(records: Iterable[dict[str, Any]], field: str) -> list[str]:
    values: list[str] = []
    for record in records:
        value = record.get(field)
        if isinstance(value, str) and value:
            values.append(value.lower())
        for device in record.get("devices", []) if isinstance(record.get("devices"), list) else []:
            if isinstance(device, dict):
                device_value = device.get(field)
                if isinstance(device_value, str) and device_value:
                    values.append(device_value.lower())
    return values


def _unique_strings(values: Iterable[str]) -> list[str]:
    return sorted(dict.fromkeys(value for value in values if value))


def _bytes_to_mac(raw: bytes) -> str:
    return ":".join(f"{byte:02x}" for byte in raw)
