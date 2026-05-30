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
