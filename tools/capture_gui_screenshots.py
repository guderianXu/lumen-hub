from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

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


def capture_pages(output_dir: Path, *, width: int = 1440, height: int = 920) -> list[Path]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(driver=DemoDriver(), telemetry_provider=demo_telemetry, auto_refresh=False)
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
        path = output_dir / f"lumen-hub-{name}.png"
        window.grab().save(str(path))
        paths.append(path)

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
