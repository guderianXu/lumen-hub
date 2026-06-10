from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

from usb9_lcd.drivers.base import (
    Capability,
    DeviceConnection,
    DisplayDevice,
    PixelFormat,
    PixelStyle,
    PreviewProfile,
    PreviewShape,
)
from usb9_lcd.gui.fan_host import GenericFanChannel, GenericFanSnapshot
from usb9_lcd.gui.main_window import MainWindow
from usb9_lcd.monitoring.models import CpuTelemetry, FanTelemetry, GpuTelemetry, SystemTelemetry

PAGE_ROWS = {
    "home": 0,
    "screen": 1,
    "fan": 2,
    "lighting": 3,
    "lianli": 5,
}


class DemoDriver:
    driver_id = "demo.driver"
    display_name = "Demo LCD Driver"

    def __init__(self) -> None:
        self.device = DisplayDevice(
            connection=DeviceConnection(
                driver_id="asus.lc_iii",
                display_name="ASUS Test LCD",
                paths=(Path("/dev/hidraw-control"), Path("/dev/hidraw-data")),
                writable=True,
                readable=True,
                details="demo",
            ),
            width=480,
            height=480,
            pixel_format=PixelFormat.RGB565,
            preview=PreviewProfile(
                width=480,
                height=480,
                shape=PreviewShape.CIRCLE,
                pixel_style=PixelStyle.CONTINUOUS,
                orientation=180,
                label="LCD",
            ),
            capabilities=frozenset({Capability.STATIC_IMAGE, Capability.SENSOR_MONITOR}),
        )

    def discover(self) -> list[DisplayDevice]:
        return [self.device]

    def upload_static_frame(self, device: DisplayDevice, frame: bytes) -> None:
        return None


def demo_telemetry() -> SystemTelemetry:
    return SystemTelemetry(
        cpu=CpuTelemetry(package_temperature_c=54.0, utilization_percent=18.0, power_w=86.0, available=True),
        gpu=GpuTelemetry(
            name="NVIDIA RTX",
            temperature_c=61,
            utilization_percent=42,
            power_w=216.0,
            fan_speed_percent=48,
            available=True,
        ),
        fans=[
            FanTelemetry(name="CPU Fan", rpm=987, percent=82, available=True),
            FanTelemetry(name="GPU Fan", rpm=1530, percent=48, available=True),
        ],
        captured_at=datetime(2026, 6, 11, 12, 0, 0),
    )


def demo_fan_snapshot() -> GenericFanSnapshot:
    return GenericFanSnapshot(
        platform_name="Demo",
        telemetry=demo_telemetry(),
        channels=[
            GenericFanChannel(name="CPU Fan", rpm=987, percent=54, role="cpu", control_available=True),
            GenericFanChannel(name="AIO Pump", rpm=2420, percent=78, role="pump", control_available=True),
            GenericFanChannel(name="Front Case Fan", rpm=1260, percent=48, role="case", control_available=True),
            GenericFanChannel(name="Rear Case Fan", rpm=1180, percent=46, role="case", control_available=True),
        ],
        control_available=True,
        control_reason="4 demo writable PWM channels detected",
        diagnostic_details="Demo data for GUI screenshot capture.",
    )


def _drain_background_gui_work(app: QApplication, window: MainWindow, *, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        fan_page = getattr(window, "fan_page", None)
        if not bool(getattr(fan_page, "_scan_active", False)):
            return
        time.sleep(0.05)
    app.processEvents()


def capture_pages(output_dir: Path, *, width: int = 1440, height: int = 920) -> list[Path]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(driver=DemoDriver(), telemetry_provider=demo_telemetry, auto_refresh=False)
    if hasattr(window.fan_page, "_snapshot_collector"):
        window.fan_page._snapshot_collector = demo_fan_snapshot
    window.resize(width, height)
    window.refresh_devices()
    window.refresh_telemetry()
    window.show()
    app.processEvents()

    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, row in PAGE_ROWS.items():
        window.navigation.setCurrentRow(row)
        app.processEvents()
        _drain_background_gui_work(app, window)
        path = output_dir / f"lumen-hub-{name}.png"
        window.grab().save(str(path))
        paths.append(path)

    _drain_background_gui_work(app, window)
    window.close()
    app.processEvents()
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture Lumen Hub GUI screenshots with demo data.")
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/gui-screenshots"))
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=920)
    args = parser.parse_args(argv)
    for path in capture_pages(args.output_dir, width=args.width, height=args.height):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
