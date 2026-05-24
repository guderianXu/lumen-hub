from __future__ import annotations

import json
import subprocess
import sys
import importlib.util
from pathlib import Path

import pytest

from usb9_lcd.lianli.wireless import WirelessDeviceInfo, WirelessSnapshot


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
    assert payload["devices"][0]["mac"] == "aa:bb:cc:dd:ee:ff"
    assert payload["devices"][0]["fan_rpm"] == [1234, 1500, 0, 0]


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
    invalid_path.write_text("{not json", encoding="utf-8")

    payload = _run_probe("summarize-experiments", str(tmp_path))

    assert payload["operation"] == "summarize-experiments"
    assert payload["json_file_count"] == 4
    assert payload["analyzed_live_log_count"] == 2
    assert payload["invalid_file_count"] == 1
    assert payload["receiver_macs"] == ["aa:bb:cc:dd:ee:ff"]
    assert payload["field_change_counts"] == {"pwm_values": 1}
    assert payload["operation_stats"]["live-pwm"]["changed_count"] == 1
    assert payload["operation_stats"]["live-rgb"]["unchanged_count"] == 1
    assert payload["validation_errors"][0]["error"] == "USB read failed"


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

        def send_static_rgb(self, target, color, *, effect_index):
            calls["target"] = target.mac
            calls["color"] = color
            calls["effect_index"] = effect_index
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
    assert calls == {
        "target": "aa:bb:cc:dd:ee:ff",
        "color": (0, 0, 0),
        "effect_index": 1,
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
    assert payload["packet_count"] == 4
    assert payload["packet_size"] == 64
    assert payload["first_packet_hex"].startswith("100008031210")


def test_probe_pwm_sync_dry_run_outputs_sync_magic_pwm():
    payload = _run_probe("dry-run-pwm-sync")

    assert payload["operation"] == "dry-run-pwm-sync"
    assert payload["enabled"] is True
    assert payload["packet_count"] == 4
    assert payload["first_packet_hex"].startswith("100008031210")
    assert "06060606" in payload["first_packet_hex"]


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
    assert payload["packet_count"] == 16
    assert payload["packet_size"] == 64
    assert payload["first_packet_hex"].startswith("100008031220")


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
