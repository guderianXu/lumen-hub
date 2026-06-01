from __future__ import annotations

import os
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from usb9_lcd.monitoring.models import CpuTelemetry, GpuTelemetry, SystemTelemetry
from usb9_lcd.monitoring.windows import WindowsFanChannel


def _telemetry(fans=None):
    return SystemTelemetry(
        cpu=CpuTelemetry(package_temperature_c=52.0, utilization_percent=12.0, available=True),
        gpu=GpuTelemetry(name="RTX", available=False),
        fans=fans or [],
        captured_at=datetime(2026, 5, 30, 12, 0, 0),
    )


def test_windows_snapshot_uses_structured_fan_backend(monkeypatch):
    import usb9_lcd.gui.fan_host as fan_host

    monkeypatch.setattr(fan_host.sys, "platform", "win32")
    monkeypatch.setattr(fan_host, "_is_windows_admin", lambda: False)
    monkeypatch.setattr(
        fan_host,
        "collect_windows_fan_channels",
        lambda: [
            WindowsFanChannel(
                name="NVIDIA GeForce RTX 5080 GPU Fan 1",
                rpm=700,
                percent=30,
                control_id="/gpu-nvidia/0/control/1",
                control_available=False,
                control_reason="GPU fan control detected, but the ordinary fan page only writes motherboard/controller fans",
            ),
            WindowsFanChannel(
                name="Nuvoton NCT6799D Fan #1",
                rpm=1200,
                percent=44,
                control_id="/lpc/nct6799d/control/0",
                control_available=True,
                control_reason="LibreHardwareMonitor control sensor available",
            ),
        ],
    )

    snapshot = fan_host.collect_generic_fan_snapshot(lambda: _telemetry())

    assert snapshot.platform_name == "Windows"
    assert snapshot.control_available is True
    assert "1 writable motherboard/controller fan" in snapshot.control_reason
    assert [channel.name for channel in snapshot.channels] == [
        "NVIDIA GeForce RTX 5080 GPU Fan 1",
        "Nuvoton NCT6799D Fan #1",
    ]
    assert snapshot.channels[0].control_available is False
    assert snapshot.channels[1].control_available is True
    assert snapshot.channels[1].windows_control_id == "/lpc/nct6799d/control/0"


def test_windows_snapshot_reports_missing_motherboard_control(monkeypatch):
    import usb9_lcd.gui.fan_host as fan_host

    monkeypatch.setattr(fan_host.sys, "platform", "win32")
    monkeypatch.setattr(fan_host, "_is_windows_admin", lambda: False)
    monkeypatch.setattr(
        fan_host,
        "collect_windows_fan_channels",
        lambda: [
            WindowsFanChannel(
                name="NVIDIA GeForce RTX 5080 GPU Fan 1",
                rpm=700,
                percent=30,
                control_id="/gpu-nvidia/0/control/1",
                control_available=False,
                control_reason="GPU fan control detected, but the ordinary fan page only writes motherboard/controller fans",
            )
        ],
    )

    snapshot = fan_host.collect_generic_fan_snapshot(lambda: _telemetry())

    assert snapshot.control_available is False
    assert "No writable motherboard/controller fan" in snapshot.control_reason
    assert "administrator" in snapshot.control_reason


def test_linux_snapshot_reports_missing_hwmon_fan_diagnostics(monkeypatch):
    import usb9_lcd.gui.fan_host as fan_host

    monkeypatch.setattr(fan_host.sys, "platform", "linux")
    monkeypatch.setattr(fan_host, "_scan_linux_hwmon_channels", lambda: [])
    monkeypatch.setattr(
        fan_host,
        "_linux_fan_probe_diagnostics",
        lambda: fan_host.LinuxFanProbeDiagnostics(
            summary="Linux 当前没有暴露 fan*_input 或 pwm* 风扇节点",
            details="Linux 风扇诊断:\n- hwmon 芯片: asus, k10temp\n- pwm*: none",
        ),
    )

    snapshot = fan_host.collect_generic_fan_snapshot(lambda: _telemetry())

    assert snapshot.control_available is False
    assert "fan*_input" in snapshot.control_reason
    assert "asus, k10temp" in snapshot.diagnostic_details
    assert "Linux 风扇诊断" in fan_host._snapshot_details(snapshot)


def test_scan_linux_hwmon_includes_pwm_only_channel(tmp_path):
    import usb9_lcd.gui.fan_host as fan_host

    hwmon_root = tmp_path / "hwmon"
    hwmon0 = hwmon_root / "hwmon0"
    hwmon0.mkdir(parents=True)
    (hwmon0 / "name").write_text("nct6683\n", encoding="utf-8")
    (hwmon0 / "pwm1").write_text("128\n", encoding="utf-8")
    (hwmon0 / "pwm1_enable").write_text("2\n", encoding="utf-8")

    channels = fan_host._scan_linux_hwmon_channels(hwmon_root)

    assert len(channels) == 1
    assert channels[0].name == "nct6683 fan1"
    assert channels[0].rpm is None
    assert 50 <= (channels[0].percent or 0) <= 51
    assert channels[0].control_available is True
    assert channels[0].pwm_path == hwmon0 / "pwm1"


def test_fan_curve_helpers_sanitize_and_interpolate():
    from usb9_lcd.gui.fan_curve_model import (
        fan_curve_preset_points,
        interpolate_fan_curve_percent,
        sanitize_fan_curve_points,
    )

    points = sanitize_fan_curve_points([[80, 110], [20, -5], [50, 40]])

    assert points == [[20, 0], [50, 40], [80, 100]]
    assert interpolate_fan_curve_percent(points, 10) == 0
    assert interpolate_fan_curve_percent(points, 35) == 20
    assert interpolate_fan_curve_percent(points, 90) == 100
    assert fan_curve_preset_points("quiet") == [[30, 18], [50, 25], [65, 38], [80, 62], [92, 100]]
    assert fan_curve_preset_points("full") == [[0, 100], [100, 100]]


def test_host_fan_settings_load_defaults_and_curve_points(tmp_path):
    import json

    from usb9_lcd.gui.settings import load_settings

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "host_fan": {
                    "curve_enabled": True,
                    "curve_interval_seconds": 99,
                    "curve_preset": "full",
                    "curve_points": [[90, 120], [20, -1]],
                }
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(path)

    assert settings.host_fan.curve_enabled is True
    assert settings.host_fan.curve_interval_seconds == 60
    assert settings.host_fan.curve_preset == "full"
    assert settings.host_fan.curve_points == [[20, 0], [90, 100]]


def test_linux_driver_probe_shell_loads_candidate_modules():
    import usb9_lcd.gui.fan_host as fan_host

    shell = fan_host._fan_hwmon_probe_shell()

    assert "modprobe nct6683 force=1" in shell
    assert "modprobe nct6775" in shell
    assert "modprobe asus_ec_sensors" in shell
    assert "modprobe it87" in shell


def test_fan_host_auto_probe_runs_before_snapshot(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage, GenericFanSnapshot

    seen = {}
    snapshot = GenericFanSnapshot(
        platform_name="Linux",
        telemetry=_telemetry(),
        channels=[],
        control_available=False,
        control_reason="Linux 当前没有暴露 fan*_input 或 pwm* 风扇节点",
        diagnostic_details="Linux 风扇诊断",
    )

    class FakeFanPage(FanControlHostPage):
        def _system_has_fan_pwm_files(self):
            return False

        def _load_fan_hwmon_drivers(self, *, interactive=False):  # noqa: ANN001
            seen["interactive"] = interactive
            return False, "probe ran"

    monkeypatch.setattr(fan_host.sys, "platform", "linux")
    app = QApplication.instance() or QApplication([])
    page = FakeFanPage(auto_load=False, auto_probe_hwmon_drivers=True, snapshot_collector=lambda: snapshot)

    result = page._collect_snapshot_after_optional_probe(interactive_driver_probe=True)

    assert seen["interactive"] is True
    assert "probe ran" in result.diagnostic_details
    assert page._driver_probe_attempted is True

    page.close()
    app.quit()


def test_fan_host_applies_curve_pwm_from_cpu_temperature(tmp_path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage, GenericFanChannel, GenericFanSnapshot
    from usb9_lcd.gui.settings import GuiSettings

    pwm_path = tmp_path / "pwm1"
    pwm_enable_path = tmp_path / "pwm1_enable"
    pwm_path.write_text("0\n", encoding="utf-8")
    pwm_enable_path.write_text("2\n", encoding="utf-8")
    settings = GuiSettings()
    settings.host_fan.curve_points = [[40, 20], [80, 100]]
    snapshot = GenericFanSnapshot(
        platform_name="Linux",
        telemetry=_telemetry(),
        channels=[
            GenericFanChannel(
                name="CPU Fan",
                rpm=900,
                pwm_path=pwm_path,
                pwm_enable_path=pwm_enable_path,
                control_available=True,
                control_reason="PWM writable",
            )
        ],
        control_available=True,
        control_reason="1 writable PWM channel(s) detected",
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(
        auto_load=False,
        settings=settings,
        settings_saver=lambda _settings: None,
        snapshot_collector=lambda: snapshot,
    )
    page._snapshot = snapshot
    page._apply_curve_to_snapshot(snapshot, source="test")

    assert pwm_enable_path.read_text(encoding="utf-8") == "1\n"
    assert pwm_path.read_text(encoding="utf-8") == "112\n"
    assert "CPU 52C -> PWM 44%" in page.details.toPlainText()

    page.close()
    app.quit()


def test_fan_host_curve_preset_updates_points_and_custom_state():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_curve_model import fan_curve_preset_points
    from usb9_lcd.gui.fan_host import FanControlHostPage
    from usb9_lcd.gui.settings import GuiSettings

    saved: list[str] = []
    settings = GuiSettings()
    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(
        auto_load=False,
        settings=settings,
        settings_saver=lambda value: saved.append(value.host_fan.curve_preset),
    )

    page.curve_preset_combo.setCurrentIndex(page.curve_preset_combo.findData("quiet"))

    assert settings.host_fan.curve_preset == "quiet"
    assert settings.host_fan.curve_points == fan_curve_preset_points("quiet")
    assert page.curve_editor.points() == fan_curve_preset_points("quiet")
    assert saved[-1] == "quiet"

    page._curve_changed([[40, 30], [80, 90]])

    assert settings.host_fan.curve_preset == "custom"
    assert page.curve_preset_combo.currentData() == "custom"
    assert settings.host_fan.curve_points == [[40, 30], [80, 90]]

    page.close()
    app.quit()


def test_fan_host_curve_change_persists_to_settings_file(tmp_path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage
    from usb9_lcd.gui.settings import GuiSettings, load_settings, save_settings

    path = tmp_path / "settings.json"
    settings = GuiSettings()
    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(
        auto_load=False,
        settings=settings,
        settings_saver=lambda value: save_settings(value, path),
    )

    page._curve_changed([[42, 33], [79, 88]])
    loaded = load_settings(path)

    assert loaded.host_fan.curve_preset == "custom"
    assert loaded.host_fan.curve_points == [[42, 33], [79, 88]]
    assert page.curve_preset_combo.currentData() == "custom"
    assert "42°C→33%" in page.curve_summary.text()
    assert "已保存" in page.status_label.text()

    page.close()
    app.quit()


def test_fan_curve_editor_commits_final_drag_position_on_release():
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_curve import FanCurveEditor

    app = QApplication.instance() or QApplication([])
    editor = FanCurveEditor()
    editor.resize(520, 320)
    editor.set_points([[30, 25], [85, 100]])
    captured: list[list[list[int]]] = []
    editor.curve_changed.connect(lambda points: captured.append(points))
    x, y = editor._to_screen(40, 60)
    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(x, y),
        QPointF(x, y),
        QPointF(x, y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    editor._dragging_idx = 0
    editor.mouseReleaseEvent(event)

    assert captured
    assert captured[-1] == [[40, 60], [85, 100]]
    assert editor.points() == [[40, 60], [85, 100]]

    editor.close()
    app.quit()


def test_fan_host_builds_pwm_permission_command(tmp_path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    pwm_path = tmp_path / "pwm1"
    pwm_enable_path = tmp_path / "pwm1_enable"
    pwm_path.write_text("80\n", encoding="utf-8")
    pwm_enable_path.write_text("2\n", encoding="utf-8")

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_load=False)

    command = page._pwm_permission_shell([pwm_path, pwm_enable_path])

    assert "chown" in command
    assert "chmod u+rw,g+rw" in command
    assert str(pwm_path) in command
    assert str(pwm_enable_path) in command

    page.close()
    app.quit()


def test_fan_host_auto_grants_pwm_permissions_and_rescans(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage, GenericFanChannel, GenericFanSnapshot

    monkeypatch.setattr(fan_host.sys, "platform", "linux")
    pwm_path = tmp_path / "pwm1"
    pwm_enable_path = tmp_path / "pwm1_enable"
    pwm_path.write_text("80\n", encoding="utf-8")
    pwm_enable_path.write_text("2\n", encoding="utf-8")
    unwritable = GenericFanSnapshot(
        platform_name="Linux",
        telemetry=_telemetry(),
        channels=[
            GenericFanChannel(
                name="nct6799 fan1",
                rpm=987,
                percent=82,
                pwm_path=pwm_path,
                pwm_enable_path=pwm_enable_path,
                control_available=False,
                control_reason="PWM exists but is not writable",
            )
        ],
        control_available=False,
        control_reason="Fan sensors detected, but no writable PWM channel is available",
    )
    writable = GenericFanSnapshot(
        platform_name="Linux",
        telemetry=_telemetry(),
        channels=[
            GenericFanChannel(
                name="nct6799 fan1",
                rpm=987,
                percent=82,
                pwm_path=pwm_path,
                pwm_enable_path=pwm_enable_path,
                control_available=True,
                control_reason="PWM writable",
            )
        ],
        control_available=True,
        control_reason="1 writable PWM channel(s) detected",
    )
    snapshots = [unwritable, writable]
    seen: dict[str, object] = {}

    class FakeFanPage(FanControlHostPage):
        def _system_has_fan_pwm_files(self):
            return True

        def _pwm_permission_paths(self, snapshot=None):  # noqa: ANN001
            return [pwm_path, pwm_enable_path]

        def _grant_pwm_permissions(self, snapshot, *, interactive=False):  # noqa: ANN001
            seen["interactive"] = interactive
            seen["snapshot"] = snapshot
            return True, "pwm-permissions=ok files=2"

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage(
        auto_load=False,
        auto_grant_pwm_permissions=True,
        snapshot_collector=lambda: snapshots.pop(0),
    )

    result = page._collect_snapshot_after_optional_probe(interactive_driver_probe=False)

    assert seen["interactive"] is False
    assert result.control_available is True
    assert "pwm-permissions=ok files=2" in result.diagnostic_details
    assert page._permission_grant_attempted is True

    page.close()
    app.quit()


def test_fan_host_permission_button_enables_for_unwritable_pwm(tmp_path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage, GenericFanChannel, GenericFanSnapshot

    pwm_path = tmp_path / "pwm1"
    pwm_enable_path = tmp_path / "pwm1_enable"
    pwm_path.write_text("80\n", encoding="utf-8")
    pwm_enable_path.write_text("2\n", encoding="utf-8")
    snapshot = GenericFanSnapshot(
        platform_name="Linux",
        telemetry=_telemetry(),
        channels=[
            GenericFanChannel(
                name="nct6799 fan1",
                rpm=987,
                pwm_path=pwm_path,
                pwm_enable_path=pwm_enable_path,
                control_available=False,
                control_reason="PWM exists but is not writable",
            )
        ],
        control_available=False,
        control_reason="Fan sensors detected, but no writable PWM channel is available",
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_load=False, snapshot_collector=lambda: snapshot)
    page._pwm_permission_paths = lambda _snapshot=None: [pwm_path, pwm_enable_path]
    page._snapshot = snapshot
    page._render_snapshot()

    assert page.permission_button.isEnabled() is True
    assert page.apply_button.isEnabled() is False

    page.close()
    app.quit()


def test_fan_host_renders_control_state_from_snapshot():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage, GenericFanChannel, GenericFanSnapshot

    app = QApplication.instance() or QApplication([])
    snapshot = GenericFanSnapshot(
        platform_name="Windows",
        telemetry=_telemetry(),
        channels=[
            GenericFanChannel(
                name="Nuvoton NCT6799D Fan #1",
                rpm=1200,
                percent=44,
                windows_control_id="/lpc/nct6799d/control/0",
                control_available=True,
                control_reason="LibreHardwareMonitor control sensor available",
            )
        ],
        control_available=True,
        control_reason="1 writable motherboard/controller fan channel(s) detected",
    )
    page = FanControlHostPage(auto_load=False, snapshot_collector=lambda: snapshot)

    page._snapshot = snapshot
    page._render_snapshot()

    assert "Nuvoton NCT6799D Fan #1" in page.sensor_value.text()
    assert page.apply_button.isEnabled() is True
    assert "writable" in page.control_value.text()

    page.close()
    app.quit()


def test_fan_host_renders_realtime_cpu_and_all_fan_channels():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage, GenericFanChannel, GenericFanSnapshot

    app = QApplication.instance() or QApplication([])
    snapshot = GenericFanSnapshot(
        platform_name="Linux",
        telemetry=_telemetry(),
        channels=[
            GenericFanChannel(name="nct6799 fan1", rpm=987, percent=82, control_reason="read-only"),
            GenericFanChannel(name="nct6799 fan2", rpm=1620, percent=58, control_reason="read-only"),
            GenericFanChannel(name="nct6799 fan3", rpm=0, percent=58, control_reason="read-only"),
            GenericFanChannel(name="nct6799 fan4", rpm=1888, percent=60, control_reason="read-only"),
        ],
        control_available=False,
        control_reason="Fan sensors detected, but no writable PWM channel is available",
    )
    page = FanControlHostPage(auto_load=False, snapshot_collector=lambda: snapshot)

    page._snapshot = snapshot
    page._render_snapshot()

    assert "52°C" in page.cpu_value.text()
    assert "凉爽" in page.cpu_value.text()
    assert "4 通道" in page.sensor_value.text()
    assert "3 有转速" in page.sensor_value.text()
    assert "最高 1888 RPM" in page.sensor_value.text()
    assert "nct6799 fan4" in page.sensor_value.text()
    assert "更新 12:00:00" in page.live_value.text()
    assert "只读显示" in page.live_value.text()

    page.release()
    page.close()
    app.quit()


def test_fan_host_live_refresh_and_curve_tick_are_separated():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_load=False)
    calls: list[dict[str, object]] = []
    page.reload_fan_control = lambda *args, **kwargs: calls.append(kwargs)  # type: ignore[method-assign]

    page._live_refresh_tick()
    page._curve_tick()

    assert calls[0]["apply_curve_after_scan"] is False
    assert calls[1]["apply_curve_after_scan"] is True

    page.release()
    page.close()
    app.quit()


def test_fan_host_render_does_not_apply_curve_during_readonly_refresh(tmp_path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage, GenericFanChannel, GenericFanSnapshot
    from usb9_lcd.gui.settings import GuiSettings

    pwm_path = tmp_path / "pwm1"
    pwm_enable_path = tmp_path / "pwm1_enable"
    pwm_path.write_text("0\n", encoding="utf-8")
    pwm_enable_path.write_text("2\n", encoding="utf-8")
    settings = GuiSettings()
    settings.host_fan.curve_enabled = True
    settings.host_fan.curve_points = [[40, 20], [80, 100]]
    snapshot = GenericFanSnapshot(
        platform_name="Linux",
        telemetry=_telemetry(),
        channels=[
            GenericFanChannel(
                name="CPU Fan",
                rpm=900,
                pwm_path=pwm_path,
                pwm_enable_path=pwm_enable_path,
                control_available=True,
                control_reason="PWM writable",
            )
        ],
        control_available=True,
        control_reason="1 writable PWM channel(s) detected",
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(
        auto_load=False,
        settings=settings,
        settings_saver=lambda _settings: None,
        snapshot_collector=lambda: snapshot,
    )
    page._snapshot = snapshot
    page._render_snapshot()

    assert pwm_enable_path.read_text(encoding="utf-8") == "2\n"
    assert pwm_path.read_text(encoding="utf-8") == "0\n"

    page._apply_curve_after_scan = True
    page._render_snapshot()

    assert pwm_enable_path.read_text(encoding="utf-8") == "1\n"
    assert pwm_path.read_text(encoding="utf-8") == "112\n"

    page.release()
    page.close()
    app.quit()


def test_fan_host_applies_windows_control_percent(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage, GenericFanChannel, GenericFanSnapshot

    calls = []
    monkeypatch.setattr(fan_host.sys, "platform", "win32")
    monkeypatch.setattr(
        fan_host,
        "set_windows_fan_control_percent",
        lambda control_id, percent: calls.append((control_id, percent)),
    )

    app = QApplication.instance() or QApplication([])
    snapshot = GenericFanSnapshot(
        platform_name="Windows",
        telemetry=_telemetry(),
        channels=[
            GenericFanChannel(
                name="Nuvoton NCT6799D Fan #1",
                rpm=1200,
                percent=44,
                windows_control_id="/lpc/nct6799d/control/0",
                control_available=True,
                control_reason="LibreHardwareMonitor control sensor available",
            )
        ],
        control_available=True,
        control_reason="1 writable motherboard/controller fan channel(s) detected",
    )
    page = FanControlHostPage(auto_load=False, snapshot_collector=lambda: snapshot)
    page.reload_fan_control = lambda *args, **kwargs: None
    page._snapshot = snapshot
    page.pwm_slider.setValue(55)

    page._apply_pwm()

    assert calls == [("/lpc/nct6799d/control/0", 55)]
    assert "已写入 PWM 55%" in page.details.toPlainText()

    page.close()
    app.quit()


def test_fan_host_applies_linux_pwm_after_enabling_manual_mode(tmp_path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage, GenericFanChannel, GenericFanSnapshot

    pwm_path = tmp_path / "pwm1"
    pwm_enable_path = tmp_path / "pwm1_enable"
    pwm_path.write_text("100\n", encoding="utf-8")
    pwm_enable_path.write_text("2\n", encoding="utf-8")
    snapshot = GenericFanSnapshot(
        platform_name="Linux",
        telemetry=_telemetry(),
        channels=[
            GenericFanChannel(
                name="Nuvoton fan1",
                rpm=1200,
                percent=39,
                pwm_path=pwm_path,
                pwm_enable_path=pwm_enable_path,
                control_available=True,
                control_reason="PWM writable",
            )
        ],
        control_available=True,
        control_reason="1 writable PWM channel(s) detected",
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_load=False, snapshot_collector=lambda: snapshot)
    page.reload_fan_control = lambda *args, **kwargs: None
    page._snapshot = snapshot
    page.pwm_slider.setValue(55)

    page._apply_pwm()

    assert pwm_enable_path.read_text(encoding="utf-8") == "1\n"
    assert pwm_path.read_text(encoding="utf-8") == "140\n"
    assert "已写入 PWM 55%" in page.details.toPlainText()

    page.close()
    app.quit()


def test_fan_host_manual_pwm_mode_is_enabled_by_default():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_load=False)

    assert page.enable_manual.isChecked() is True

    page.close()
    app.quit()
