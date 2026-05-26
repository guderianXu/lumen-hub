from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
from typing import Any

from usb9_lcd.lianli.wireless import LianLiWirelessError


LIVE_SNAPSHOT_OPERATIONS = {"live-list", "live-list-before", "live-list-after"}


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LianLiWirelessError(f"unable to read JSON log {path}: {error}") from error
    if not isinstance(payload, dict):
        raise LianLiWirelessError(f"JSON log must contain an object: {path}")
    return payload


def analyze_live_log(path: Path) -> dict[str, Any]:
    payload = load_json_file(path)
    before = payload.get("before")
    after = payload.get("after")
    target_mac = payload.get("target")
    if not isinstance(before, dict) or not isinstance(after, dict) or not isinstance(target_mac, str):
        raise LianLiWirelessError(
            "live log must contain before object, after snapshot, and target MAC"
        )
    after_target = devices_by_mac(after).get(target_mac.lower())
    changes = device_field_changes(before, after_target or {})
    snapshot_changed = bool(changes)
    expectation = expected_effect(payload, after_target)
    expected_matched = expectation.get("matched")
    likely_effective = bool(expected_matched) if expectation["available"] else snapshot_changed
    return {
        "operation": "analyze-log",
        "path": str(path),
        "source_operation": payload.get("operation"),
        "target": target_mac,
        "packets_written": payload.get("packets_written"),
        "target_found_after": after_target is not None,
        "snapshot_changed": snapshot_changed,
        "likely_effective": likely_effective,
        "expected_effect": expectation,
        "changes": changes,
        "notes": analysis_notes(payload, changes, after_target is not None, expectation),
    }


def diff_snapshot_files(before_path: Path, after_path: Path) -> dict[str, Any]:
    before_payload = load_json_file(before_path)
    after_payload = load_json_file(after_path)
    diff = diff_device_maps(
        devices_by_mac(before_payload),
        devices_by_mac(after_payload),
    )
    return {
        "operation": "diff-snapshots",
        "before_path": str(before_path),
        "after_path": str(after_path),
        **diff,
    }


def summarize_experiment_dir(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise LianLiWirelessError(f"experiment path does not exist: {path}")
    files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    summaries: list[dict[str, Any]] = []
    invalid_files: list[dict[str, str]] = []
    receiver_macs: set[str] = set()
    validation_errors: list[dict[str, str]] = []
    validation_runs: list[dict[str, Any]] = []
    receiver_validation_bundles: list[dict[str, Any]] = []
    safe_experiment_runs: list[dict[str, Any]] = []
    packet_compare_runs: list[dict[str, Any]] = []
    live_snapshot_runs: list[dict[str, Any]] = []
    live_snapshot_devices: dict[str, dict[str, Any]] = {}
    operation_stats: dict[str, dict[str, Any]] = {}
    field_change_counts: dict[str, int] = {}

    for file_index, file_path in enumerate(files):
        try:
            payload = load_json_file(file_path)
        except LianLiWirelessError as error:
            invalid_files.append({"path": str(file_path), "error": str(error)})
            continue

        operation = str(payload.get("operation") or "unknown")
        for mac in devices_by_mac(payload):
            receiver_macs.add(mac)
        if operation in LIVE_SNAPSHOT_OPERATIONS:
            snapshot = live_snapshot_summary(file_path, payload, index=file_index)
            live_snapshot_runs.append(snapshot)
            for mac, device in snapshot.get("devices", {}).items():
                if not isinstance(device, dict):
                    continue
                existing = live_snapshot_devices.get(mac)
                if existing is None or _live_snapshot_device_sort_key(device) >= _live_snapshot_device_sort_key(existing):
                    merged = dict(existing or {})
                    merged.update(device)
                    if not device.get("raw_hex") and existing and existing.get("raw_hex"):
                        merged["raw_hex"] = existing["raw_hex"]
                    live_snapshot_devices[mac] = merged
        if operation == "validate-readonly":
            validation_runs.append(validation_run_summary(file_path, payload))
        if operation == "receiver-validation-bundle":
            receiver_validation_bundles.append(receiver_validation_bundle_summary(file_path, payload))
        if operation.startswith("safe-") and operation.endswith("-experiment"):
            safe_experiment_runs.append(safe_experiment_summary(file_path, payload))
        if operation == "linux-control-packet-compare":
            packet_compare_runs.append(packet_compare_summary(file_path, payload))
        if payload.get("status") == "error":
            validation_errors.append(
                {
                    "path": str(file_path),
                    "operation": operation,
                    "error": str(payload.get("error") or ""),
                }
            )

        before = payload.get("before")
        after = payload.get("after")
        target = payload.get("target")
        if not isinstance(before, dict) or not isinstance(after, dict) or not isinstance(target, str):
            continue
        after_target = devices_by_mac(after).get(target.lower())
        if after_target is not None:
            receiver_macs.add(target.lower())
        changes = device_field_changes(before, after_target or {})
        expectation = expected_effect(payload, after_target)
        for change in changes:
            field = str(change["field"])
            field_change_counts[field] = field_change_counts.get(field, 0) + 1

        stat = operation_stats.setdefault(
            operation,
            {
                "count": 0,
                "changed_count": 0,
                "unchanged_count": 0,
                "target_missing_count": 0,
                "expected_matched_count": 0,
                "expected_mismatch_count": 0,
                "expected_unavailable_count": 0,
                "fields": {},
            },
        )
        stat["count"] += 1
        if after_target is None:
            stat["target_missing_count"] += 1
        elif changes:
            stat["changed_count"] += 1
        else:
            stat["unchanged_count"] += 1
        if expectation["available"]:
            if expectation["matched"]:
                stat["expected_matched_count"] += 1
            else:
                stat["expected_mismatch_count"] += 1
        else:
            stat["expected_unavailable_count"] += 1
        fields = stat["fields"]
        for change in changes:
            field = str(change["field"])
            fields[field] = fields.get(field, 0) + 1

        summaries.append(
            {
                "path": str(file_path),
                "operation": operation,
                "target": target,
                "target_found_after": after_target is not None,
                "changed": bool(changes),
                "changed_fields": [change["field"] for change in changes],
                "expected_effect_available": expectation["available"],
                "expected_effect_matched": expectation["matched"],
                "packets_written": payload.get("packets_written"),
            }
        )

    live_snapshot_context = {
        "status": "available" if live_snapshot_devices else "missing",
        "run_count": len(live_snapshot_runs),
        "device_count": len(live_snapshot_devices),
        "devices": dict(sorted(live_snapshot_devices.items())),
    }
    hardware_validation = hardware_validation_summary(
        validation_runs,
        safe_experiment_runs,
        validation_errors,
        receiver_validation_bundles=receiver_validation_bundles,
    )
    return {
        "operation": "summarize-experiments",
        "path": str(path),
        "json_file_count": len(files),
        "analyzed_live_log_count": len(summaries),
        "invalid_file_count": len(invalid_files),
        "receiver_macs": sorted(receiver_macs),
        "operation_stats": operation_stats,
        "field_change_counts": dict(sorted(field_change_counts.items())),
        "validation_errors": validation_errors,
        "validation_runs": validation_runs,
        "receiver_validation_bundles": receiver_validation_bundles,
        "safe_experiment_runs": safe_experiment_runs,
        "packet_compare_runs": packet_compare_runs,
        "live_snapshot_runs": live_snapshot_runs,
        "live_snapshot_devices": dict(sorted(live_snapshot_devices.items())),
        "live_snapshot_context": live_snapshot_context,
        "hardware_validation": hardware_validation,
        "receiver_control_next_action": receiver_control_next_action(
            path,
            hardware_validation,
            live_snapshot_devices,
            receiver_validation_bundles,
        ),
        "invalid_files": invalid_files,
        "live_logs": summaries,
    }


def validation_run_summary(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    steps = payload.get("steps")
    step_items = steps if isinstance(steps, list) else []
    return {
        "path": str(path),
        "output_dir": str(payload.get("output_dir") or ""),
        "step_count": int(payload.get("step_count", len(step_items)) or 0),
        "ok_count": int(payload.get("ok_count", 0) or 0),
        "error_count": int(payload.get("error_count", 0) or 0),
        "status": "ok" if int(payload.get("error_count", 0) or 0) == 0 else "error",
        "steps": [
            {
                "name": str(step.get("name") or ""),
                "status": str(step.get("status") or ""),
                "error": str(step.get("error") or ""),
            }
            for step in step_items
            if isinstance(step, dict)
        ],
    }


def receiver_validation_bundle_summary(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    steps = payload.get("steps")
    step_items = steps if isinstance(steps, list) else []
    error_count = _int_value(
        payload.get("error_count"),
        default=sum(1 for step in step_items if isinstance(step, dict) and step.get("status") == "error"),
    )
    ready_for_write = bool(payload.get("ready_for_guarded_write"))
    write_gate_status = str(payload.get("write_gate_status") or "")
    return {
        "path": str(path),
        "output_dir": str(payload.get("output_dir") or ""),
        "capture_dir": str(payload.get("capture_dir") or ""),
        "experiment_dir": str(payload.get("experiment_dir") or ""),
        "step_count": _int_value(payload.get("step_count"), default=len(step_items)),
        "ok_count": _int_value(payload.get("ok_count"), default=0),
        "error_count": error_count,
        "status": receiver_validation_bundle_status(
            error_count,
            ready_for_write=ready_for_write,
            write_gate_status=write_gate_status,
        ),
        "ready_for_guarded_write": ready_for_write,
        "write_gate_status": write_gate_status,
        "write_gate_next_command": str(payload.get("write_gate_next_command") or ""),
        "next_steps": _string_list(payload.get("next_steps")),
        "steps": [
            {
                "name": str(step.get("name") or ""),
                "status": str(step.get("status") or ""),
                "path": str(step.get("path") or ""),
                "error": str(step.get("error") or ""),
            }
            for step in step_items
            if isinstance(step, dict)
        ],
    }


def receiver_validation_bundle_status(
    error_count: int,
    *,
    ready_for_write: bool,
    write_gate_status: str,
) -> str:
    if error_count:
        return "errors"
    if ready_for_write:
        return "write-gate-ready"
    if write_gate_status:
        return "write-gate-blocked"
    return "readonly-collected"


def safe_experiment_summary(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    operation_stats = summary.get("operation_stats") if isinstance(summary.get("operation_stats"), dict) else {}
    target = payload.get("target")
    if isinstance(target, str) and target:
        target = target.lower()
    else:
        target = ""
    return {
        "path": str(path),
        "operation": str(payload.get("operation") or ""),
        "target": target,
        "output_dir": str(payload.get("output_dir") or ""),
        "packets_written": payload.get("packets_written"),
        "likely_effective": payload.get("likely_effective"),
        "visual_confirmation_required": payload.get("visual_confirmation_required"),
        "operation_stats": operation_stats,
    }


def packet_compare_summary(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    diagnostics = payload.get("match_diagnostics")
    if not isinstance(diagnostics, dict):
        diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    write_gate = payload.get("write_gate") if isinstance(payload.get("write_gate"), dict) else {}
    target_state = payload.get("target_state") if isinstance(payload.get("target_state"), dict) else {}
    return {
        "path": str(path),
        "schema_version": str(payload.get("schema_version") or ""),
        "packet_preview_schema_version": str(payload.get("packet_preview_schema_version") or ""),
        "control_operation": str(payload.get("control_operation") or ""),
        "target_id": str(payload.get("target_id") or ""),
        "observed_capture": str(payload.get("observed_capture") or ""),
        "status": str(payload.get("status") or ""),
        "matched": bool(payload.get("matched")),
        "exact_match": bool(payload.get("exact_match")),
        "semantic_match": bool(payload.get("semantic_match")),
        "diagnostics_status": str(diagnostics.get("status") or ""),
        "write_gate_status": str(write_gate.get("status") or ""),
        "allows_guarded_write": bool(write_gate.get("allows_guarded_write")),
        "target_state_status": str(target_state.get("status") or ""),
        "target_state_missing_packet_fields": _string_list(target_state.get("missing_packet_fields")),
        "target_state_placeholder_fields": _string_list(target_state.get("placeholder_fields")),
        "target_state_snapshot_metadata_available": bool(target_state.get("snapshot_metadata_available")),
        "target_state_snapshot_state_available": bool(target_state.get("snapshot_state_available")),
        "target_state_raw_hex_available": bool(target_state.get("raw_hex_available")),
        "target_state_live_snapshot_refresh_required": bool(target_state.get("live_snapshot_refresh_required")),
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int))]


def _int_value(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def live_snapshot_summary(path: Path, payload: dict[str, Any], *, index: int = 0) -> dict[str, Any]:
    devices = {
        mac: live_snapshot_device_summary(path, device, operation=str(payload.get("operation") or ""), index=index)
        for mac, device in devices_by_mac(payload).items()
    }
    return {
        "path": str(path),
        "operation": str(payload.get("operation") or ""),
        "index": index,
        "device_count": int(payload.get("device_count", len(devices)) or 0),
        "motherboard_pwm": payload.get("motherboard_pwm"),
        "device_macs": sorted(devices),
        "devices": dict(sorted(devices.items())),
    }


def live_snapshot_device_summary(
    path: Path,
    device: dict[str, Any],
    *,
    operation: str,
    index: int,
) -> dict[str, Any]:
    fields = (
        "mac",
        "master_mac",
        "is_bound",
        "channel",
        "rx_type",
        "device_type",
        "fan_count",
        "pwm_values",
        "fan_rpm",
        "command_sequence",
        "raw_hex",
    )
    result = {
        key: device[key]
        for key in fields
        if key in device and device[key] not in (None, "", [])
    }
    mac = str(result.get("mac") or "").lower()
    result["mac"] = mac
    result["snapshot_source"] = operation
    result["snapshot_path"] = str(path)
    result["snapshot_index"] = index
    return result


def _live_snapshot_device_sort_key(device: dict[str, Any]) -> tuple[int, int, str]:
    index = device.get("snapshot_index")
    raw_quality = 1 if device.get("raw_hex") else 0
    return int(index if isinstance(index, int) else -1), raw_quality, str(device.get("snapshot_path") or "")


def receiver_control_next_action(
    path: Path,
    hardware_validation: dict[str, Any],
    live_snapshot_devices: dict[str, dict[str, Any]],
    receiver_validation_bundles: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = receiver_safe_pwm_candidates(path, live_snapshot_devices)
    ready_candidates = [item for item in candidates if item["status"] == "ready"]
    hardware_status = str(hardware_validation.get("status") or "")
    recommended_commands: list[str] = []

    if hardware_status == "errors":
        status = "validation-errors"
        reason = "Inspect validation_errors and receiver_validation_bundles before any write."
        can_run_safe_pwm = False
    elif hardware_status == "readonly-and-write-observed":
        status = "write-validation-already-observed"
        reason = "A guarded write experiment is already represented in this log directory."
        can_run_safe_pwm = False
    elif hardware_status == "readonly-and-write-gate-ready":
        if ready_candidates:
            status = "ready-for-single-target-safe-pwm"
            reason = "Write-gate passed and at least one bound receiver MAC is available from live-list."
            can_run_safe_pwm = True
            recommended_commands.append(str(ready_candidates[0]["safe_pwm_command"]))
            recommended_commands.append(_tool_command("summarize-experiments", str(path)))
        else:
            status = "needs-bound-target"
            reason = "Write-gate passed, but no bound receiver MAC is available from live-list."
            can_run_safe_pwm = False
            recommended_commands.append(_tool_command("--save-json", str(path / "live-list.json"), "live-list"))
    elif not receiver_validation_bundles:
        status = "needs-receiver-validation-bundle"
        reason = "No receiver-validation-bundle log was found in this directory."
        can_run_safe_pwm = False
        recommended_commands.append(
            _tool_command(
                "--save-json",
                str(path / "receiver-validation-bundle.json"),
                "receiver-validation-bundle",
                "--output-dir",
                str(path),
                "--capture-dir",
                ".cache/lianli",
            )
        )
    elif not live_snapshot_devices:
        status = "needs-live-list"
        reason = "Receiver validation exists, but no live-list snapshot with receiver MACs was found."
        can_run_safe_pwm = False
        recommended_commands.append(_tool_command("--save-json", str(path / "live-list.json"), "live-list"))
    else:
        status = "needs-write-gate"
        reason = "Do not write until linux-control-write-gate reports write-enabled."
        can_run_safe_pwm = False
        next_gate_command = _latest_write_gate_next_command(receiver_validation_bundles)
        if next_gate_command:
            recommended_commands.append(next_gate_command)

    return {
        "status": status,
        "reason": reason,
        "can_run_safe_pwm": can_run_safe_pwm,
        "candidate_count": len(candidates),
        "ready_candidate_count": len(ready_candidates),
        "hardware_status": hardware_status,
        "recommended_commands": recommended_commands,
        "candidates": candidates,
    }


def receiver_safe_pwm_candidates(path: Path, live_snapshot_devices: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for mac, device in sorted(live_snapshot_devices.items()):
        candidate = receiver_safe_pwm_candidate(path, mac, device)
        if candidate:
            candidates.append(candidate)
    return candidates


def receiver_safe_pwm_candidate(path: Path, mac: str, device: dict[str, Any]) -> dict[str, Any]:
    normalized_mac = str(mac or device.get("mac") or "").lower()
    is_bound = device.get("is_bound")
    if not normalized_mac:
        status = "missing-mac"
    elif is_bound is False:
        status = "unbound"
    else:
        status = "ready"
    output_dir = path / "experiments" / f"safe-pwm-{_path_token(normalized_mac)}"
    argv = [
        "safe-pwm-experiment",
        "--mac",
        normalized_mac,
        "--pwm",
        "120",
        "--output-dir",
        str(output_dir),
        "--confirm",
        "WRITE-LIANLI",
    ]
    return {
        "mac": normalized_mac,
        "status": status,
        "is_bound": is_bound,
        "channel": device.get("channel"),
        "rx_type": device.get("rx_type"),
        "device_type": device.get("device_type"),
        "fan_count": device.get("fan_count"),
        "pwm_values": device.get("pwm_values"),
        "fan_rpm": device.get("fan_rpm"),
        "snapshot_source": str(device.get("snapshot_source") or ""),
        "snapshot_path": str(device.get("snapshot_path") or ""),
        "raw_hex_available": bool(device.get("raw_hex")),
        "safe_pwm_argv": argv,
        "safe_pwm_command": _tool_command(*argv),
    }


def _latest_write_gate_next_command(receiver_validation_bundles: list[dict[str, Any]]) -> str:
    for bundle in reversed(receiver_validation_bundles):
        command = str(bundle.get("write_gate_next_command") or "")
        if command:
            return command
    return ""


def _path_token(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-") or "unknown"


def _tool_command(*parts: str) -> str:
    return "python tools/lianli_wireless_probe.py " + " ".join(shlex.quote(str(part)) for part in parts)


def hardware_validation_summary(
    validation_runs: list[dict[str, Any]],
    safe_experiment_runs: list[dict[str, Any]],
    validation_errors: list[dict[str, str]],
    *,
    receiver_validation_bundles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    receiver_validation_bundles = receiver_validation_bundles or []
    safe_effective = [
        item
        for item in safe_experiment_runs
        if item.get("likely_effective") is True or item.get("visual_confirmation_required") is True
    ]
    failed_validation_runs = [item for item in validation_runs if item.get("status") != "ok"]
    failed_receiver_bundles = [
        item
        for item in receiver_validation_bundles
        if item.get("status") == "errors" or _int_value(item.get("error_count"), default=0) > 0
    ]
    write_gate_ready = [
        item
        for item in receiver_validation_bundles
        if item.get("ready_for_guarded_write") is True
    ]
    if failed_validation_runs or validation_errors or failed_receiver_bundles:
        status = "errors"
    elif (validation_runs or receiver_validation_bundles) and safe_effective:
        status = "readonly-and-write-observed"
    elif write_gate_ready:
        status = "readonly-and-write-gate-ready"
    elif validation_runs:
        status = "readonly-observed"
    elif receiver_validation_bundles:
        status = "readonly-observed"
    elif safe_effective:
        status = "write-observed"
    else:
        status = "no-hardware-validation-logs"
    result = {
        "status": status,
        "validation_run_count": len(validation_runs),
        "validation_error_count": len(validation_errors) + len(failed_validation_runs) + len(failed_receiver_bundles),
        "safe_experiment_count": len(safe_experiment_runs),
        "safe_effective_count": len(safe_effective),
        "targets": sorted(
            {
                str(item.get("target") or "")
                for item in safe_experiment_runs
                if item.get("target")
            }
        ),
    }
    if receiver_validation_bundles:
        result.update(
            {
                "receiver_validation_bundle_count": len(receiver_validation_bundles),
                "receiver_validation_bundle_error_count": len(failed_receiver_bundles),
                "write_gate_ready_count": len(write_gate_ready),
                "write_gate_statuses": sorted(
                    {
                        str(item.get("write_gate_status") or "")
                        for item in receiver_validation_bundles
                        if item.get("write_gate_status")
                    }
                ),
            }
        )
    return result


def devices_by_mac(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    devices = payload.get("devices", [])
    if isinstance(devices, dict):
        devices = list(devices.values())
    if not isinstance(devices, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in devices:
        if not isinstance(item, dict):
            continue
        mac = item.get("mac")
        if isinstance(mac, str) and mac:
            result[mac.lower()] = item
    return result


def device_field_changes(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[dict[str, Any]]:
    watched_fields = (
        "master_mac",
        "is_bound",
        "channel",
        "rx_type",
        "device_type",
        "fan_count",
        "pwm_values",
        "fan_rpm",
        "command_sequence",
    )
    changes = []
    for field in watched_fields:
        before_value = before.get(field)
        after_value = after.get(field)
        if before_value != after_value:
            changes.append(
                {
                    "field": field,
                    "before": before_value,
                    "after": after_value,
                }
            )
    return changes


def diff_device_maps(
    before_devices: dict[str, dict[str, Any]],
    after_devices: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    before_keys = set(before_devices)
    after_keys = set(after_devices)
    changed = []
    unchanged = []
    for mac in sorted(before_keys & after_keys):
        changes = device_field_changes(before_devices[mac], after_devices[mac])
        if changes:
            changed.append({"mac": mac, "changes": changes})
        else:
            unchanged.append(mac)
    return {
        "added": sorted(after_keys - before_keys),
        "removed": sorted(before_keys - after_keys),
        "changed": changed,
        "unchanged": unchanged,
        "summary": {
            "before_count": len(before_devices),
            "after_count": len(after_devices),
            "added_count": len(after_keys - before_keys),
            "removed_count": len(before_keys - after_keys),
            "changed_count": len(changed),
            "unchanged_count": len(unchanged),
        },
    }


def expected_effect(
    payload: dict[str, Any],
    after_target: dict[str, Any] | None,
) -> dict[str, Any]:
    operation = str(payload.get("operation") or "")
    if operation in {"live-pwm", "live-pwm-mirror"}:
        expected_pwm = _int_list(payload.get("pwm_values"))
        if expected_pwm is None:
            return _expectation_unavailable(f"{operation} log has no pwm_values expectation.")
        return _expectation_result(
            operation,
            [_field_check("pwm_values", expected_pwm, _after_value(after_target, "pwm_values"))],
            after_target is not None,
        )
    if operation == "live-pwm-sync":
        expected_pwm = _int_list(payload.get("expected_pwm_values"))
        if expected_pwm is None:
            enabled = bool(payload.get("enabled", True))
            fallback_pwm = payload.get("fallback_pwm")
            if enabled:
                expected_pwm = [6, 6, 6, 6]
            elif isinstance(fallback_pwm, int):
                expected_pwm = [fallback_pwm] * 4
        if expected_pwm is None:
            return _expectation_unavailable("live-pwm-sync log has no expected PWM tuple.")
        return _expectation_result(
            operation,
            [_field_check("pwm_values", expected_pwm, _after_value(after_target, "pwm_values"))],
            after_target is not None,
        )
    if operation == "live-bind":
        checks = [
            _field_check("is_bound", True, _after_value(after_target, "is_bound")),
        ]
        master_mac = payload.get("master_mac")
        if isinstance(master_mac, str) and master_mac:
            checks.append(_field_check("master_mac", master_mac, _after_value(after_target, "master_mac"), casefold=True))
        rx_type = payload.get("rx_type")
        if isinstance(rx_type, int):
            checks.append(_field_check("rx_type", rx_type, _after_value(after_target, "rx_type")))
        channel = payload.get("channel")
        if isinstance(channel, int):
            checks.append(_field_check("channel", channel, _after_value(after_target, "channel")))
        return _expectation_result(operation, checks, after_target is not None)
    if operation == "live-unbind":
        return _expectation_result(
            operation,
            [
                _field_check("is_bound", False, _after_value(after_target, "is_bound")),
                _field_check("master_mac", "00:00:00:00:00:00", _after_value(after_target, "master_mac"), casefold=True),
                _field_check("rx_type", 0, _after_value(after_target, "rx_type")),
            ],
            after_target is not None,
        )
    if operation in {"live-rgb", "live-rainbow"}:
        return _expectation_unavailable("RGB writes may not change receiver snapshot fields; confirm visually.")
    return _expectation_unavailable(f"No structured expected-effect check for operation: {operation or 'unknown'}.")


def _expectation_result(
    operation: str,
    checks: list[dict[str, Any]],
    target_found_after: bool,
) -> dict[str, Any]:
    if not target_found_after:
        for check in checks:
            check["matched"] = False
    return {
        "available": True,
        "operation": operation,
        "matched": target_found_after and all(bool(check["matched"]) for check in checks),
        "checks": checks,
    }


def _expectation_unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "matched": None,
        "checks": [],
        "reason": reason,
    }


def _field_check(
    field: str,
    expected: Any,
    actual: Any,
    *,
    casefold: bool = False,
) -> dict[str, Any]:
    if casefold and isinstance(expected, str) and isinstance(actual, str):
        matched = expected.lower() == actual.lower()
    else:
        matched = expected == actual
    return {
        "field": field,
        "expected": expected,
        "actual": actual,
        "matched": matched,
    }


def _after_value(after_target: dict[str, Any] | None, field: str) -> Any:
    if after_target is None:
        return None
    return after_target.get(field)


def _int_list(value: Any) -> list[int] | None:
    if not isinstance(value, list):
        return None
    if not all(isinstance(item, int) for item in value):
        return None
    return value


def analysis_notes(
    payload: dict[str, Any],
    changes: list[dict[str, Any]],
    target_found_after: bool,
    expectation: dict[str, Any],
) -> list[str]:
    if not target_found_after:
        return ["Target MAC was not present in the after snapshot."]
    if expectation["available"] and expectation["matched"]:
        return ["After snapshot matches the expected command effect."]
    if expectation["available"] and not expectation["matched"]:
        if changes:
            return ["After snapshot changed but does not match the expected command effect."]
        return ["After snapshot does not match the expected command effect."]
    if changes:
        return ["Snapshot fields changed after the command."]
    operation = str(payload.get("operation") or "")
    if operation in {"live-rgb", "live-rainbow"}:
        return ["RGB writes may not change receiver snapshot fields; confirm visually."]
    return ["No receiver snapshot fields changed; command effect is unverified."]
