from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from usb9_lcd.lianli.wireless import LianLiWirelessError


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
    operation_stats: dict[str, dict[str, Any]] = {}
    field_change_counts: dict[str, int] = {}

    for file_path in files:
        try:
            payload = load_json_file(file_path)
        except LianLiWirelessError as error:
            invalid_files.append({"path": str(file_path), "error": str(error)})
            continue

        operation = str(payload.get("operation") or "unknown")
        for mac in devices_by_mac(payload):
            receiver_macs.add(mac)
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
        "invalid_files": invalid_files,
        "live_logs": summaries,
    }


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
    if operation == "live-pwm":
        expected_pwm = _int_list(payload.get("pwm_values"))
        if expected_pwm is None:
            return _expectation_unavailable("live-pwm log has no pwm_values expectation.")
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
    if operation == "live-rgb":
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
    if operation == "live-rgb":
        return ["RGB writes may not change receiver snapshot fields; confirm visually."]
    return ["No receiver snapshot fields changed; command effect is unverified."]
