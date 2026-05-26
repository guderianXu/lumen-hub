#!/usr/bin/env python3
from __future__ import annotations

import json
import argparse
from dataclasses import asdict, dataclass
from pathlib import Path

from usb9_lcd.lianli import KNOWN_USB_DEVICES, UDEV_RULES, scan_known_usb_devices
from usb9_lcd.lianli.analysis import analyze_live_log, diff_snapshot_files, summarize_experiment_dir
from usb9_lcd.lianli.artifact import (
    DEFAULT_TREE_MAX_FILE_SIZE,
    HID_JS_MAX_FILE_SIZE,
    analyze_artifact_file,
    analyze_artifact_tree,
    artifact_evidence_matrix,
    diff_artifact_files,
    extract_hid_js_commands,
    extract_wireless_js_clues,
)
from usb9_lcd.lianli.capture import (
    analyze_capture_file,
    capture_protocol_report_file,
    capture_replay_plan_file,
    capture_signature_match_file,
    capture_timeline_report_file,
    capture_transport_report_file,
    capture_triage_report_file,
    capture_set_report,
    compare_capture_file,
    linux_control_action_plan_report,
    linux_control_manifest_report,
    linux_control_packet_compare_report,
    linux_control_packet_preview_report,
    linux_control_preflight_report,
    linux_control_target_registry_report,
    linux_control_write_gate_report,
    linux_interface_contract_report,
    protocol_signature_catalog,
    summarize_capture_dir,
    usb_capture_readiness,
    windows_capture_plan,
)
from usb9_lcd.lianli.changelog import DEFAULT_CHANGELOG_URL, analyze_lconnect_changelog
from usb9_lcd.lianli.lcd import (
    build_wireless_lcd_packet,
    create_pyusb_lcd_backend,
    wireless_lcd_command_from_name,
    wireless_lcd_encryption_available,
)
from usb9_lcd.lianli.wireless import (
    LianLiWirelessBackend,
    LianLiWirelessError,
    WirelessDeviceInfo,
    build_master_query_request,
    build_wireless_list_request,
    create_pyusb_backend,
    extract_motherboard_pwm,
    infer_led_count,
)


WRITE_CONFIRM_TOKEN = "WRITE-LIANLI"


@dataclass(frozen=True)
class UsbDevice:
    vendor_id: str
    product_id: str
    label: str
    manufacturer: str
    product: str
    serial: str
    sysfs_path: str
    busnum: str
    devnum: str


def scan() -> list[UsbDevice]:
    devices: list[UsbDevice] = []
    for device in scan_known_usb_devices(Path("/sys")):
        devices.append(
            UsbDevice(
                vendor_id=f"{device.vendor_id:04x}",
                product_id=f"{device.product_id:04x}",
                label=device.label,
                manufacturer=device.manufacturer,
                product=device.product,
                serial=device.serial,
                sysfs_path=device.sysfs_path,
                busnum=device.busnum,
                devnum=device.devnum,
            )
        )
    return devices


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only LIAN LI L-Wireless probe and packet dry-run tool"
    )
    parser.add_argument(
        "--save-json",
        type=Path,
        help="Also write the JSON result to this path for hardware validation logs",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("scan", help="List known LIAN LI USB devices from sysfs")
    subparsers.add_parser("udev-rules", help="Print suggested Linux udev rules")
    subparsers.add_parser("live-list", help="Read L-Wireless receiver snapshot via PyUSB")

    validate = subparsers.add_parser(
        "validate-readonly",
        help="Run read-only hardware validation probes and save per-step JSON logs",
    )
    validate.add_argument("--output-dir", type=Path, default=Path(".cache/lianli/validation"))
    validate.add_argument("--skip-lcd", action="store_true")

    receiver_bundle = subparsers.add_parser(
        "receiver-validation-bundle",
        help="Run the post-plug read-only receiver validation bundle and save evidence JSON files",
    )
    receiver_bundle.add_argument("--output-dir", type=Path, default=Path(".cache/lianli/hardware"))
    receiver_bundle.add_argument("--capture-dir", type=Path, default=Path(".cache/lianli"))
    receiver_bundle.add_argument("--experiment-dir", type=Path)
    receiver_bundle.add_argument("--skip-lcd", action="store_true")
    receiver_bundle.add_argument("--version", default="2.1.17")
    receiver_bundle.add_argument("--capture-base", default=None)
    receiver_bundle.add_argument("--sys-root", type=Path, default=Path("/sys"))
    receiver_bundle.add_argument("--dev-root", type=Path, default=Path("/dev"))
    receiver_bundle.add_argument("--led-count", type=int, default=12)
    receiver_bundle.add_argument("--rainbow-frames", type=int, default=3)
    receiver_bundle.add_argument("--interval-ms", type=int, default=40)
    receiver_bundle.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)

    analyze = subparsers.add_parser(
        "analyze-log",
        help="Analyze one saved live write JSON log for receiver state changes",
    )
    analyze.add_argument("path", type=Path)

    diff = subparsers.add_parser(
        "diff-snapshots",
        help="Compare two saved receiver snapshot JSON files",
    )
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)

    summarize = subparsers.add_parser(
        "summarize-experiments",
        help="Summarize a directory of saved LIAN LI JSON validation/write logs",
    )
    summarize.add_argument("path", type=Path)

    artifact = subparsers.add_parser(
        "analyze-artifact",
        help="Scan one L-Connect installer/binary artifact for static USB/protocol clues",
    )
    artifact.add_argument("path", type=Path)

    artifact_tree = subparsers.add_parser(
        "analyze-artifact-tree",
        help="Recursively scan an extracted L-Connect artifact tree for static clues",
    )
    artifact_tree.add_argument("path", type=Path)
    artifact_tree.add_argument(
        "--max-file-size",
        type=int,
        default=DEFAULT_TREE_MAX_FILE_SIZE,
        help="Skip files larger than this many bytes; raise it for huge NSIS payloads",
    )

    artifact_diff = subparsers.add_parser(
        "diff-artifacts",
        help="Compare two installer/binary artifacts and report changed ranges plus new static clues",
    )
    artifact_diff.add_argument("before", type=Path)
    artifact_diff.add_argument("after", type=Path)
    artifact_diff.add_argument(
        "--block-size",
        type=int,
        default=64 * 1024,
        help="Fixed block size for similarity hashing",
    )

    artifact_matrix = subparsers.add_parser(
        "artifact-evidence-matrix",
        help="Summarize saved artifact/JS scan JSON reports by L-Connect version",
    )
    artifact_matrix.add_argument("path", type=Path)

    hid_js = subparsers.add_parser(
        "extract-hid-js",
        help="Extract structured AL/SL V2 HID command templates from L-Connect JS assets",
    )
    hid_js.add_argument("path", type=Path)
    hid_js.add_argument(
        "--max-file-size",
        type=int,
        default=HID_JS_MAX_FILE_SIZE,
        help="Skip JS files larger than this many bytes",
    )

    wireless_js = subparsers.add_parser(
        "extract-wireless-js",
        help="Extract wireless/USB/IPC clue contexts from L-Connect JS assets",
    )
    wireless_js.add_argument("path", type=Path)
    wireless_js.add_argument(
        "--max-file-size",
        type=int,
        default=HID_JS_MAX_FILE_SIZE,
        help="Skip JS files larger than this many bytes",
    )

    changelog = subparsers.add_parser(
        "analyze-changelog",
        help="Rank official L-Connect 3 changelog versions by L-Wireless relevance",
    )
    changelog.add_argument(
        "source",
        nargs="?",
        default=DEFAULT_CHANGELOG_URL,
        help="Official changelog URL or a saved HTML/text file",
    )
    changelog.add_argument("--top", type=int, default=8, help="Number of top wireless-related versions to return")

    capture = subparsers.add_parser(
        "analyze-capture",
        help="Decode a text/JSON hex export from L-Connect/USBPcap into RF operations",
    )
    capture.add_argument("path", type=Path)

    replay_plan = subparsers.add_parser(
        "capture-replay-plan",
        help="Build copy-paste dry-run and compare commands from decoded capture RF frames",
    )
    replay_plan.add_argument("path", type=Path)

    protocol_report = subparsers.add_parser(
        "capture-protocol-report",
        help="Aggregate decoded capture RF frames into device, operation, and parameter evidence",
    )
    protocol_report.add_argument("path", type=Path)

    timeline_report = subparsers.add_parser(
        "capture-timeline-report",
        help="Render decoded receiver snapshots and RF frames as a chronological protocol timeline",
    )
    timeline_report.add_argument("path", type=Path)

    transport_report = subparsers.add_parser(
        "capture-transport-report",
        help="Summarize USBPcap/export fields and rank payloads that look like L-Wireless traffic",
    )
    transport_report.add_argument("path", type=Path)

    signatures = subparsers.add_parser(
        "protocol-signatures",
        help="Emit local packet/payload signatures for known L-Wireless operations",
    )
    signatures.add_argument("--led-count", type=int, default=12)
    signatures.add_argument("--rainbow-frames", type=int, default=3)
    signatures.add_argument("--interval-ms", type=int, default=40)
    signatures.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)

    signature_match = subparsers.add_parser(
        "capture-signature-match",
        help="Match one capture against the local L-Wireless protocol signature catalog",
    )
    signature_match.add_argument("path", type=Path)
    signature_match.add_argument("--led-count", type=int, default=12)
    signature_match.add_argument("--rainbow-frames", type=int, default=3)
    signature_match.add_argument("--interval-ms", type=int, default=40)
    signature_match.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)

    triage_report = subparsers.add_parser(
        "capture-triage-report",
        help="Run transport, signature, replay, and protocol triage for one capture",
    )
    triage_report.add_argument("path", type=Path)
    triage_report.add_argument("--led-count", type=int, default=12)
    triage_report.add_argument("--rainbow-frames", type=int, default=3)
    triage_report.add_argument("--interval-ms", type=int, default=40)
    triage_report.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)

    capture_summary = subparsers.add_parser(
        "summarize-captures",
        help="Batch-analyze a capture file or directory and rank protocol-rich captures",
    )
    capture_summary.add_argument("path", type=Path)

    capture_set = subparsers.add_parser(
        "capture-set-report",
        help="Audit a planned Windows USBPcap capture set against expected L-Wireless scenario evidence",
    )
    capture_set.add_argument("path", type=Path)
    capture_set.add_argument("--version", default="2.1.17")
    capture_set.add_argument("--capture-base", default=None)
    capture_set.add_argument(
        "--experiment-dir",
        type=Path,
        help="Attach summarize-experiments output from a Linux validation/experiment directory",
    )
    capture_set.add_argument("--led-count", type=int, default=12)
    capture_set.add_argument("--rainbow-frames", type=int, default=3)
    capture_set.add_argument("--interval-ms", type=int, default=40)
    capture_set.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)

    linux_contract = subparsers.add_parser(
        "linux-interface-contract",
        help="Export the capture-derived Linux/PyUSB control contract without the full capture audit",
    )
    linux_contract.add_argument("path", type=Path)
    linux_contract.add_argument("--version", default="2.1.17")
    linux_contract.add_argument("--capture-base", default=None)
    linux_contract.add_argument(
        "--experiment-dir",
        type=Path,
        help="Attach summarize-experiments output from a Linux validation/experiment directory",
    )
    linux_contract.add_argument("--led-count", type=int, default=12)
    linux_contract.add_argument("--rainbow-frames", type=int, default=3)
    linux_contract.add_argument("--interval-ms", type=int, default=40)
    linux_contract.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)

    linux_manifest = subparsers.add_parser(
        "linux-control-manifest",
        help="Export a GUI/backend-friendly Linux L-Wireless control manifest",
    )
    linux_manifest.add_argument("path", type=Path)
    linux_manifest.add_argument("--version", default="2.1.17")
    linux_manifest.add_argument("--capture-base", default=None)
    linux_manifest.add_argument(
        "--experiment-dir",
        type=Path,
        help="Attach summarize-experiments output from a Linux validation/experiment directory",
    )
    linux_manifest.add_argument("--led-count", type=int, default=12)
    linux_manifest.add_argument("--rainbow-frames", type=int, default=3)
    linux_manifest.add_argument("--interval-ms", type=int, default=40)
    linux_manifest.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)

    linux_preflight = subparsers.add_parser(
        "linux-control-preflight",
        help="Check manifest, local USB visibility, and /dev/bus/usb permissions before live control",
    )
    linux_preflight.add_argument("path", type=Path)
    linux_preflight.add_argument("--version", default="2.1.17")
    linux_preflight.add_argument("--capture-base", default=None)
    linux_preflight.add_argument(
        "--experiment-dir",
        type=Path,
        help="Attach summarize-experiments output from a Linux validation/experiment directory",
    )
    linux_preflight.add_argument("--sys-root", type=Path, default=Path("/sys"))
    linux_preflight.add_argument("--dev-root", type=Path, default=Path("/dev"))
    linux_preflight.add_argument("--led-count", type=int, default=12)
    linux_preflight.add_argument("--rainbow-frames", type=int, default=3)
    linux_preflight.add_argument("--interval-ms", type=int, default=40)
    linux_preflight.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)

    linux_action_plan = subparsers.add_parser(
        "linux-control-action-plan",
        help="Build an ordered safe-action plan from preflight, manifest, and capture evidence",
    )
    linux_action_plan.add_argument("path", type=Path)
    linux_action_plan.add_argument("--version", default="2.1.17")
    linux_action_plan.add_argument("--capture-base", default=None)
    linux_action_plan.add_argument(
        "--experiment-dir",
        type=Path,
        help="Attach summarize-experiments output from a Linux validation/experiment directory",
    )
    linux_action_plan.add_argument("--sys-root", type=Path, default=Path("/sys"))
    linux_action_plan.add_argument("--dev-root", type=Path, default=Path("/dev"))
    linux_action_plan.add_argument("--led-count", type=int, default=12)
    linux_action_plan.add_argument("--rainbow-frames", type=int, default=3)
    linux_action_plan.add_argument("--interval-ms", type=int, default=40)
    linux_action_plan.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)

    linux_write_gate = subparsers.add_parser(
        "linux-control-write-gate",
        help="Summarize whether guarded WRITE-LIANLI writes are currently allowed",
    )
    linux_write_gate.add_argument("path", type=Path)
    linux_write_gate.add_argument("--version", default="2.1.17")
    linux_write_gate.add_argument("--capture-base", default=None)
    linux_write_gate.add_argument(
        "--experiment-dir",
        type=Path,
        help="Attach summarize-experiments output from a Linux validation/experiment directory",
    )
    linux_write_gate.add_argument("--sys-root", type=Path, default=Path("/sys"))
    linux_write_gate.add_argument("--dev-root", type=Path, default=Path("/dev"))
    linux_write_gate.add_argument("--led-count", type=int, default=12)
    linux_write_gate.add_argument("--rainbow-frames", type=int, default=3)
    linux_write_gate.add_argument("--interval-ms", type=int, default=40)
    linux_write_gate.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)

    linux_target_registry = subparsers.add_parser(
        "linux-control-target-registry",
        help="Export capture-derived target contexts for GUI/backend packet building",
    )
    linux_target_registry.add_argument("path", type=Path)
    linux_target_registry.add_argument("--version", default="2.1.17")
    linux_target_registry.add_argument("--capture-base", default=None)
    linux_target_registry.add_argument(
        "--experiment-dir",
        type=Path,
        help="Attach summarize-experiments output from a Linux validation/experiment directory",
    )
    linux_target_registry.add_argument("--sys-root", type=Path, default=Path("/sys"))
    linux_target_registry.add_argument("--dev-root", type=Path, default=Path("/dev"))
    linux_target_registry.add_argument("--led-count", type=int, default=12)
    linux_target_registry.add_argument("--rainbow-frames", type=int, default=3)
    linux_target_registry.add_argument("--interval-ms", type=int, default=40)
    linux_target_registry.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)

    linux_packet_preview = subparsers.add_parser(
        "linux-control-packet-preview",
        help="Build no-write RF packet previews from the capture-derived target registry",
    )
    linux_packet_preview.add_argument("path", type=Path)
    linux_packet_preview.add_argument(
        "control_operation",
        choices=(
            "live-pwm",
            "live-pwm-sync",
            "live-pwm-mirror",
            "live-bind",
            "live-unbind",
            "live-rgb",
            "live-rainbow",
        ),
    )
    linux_packet_preview.add_argument("--target-id", default="")
    linux_packet_preview.add_argument("--version", default="2.1.17")
    linux_packet_preview.add_argument("--capture-base", default=None)
    linux_packet_preview.add_argument("--experiment-dir", type=Path)
    linux_packet_preview.add_argument("--sys-root", type=Path, default=Path("/sys"))
    linux_packet_preview.add_argument("--dev-root", type=Path, default=Path("/dev"))
    linux_packet_preview.add_argument("--pwm", type=int)
    linux_packet_preview.add_argument("--pwm-values", type=_parse_pwm_values)
    linux_packet_preview.add_argument("--motherboard-pwm", type=int, default=120)
    linux_packet_preview.add_argument("--color", default=None)
    linux_packet_preview.add_argument("--led-count", type=int)
    linux_packet_preview.add_argument("--frame-count", type=int)
    linux_packet_preview.add_argument("--interval-ms", type=int)
    linux_packet_preview.add_argument("--effect-index", type=lambda value: int(value, 0))

    linux_packet_compare = subparsers.add_parser(
        "linux-control-packet-compare",
        help="Compare a capture-derived packet preview against an official L-Connect capture",
    )
    linux_packet_compare.add_argument("path", type=Path)
    linux_packet_compare.add_argument(
        "control_operation",
        choices=(
            "live-pwm",
            "live-pwm-sync",
            "live-pwm-mirror",
            "live-bind",
            "live-unbind",
            "live-rgb",
            "live-rainbow",
        ),
    )
    linux_packet_compare.add_argument("observed_capture", type=Path)
    linux_packet_compare.add_argument("--target-id", default="")
    linux_packet_compare.add_argument("--version", default="2.1.17")
    linux_packet_compare.add_argument("--capture-base", default=None)
    linux_packet_compare.add_argument("--experiment-dir", type=Path)
    linux_packet_compare.add_argument("--sys-root", type=Path, default=Path("/sys"))
    linux_packet_compare.add_argument("--dev-root", type=Path, default=Path("/dev"))
    linux_packet_compare.add_argument("--pwm", type=int)
    linux_packet_compare.add_argument("--pwm-values", type=_parse_pwm_values)
    linux_packet_compare.add_argument("--motherboard-pwm", type=int, default=120)
    linux_packet_compare.add_argument("--color", default=None)
    linux_packet_compare.add_argument("--led-count", type=int)
    linux_packet_compare.add_argument("--frame-count", type=int)
    linux_packet_compare.add_argument("--interval-ms", type=int)
    linux_packet_compare.add_argument("--effect-index", type=lambda value: int(value, 0))

    compare_capture = subparsers.add_parser(
        "compare-capture",
        help="Compare a L-Connect capture export against locally built RF packets",
    )
    compare_capture.add_argument("path", type=Path)
    compare_capture.add_argument(
        "expected_operation",
        choices=("pwm", "pwm-sync", "pwm-mirror", "bind", "unbind", "rgb", "rainbow"),
    )
    _add_target_args(compare_capture)
    compare_capture.add_argument("--pwm", type=int, default=120, help="PWM value for pwm")
    compare_capture.add_argument(
        "--pwm-values",
        type=_parse_pwm_values,
        help="PWM tuple for pwm, e.g. 80,90,100,110; overrides --pwm",
    )
    compare_mirror_source = compare_capture.add_mutually_exclusive_group()
    compare_mirror_source.add_argument("--motherboard-pwm", type=int, help="Decoded PWM value for pwm-mirror")
    compare_mirror_source.add_argument("--snapshot-hex", help="Receiver snapshot hex used to decode pwm-mirror")
    compare_capture.add_argument(
        "--disable",
        action="store_true",
        help="For pwm-sync, compare the direct fallback PWM packet instead",
    )
    compare_capture.add_argument("--fallback-pwm", type=int, default=100)
    compare_capture.add_argument("--color", default="0,0,0", help="RGB triple for rgb")
    compare_capture.add_argument("--frame-count", type=int, default=24, help="Frame count for rainbow")
    compare_capture.add_argument("--interval-ms", type=int, default=50, help="Frame interval for rainbow")
    compare_capture.add_argument("--led-count", type=int, help="LED count override for rgb/rainbow")
    compare_capture.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)

    windows_plan = subparsers.add_parser(
        "windows-capture-plan",
        help="Build a Wine/VM/Docker plan for capturing official L-Connect USB traffic",
    )
    windows_plan.add_argument("--version", default="2.1.17", help="L-Connect version under test")
    windows_plan.add_argument("--installer", type=Path, help="Optional local L-Connect installer path")
    windows_plan.add_argument("--capture-base", help="Output capture filename prefix")
    windows_plan.add_argument(
        "--environment",
        choices=("auto", "vm", "wine", "docker"),
        default="auto",
        help="Environment being considered; VM USB passthrough remains the protocol-capture recommendation",
    )

    usb_readiness = subparsers.add_parser(
        "usb-capture-readiness",
        help="Check local USB visibility and next steps for L-Wireless capture/control validation",
    )
    usb_readiness.add_argument("--sys-root", type=Path, default=Path("/sys"))

    master = subparsers.add_parser(
        "live-master",
        help="Query active master controller MAC via PyUSB RF sender",
    )
    master.add_argument("--channel", type=int, default=8)

    master_dry = subparsers.add_parser(
        "dry-run-master-query",
        help="Build master query request without writing USB",
    )
    master_dry.add_argument("--channel", type=int, default=8)

    pwm = subparsers.add_parser("dry-run-pwm", help="Build PWM RF packets without writing USB")
    _add_target_args(pwm)
    pwm.add_argument("--pwm", type=int, default=120, help="PWM value 0-255")
    pwm.add_argument(
        "--pwm-values",
        type=_parse_pwm_values,
        help="PWM tuple, e.g. 80,90,100,110; overrides --pwm",
    )

    sync = subparsers.add_parser(
        "dry-run-pwm-sync",
        help="Build motherboard PWM sync RF packets without writing USB",
    )
    _add_target_args(sync)
    sync.add_argument(
        "--disable",
        action="store_true",
        help="Build fallback direct PWM packets instead of sync enable packets",
    )
    sync.add_argument("--fallback-pwm", type=int, default=100)

    mirror = subparsers.add_parser(
        "dry-run-pwm-mirror",
        help="Build direct PWM RF packets from a motherboard PWM value or receiver snapshot",
    )
    _add_target_args(mirror)
    mirror_source = mirror.add_mutually_exclusive_group(required=True)
    mirror_source.add_argument("--motherboard-pwm", type=int, help="Already-decoded PWM value 0-255")
    mirror_source.add_argument("--snapshot-hex", help="Receiver snapshot hex used to decode motherboard PWM")
    mirror.add_argument("--min-pwm", type=int, default=40)

    bind = subparsers.add_parser(
        "dry-run-bind",
        help="Build bind RF packets for an unbound receiver without writing USB",
    )
    _add_target_args(bind)

    unbind = subparsers.add_parser(
        "dry-run-unbind",
        help="Build unbind RF packets for a bound receiver without writing USB",
    )
    _add_target_args(unbind)

    rgb = subparsers.add_parser("dry-run-rgb", help="Build static RGB RF packets without writing USB")
    _add_target_args(rgb)
    rgb.add_argument("--color", default="0,0,0", help="RGB triple, e.g. 255,0,0")
    rgb.add_argument("--led-count", type=int)
    rgb.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)

    rainbow = subparsers.add_parser(
        "dry-run-rainbow",
        help="Build multi-frame rainbow RGB RF packets without writing USB",
    )
    _add_target_args(rainbow)
    rainbow.add_argument("--frame-count", type=int, default=24)
    rainbow.add_argument("--interval-ms", type=int, default=50)
    rainbow.add_argument("--led-count", type=int)
    rainbow.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)

    lcd = subparsers.add_parser(
        "dry-run-lcd",
        help="Build wireless LCD command header without writing USB",
    )
    lcd.add_argument(
        "lcd_command",
        choices=("handshake", "get-ver", "brightness", "rotate", "push-jpg", "reboot"),
    )
    lcd.add_argument("--value", type=int, help="Single-byte value for brightness/rotate")
    lcd.add_argument("--payload-size", type=int, default=0, help="Dummy payload size for push-jpg")
    lcd.add_argument("--timestamp-ms", type=int, default=0)
    lcd.add_argument(
        "--encrypt",
        action="store_true",
        help="Also DES-CBC encrypt the header; requires pycryptodomex",
    )

    lcd_info = subparsers.add_parser(
        "live-lcd-info",
        help="Read wireless LCD handshake and firmware via PyUSB",
    )
    lcd_info.add_argument(
        "--mode",
        choices=("handshake", "firmware", "both"),
        default="both",
    )

    lcd_control = subparsers.add_parser(
        "live-lcd-control",
        help="Guarded live wireless LCD brightness/rotation control",
    )
    lcd_control.add_argument("--brightness", type=int)
    lcd_control.add_argument("--rotation", type=int, choices=(0, 90, 180, 270))
    _add_write_confirm_arg(lcd_control)

    live_pwm = subparsers.add_parser(
        "live-pwm",
        help="Guarded live PWM write to one bound receiver",
    )
    live_pwm.add_argument("--mac", required=True)
    live_pwm.add_argument("--pwm", type=int, required=True)
    live_pwm.add_argument("--min-pwm", type=int, default=40)
    _add_write_confirm_arg(live_pwm)

    safe_pwm = subparsers.add_parser(
        "safe-pwm-experiment",
        help="Run a guarded single-MAC PWM experiment and save before/write/after analysis logs",
    )
    safe_pwm.add_argument("--mac", required=True)
    safe_pwm.add_argument("--pwm", type=int, required=True)
    safe_pwm.add_argument("--min-pwm", type=int, default=40)
    safe_pwm.add_argument("--output-dir", type=Path, default=Path(".cache/lianli/pwm-experiment"))
    _add_write_confirm_arg(safe_pwm)

    live_sync = subparsers.add_parser(
        "live-pwm-sync",
        help="Guarded live motherboard PWM sync write to one bound receiver",
    )
    live_sync.add_argument("--mac", required=True)
    live_sync.add_argument("--disable", action="store_true")
    live_sync.add_argument("--fallback-pwm", type=int, default=100)
    live_sync.add_argument("--min-fallback-pwm", type=int, default=40)
    _add_write_confirm_arg(live_sync)

    live_mirror = subparsers.add_parser(
        "live-pwm-mirror",
        help="Read motherboard PWM from the receiver snapshot and write direct PWM to one bound receiver",
    )
    live_mirror.add_argument("--mac", required=True)
    live_mirror.add_argument("--min-pwm", type=int, default=40)
    _add_write_confirm_arg(live_mirror)

    safe_sync = subparsers.add_parser(
        "safe-sync-experiment",
        help="Run a guarded single-MAC motherboard PWM sync experiment and save analysis logs",
    )
    safe_sync.add_argument("--mac", required=True)
    safe_sync.add_argument("--disable", action="store_true")
    safe_sync.add_argument("--fallback-pwm", type=int, default=100)
    safe_sync.add_argument("--min-fallback-pwm", type=int, default=40)
    safe_sync.add_argument("--output-dir", type=Path, default=Path(".cache/lianli/sync-experiment"))
    _add_write_confirm_arg(safe_sync)

    safe_mirror = subparsers.add_parser(
        "safe-pwm-mirror-experiment",
        help="Run a guarded single-MAC motherboard PWM mirror experiment and save analysis logs",
    )
    safe_mirror.add_argument("--mac", required=True)
    safe_mirror.add_argument("--min-pwm", type=int, default=40)
    safe_mirror.add_argument("--output-dir", type=Path, default=Path(".cache/lianli/pwm-mirror-experiment"))
    _add_write_confirm_arg(safe_mirror)

    live_rgb = subparsers.add_parser(
        "live-rgb",
        help="Guarded live static RGB write to one bound receiver",
    )
    live_rgb.add_argument("--mac", required=True)
    live_rgb.add_argument("--color", required=True, help="RGB triple, e.g. 255,0,0")
    live_rgb.add_argument("--led-count", type=int)
    live_rgb.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)
    _add_write_confirm_arg(live_rgb)

    live_rainbow = subparsers.add_parser(
        "live-rainbow",
        help="Guarded live generated rainbow RGB write to one bound receiver",
    )
    live_rainbow.add_argument("--mac", required=True)
    live_rainbow.add_argument("--frame-count", type=int, default=24)
    live_rainbow.add_argument("--interval-ms", type=int, default=50)
    live_rainbow.add_argument("--led-count", type=int)
    live_rainbow.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)
    _add_write_confirm_arg(live_rainbow)

    safe_rgb = subparsers.add_parser(
        "safe-rgb-experiment",
        help="Run a guarded single-MAC RGB experiment and save before/write/after analysis logs",
    )
    safe_rgb.add_argument("--mac", required=True)
    safe_rgb.add_argument("--color", required=True, help="RGB triple, e.g. 255,0,0")
    safe_rgb.add_argument("--led-count", type=int)
    safe_rgb.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)
    safe_rgb.add_argument("--output-dir", type=Path, default=Path(".cache/lianli/rgb-experiment"))
    _add_write_confirm_arg(safe_rgb)

    safe_rainbow = subparsers.add_parser(
        "safe-rainbow-experiment",
        help="Run a guarded generated rainbow RGB experiment and save before/write/after analysis logs",
    )
    safe_rainbow.add_argument("--mac", required=True)
    safe_rainbow.add_argument("--frame-count", type=int, default=24)
    safe_rainbow.add_argument("--interval-ms", type=int, default=50)
    safe_rainbow.add_argument("--led-count", type=int)
    safe_rainbow.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)
    safe_rainbow.add_argument("--output-dir", type=Path, default=Path(".cache/lianli/rainbow-experiment"))
    _add_write_confirm_arg(safe_rainbow)

    live_bind = subparsers.add_parser(
        "live-bind",
        help="Guarded live bind request for one unbound receiver",
    )
    live_bind.add_argument("--mac", required=True)
    live_bind.add_argument("--master-mac")
    live_bind.add_argument("--channel", type=int)
    live_bind.add_argument("--rx-type", type=int, required=True)
    _add_write_confirm_arg(live_bind)

    safe_bind = subparsers.add_parser(
        "safe-bind-experiment",
        help="Run a guarded bind experiment for one unbound receiver and save analysis logs",
    )
    safe_bind.add_argument("--mac", required=True)
    safe_bind.add_argument("--master-mac")
    safe_bind.add_argument("--channel", type=int)
    safe_bind.add_argument("--rx-type", type=int, required=True)
    safe_bind.add_argument("--output-dir", type=Path, default=Path(".cache/lianli/bind-experiment"))
    _add_write_confirm_arg(safe_bind)

    live_unbind = subparsers.add_parser(
        "live-unbind",
        help="Guarded live unbind request for one bound receiver",
    )
    live_unbind.add_argument("--mac", required=True)
    live_unbind.add_argument("--channel", type=int)
    _add_write_confirm_arg(live_unbind)

    safe_unbind = subparsers.add_parser(
        "safe-unbind-experiment",
        help="Run a guarded unbind experiment for one bound receiver and save analysis logs",
    )
    safe_unbind.add_argument("--mac", required=True)
    safe_unbind.add_argument("--channel", type=int)
    safe_unbind.add_argument("--output-dir", type=Path, default=Path(".cache/lianli/unbind-experiment"))
    _add_write_confirm_arg(safe_unbind)

    return parser


def _add_write_confirm_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--confirm",
        required=True,
        help=f"Required exact token for live USB writes: {WRITE_CONFIRM_TOKEN}",
    )


def _add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mac", default="aa:bb:cc:dd:ee:ff")
    parser.add_argument("--master-mac", default="10:20:30:40:50:60")
    parser.add_argument("--channel", type=int, default=8)
    parser.add_argument("--rx-type", type=int, default=3)
    parser.add_argument("--device-type", type=int, default=2)
    parser.add_argument("--fan-count", type=int, default=3)
    parser.add_argument("--sequence", type=int, default=7)
    parser.add_argument(
        "--current-pwm",
        type=_parse_pwm_values,
        default=(0, 0, 0, 0),
        help="Current receiver PWM tuple for bind/unbind replay, e.g. 80,90,100,110",
    )


def _fake_target(
    args: argparse.Namespace,
    *,
    master_mac: str | None = None,
    rx_type: int | None = None,
) -> WirelessDeviceInfo:
    return WirelessDeviceInfo(
        mac=args.mac,
        master_mac=args.master_mac if master_mac is None else master_mac,
        channel=args.channel,
        rx_type=args.rx_type if rx_type is None else rx_type,
        device_type=args.device_type,
        fan_count=args.fan_count,
        pwm_values=tuple(args.current_pwm),
        fan_rpm=(0, 0, 0, 0),
        command_sequence=args.sequence,
        raw=bytes(42),
    )


def _packet_summary(packets: list[bytes]) -> dict[str, object]:
    return {
        "packet_count": len(packets),
        "packet_size": len(packets[0]) if packets else 0,
        "first_packet_hex": packets[0].hex() if packets else "",
        "last_packet_hex": packets[-1].hex() if packets else "",
    }


def _expected_compare_packets(args: argparse.Namespace) -> list[bytes]:
    backend = LianLiWirelessBackend()
    if args.expected_operation == "pwm":
        return backend.build_pwm_packets(_fake_target(args), _pwm_values_from_args(args))
    if args.expected_operation == "pwm-sync":
        return backend.build_motherboard_pwm_sync_packets(
            _fake_target(args),
            enable=not args.disable,
            fallback_pwm=args.fallback_pwm,
        )
    if args.expected_operation == "pwm-mirror":
        motherboard_pwm = _motherboard_pwm_from_args(args)
        args.expected_motherboard_pwm = motherboard_pwm
        return backend.build_motherboard_pwm_mirror_packets(
            _fake_target(args),
            motherboard_pwm,
        )
    if args.expected_operation == "bind":
        target = _fake_target(args, master_mac="00:00:00:00:00:00", rx_type=0)
        return backend.build_bind_packets(
            target,
            master_mac=args.master_mac,
            rx_type=args.rx_type,
            channel=args.channel,
        )
    if args.expected_operation == "unbind":
        return backend.build_unbind_packets(_fake_target(args), channel=args.channel)
    if args.expected_operation == "rgb":
        return backend.build_static_rgb_packets(
            _fake_target(args),
            _parse_rgb(args.color),
            effect_index=args.effect_index,
            led_count=args.led_count,
        )
    if args.expected_operation == "rainbow":
        return backend.build_rainbow_rgb_packets(
            _fake_target(args),
            frame_count=args.frame_count,
            interval_ms=args.interval_ms,
            effect_index=args.effect_index,
            led_count=args.led_count,
        )
    raise LianLiWirelessError(f"unknown expected operation: {args.expected_operation}")


def _wireless_device_payload(device: WirelessDeviceInfo) -> dict[str, object]:
    return {
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


def _parse_rgb(value: str) -> tuple[int, int, int]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("RGB color must be R,G,B")
    try:
        rgb = tuple(int(part) for part in parts)
    except ValueError as error:
        raise argparse.ArgumentTypeError("RGB values must be integers") from error
    if any(component < 0 or component > 255 for component in rgb):
        raise argparse.ArgumentTypeError("RGB values must be in 0-255")
    return rgb  # type: ignore[return-value]


def _parse_pwm_values(value: str) -> tuple[int, int, int, int]:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(parts) == 1:
        parts = parts * 4
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("PWM values must be one value or four comma-separated values")
    try:
        pwm_values = tuple(int(part) for part in parts)
    except ValueError as error:
        raise argparse.ArgumentTypeError("PWM values must be integers") from error
    if any(value < 0 or value > 255 for value in pwm_values):
        raise argparse.ArgumentTypeError("PWM values must be in 0-255")
    return pwm_values  # type: ignore[return-value]


def _pwm_values_from_args(args: argparse.Namespace) -> tuple[int, int, int, int]:
    values = getattr(args, "pwm_values", None)
    if values is not None:
        return tuple(values)
    pwm = max(0, min(255, int(args.pwm)))
    return pwm, pwm, pwm, pwm


def _rgb_led_count(args: argparse.Namespace, target: WirelessDeviceInfo) -> int:
    led_count = getattr(args, "led_count", None)
    return int(led_count) if led_count is not None else infer_led_count(target)


def _rainbow_led_count(args: argparse.Namespace, target: WirelessDeviceInfo) -> int:
    return _rgb_led_count(args, target)


def _scan_payload() -> dict[str, object]:
    devices = scan()
    return {
        "known_ids": {
            f"{vid:04x}:{pid:04x}": label
            for (vid, pid), label in KNOWN_USB_DEVICES.items()
        },
        "wireless_list_request_hex": build_wireless_list_request().hex(),
        "devices": [asdict(device) for device in devices],
    }


def _require_write_confirmation(args: argparse.Namespace) -> None:
    if args.confirm != WRITE_CONFIRM_TOKEN:
        raise LianLiWirelessError(
            f"live writes require --confirm {WRITE_CONFIRM_TOKEN}"
        )


def _find_target(snapshot, mac: str) -> WirelessDeviceInfo:
    target = next(
        (device for device in snapshot.devices if device.mac.lower() == mac.lower()),
        None,
    )
    if target is None:
        raise LianLiWirelessError(f"receiver MAC not found in live snapshot: {mac}")
    return target


def _snapshot_payload_from_backend(backend: LianLiWirelessBackend) -> dict[str, object]:
    snapshot = backend.list_devices()
    return {
        "device_count": snapshot.device_count,
        "motherboard_pwm": snapshot.motherboard_pwm,
        "devices": [_wireless_device_payload(device) for device in snapshot.devices],
    }


def _safe_pwm_tuple(value: int, minimum: int) -> tuple[int, int, int, int]:
    if value < minimum:
        raise LianLiWirelessError(
            f"refusing PWM {value}; pass a value >= --min-pwm {minimum}"
        )
    pwm = max(0, min(255, int(value)))
    return pwm, pwm, pwm, pwm


def _decode_snapshot_hex(value: str) -> bytes:
    compact = "".join(char for char in value.strip() if char in "0123456789abcdefABCDEF")
    if len(compact) % 2:
        raise LianLiWirelessError("snapshot hex has an odd number of hex digits")
    try:
        return bytes.fromhex(compact)
    except ValueError as error:
        raise LianLiWirelessError("snapshot hex contains invalid bytes") from error


def _motherboard_pwm_from_args(args: argparse.Namespace) -> int:
    if getattr(args, "snapshot_hex", None):
        decoded = extract_motherboard_pwm(_decode_snapshot_hex(args.snapshot_hex))
        if decoded is None:
            raise LianLiWirelessError("snapshot does not contain a valid motherboard PWM value")
        return decoded
    value = getattr(args, "motherboard_pwm", None)
    if value is None:
        raise LianLiWirelessError("pass --motherboard-pwm or --snapshot-hex")
    return max(0, min(255, int(value)))


def _safe_motherboard_pwm(value: int | None, minimum: int) -> int:
    if value is None:
        raise LianLiWirelessError("live receiver snapshot has no motherboard PWM value")
    if value < minimum:
        raise LianLiWirelessError(
            f"refusing motherboard PWM {value}; pass a value >= --min-pwm {minimum}"
        )
    return max(0, min(255, int(value)))


def _emit_payload(payload: dict[str, object], save_json: Path | None = None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if save_json is not None:
        save_json.parent.mkdir(parents=True, exist_ok=True)
        save_json.write_text(text + "\n", encoding="utf-8")
    print(text)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validation_step(
    name: str,
    output_dir: Path,
    operation,  # noqa: ANN001
) -> dict[str, object]:
    try:
        payload = operation()
        status = "ok"
        error = ""
    except Exception as exc:  # noqa: BLE001
        payload = {"operation": name, "error": str(exc)}
        status = "error"
        error = str(exc)
    path = output_dir / f"{name}.json"
    _write_json(path, payload)
    return {"name": name, "status": status, "path": str(path), "error": error}


def _live_list_payload() -> dict[str, object]:
    backend = create_pyusb_backend()
    snapshot = backend.list_devices()
    return {
        "operation": "live-list",
        "device_count": snapshot.device_count,
        "motherboard_pwm": snapshot.motherboard_pwm,
        "devices": [
            _wireless_device_payload(device)
            for device in snapshot.devices
        ],
    }


def _live_master_payload(channel: int = 8) -> dict[str, object]:
    backend = create_pyusb_backend()
    result = backend.query_master_mac(channel=channel)
    return {
        "operation": "live-master",
        "channel": channel,
        "master_mac": result[0] if result else None,
        "detected": result is not None,
    }


def _live_lcd_info_payload() -> dict[str, object]:
    backend = create_pyusb_lcd_backend()
    return {
        "operation": "live-lcd-info",
        "mode": "both",
        "handshake": backend.handshake(),
        "firmware": backend.firmware_version(),
    }


def _validate_readonly_payload(output_dir: Path, *, include_lcd: bool = True) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    steps: list[dict[str, object]] = []
    steps.append(_validation_step("scan", output_dir, _scan_payload))
    steps.append(_validation_step("live-list", output_dir, _live_list_payload))
    steps.append(_validation_step("live-master", output_dir, _live_master_payload))

    if include_lcd:
        steps.append(_validation_step("live-lcd-info", output_dir, _live_lcd_info_payload))

    return {
        "operation": "validate-readonly",
        "output_dir": str(output_dir),
        "step_count": len(steps),
        "ok_count": sum(1 for step in steps if step["status"] == "ok"),
        "error_count": sum(1 for step in steps if step["status"] == "error"),
        "steps": steps,
    }


def _receiver_validation_bundle_payload(args: argparse.Namespace) -> dict[str, object]:
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    experiment_dir = args.experiment_dir or (output_dir / "experiments")
    readonly_dir = output_dir / "readonly"
    steps: list[dict[str, object]] = []
    steps.append(_validation_step("scan", output_dir, _scan_payload))
    steps.append(_validation_step("readiness", output_dir, lambda: usb_capture_readiness(sys_root=args.sys_root)))
    steps.append(_validation_step("live-list", output_dir, _live_list_payload))
    steps.append(_validation_step("live-master", output_dir, _live_master_payload))
    steps.append(
        _validation_step(
            "validate-readonly",
            output_dir,
            lambda: _validate_readonly_payload(readonly_dir, include_lcd=not args.skip_lcd),
        )
    )
    steps.append(
        _validation_step(
            "preflight",
            output_dir,
            lambda: linux_control_preflight_report(
                args.capture_dir,
                sys_root=args.sys_root,
                dev_root=args.dev_root,
                version=args.version,
                capture_base=args.capture_base,
                experiment_dir=experiment_dir,
                led_count=args.led_count,
                rainbow_frames=args.rainbow_frames,
                interval_ms=args.interval_ms,
                effect_index=args.effect_index,
            ),
        )
    )
    steps.append(
        _validation_step(
            "write-gate",
            output_dir,
            lambda: linux_control_write_gate_report(
                args.capture_dir,
                sys_root=args.sys_root,
                dev_root=args.dev_root,
                version=args.version,
                capture_base=args.capture_base,
                experiment_dir=experiment_dir,
                led_count=args.led_count,
                rainbow_frames=args.rainbow_frames,
                interval_ms=args.interval_ms,
                effect_index=args.effect_index,
            ),
        )
    )
    write_gate_path = output_dir / "write-gate.json"
    write_gate_payload: dict[str, object] = {}
    if write_gate_path.exists():
        try:
            parsed = json.loads(write_gate_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                write_gate_payload = parsed
        except (OSError, json.JSONDecodeError):
            write_gate_payload = {}
    ready_for_write = bool(write_gate_payload.get("allows_any_guarded_write"))
    next_command = str(write_gate_payload.get("next_command") or "")
    return {
        "operation": "receiver-validation-bundle",
        "output_dir": str(output_dir),
        "capture_dir": str(args.capture_dir),
        "experiment_dir": str(experiment_dir),
        "step_count": len(steps),
        "ok_count": sum(1 for step in steps if step["status"] == "ok"),
        "error_count": sum(1 for step in steps if step["status"] == "error"),
        "ready_for_guarded_write": ready_for_write,
        "write_gate_status": str(write_gate_payload.get("status") or ""),
        "write_gate_next_command": next_command,
        "steps": steps,
        "next_steps": _receiver_validation_next_steps(ready_for_write, next_command),
    }


def _receiver_validation_next_steps(ready_for_write: bool, next_command: str) -> list[str]:
    if ready_for_write:
        return [
            "Choose one target MAC from live-list.json.",
            "Run safe-pwm-experiment with --confirm WRITE-LIANLI and a conservative PWM value.",
            "Record visible fan response and keep before/write/after JSON logs.",
        ]
    steps = [
        "Do not run live writes yet.",
        "Inspect write-gate.json and preflight.json.",
    ]
    if next_command:
        steps.append(f"Run next gate command: {next_command}")
    else:
        steps.append("Collect missing official Windows USBPcap evidence and rerun packet compare.")
    return steps


def _safe_pwm_experiment_payload(args: argparse.Namespace) -> dict[str, object]:
    _require_write_confirmation(args)
    pwm_values = _safe_pwm_tuple(args.pwm, args.min_pwm)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    backend = create_pyusb_backend()

    before = backend.list_devices()
    before_payload = {
        "operation": "live-list-before",
        "device_count": before.device_count,
        "devices": [_wireless_device_payload(device) for device in before.devices],
    }
    before_path = output_dir / "live-list-before.json"
    _write_json(before_path, before_payload)

    target = _find_target(before, args.mac)
    packets_written = backend.send_pwm(target, pwm_values)
    after = backend.list_devices()
    after_payload = {
        "operation": "live-list-after",
        "device_count": after.device_count,
        "devices": [_wireless_device_payload(device) for device in after.devices],
    }
    after_path = output_dir / "live-list-after.json"
    _write_json(after_path, after_payload)

    write_payload = {
        "operation": "live-pwm",
        "target": target.mac,
        "pwm_values": list(pwm_values),
        "packets_written": packets_written,
        "before": _wireless_device_payload(target),
        "after": {
            "device_count": after.device_count,
            "devices": [_wireless_device_payload(device) for device in after.devices],
        },
    }
    write_path = output_dir / "live-pwm.json"
    _write_json(write_path, write_payload)

    analysis_payload = analyze_live_log(write_path)
    analysis_path = output_dir / "analyze-live-pwm.json"
    _write_json(analysis_path, analysis_payload)
    summary_payload = summarize_experiment_dir(output_dir)
    summary_path = output_dir / "summary.json"
    _write_json(summary_path, summary_payload)

    return {
        "operation": "safe-pwm-experiment",
        "target": target.mac,
        "pwm_values": list(pwm_values),
        "output_dir": str(output_dir),
        "packets_written": packets_written,
        "likely_effective": analysis_payload["likely_effective"],
        "steps": [
            {"name": "before", "path": str(before_path)},
            {"name": "write", "path": str(write_path)},
            {"name": "after", "path": str(after_path)},
            {"name": "analysis", "path": str(analysis_path)},
            {"name": "summary", "path": str(summary_path)},
        ],
        "analysis": analysis_payload,
        "summary": summary_payload,
    }


def _safe_rgb_experiment_payload(args: argparse.Namespace) -> dict[str, object]:
    _require_write_confirmation(args)
    color = _parse_rgb(args.color)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    backend = create_pyusb_backend()

    before = backend.list_devices()
    before_payload = {
        "operation": "live-list-before",
        "device_count": before.device_count,
        "devices": [_wireless_device_payload(device) for device in before.devices],
    }
    before_path = output_dir / "live-list-before.json"
    _write_json(before_path, before_payload)

    target = _find_target(before, args.mac)
    led_count = _rgb_led_count(args, target)
    packets_written = backend.send_static_rgb(
        target,
        color,
        effect_index=args.effect_index,
        led_count=args.led_count,
    )
    after = backend.list_devices()
    after_payload = {
        "operation": "live-list-after",
        "device_count": after.device_count,
        "devices": [_wireless_device_payload(device) for device in after.devices],
    }
    after_path = output_dir / "live-list-after.json"
    _write_json(after_path, after_payload)

    write_payload = {
        "operation": "live-rgb",
        "target": target.mac,
        "color": list(color),
        "led_count": led_count,
        "effect_index": args.effect_index,
        "packets_written": packets_written,
        "before": _wireless_device_payload(target),
        "after": {
            "device_count": after.device_count,
            "devices": [_wireless_device_payload(device) for device in after.devices],
        },
    }
    write_path = output_dir / "live-rgb.json"
    _write_json(write_path, write_payload)

    analysis_payload = analyze_live_log(write_path)
    visual_confirmation_required = not bool(analysis_payload["snapshot_changed"])
    analysis_payload["visual_confirmation_required"] = visual_confirmation_required
    analysis_path = output_dir / "analyze-live-rgb.json"
    _write_json(analysis_path, analysis_payload)
    summary_payload = summarize_experiment_dir(output_dir)
    summary_path = output_dir / "summary.json"
    _write_json(summary_path, summary_payload)

    return {
        "operation": "safe-rgb-experiment",
        "target": target.mac,
        "color": list(color),
        "led_count": led_count,
        "effect_index": args.effect_index,
        "output_dir": str(output_dir),
        "packets_written": packets_written,
        "likely_effective": analysis_payload["likely_effective"],
        "visual_confirmation_required": visual_confirmation_required,
        "steps": [
            {"name": "before", "path": str(before_path)},
            {"name": "write", "path": str(write_path)},
            {"name": "after", "path": str(after_path)},
            {"name": "analysis", "path": str(analysis_path)},
            {"name": "summary", "path": str(summary_path)},
        ],
        "analysis": analysis_payload,
        "summary": summary_payload,
    }


def _safe_rainbow_experiment_payload(args: argparse.Namespace) -> dict[str, object]:
    _require_write_confirmation(args)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    backend = create_pyusb_backend()

    before = backend.list_devices()
    before_payload = {
        "operation": "live-list-before",
        "device_count": before.device_count,
        "devices": [_wireless_device_payload(device) for device in before.devices],
    }
    before_path = output_dir / "live-list-before.json"
    _write_json(before_path, before_payload)

    target = _find_target(before, args.mac)
    led_count = _rainbow_led_count(args, target)
    packets_written = backend.send_rainbow_rgb(
        target,
        frame_count=args.frame_count,
        interval_ms=args.interval_ms,
        effect_index=args.effect_index,
        led_count=args.led_count,
    )
    after = backend.list_devices()
    after_payload = {
        "operation": "live-list-after",
        "device_count": after.device_count,
        "devices": [_wireless_device_payload(device) for device in after.devices],
    }
    after_path = output_dir / "live-list-after.json"
    _write_json(after_path, after_payload)

    write_payload = {
        "operation": "live-rainbow",
        "target": target.mac,
        "frame_count": args.frame_count,
        "interval_ms": args.interval_ms,
        "led_count": led_count,
        "effect_index": args.effect_index,
        "packets_written": packets_written,
        "before": _wireless_device_payload(target),
        "after": {
            "device_count": after.device_count,
            "devices": [_wireless_device_payload(device) for device in after.devices],
        },
    }
    write_path = output_dir / "live-rainbow.json"
    _write_json(write_path, write_payload)

    analysis_payload = analyze_live_log(write_path)
    visual_confirmation_required = not bool(analysis_payload["snapshot_changed"])
    analysis_payload["visual_confirmation_required"] = visual_confirmation_required
    analysis_path = output_dir / "analyze-live-rainbow.json"
    _write_json(analysis_path, analysis_payload)
    summary_payload = summarize_experiment_dir(output_dir)
    summary_path = output_dir / "summary.json"
    _write_json(summary_path, summary_payload)

    return {
        "operation": "safe-rainbow-experiment",
        "target": target.mac,
        "frame_count": args.frame_count,
        "interval_ms": args.interval_ms,
        "led_count": led_count,
        "effect_index": args.effect_index,
        "output_dir": str(output_dir),
        "packets_written": packets_written,
        "likely_effective": analysis_payload["likely_effective"],
        "visual_confirmation_required": visual_confirmation_required,
        "steps": [
            {"name": "before", "path": str(before_path)},
            {"name": "write", "path": str(write_path)},
            {"name": "after", "path": str(after_path)},
            {"name": "analysis", "path": str(analysis_path)},
            {"name": "summary", "path": str(summary_path)},
        ],
        "analysis": analysis_payload,
        "summary": summary_payload,
    }


def _safe_sync_experiment_payload(args: argparse.Namespace) -> dict[str, object]:
    _require_write_confirmation(args)
    if args.disable and args.fallback_pwm < args.min_fallback_pwm:
        raise LianLiWirelessError(
            f"refusing fallback PWM {args.fallback_pwm}; pass a value >= "
            f"--min-fallback-pwm {args.min_fallback_pwm}"
        )
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    backend = create_pyusb_backend()

    before = backend.list_devices()
    before_payload = {
        "operation": "live-list-before",
        "device_count": before.device_count,
        "devices": [_wireless_device_payload(device) for device in before.devices],
    }
    before_path = output_dir / "live-list-before.json"
    _write_json(before_path, before_payload)

    target = _find_target(before, args.mac)
    enabled = not args.disable
    packets_written = backend.send_motherboard_pwm_sync(
        target,
        enable=enabled,
        fallback_pwm=args.fallback_pwm,
    )
    after = backend.list_devices()
    after_payload = {
        "operation": "live-list-after",
        "device_count": after.device_count,
        "devices": [_wireless_device_payload(device) for device in after.devices],
    }
    after_path = output_dir / "live-list-after.json"
    _write_json(after_path, after_payload)

    expected_pwm_values = [6, 6, 6, 6] if enabled else [args.fallback_pwm] * 4
    write_payload = {
        "operation": "live-pwm-sync",
        "target": target.mac,
        "enabled": enabled,
        "fallback_pwm": args.fallback_pwm,
        "expected_pwm_values": expected_pwm_values,
        "packets_written": packets_written,
        "before": _wireless_device_payload(target),
        "after": {
            "device_count": after.device_count,
            "devices": [_wireless_device_payload(device) for device in after.devices],
        },
    }
    write_path = output_dir / "live-pwm-sync.json"
    _write_json(write_path, write_payload)

    analysis_payload = analyze_live_log(write_path)
    analysis_path = output_dir / "analyze-live-pwm-sync.json"
    _write_json(analysis_path, analysis_payload)
    summary_payload = summarize_experiment_dir(output_dir)
    summary_path = output_dir / "summary.json"
    _write_json(summary_path, summary_payload)

    return {
        "operation": "safe-sync-experiment",
        "target": target.mac,
        "enabled": enabled,
        "fallback_pwm": args.fallback_pwm,
        "expected_pwm_values": expected_pwm_values,
        "output_dir": str(output_dir),
        "packets_written": packets_written,
        "likely_effective": analysis_payload["likely_effective"],
        "steps": [
            {"name": "before", "path": str(before_path)},
            {"name": "write", "path": str(write_path)},
            {"name": "after", "path": str(after_path)},
            {"name": "analysis", "path": str(analysis_path)},
            {"name": "summary", "path": str(summary_path)},
        ],
        "analysis": analysis_payload,
        "summary": summary_payload,
    }


def _safe_pwm_mirror_experiment_payload(args: argparse.Namespace) -> dict[str, object]:
    _require_write_confirmation(args)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    backend = create_pyusb_backend()

    before = backend.list_devices()
    motherboard_pwm = _safe_motherboard_pwm(before.motherboard_pwm, args.min_pwm)
    before_payload = {
        "operation": "live-list-before",
        "device_count": before.device_count,
        "motherboard_pwm": before.motherboard_pwm,
        "devices": [_wireless_device_payload(device) for device in before.devices],
    }
    before_path = output_dir / "live-list-before.json"
    _write_json(before_path, before_payload)

    target = _find_target(before, args.mac)
    pwm_values = (motherboard_pwm, motherboard_pwm, motherboard_pwm, motherboard_pwm)
    packets_written = backend.send_motherboard_pwm_mirror(target, motherboard_pwm)

    after = backend.list_devices()
    after_payload = {
        "operation": "live-list-after",
        "device_count": after.device_count,
        "motherboard_pwm": after.motherboard_pwm,
        "devices": [_wireless_device_payload(device) for device in after.devices],
    }
    after_path = output_dir / "live-list-after.json"
    _write_json(after_path, after_payload)

    write_payload = {
        "operation": "live-pwm-mirror",
        "target": target.mac,
        "motherboard_pwm": motherboard_pwm,
        "pwm_values": list(pwm_values),
        "packets_written": packets_written,
        "before": _wireless_device_payload(target),
        "after": after_payload,
    }
    write_path = output_dir / "live-pwm-mirror.json"
    _write_json(write_path, write_payload)

    analysis_payload = analyze_live_log(write_path)
    analysis_path = output_dir / "analyze-live-pwm-mirror.json"
    _write_json(analysis_path, analysis_payload)
    summary_payload = summarize_experiment_dir(output_dir)
    summary_path = output_dir / "summary.json"
    _write_json(summary_path, summary_payload)

    return {
        "operation": "safe-pwm-mirror-experiment",
        "target": target.mac,
        "motherboard_pwm": motherboard_pwm,
        "pwm_values": list(pwm_values),
        "output_dir": str(output_dir),
        "packets_written": packets_written,
        "likely_effective": analysis_payload["likely_effective"],
        "steps": [
            {"name": "before", "path": str(before_path)},
            {"name": "write", "path": str(write_path)},
            {"name": "after", "path": str(after_path)},
            {"name": "analysis", "path": str(analysis_path)},
            {"name": "summary", "path": str(summary_path)},
        ],
        "analysis": analysis_payload,
        "summary": summary_payload,
    }


def _safe_bind_experiment_payload(args: argparse.Namespace) -> dict[str, object]:
    _require_write_confirmation(args)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    backend = create_pyusb_backend()

    before = backend.list_devices()
    before_payload = {
        "operation": "live-list-before",
        "device_count": before.device_count,
        "devices": [_wireless_device_payload(device) for device in before.devices],
    }
    before_path = output_dir / "live-list-before.json"
    _write_json(before_path, before_payload)

    target = _find_target(before, args.mac)
    if target.is_bound:
        raise LianLiWirelessError(f"receiver is already bound: {target.mac}")
    master_mac = args.master_mac
    master_query_channel = args.channel or target.channel or 8
    if master_mac is None:
        master_query = backend.query_master_mac(channel=master_query_channel)
        if master_query is None:
            raise LianLiWirelessError("unable to infer master MAC; pass --master-mac")
        master_mac = master_query[0]
    packets_written = backend.send_bind(
        target,
        master_mac=master_mac,
        rx_type=args.rx_type,
        channel=args.channel,
    )
    after = backend.list_devices()
    after_payload = {
        "operation": "live-list-after",
        "device_count": after.device_count,
        "devices": [_wireless_device_payload(device) for device in after.devices],
    }
    after_path = output_dir / "live-list-after.json"
    _write_json(after_path, after_payload)

    write_payload = {
        "operation": "live-bind",
        "target": target.mac,
        "master_mac": master_mac,
        "rx_type": args.rx_type,
        "channel": args.channel,
        "master_query_channel": master_query_channel,
        "packets_written": packets_written,
        "before": _wireless_device_payload(target),
        "after": {
            "device_count": after.device_count,
            "devices": [_wireless_device_payload(device) for device in after.devices],
        },
    }
    write_path = output_dir / "live-bind.json"
    _write_json(write_path, write_payload)

    analysis_payload = analyze_live_log(write_path)
    analysis_path = output_dir / "analyze-live-bind.json"
    _write_json(analysis_path, analysis_payload)
    summary_payload = summarize_experiment_dir(output_dir)
    summary_path = output_dir / "summary.json"
    _write_json(summary_path, summary_payload)

    return {
        "operation": "safe-bind-experiment",
        "target": target.mac,
        "master_mac": master_mac,
        "rx_type": args.rx_type,
        "channel": args.channel,
        "master_query_channel": master_query_channel,
        "output_dir": str(output_dir),
        "packets_written": packets_written,
        "likely_effective": analysis_payload["likely_effective"],
        "steps": [
            {"name": "before", "path": str(before_path)},
            {"name": "write", "path": str(write_path)},
            {"name": "after", "path": str(after_path)},
            {"name": "analysis", "path": str(analysis_path)},
            {"name": "summary", "path": str(summary_path)},
        ],
        "analysis": analysis_payload,
        "summary": summary_payload,
    }


def _safe_unbind_experiment_payload(args: argparse.Namespace) -> dict[str, object]:
    _require_write_confirmation(args)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    backend = create_pyusb_backend()

    before = backend.list_devices()
    before_payload = {
        "operation": "live-list-before",
        "device_count": before.device_count,
        "devices": [_wireless_device_payload(device) for device in before.devices],
    }
    before_path = output_dir / "live-list-before.json"
    _write_json(before_path, before_payload)

    target = _find_target(before, args.mac)
    if not target.is_bound:
        raise LianLiWirelessError(f"receiver is already unbound: {target.mac}")
    packets_written = backend.send_unbind(target, channel=args.channel)
    after = backend.list_devices()
    after_payload = {
        "operation": "live-list-after",
        "device_count": after.device_count,
        "devices": [_wireless_device_payload(device) for device in after.devices],
    }
    after_path = output_dir / "live-list-after.json"
    _write_json(after_path, after_payload)

    write_payload = {
        "operation": "live-unbind",
        "target": target.mac,
        "channel": args.channel,
        "packets_written": packets_written,
        "before": _wireless_device_payload(target),
        "after": {
            "device_count": after.device_count,
            "devices": [_wireless_device_payload(device) for device in after.devices],
        },
    }
    write_path = output_dir / "live-unbind.json"
    _write_json(write_path, write_payload)

    analysis_payload = analyze_live_log(write_path)
    analysis_path = output_dir / "analyze-live-unbind.json"
    _write_json(analysis_path, analysis_payload)
    summary_payload = summarize_experiment_dir(output_dir)
    summary_path = output_dir / "summary.json"
    _write_json(summary_path, summary_payload)

    return {
        "operation": "safe-unbind-experiment",
        "target": target.mac,
        "channel": args.channel,
        "output_dir": str(output_dir),
        "packets_written": packets_written,
        "likely_effective": analysis_payload["likely_effective"],
        "steps": [
            {"name": "before", "path": str(before_path)},
            {"name": "write", "path": str(write_path)},
            {"name": "after", "path": str(after_path)},
            {"name": "analysis", "path": str(analysis_path)},
            {"name": "summary", "path": str(summary_path)},
        ],
        "analysis": analysis_payload,
        "summary": summary_payload,
    }


def _lcd_payload(args: argparse.Namespace) -> dict[str, object]:
    command = wireless_lcd_command_from_name(args.lcd_command)
    single_byte = args.value
    if args.lcd_command == "brightness" and single_byte is None:
        single_byte = 50
    elif args.lcd_command == "rotate" and single_byte is None:
        single_byte = 0
    payload = b""
    if args.lcd_command == "push-jpg":
        payload_size = max(0, int(args.payload_size))
        payload = bytes(payload_size)
        single_byte = None
    packet = build_wireless_lcd_packet(
        command,
        payload=payload,
        single_byte=single_byte,
        timestamp_ms=args.timestamp_ms,
        encrypt=args.encrypt,
    )
    return {
        "operation": "dry-run-lcd",
        "command": args.lcd_command,
        "command_id": packet.command,
        "timestamp_ms": packet.timestamp_ms,
        "payload_size": len(packet.payload),
        "packet_length": packet.packet_length,
        "header_plaintext_size": len(packet.plaintext_header),
        "header_plaintext_first64_hex": packet.plaintext_header[:64].hex(),
        "encryption_available": wireless_lcd_encryption_available(),
        "encrypted": packet.encryption_available,
        "encrypted_header_size": len(packet.encrypted_header or b""),
        "encrypted_header_first64_hex": (packet.encrypted_header or b"")[:64].hex(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command or "scan"
    if command == "scan":
        payload = _scan_payload()
    elif command == "udev-rules":
        payload = {
            "path": "/etc/udev/rules.d/70-lianli-wireless.rules",
            "rules": list(UDEV_RULES),
        }
    elif command == "analyze-log":
        payload = analyze_live_log(args.path)
    elif command == "diff-snapshots":
        payload = diff_snapshot_files(args.before, args.after)
    elif command == "summarize-experiments":
        payload = summarize_experiment_dir(args.path)
    elif command == "analyze-artifact":
        payload = analyze_artifact_file(args.path)
    elif command == "analyze-artifact-tree":
        payload = analyze_artifact_tree(args.path, max_file_size=args.max_file_size)
    elif command == "diff-artifacts":
        payload = diff_artifact_files(args.before, args.after, block_size=args.block_size)
    elif command == "artifact-evidence-matrix":
        payload = artifact_evidence_matrix(args.path)
    elif command == "extract-hid-js":
        payload = extract_hid_js_commands(args.path, max_file_size=args.max_file_size)
    elif command == "extract-wireless-js":
        payload = extract_wireless_js_clues(args.path, max_file_size=args.max_file_size)
    elif command == "analyze-changelog":
        payload = analyze_lconnect_changelog(args.source, top=args.top)
    elif command == "analyze-capture":
        payload = analyze_capture_file(args.path)
    elif command == "capture-replay-plan":
        payload = capture_replay_plan_file(args.path)
    elif command == "capture-protocol-report":
        payload = capture_protocol_report_file(args.path)
    elif command == "capture-timeline-report":
        payload = capture_timeline_report_file(args.path)
    elif command == "capture-transport-report":
        payload = capture_transport_report_file(args.path)
    elif command == "protocol-signatures":
        payload = protocol_signature_catalog(
            led_count=args.led_count,
            rainbow_frames=args.rainbow_frames,
            interval_ms=args.interval_ms,
            effect_index=args.effect_index,
        )
    elif command == "capture-signature-match":
        payload = capture_signature_match_file(
            args.path,
            led_count=args.led_count,
            rainbow_frames=args.rainbow_frames,
            interval_ms=args.interval_ms,
            effect_index=args.effect_index,
        )
    elif command == "capture-triage-report":
        payload = capture_triage_report_file(
            args.path,
            led_count=args.led_count,
            rainbow_frames=args.rainbow_frames,
            interval_ms=args.interval_ms,
            effect_index=args.effect_index,
        )
    elif command == "summarize-captures":
        payload = summarize_capture_dir(args.path)
    elif command == "capture-set-report":
        payload = capture_set_report(
            args.path,
            version=args.version,
            capture_base=args.capture_base,
            experiment_dir=args.experiment_dir,
            led_count=args.led_count,
            rainbow_frames=args.rainbow_frames,
            interval_ms=args.interval_ms,
            effect_index=args.effect_index,
        )
    elif command == "linux-interface-contract":
        payload = linux_interface_contract_report(
            args.path,
            version=args.version,
            capture_base=args.capture_base,
            experiment_dir=args.experiment_dir,
            led_count=args.led_count,
            rainbow_frames=args.rainbow_frames,
            interval_ms=args.interval_ms,
            effect_index=args.effect_index,
        )
    elif command == "linux-control-manifest":
        payload = linux_control_manifest_report(
            args.path,
            version=args.version,
            capture_base=args.capture_base,
            experiment_dir=args.experiment_dir,
            led_count=args.led_count,
            rainbow_frames=args.rainbow_frames,
            interval_ms=args.interval_ms,
            effect_index=args.effect_index,
        )
    elif command == "linux-control-preflight":
        payload = linux_control_preflight_report(
            args.path,
            sys_root=args.sys_root,
            dev_root=args.dev_root,
            version=args.version,
            capture_base=args.capture_base,
            experiment_dir=args.experiment_dir,
            led_count=args.led_count,
            rainbow_frames=args.rainbow_frames,
            interval_ms=args.interval_ms,
            effect_index=args.effect_index,
        )
    elif command == "linux-control-action-plan":
        payload = linux_control_action_plan_report(
            args.path,
            sys_root=args.sys_root,
            dev_root=args.dev_root,
            version=args.version,
            capture_base=args.capture_base,
            experiment_dir=args.experiment_dir,
            led_count=args.led_count,
            rainbow_frames=args.rainbow_frames,
            interval_ms=args.interval_ms,
            effect_index=args.effect_index,
        )
    elif command == "linux-control-write-gate":
        payload = linux_control_write_gate_report(
            args.path,
            sys_root=args.sys_root,
            dev_root=args.dev_root,
            version=args.version,
            capture_base=args.capture_base,
            experiment_dir=args.experiment_dir,
            led_count=args.led_count,
            rainbow_frames=args.rainbow_frames,
            interval_ms=args.interval_ms,
            effect_index=args.effect_index,
        )
    elif command == "linux-control-target-registry":
        payload = linux_control_target_registry_report(
            args.path,
            sys_root=args.sys_root,
            dev_root=args.dev_root,
            version=args.version,
            capture_base=args.capture_base,
            experiment_dir=args.experiment_dir,
            led_count=args.led_count,
            rainbow_frames=args.rainbow_frames,
            interval_ms=args.interval_ms,
            effect_index=args.effect_index,
        )
    elif command == "linux-control-packet-preview":
        payload = linux_control_packet_preview_report(
            args.path,
            control_operation=args.control_operation,
            target_id=args.target_id,
            sys_root=args.sys_root,
            dev_root=args.dev_root,
            version=args.version,
            capture_base=args.capture_base,
            experiment_dir=args.experiment_dir,
            pwm_values=args.pwm_values
            or ((args.pwm, args.pwm, args.pwm, args.pwm) if args.pwm is not None else None),
            motherboard_pwm=args.motherboard_pwm,
            color=_parse_rgb(args.color) if args.color is not None else None,
            led_count=args.led_count,
            frame_count=args.frame_count,
            interval_ms=args.interval_ms,
            effect_index=args.effect_index,
        )
    elif command == "linux-control-packet-compare":
        payload = linux_control_packet_compare_report(
            args.path,
            args.observed_capture,
            control_operation=args.control_operation,
            target_id=args.target_id,
            sys_root=args.sys_root,
            dev_root=args.dev_root,
            version=args.version,
            capture_base=args.capture_base,
            experiment_dir=args.experiment_dir,
            pwm_values=args.pwm_values
            or ((args.pwm, args.pwm, args.pwm, args.pwm) if args.pwm is not None else None),
            motherboard_pwm=args.motherboard_pwm,
            color=_parse_rgb(args.color) if args.color is not None else None,
            led_count=args.led_count,
            frame_count=args.frame_count,
            interval_ms=args.interval_ms,
            effect_index=args.effect_index,
        )
    elif command == "compare-capture":
        expected_packets = _expected_compare_packets(args)
        payload = compare_capture_file(
            args.path,
            expected_packets,
            expected_source=args.expected_operation,
        )
        payload["expected_operation"] = args.expected_operation
        if hasattr(args, "expected_motherboard_pwm"):
            payload["expected_motherboard_pwm"] = args.expected_motherboard_pwm
    elif command == "windows-capture-plan":
        payload = windows_capture_plan(
            version=args.version,
            installer=args.installer,
            capture_base=args.capture_base,
            environment=args.environment,
        )
    elif command == "usb-capture-readiness":
        payload = usb_capture_readiness(sys_root=args.sys_root)
    elif command == "live-list":
        payload = _live_list_payload()
    elif command == "validate-readonly":
        payload = _validate_readonly_payload(args.output_dir, include_lcd=not args.skip_lcd)
    elif command == "receiver-validation-bundle":
        payload = _receiver_validation_bundle_payload(args)
    elif command == "live-master":
        payload = _live_master_payload(args.channel)
    elif command == "dry-run-master-query":
        request = build_master_query_request(args.channel)
        payload = {
            "operation": "dry-run-master-query",
            "channel": args.channel,
            "request_hex": request.hex(),
        }
    elif command == "dry-run-pwm":
        target = _fake_target(args)
        pwm_values = _pwm_values_from_args(args)
        packets = LianLiWirelessBackend().build_pwm_packets(target, pwm_values)
        payload = {
            "operation": "dry-run-pwm",
            "target": target.mac,
            "pwm_values": list(pwm_values),
            **_packet_summary(packets),
        }
    elif command == "dry-run-pwm-sync":
        target = _fake_target(args)
        packets = LianLiWirelessBackend().build_motherboard_pwm_sync_packets(
            target,
            enable=not args.disable,
            fallback_pwm=args.fallback_pwm,
        )
        payload = {
            "operation": "dry-run-pwm-sync",
            "target": target.mac,
            "enabled": not args.disable,
            **_packet_summary(packets),
        }
    elif command == "dry-run-pwm-mirror":
        target = _fake_target(args)
        motherboard_pwm = _safe_motherboard_pwm(_motherboard_pwm_from_args(args), args.min_pwm)
        pwm_values = (motherboard_pwm, motherboard_pwm, motherboard_pwm, motherboard_pwm)
        packets = LianLiWirelessBackend().build_motherboard_pwm_mirror_packets(
            target,
            motherboard_pwm,
        )
        payload = {
            "operation": "dry-run-pwm-mirror",
            "target": target.mac,
            "motherboard_pwm": motherboard_pwm,
            "pwm_values": list(pwm_values),
            **_packet_summary(packets),
        }
    elif command == "dry-run-bind":
        target = _fake_target(args, master_mac="00:00:00:00:00:00", rx_type=0)
        packets = LianLiWirelessBackend().build_bind_packets(
            target,
            master_mac=args.master_mac,
            rx_type=args.rx_type,
            channel=args.channel,
        )
        payload = {
            "operation": "dry-run-bind",
            "target": target.mac,
            "master_mac": args.master_mac,
            "rx_type": args.rx_type,
            **_packet_summary(packets),
        }
    elif command == "dry-run-unbind":
        target = _fake_target(args)
        packets = LianLiWirelessBackend().build_unbind_packets(
            target,
            channel=args.channel,
        )
        payload = {
            "operation": "dry-run-unbind",
            "target": target.mac,
            **_packet_summary(packets),
        }
    elif command == "dry-run-rgb":
        target = _fake_target(args)
        color = _parse_rgb(args.color)
        packets = LianLiWirelessBackend().build_static_rgb_packets(
            target,
            color,
            effect_index=args.effect_index,
            led_count=args.led_count,
        )
        led_count = _rgb_led_count(args, target)
        payload = {
            "operation": "dry-run-rgb",
            "target": target.mac,
            "color": color,
            "led_count": led_count,
            **_packet_summary(packets),
        }
    elif command == "dry-run-rainbow":
        target = _fake_target(args)
        frame_count = int(args.frame_count)
        interval_ms = int(args.interval_ms)
        packets = LianLiWirelessBackend().build_rainbow_rgb_packets(
            target,
            frame_count=frame_count,
            interval_ms=interval_ms,
            effect_index=args.effect_index,
            led_count=args.led_count,
        )
        led_count = args.led_count if args.led_count is not None else infer_led_count(target)
        payload = {
            "operation": "dry-run-rainbow",
            "target": target.mac,
            "led_count": led_count,
            "frame_count": frame_count,
            "interval_ms": interval_ms,
            **_packet_summary(packets),
        }
    elif command == "dry-run-lcd":
        payload = _lcd_payload(args)
    elif command == "live-lcd-info":
        backend = create_pyusb_lcd_backend()
        payload = {"operation": "live-lcd-info", "mode": args.mode}
        if args.mode in ("handshake", "both"):
            payload["handshake"] = backend.handshake()
        if args.mode in ("firmware", "both"):
            payload["firmware"] = backend.firmware_version()
    elif command == "live-lcd-control":
        _require_write_confirmation(args)
        if args.brightness is None and args.rotation is None:
            raise LianLiWirelessError("pass --brightness and/or --rotation")
        backend = create_pyusb_lcd_backend()
        applied: dict[str, object] = {}
        if args.brightness is not None:
            applied["brightness"] = {
                "value": max(0, min(100, int(args.brightness))),
                "bytes_written": backend.set_brightness(args.brightness),
            }
        if args.rotation is not None:
            applied["rotation"] = {
                "degrees": args.rotation,
                "bytes_written": backend.set_rotation(args.rotation),
            }
        payload = {"operation": "live-lcd-control", "applied": applied}
    elif command == "live-pwm":
        _require_write_confirmation(args)
        pwm_values = _safe_pwm_tuple(args.pwm, args.min_pwm)
        backend = create_pyusb_backend()
        before = backend.list_devices()
        target = _find_target(before, args.mac)
        packets_written = backend.send_pwm(target, pwm_values)
        after_payload = _snapshot_payload_from_backend(backend)
        payload = {
            "operation": "live-pwm",
            "target": target.mac,
            "pwm_values": list(pwm_values),
            "packets_written": packets_written,
            "before": _wireless_device_payload(target),
            "after": after_payload,
        }
    elif command == "safe-pwm-experiment":
        payload = _safe_pwm_experiment_payload(args)
    elif command == "live-pwm-sync":
        _require_write_confirmation(args)
        if args.disable and args.fallback_pwm < args.min_fallback_pwm:
            raise LianLiWirelessError(
                f"refusing fallback PWM {args.fallback_pwm}; pass a value >= "
                f"--min-fallback-pwm {args.min_fallback_pwm}"
            )
        backend = create_pyusb_backend()
        before = backend.list_devices()
        target = _find_target(before, args.mac)
        packets_written = backend.send_motherboard_pwm_sync(
            target,
            enable=not args.disable,
            fallback_pwm=args.fallback_pwm,
        )
        after_payload = _snapshot_payload_from_backend(backend)
        payload = {
            "operation": "live-pwm-sync",
            "target": target.mac,
            "enabled": not args.disable,
            "fallback_pwm": args.fallback_pwm,
            "packets_written": packets_written,
            "before": _wireless_device_payload(target),
            "after": after_payload,
        }
    elif command == "live-pwm-mirror":
        _require_write_confirmation(args)
        backend = create_pyusb_backend()
        before = backend.list_devices()
        motherboard_pwm = _safe_motherboard_pwm(before.motherboard_pwm, args.min_pwm)
        target = _find_target(before, args.mac)
        packets_written = backend.send_motherboard_pwm_mirror(target, motherboard_pwm)
        pwm_values = [motherboard_pwm] * 4
        after_payload = _snapshot_payload_from_backend(backend)
        payload = {
            "operation": "live-pwm-mirror",
            "target": target.mac,
            "motherboard_pwm": motherboard_pwm,
            "pwm_values": pwm_values,
            "packets_written": packets_written,
            "before": _wireless_device_payload(target),
            "after": after_payload,
        }
    elif command == "safe-sync-experiment":
        payload = _safe_sync_experiment_payload(args)
    elif command == "safe-pwm-mirror-experiment":
        payload = _safe_pwm_mirror_experiment_payload(args)
    elif command == "live-rgb":
        _require_write_confirmation(args)
        color = _parse_rgb(args.color)
        backend = create_pyusb_backend()
        before = backend.list_devices()
        target = _find_target(before, args.mac)
        led_count = _rgb_led_count(args, target)
        packets_written = backend.send_static_rgb(
            target,
            color,
            effect_index=args.effect_index,
            led_count=args.led_count,
        )
        after_payload = _snapshot_payload_from_backend(backend)
        payload = {
            "operation": "live-rgb",
            "target": target.mac,
            "color": list(color),
            "led_count": led_count,
            "effect_index": args.effect_index,
            "packets_written": packets_written,
            "before": _wireless_device_payload(target),
            "after": after_payload,
        }
    elif command == "live-rainbow":
        _require_write_confirmation(args)
        backend = create_pyusb_backend()
        before = backend.list_devices()
        target = _find_target(before, args.mac)
        led_count = _rainbow_led_count(args, target)
        packets_written = backend.send_rainbow_rgb(
            target,
            frame_count=args.frame_count,
            interval_ms=args.interval_ms,
            effect_index=args.effect_index,
            led_count=args.led_count,
        )
        after_payload = _snapshot_payload_from_backend(backend)
        payload = {
            "operation": "live-rainbow",
            "target": target.mac,
            "frame_count": args.frame_count,
            "interval_ms": args.interval_ms,
            "led_count": led_count,
            "effect_index": args.effect_index,
            "packets_written": packets_written,
            "before": _wireless_device_payload(target),
            "after": after_payload,
        }
    elif command == "safe-rgb-experiment":
        payload = _safe_rgb_experiment_payload(args)
    elif command == "safe-rainbow-experiment":
        payload = _safe_rainbow_experiment_payload(args)
    elif command == "safe-bind-experiment":
        payload = _safe_bind_experiment_payload(args)
    elif command == "safe-unbind-experiment":
        payload = _safe_unbind_experiment_payload(args)
    elif command == "live-bind":
        _require_write_confirmation(args)
        backend = create_pyusb_backend()
        before = backend.list_devices()
        target = _find_target(before, args.mac)
        if target.is_bound:
            raise LianLiWirelessError(f"receiver is already bound: {target.mac}")
        master_mac = args.master_mac
        if master_mac is None:
            master_query = backend.query_master_mac(channel=args.channel or target.channel or 8)
            if master_query is None:
                raise LianLiWirelessError(
                    "unable to infer master MAC; pass --master-mac"
                )
            master_mac = master_query[0]
        packets_written = backend.send_bind(
            target,
            master_mac=master_mac,
            rx_type=args.rx_type,
            channel=args.channel,
        )
        after_payload = _snapshot_payload_from_backend(backend)
        payload = {
            "operation": "live-bind",
            "target": target.mac,
            "master_mac": master_mac,
            "rx_type": args.rx_type,
            "packets_written": packets_written,
            "before": _wireless_device_payload(target),
            "after": after_payload,
        }
    elif command == "live-unbind":
        _require_write_confirmation(args)
        backend = create_pyusb_backend()
        before = backend.list_devices()
        target = _find_target(before, args.mac)
        if not target.is_bound:
            raise LianLiWirelessError(f"receiver is already unbound: {target.mac}")
        packets_written = backend.send_unbind(target, channel=args.channel)
        after_payload = _snapshot_payload_from_backend(backend)
        payload = {
            "operation": "live-unbind",
            "target": target.mac,
            "packets_written": packets_written,
            "before": _wireless_device_payload(target),
            "after": after_payload,
        }
    else:
        parser.error(f"unknown command: {command}")
        return 2
    _emit_payload(payload, args.save_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
