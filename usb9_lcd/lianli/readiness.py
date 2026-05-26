from __future__ import annotations

from pathlib import Path
import shlex
from typing import Any

from usb9_lcd.lianli.analysis import (
    RECEIVER_EVIDENCE_REQUIRED_FILES,
    receiver_evidence_report,
    receiver_pairing_risk_report,
)
from usb9_lcd.lianli.capture import capture_gap_report


def lianli_validation_gate(
    *,
    capture_dir: Path,
    hardware_dir: Path,
    version: str = "2.1.17",
    capture_base: str | None = None,
    experiment_dir: Path | None = None,
    led_count: int = 12,
    rainbow_frames: int = 3,
    interval_ms: int = 40,
    effect_index: int = 1,
) -> dict[str, Any]:
    capture_dir = capture_dir.expanduser()
    hardware_dir = hardware_dir.expanduser()
    capture = capture_gap_report(
        capture_dir,
        version=version,
        capture_base=capture_base,
        experiment_dir=experiment_dir,
        led_count=led_count,
        rainbow_frames=rainbow_frames,
        interval_ms=interval_ms,
        effect_index=effect_index,
    )
    evidence = _receiver_evidence_or_missing(hardware_dir)
    pairing = _receiver_pairing_or_missing(hardware_dir, evidence)
    checklist = _validation_checklist(capture, evidence, pairing)
    blockers = [item for item in checklist if item["status"] == "blocker"]
    warnings = [item for item in checklist if item["status"] == "warning"]
    status = _validation_status(capture, evidence, pairing, blockers, warnings)
    return {
        "operation": "lianli-validation-gate",
        "status": status,
        "version": version,
        "capture_dir": str(capture_dir),
        "hardware_dir": str(hardware_dir),
        "capture_base": str(capture.get("capture_base") or capture_base or f"l-connect-v{version}"),
        "capture_status": str(capture.get("status") or ""),
        "receiver_evidence_status": str(evidence.get("status") or ""),
        "receiver_next_action_status": _receiver_next_action_status(evidence),
        "pairing_risk_status": str(pairing.get("status") or ""),
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "checklist": checklist,
        "blockers": blockers,
        "warnings": warnings,
        "recommended_commands": _validation_recommended_commands(
            capture,
            evidence,
            pairing,
            capture_dir=capture_dir,
            hardware_dir=hardware_dir,
            version=version,
            capture_base=str(capture.get("capture_base") or capture_base or f"l-connect-v{version}"),
        ),
        "reports": {
            "capture_gap": capture,
            "receiver_evidence": evidence,
            "pairing_risk": pairing,
        },
    }


def _receiver_evidence_or_missing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "operation": "receiver-evidence-report",
            "path": str(path),
            "status": "missing-evidence-directory",
            "required_missing_count": len(RECEIVER_EVIDENCE_REQUIRED_FILES),
            "required_present_count": 0,
            "receiver_identity_consistency": {"status": "missing"},
            "hardware_validation": {"status": "missing"},
            "receiver_control_next_action": {
                "status": "needs-receiver-validation-bundle",
                "can_run_safe_pwm": False,
                "can_run_safe_lighting": False,
                "can_review_safe_pairing": False,
            },
            "recommended_commands": [],
            "error": "",
        }
    try:
        return receiver_evidence_report(path)
    except Exception as error:  # noqa: BLE001 - the gate must not hide the capture report behind one bad JSON.
        return {
            "operation": "receiver-evidence-report",
            "path": str(path),
            "status": "analysis-error",
            "required_missing_count": len(RECEIVER_EVIDENCE_REQUIRED_FILES),
            "required_present_count": 0,
            "receiver_identity_consistency": {"status": "unknown"},
            "hardware_validation": {"status": "unknown"},
            "receiver_control_next_action": {
                "status": "receiver-evidence-analysis-error",
                "can_run_safe_pwm": False,
                "can_run_safe_lighting": False,
                "can_review_safe_pairing": False,
            },
            "recommended_commands": [],
            "error": str(error),
        }


def _receiver_pairing_or_missing(path: Path, evidence: dict[str, Any]) -> dict[str, Any]:
    if str(evidence.get("status") or "") in {"missing-evidence-directory", "analysis-error"}:
        return {
            "operation": "receiver-pairing-risk-report",
            "path": str(path),
            "status": "not-ready",
            "blockers": [],
            "warnings": [],
            "recommended_commands": [],
        }
    try:
        return receiver_pairing_risk_report(path)
    except Exception as error:  # noqa: BLE001 - report the failure as one gate item.
        return {
            "operation": "receiver-pairing-risk-report",
            "path": str(path),
            "status": "analysis-error",
            "blockers": [{"name": "pairing-risk-analysis", "status": "blocker", "message": str(error)}],
            "warnings": [],
            "recommended_commands": [],
            "error": str(error),
        }


def _validation_checklist(
    capture: dict[str, Any],
    evidence: dict[str, Any],
    pairing: dict[str, Any],
) -> list[dict[str, Any]]:
    gap_ids = {str(item.get("id") or "") for item in _dict_items(capture.get("scenario_gaps"))}
    operation_gaps = {
        str(item.get("operation") or ""): item for item in _dict_items(capture.get("operation_gaps"))
    }
    capture_status = str(capture.get("status") or "")
    evidence_status = str(evidence.get("status") or "")
    identity = evidence.get("receiver_identity_consistency")
    identity_status = str(identity.get("status") or "") if isinstance(identity, dict) else ""
    next_action = _receiver_next_action(evidence)
    next_status = str(next_action.get("status") or "")
    required_missing = _int_value(evidence.get("required_missing_count"))

    checks = [
        _check(
            "windows-baseline-capture",
            "ok"
            if capture_status not in {"analysis-errors", "needs-all-windows-captures", "needs-baseline-capture"}
            and "baseline" not in gap_ids
            else "blocker",
            "Windows baseline capture with receiver identity and idle snapshots is still missing.",
            value=capture_status,
        ),
        _operation_check(
            operation_gaps,
            "live-pwm",
            "windows-direct-pwm-capture",
            "Direct PWM evidence is required before lighting or pairing work is trusted.",
        ),
        _operation_check(
            operation_gaps,
            "live-rgb",
            "windows-static-rgb-capture",
            "Static RGB evidence is still needed before treating lighting control as validated.",
            blocker=False,
        ),
        _operation_check(
            operation_gaps,
            "live-rainbow",
            "windows-rainbow-capture",
            "Generated rainbow evidence is still needed before pairing risk review.",
            blocker=False,
        ),
        _check(
            "receiver-validation-bundle",
            "ok" if required_missing == 0 and evidence_status not in {"missing-evidence-directory", "analysis-error"} else "blocker",
            "Run the receiver validation bundle after plugging in the L-Wireless receiver.",
            value=evidence_status,
            detail={"required_missing_count": required_missing},
        ),
        _check(
            "receiver-identity-consistent",
            "ok" if identity_status == "consistent" else "blocker",
            "Receiver MAC/master/channel identity evidence is missing, incomplete, or conflicting.",
            value=identity_status or "unknown",
        ),
        _write_gate_check(next_status, next_action),
        _pairing_check(pairing, next_status),
    ]
    return checks


def _operation_check(
    operation_gaps: dict[str, dict[str, Any]],
    operation: str,
    name: str,
    message: str,
    *,
    blocker: bool = True,
) -> dict[str, Any]:
    gap = operation_gaps.get(operation)
    if gap is None:
        return _check(name, "ok", message, value="linux-validated")
    windows_status = str(gap.get("windows_evidence_status") or "")
    experiment_status = str(gap.get("experiment_status") or "")
    if windows_status == "evidence-found":
        return _check(
            name,
            "warning",
            f"Windows evidence exists, but Linux validation is still pending: {experiment_status or 'unknown'}.",
            value=experiment_status or str(gap.get("overall_status") or ""),
            detail={"operation": operation},
        )
    return _check(
        name,
        "blocker" if blocker else "warning",
        message,
        value=windows_status or str(gap.get("overall_status") or ""),
        detail={"operation": operation, "missing_scenarios": gap.get("missing_scenarios", [])},
    )


def _write_gate_check(next_status: str, next_action: dict[str, Any]) -> dict[str, Any]:
    if bool(next_action.get("can_run_safe_pwm")):
        return _check(
            "linux-safe-pwm-gate",
            "ok",
            "Receiver write gate allows one guarded single-target PWM validation.",
            value=next_status,
        )
    if next_status in {"ready-for-safe-lighting-validation", "ready-for-pairing-risk-review"}:
        return _check(
            "linux-safe-pwm-gate",
            "ok",
            "Safe PWM evidence is already confirmed; proceed through the next guarded stage.",
            value=next_status,
        )
    if next_status in {"write-validation-needs-observation", "write-validation-already-observed"}:
        return _check(
            "linux-safe-pwm-gate",
            "warning",
            "A guarded write exists but needs observation or report review before extending control.",
            value=next_status,
        )
    return _check(
        "linux-safe-pwm-gate",
        "blocker",
        "Do not run live writes until receiver evidence and linux-control-write-gate are ready.",
        value=next_status or "unknown",
    )


def _pairing_check(pairing: dict[str, Any], next_status: str) -> dict[str, Any]:
    status = str(pairing.get("status") or "")
    if status == "ready-for-manual-pairing-review":
        return _check(
            "pairing-risk-review",
            "ok",
            "Pairing remains a manual review step, but prerequisites are satisfied.",
            value=status,
        )
    if next_status == "ready-for-pairing-risk-review":
        return _check(
            "pairing-risk-review",
            "warning",
            "Pairing prerequisites are close; inspect receiver-pairing-risk-report before any bind/unbind.",
            value=status or "unknown",
        )
    return _check(
        "pairing-risk-review",
        "warning",
        "Pairing is intentionally deferred until PWM and lighting evidence are confirmed.",
        value=status or "deferred",
    )


def _validation_status(
    capture: dict[str, Any],
    evidence: dict[str, Any],
    pairing: dict[str, Any],
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> str:
    capture_status = str(capture.get("status") or "")
    evidence_status = str(evidence.get("status") or "")
    next_status = _receiver_next_action_status(evidence)
    if blockers:
        if capture_status == "analysis-errors":
            return "needs-capture-analysis-fix"
        if capture_status in {"needs-all-windows-captures", "needs-baseline-capture"}:
            return "needs-windows-baseline-capture"
        if capture_status == "needs-windows-capture":
            return "needs-windows-capture"
        if evidence_status in {"missing-evidence-directory", "missing-readonly-evidence", "analysis-error"}:
            return "needs-receiver-validation-bundle"
        if evidence_status.startswith("receiver-identity"):
            return "needs-receiver-identity-fix"
        if next_status == "write-validation-needs-observation":
            return "needs-write-observation"
        return "blocked"
    if str(pairing.get("status") or "") == "ready-for-manual-pairing-review":
        return "ready-for-manual-pairing-review"
    if next_status == "ready-for-safe-lighting-validation":
        return "ready-for-safe-lighting-validation"
    if next_status == "ready-for-single-target-safe-pwm":
        return "ready-for-safe-pwm-validation"
    if warnings:
        return "ready-with-warnings"
    return "ready-for-review"


def _validation_recommended_commands(
    capture: dict[str, Any],
    evidence: dict[str, Any],
    pairing: dict[str, Any],
    *,
    capture_dir: Path,
    hardware_dir: Path,
    version: str,
    capture_base: str,
) -> list[str]:
    commands = _string_list(capture.get("recommended_commands"))
    evidence_status = str(evidence.get("status") or "")
    if evidence_status in {"missing-evidence-directory", "missing-readonly-evidence", "analysis-error"}:
        commands.append(
            _tool_command(
                "--save-json",
                str(hardware_dir / "receiver-validation-bundle.json"),
                "receiver-validation-bundle",
                "--output-dir",
                str(hardware_dir),
                "--capture-dir",
                str(capture_dir),
                "--version",
                version,
                "--capture-base",
                capture_base,
            )
        )
    commands.extend(_string_list(evidence.get("recommended_commands")))
    commands.extend(_string_list(pairing.get("recommended_commands")))
    commands.append(_tool_command("receiver-evidence-report", str(hardware_dir)))
    commands.append(_tool_command("receiver-pairing-risk-report", str(hardware_dir)))
    commands.append(
        _tool_command(
            "lianli-validation-gate",
            "--capture-dir",
            str(capture_dir),
            "--hardware-dir",
            str(hardware_dir),
            "--version",
            version,
            "--capture-base",
            capture_base,
        )
    )
    return _unique_preserve_order(commands)


def _receiver_next_action(evidence: dict[str, Any]) -> dict[str, Any]:
    value = evidence.get("receiver_control_next_action")
    return value if isinstance(value, dict) else {}


def _receiver_next_action_status(evidence: dict[str, Any]) -> str:
    return str(_receiver_next_action(evidence).get("status") or "")


def _check(
    name: str,
    status: str,
    message: str,
    *,
    value: Any = "",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = {
        "name": name,
        "status": status,
        "message": message,
        "value": value,
    }
    if detail:
        item["detail"] = detail
    return item


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _tool_command(*parts: object) -> str:
    return "python tools/lianli_wireless_probe.py " + " ".join(shlex.quote(str(part)) for part in parts)


def _unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
