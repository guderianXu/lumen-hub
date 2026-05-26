from __future__ import annotations

import json
import subprocess
import sys
import importlib.util
from pathlib import Path

import pytest

from usb9_lcd.lianli.wireless import RGB_FIRST_PAYLOAD_REPEAT_COUNT, WirelessDeviceInfo, WirelessSnapshot


def _run_probe(*args: str) -> dict:
    result = subprocess.run(
        [sys.executable, "tools/lianli_wireless_probe.py", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _run_probe_raw(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "tools/lianli_wireless_probe.py", *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _write_tshark_json_capture(
    path: Path,
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


def _write_linux_experiment_summary_inputs(path: Path) -> None:
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


def _write_receiver_evidence_required_payloads(path: Path) -> None:
    required_payloads = {
        "receiver-validation-bundle.json": {
            "operation": "receiver-validation-bundle",
            "output_dir": str(path),
            "capture_dir": str(path / "captures"),
            "experiment_dir": str(path / "experiments"),
            "step_count": 7,
            "ok_count": 7,
            "error_count": 0,
            "ready_for_guarded_write": True,
            "write_gate_status": "write-enabled",
            "steps": [],
        },
        "summary.json": {"operation": "summarize-experiments", "path": str(path)},
        "scan.json": {"operation": "scan", "device_count": 1},
        "readiness.json": {"operation": "usb-capture-readiness", "status": "linux-live-ready"},
        "live-list.json": {"operation": "live-list", "device_count": 1, "devices": [_device_payload()]},
        "live-master.json": {"operation": "live-master", "detected": True, "master_mac": "10:20:30:40:50:60"},
        "validate-readonly.json": {"operation": "validate-readonly", "step_count": 3, "ok_count": 3, "error_count": 0},
        "preflight.json": {"operation": "linux-control-preflight", "status": "ready"},
        "write-gate.json": {
            "operation": "linux-control-write-gate",
            "status": "write-enabled",
            "allows_any_guarded_write": True,
        },
        "readonly/scan.json": {"operation": "scan", "device_count": 1},
        "readonly/live-list.json": {"operation": "live-list", "device_count": 1, "devices": [_device_payload()]},
        "readonly/live-master.json": {"operation": "live-master", "detected": True},
    }
    for relative_path, payload in required_payloads.items():
        item_path = path / relative_path
        item_path.parent.mkdir(parents=True, exist_ok=True)
        item_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _write_receiver_safe_pwm_evidence(path: Path) -> Path:
    output_dir = path / "experiments" / "safe-pwm-aa-bb-cc-dd-ee-ff"
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in {
        "live-list-before.json": {"operation": "live-list-before", "device_count": 1, "devices": [_device_payload()]},
        "live-pwm.json": {
            "operation": "live-pwm",
            "target": "aa:bb:cc:dd:ee:ff",
            "pwm_values": [120, 120, 120, 120],
            "packets_written": 4,
        },
        "live-list-after.json": {
            "operation": "live-list-after",
            "device_count": 1,
            "devices": [_device_payload(pwm=[120, 120, 120, 120])],
        },
        "analyze-live-pwm.json": {
            "operation": "analyze-log",
            "target": "aa:bb:cc:dd:ee:ff",
            "likely_effective": True,
            "expected_effect": {"available": True, "matched": True},
        },
        "summary.json": {"operation": "summarize-experiments", "path": str(output_dir)},
    }.items():
        (output_dir / name).write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return output_dir


def _write_receiver_safe_sync_evidence(path: Path) -> Path:
    output_dir = path / "experiments" / "safe-sync-aa-bb-cc-dd-ee-ff"
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in {
        "live-list-before.json": {"operation": "live-list-before", "device_count": 1, "devices": [_device_payload()]},
        "live-pwm-sync.json": {
            "operation": "live-pwm-sync",
            "target": "aa:bb:cc:dd:ee:ff",
            "enabled": True,
            "fallback_pwm": 100,
            "expected_pwm_values": [6, 6, 6, 6],
            "packets_written": 4,
        },
        "live-list-after.json": {
            "operation": "live-list-after",
            "device_count": 1,
            "devices": [_device_payload(pwm=[6, 6, 6, 6])],
        },
        "analyze-live-pwm-sync.json": {
            "operation": "analyze-log",
            "source_operation": "live-pwm-sync",
            "target": "aa:bb:cc:dd:ee:ff",
            "likely_effective": True,
            "target_found_after": True,
            "expected_effect": {"available": True, "matched": True},
        },
        "summary.json": {"operation": "summarize-experiments", "path": str(output_dir)},
    }.items():
        (output_dir / name).write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return output_dir


def _write_receiver_safe_mirror_evidence(path: Path) -> Path:
    output_dir = path / "experiments" / "safe-pwm-mirror-aa-bb-cc-dd-ee-ff"
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in {
        "live-list-before.json": {
            "operation": "live-list-before",
            "device_count": 1,
            "motherboard_pwm": 127,
            "devices": [_device_payload()],
        },
        "live-pwm-mirror.json": {
            "operation": "live-pwm-mirror",
            "target": "aa:bb:cc:dd:ee:ff",
            "motherboard_pwm": 127,
            "pwm_values": [127, 127, 127, 127],
            "packets_written": 4,
        },
        "live-list-after.json": {
            "operation": "live-list-after",
            "device_count": 1,
            "motherboard_pwm": 127,
            "devices": [_device_payload(pwm=[127, 127, 127, 127])],
        },
        "analyze-live-pwm-mirror.json": {
            "operation": "analyze-log",
            "source_operation": "live-pwm-mirror",
            "target": "aa:bb:cc:dd:ee:ff",
            "likely_effective": True,
            "target_found_after": True,
            "expected_effect": {"available": True, "matched": True},
        },
        "summary.json": {"operation": "summarize-experiments", "path": str(output_dir)},
    }.items():
        (output_dir / name).write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return output_dir


def _write_lianli_usb_sysfs_and_dev(sys_root: Path, dev_root: Path) -> None:
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


def _load_probe_module():
    spec = importlib.util.spec_from_file_location(
        "lianli_wireless_probe",
        Path("tools/lianli_wireless_probe.py"),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_probe_scan_reports_known_ids():
    payload = _run_probe("scan")

    assert payload["known_ids"]["0416:8040"] == "L-Wireless RF sender / transmitter"
    assert payload["known_ids"]["0416:8041"] == "L-Wireless RF receiver"
    assert payload["wireless_list_request_hex"].startswith("1001")
    assert isinstance(payload["devices"], list)


def test_probe_outputs_udev_rules():
    payload = _run_probe("udev-rules")

    assert payload["path"] == "/etc/udev/rules.d/70-lianli-wireless.rules"
    assert any('ATTR{idVendor}=="0416"' in rule for rule in payload["rules"])
    assert any('ATTR{idProduct}=="8041"' in rule for rule in payload["rules"])


def test_probe_live_list_uses_read_only_backend(monkeypatch, capsys):
    module = _load_probe_module()

    class FakeBackend:
        def list_devices(self):
            return WirelessSnapshot(
                raw=b"snapshot",
                devices=[
                    WirelessDeviceInfo(
                        mac="aa:bb:cc:dd:ee:ff",
                        master_mac="10:20:30:40:50:60",
                        channel=8,
                        rx_type=3,
                        device_type=2,
                        fan_count=3,
                        pwm_values=(80, 90, 100, 110),
                        fan_rpm=(1234, 1500, 0, 0),
                        command_sequence=7,
                        raw=bytes(42),
                    )
                ],
            )

    monkeypatch.setattr(module, "create_pyusb_backend", lambda: FakeBackend())

    assert module.main(["live-list"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "live-list"
    assert payload["device_count"] == 1
    assert payload["motherboard_pwm"] is None
    assert payload["devices"][0]["mac"] == "aa:bb:cc:dd:ee:ff"
    assert payload["devices"][0]["fan_rpm"] == [1234, 1500, 0, 0]
    assert payload["devices"][0]["raw_hex"] == bytes(42).hex()


def test_probe_live_master_uses_sender_query(monkeypatch, capsys):
    module = _load_probe_module()

    class FakeBackend:
        def query_master_mac(self, *, channel):
            return "10:20:30:40:50:60", channel

    monkeypatch.setattr(module, "create_pyusb_backend", lambda: FakeBackend())

    assert module.main(["live-master", "--channel", "8"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "live-master"
    assert payload["detected"] is True
    assert payload["master_mac"] == "10:20:30:40:50:60"


def test_probe_master_query_dry_run_outputs_request():
    payload = _run_probe("dry-run-master-query", "--channel", "8")

    assert payload["operation"] == "dry-run-master-query"
    assert payload["request_hex"].startswith("1108")
    assert len(bytes.fromhex(payload["request_hex"])) == 64


def test_probe_save_json_writes_same_payload(tmp_path):
    output_path = tmp_path / "lianli" / "master-query.json"

    result = _run_probe_raw(
        "--save-json",
        str(output_path),
        "dry-run-master-query",
        "--channel",
        "8",
    )
    stdout_payload = json.loads(result.stdout)
    file_payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert output_path.exists()
    assert stdout_payload == file_payload
    assert file_payload["operation"] == "dry-run-master-query"


def test_probe_analyze_artifact_reports_static_clues(tmp_path):
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"MZ" + b"L-Connect 3 0416:8041 slv3tuzx")

    payload = _run_probe("analyze-artifact", str(path))
    labels = {match["label"] for match in payload["matches"]}

    assert payload["operation"] == "analyze-artifact"
    assert payload["file_type"] == "pe"
    assert "RF receiver VID:PID text" in labels
    assert "wireless LCD DES key" in labels


def test_probe_analyze_artifact_tree_reports_directory_summary(tmp_path):
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

    payload = _run_probe("analyze-artifact-tree", str(root), "--max-file-size", "32")

    assert payload["operation"] == "analyze-artifact-tree"
    assert payload["file_count"] == 2
    assert payload["scanned_file_count"] == 1
    assert payload["skipped_file_count"] == 1
    assert payload["summary"]["categories"]["usb-id"] == 1
    assert payload["summary"]["nsis_file_count"] == 1


def test_probe_diff_artifacts_reports_new_static_clues(tmp_path):
    before = tmp_path / "before.bin"
    after = tmp_path / "after.bin"
    before.write_bytes(b"prefix-" + b"0416:8041" + b"-suffix")
    after.write_bytes(b"prefix-" + b"L-Wireless 0416:8040" + b"-suffix")

    payload = _run_probe("diff-artifacts", str(before), str(after), "--block-size", "8")
    added = {item["label"] for item in payload["static_match_delta"]["added"]}

    assert payload["operation"] == "diff-artifacts"
    assert payload["common_prefix_bytes"] == len(b"prefix-")
    assert payload["common_suffix_bytes"] == len(b"-suffix")
    assert {"RF sender VID:PID text", "L-Wireless text"} <= added


def test_probe_artifact_evidence_matrix_summarizes_saved_reports(tmp_path):
    (tmp_path / "analyze-artifact-v2.0.34-exe.json").write_text(
        json.dumps(
            {
                "operation": "analyze-artifact",
                "path": "L-Connect-v2.0.34.exe",
                "summary": {
                    "categories": {"usb-id": 2},
                    "confidence": {"high": 2},
                    "high_confidence_patterns": [
                        "RF sender VID:PID text",
                        "RF receiver VID:PID text",
                    ],
                },
                "matches": [
                    {"label": "RF sender VID:PID text", "category": "usb-id", "confidence": "high", "count": 1},
                    {"label": "RF receiver VID:PID text", "category": "usb-id", "confidence": "high", "count": 1},
                ],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "extract-hid-js-v2.1.23.json").write_text(
        json.dumps(
            {
                "operation": "extract-hid-js",
                "root": "assets-v2.1.23",
                "summary": {"command_categories": {"sync": 2, "telemetry": 1}},
                "product_ids": [{"vid_pid": "0416:a103", "count": 1}],
                "warnings": ["wired HID only"],
            }
        ),
        encoding="utf-8",
    )

    payload = _run_probe("artifact-evidence-matrix", str(tmp_path))
    versions = {item["version"]: item for item in payload["versions"]}

    assert payload["operation"] == "artifact-evidence-matrix"
    assert payload["version_count"] == 2
    assert payload["summary"]["high_priority_capture_versions"] == ["v2.0.34"]
    assert versions["v2.0.34"]["assessment"] == "rf-usb-protocol-lead"
    assert versions["v2.0.34"]["rf_static_labels"] == {
        "RF receiver VID:PID text": 2,
        "RF sender VID:PID text": 2,
    }
    assert versions["v2.1.23"]["assessment"] == "wired-hid-fan-lead"
    assert versions["v2.1.23"]["hid_command_categories"] == {"sync": 2, "telemetry": 1}


def test_probe_extract_hid_js_reports_structured_commands(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    (root / "index.js").write_text(
        "const H1=[41219,41221];"
        "function loadSLV2FanHidDevices(){loadHidDevices(H1)}"
        "syncSLV2FanRpm2MotherBoard(){x.write(t,[[224,16,98,r?17:16]])}"
        "checkingSLV2FanControllerRpm(){x.write(t,[[224,80,0,0,0,0,0,0]]);d.getInputReport(224,65)}",
        encoding="utf-8",
    )

    payload = _run_probe("extract-hid-js", str(root))
    command_names = {item["name"] for item in payload["command_templates"]}

    assert payload["operation"] == "extract-hid-js"
    assert payload["js_file_count"] == 1
    assert payload["matched_file_count"] == 1
    assert {item["decimal"] for item in payload["product_ids"]} == {41219, 41221}
    assert "motherboard-rpm-sync" in command_names
    assert "fan-rpm-poll" in command_names
    assert "fan-input-report" in command_names


def test_probe_extract_wireless_js_reports_usb_ipc_clues(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    (root / "wireless.js").write_text(
        "const sender='0416:8040';"
        "const receiver='0416:8041';"
        "const label='L-Wireless';"
        "ipcRenderer.send('message-queue', 'scan');"
        "pipe.writeSettings('WirelessConfig', label);",
        encoding="utf-8",
    )

    payload = _run_probe("extract-wireless-js", str(root))
    clue_names = {item["name"] for item in payload["clues"]}

    assert payload["operation"] == "extract-wireless-js"
    assert payload["js_file_count"] == 1
    assert payload["matched_file_count"] == 1
    assert {"rf-sender-usb-id", "rf-receiver-usb-id", "l-wireless-product"} <= clue_names
    assert payload["summary"]["categories"]["usb-id"] == 2
    assert payload["summary"]["categories"]["ipc"] == 3


def test_probe_analyze_changelog_reports_wireless_versions(tmp_path):
    changelog = tmp_path / "l3-changelog.html"
    changelog.write_text(
        """
        <h2>L3 v2.1.23</h2>
        <p>發表於：04-24-2026</p>
        <p>增加對 H2 OLED 曲面螢幕的支持</p>
        <h2>L3 v2.0.34</h2>
        <p>發表於：09-19-2025</p>
        <a href="https://example.test/L-Connect-v2.0.34.exe">下載</a>
        <p>修正了無線風扇 MB RPM 同步無法運作的問題。</p>
        """,
        encoding="utf-8",
    )

    payload = _run_probe("analyze-changelog", str(changelog), "--top", "1")

    assert payload["operation"] == "analyze-changelog"
    assert payload["entry_count"] == 2
    assert payload["wireless_entry_count"] == 1
    assert payload["summary"]["top_versions"] == ["2.0.34"]
    assert payload["top_entries"][0]["download_urls"] == ["https://example.test/L-Connect-v2.0.34.exe"]
    assert {"rpm-pwm", "wireless-fan"} <= set(payload["top_entries"][0]["matched_keywords"])


def test_probe_diff_snapshots_reports_changed_pwm(tmp_path):
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(
        json.dumps({"operation": "live-list", "devices": [_device_payload(pwm=[80, 90, 100, 110])]})
        + "\n",
        encoding="utf-8",
    )
    after_path.write_text(
        json.dumps({"operation": "live-list", "devices": [_device_payload(pwm=[120, 120, 120, 120])]})
        + "\n",
        encoding="utf-8",
    )

    payload = _run_probe("diff-snapshots", str(before_path), str(after_path))

    assert payload["operation"] == "diff-snapshots"
    assert payload["summary"]["changed_count"] == 1
    assert payload["changed"][0]["mac"] == "aa:bb:cc:dd:ee:ff"
    assert payload["changed"][0]["changes"][0] == {
        "field": "pwm_values",
        "before": [80, 90, 100, 110],
        "after": [120, 120, 120, 120],
    }


def test_probe_analyze_log_reports_live_write_effect(tmp_path):
    log_path = tmp_path / "live-pwm.json"
    log_path.write_text(
        json.dumps(
            {
                "operation": "live-pwm",
                "target": "aa:bb:cc:dd:ee:ff",
                "pwm_values": [120, 120, 120, 120],
                "packets_written": 4,
                "before": _device_payload(pwm=[80, 90, 100, 110]),
                "after": {
                    "device_count": 1,
                    "devices": [_device_payload(pwm=[120, 120, 120, 120])],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = _run_probe("analyze-log", str(log_path))

    assert payload["operation"] == "analyze-log"
    assert payload["source_operation"] == "live-pwm"
    assert payload["target_found_after"] is True
    assert payload["snapshot_changed"] is True
    assert payload["likely_effective"] is True
    assert payload["expected_effect"]["available"] is True
    assert payload["expected_effect"]["matched"] is True
    assert payload["expected_effect"]["checks"][0] == {
        "field": "pwm_values",
        "expected": [120, 120, 120, 120],
        "actual": [120, 120, 120, 120],
        "matched": True,
    }
    assert payload["changes"][0]["field"] == "pwm_values"
    assert payload["notes"] == ["After snapshot matches the expected command effect."]


def test_probe_analyze_log_flags_expected_effect_mismatch(tmp_path):
    log_path = tmp_path / "live-pwm.json"
    log_path.write_text(
        json.dumps(
            {
                "operation": "live-pwm",
                "target": "aa:bb:cc:dd:ee:ff",
                "pwm_values": [120, 120, 120, 120],
                "packets_written": 4,
                "before": _device_payload(pwm=[80, 90, 100, 110]),
                "after": {
                    "device_count": 1,
                    "devices": [_device_payload(pwm=[100, 100, 100, 100])],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = _run_probe("analyze-log", str(log_path))

    assert payload["snapshot_changed"] is True
    assert payload["likely_effective"] is False
    assert payload["expected_effect"]["matched"] is False
    assert payload["expected_effect"]["checks"][0] == {
        "field": "pwm_values",
        "expected": [120, 120, 120, 120],
        "actual": [100, 100, 100, 100],
        "matched": False,
    }
    assert payload["notes"] == ["After snapshot changed but does not match the expected command effect."]


def test_probe_summarize_experiments_groups_live_logs(tmp_path):
    pwm_path = tmp_path / "live-pwm.json"
    rgb_path = tmp_path / "nested" / "live-rgb.json"
    validation_error_path = tmp_path / "live-lcd-info.json"
    readonly_summary_path = tmp_path / "validate-readonly.json"
    safe_summary_path = tmp_path / "safe-pwm-experiment.json"
    packet_compare_path = tmp_path / "linux-control-packet-compare-live-pwm.json"
    invalid_path = tmp_path / "broken.json"
    pwm_path.write_text(
        json.dumps(
            {
                "operation": "live-pwm",
                "target": "aa:bb:cc:dd:ee:ff",
                "packets_written": 4,
                "before": _device_payload(pwm=[80, 90, 100, 110]),
                "after": {
                    "device_count": 1,
                    "devices": [_device_payload(pwm=[120, 120, 120, 120])],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rgb_path.parent.mkdir()
    rgb_path.write_text(
        json.dumps(
            {
                "operation": "live-rgb",
                "target": "aa:bb:cc:dd:ee:ff",
                "packets_written": 16,
                "before": _device_payload(),
                "after": {
                    "device_count": 1,
                    "devices": [_device_payload()],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    validation_error_path.write_text(
        json.dumps(
            {
                "operation": "live-lcd-info",
                "status": "error",
                "error": "USB read failed",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    readonly_summary_path.write_text(
        json.dumps(
            {
                "operation": "validate-readonly",
                "output_dir": str(tmp_path),
                "step_count": 3,
                "ok_count": 3,
                "error_count": 0,
                "steps": [
                    {"name": "scan", "status": "ok", "path": str(tmp_path / "scan.json"), "error": ""},
                    {"name": "live-list", "status": "ok", "path": str(tmp_path / "live-list.json"), "error": ""},
                    {"name": "live-master", "status": "ok", "path": str(tmp_path / "live-master.json"), "error": ""},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    safe_summary_path.write_text(
        json.dumps(
            {
                "operation": "safe-pwm-experiment",
                "target": "AA:BB:CC:DD:EE:FF",
                "output_dir": str(tmp_path),
                "packets_written": 4,
                "likely_effective": True,
                "summary": {
                    "operation_stats": {
                        "live-pwm": {
                            "changed_count": 1,
                            "expected_matched_count": 1,
                        }
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    packet_compare_path.write_text(
        json.dumps(
            {
                "operation": "linux-control-packet-compare",
                "schema_version": "lianli-linux-control-packet-compare/v1",
                "control_operation": "live-pwm",
                "target_id": "aa:bb:cc:dd:ee:ff@ch8/rx3",
                "observed_capture": str(tmp_path / "l-connect-pwm.json"),
                "status": "matched",
                "matched": True,
                "exact_match": True,
                "semantic_match": True,
                "match_diagnostics": {"status": "exact-match"},
                "target_state": {
                    "status": "capture-backed-target-state",
                    "missing_packet_fields": [],
                    "placeholder_fields": [],
                    "snapshot_metadata_available": True,
                    "snapshot_state_available": True,
                    "raw_hex_available": True,
                    "live_snapshot_refresh_required": True,
                },
                "write_gate": {
                    "status": "pass",
                    "allows_guarded_write": True,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    invalid_path.write_text("{not json", encoding="utf-8")

    payload = _run_probe("summarize-experiments", str(tmp_path))

    assert payload["operation"] == "summarize-experiments"
    assert payload["json_file_count"] == 7
    assert payload["analyzed_live_log_count"] == 2
    assert payload["invalid_file_count"] == 1
    assert payload["receiver_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert payload["field_change_counts"] == {"pwm_values": 1}
    assert payload["operation_stats"]["live-pwm"]["changed_count"] == 1
    assert payload["operation_stats"]["live-rgb"]["unchanged_count"] == 1
    assert payload["validation_errors"][0]["error"] == "USB read failed"
    assert payload["validation_runs"][0]["status"] == "ok"
    assert payload["validation_runs"][0]["ok_count"] == 3
    assert payload["safe_experiment_runs"][0]["operation"] == "safe-pwm-experiment"
    assert payload["safe_experiment_runs"][0]["target"] == "aa:bb:cc:dd:ee:ff"
    assert payload["safe_experiment_runs"][0]["likely_effective"] is True
    assert payload["packet_compare_runs"][0]["control_operation"] == "live-pwm"
    assert payload["packet_compare_runs"][0]["schema_version"] == "lianli-linux-control-packet-compare/v1"
    assert payload["packet_compare_runs"][0]["write_gate_status"] == "pass"
    assert payload["packet_compare_runs"][0]["allows_guarded_write"] is True
    assert payload["packet_compare_runs"][0]["target_state_status"] == "capture-backed-target-state"
    assert payload["packet_compare_runs"][0]["target_state_placeholder_fields"] == []
    assert payload["packet_compare_runs"][0]["target_state_snapshot_state_available"] is True
    assert payload["packet_compare_runs"][0]["target_state_raw_hex_available"] is True
    assert payload["hardware_validation"] == {
        "status": "errors",
        "validation_run_count": 1,
        "validation_error_count": 1,
        "safe_experiment_count": 1,
        "safe_effective_count": 1,
        "targets": ["aa:bb:cc:dd:ee:ff"],
    }


def test_probe_summarize_experiments_reports_receiver_validation_bundle(tmp_path):
    bundle_path = tmp_path / "receiver-validation-bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "operation": "receiver-validation-bundle",
                "output_dir": str(tmp_path),
                "capture_dir": str(tmp_path / "captures"),
                "experiment_dir": str(tmp_path / "experiments"),
                "step_count": 7,
                "ok_count": 7,
                "error_count": 0,
                "ready_for_guarded_write": True,
                "write_gate_status": "write-enabled",
                "write_gate_next_command": "",
                "steps": [
                    {"name": "scan", "status": "ok", "path": str(tmp_path / "scan.json"), "error": ""},
                    {"name": "readiness", "status": "ok", "path": str(tmp_path / "readiness.json"), "error": ""},
                    {"name": "live-list", "status": "ok", "path": str(tmp_path / "live-list.json"), "error": ""},
                    {"name": "live-master", "status": "ok", "path": str(tmp_path / "live-master.json"), "error": ""},
                    {"name": "validate-readonly", "status": "ok", "path": str(tmp_path / "validate-readonly.json"), "error": ""},
                    {"name": "preflight", "status": "ok", "path": str(tmp_path / "preflight.json"), "error": ""},
                    {"name": "write-gate", "status": "ok", "path": str(tmp_path / "write-gate.json"), "error": ""},
                ],
                "next_steps": [
                    "Choose one target MAC from live-list.json.",
                    "Run safe-pwm-experiment with --confirm WRITE-LIANLI and a conservative PWM value.",
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = _run_probe("summarize-experiments", str(tmp_path))

    assert payload["operation"] == "summarize-experiments"
    assert payload["json_file_count"] == 1
    assert payload["receiver_validation_bundles"] == [
        {
            "path": str(bundle_path),
            "output_dir": str(tmp_path),
            "capture_dir": str(tmp_path / "captures"),
            "experiment_dir": str(tmp_path / "experiments"),
            "step_count": 7,
            "ok_count": 7,
            "error_count": 0,
            "status": "write-gate-ready",
            "ready_for_guarded_write": True,
            "write_gate_status": "write-enabled",
            "write_gate_next_command": "",
            "next_steps": [
                "Choose one target MAC from live-list.json.",
                "Run safe-pwm-experiment with --confirm WRITE-LIANLI and a conservative PWM value.",
            ],
            "steps": [
                {"name": "scan", "status": "ok", "path": str(tmp_path / "scan.json"), "error": ""},
                {"name": "readiness", "status": "ok", "path": str(tmp_path / "readiness.json"), "error": ""},
                {"name": "live-list", "status": "ok", "path": str(tmp_path / "live-list.json"), "error": ""},
                {"name": "live-master", "status": "ok", "path": str(tmp_path / "live-master.json"), "error": ""},
                {"name": "validate-readonly", "status": "ok", "path": str(tmp_path / "validate-readonly.json"), "error": ""},
                {"name": "preflight", "status": "ok", "path": str(tmp_path / "preflight.json"), "error": ""},
                {"name": "write-gate", "status": "ok", "path": str(tmp_path / "write-gate.json"), "error": ""},
            ],
        }
    ]
    assert payload["hardware_validation"] == {
        "status": "readonly-and-write-gate-ready",
        "validation_run_count": 0,
        "validation_error_count": 0,
        "safe_experiment_count": 0,
        "safe_effective_count": 0,
        "targets": [],
        "receiver_validation_bundle_count": 1,
        "receiver_validation_bundle_error_count": 0,
        "write_gate_ready_count": 1,
        "write_gate_statuses": ["write-enabled"],
    }


def test_probe_summarize_experiments_recommends_one_safe_pwm_after_write_gate(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)

    payload = _run_probe("summarize-experiments", str(tmp_path))
    action = payload["receiver_control_next_action"]

    assert payload["receiver_identity_consistency"]["status"] == "consistent"
    assert action["status"] == "ready-for-single-target-safe-pwm"
    assert action["can_run_safe_pwm"] is True
    assert action["receiver_identity_status"] == "consistent"
    assert action["candidate_count"] == 1
    assert action["ready_candidate_count"] == 1
    assert action["candidates"][0]["mac"] == "aa:bb:cc:dd:ee:ff"
    assert action["candidates"][0]["status"] == "ready"
    assert action["candidates"][0]["safe_pwm_argv"] == [
        "safe-pwm-experiment",
        "--mac",
        "aa:bb:cc:dd:ee:ff",
        "--pwm",
        "120",
        "--output-dir",
        str(tmp_path / "experiments" / "safe-pwm-aa-bb-cc-dd-ee-ff"),
        "--confirm",
        "WRITE-LIANLI",
    ]
    assert action["recommended_commands"][0] == action["candidates"][0]["safe_pwm_command"]
    assert action["recommended_commands"][1] == f"python tools/lianli_wireless_probe.py summarize-experiments {tmp_path}"


def test_probe_summarize_experiments_blocks_safe_pwm_on_identity_conflict(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    readonly_live_list = tmp_path / "readonly" / "live-list.json"
    payload = json.loads(readonly_live_list.read_text(encoding="utf-8"))
    payload["devices"][0]["mac"] = "11:22:33:44:55:66"
    readonly_live_list.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    summary = _run_probe("summarize-experiments", str(tmp_path))
    action = summary["receiver_control_next_action"]

    assert summary["receiver_identity_consistency"]["status"] == "conflict"
    assert action["status"] == "receiver-identity-conflict"
    assert action["can_run_safe_pwm"] is False
    assert action["receiver_identity_status"] == "conflict"
    assert not any("safe-pwm-experiment" in command for command in action["recommended_commands"])
    assert "receiver-validation-bundle" in action["recommended_commands"][0]


def test_probe_summarize_experiments_blocks_safe_pwm_on_incomplete_identity(tmp_path):
    (tmp_path / "receiver-validation-bundle.json").write_text(
        json.dumps(
            {
                "operation": "receiver-validation-bundle",
                "output_dir": str(tmp_path),
                "capture_dir": str(tmp_path / "captures"),
                "experiment_dir": str(tmp_path / "experiments"),
                "step_count": 7,
                "ok_count": 7,
                "error_count": 0,
                "ready_for_guarded_write": True,
                "write_gate_status": "write-enabled",
                "steps": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "live-list.json").write_text(
        json.dumps({"operation": "live-list", "device_count": 1, "devices": [_device_payload()]}) + "\n",
        encoding="utf-8",
    )

    summary = _run_probe("summarize-experiments", str(tmp_path))
    action = summary["receiver_control_next_action"]

    assert summary["receiver_identity_consistency"]["status"] == "incomplete"
    assert action["status"] == "needs-receiver-identity-validation"
    assert action["can_run_safe_pwm"] is False
    assert action["receiver_identity_status"] == "incomplete"
    assert not any("safe-pwm-experiment" in command for command in action["recommended_commands"])


def test_probe_receiver_evidence_report_audits_saved_bundle(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)

    payload = _run_probe("receiver-evidence-report", str(tmp_path))
    manifest_by_path = {item["relative_path"]: item for item in payload["file_manifest"]}
    write_set = payload["write_evidence_sets"][0]

    assert payload["operation"] == "receiver-evidence-report"
    assert payload["status"] == "ready-for-single-target-safe-pwm"
    assert payload["required_missing_count"] == 0
    assert payload["required_present_count"] == 12
    assert payload["json_file_count"] == 12
    assert payload["write_evidence_set_count"] == 1
    assert payload["write_evidence_complete_count"] == 0
    assert payload["write_evidence_partial_count"] == 0
    assert write_set["relative_dir"] == "experiments/safe-pwm-aa-bb-cc-dd-ee-ff"
    assert write_set["status"] == "missing"
    assert write_set["sources"] == ["recommended-command"]
    assert payload["write_files"][0]["relative_path"] == "experiments/safe-pwm-aa-bb-cc-dd-ee-ff/live-list-before.json"
    assert payload["hardware_validation"]["status"] == "readonly-and-write-gate-ready"
    assert payload["receiver_control_next_action"]["status"] == "ready-for-single-target-safe-pwm"
    identity = payload["receiver_identity_consistency"]
    assert identity["status"] == "consistent"
    assert identity["receiver_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert identity["master_query_macs"] == ["10:20:30:40:50:60"]
    assert identity["snapshot_master_macs"] == ["10:20:30:40:50:60"]
    assert identity["conflicts"] == []
    assert payload["recommended_commands"][0].startswith(
        "python tools/lianli_wireless_probe.py safe-pwm-experiment --mac aa:bb:cc:dd:ee:ff"
    )
    assert payload["recommended_commands"][-1] == f"python tools/lianli_wireless_probe.py receiver-evidence-report {tmp_path}"
    assert manifest_by_path["live-list.json"]["operation"] == "live-list"
    assert len(manifest_by_path["live-list.json"]["sha256"]) == 64
    assert all(item["exists"] for item in payload["required_files"])


def test_probe_receiver_evidence_report_flags_receiver_identity_conflict(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    readonly_live_list = tmp_path / "readonly" / "live-list.json"
    payload = json.loads(readonly_live_list.read_text(encoding="utf-8"))
    payload["devices"][0]["fan_count"] = 4
    readonly_live_list.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    report = _run_probe("receiver-evidence-report", str(tmp_path))
    identity = report["receiver_identity_consistency"]

    assert report["status"] == "receiver-identity-conflict"
    assert identity["status"] == "conflict"
    assert identity["receiver_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert identity["conflicts"][0]["type"] == "snapshot-field-mismatch"
    assert identity["conflicts"][0]["field"] == "fan_count"
    assert identity["conflicts"][0]["values"] == ["3", "4"]
    assert report["recommended_commands"][0].startswith(
        "python tools/lianli_wireless_probe.py --save-json"
    )
    assert "receiver-validation-bundle" in report["recommended_commands"][0]


def test_probe_receiver_evidence_report_flags_snapshot_receiver_set_mismatch(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    readonly_live_list = tmp_path / "readonly" / "live-list.json"
    payload = json.loads(readonly_live_list.read_text(encoding="utf-8"))
    payload["devices"][0]["mac"] = "11:22:33:44:55:66"
    readonly_live_list.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    report = _run_probe("receiver-evidence-report", str(tmp_path))
    identity = report["receiver_identity_consistency"]
    conflicts = {item["type"]: item for item in identity["conflicts"]}

    assert report["status"] == "receiver-identity-conflict"
    assert identity["status"] == "conflict"
    assert identity["receiver_macs"] == ["11:22:33:44:55:66", "aa:bb:cc:dd:ee:ff"]
    assert conflicts["snapshot-receiver-set-mismatch"]["sources"] == [
        {"relative_path": "live-list.json", "device_macs": ["aa:bb:cc:dd:ee:ff"]},
        {"relative_path": "readonly/live-list.json", "device_macs": ["11:22:33:44:55:66"]},
    ]


def test_probe_receiver_evidence_report_flags_master_query_source_mismatch(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    readonly_master = tmp_path / "readonly" / "live-master.json"
    payload = json.loads(readonly_master.read_text(encoding="utf-8"))
    payload["master_mac"] = "66:55:44:33:22:11"
    readonly_master.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    report = _run_probe("receiver-evidence-report", str(tmp_path))
    identity = report["receiver_identity_consistency"]
    conflicts = {item["type"]: item for item in identity["conflicts"]}

    assert report["status"] == "receiver-identity-conflict"
    assert identity["status"] == "conflict"
    assert identity["master_query_macs"] == ["10:20:30:40:50:60", "66:55:44:33:22:11"]
    assert conflicts["master-query-source-mismatch"]["sources"] == [
        {"relative_path": "live-master.json", "master_mac": "10:20:30:40:50:60"},
        {"relative_path": "readonly/live-master.json", "master_mac": "66:55:44:33:22:11"},
    ]


def test_probe_receiver_evidence_report_flags_master_query_mismatch(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    live_master = tmp_path / "live-master.json"
    payload = json.loads(live_master.read_text(encoding="utf-8"))
    payload["master_mac"] = "66:55:44:33:22:11"
    live_master.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    report = _run_probe("receiver-evidence-report", str(tmp_path))
    identity = report["receiver_identity_consistency"]
    conflicts = {item["type"]: item for item in identity["conflicts"]}

    assert report["status"] == "receiver-identity-conflict"
    assert identity["status"] == "conflict"
    assert identity["master_query_macs"] == ["66:55:44:33:22:11"]
    assert identity["snapshot_master_macs"] == ["10:20:30:40:50:60"]
    assert conflicts["master-query-mismatch"]["master_query_values"] == ["66:55:44:33:22:11"]
    assert conflicts["master-query-mismatch"]["snapshot_values"] == ["10:20:30:40:50:60"]


def test_probe_receiver_evidence_report_detects_completed_safe_pwm_directory(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    _write_receiver_safe_pwm_evidence(tmp_path)

    payload = _run_probe("receiver-evidence-report", str(tmp_path))
    write_set = payload["write_evidence_sets"][0]

    assert payload["status"] == "write-evidence-needs-observation"
    assert payload["write_evidence_complete_count"] == 1
    assert payload["write_evidence_partial_count"] == 0
    assert payload["write_evidence_confirmed_count"] == 0
    assert payload["visual_observation_missing_count"] == 1
    assert write_set["status"] == "complete"
    assert write_set["control_proof_status"] == "machine-evidence-complete-needs-observation"
    assert write_set["visual_observation"]["status"] == "missing"
    assert write_set["relative_dir"] == "experiments/safe-pwm-aa-bb-cc-dd-ee-ff"
    assert write_set["sources"] == ["existing-files", "recommended-command"]
    assert write_set["target"] == "aa:bb:cc:dd:ee:ff"
    assert write_set["pwm_values"] == [120, 120, 120, 120]
    assert write_set["packets_written"] == 4
    assert write_set["likely_effective"] is True
    assert write_set["machine_consistency"]["status"] == "consistent"
    assert all(item["exists"] for item in write_set["files"])
    observation_commands = [command for command in payload["recommended_commands"] if "receiver-observation" in command]
    assert observation_commands
    assert "--target aa:bb:cc:dd:ee:ff" in observation_commands[0]
    assert "--observed-pwm 120" in observation_commands[0]


def test_probe_receiver_evidence_report_detects_completed_safe_sync_directory(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    _write_receiver_safe_sync_evidence(tmp_path)

    payload = _run_probe("receiver-evidence-report", str(tmp_path))
    write_set = next(item for item in payload["write_evidence_sets"] if item["write_operation"] == "live-pwm-sync")

    assert payload["status"] == "write-evidence-needs-observation"
    assert payload["write_evidence_complete_count"] == 1
    assert write_set["status"] == "complete"
    assert write_set["write_kind"] == "motherboard-pwm-sync"
    assert write_set["write_file"] == "live-pwm-sync.json"
    assert write_set["analysis_file"] == "analyze-live-pwm-sync.json"
    assert write_set["control_proof_status"] == "machine-evidence-complete-needs-observation"
    assert write_set["target"] == "aa:bb:cc:dd:ee:ff"
    assert write_set["pwm_values"] == [6, 6, 6, 6]
    assert write_set["machine_consistency"]["status"] == "consistent"
    assert {item["relative_path"] for item in write_set["files"]} == {
        "experiments/safe-sync-aa-bb-cc-dd-ee-ff/live-list-before.json",
        "experiments/safe-sync-aa-bb-cc-dd-ee-ff/live-pwm-sync.json",
        "experiments/safe-sync-aa-bb-cc-dd-ee-ff/live-list-after.json",
        "experiments/safe-sync-aa-bb-cc-dd-ee-ff/analyze-live-pwm-sync.json",
        "experiments/safe-sync-aa-bb-cc-dd-ee-ff/summary.json",
    }
    observation_commands = [command for command in payload["recommended_commands"] if "receiver-observation" in command]
    assert observation_commands
    assert "--target aa:bb:cc:dd:ee:ff" in observation_commands[0]
    assert "--observed-pwm 6" in observation_commands[0]


def test_probe_receiver_evidence_report_detects_completed_safe_mirror_directory(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    _write_receiver_safe_mirror_evidence(tmp_path)

    payload = _run_probe("receiver-evidence-report", str(tmp_path))
    write_set = next(item for item in payload["write_evidence_sets"] if item["write_operation"] == "live-pwm-mirror")

    assert payload["status"] == "write-evidence-needs-observation"
    assert payload["write_evidence_complete_count"] == 1
    assert write_set["status"] == "complete"
    assert write_set["write_kind"] == "motherboard-pwm-mirror"
    assert write_set["write_file"] == "live-pwm-mirror.json"
    assert write_set["analysis_file"] == "analyze-live-pwm-mirror.json"
    assert write_set["target"] == "aa:bb:cc:dd:ee:ff"
    assert write_set["pwm_values"] == [127, 127, 127, 127]
    assert write_set["machine_consistency"]["status"] == "consistent"
    observation_commands = [command for command in payload["recommended_commands"] if "receiver-observation" in command]
    assert observation_commands
    assert "--observed-pwm 127" in observation_commands[0]


def test_receiver_evidence_write_set_prefers_mirror_prefix_over_direct_pwm(tmp_path):
    from usb9_lcd.lianli.analysis import receiver_evidence_write_set

    output_dir = tmp_path / "experiments" / "safe-pwm-mirror-aa-bb-cc-dd-ee-ff"

    write_set = receiver_evidence_write_set(tmp_path, output_dir, {"recommended-command"})

    assert write_set["status"] == "missing"
    assert write_set["write_operation"] == "live-pwm-mirror"
    assert write_set["write_file"] == "live-pwm-mirror.json"
    assert any(item["relative_path"].endswith("analyze-live-pwm-mirror.json") for item in write_set["files"])


def test_probe_receiver_evidence_report_flags_safe_sync_machine_pwm_mismatch(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    output_dir = _write_receiver_safe_sync_evidence(tmp_path)
    after_path = output_dir / "live-list-after.json"
    after = json.loads(after_path.read_text(encoding="utf-8"))
    after["devices"][0]["pwm_values"] = [100, 100, 100, 100]
    after_path.write_text(json.dumps(after) + "\n", encoding="utf-8")

    payload = _run_probe("receiver-evidence-report", str(tmp_path))
    write_set = next(item for item in payload["write_evidence_sets"] if item["write_operation"] == "live-pwm-sync")

    assert payload["status"] == "write-evidence-machine-conflict"
    assert payload["write_evidence_machine_conflict_count"] == 1
    assert write_set["control_proof_status"] == "machine-evidence-conflict"
    assert write_set["machine_consistency"]["status"] == "conflict"
    assert any(
        check["name"] == "after-pwm-values" and check["status"] == "mismatch"
        for check in write_set["machine_consistency"]["checks"]
    )


def test_probe_receiver_evidence_report_observation_command_keeps_pwm_tuple(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    output_dir = _write_receiver_safe_pwm_evidence(tmp_path)
    live_pwm_path = output_dir / "live-pwm.json"
    live_pwm = json.loads(live_pwm_path.read_text(encoding="utf-8"))
    live_pwm["pwm_values"] = [96, 112, 128, 144]
    live_pwm_path.write_text(json.dumps(live_pwm) + "\n", encoding="utf-8")
    after_path = output_dir / "live-list-after.json"
    after = json.loads(after_path.read_text(encoding="utf-8"))
    after["devices"][0]["pwm_values"] = [96, 112, 128, 144]
    after_path.write_text(json.dumps(after) + "\n", encoding="utf-8")

    payload = _run_probe("receiver-evidence-report", str(tmp_path))
    observation_commands = [command for command in payload["recommended_commands"] if "receiver-observation" in command]

    assert observation_commands
    assert "--target aa:bb:cc:dd:ee:ff" in observation_commands[0]
    assert "--observed-pwm 96,112,128,144" in observation_commands[0]


def test_probe_receiver_evidence_report_flags_machine_target_missing_after(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    output_dir = _write_receiver_safe_pwm_evidence(tmp_path)
    after_path = output_dir / "live-list-after.json"
    after = json.loads(after_path.read_text(encoding="utf-8"))
    after["devices"][0]["mac"] = "11:22:33:44:55:66"
    after_path.write_text(json.dumps(after) + "\n", encoding="utf-8")
    _run_probe(
        "--save-json",
        str(output_dir / "observation.json"),
        "receiver-observation",
        str(output_dir),
        "--effect",
        "changed",
        "--target",
        "aa:bb:cc:dd:ee:ff",
        "--observed-pwm",
        "120",
    )

    payload = _run_probe("receiver-evidence-report", str(tmp_path))
    write_set = next(item for item in payload["write_evidence_sets"] if item["status"] == "complete")

    assert payload["status"] == "write-evidence-machine-conflict"
    assert payload["write_evidence_confirmed_count"] == 0
    assert payload["write_evidence_machine_conflict_count"] == 1
    assert write_set["control_proof_status"] == "machine-evidence-conflict"
    assert write_set["machine_consistency"]["status"] == "conflict"
    assert any(check["name"] == "after-target" and check["status"] == "missing" for check in write_set["machine_consistency"]["checks"])


def test_probe_receiver_evidence_report_flags_machine_analysis_mismatch(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    output_dir = _write_receiver_safe_pwm_evidence(tmp_path)
    analysis_path = output_dir / "analyze-live-pwm.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["target"] = "11:22:33:44:55:66"
    analysis["likely_effective"] = False
    analysis["expected_effect"] = {"available": True, "matched": False}
    analysis_path.write_text(json.dumps(analysis) + "\n", encoding="utf-8")

    payload = _run_probe("receiver-evidence-report", str(tmp_path))
    write_set = payload["write_evidence_sets"][0]

    assert payload["status"] == "write-evidence-machine-conflict"
    assert payload["write_evidence_machine_conflict_count"] == 1
    assert write_set["control_proof_status"] == "machine-evidence-conflict"
    assert write_set["machine_consistency"]["status"] == "conflict"
    assert any(check["name"] == "analysis-target" and check["status"] == "mismatch" for check in write_set["machine_consistency"]["checks"])
    assert any(check["name"] == "analysis-expected-effect" and check["status"] == "mismatch" for check in write_set["machine_consistency"]["checks"])


def test_probe_receiver_observation_records_visual_result(tmp_path):
    output_dir = _write_receiver_safe_pwm_evidence(tmp_path)

    payload = _run_probe(
        "receiver-observation",
        str(output_dir),
        "--effect",
        "changed",
        "--observed-pwm",
        "120",
        "--observed-rpm",
        "audibly faster",
        "--operator",
        "tester",
        "--observed-at",
        "2026-05-26T20:00:00+08:00",
        "--note",
        "fan speed visibly changed after guarded PWM write",
    )

    assert payload["operation"] == "receiver-observation"
    assert payload["experiment_dir"] == str(output_dir)
    assert payload["target"] == "aa:bb:cc:dd:ee:ff"
    assert payload["effect"] == "changed"
    assert payload["observed_pwm"] == "120"
    assert payload["observed_rpm"] == "audibly faster"
    assert payload["operator"] == "tester"
    assert payload["observed_at"] == "2026-05-26T20:00:00+08:00"
    assert payload["machine_evidence_status"] == "complete"
    assert payload["notes"] == ["fan speed visibly changed after guarded PWM write"]


def test_probe_receiver_evidence_report_uses_visual_observation(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    output_dir = _write_receiver_safe_pwm_evidence(tmp_path)
    _run_probe(
        "--save-json",
        str(output_dir / "observation.json"),
        "receiver-observation",
        str(output_dir),
        "--effect",
        "changed",
        "--note",
        "fan speed visibly changed after guarded PWM write",
    )

    payload = _run_probe("receiver-evidence-report", str(tmp_path))
    write_set = payload["write_evidence_sets"][0]

    assert payload["status"] == "write-evidence-confirmed"
    assert payload["write_evidence_confirmed_count"] == 1
    assert payload["visual_observation_missing_count"] == 0
    assert write_set["control_proof_status"] == "visually-confirmed"
    assert write_set["visual_observation"]["status"] == "confirmed"
    assert write_set["visual_observation"]["effect"] == "changed"
    assert write_set["visual_observation"]["notes"] == ["fan speed visibly changed after guarded PWM write"]
    assert write_set["observation_consistency"]["status"] == "consistent"
    assert write_set["observation_consistency"]["target_status"] == "match"
    assert write_set["observation_consistency"]["pwm_status"] == "not-provided"


def test_probe_receiver_evidence_report_flags_contradicting_observation(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    output_dir = _write_receiver_safe_pwm_evidence(tmp_path)
    _run_probe(
        "--save-json",
        str(output_dir / "observation.json"),
        "receiver-observation",
        str(output_dir),
        "--effect",
        "unchanged",
        "--note",
        "no visible or audible fan response",
    )

    payload = _run_probe("receiver-evidence-report", str(tmp_path))
    write_set = payload["write_evidence_sets"][0]

    assert payload["status"] == "write-evidence-observation-conflict"
    assert payload["write_evidence_confirmed_count"] == 0
    assert payload["write_evidence_conflict_count"] == 1
    assert payload["visual_observation_missing_count"] == 0
    assert write_set["control_proof_status"] == "visual-observation-conflicts"
    assert write_set["visual_observation"]["status"] == "contradicts"
    assert write_set["visual_observation"]["effect"] == "unchanged"
    assert not any("receiver-observation" in command for command in payload["recommended_commands"])


def test_probe_receiver_evidence_report_flags_target_mismatch_observation(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    output_dir = _write_receiver_safe_pwm_evidence(tmp_path)
    _run_probe(
        "--save-json",
        str(output_dir / "observation.json"),
        "receiver-observation",
        str(output_dir),
        "--effect",
        "changed",
        "--target",
        "ff:ee:dd:cc:bb:aa",
        "--note",
        "different target was watched by mistake",
    )

    payload = _run_probe("receiver-evidence-report", str(tmp_path))
    write_set = payload["write_evidence_sets"][0]
    consistency = write_set["observation_consistency"]

    assert payload["status"] == "write-evidence-observation-conflict"
    assert payload["write_evidence_confirmed_count"] == 0
    assert payload["write_evidence_conflict_count"] == 1
    assert write_set["control_proof_status"] == "visual-observation-conflicts"
    assert write_set["visual_observation"]["status"] == "confirmed"
    assert consistency["status"] == "conflict"
    assert consistency["target_status"] == "mismatch"
    assert consistency["machine_target"] == "aa:bb:cc:dd:ee:ff"
    assert consistency["observed_target"] == "ff:ee:dd:cc:bb:aa"


def test_probe_receiver_evidence_report_flags_pwm_mismatch_observation(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    output_dir = _write_receiver_safe_pwm_evidence(tmp_path)
    _run_probe(
        "--save-json",
        str(output_dir / "observation.json"),
        "receiver-observation",
        str(output_dir),
        "--effect",
        "changed",
        "--observed-pwm",
        "80",
        "--note",
        "wrong PWM value was recorded",
    )

    payload = _run_probe("receiver-evidence-report", str(tmp_path))
    write_set = payload["write_evidence_sets"][0]
    consistency = write_set["observation_consistency"]

    assert payload["status"] == "write-evidence-observation-conflict"
    assert payload["write_evidence_confirmed_count"] == 0
    assert payload["write_evidence_conflict_count"] == 1
    assert write_set["control_proof_status"] == "visual-observation-conflicts"
    assert write_set["visual_observation"]["status"] == "confirmed"
    assert consistency["status"] == "conflict"
    assert consistency["target_status"] == "match"
    assert consistency["pwm_status"] == "mismatch"
    assert consistency["machine_pwm_values"] == [120, 120, 120, 120]
    assert consistency["observed_pwm_values"] == [80]


def test_probe_receiver_evidence_report_flags_invalid_observation(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    output_dir = _write_receiver_safe_pwm_evidence(tmp_path)
    (output_dir / "observation.json").write_text(
        json.dumps({"operation": "wrong-observation", "effect": "changed"}) + "\n",
        encoding="utf-8",
    )

    payload = _run_probe("receiver-evidence-report", str(tmp_path))
    write_set = payload["write_evidence_sets"][0]

    assert payload["status"] == "write-evidence-invalid-observation"
    assert payload["visual_observation_invalid_count"] == 1
    assert payload["visual_observation_missing_count"] == 0
    assert write_set["control_proof_status"] == "invalid-observation"
    assert write_set["visual_observation"]["status"] == "invalid"


def test_probe_receiver_evidence_report_flags_unclear_observation(tmp_path):
    _write_receiver_evidence_required_payloads(tmp_path)
    output_dir = _write_receiver_safe_pwm_evidence(tmp_path)
    _run_probe(
        "--save-json",
        str(output_dir / "observation.json"),
        "receiver-observation",
        str(output_dir),
        "--effect",
        "unclear",
        "--note",
        "case noise made the result ambiguous",
    )

    payload = _run_probe("receiver-evidence-report", str(tmp_path))
    write_set = payload["write_evidence_sets"][0]

    assert payload["status"] == "write-evidence-unclear-observation"
    assert payload["visual_observation_unclear_count"] == 1
    assert payload["visual_observation_missing_count"] == 0
    assert write_set["control_proof_status"] == "needs-clear-observation"
    assert write_set["visual_observation"]["status"] == "unclear"


def test_probe_live_pwm_requires_confirmation(monkeypatch):
    module = _load_probe_module()

    with pytest.raises(module.LianLiWirelessError, match="live writes require"):
        module.main(
            [
                "live-pwm",
                "--mac",
                "aa:bb:cc:dd:ee:ff",
                "--pwm",
                "120",
                "--confirm",
                "wrong",
            ]
        )


def test_probe_live_pwm_writes_one_target_and_rereads(monkeypatch, capsys):
    module = _load_probe_module()
    calls: dict[str, object] = {}

    class FakeBackend:
        def __init__(self):
            self.list_count = 0

        def list_devices(self):
            self.list_count += 1
            return WirelessSnapshot(raw=b"snapshot", devices=[_bound_device()])

        def send_pwm(self, target, pwm_values):
            calls["target"] = target.mac
            calls["pwm_values"] = pwm_values
            return 4

    backend = FakeBackend()
    monkeypatch.setattr(module, "create_pyusb_backend", lambda: backend)

    assert module.main(
        [
            "live-pwm",
            "--mac",
            "aa:bb:cc:dd:ee:ff",
            "--pwm",
            "120",
            "--confirm",
            module.WRITE_CONFIRM_TOKEN,
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "live-pwm"
    assert payload["packets_written"] == 4
    assert payload["after"]["device_count"] == 1
    assert backend.list_count == 2
    assert calls["target"] == "aa:bb:cc:dd:ee:ff"
    assert calls["pwm_values"] == (120, 120, 120, 120)


def test_probe_live_rainbow_writes_one_target_and_rereads(monkeypatch, capsys):
    module = _load_probe_module()
    calls: dict[str, object] = {}

    class FakeBackend:
        def __init__(self):
            self.list_count = 0

        def list_devices(self):
            self.list_count += 1
            return WirelessSnapshot(raw=b"snapshot", devices=[_bound_device()])

        def send_rainbow_rgb(
            self,
            target,
            *,
            frame_count,
            interval_ms,
            effect_index,
            led_count,
        ):
            calls["target"] = target.mac
            calls["frame_count"] = frame_count
            calls["interval_ms"] = interval_ms
            calls["effect_index"] = effect_index
            calls["led_count"] = led_count
            return 44

    backend = FakeBackend()
    monkeypatch.setattr(module, "create_pyusb_backend", lambda: backend)

    assert module.main(
        [
            "live-rainbow",
            "--mac",
            "aa:bb:cc:dd:ee:ff",
            "--frame-count",
            "3",
            "--interval-ms",
            "40",
            "--led-count",
            "132",
            "--effect-index",
            "2",
            "--confirm",
            module.WRITE_CONFIRM_TOKEN,
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "live-rainbow"
    assert payload["packets_written"] == 44
    assert payload["frame_count"] == 3
    assert payload["interval_ms"] == 40
    assert payload["led_count"] == 132
    assert payload["effect_index"] == 2
    assert payload["after"]["device_count"] == 1
    assert backend.list_count == 2
    assert calls == {
        "target": "aa:bb:cc:dd:ee:ff",
        "frame_count": 3,
        "interval_ms": 40,
        "effect_index": 2,
        "led_count": 132,
    }


def test_probe_safe_pwm_experiment_saves_before_after_analysis(monkeypatch, tmp_path, capsys):
    module = _load_probe_module()
    calls: dict[str, object] = {}

    class FakeBackend:
        def __init__(self):
            self.list_count = 0

        def list_devices(self):
            self.list_count += 1
            if self.list_count == 1:
                return WirelessSnapshot(raw=b"before", devices=[_bound_device()])
            return WirelessSnapshot(
                raw=b"after",
                devices=[
                    WirelessDeviceInfo(
                        mac="aa:bb:cc:dd:ee:ff",
                        master_mac="10:20:30:40:50:60",
                        channel=8,
                        rx_type=3,
                        device_type=2,
                        fan_count=3,
                        pwm_values=(120, 120, 120, 120),
                        fan_rpm=(1300, 1500, 0, 0),
                        command_sequence=8,
                        raw=bytes(42),
                    )
                ],
            )

        def send_pwm(self, target, pwm_values):
            calls["target"] = target.mac
            calls["pwm_values"] = pwm_values
            return 4

    backend = FakeBackend()
    monkeypatch.setattr(module, "create_pyusb_backend", lambda: backend)

    assert module.main(
        [
            "safe-pwm-experiment",
            "--mac",
            "aa:bb:cc:dd:ee:ff",
            "--pwm",
            "120",
            "--output-dir",
            str(tmp_path),
            "--confirm",
            module.WRITE_CONFIRM_TOKEN,
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "safe-pwm-experiment"
    assert payload["likely_effective"] is True
    assert backend.list_count == 2
    assert calls["target"] == "aa:bb:cc:dd:ee:ff"
    assert calls["pwm_values"] == (120, 120, 120, 120)
    for name in (
        "live-list-before.json",
        "live-pwm.json",
        "live-list-after.json",
        "analyze-live-pwm.json",
        "summary.json",
    ):
        assert (tmp_path / name).exists()
    analysis = json.loads((tmp_path / "analyze-live-pwm.json").read_text(encoding="utf-8"))
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert analysis["expected_effect"]["matched"] is True
    assert analysis["changes"][0]["field"] == "pwm_values"
    assert summary["operation_stats"]["live-pwm"]["changed_count"] == 1
    assert summary["operation_stats"]["live-pwm"]["expected_matched_count"] == 1


def test_probe_safe_rgb_experiment_marks_visual_confirmation(monkeypatch, tmp_path, capsys):
    module = _load_probe_module()
    calls: dict[str, object] = {}

    class FakeBackend:
        def __init__(self):
            self.list_count = 0

        def list_devices(self):
            self.list_count += 1
            return WirelessSnapshot(raw=b"snapshot", devices=[_bound_device()])

        def send_static_rgb(self, target, color, *, effect_index, led_count=None):
            calls["target"] = target.mac
            calls["color"] = color
            calls["effect_index"] = effect_index
            calls["led_count"] = led_count
            return 16

    backend = FakeBackend()
    monkeypatch.setattr(module, "create_pyusb_backend", lambda: backend)

    assert module.main(
        [
            "safe-rgb-experiment",
            "--mac",
            "aa:bb:cc:dd:ee:ff",
            "--color",
            "0,0,0",
            "--effect-index",
            "1",
            "--output-dir",
            str(tmp_path),
            "--confirm",
            module.WRITE_CONFIRM_TOKEN,
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "safe-rgb-experiment"
    assert payload["likely_effective"] is False
    assert payload["visual_confirmation_required"] is True
    assert payload["led_count"] == 132
    assert calls == {
        "target": "aa:bb:cc:dd:ee:ff",
        "color": (0, 0, 0),
        "effect_index": 1,
        "led_count": None,
    }
    for name in (
        "live-list-before.json",
        "live-rgb.json",
        "live-list-after.json",
        "analyze-live-rgb.json",
        "summary.json",
    ):
        assert (tmp_path / name).exists()
    analysis = json.loads((tmp_path / "analyze-live-rgb.json").read_text(encoding="utf-8"))
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert analysis["visual_confirmation_required"] is True
    assert analysis["notes"] == ["RGB writes may not change receiver snapshot fields; confirm visually."]
    assert summary["operation_stats"]["live-rgb"]["unchanged_count"] == 1


def test_probe_safe_rainbow_experiment_marks_visual_confirmation(monkeypatch, tmp_path, capsys):
    module = _load_probe_module()
    calls: dict[str, object] = {}

    class FakeBackend:
        def __init__(self):
            self.list_count = 0

        def list_devices(self):
            self.list_count += 1
            return WirelessSnapshot(raw=b"snapshot", devices=[_bound_device()])

        def send_rainbow_rgb(
            self,
            target,
            *,
            frame_count,
            interval_ms,
            effect_index,
            led_count,
        ):
            calls["target"] = target.mac
            calls["frame_count"] = frame_count
            calls["interval_ms"] = interval_ms
            calls["effect_index"] = effect_index
            calls["led_count"] = led_count
            return 44

    backend = FakeBackend()
    monkeypatch.setattr(module, "create_pyusb_backend", lambda: backend)

    assert module.main(
        [
            "safe-rainbow-experiment",
            "--mac",
            "aa:bb:cc:dd:ee:ff",
            "--frame-count",
            "3",
            "--interval-ms",
            "40",
            "--led-count",
            "132",
            "--effect-index",
            "2",
            "--output-dir",
            str(tmp_path),
            "--confirm",
            module.WRITE_CONFIRM_TOKEN,
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "safe-rainbow-experiment"
    assert payload["likely_effective"] is False
    assert payload["visual_confirmation_required"] is True
    assert payload["frame_count"] == 3
    assert payload["interval_ms"] == 40
    assert payload["led_count"] == 132
    assert payload["effect_index"] == 2
    assert calls == {
        "target": "aa:bb:cc:dd:ee:ff",
        "frame_count": 3,
        "interval_ms": 40,
        "effect_index": 2,
        "led_count": 132,
    }
    for name in (
        "live-list-before.json",
        "live-rainbow.json",
        "live-list-after.json",
        "analyze-live-rainbow.json",
        "summary.json",
    ):
        assert (tmp_path / name).exists()
    analysis = json.loads((tmp_path / "analyze-live-rainbow.json").read_text(encoding="utf-8"))
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert analysis["visual_confirmation_required"] is True
    assert analysis["notes"] == ["RGB writes may not change receiver snapshot fields; confirm visually."]
    assert summary["operation_stats"]["live-rainbow"]["unchanged_count"] == 1


def test_probe_safe_sync_experiment_saves_magic_pwm_analysis(monkeypatch, tmp_path, capsys):
    module = _load_probe_module()
    calls: dict[str, object] = {}

    class FakeBackend:
        def __init__(self):
            self.list_count = 0

        def list_devices(self):
            self.list_count += 1
            if self.list_count == 1:
                return WirelessSnapshot(raw=b"before", devices=[_bound_device()])
            return WirelessSnapshot(
                raw=b"after",
                devices=[
                    WirelessDeviceInfo(
                        mac="aa:bb:cc:dd:ee:ff",
                        master_mac="10:20:30:40:50:60",
                        channel=8,
                        rx_type=3,
                        device_type=2,
                        fan_count=3,
                        pwm_values=(6, 6, 6, 6),
                        fan_rpm=(1234, 1500, 0, 0),
                        command_sequence=8,
                        raw=bytes(42),
                    )
                ],
            )

        def send_motherboard_pwm_sync(self, target, *, enable, fallback_pwm):
            calls["target"] = target.mac
            calls["enable"] = enable
            calls["fallback_pwm"] = fallback_pwm
            return 4

    backend = FakeBackend()
    monkeypatch.setattr(module, "create_pyusb_backend", lambda: backend)

    assert module.main(
        [
            "safe-sync-experiment",
            "--mac",
            "aa:bb:cc:dd:ee:ff",
            "--output-dir",
            str(tmp_path),
            "--confirm",
            module.WRITE_CONFIRM_TOKEN,
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "safe-sync-experiment"
    assert payload["enabled"] is True
    assert payload["expected_pwm_values"] == [6, 6, 6, 6]
    assert payload["likely_effective"] is True
    assert calls == {
        "target": "aa:bb:cc:dd:ee:ff",
        "enable": True,
        "fallback_pwm": 100,
    }
    for name in (
        "live-list-before.json",
        "live-pwm-sync.json",
        "live-list-after.json",
        "analyze-live-pwm-sync.json",
        "summary.json",
    ):
        assert (tmp_path / name).exists()
    analysis = json.loads((tmp_path / "analyze-live-pwm-sync.json").read_text(encoding="utf-8"))
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert analysis["expected_effect"]["matched"] is True
    assert analysis["changes"][0] == {
        "field": "pwm_values",
        "before": [80, 90, 100, 110],
        "after": [6, 6, 6, 6],
    }
    assert summary["operation_stats"]["live-pwm-sync"]["changed_count"] == 1


def test_probe_safe_pwm_mirror_experiment_uses_motherboard_pwm(monkeypatch, tmp_path, capsys):
    module = _load_probe_module()
    calls: dict[str, object] = {}

    class FakeBackend:
        def __init__(self):
            self.list_count = 0

        def list_devices(self):
            self.list_count += 1
            if self.list_count == 1:
                return WirelessSnapshot(raw=bytes.fromhex("10000a0a"), devices=[_bound_device()])
            return WirelessSnapshot(
                raw=bytes.fromhex("10000a0a"),
                devices=[
                    WirelessDeviceInfo(
                        mac="aa:bb:cc:dd:ee:ff",
                        master_mac="10:20:30:40:50:60",
                        channel=8,
                        rx_type=3,
                        device_type=2,
                        fan_count=3,
                        pwm_values=(127, 127, 127, 127),
                        fan_rpm=(1234, 1500, 0, 0),
                        command_sequence=8,
                        raw=bytes(42),
                    )
                ],
            )

        def send_motherboard_pwm_mirror(self, target, motherboard_pwm):
            calls["target"] = target.mac
            calls["motherboard_pwm"] = motherboard_pwm
            return 4

    backend = FakeBackend()
    monkeypatch.setattr(module, "create_pyusb_backend", lambda: backend)

    assert module.main(
        [
            "safe-pwm-mirror-experiment",
            "--mac",
            "aa:bb:cc:dd:ee:ff",
            "--output-dir",
            str(tmp_path),
            "--confirm",
            module.WRITE_CONFIRM_TOKEN,
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "safe-pwm-mirror-experiment"
    assert payload["motherboard_pwm"] == 127
    assert payload["pwm_values"] == [127, 127, 127, 127]
    assert payload["likely_effective"] is True
    assert calls == {"target": "aa:bb:cc:dd:ee:ff", "motherboard_pwm": 127}
    for name in (
        "live-list-before.json",
        "live-pwm-mirror.json",
        "live-list-after.json",
        "analyze-live-pwm-mirror.json",
        "summary.json",
    ):
        assert (tmp_path / name).exists()
    analysis = json.loads((tmp_path / "analyze-live-pwm-mirror.json").read_text(encoding="utf-8"))
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert analysis["expected_effect"]["matched"] is True
    assert summary["operation_stats"]["live-pwm-mirror"]["changed_count"] == 1


def test_probe_safe_bind_experiment_infers_master_and_saves_analysis(monkeypatch, tmp_path, capsys):
    module = _load_probe_module()
    calls: dict[str, object] = {}

    class FakeBackend:
        def __init__(self):
            self.list_count = 0

        def list_devices(self):
            self.list_count += 1
            if self.list_count == 1:
                return WirelessSnapshot(raw=b"before", devices=[_unbound_device()])
            return WirelessSnapshot(raw=b"after", devices=[_bound_device()])

        def query_master_mac(self, *, channel):
            calls["query_channel"] = channel
            return "10:20:30:40:50:60", channel

        def send_bind(self, target, *, master_mac, rx_type, channel):
            calls["target"] = target.mac
            calls["master_mac"] = master_mac
            calls["rx_type"] = rx_type
            calls["channel"] = channel
            return 4

    backend = FakeBackend()
    monkeypatch.setattr(module, "create_pyusb_backend", lambda: backend)

    assert module.main(
        [
            "safe-bind-experiment",
            "--mac",
            "aa:bb:cc:dd:ee:ff",
            "--rx-type",
            "3",
            "--output-dir",
            str(tmp_path),
            "--confirm",
            module.WRITE_CONFIRM_TOKEN,
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "safe-bind-experiment"
    assert payload["master_mac"] == "10:20:30:40:50:60"
    assert payload["rx_type"] == 3
    assert payload["likely_effective"] is True
    assert calls == {
        "query_channel": 8,
        "target": "aa:bb:cc:dd:ee:ff",
        "master_mac": "10:20:30:40:50:60",
        "rx_type": 3,
        "channel": None,
    }
    for name in (
        "live-list-before.json",
        "live-bind.json",
        "live-list-after.json",
        "analyze-live-bind.json",
        "summary.json",
    ):
        assert (tmp_path / name).exists()
    analysis = json.loads((tmp_path / "analyze-live-bind.json").read_text(encoding="utf-8"))
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert analysis["expected_effect"]["matched"] is True
    changed_fields = [change["field"] for change in analysis["changes"]]
    assert "master_mac" in changed_fields
    assert "is_bound" in changed_fields
    assert summary["operation_stats"]["live-bind"]["changed_count"] == 1


def test_probe_safe_unbind_experiment_saves_unbound_analysis(monkeypatch, tmp_path, capsys):
    module = _load_probe_module()
    calls: dict[str, object] = {}

    class FakeBackend:
        def __init__(self):
            self.list_count = 0

        def list_devices(self):
            self.list_count += 1
            if self.list_count == 1:
                return WirelessSnapshot(raw=b"before", devices=[_bound_device()])
            return WirelessSnapshot(raw=b"after", devices=[_unbound_device()])

        def send_unbind(self, target, *, channel):
            calls["target"] = target.mac
            calls["channel"] = channel
            return 4

    backend = FakeBackend()
    monkeypatch.setattr(module, "create_pyusb_backend", lambda: backend)

    assert module.main(
        [
            "safe-unbind-experiment",
            "--mac",
            "aa:bb:cc:dd:ee:ff",
            "--output-dir",
            str(tmp_path),
            "--confirm",
            module.WRITE_CONFIRM_TOKEN,
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "safe-unbind-experiment"
    assert payload["likely_effective"] is True
    assert calls == {"target": "aa:bb:cc:dd:ee:ff", "channel": None}
    for name in (
        "live-list-before.json",
        "live-unbind.json",
        "live-list-after.json",
        "analyze-live-unbind.json",
        "summary.json",
    ):
        assert (tmp_path / name).exists()
    analysis = json.loads((tmp_path / "analyze-live-unbind.json").read_text(encoding="utf-8"))
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert analysis["expected_effect"]["matched"] is True
    changed_fields = [change["field"] for change in analysis["changes"]]
    assert "master_mac" in changed_fields
    assert "is_bound" in changed_fields
    assert summary["operation_stats"]["live-unbind"]["changed_count"] == 1


def test_probe_live_bind_can_infer_master_mac(monkeypatch, capsys):
    module = _load_probe_module()
    calls: dict[str, object] = {}

    class FakeBackend:
        def __init__(self):
            self.list_count = 0

        def list_devices(self):
            self.list_count += 1
            return WirelessSnapshot(raw=b"snapshot", devices=[_unbound_device()])

        def query_master_mac(self, *, channel):
            calls["query_channel"] = channel
            return "10:20:30:40:50:60", channel

        def send_bind(self, target, *, master_mac, rx_type, channel):
            calls["target"] = target.mac
            calls["master_mac"] = master_mac
            calls["rx_type"] = rx_type
            calls["channel"] = channel
            return 4

    backend = FakeBackend()
    monkeypatch.setattr(module, "create_pyusb_backend", lambda: backend)

    assert module.main(
        [
            "live-bind",
            "--mac",
            "aa:bb:cc:dd:ee:ff",
            "--rx-type",
            "3",
            "--confirm",
            module.WRITE_CONFIRM_TOKEN,
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "live-bind"
    assert payload["master_mac"] == "10:20:30:40:50:60"
    assert payload["packets_written"] == 4
    assert backend.list_count == 2
    assert calls == {
        "query_channel": 8,
        "target": "aa:bb:cc:dd:ee:ff",
        "master_mac": "10:20:30:40:50:60",
        "rx_type": 3,
        "channel": None,
    }


def test_probe_pwm_dry_run_outputs_rf_packets():
    payload = _run_probe("dry-run-pwm", "--pwm", "120")

    assert payload["operation"] == "dry-run-pwm"
    assert payload["pwm_values"] == [120, 120, 120, 120]
    assert payload["packet_count"] == 4
    assert payload["packet_size"] == 64
    assert payload["first_packet_hex"].startswith("100008031210")


def test_probe_pwm_dry_run_accepts_explicit_pwm_tuple():
    payload = _run_probe("dry-run-pwm", "--pwm-values", "80,90,100,110")

    assert payload["operation"] == "dry-run-pwm"
    assert payload["pwm_values"] == [80, 90, 100, 110]
    assert "505a646e" in payload["first_packet_hex"]


def test_probe_pwm_sync_dry_run_outputs_sync_magic_pwm():
    payload = _run_probe("dry-run-pwm-sync")

    assert payload["operation"] == "dry-run-pwm-sync"
    assert payload["enabled"] is True
    assert payload["packet_count"] == 4
    assert payload["first_packet_hex"].startswith("100008031210")
    assert "06060606" in payload["first_packet_hex"]


def test_probe_pwm_mirror_dry_run_decodes_snapshot_pwm():
    payload = _run_probe("dry-run-pwm-mirror", "--snapshot-hex", "10 00 0a 0a")

    assert payload["operation"] == "dry-run-pwm-mirror"
    assert payload["motherboard_pwm"] == 127
    assert payload["pwm_values"] == [127, 127, 127, 127]
    assert payload["packet_count"] == 4
    assert "7f7f7f7f" in payload["first_packet_hex"]


def test_probe_live_pwm_mirror_reads_snapshot_and_writes_direct_pwm(monkeypatch, capsys):
    module = _load_probe_module()
    calls: dict[str, object] = {}

    class FakeBackend:
        def __init__(self):
            self.list_count = 0

        def list_devices(self):
            self.list_count += 1
            if self.list_count == 1:
                return WirelessSnapshot(raw=bytes.fromhex("10000a0a"), devices=[_bound_device()])
            return WirelessSnapshot(
                raw=bytes.fromhex("10000a0a"),
                devices=[
                    WirelessDeviceInfo(
                        mac="aa:bb:cc:dd:ee:ff",
                        master_mac="10:20:30:40:50:60",
                        channel=8,
                        rx_type=3,
                        device_type=2,
                        fan_count=3,
                        pwm_values=(127, 127, 127, 127),
                        fan_rpm=(1234, 1500, 0, 0),
                        command_sequence=8,
                        raw=bytes(42),
                    )
                ],
            )

        def send_motherboard_pwm_mirror(self, target, motherboard_pwm):
            calls["target"] = target.mac
            calls["motherboard_pwm"] = motherboard_pwm
            return 4

    backend = FakeBackend()
    monkeypatch.setattr(module, "create_pyusb_backend", lambda: backend)

    assert module.main(
        [
            "live-pwm-mirror",
            "--mac",
            "aa:bb:cc:dd:ee:ff",
            "--confirm",
            module.WRITE_CONFIRM_TOKEN,
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "live-pwm-mirror"
    assert payload["motherboard_pwm"] == 127
    assert payload["pwm_values"] == [127, 127, 127, 127]
    assert payload["packets_written"] == 4
    assert calls == {"target": "aa:bb:cc:dd:ee:ff", "motherboard_pwm": 127}
    assert backend.list_count == 2


def test_probe_bind_dry_run_outputs_unbound_rf_header():
    payload = _run_probe("dry-run-bind", "--master-mac", "10:20:30:40:50:60", "--rx-type", "3")

    assert payload["operation"] == "dry-run-bind"
    assert payload["packet_count"] == 4
    assert payload["packet_size"] == 64
    assert payload["first_packet_hex"].startswith(
        "100008001210aabbccddeeff102030405060030801"
    )


def test_probe_unbind_dry_run_outputs_zero_master_payload():
    payload = _run_probe("dry-run-unbind")

    assert payload["operation"] == "dry-run-unbind"
    assert payload["packet_count"] == 4
    assert payload["packet_size"] == 64
    assert payload["first_packet_hex"].startswith(
        "100008031210aabbccddeeff000000000000000800"
    )


def test_probe_rgb_dry_run_outputs_turn_off_packets():
    payload = _run_probe("dry-run-rgb", "--color", "0,0,0")

    assert payload["operation"] == "dry-run-rgb"
    assert payload["color"] == [0, 0, 0]
    assert payload["led_count"] == 132
    assert payload["packet_count"] == (4 + RGB_FIRST_PAYLOAD_REPEAT_COUNT - 1) * 4
    assert payload["packet_size"] == 64
    assert payload["first_packet_hex"].startswith("100008031220")


def test_probe_rgb_dry_run_accepts_led_count_override():
    payload = _run_probe("dry-run-rgb", "--color", "0,0,0", "--led-count", "12")

    assert payload["operation"] == "dry-run-rgb"
    assert payload["led_count"] == 12
    assert payload["packet_count"] == (2 + RGB_FIRST_PAYLOAD_REPEAT_COUNT - 1) * 4


def test_probe_rainbow_dry_run_outputs_multi_frame_packets():
    payload = _run_probe("dry-run-rainbow", "--frame-count", "3", "--interval-ms", "40")

    assert payload["operation"] == "dry-run-rainbow"
    assert payload["led_count"] == 132
    assert payload["frame_count"] == 3
    assert payload["interval_ms"] == 40
    assert payload["packet_count"] > (4 + RGB_FIRST_PAYLOAD_REPEAT_COUNT - 1) * 4
    assert payload["packet_size"] == 64
    assert payload["first_packet_hex"].startswith("100008031220")


def test_probe_rainbow_dry_run_accepts_led_count_override():
    payload = _run_probe("dry-run-rainbow", "--frame-count", "2", "--led-count", "12")

    assert payload["operation"] == "dry-run-rainbow"
    assert payload["led_count"] == 12
    assert payload["frame_count"] == 2
    assert payload["packet_count"] > 0


def test_probe_analyze_capture_decodes_saved_rf_packets(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend

    target = _bound_device()
    packets = LianLiWirelessBackend().build_motherboard_pwm_sync_packets(target)
    capture_path = tmp_path / "l-connect-capture.txt"
    capture_path.write_text("\n".join(packet.hex() for packet in packets), encoding="utf-8")

    payload = _run_probe("analyze-capture", str(capture_path))

    assert payload["operation"] == "analyze-capture"
    assert payload["packet_count"] == 4
    assert payload["summary"]["kinds"] == {"rf-chunk": 4}
    assert payload["summary"]["rf_operations"] == {"live-pwm-sync": 1}
    assert payload["rf_frames"][0]["operation"] == "live-pwm-sync"
    assert payload["rf_frames"][0]["pwm_values"] == [6, 6, 6, 6]


def test_probe_capture_replay_plan_outputs_copy_paste_commands(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend

    target = _bound_device()
    packets = [_snapshot_payload_with_motherboard_pwm(10, 10)]
    packets.extend(LianLiWirelessBackend().build_motherboard_pwm_mirror_packets(target, 127))
    capture_path = tmp_path / "l-connect-capture.txt"
    capture_path.write_text("\n".join(packet.hex() for packet in packets), encoding="utf-8")

    payload = _run_probe("capture-replay-plan", str(capture_path))

    assert payload["operation"] == "capture-replay-plan"
    assert payload["replay_hint_count"] == 1
    assert payload["operation_counts"] == {"live-pwm-mirror": 1}
    assert "dry-run-pwm-mirror" in payload["dry_run_commands"][0]
    assert "--motherboard-pwm 127" in payload["dry_run_commands"][0]
    assert f"compare-capture {capture_path} pwm-mirror" in payload["compare_capture_commands"][0]
    assert payload["items"][0]["compare_capture"]["argv"][2:5] == [
        "compare-capture",
        str(capture_path),
        "pwm-mirror",
    ]


def test_probe_capture_replay_plan_outputs_rainbow_commands(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend

    target = _bound_device()
    packets = LianLiWirelessBackend().build_rainbow_rgb_packets(
        target,
        frame_count=3,
        interval_ms=40,
        effect_index=2,
    )
    capture_path = tmp_path / "l-connect-rainbow-capture.txt"
    capture_path.write_text("\n".join(packet.hex() for packet in packets), encoding="utf-8")

    payload = _run_probe("capture-replay-plan", str(capture_path))

    assert payload["operation"] == "capture-replay-plan"
    assert payload["replay_hint_count"] == 1
    assert payload["operation_counts"] == {"live-rgb": 1}
    assert "dry-run-rainbow" in payload["dry_run_commands"][0]
    assert "--frame-count 3" in payload["dry_run_commands"][0]
    assert "--interval-ms 40" in payload["dry_run_commands"][0]
    assert "--led-count 132" in payload["dry_run_commands"][0]
    assert f"compare-capture {capture_path} rainbow" in payload["compare_capture_commands"][0]
    item = payload["items"][0]
    assert item["decoded_args"]["rainbow_generated_match"] is True
    assert item["compare_capture"]["expected_operation"] == "rainbow"
    assert item["compare_capture"]["argv"][2:5] == [
        "compare-capture",
        str(capture_path),
        "rainbow",
    ]


def test_probe_windows_capture_plan_outputs_vm_workflow(tmp_path):
    installer = tmp_path / "L-Connect-v2.1.17.exe"
    installer.write_bytes(b"installer")

    payload = _run_probe(
        "windows-capture-plan",
        "--version",
        "2.1.17",
        "--installer",
        str(installer),
        "--capture-base",
        "lianli-v2117",
    )

    assert payload["operation"] == "windows-capture-plan"
    assert payload["recommended_environment"] == "vm-usb-passthrough"
    assert payload["installer"]["exists"] is True
    assert any(target["vid_pid"] == "0416:8040" for target in payload["usb_targets"])
    assert any(scenario["id"] == "sort-quick-sync" for scenario in payload["scenarios"])
    assert any(
        "capture-protocol-report lianli-v2117-00-baseline.pcapng" in command
        for command in payload["post_capture"]["preferred_linux_flow"]
    )
    assert any(
        "capture-timeline-report lianli-v2117-00-baseline.pcapng" in command
        for command in payload["post_capture"]["preferred_linux_flow"]
    )


def test_probe_usb_capture_readiness_reports_local_l_wireless_devices(tmp_path):
    for name, vid, pid in (
        ("1-1", "0416", "8040"),
        ("1-2", "0416", "8041"),
    ):
        device_root = tmp_path / "bus" / "usb" / "devices" / name
        device_root.mkdir(parents=True)
        (device_root / "idVendor").write_text(vid, encoding="utf-8")
        (device_root / "idProduct").write_text(pid, encoding="utf-8")
        (device_root / "manufacturer").write_text("LIAN LI", encoding="utf-8")
        (device_root / "product").write_text("SLV3", encoding="utf-8")
        (device_root / "serial").write_text(name, encoding="utf-8")
        (device_root / "busnum").write_text("001", encoding="utf-8")
        (device_root / "devnum").write_text("002", encoding="utf-8")

    payload = _run_probe("usb-capture-readiness", "--sys-root", str(tmp_path))

    assert payload["operation"] == "usb-capture-readiness"
    assert payload["status"] == "linux-live-ready"
    assert payload["known_device_count"] == 2
    assert any(target["vid_pid"] == "0416:8040" and target["present"] for target in payload["targets"])
    assert any("live-master" in command for command in payload["linux_live_commands"])
    assert any("windows-capture-plan" in command for command in payload["windows_capture_commands"])


def test_probe_summarize_captures_ranks_capture_directory(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend

    target = _bound_device()
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    pwm_capture = capture_dir / "direct-pwm.txt"
    pwm_capture.write_text(
        "\n".join(
            packet.hex()
            for packet in LianLiWirelessBackend().build_pwm_packets(target, [120])
        ),
        encoding="utf-8",
    )
    (capture_dir / "empty.txt").write_text("not a capture", encoding="utf-8")

    payload = _run_probe("summarize-captures", str(capture_dir))

    assert payload["operation"] == "summarize-captures"
    assert payload["file_count"] == 2
    assert payload["candidate_count"] == 1
    assert payload["top_candidates"][0]["path"] == "direct-pwm.txt"
    assert payload["top_candidates"][0]["rf_operations"] == {"live-pwm": 1}
    assert any(
        "capture-protocol-report" in command
        for command in payload["top_candidates"][0]["recommended_commands"]
    )
    assert any(
        "capture-timeline-report" in command
        for command in payload["top_candidates"][0]["recommended_commands"]
    )


def test_probe_capture_transport_report_summarizes_export_fields(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend

    target = _bound_device()
    packets = LianLiWirelessBackend().build_pwm_packets(target, [120])
    capture_path = tmp_path / "usbpcap-export.json"
    capture_path.write_text(
        json.dumps(
            [
                {"_source": {"layers": {"frame": {"frame.number": "1"}, "usb.capdata": packets[0].hex(":")}}},
                {"_source": {"layers": {"frame": {"frame.number": "2"}, "usbhid.data": "00:" + packets[1].hex(":")}}},
            ]
        ),
        encoding="utf-8",
    )

    payload = _run_probe("capture-transport-report", str(capture_path))

    assert payload["operation"] == "capture-transport-report"
    assert payload["packet_candidate_count"] == 2
    assert payload["protocol_candidate_count"] == 2
    assert payload["field_counts"] == {"usb.capdata": 1, "usbhid.data": 1}
    assert payload["first_protocol_candidates"][0]["frame_number"] == "1"
    assert any("capture-protocol-report" in command for command in payload["recommended_commands"])
    assert any("capture-timeline-report" in command for command in payload["recommended_commands"])


def test_probe_protocol_signatures_outputs_catalog():
    payload = _run_probe("protocol-signatures", "--led-count", "12", "--rainbow-frames", "3", "--interval-ms", "40")
    items = {item["operation"]: item for item in payload["items"]}

    assert payload["operation"] == "protocol-signatures"
    assert payload["summary"]["signature_count"] == len(items)
    assert payload["usb_targets"]["sender"]["vid_pid"] == "0416:8040"
    assert payload["usb_targets"]["receiver"]["vid_pid"] == "0416:8041"
    assert "pwm-sync-enable" in items
    assert "rgb-off" in items
    assert items["pwm-sync-enable"]["summary"]["rf_operations"] == {"live-pwm-sync": 1}
    assert items["rgb-off"]["expected_operation"] == "rgb"
    assert "compare-capture '<capture>' pwm-sync" in items["pwm-sync-enable"]["commands"]["compare_capture"]["command"]
    assert any("dry-run-rainbow" in command for command in payload["dry_run_commands"])


def test_probe_capture_signature_match_matches_catalog_entries(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend, build_master_query_request, build_wireless_list_request

    target = _bound_device()
    packets = [
        build_wireless_list_request(),
        build_master_query_request(8),
        *LianLiWirelessBackend().build_motherboard_pwm_sync_packets(target),
    ]
    capture_path = tmp_path / "l-connect-sync.txt"
    capture_path.write_text("\n".join(packet.hex() for packet in packets), encoding="utf-8")

    payload = _run_probe(
        "capture-signature-match",
        str(capture_path),
        "--led-count",
        "12",
        "--rainbow-frames",
        "3",
        "--interval-ms",
        "40",
    )
    items = {item["operation"]: item for item in payload["items"]}

    assert payload["operation"] == "capture-signature-match"
    assert payload["matched_operations"] == [
        "receiver-list-request",
        "master-query-request",
        "pwm-sync-enable",
    ]
    assert items["pwm-sync-enable"]["semantic_match"] is True
    assert items["pwm"]["matched"] is False
    assert str(capture_path) in items["pwm-sync-enable"]["commands"]["compare_capture"]["command"]


def test_probe_capture_signature_match_reports_shape_match_for_real_mac(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend

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
    capture_path = tmp_path / "real-mac-sync.txt"
    capture_path.write_text("\n".join(packet.hex() for packet in packets), encoding="utf-8")

    payload = _run_probe("capture-signature-match", str(capture_path))
    items = {item["operation"]: item for item in payload["items"]}

    assert payload["matched_operations"] == ["pwm-sync-enable"]
    assert items["pwm-sync-enable"]["shape_match"] is True
    assert items["pwm-sync-enable"]["semantic_match"] is False
    assert items["pwm-sync-enable"]["shape"]["matches"][0]["target_mac"] == "de:ad:be:ef:00:01"
    observed_compare = items["pwm-sync-enable"]["observed_commands"]["compare_capture_commands"][0]
    assert "--mac de:ad:be:ef:00:01" in observed_compare
    assert "--master-mac 01:02:03:04:05:06" in observed_compare
    assert payload["matched_commands"] == [observed_compare]


def test_probe_capture_signature_match_reports_observed_direct_pwm_tuple(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend

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
    capture_path = tmp_path / "real-direct-pwm.txt"
    capture_path.write_text("\n".join(packet.hex() for packet in packets), encoding="utf-8")

    payload = _run_probe("capture-signature-match", str(capture_path))
    items = {item["operation"]: item for item in payload["items"]}

    assert payload["matched_operations"] == ["pwm"]
    assert items["pwm"]["shape_match"] is True
    assert items["pwm"]["shape"]["matches"][0]["pwm_values"] == [77, 88, 99, 111]
    observed_compare = items["pwm"]["observed_commands"]["compare_capture_commands"][0]
    assert "--mac de:ad:be:ef:00:02" in observed_compare
    assert "--pwm-values 77,88,99,111" in observed_compare
    assert payload["matched_commands"] == [observed_compare]


def test_probe_capture_triage_report_combines_capture_evidence(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend, build_master_query_request, build_wireless_list_request

    target = _bound_device()
    packets = [
        build_wireless_list_request(),
        build_master_query_request(8),
        _snapshot_payload_with_motherboard_pwm(10, 10),
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

    payload = _run_probe(
        "capture-triage-report",
        str(capture_path),
        "--led-count",
        "12",
        "--rainbow-frames",
        "3",
        "--interval-ms",
        "40",
    )

    assert payload["operation"] == "capture-triage-report"
    assert payload["status"] == "protocol-signature-match"
    assert payload["summary"]["matched_operations"] == [
        "receiver-list-request",
        "master-query-request",
        "pwm",
    ]
    assert payload["transport"]["protocol_candidate_count"] == 7
    assert payload["transport"]["lianli_usb_targets"]["sender_seen"] is True
    assert payload["transport"]["lianli_usb_targets"]["receiver_seen"] is True
    assert payload["transport"]["usb_device_counts"] == {"0416:8040": 6, "0416:8041": 1}
    assert payload["signature_match"]["matched_signature_count"] == 3
    assert any("--pwm-values 77,88,99,111" in command for command in payload["signature_match"]["matched_commands"])
    assert payload["protocol"]["operations"]["live-pwm"]["pwm_values"] == {"77,88,99,111": 1}
    assert payload["protocol"]["operations"]["live-pwm"]["usb_device_counts"] == {"0416:8040": 4}
    assert payload["protocol"]["operations"]["live-pwm"]["usb_endpoint_counts"] == {"1/7/0x01/OUT/URB_BULK": 4}
    assert payload["summary"]["linux_live_write_target_count"] == 1
    assert payload["linux_live_write_targets"][0]["vid_pid"] == "0416:8040"
    assert payload["linux_live_write_targets"][0]["write_endpoint"] == "0x01"
    assert payload["linux_live_write_targets"][0]["target_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert "High-confidence Linux RF sender target" in payload["next_steps"][0]
    assert any("capture-protocol-report" in command for command in payload["recommended_commands"])
    assert any("capture-timeline-report" in command for command in payload["recommended_commands"])
    assert any("validate-readonly" in command for command in payload["recommended_commands"])
    assert any("safe-pwm-experiment --mac aa:bb:cc:dd:ee:ff" in command for command in payload["recommended_commands"])


def test_probe_capture_set_report_audits_planned_capture_directory(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend

    base = "lianli-v2117"
    target = _bound_device()
    packets = LianLiWirelessBackend().build_pwm_packets(target, [77, 88, 99, 111])
    _write_tshark_json_capture(
        tmp_path / f"{base}-01-direct-fan-speed.json",
        packets,
        product_ids=["0x8040"] * len(packets),
    )
    experiment_dir = tmp_path / "linux-experiments"
    _write_linux_experiment_summary_inputs(experiment_dir)

    payload = _run_probe(
        "capture-set-report",
        str(tmp_path),
        "--capture-base",
        base,
        "--experiment-dir",
        str(experiment_dir),
    )
    scenarios = {scenario["id"]: scenario for scenario in payload["scenarios"]}

    assert payload["operation"] == "capture-set-report"
    assert payload["status"] == "partial-capture-set"
    assert payload["scenario_count"] == 7
    assert payload["found_capture_count"] == 1
    assert payload["evidence_found_count"] == 1
    assert payload["status_counts"] == {"evidence-found": 1, "missing-capture": 6}
    assert payload["aggregate_rf_operations"] == {"live-pwm": 1}
    assert payload["aggregate_matched_signatures"] == {"pwm": 1}
    deltas = payload["cross_scenario_deltas"]
    assert deltas["status"] == "needs-more-captures"
    assert deltas["found_scenario_count"] == 1
    assert deltas["rf_operation_index"][0]["operation"] == "live-pwm"
    assert deltas["rf_operation_index"][0]["unique_to_scenario"] == "direct-fan-speed"
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
    assert deltas["scenario_deltas"][0]["unique_rf_operations"] == ["live-pwm"]
    assert deltas["scenario_deltas"][0]["unique_parameter_evidence"][0]["value"] == "77,88,99,111"
    assert deltas["scenario_deltas"][0]["unique_parameter_labels"] == ["live-pwm.pwm_values=77,88,99,111"]
    assert "Need at least two captured scenarios" in deltas["notes"][0]
    assert payload["linux_live_write_targets"][0]["operation"] == "live-pwm"
    assert payload["linux_live_write_targets"][0]["vid_pid"] == "0416:8040"
    assert payload["linux_live_write_targets"][0]["write_endpoint"] == "0x01"
    assert payload["linux_live_write_targets"][0]["scenario_ids"] == ["direct-fan-speed"]
    assert payload["linux_live_write_targets"][0]["target_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert payload["linux_live_write_targets"][0]["channels"] == [8]
    assert payload["linux_live_write_targets"][0]["rx_types"] == [3]
    assert payload["linux_live_write_targets"][0]["runtime_contexts"][0]["rx_type"] == 3
    assert payload["experiment_dir"] == str(experiment_dir)
    assert payload["hardware_validation"]["status"] == "readonly-and-write-observed"
    assert payload["hardware_validation"]["safe_effective_count"] == 1
    assert payload["linux_validation_plan"]["status"] == "linux-readonly-and-guarded-write-observed"
    assert payload["linux_validation_plan"]["hardware_validation"] == payload["hardware_validation"]
    matrix = {item["operation"]: item for item in payload["linux_control_matrix"]}
    assert matrix["receiver-snapshot"]["overall_status"] == "linux-validated"
    assert matrix["live-pwm"]["overall_status"] == "linux-validated"
    assert matrix["live-pwm"]["linux_target_status"] == "high-confidence"
    assert matrix["live-pwm"]["experiment_status"] == "validated"
    assert matrix["live-rgb"]["overall_status"] == "needs-windows-capture"
    contract = payload["linux_interface_contract"]
    assert contract["transport"]["sender"]["confidence"] == "high"
    assert contract["transport"]["sender"]["write_endpoint"] == "0x01"
    assert contract["protocol_delta_summary"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert contract["validated_operations"] == ["receiver-snapshot", "live-pwm"]
    contracts = {item["operation"]: item for item in contract["operation_contracts"]}
    assert contracts["live-pwm"]["backend"]["safe_cli"] == "safe-pwm-experiment"
    assert contracts["live-pwm"]["transport"]["target_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert contracts["live-pwm"]["transport"]["channels"] == [8]
    assert contracts["live-pwm"]["transport"]["runtime_contexts"][0]["channel"] == 8
    assert contracts["live-pwm"]["protocol_deltas"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert contracts["live-pwm-sync"]["backend"]["safe_cli"] == "safe-sync-experiment"
    standalone_contract = _run_probe(
        "linux-interface-contract",
        str(tmp_path),
        "--capture-base",
        base,
        "--experiment-dir",
        str(experiment_dir),
    )
    assert standalone_contract["operation"] == "linux-interface-contract"
    assert standalone_contract["schema_version"] == "lianli-linux-interface-contract/v1"
    assert standalone_contract["status"] == "linux-control-partially-validated"
    assert standalone_contract["source"]["linux_live_write_target_count"] == 1
    assert standalone_contract["validated_operations"] == ["receiver-snapshot", "live-pwm"]
    assert standalone_contract["transport"]["sender"]["write_endpoint"] == "0x01"
    assert standalone_contract["protocol_delta_summary"]["next_focus"] == [
        "compare unique RF operation(s): live-pwm with live-pwm.pwm_values=77,88,99,111"
    ]
    standalone_contracts = {
        item["operation"]: item for item in standalone_contract["operation_contracts"]
    }
    assert standalone_contracts["live-pwm"]["backend"]["safe_cli"] == "safe-pwm-experiment"
    standalone_summary = {
        item["operation"]: item for item in standalone_contract["control_matrix_summary"]
    }
    assert standalone_summary["live-pwm"]["overall_status"] == "linux-validated"
    manifest = _run_probe(
        "linux-control-manifest",
        str(tmp_path),
        "--capture-base",
        base,
        "--experiment-dir",
        str(experiment_dir),
    )
    assert manifest["operation"] == "linux-control-manifest"
    assert manifest["schema_version"] == "lianli-linux-control-manifest/v1"
    assert manifest["target_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert manifest["protocol_delta_summary"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert manifest["safety_gates"]["write_confirmation_token"] == "WRITE-LIANLI"
    manifest_operations = {item["operation"]: item for item in manifest["operations"]}
    assert manifest_operations["receiver-snapshot"]["enabled_by_default"] is True
    assert manifest_operations["live-pwm"]["capability"] == "fan-speed"
    assert manifest_operations["live-pwm"]["runtime_context"]["status"] == "complete"
    assert manifest_operations["live-pwm"]["observed_parameters"]["default_pwm_values"] == [77, 88, 99, 111]
    assert manifest_operations["live-pwm"]["protocol_deltas"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert manifest_operations["live-pwm"]["safety"]["safe_cli"] == "safe-pwm-experiment"
    assert manifest_operations["live-rgb"]["safety"]["visual_confirmation_required"] is True
    assert manifest_operations["live-rainbow"]["missing_scenarios"][0]["capture_file"] == (
        f"{base}-06-lighting-generated-rainbow.pcapng"
    )
    assert manifest["operation_map"]["live-pwm"]["evidence"]["experiment"] == "validated"
    sys_root = tmp_path / "sys"
    dev_root = tmp_path / "dev"
    _write_lianli_usb_sysfs_and_dev(sys_root, dev_root)
    preflight = _run_probe(
        "linux-control-preflight",
        str(tmp_path),
        "--capture-base",
        base,
        "--experiment-dir",
        str(experiment_dir),
        "--sys-root",
        str(sys_root),
        "--dev-root",
        str(dev_root),
    )
    assert preflight["operation"] == "linux-control-preflight"
    assert preflight["schema_version"] == "lianli-linux-control-preflight/v1"
    assert preflight["status"] == "ready-for-safe-experiments"
    assert preflight["permission_status"] == "read-write-ok"
    assert preflight["protocol_delta_summary"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert "live-pwm" in preflight["ready_operations"]
    preflight_operations = {item["operation"]: item for item in preflight["operations"]}
    assert preflight_operations["receiver-snapshot"]["required_vid_pid"] == "0416:8041"
    assert preflight_operations["live-pwm"]["preflight_status"] == "ready"
    assert preflight_operations["live-pwm"]["runtime_context"]["contexts"][0]["rx_type"] == 3
    assert preflight_operations["live-pwm"]["observed_parameters"]["default_pwm_values"] == [77, 88, 99, 111]
    assert preflight_operations["live-pwm"]["protocol_deltas"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert preflight_operations["live-pwm"]["source_capture_paths"] == [
        str(tmp_path / f"{base}-01-direct-fan-speed.json")
    ]
    assert preflight_operations["live-rgb"]["preflight_status"] == "needs-capture-evidence"
    assert preflight_operations["live-rainbow"]["missing_scenarios"][0]["id"] == "lighting-generated-rainbow"
    (experiment_dir / "linux-control-packet-compare-live-pwm.json").write_text(
        json.dumps(
            {
                "operation": "linux-control-packet-compare",
                "schema_version": "lianli-linux-control-packet-compare/v1",
                "control_operation": "live-pwm",
                "target_id": "aa:bb:cc:dd:ee:ff@ch8/rx3",
                "observed_capture": str(tmp_path / f"{base}-01-direct-fan-speed.json"),
                "status": "matched",
                "matched": True,
                "exact_match": True,
                "semantic_match": True,
                "match_diagnostics": {"status": "exact-match"},
                "write_gate": {
                    "status": "pass",
                    "allows_guarded_write": True,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    action_plan = _run_probe(
        "linux-control-action-plan",
        str(tmp_path),
        "--capture-base",
        base,
        "--experiment-dir",
        str(experiment_dir),
        "--sys-root",
        str(sys_root),
        "--dev-root",
        str(dev_root),
    )
    assert action_plan["operation"] == "linux-control-action-plan"
    assert action_plan["schema_version"] == "lianli-linux-control-action-plan/v1"
    assert action_plan["status"] == "ready-for-safe-experiments"
    assert action_plan["protocol_delta_summary"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert action_plan["guarded_write_readiness"]["status"] == "guarded-write-ready"
    assert action_plan["guarded_write_readiness"]["ready_action_ids"] == ["safe-experiment:live-pwm"]
    assert any("safe-pwm-experiment" in command for command in action_plan["next_commands"])
    cli_actions = {item["id"]: item for item in action_plan["actions"]}
    assert cli_actions["safe-experiment:live-pwm"]["status"] == "ready"
    assert cli_actions["safe-experiment:live-pwm"]["confirmation_token"] == "WRITE-LIANLI"
    assert cli_actions["safe-experiment:live-pwm"]["runtime_context"]["contexts"][0]["channel"] == 8
    assert cli_actions["safe-experiment:live-pwm"]["observed_parameters"]["default_pwm_values"] == [77, 88, 99, 111]
    assert cli_actions["safe-experiment:live-pwm"]["protocol_deltas"]["unique_parameter_labels"] == [
        "live-pwm.pwm_values=77,88,99,111"
    ]
    assert cli_actions["safe-experiment:live-pwm"]["source_capture_paths"] == [
        str(tmp_path / f"{base}-01-direct-fan-speed.json")
    ]
    pre_write = cli_actions["safe-experiment:live-pwm"]["pre_write_validation_commands"]
    assert len(pre_write) == 2
    assert "linux-control-packet-preview" in pre_write[0]
    assert "linux-control-packet-compare" in pre_write[1]
    assert str(tmp_path / f"{base}-01-direct-fan-speed.json") in pre_write[1]
    pre_write_gate = cli_actions["safe-experiment:live-pwm"]["pre_write_validation"]
    assert pre_write_gate["required"] is True
    assert pre_write_gate["validation_status"] == "passed"
    assert pre_write_gate["allows_guarded_write"] is True
    assert pre_write_gate["minimum_required_match"] == "exact-match"
    assert pre_write_gate["required_write_gate_status"] == "pass"
    assert pre_write_gate["compare_commands"] == [pre_write[1]]
    assert pre_write_gate["observed_results"][0]["write_gate_status"] == "pass"
    assert pre_write_gate["expected_compare_results"][0]["required_allows_guarded_write"] is True
    assert action_plan["packet_compare_validation"]["pass_count"] == 1
    assert action_plan["packet_compare_validation"]["valid_schema_count"] == 1
    assert action_plan["packet_compare_validation"]["invalid_schema_count"] == 0
    assert cli_actions["safe-experiment:live-pwm"]["execution"]["status"] == "write-enabled"
    assert cli_actions["safe-experiment:live-pwm"]["execution"]["write_command_enabled"] is True
    write_gate = _run_probe(
        "linux-control-write-gate",
        str(tmp_path),
        "--capture-base",
        base,
        "--experiment-dir",
        str(experiment_dir),
        "--sys-root",
        str(sys_root),
        "--dev-root",
        str(dev_root),
    )
    assert write_gate["operation"] == "linux-control-write-gate"
    assert write_gate["schema_version"] == "lianli-linux-control-write-gate/v1"
    assert write_gate["status"] == "write-enabled"
    assert write_gate["allows_any_guarded_write"] is True
    assert write_gate["ready_action_ids"] == ["safe-experiment:live-pwm"]
    assert write_gate["write_confirmation_token"] == "WRITE-LIANLI"
    assert write_gate["actions"][0]["validation_status"] == "passed"
    assert write_gate["actions"][0]["target_state"]["status"] == "missing"
    assert "safe-pwm-experiment" in cli_actions["safe-experiment:live-pwm"]["command"]
    assert cli_actions["capture-evidence:live-rgb"]["status"] == "needs-evidence"
    assert cli_actions["capture-evidence:live-rainbow"]["missing_scenarios"][0]["capture_file"] == (
        f"{base}-06-lighting-generated-rainbow.pcapng"
    )
    assert any(
        f"compare-capture {base}-06-lighting-generated-rainbow.pcapng rainbow" in command
        for command in cli_actions["capture-evidence:live-rainbow"]["post_capture_commands"]
    )
    assert any("linux-control-packet-compare" in command for command in action_plan["commands"])
    assert any(f"capture missing scenario: {base}-06-lighting-generated-rainbow.pcapng" == command for command in action_plan["commands"])
    registry = _run_probe(
        "linux-control-target-registry",
        str(tmp_path),
        "--capture-base",
        base,
        "--experiment-dir",
        str(experiment_dir),
        "--sys-root",
        str(sys_root),
        "--dev-root",
        str(dev_root),
    )
    assert registry["operation"] == "linux-control-target-registry"
    assert registry["schema_version"] == "lianli-linux-control-target-registry/v1"
    assert registry["status"] == "packet-build-ready"
    assert registry["target_count"] == 1
    registry_target = registry["targets"][0]
    assert registry_target["id"] == "aa:bb:cc:dd:ee:ff@ch8/rx3"
    assert registry_target["packet_build_ready"] is True
    assert registry_target["wireless_device_info_template"]["kwargs"]["master_mac"] == "10:20:30:40:50:60"
    assert registry_target["observed_parameters"]["live-pwm"]["default_pwm_values"] == [77, 88, 99, 111]
    assert registry_target["ready_operations"] == ["live-pwm"]
    assert "device_type" in registry_target["missing_packet_fields"]
    preview = _run_probe(
        "linux-control-packet-preview",
        str(tmp_path),
        "live-pwm",
        "--target-id",
        registry_target["id"],
        "--capture-base",
        base,
        "--experiment-dir",
        str(experiment_dir),
        "--sys-root",
        str(sys_root),
        "--dev-root",
        str(dev_root),
    )
    assert preview["operation"] == "linux-control-packet-preview"
    assert preview["schema_version"] == "lianli-linux-control-packet-preview/v1"
    assert preview["status"] == "packet-preview-ready"
    assert "device_type" in preview["target"]["missing_packet_fields"]
    assert preview["target_state"]["status"] == "dry-run-uses-placeholders"
    assert "device_type" in preview["target_state"]["missing_packet_fields"]
    assert "raw" in preview["target_state"]["placeholder_fields"]
    assert preview["target_state"]["snapshot_metadata_available"] is True
    assert preview["target_state"]["snapshot_state_available"] is False
    assert preview["target_state"]["raw_hex_available"] is False
    assert preview["target_state"]["live_snapshot_refresh_required"] is True
    assert preview["parameters"]["pwm_values"] == [77, 88, 99, 111]
    assert preview["parameters"]["pwm_values_source"] == "capture-evidence"
    assert preview["packet_preview"]["rf_operations"] == {"live-pwm": 1}
    assert preview["packet_preview"]["first_packet_hex"].startswith(
        "100008031210aabbccddeeff1020304050600308014d58636f"
    )
    preview_packets = preview["packet_preview"]["packets"]
    assert len(preview_packets) == 4
    assert preview_packets[0]["kind"] == "rf-chunk"
    assert preview_packets[0]["rf_frames"][0]["operation"] == "live-pwm"
    assert preview_packets[0]["rf_frames"][0]["pwm_values"] == [77, 88, 99, 111]
    assert len(preview_packets[0]["sha256"]) == 64
    comparison = _run_probe(
        "linux-control-packet-compare",
        str(tmp_path),
        "live-pwm",
        str(tmp_path / f"{base}-01-direct-fan-speed.json"),
        "--target-id",
        registry_target["id"],
        "--capture-base",
        base,
        "--experiment-dir",
        str(experiment_dir),
        "--sys-root",
        str(sys_root),
        "--dev-root",
        str(dev_root),
    )
    assert comparison["operation"] == "linux-control-packet-compare"
    assert comparison["schema_version"] == "lianli-linux-control-packet-compare/v1"
    assert comparison["status"] == "matched"
    assert comparison["matched"] is True
    assert comparison["exact_match"] is False
    assert comparison["semantic_match"] is True
    assert comparison["target_state"]["status"] == "dry-run-uses-placeholders"
    assert "raw" in comparison["target_state"]["placeholder_fields"]
    assert comparison["parameters"]["pwm_values_source"] == "capture-evidence"
    assert comparison["expected_packet_count"] == 4
    assert comparison["match_diagnostics"]["status"] == "semantic-match-exact-mismatch"
    assert comparison["write_gate"]["status"] == "refresh-live-snapshot"
    assert comparison["write_gate"]["allows_guarded_write"] is False
    assert comparison["write_gate"]["comparison_status"] == "semantic-match-exact-mismatch"
    assert "live-list" in comparison["write_gate"]["required_before_write"][0]
    assert payload["linux_validation_plan"]["high_confidence_target_count"] == 1
    assert any("usb-capture-readiness" in command for command in payload["linux_validation_plan"]["commands"])
    assert any("validate-readonly" in command for command in payload["linux_validation_plan"]["commands"])
    assert any(
        "safe-pwm-experiment --mac aa:bb:cc:dd:ee:ff" in command
        for command in payload["linux_validation_plan"]["commands"]
    )
    assert any(f"summarize-experiments {experiment_dir}" in command for command in payload["linux_validation_plan"]["commands"])
    assert scenarios["direct-fan-speed"]["status"] == "evidence-found"
    assert scenarios["direct-fan-speed"]["matched_evidence"] == [
        "direct PWM RF frame",
        "non-sync PWM tuple",
    ]
    assert scenarios["direct-fan-speed"]["summary"]["linux_live_write_target_count"] == 1
    assert scenarios["direct-fan-speed"]["linux_live_write_targets"][0]["confidence"] == "high"
    assert scenarios["baseline"]["status"] == "missing-capture"
    assert any(
        command == "capture missing scenario: lianli-v2117-00-baseline.pcapng"
        for command in payload["recommended_commands"]
    )


def test_probe_capture_protocol_report_summarizes_capture_evidence(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend

    target = _bound_device()
    packets = [_snapshot_payload_with_motherboard_pwm(10, 10)]
    packets.extend(LianLiWirelessBackend().build_motherboard_pwm_mirror_packets(target, 127))
    capture_path = tmp_path / "l-connect-protocol.json"
    _write_tshark_json_capture(
        capture_path,
        packets,
        product_ids=["0x8041", "0x8040", "0x8040", "0x8040", "0x8040"],
    )

    payload = _run_probe("capture-protocol-report", str(capture_path))

    assert payload["operation"] == "capture-protocol-report"
    assert payload["rf_frame_count"] == 1
    assert payload["receiver_snapshot_count"] == 1
    assert payload["motherboard_pwm_values"] == [127]
    assert payload["summary"]["device_count"] == 1
    assert payload["summary"]["operation_count"] == 1
    assert payload["devices"]["aa:bb:cc:dd:ee:ff"]["operations"] == {"live-pwm-mirror": 1}
    assert payload["operations"]["live-pwm-mirror"]["motherboard_pwm_values"] == [127]
    assert payload["operations"]["live-pwm-mirror"]["pwm_values"] == {"127,127,127,127": 1}
    assert payload["operations"]["live-pwm-mirror"]["usb_device_counts"] == {"0416:8040": 4}
    assert payload["operations"]["live-pwm-mirror"]["usb_endpoint_counts"] == {"1/7/0x01/OUT/URB_BULK": 4}
    assert payload["operations"]["live-pwm-mirror"]["usb_frame_numbers"] == ["2", "3", "4", "5"]
    assert payload["operations"]["live-pwm-mirror"]["linux_live_write_targets"][0]["vid_pid"] == "0416:8040"
    assert payload["operations"]["live-pwm-mirror"]["linux_live_write_targets"][0]["write_endpoint"] == "0x01"
    assert payload["operations"]["live-pwm-mirror"]["linux_live_write_targets"][0]["confidence"] == "high"
    assert payload["linux_live_write_targets"][0]["operation"] == "live-pwm-mirror"


def test_probe_capture_timeline_report_orders_capture_events(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend, build_master_query_request, build_wireless_list_request

    target = _bound_device()
    packets = [
        build_wireless_list_request(),
        build_master_query_request(8),
        _snapshot_payload_with_motherboard_pwm(10, 10),
        *LianLiWirelessBackend().build_pwm_packets(target, [77, 88, 99, 111]),
    ]
    capture_path = tmp_path / "l-connect-timeline.json"
    _write_tshark_json_capture(
        capture_path,
        packets,
        product_ids=["0x8041", "0x8040", "0x8041", "0x8040", "0x8040", "0x8040", "0x8040"],
    )

    payload = _run_probe("capture-timeline-report", str(capture_path))

    assert payload["operation"] == "capture-timeline-report"
    assert payload["event_count"] == 4
    assert [event["event_type"] for event in payload["events"]] == [
        "receiver-list-request",
        "master-query-request",
        "receiver-snapshot",
        "rf-frame",
    ]
    assert payload["events"][3]["operation"] == "live-pwm"
    assert payload["events"][3]["chunk_packet_indexes"] == [3, 4, 5, 6]
    assert payload["events"][3]["usb"]["frame_number"] == "4"
    assert payload["events"][3]["usb"]["frame_time_relative"] == "0.400000"
    assert payload["events"][3]["time_relative_s"] == 0.4
    assert payload["events"][3]["chunk_time_span_s"] == 0.3
    assert payload["events"][3]["usb"]["vid_pid"] == "0416:8040"
    assert payload["events"][3]["chunk_usb"][-1]["frame_number"] == "7"
    assert payload["summary"]["time_span_s"] == 0.3
    assert payload["summary"]["device_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert payload["summary"]["logical_rf_operations"] == {"live-pwm": 1}


def test_probe_capture_protocol_report_decodes_static_rgb_color(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend

    target = _bound_device()
    packets = LianLiWirelessBackend().build_static_rgb_packets(target, (0, 0, 0), effect_index=1)
    capture_path = tmp_path / "l-connect-rgb.txt"
    capture_path.write_text("\n".join(packet.hex() for packet in packets), encoding="utf-8")

    payload = _run_probe("capture-protocol-report", str(capture_path))

    assert payload["operation"] == "capture-protocol-report"
    assert payload["rf_frame_count"] == 4 + RGB_FIRST_PAYLOAD_REPEAT_COUNT - 1
    assert payload["replay_hint_count"] == 1
    assert payload["summary"]["rf_operations"] == {"live-rgb": 1}
    assert payload["summary"]["rf_frame_operations"] == {
        "live-rgb": 4 + RGB_FIRST_PAYLOAD_REPEAT_COUNT - 1
    }
    assert payload["devices"]["aa:bb:cc:dd:ee:ff"]["operations"] == {"live-rgb": 1}
    assert payload["devices"]["aa:bb:cc:dd:ee:ff"]["rf_frame_operations"] == {
        "live-rgb": 4 + RGB_FIRST_PAYLOAD_REPEAT_COUNT - 1
    }
    assert payload["operations"]["live-rgb"]["count"] == 1
    assert payload["operations"]["live-rgb"]["rgb_sequence_frame_counts"] == [
        4 + RGB_FIRST_PAYLOAD_REPEAT_COUNT - 1
    ]
    assert payload["operations"]["live-rgb"]["rgb_first_packet_retransmit_counts"] == [
        RGB_FIRST_PAYLOAD_REPEAT_COUNT
    ]
    assert payload["operations"]["live-rgb"]["rgb_decode_statuses"] == {"decoded-literal": 1}
    assert payload["operations"]["live-rgb"]["rgb_static_colors"] == {"#000000": 1}
    assert payload["operations"]["live-rgb"]["rgb_decoded_lengths"] == [132 * 3]


def test_probe_compare_capture_matches_local_packet_builder(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend

    target = _bound_device()
    packets = LianLiWirelessBackend().build_motherboard_pwm_sync_packets(target)
    capture_path = tmp_path / "l-connect-capture.txt"
    capture_path.write_text("\n".join(packet.hex() for packet in packets), encoding="utf-8")

    payload = _run_probe(
        "compare-capture",
        str(capture_path),
        "pwm-sync",
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
    )

    assert payload["operation"] == "compare-capture"
    assert payload["expected_operation"] == "pwm-sync"
    assert payload["matched"] is True
    assert payload["exact_match"] is True
    assert payload["semantic_match"] is True
    assert payload["semantic"]["matches"][0]["operation"] == "live-pwm-sync"


def test_probe_compare_capture_reports_nearest_mismatch(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend

    target = _bound_device()
    packets = LianLiWirelessBackend().build_pwm_packets(target, [130])
    capture_path = tmp_path / "l-connect-mismatch.txt"
    capture_path.write_text("\n".join(packet.hex() for packet in packets), encoding="utf-8")

    payload = _run_probe(
        "compare-capture",
        str(capture_path),
        "pwm",
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
        "--pwm",
        "120",
    )

    assert payload["matched"] is False
    assert payload["diagnostics"]["status"] == "semantic-mismatch"
    assert payload["diagnostics"]["primary"] == "semantic"
    closest = payload["semantic"]["missing"][0]["closest_observed"]
    assert closest["observed_index"] == 0
    assert {
        "field": "pwm_values",
        "expected": [120, 120, 120, 120],
        "observed": [130, 130, 130, 130],
    } in closest["differing_fields"]
    assert payload["diagnostics"]["nearest_differences"][0]["differing_fields"] == closest["differing_fields"]


def test_probe_compare_capture_matches_pwm_mirror_sequence(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend

    target = _bound_device()
    packets = [_snapshot_payload_with_motherboard_pwm(10, 10)]
    packets.extend(LianLiWirelessBackend().build_motherboard_pwm_mirror_packets(target, 127))
    capture_path = tmp_path / "l-connect-pwm-mirror-capture.txt"
    capture_path.write_text("\n".join(packet.hex() for packet in packets), encoding="utf-8")

    payload = _run_probe(
        "compare-capture",
        str(capture_path),
        "pwm-mirror",
        "--snapshot-hex",
        "10 00 0a 0a",
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
    )

    assert payload["operation"] == "compare-capture"
    assert payload["expected_operation"] == "pwm-mirror"
    assert payload["expected_motherboard_pwm"] == 127
    assert payload["matched"] is True
    assert payload["exact_match"] is True
    assert payload["semantic_match"] is True
    assert payload["observed"]["summary"]["rf_operations"] == {"live-pwm-mirror": 1}
    assert payload["expected"]["summary"]["rf_operations"] == {"live-pwm": 1}
    assert payload["semantic"]["matches"][0]["operation"] == "live-pwm"


def test_probe_compare_capture_matches_rainbow_sequence(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend

    target = _bound_device()
    packets = LianLiWirelessBackend().build_rainbow_rgb_packets(
        target,
        frame_count=3,
        interval_ms=40,
        effect_index=2,
    )
    capture_path = tmp_path / "l-connect-rainbow-capture.txt"
    capture_path.write_text("\n".join(packet.hex() for packet in packets), encoding="utf-8")

    payload = _run_probe(
        "compare-capture",
        str(capture_path),
        "rainbow",
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
        "--frame-count",
        "3",
        "--interval-ms",
        "40",
        "--led-count",
        "132",
        "--effect-index",
        "2",
    )

    assert payload["operation"] == "compare-capture"
    assert payload["expected_operation"] == "rainbow"
    assert payload["matched"] is True
    assert payload["exact_match"] is True
    assert payload["semantic_match"] is True
    assert payload["observed"]["summary"]["rf_operations"] == {"live-rgb": 1}
    assert payload["observed"]["summary"]["rf_frame_operations"] == {"live-rgb": 11}
    assert payload["semantic"]["matched_count"] == 11


def test_probe_compare_capture_bind_uses_current_pwm_tuple(tmp_path):
    from usb9_lcd.lianli.wireless import LianLiWirelessBackend

    target = _unbound_device()
    packets = LianLiWirelessBackend().build_bind_packets(
        target,
        master_mac="10:20:30:40:50:60",
        rx_type=3,
        channel=8,
    )
    capture_path = tmp_path / "l-connect-bind-capture.txt"
    capture_path.write_text("\n".join(packet.hex() for packet in packets), encoding="utf-8")

    payload = _run_probe(
        "compare-capture",
        str(capture_path),
        "bind",
        "--mac",
        target.mac,
        "--master-mac",
        "10:20:30:40:50:60",
        "--channel",
        "8",
        "--rx-type",
        "3",
        "--device-type",
        str(target.device_type),
        "--fan-count",
        str(target.fan_count),
        "--current-pwm",
        "80,90,100,110",
    )

    assert payload["operation"] == "compare-capture"
    assert payload["expected_operation"] == "bind"
    assert payload["matched"] is True
    assert payload["exact_match"] is True
    assert payload["semantic_match"] is True


def test_probe_lcd_dry_run_outputs_wireless_header():
    payload = _run_probe(
        "dry-run-lcd",
        "brightness",
        "--value",
        "65",
        "--timestamp-ms",
        "16909060",
    )

    assert payload["operation"] == "dry-run-lcd"
    assert payload["command"] == "brightness"
    assert payload["command_id"] == 14
    assert payload["packet_length"] == 512
    assert payload["header_plaintext_size"] == 504
    assert payload["header_plaintext_first64_hex"].startswith("0e001a6d0403020141")


def test_probe_lcd_push_jpg_dry_run_records_payload_size():
    payload = _run_probe(
        "dry-run-lcd",
        "push-jpg",
        "--payload-size",
        "6",
        "--timestamp-ms",
        "1",
    )

    assert payload["command_id"] == 101
    assert payload["payload_size"] == 6
    assert payload["packet_length"] == 102400
    assert payload["header_plaintext_first64_hex"].startswith("65001a6d0100000000000006")


def test_probe_live_lcd_info_reads_handshake_and_firmware(monkeypatch, capsys):
    module = _load_probe_module()

    class FakeLcdBackend:
        def handshake(self):
            return {"mode": 5, "frame_index": 7}

        def firmware_version(self):
            return {"version": "1.2.3", "build": ""}

    monkeypatch.setattr(module, "create_pyusb_lcd_backend", lambda: FakeLcdBackend())

    assert module.main(["live-lcd-info"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "operation": "live-lcd-info",
        "mode": "both",
        "handshake": {"mode": 5, "frame_index": 7},
        "firmware": {"version": "1.2.3", "build": ""},
    }


def test_probe_live_lcd_control_requires_confirmation(monkeypatch):
    module = _load_probe_module()

    with pytest.raises(module.LianLiWirelessError, match="live writes require"):
        module.main(["live-lcd-control", "--brightness", "65", "--confirm", "wrong"])


def test_probe_live_lcd_control_writes_brightness_and_rotation(monkeypatch, capsys):
    module = _load_probe_module()
    calls: list[tuple[str, int]] = []

    class FakeLcdBackend:
        def set_brightness(self, value):
            calls.append(("brightness", value))
            return 512

        def set_rotation(self, degrees):
            calls.append(("rotation", degrees))
            return 512

    monkeypatch.setattr(module, "create_pyusb_lcd_backend", lambda: FakeLcdBackend())

    assert module.main(
        [
            "live-lcd-control",
            "--brightness",
            "65",
            "--rotation",
            "180",
            "--confirm",
            module.WRITE_CONFIRM_TOKEN,
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert calls == [("brightness", 65), ("rotation", 180)]
    assert payload == {
        "operation": "live-lcd-control",
        "applied": {
            "brightness": {"value": 65, "bytes_written": 512},
            "rotation": {"degrees": 180, "bytes_written": 512},
        },
    }


def test_probe_validate_readonly_saves_step_logs(monkeypatch, tmp_path, capsys):
    module = _load_probe_module()

    class FakeBackend:
        def list_devices(self):
            return WirelessSnapshot(raw=b"snapshot", devices=[_bound_device()])

        def query_master_mac(self, *, channel):
            return "10:20:30:40:50:60", channel

    class FakeLcdBackend:
        def handshake(self):
            return {"mode": 5, "frame_index": 7}

        def firmware_version(self):
            return {"version": "1.2.3", "build": ""}

    monkeypatch.setattr(module, "create_pyusb_backend", lambda: FakeBackend())
    monkeypatch.setattr(module, "create_pyusb_lcd_backend", lambda: FakeLcdBackend())

    assert module.main(["validate-readonly", "--output-dir", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "validate-readonly"
    assert payload["step_count"] == 4
    assert payload["error_count"] == 0
    assert (tmp_path / "scan.json").exists()
    assert json.loads((tmp_path / "live-list.json").read_text(encoding="utf-8"))["device_count"] == 1
    assert json.loads((tmp_path / "live-master.json").read_text(encoding="utf-8"))["detected"] is True
    assert json.loads((tmp_path / "live-lcd-info.json").read_text(encoding="utf-8"))["firmware"]["version"] == "1.2.3"


def test_probe_validate_readonly_can_skip_lcd(monkeypatch, tmp_path, capsys):
    module = _load_probe_module()

    class FakeBackend:
        def list_devices(self):
            return WirelessSnapshot(raw=b"snapshot", devices=[])

        def query_master_mac(self, *, channel):
            return None

    monkeypatch.setattr(module, "create_pyusb_backend", lambda: FakeBackend())

    assert module.main(["validate-readonly", "--skip-lcd", "--output-dir", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["step_count"] == 3
    assert not (tmp_path / "live-lcd-info.json").exists()


def test_probe_receiver_validation_bundle_saves_post_plug_evidence(monkeypatch, tmp_path, capsys):
    module = _load_probe_module()

    class FakeBackend:
        def list_devices(self):
            return WirelessSnapshot(raw=b"snapshot", devices=[_bound_device()])

        def query_master_mac(self, *, channel):
            return "10:20:30:40:50:60", channel

    class FakeLcdBackend:
        def handshake(self):
            return {"mode": 5, "frame_index": 7}

        def firmware_version(self):
            return {"version": "1.2.3", "build": ""}

    capture_dir = tmp_path / "captures"
    experiment_dir = tmp_path / "experiments"
    sys_root = tmp_path / "sys"
    dev_root = tmp_path / "dev"
    calls: dict[str, tuple[Path, Path, Path] | Path] = {}

    monkeypatch.setattr(module, "create_pyusb_backend", lambda: FakeBackend())
    monkeypatch.setattr(module, "create_pyusb_lcd_backend", lambda: FakeLcdBackend())
    monkeypatch.setattr(
        module,
        "_scan_payload",
        lambda: {"operation": "scan", "device_count": 1, "devices": [{"vid_pid": "0416:8040"}]},
    )
    monkeypatch.setattr(
        module,
        "usb_capture_readiness",
        lambda *, sys_root: {"operation": "usb-capture-readiness", "status": "linux-live-ready", "sys_root": str(sys_root)},
    )

    def fake_preflight(path, *, sys_root, dev_root, **_kwargs):  # noqa: ANN001
        calls["preflight"] = (path, sys_root, dev_root)
        return {"operation": "linux-control-preflight", "status": "ready"}

    def fake_write_gate(path, *, sys_root, dev_root, experiment_dir, **_kwargs):  # noqa: ANN001
        calls["write_gate"] = (path, sys_root, dev_root)
        calls["experiment_dir"] = experiment_dir
        return {
            "operation": "linux-control-write-gate",
            "status": "write-enabled",
            "allows_any_guarded_write": True,
            "ready_action_count": 1,
            "blocked_action_count": 0,
        }

    monkeypatch.setattr(module, "linux_control_preflight_report", fake_preflight)
    monkeypatch.setattr(module, "linux_control_write_gate_report", fake_write_gate)

    assert module.main(
        [
            "receiver-validation-bundle",
            "--output-dir",
            str(tmp_path),
            "--capture-dir",
            str(capture_dir),
            "--experiment-dir",
            str(experiment_dir),
            "--sys-root",
            str(sys_root),
            "--dev-root",
            str(dev_root),
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["operation"] == "receiver-validation-bundle"
    assert payload["step_count"] == 7
    assert payload["error_count"] == 0
    assert payload["ready_for_guarded_write"] is True
    assert payload["write_gate_status"] == "write-enabled"
    assert payload["bundle_path"] == str(tmp_path / "receiver-validation-bundle.json")
    assert payload["summary_path"] == str(tmp_path / "summary.json")
    assert payload["hardware_validation"]["status"] == "readonly-and-write-gate-ready"
    assert payload["receiver_control_next_action"]["status"] == "ready-for-single-target-safe-pwm"
    assert payload["receiver_control_next_action"]["recommended_commands"][0].startswith(
        "python tools/lianli_wireless_probe.py safe-pwm-experiment --mac aa:bb:cc:dd:ee:ff"
    )
    assert calls["preflight"] == (capture_dir, sys_root, dev_root)
    assert calls["write_gate"] == (capture_dir, sys_root, dev_root)
    assert calls["experiment_dir"] == experiment_dir
    for name in (
        "receiver-validation-bundle.json",
        "scan.json",
        "readiness.json",
        "live-list.json",
        "live-master.json",
        "validate-readonly.json",
        "preflight.json",
        "write-gate.json",
        "summary.json",
    ):
        assert (tmp_path / name).exists()
    saved_bundle = json.loads((tmp_path / "receiver-validation-bundle.json").read_text(encoding="utf-8"))
    saved_summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert saved_bundle["receiver_control_next_action"]["status"] == "ready-for-single-target-safe-pwm"
    assert saved_summary["receiver_control_next_action"]["status"] == "ready-for-single-target-safe-pwm"
    assert json.loads((tmp_path / "live-list.json").read_text(encoding="utf-8"))["device_count"] == 1
    assert json.loads((tmp_path / "readonly" / "live-lcd-info.json").read_text(encoding="utf-8"))["firmware"]["version"] == "1.2.3"
    assert any("safe-pwm-experiment" in step for step in payload["next_steps"])


def _bound_device() -> WirelessDeviceInfo:
    return WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )


def _snapshot_payload_with_motherboard_pwm(indicator: int, value: int) -> bytes:
    snapshot = bytearray(434)
    snapshot[0] = 0x10
    snapshot[1] = 1
    snapshot[2] = indicator
    snapshot[3] = value
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


def _device_payload(
    *,
    pwm: list[int] | None = None,
    rpm: list[int] | None = None,
    master_mac: str = "10:20:30:40:50:60",
) -> dict[str, object]:
    return {
        "mac": "aa:bb:cc:dd:ee:ff",
        "master_mac": master_mac,
        "is_bound": master_mac != "00:00:00:00:00:00",
        "channel": 8,
        "rx_type": 3,
        "device_type": 2,
        "fan_count": 3,
        "pwm_values": pwm or [80, 90, 100, 110],
        "fan_rpm": rpm or [1234, 1500, 0, 0],
        "command_sequence": 7,
    }


def _unbound_device() -> WirelessDeviceInfo:
    device = _bound_device()
    return WirelessDeviceInfo(
        mac=device.mac,
        master_mac="00:00:00:00:00:00",
        channel=device.channel,
        rx_type=0,
        device_type=device.device_type,
        fan_count=device.fan_count,
        pwm_values=device.pwm_values,
        fan_rpm=device.fan_rpm,
        command_sequence=device.command_sequence,
        raw=device.raw,
    )
