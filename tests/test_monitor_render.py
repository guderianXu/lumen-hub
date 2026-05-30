from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image

from usb9_lcd.drivers.base import (
    Capability,
    DeviceConnection,
    DisplayDevice,
    PixelFormat,
    PixelStyle,
    PreviewProfile,
    PreviewShape,
)
from usb9_lcd.monitoring.models import CpuTelemetry, GpuTelemetry, SystemTelemetry
from usb9_lcd.monitoring.render import MonitorRenderSettings, render_monitoring_frame, render_monitoring_image


def _device(
    width: int = 480,
    height: int = 480,
    *,
    pixel_format: PixelFormat | str = PixelFormat.RGB565,
    capabilities: frozenset[Capability] = frozenset({Capability.STATIC_IMAGE}),
) -> DisplayDevice:
    return DisplayDevice(
        connection=DeviceConnection(
            driver_id="test.driver",
            display_name="Test Display",
            paths=(Path("/dev/null"),),
            writable=True,
            readable=True,
        ),
        width=width,
        height=height,
        pixel_format=pixel_format,
        preview=PreviewProfile(
            width=width,
            height=height,
            shape=PreviewShape.SQUARE,
            pixel_style=PixelStyle.CONTINUOUS,
        ),
        capabilities=capabilities,
    )


def _telemetry() -> SystemTelemetry:
    return SystemTelemetry(
        cpu=CpuTelemetry(package_temperature_c=54.5, utilization_percent=18, available=True),
        gpu=GpuTelemetry(
            name="RTX",
            temperature_c=61,
            utilization_percent=42,
            power_w=216.5,
            memory_used_mb=8000,
            memory_total_mb=24000,
            graphics_clock_mhz=2700,
            available=True,
        ),
        captured_at=datetime(2026, 5, 20, 12, 0, 0),
    )


def test_render_monitoring_image_uses_device_size():
    image = render_monitoring_image(_telemetry(), _device())

    assert isinstance(image, Image.Image)
    assert image.mode == "RGB"
    assert image.size == (480, 480)
    assert image.getpixel((10, 10)) != (0, 0, 0)
    assert len(set(image.getdata())) > 10


def test_render_monitoring_image_handles_small_devices():
    for size in ((1, 1), (32, 32), (96, 64)):
        image = render_monitoring_image(_telemetry(), _device(*size))

        assert image.size == size


def test_render_monitoring_image_supports_default_layouts():
    for layout in ("balanced", "gpu_focus", "minimal", "details", "overlay"):
        image = render_monitoring_image(_telemetry(), _device(320, 320), MonitorRenderSettings(layout=layout))

        assert image.size == (320, 320)
        assert len(set(image.getdata())) > 10


def test_render_monitoring_image_supports_palettes():
    neon = render_monitoring_image(_telemetry(), _device(320, 320), MonitorRenderSettings(palette="neon"))
    amber = render_monitoring_image(_telemetry(), _device(320, 320), MonitorRenderSettings(palette="amber"))

    assert neon.size == amber.size
    assert neon.getpixel((160, 160)) != amber.getpixel((160, 160))


def test_render_monitoring_frame_returns_rgb565_bytes():
    frame = render_monitoring_frame(_telemetry(), _device(96, 64))

    assert len(frame) == 96 * 64 * 2


def test_render_monitoring_frame_returns_jpeg_for_jpeg_device():
    frame = render_monitoring_frame(_telemetry(), _device(96, 64, pixel_format=PixelFormat.JPEG))

    assert frame.startswith(b"\xff\xd8")
    with Image.open(BytesIO(frame)) as jpeg:
        assert jpeg.size == (96, 64)


def test_render_monitoring_frame_requires_static_image_support():
    device = _device(capabilities=frozenset())

    try:
        render_monitoring_frame(_telemetry(), device)
    except ValueError as error:
        assert str(error) == "selected device does not support static images"
    else:
        raise AssertionError("expected ValueError")


def test_render_monitoring_frame_rejects_unknown_pixel_format():
    device = _device(pixel_format="rgb888")

    try:
        render_monitoring_frame(_telemetry(), device)
    except ValueError as error:
        assert "not supported" in str(error)
        assert "rgb888" in str(error)
    else:
        raise AssertionError("expected ValueError")


def test_render_monitoring_image_handles_unavailable_values():
    telemetry = SystemTelemetry(
        cpu=CpuTelemetry(error="no sensor"),
        gpu=GpuTelemetry(error="nvidia-smi missing"),
        captured_at=datetime(2026, 5, 20, 12, 0, 0),
    )

    image = render_monitoring_image(telemetry, _device(320, 240))

    assert image.size == (320, 240)
