from pathlib import Path
from io import BytesIO

import pytest
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
from usb9_lcd.render import ImageRenderSettings, render_static_image


def _device(
    *,
    width: int = 2,
    height: int = 1,
    capabilities: frozenset[Capability] = frozenset({Capability.STATIC_IMAGE}),
    pixel_format: PixelFormat | str = PixelFormat.RGB565,
) -> DisplayDevice:
    return DisplayDevice(
        connection=DeviceConnection(
            driver_id="test.driver",
            display_name="Test Display",
            paths=(Path("/dev/test-display"),),
            writable=True,
            readable=False,
        ),
        width=width,
        height=height,
        pixel_format=pixel_format,
        preview=PreviewProfile(
            width=width,
            height=height,
            shape=PreviewShape.RECTANGLE,
            pixel_style=PixelStyle.CONTINUOUS,
        ),
        capabilities=capabilities,
    )


def test_render_static_image_returns_rgb565_frame_for_device_size(tmp_path: Path):
    image_path = tmp_path / "red.png"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(image_path)

    rendered = render_static_image(image_path, _device(), ImageRenderSettings(fit="stretch"))

    assert rendered.frame == bytes([0xF8, 0x00, 0xF8, 0x00])
    assert rendered.width == 2
    assert rendered.height == 1
    assert rendered.byte_count == 4


def test_render_static_image_returns_jpeg_frame_for_jpeg_device(tmp_path: Path):
    image_path = tmp_path / "red.png"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(image_path)

    rendered = render_static_image(
        image_path,
        _device(width=4, height=4, pixel_format=PixelFormat.JPEG),
        ImageRenderSettings(fit="stretch", rotate=180),
    )

    assert rendered.frame.startswith(b"\xff\xd8")
    assert rendered.frame.endswith(b"\xff\xd9")
    assert rendered.width == 4
    assert rendered.height == 4
    assert rendered.byte_count == len(rendered.frame)
    with Image.open(BytesIO(rendered.frame)) as jpeg:
        assert jpeg.size == (4, 4)


def test_render_static_image_rejects_device_without_static_image_support(tmp_path: Path):
    image_path = tmp_path / "red.png"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(image_path)

    with pytest.raises(ValueError, match="selected device does not support static images"):
        render_static_image(image_path, _device(capabilities=frozenset()))


def test_render_static_image_rejects_unknown_pixel_format(tmp_path: Path):
    image_path = tmp_path / "red.png"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(image_path)

    with pytest.raises(ValueError, match="selected device pixel format is not supported"):
        render_static_image(image_path, _device(pixel_format="rgb888"))
