from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shlex
from typing import Any

from usb9_lcd.lianli.wireless import LianLiWirelessError


LIVE_SNAPSHOT_OPERATIONS = {"live-list", "live-list-before", "live-list-after"}
RECEIVER_EVIDENCE_REQUIRED_FILES = (
    "receiver-validation-bundle.json",
    "summary.json",
    "scan.json",
    "readiness.json",
    "live-list.json",
    "live-master.json",
    "validate-readonly.json",
    "readonly/scan.json",
    "readonly/live-list.json",
    "readonly/live-master.json",
    "preflight.json",
    "write-gate.json",
)
RECEIVER_EVIDENCE_WRITE_FILE_NAMES = (
    "live-list-before.json",
    "live-pwm.json",
    "live-list-after.json",
    "analyze-live-pwm.json",
    "summary.json",
)
RECEIVER_EVIDENCE_WRITE_SPECS = {
    "live-pwm": {
        "operation": "live-pwm",
        "label": "direct-pwm",
        "write_file": "live-pwm.json",
        "analysis_file": "analyze-live-pwm.json",
        "dir_prefixes": ("safe-pwm-",),
    },
    "live-pwm-sync": {
        "operation": "live-pwm-sync",
        "label": "motherboard-pwm-sync",
        "write_file": "live-pwm-sync.json",
        "analysis_file": "analyze-live-pwm-sync.json",
        "dir_prefixes": ("safe-sync-", "safe-pwm-sync-"),
    },
    "live-pwm-mirror": {
        "operation": "live-pwm-mirror",
        "label": "motherboard-pwm-mirror",
        "write_file": "live-pwm-mirror.json",
        "analysis_file": "analyze-live-pwm-mirror.json",
        "dir_prefixes": ("safe-pwm-mirror-",),
    },
    "live-rgb": {
        "operation": "live-rgb",
        "label": "static-rgb",
        "write_file": "live-rgb.json",
        "analysis_file": "analyze-live-rgb.json",
        "dir_prefixes": ("safe-rgb-",),
    },
    "live-rainbow": {
        "operation": "live-rainbow",
        "label": "rainbow-rgb",
        "write_file": "live-rainbow.json",
        "analysis_file": "analyze-live-rainbow.json",
        "dir_prefixes": ("safe-rainbow-",),
    },
    "live-bind": {
        "operation": "live-bind",
        "label": "pairing-bind",
        "write_file": "live-bind.json",
        "analysis_file": "analyze-live-bind.json",
        "dir_prefixes": ("safe-bind-",),
    },
    "live-unbind": {
        "operation": "live-unbind",
        "label": "pairing-unbind",
        "write_file": "live-unbind.json",
        "analysis_file": "analyze-live-unbind.json",
        "dir_prefixes": ("safe-unbind-",),
    },
}
RECEIVER_EVIDENCE_FALLBACK_WRITE_DIR = "experiments/safe-pwm-001"
RECEIVER_OBSERVATION_FILE_NAME = "observation.json"
RECEIVER_OBSERVATION_EFFECTS = ("changed", "unchanged", "unclear")
RECEIVER_PWM_WRITE_OPERATIONS = {"live-pwm", "live-pwm-sync", "live-pwm-mirror"}
RECEIVER_LIGHTING_WRITE_OPERATIONS = {"live-rgb", "live-rainbow"}
RECEIVER_IDENTITY_SNAPSHOT_FILES = ("live-list.json", "readonly/live-list.json")
RECEIVER_IDENTITY_MASTER_FILES = ("live-master.json", "readonly/live-master.json")
RECEIVER_IDENTITY_FIELDS = ("master_mac", "is_bound", "channel", "rx_type", "device_type", "fan_count")


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


def receiver_evidence_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise LianLiWirelessError(f"evidence path does not exist: {path}")
    root = path if path.is_dir() else path.parent
    summary = summarize_experiment_dir(path)
    manifest = receiver_evidence_manifest(root)
    required = receiver_evidence_checklist(root, RECEIVER_EVIDENCE_REQUIRED_FILES)
    hardware_validation = summary.get("hardware_validation") if isinstance(summary.get("hardware_validation"), dict) else {}
    next_action = summary.get("receiver_control_next_action") if isinstance(summary.get("receiver_control_next_action"), dict) else {}
    identity_consistency = receiver_identity_consistency(root)
    write_sets = receiver_evidence_write_sets(root, next_action)
    write = [
        file_item
        for write_set in write_sets
        for file_item in write_set["files"]
    ]
    missing_required = [item for item in required if not item["exists"]]
    complete_write_sets = [item for item in write_sets if item["status"] == "complete"]
    partial_write_sets = [item for item in write_sets if item["status"] == "partial"]
    confirmed_write_sets = [
        item for item in write_sets if item["control_proof_status"] == "visually-confirmed"
    ]
    conflict_write_sets = [
        item for item in write_sets if item["control_proof_status"] == "visual-observation-conflicts"
    ]
    machine_conflict_write_sets = [
        item for item in write_sets if item["control_proof_status"] == "machine-evidence-conflict"
    ]
    machine_incomplete_write_sets = [
        item for item in write_sets if item["control_proof_status"] == "machine-evidence-incomplete"
    ]
    invalid_observation_sets = [
        item for item in write_sets if item["control_proof_status"] == "invalid-observation"
    ]
    unclear_observation_sets = [
        item for item in write_sets if item["control_proof_status"] == "needs-clear-observation"
    ]
    observation_missing_sets = [
        item for item in complete_write_sets if item["visual_observation"]["status"] == "missing"
    ]
    status = receiver_evidence_status(
        missing_required_count=len(missing_required),
        invalid_file_count=int(summary.get("invalid_file_count") or 0),
        hardware_status=str(hardware_validation.get("status") or ""),
        next_action_status=str(next_action.get("status") or ""),
        identity_status=str(identity_consistency.get("status") or ""),
        complete_write_set_count=len(complete_write_sets),
        confirmed_write_set_count=len(confirmed_write_sets),
        conflict_write_set_count=len(conflict_write_sets),
        machine_conflict_count=len(machine_conflict_write_sets),
        machine_incomplete_count=len(machine_incomplete_write_sets),
        invalid_observation_count=len(invalid_observation_sets),
        unclear_observation_count=len(unclear_observation_sets),
    )
    return {
        "operation": "receiver-evidence-report",
        "path": str(path),
        "root": str(root),
        "status": status,
        "json_file_count": len(manifest),
        "required_present_count": len(required) - len(missing_required),
        "required_missing_count": len(missing_required),
        "required_files": required,
        "write_files": write,
        "write_evidence_set_count": len(write_sets),
        "write_evidence_complete_count": len(complete_write_sets),
        "write_evidence_partial_count": len(partial_write_sets),
        "write_evidence_confirmed_count": len(confirmed_write_sets),
        "write_evidence_conflict_count": len(conflict_write_sets),
        "write_evidence_machine_conflict_count": len(machine_conflict_write_sets),
        "write_evidence_machine_incomplete_count": len(machine_incomplete_write_sets),
        "visual_observation_invalid_count": len(invalid_observation_sets),
        "visual_observation_unclear_count": len(unclear_observation_sets),
        "visual_observation_missing_count": len(observation_missing_sets),
        "write_evidence_sets": write_sets,
        "receiver_identity_consistency": identity_consistency,
        "file_manifest": manifest,
        "hardware_validation": hardware_validation,
        "receiver_control_next_action": next_action,
        "recommended_commands": receiver_evidence_recommended_commands(root, status, next_action),
        "summary": summary,
    }


def receiver_observation_record(
    path: Path,
    *,
    effect: str = "unclear",
    target: str = "",
    observed_pwm: str = "",
    observed_rpm: str = "",
    note: str | list[str] = "",
    operator: str = "",
    observed_at: str = "",
) -> dict[str, Any]:
    normalized_effect = str(effect or "unclear").strip().lower()
    if normalized_effect not in RECEIVER_OBSERVATION_EFFECTS:
        raise LianLiWirelessError(
            "receiver observation effect must be one of: "
            + ", ".join(RECEIVER_OBSERVATION_EFFECTS)
        )
    notes = note if isinstance(note, list) else [note]
    clean_notes = [str(item).strip() for item in notes if str(item).strip()]
    write_set = receiver_evidence_write_set(Path(path).parent, path, {"observation-target"})
    inferred_target = target or str(write_set.get("target") or "")
    return {
        "operation": "receiver-observation",
        "experiment_dir": str(path),
        "target": inferred_target,
        "effect": normalized_effect,
        "observed_pwm": str(observed_pwm or ""),
        "observed_rpm": str(observed_rpm or ""),
        "operator": str(operator or ""),
        "observed_at": str(observed_at or ""),
        "notes": clean_notes,
        "machine_evidence_status": str(write_set.get("status") or ""),
        "machine_evidence_files": write_set.get("files", []),
        "checklist": [
            "Confirm the target MAC matches the fan group being watched.",
            "Record whether fan speed visibly/audibly changed after the guarded write.",
            "Keep this JSON as observation.json inside the same safe-pwm experiment directory.",
        ],
    }


def receiver_evidence_manifest(root: Path) -> list[dict[str, Any]]:
    files = sorted(root.rglob("*.json")) if root.is_dir() else [root]
    result = []
    for file_path in files:
        item = {
            "path": str(file_path),
            "relative_path": str(file_path.relative_to(root)) if root.is_dir() else file_path.name,
            "size_bytes": file_path.stat().st_size,
            "sha256": _sha256_file(file_path),
        }
        try:
            payload = load_json_file(file_path)
        except LianLiWirelessError as error:
            item["status"] = "invalid-json"
            item["error"] = str(error)
        else:
            item["status"] = "ok"
            item["operation"] = str(payload.get("operation") or "")
        result.append(item)
    return result


def receiver_evidence_checklist(root: Path, relative_paths: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        receiver_evidence_file_item(root, root / relative_path)
        for relative_path in relative_paths
    ]


def receiver_evidence_write_sets(root: Path, next_action: dict[str, Any]) -> list[dict[str, Any]]:
    directories: dict[Path, set[str]] = {}

    experiments_dir = root / "experiments"
    if experiments_dir.exists():
        for output_dir in sorted(path for path in experiments_dir.rglob("*") if path.is_dir()):
            if not _looks_like_safe_pwm_evidence_dir(output_dir):
                continue
            _add_write_evidence_dir(directories, output_dir, "existing-files")

    for output_dir in _receiver_evidence_recommended_output_dirs(next_action):
        _add_write_evidence_dir(directories, output_dir, "recommended-command")

    if not directories:
        _add_write_evidence_dir(directories, root / RECEIVER_EVIDENCE_FALLBACK_WRITE_DIR, "checklist-template")

    return [
        receiver_evidence_write_set(root, output_dir, sources)
        for output_dir, sources in sorted(directories.items(), key=lambda item: str(item[0]))
    ]


def receiver_evidence_write_set(root: Path, output_dir: Path, sources: set[str]) -> dict[str, Any]:
    spec = _receiver_evidence_write_spec(output_dir)
    files = [
        receiver_evidence_file_item(root, output_dir / file_name)
        for file_name in _receiver_evidence_write_file_names(spec)
    ]
    present = [item for item in files if item["exists"]]
    missing = [item for item in files if not item["exists"]]
    live_pwm = _optional_json_object(output_dir / str(spec["write_file"]))
    before_snapshot = _optional_json_object(output_dir / "live-list-before.json")
    after_snapshot = _optional_json_object(output_dir / "live-list-after.json")
    analysis = _optional_json_object(output_dir / str(spec["analysis_file"]))
    visual_confirmation_required = bool(
        live_pwm.get("visual_confirmation_required") or analysis.get("visual_confirmation_required")
    )
    visual_observation = receiver_visual_observation(output_dir)
    observation_consistency = receiver_observation_consistency(live_pwm, visual_observation)
    machine_consistency = receiver_machine_write_consistency(live_pwm, analysis, before_snapshot, after_snapshot)
    if not missing:
        status = "complete"
    elif present:
        status = "partial"
    else:
        status = "missing"
    control_proof_status = receiver_control_proof_status(
        machine_status=status,
        likely_effective=analysis.get("likely_effective"),
        visual_confirmation_required=visual_confirmation_required,
        visual_status=str(visual_observation.get("status") or ""),
        consistency_status=str(observation_consistency.get("status") or ""),
        machine_consistency_status=str(machine_consistency.get("status") or ""),
    )
    return {
        "output_dir": str(output_dir),
        "relative_dir": _relative_path(root, output_dir),
        "sources": sorted(sources),
        "status": status,
        "control_proof_status": control_proof_status,
        "write_operation": str(spec["operation"]),
        "write_kind": str(spec["label"]),
        "write_file": str(spec["write_file"]),
        "analysis_file": str(spec["analysis_file"]),
        "present_count": len(present),
        "missing_count": len(missing),
        "target": str(live_pwm.get("target") or analysis.get("target") or ""),
        "pwm_values": receiver_write_expected_pwm_values(live_pwm),
        "packets_written": live_pwm.get("packets_written"),
        "likely_effective": analysis.get("likely_effective"),
        "visual_confirmation_required": visual_confirmation_required,
        "expected_effect": analysis.get("expected_effect") if isinstance(analysis.get("expected_effect"), dict) else {},
        "machine_consistency": machine_consistency,
        "visual_observation": visual_observation,
        "observation_consistency": observation_consistency,
        "files": files,
    }


def receiver_identity_consistency(root: Path) -> dict[str, Any]:
    snapshot_sources = []
    master_sources = []
    devices: dict[str, list[dict[str, Any]]] = {}
    conflicts: list[dict[str, Any]] = []
    missing_identity: list[dict[str, Any]] = []

    for relative_path in RECEIVER_IDENTITY_SNAPSHOT_FILES:
        path = root / relative_path
        source = {
            "relative_path": relative_path,
            "path": str(path),
            "exists": path.exists(),
            "status": "missing",
            "device_count": 0,
            "device_macs": [],
        }
        if path.exists():
            try:
                payload = load_json_file(path)
            except LianLiWirelessError as error:
                source["status"] = "invalid"
                source["error"] = str(error)
            else:
                source_devices = devices_by_mac(payload)
                source["status"] = "ok"
                source["device_count"] = len(source_devices)
                source["device_macs"] = sorted(source_devices)
                for mac, device in sorted(source_devices.items()):
                    entry = receiver_identity_entry(relative_path, device)
                    devices.setdefault(mac, []).append(entry)
                    missing_fields = [
                        field
                        for field in ("master_mac", "channel", "rx_type", "fan_count")
                        if entry["fields"].get(field) in ("", None)
                    ]
                    if missing_fields:
                        missing_identity.append(
                            {
                                "relative_path": relative_path,
                                "mac": mac,
                                "missing_fields": missing_fields,
                            }
                        )
        snapshot_sources.append(source)

    ok_snapshot_sources = [
        source
        for source in snapshot_sources
        if source.get("status") == "ok"
    ]
    if len(ok_snapshot_sources) > 1:
        source_mac_sets = [
            tuple(str(mac) for mac in source.get("device_macs", []) if isinstance(mac, str))
            for source in ok_snapshot_sources
        ]
        if len(set(source_mac_sets)) > 1:
            conflicts.append(
                {
                    "type": "snapshot-receiver-set-mismatch",
                    "field": "receiver_macs",
                    "sources": [
                        {
                            "relative_path": str(source.get("relative_path") or ""),
                            "device_macs": list(source_mac_sets[index]),
                        }
                        for index, source in enumerate(ok_snapshot_sources)
                    ],
                }
            )

    for relative_path in RECEIVER_IDENTITY_MASTER_FILES:
        path = root / relative_path
        source = {
            "relative_path": relative_path,
            "path": str(path),
            "exists": path.exists(),
            "status": "missing",
            "master_mac": "",
            "detected": False,
        }
        if path.exists():
            try:
                payload = load_json_file(path)
            except LianLiWirelessError as error:
                source["status"] = "invalid"
                source["error"] = str(error)
            else:
                master_mac = _normalize_mac(str(payload.get("master_mac") or ""))
                source["status"] = "ok" if master_mac else "no-master-mac"
                source["detected"] = bool(payload.get("detected"))
                source["master_mac"] = master_mac
                if "channel" in payload:
                    source["channel"] = payload.get("channel")
        master_sources.append(source)

    for mac, entries in sorted(devices.items()):
        for field in RECEIVER_IDENTITY_FIELDS:
            values = _unique_preserve_order(
                [
                    _identity_value_key(entry["fields"].get(field))
                    for entry in entries
                    if entry["fields"].get(field) not in ("", None)
                ]
            )
            if len(values) > 1:
                conflicts.append(
                    {
                        "type": "snapshot-field-mismatch",
                        "mac": mac,
                        "field": field,
                        "values": values,
                        "sources": [
                            {
                                "relative_path": entry["relative_path"],
                                "value": _identity_value_key(entry["fields"].get(field)),
                            }
                            for entry in entries
                            if entry["fields"].get(field) not in ("", None)
                        ],
                    }
                )

    master_macs = sorted(
        {
            str(source.get("master_mac") or "")
            for source in master_sources
            if source.get("master_mac")
        }
    )
    if len(master_macs) > 1:
        conflicts.append(
            {
                "type": "master-query-source-mismatch",
                "field": "master_mac",
                "values": master_macs,
                "sources": [
                    {
                        "relative_path": str(source.get("relative_path") or ""),
                        "master_mac": str(source.get("master_mac") or ""),
                    }
                    for source in master_sources
                    if source.get("master_mac")
                ],
            }
        )
    bound_master_macs = sorted(
        {
            str(entry["fields"].get("master_mac") or "")
            for entries in devices.values()
            for entry in entries
            if entry["fields"].get("master_mac") and entry["fields"].get("master_mac") != "00:00:00:00:00:00"
        }
    )
    if master_macs and bound_master_macs:
        mismatched_masters = [mac for mac in bound_master_macs if mac not in set(master_macs)]
        if mismatched_masters:
            conflicts.append(
                {
                    "type": "master-query-mismatch",
                    "field": "master_mac",
                    "master_query_values": master_macs,
                    "snapshot_values": bound_master_macs,
                    "mismatched_snapshot_values": mismatched_masters,
                }
            )

    if conflicts:
        status = "conflict"
    elif not devices:
        status = "missing"
    elif missing_identity or (bound_master_macs and not master_macs):
        status = "incomplete"
    else:
        status = "consistent"

    return {
        "status": status,
        "receiver_count": len(devices),
        "receiver_macs": sorted(devices),
        "master_query_macs": master_macs,
        "snapshot_master_macs": bound_master_macs,
        "snapshot_sources": snapshot_sources,
        "master_sources": master_sources,
        "devices": {
            mac: {
                "sources": entries,
                "source_count": len(entries),
            }
            for mac, entries in sorted(devices.items())
        },
        "missing_identity": missing_identity,
        "conflicts": conflicts,
        "notes": receiver_identity_consistency_notes(status, conflicts, missing_identity, master_macs, bound_master_macs),
    }


def receiver_identity_entry(relative_path: str, device: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for field in RECEIVER_IDENTITY_FIELDS:
        value = device.get(field)
        if field == "master_mac":
            value = _normalize_mac(str(value or ""))
        elif field in {"channel", "rx_type", "device_type", "fan_count"} and value not in ("", None):
            value = _int_value(value, default=-1)
            if value < 0:
                value = ""
        fields[field] = value
    return {
        "relative_path": relative_path,
        "fields": fields,
    }


def receiver_identity_consistency_notes(
    status: str,
    conflicts: list[dict[str, Any]],
    missing_identity: list[dict[str, Any]],
    master_macs: list[str],
    bound_master_macs: list[str],
) -> list[str]:
    if status == "conflict":
        return ["Receiver identity fields disagree across readonly snapshots or master query logs."]
    if status == "missing":
        return ["No receiver MAC was found in live-list or readonly/live-list evidence."]
    if status == "incomplete":
        notes = []
        if missing_identity:
            notes.append("One or more receiver snapshots are missing master/channel/rx_type/fan_count fields.")
        if bound_master_macs and not master_macs:
            notes.append("Bound receivers have master MACs, but live-master did not provide a master MAC.")
        return notes or ["Receiver identity evidence is incomplete."]
    return ["Receiver MAC, master MAC, channel, rx_type, device_type, and fan_count are consistent across readonly evidence."]


def _identity_value_key(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def receiver_visual_observation(output_dir: Path) -> dict[str, Any]:
    path = output_dir / RECEIVER_OBSERVATION_FILE_NAME
    base = {
        "path": str(path),
        "relative_name": RECEIVER_OBSERVATION_FILE_NAME,
    }
    if not path.exists():
        return {
            **base,
            "status": "missing",
            "effect": "",
            "target": "",
            "notes": [],
        }
    try:
        payload = load_json_file(path)
    except LianLiWirelessError as error:
        return {
            **base,
            "status": "invalid",
            "effect": "",
            "target": "",
            "notes": [],
            "error": str(error),
        }
    effect = str(payload.get("effect") or "unclear").strip().lower()
    if str(payload.get("operation") or "") != "receiver-observation":
        status = "invalid"
    elif effect == "changed":
        status = "confirmed"
    elif effect == "unchanged":
        status = "contradicts"
    elif effect == "unclear":
        status = "unclear"
    else:
        status = "invalid"
    return {
        **base,
        "status": status,
        "effect": effect,
        "target": str(payload.get("target") or ""),
        "observed_pwm": str(payload.get("observed_pwm") or ""),
        "observed_rpm": str(payload.get("observed_rpm") or ""),
        "operator": str(payload.get("operator") or ""),
        "observed_at": str(payload.get("observed_at") or ""),
        "notes": _string_list(payload.get("notes")),
    }


def receiver_observation_consistency(live_pwm: dict[str, Any], visual_observation: dict[str, Any]) -> dict[str, Any]:
    visual_status = str(visual_observation.get("status") or "")
    if visual_status in {"missing", "invalid", "unclear"}:
        return {
            "status": "not-applicable",
            "target_status": "not-checked",
            "pwm_status": "not-checked",
            "notes": [],
        }

    machine_target = _normalize_mac(str(live_pwm.get("target") or ""))
    observed_target = _normalize_mac(str(visual_observation.get("target") or ""))
    target_status = "not-provided"
    notes = []
    if machine_target and observed_target:
        target_status = "match" if machine_target == observed_target else "mismatch"
    elif machine_target:
        target_status = "observation-target-missing"
    elif observed_target:
        target_status = "machine-target-missing"

    machine_pwm = receiver_write_expected_pwm_values(live_pwm)
    observed_pwm_text = str(visual_observation.get("observed_pwm") or "").strip()
    observed_pwm = _parse_observed_pwm_values(observed_pwm_text)
    pwm_status = "not-provided"
    if observed_pwm_text:
        if observed_pwm is None:
            pwm_status = "unparseable"
        elif not machine_pwm:
            pwm_status = "machine-pwm-missing"
        elif len(observed_pwm) == 1 and len(set(machine_pwm)) == 1:
            pwm_status = "match" if observed_pwm[0] == machine_pwm[0] else "mismatch"
        else:
            pwm_status = "match" if observed_pwm == machine_pwm else "mismatch"

    if target_status == "mismatch":
        notes.append("Observation target MAC differs from live-pwm target.")
    if pwm_status == "mismatch":
        notes.append("Observation PWM differs from live-pwm pwm_values.")
    status = "conflict" if target_status == "mismatch" or pwm_status == "mismatch" else "consistent"
    return {
        "status": status,
        "target_status": target_status,
        "pwm_status": pwm_status,
        "machine_target": machine_target,
        "observed_target": observed_target,
        "machine_pwm_values": machine_pwm,
        "observed_pwm_values": observed_pwm or [],
        "notes": notes,
    }


def receiver_write_expected_pwm_values(live_write: dict[str, Any]) -> list[int]:
    operation = str(live_write.get("operation") or "")
    if operation == "live-pwm-sync":
        expected = _int_list(live_write.get("expected_pwm_values"))
        if expected:
            return expected
        enabled = bool(live_write.get("enabled", True))
        if enabled:
            return [6, 6, 6, 6]
        fallback_pwm = _int_value(live_write.get("fallback_pwm"), default=-1)
        return [fallback_pwm] * 4 if fallback_pwm >= 0 else []
    return _int_list(live_write.get("pwm_values")) or []


def receiver_machine_write_consistency(
    live_pwm: dict[str, Any],
    analysis: dict[str, Any],
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any],
) -> dict[str, Any]:
    if not live_pwm and not analysis and not before_snapshot and not after_snapshot:
        return {"status": "not-applicable", "checks": [], "notes": []}

    checks: list[dict[str, Any]] = []
    notes: list[str] = []
    target = _normalize_mac(str(live_pwm.get("target") or analysis.get("target") or ""))
    if not target:
        checks.append({"name": "target-present", "status": "incomplete"})
        notes.append("live write/analysis target MAC is missing.")
    else:
        checks.append({"name": "target-present", "status": "match", "target": target})

    analysis_target = _normalize_mac(str(analysis.get("target") or ""))
    if target and analysis_target:
        status = "match" if target == analysis_target else "mismatch"
        checks.append(
            {
                "name": "analysis-target",
                "status": status,
                "live_pwm_target": target,
                "analysis_target": analysis_target,
            }
        )
        if status == "mismatch":
            notes.append("analysis target differs from live write target.")
    elif target:
        checks.append({"name": "analysis-target", "status": "incomplete", "live_pwm_target": target})

    before_devices = devices_by_mac(before_snapshot)
    after_devices = devices_by_mac(after_snapshot)
    before_target = before_devices.get(target) if target else None
    after_target = after_devices.get(target) if target else None
    for name, device in (("before-target", before_target), ("after-target", after_target)):
        if not target:
            continue
        status = "present" if device is not None else "missing"
        checks.append({"name": name, "status": status, "target": target})
        if status == "missing":
            notes.append(f"{name} snapshot does not contain live write target.")

    machine_pwm = receiver_write_expected_pwm_values(live_pwm)
    after_pwm = _int_list(after_target.get("pwm_values")) if isinstance(after_target, dict) else []
    if machine_pwm and after_pwm:
        status = "match" if after_pwm == machine_pwm else "mismatch"
        checks.append(
            {
                "name": "after-pwm-values",
                "status": status,
                "live_pwm_values": machine_pwm,
                "after_pwm_values": after_pwm,
            }
        )
        if status == "mismatch":
            notes.append("live-list-after pwm_values differ from live write expected pwm_values.")
    elif machine_pwm:
        checks.append({"name": "after-pwm-values", "status": "incomplete", "live_pwm_values": machine_pwm})

    target_found_after = analysis.get("target_found_after")
    if target_found_after is False:
        checks.append({"name": "analysis-target-found-after", "status": "mismatch", "value": False})
        notes.append("analysis reports the target was not found after the write.")
    elif target_found_after is True:
        checks.append({"name": "analysis-target-found-after", "status": "match", "value": True})

    visual_confirmation_required = bool(
        live_pwm.get("visual_confirmation_required") or analysis.get("visual_confirmation_required")
    )

    expected_effect = analysis.get("expected_effect") if isinstance(analysis.get("expected_effect"), dict) else {}
    expected_matched = expected_effect.get("matched")
    if expected_matched is False:
        checks.append({"name": "analysis-expected-effect", "status": "mismatch", "matched": False})
        notes.append("analysis expected_effect did not match.")
    elif expected_matched is True:
        checks.append({"name": "analysis-expected-effect", "status": "match", "matched": True})
    elif expected_effect:
        status = "expected" if visual_confirmation_required else "incomplete"
        checks.append({"name": "analysis-expected-effect", "status": status, "matched": expected_matched})

    if visual_confirmation_required:
        checks.append(
            {
                "name": "analysis-visual-confirmation-required",
                "status": "expected",
                "value": True,
            }
        )
    if analysis.get("likely_effective") is False and not visual_confirmation_required:
        checks.append({"name": "analysis-likely-effective", "status": "mismatch", "value": False})
        notes.append("analysis reports likely_effective=false.")
    elif analysis.get("likely_effective") is True:
        checks.append({"name": "analysis-likely-effective", "status": "match", "value": True})

    statuses = {str(check.get("status") or "") for check in checks}
    if "mismatch" in statuses or "missing" in statuses:
        status = "conflict"
    elif "incomplete" in statuses:
        status = "incomplete"
    else:
        status = "consistent"
    return {
        "status": status,
        "target": target,
        "checks": checks,
        "notes": notes,
    }


def receiver_control_proof_status(
    *,
    machine_status: str,
    likely_effective: Any,
    visual_confirmation_required: Any,
    visual_status: str,
    consistency_status: str = "",
    machine_consistency_status: str = "",
) -> str:
    if machine_status != "complete":
        return f"machine-evidence-{machine_status}"
    if machine_consistency_status == "conflict":
        return "machine-evidence-conflict"
    if machine_consistency_status == "incomplete":
        return "machine-evidence-incomplete"
    if visual_status == "confirmed":
        if consistency_status == "conflict":
            return "visual-observation-conflicts"
        return "visually-confirmed"
    if visual_status == "contradicts":
        return "visual-observation-conflicts"
    if visual_status == "invalid":
        return "invalid-observation"
    if visual_status == "unclear":
        return "needs-clear-observation"
    if likely_effective is True or visual_confirmation_required is True:
        return "machine-evidence-complete-needs-observation"
    return "needs-observation"


def _normalize_mac(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    parts = re.findall(r"[0-9a-f]{2}", text)
    return ":".join(parts[:6]) if len(parts) >= 6 else text


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            return []
    return result


def _parse_observed_pwm_values(value: str) -> list[int] | None:
    text = str(value or "").strip()
    if not text:
        return []
    values = [int(item) for item in re.findall(r"\d+", text)]
    if not values:
        return None
    return values


def receiver_evidence_file_item(root: Path, path: Path) -> dict[str, Any]:
    return {
        "relative_path": _relative_path(root, path),
        "exists": path.exists(),
        "path": str(path),
    }


def _add_write_evidence_dir(directories: dict[Path, set[str]], output_dir: Path, source: str) -> None:
    directories.setdefault(output_dir, set()).add(source)


def _looks_like_safe_pwm_evidence_dir(path: Path) -> bool:
    spec = _receiver_evidence_write_spec(path)
    file_names = _receiver_evidence_write_file_names(spec)
    if not any((path / file_name).exists() for file_name in file_names):
        return False
    prefixes = tuple(str(prefix) for prefix in spec.get("dir_prefixes", ()))
    return path.name.startswith(prefixes) or any((path / file_name).exists() for file_name in file_names[1:3])


def _receiver_evidence_write_spec(output_dir: Path) -> dict[str, Any]:
    for spec in RECEIVER_EVIDENCE_WRITE_SPECS.values():
        if (output_dir / str(spec["write_file"])).exists() or (output_dir / str(spec["analysis_file"])).exists():
            return spec
    prefix_specs = sorted(
        RECEIVER_EVIDENCE_WRITE_SPECS.values(),
        key=lambda item: max((len(str(prefix)) for prefix in item.get("dir_prefixes", ())), default=0),
        reverse=True,
    )
    for spec in prefix_specs:
        prefixes = tuple(str(prefix) for prefix in spec.get("dir_prefixes", ()))
        if output_dir.name.startswith(prefixes):
            return spec
    return RECEIVER_EVIDENCE_WRITE_SPECS["live-pwm"]


def _receiver_evidence_write_file_names(spec: dict[str, Any]) -> tuple[str, ...]:
    return (
        "live-list-before.json",
        str(spec["write_file"]),
        "live-list-after.json",
        str(spec["analysis_file"]),
        "summary.json",
    )


def _receiver_evidence_recommended_output_dirs(next_action: dict[str, Any]) -> list[Path]:
    candidates = next_action.get("candidates")
    if not isinstance(candidates, list):
        return []
    output_dirs = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key, argv in candidate.items():
            if not str(key).endswith("_argv") or not isinstance(argv, list):
                continue
            args = [str(item) for item in argv]
            if "--output-dir" not in args:
                continue
            index = args.index("--output-dir")
            if index + 1 >= len(args):
                continue
            output_dirs.append(Path(args[index + 1]))
    return output_dirs


def _optional_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return load_json_file(path)
    except LianLiWirelessError:
        return {}


def _relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def receiver_evidence_status(
    *,
    missing_required_count: int,
    invalid_file_count: int,
    hardware_status: str,
    next_action_status: str,
    identity_status: str = "",
    complete_write_set_count: int = 0,
    confirmed_write_set_count: int = 0,
    conflict_write_set_count: int = 0,
    machine_conflict_count: int = 0,
    machine_incomplete_count: int = 0,
    invalid_observation_count: int = 0,
    unclear_observation_count: int = 0,
) -> str:
    if invalid_file_count:
        return "invalid-evidence-files"
    if missing_required_count:
        return "missing-readonly-evidence"
    if hardware_status == "errors":
        return "validation-errors"
    if identity_status == "conflict":
        return "receiver-identity-conflict"
    if identity_status == "incomplete":
        return "receiver-identity-incomplete"
    if identity_status == "missing":
        return "receiver-identity-missing"
    if machine_conflict_count:
        return "write-evidence-machine-conflict"
    if machine_incomplete_count:
        return "write-evidence-machine-incomplete"
    if conflict_write_set_count:
        return "write-evidence-observation-conflict"
    if invalid_observation_count:
        return "write-evidence-invalid-observation"
    if unclear_observation_count:
        return "write-evidence-unclear-observation"
    if confirmed_write_set_count:
        return "write-evidence-confirmed"
    if hardware_status == "readonly-and-write-observed" or complete_write_set_count:
        return "write-evidence-needs-observation"
    if next_action_status == "ready-for-single-target-safe-pwm":
        return "ready-for-single-target-safe-pwm"
    if hardware_status == "readonly-and-write-gate-ready":
        return "write-gate-ready"
    if hardware_status == "readonly-observed":
        return "readonly-evidence-collected"
    return "needs-review"


def receiver_evidence_recommended_commands(path: Path, status: str, next_action: dict[str, Any]) -> list[str]:
    commands = []
    if status in {
        "missing-readonly-evidence",
        "invalid-evidence-files",
        "receiver-identity-conflict",
        "receiver-identity-incomplete",
        "receiver-identity-missing",
    }:
        commands.append(
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
    action_commands = next_action.get("recommended_commands")
    if isinstance(action_commands, list):
        commands.extend(str(command) for command in action_commands if isinstance(command, str) and command)
    if status == "write-evidence-needs-observation":
        commands.extend(receiver_observation_commands(path))
    if _tool_command("receiver-evidence-report", str(path)) not in commands:
        commands.append(_tool_command("receiver-evidence-report", str(path)))
    return _unique_preserve_order(commands)


def receiver_observation_commands(path: Path) -> list[str]:
    commands = []
    for write_set in receiver_evidence_write_sets(path, {}):
        if write_set.get("status") != "complete":
            continue
        observation = write_set.get("visual_observation")
        if isinstance(observation, dict) and observation.get("status") == "confirmed":
            continue
        output_dir = str(write_set.get("output_dir") or "")
        if not output_dir:
            continue
        argv = [
            "--save-json",
            str(Path(output_dir) / RECEIVER_OBSERVATION_FILE_NAME),
            "receiver-observation",
            output_dir,
            "--effect",
            "changed",
        ]
        target = str(write_set.get("target") or "").strip()
        if target:
            argv.extend(["--target", target])
        observed_pwm = _format_observed_pwm_arg(write_set.get("pwm_values"))
        if observed_pwm:
            argv.extend(["--observed-pwm", observed_pwm])
        operation = str(write_set.get("write_operation") or "")
        argv.extend(
            [
                "--note",
                _receiver_observation_note(operation),
            ]
        )
        commands.append(_tool_command(*argv))
    return commands


def _receiver_observation_note(operation: str) -> str:
    if operation in {"live-rgb", "live-rainbow"}:
        return "lighting visibly changed after guarded wireless lighting write"
    if operation == "live-bind":
        return "receiver pairing state changed after guarded bind write"
    if operation == "live-unbind":
        return "receiver pairing state changed after guarded unbind write"
    return "fan speed visibly changed after guarded PWM write"


def _format_observed_pwm_arg(value: Any) -> str:
    values = _int_list(value)
    if not values:
        return ""
    if len(set(values)) == 1:
        return str(values[0])
    return ",".join(str(item) for item in values)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


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
    identity_consistency = receiver_identity_consistency(path if path.is_dir() else path.parent)
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
        "receiver_identity_consistency": identity_consistency,
        "receiver_control_next_action": receiver_control_next_action(
            path,
            hardware_validation,
            live_snapshot_devices,
            receiver_validation_bundles,
            identity_consistency,
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
    identity_consistency: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidates = receiver_safe_pwm_candidates(path, live_snapshot_devices)
    ready_candidates = [item for item in candidates if item["status"] == "ready"]
    hardware_status = str(hardware_validation.get("status") or "")
    identity_consistency = identity_consistency or {}
    identity_status = str(identity_consistency.get("status") or "")
    write_sets = _receiver_actual_write_sets(path)
    write_conflict_sets = [
        item
        for item in write_sets
        if str(item.get("control_proof_status") or "")
        in {
            "machine-evidence-conflict",
            "visual-observation-conflicts",
            "invalid-observation",
        }
    ]
    write_incomplete_sets = [
        item
        for item in write_sets
        if str(item.get("control_proof_status") or "") == "machine-evidence-incomplete"
    ]
    pending_observation_sets = [
        item
        for item in write_sets
        if str(item.get("control_proof_status") or "")
        in {
            "machine-evidence-complete-needs-observation",
            "needs-clear-observation",
        }
    ]
    confirmed_pwm_sets = [
        item
        for item in write_sets
        if str(item.get("control_proof_status") or "") == "visually-confirmed"
        and str(item.get("write_operation") or "") in RECEIVER_PWM_WRITE_OPERATIONS
    ]
    expansion_candidate: dict[str, Any] = {}
    recommended_commands: list[str] = []
    can_run_safe_lighting = False

    if hardware_status == "errors":
        status = "validation-errors"
        reason = "Inspect validation_errors and receiver_validation_bundles before any write."
        can_run_safe_pwm = False
    elif write_conflict_sets:
        status = "write-validation-conflict"
        reason = "At least one guarded write evidence set conflicts; inspect receiver-evidence-report before any more writes."
        can_run_safe_pwm = False
        recommended_commands.append(_tool_command("receiver-evidence-report", str(path)))
    elif write_incomplete_sets:
        status = "write-validation-incomplete"
        reason = "At least one guarded write evidence set is incomplete; finish or remove that evidence before any more writes."
        can_run_safe_pwm = False
        recommended_commands.append(_tool_command("receiver-evidence-report", str(path)))
    elif pending_observation_sets:
        status = "write-validation-needs-observation"
        reason = "A guarded write has complete machine logs, but still needs a clear visual/audible observation record."
        can_run_safe_pwm = False
        recommended_commands.extend(receiver_observation_commands(path))
        recommended_commands.append(_tool_command("receiver-evidence-report", str(path)))
    elif confirmed_pwm_sets and identity_status == "conflict":
        status = "receiver-identity-conflict"
        reason = "A PWM write was confirmed, but receiver identity logs now disagree; recapture the receiver bundle before extending control."
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
    elif confirmed_pwm_sets and identity_status in {"missing", "incomplete"}:
        status = "needs-receiver-identity-validation"
        reason = "A PWM write was confirmed, but receiver identity evidence is incomplete; rerun the validation bundle before extending control."
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
    elif confirmed_pwm_sets:
        target = str(confirmed_pwm_sets[0].get("target") or "")
        device = live_snapshot_devices.get(target.lower(), {}) if target else {}
        expansion_candidate = receiver_safe_expansion_candidate(path, target, device, write_sets)
        lighting_commands = _string_list(expansion_candidate.get("safe_lighting_commands"))
        if lighting_commands:
            status = "ready-for-safe-lighting-validation"
            reason = "A single-target PWM write has been visually confirmed; the next safe expansion is one guarded lighting experiment."
            can_run_safe_pwm = False
            can_run_safe_lighting = True
            recommended_commands.append(lighting_commands[0])
            recommended_commands.append(_tool_command("receiver-evidence-report", str(path)))
        else:
            status = "write-validation-already-observed"
            reason = "Confirmed PWM evidence is present and no unrun safe lighting validation command remains in this directory."
            can_run_safe_pwm = False
            recommended_commands.append(_tool_command("receiver-evidence-report", str(path)))
    elif hardware_status == "readonly-and-write-observed":
        status = "write-validation-already-observed"
        reason = "A guarded write experiment is already represented in this log directory."
        can_run_safe_pwm = False
    elif hardware_status == "readonly-and-write-gate-ready":
        if identity_status == "conflict":
            status = "receiver-identity-conflict"
            reason = "Readonly snapshots or master-query logs disagree; recapture the receiver bundle before any write."
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
        elif identity_status in {"missing", "incomplete"}:
            status = "needs-receiver-identity-validation"
            reason = "Write-gate passed, but receiver identity evidence is incomplete; rerun the validation bundle first."
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
        elif ready_candidates:
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
        "can_run_safe_lighting": can_run_safe_lighting,
        "candidate_count": len(candidates),
        "ready_candidate_count": len(ready_candidates),
        "write_evidence_set_count": len(write_sets),
        "write_evidence_conflict_count": len(write_conflict_sets),
        "write_evidence_incomplete_count": len(write_incomplete_sets),
        "write_evidence_pending_observation_count": len(pending_observation_sets),
        "confirmed_pwm_write_count": len(confirmed_pwm_sets),
        "hardware_status": hardware_status,
        "receiver_identity_status": identity_status,
        "receiver_identity_consistency": identity_consistency,
        "safe_expansion_candidate": expansion_candidate,
        "recommended_commands": recommended_commands,
        "candidates": candidates,
    }


def _receiver_actual_write_sets(path: Path) -> list[dict[str, Any]]:
    return [
        item
        for item in receiver_evidence_write_sets(path, {})
        if str(item.get("status") or "") != "missing"
    ]


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


def receiver_safe_expansion_candidate(
    path: Path,
    target: str,
    device: dict[str, Any],
    write_sets: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_mac = _normalize_mac(target)
    if not normalized_mac:
        return {
            "status": "missing-target",
            "mac": "",
            "safe_lighting_commands": [],
            "deferred_pairing_commands": [],
        }
    present_operations = {
        str(item.get("write_operation") or "")
        for item in write_sets
        if str(item.get("status") or "") != "missing"
    }
    lighting_commands = []
    token = _path_token(normalized_mac)
    if "live-rgb" not in present_operations:
        argv = [
            "safe-rgb-experiment",
            "--mac",
            normalized_mac,
            "--color",
            "0,0,0",
            "--output-dir",
            str(path / "experiments" / f"safe-rgb-{token}"),
            "--confirm",
            "WRITE-LIANLI",
        ]
        lighting_commands.append(_tool_command(*argv))
    if "live-rainbow" not in present_operations:
        argv = [
            "safe-rainbow-experiment",
            "--mac",
            normalized_mac,
            "--frame-count",
            "24",
            "--interval-ms",
            "50",
            "--output-dir",
            str(path / "experiments" / f"safe-rainbow-{token}"),
            "--confirm",
            "WRITE-LIANLI",
        ]
        lighting_commands.append(_tool_command(*argv))

    pairing_commands = []
    is_bound = device.get("is_bound")
    if is_bound is True and "live-unbind" not in present_operations:
        argv = [
            "safe-unbind-experiment",
            "--mac",
            normalized_mac,
            "--output-dir",
            str(path / "experiments" / f"safe-unbind-{token}"),
            "--confirm",
            "WRITE-LIANLI",
        ]
        channel = device.get("channel")
        if isinstance(channel, int):
            argv[3:3] = ["--channel", str(channel)]
        pairing_commands.append(_tool_command(*argv))
    elif is_bound is False and "live-bind" not in present_operations and isinstance(device.get("rx_type"), int):
        argv = [
            "safe-bind-experiment",
            "--mac",
            normalized_mac,
            "--rx-type",
            str(device["rx_type"]),
            "--output-dir",
            str(path / "experiments" / f"safe-bind-{token}"),
            "--confirm",
            "WRITE-LIANLI",
        ]
        channel = device.get("channel")
        if isinstance(channel, int):
            argv[5:5] = ["--channel", str(channel)]
        pairing_commands.append(_tool_command(*argv))

    return {
        "status": "ready" if lighting_commands else "lighting-covered",
        "mac": normalized_mac,
        "completed_operations": sorted(present_operations),
        "safe_lighting_commands": lighting_commands,
        "deferred_pairing_commands": pairing_commands,
        "note": "Run one lighting command at a time and record receiver-observation before trying pairing commands.",
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
