#!/usr/bin/env python3
from __future__ import annotations

import json
import argparse
from dataclasses import asdict, dataclass
from pathlib import Path

from usb9_lcd.lianli import KNOWN_USB_DEVICES, UDEV_RULES, scan_known_usb_devices
from usb9_lcd.lianli.analysis import analyze_live_log, diff_snapshot_files, summarize_experiment_dir
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
    rgb.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)

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

    live_rgb = subparsers.add_parser(
        "live-rgb",
        help="Guarded live static RGB write to one bound receiver",
    )
    live_rgb.add_argument("--mac", required=True)
    live_rgb.add_argument("--color", required=True, help="RGB triple, e.g. 255,0,0")
    live_rgb.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)
    _add_write_confirm_arg(live_rgb)

    safe_rgb = subparsers.add_parser(
        "safe-rgb-experiment",
        help="Run a guarded single-MAC RGB experiment and save before/write/after analysis logs",
    )
    safe_rgb.add_argument("--mac", required=True)
    safe_rgb.add_argument("--color", required=True, help="RGB triple, e.g. 255,0,0")
    safe_rgb.add_argument("--effect-index", type=lambda value: int(value, 0), default=1)
    safe_rgb.add_argument("--output-dir", type=Path, default=Path(".cache/lianli/rgb-experiment"))
    _add_write_confirm_arg(safe_rgb)

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
        pwm_values=(0, 0, 0, 0),
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
        "devices": [_wireless_device_payload(device) for device in snapshot.devices],
    }


def _safe_pwm_tuple(value: int, minimum: int) -> tuple[int, int, int, int]:
    if value < minimum:
        raise LianLiWirelessError(
            f"refusing PWM {value}; pass a value >= --min-pwm {minimum}"
        )
    pwm = max(0, min(255, int(value)))
    return pwm, pwm, pwm, pwm


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


def _validate_readonly_payload(output_dir: Path, *, include_lcd: bool = True) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    steps: list[dict[str, object]] = []
    steps.append(_validation_step("scan", output_dir, _scan_payload))

    def live_list() -> dict[str, object]:
        backend = create_pyusb_backend()
        snapshot = backend.list_devices()
        return {
            "operation": "live-list",
            "device_count": snapshot.device_count,
            "devices": [_wireless_device_payload(device) for device in snapshot.devices],
        }

    def live_master() -> dict[str, object]:
        backend = create_pyusb_backend()
        result = backend.query_master_mac(channel=8)
        return {
            "operation": "live-master",
            "channel": 8,
            "master_mac": result[0] if result else None,
            "detected": result is not None,
        }

    steps.append(_validation_step("live-list", output_dir, live_list))
    steps.append(_validation_step("live-master", output_dir, live_master))

    if include_lcd:
        def live_lcd_info() -> dict[str, object]:
            backend = create_pyusb_lcd_backend()
            return {
                "operation": "live-lcd-info",
                "mode": "both",
                "handshake": backend.handshake(),
                "firmware": backend.firmware_version(),
            }

        steps.append(_validation_step("live-lcd-info", output_dir, live_lcd_info))

    return {
        "operation": "validate-readonly",
        "output_dir": str(output_dir),
        "step_count": len(steps),
        "ok_count": sum(1 for step in steps if step["status"] == "ok"),
        "error_count": sum(1 for step in steps if step["status"] == "error"),
        "steps": steps,
    }


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
    packets_written = backend.send_static_rgb(
        target,
        color,
        effect_index=args.effect_index,
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
    elif command == "live-list":
        backend = create_pyusb_backend()
        snapshot = backend.list_devices()
        payload = {
            "operation": "live-list",
            "device_count": snapshot.device_count,
            "devices": [
                _wireless_device_payload(device)
                for device in snapshot.devices
            ],
        }
    elif command == "validate-readonly":
        payload = _validate_readonly_payload(args.output_dir, include_lcd=not args.skip_lcd)
    elif command == "live-master":
        backend = create_pyusb_backend()
        result = backend.query_master_mac(channel=args.channel)
        payload = {
            "operation": "live-master",
            "channel": args.channel,
            "master_mac": result[0] if result else None,
            "detected": result is not None,
        }
    elif command == "dry-run-master-query":
        request = build_master_query_request(args.channel)
        payload = {
            "operation": "dry-run-master-query",
            "channel": args.channel,
            "request_hex": request.hex(),
        }
    elif command == "dry-run-pwm":
        target = _fake_target(args)
        packets = LianLiWirelessBackend().build_pwm_packets(target, [args.pwm])
        payload = {"operation": "dry-run-pwm", "target": target.mac, **_packet_summary(packets)}
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
        )
        payload = {
            "operation": "dry-run-rgb",
            "target": target.mac,
            "color": color,
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
    elif command == "safe-sync-experiment":
        payload = _safe_sync_experiment_payload(args)
    elif command == "live-rgb":
        _require_write_confirmation(args)
        color = _parse_rgb(args.color)
        backend = create_pyusb_backend()
        before = backend.list_devices()
        target = _find_target(before, args.mac)
        packets_written = backend.send_static_rgb(
            target,
            color,
            effect_index=args.effect_index,
        )
        after_payload = _snapshot_payload_from_backend(backend)
        payload = {
            "operation": "live-rgb",
            "target": target.mac,
            "color": list(color),
            "packets_written": packets_written,
            "before": _wireless_device_payload(target),
            "after": after_payload,
        }
    elif command == "safe-rgb-experiment":
        payload = _safe_rgb_experiment_payload(args)
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
