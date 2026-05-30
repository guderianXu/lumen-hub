from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from usb9_lcd.animation import AnimationRenderSettings, iter_animated_frames
from usb9_lcd.drivers.base import (
    Capability,
    DeviceConnection,
    DisplayDevice,
    PixelFormat,
    PixelStyle,
    PreviewProfile,
    PreviewShape,
)


def _device(
    width: int = 2,
    height: int = 1,
    *,
    capabilities=frozenset({Capability.STATIC_IMAGE}),
    pixel_format=PixelFormat.RGB565,
) -> DisplayDevice:
    return DisplayDevice(
        connection=DeviceConnection("test.driver", "Test Display", (Path("/dev/null"),), True, True),
        width=width,
        height=height,
        pixel_format=pixel_format,
        preview=PreviewProfile(width, height, PreviewShape.RECTANGLE, PixelStyle.CONTINUOUS),
        capabilities=capabilities,
    )


def _gif(path: Path) -> None:
    Image.new("RGB", (1, 1), (255, 0, 0)).save(
        path,
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), (0, 255, 0))],
        duration=100,
        loop=0,
    )


def test_iter_animated_frames_returns_rgb565_frames(tmp_path: Path):
    gif_path = tmp_path / "blink.gif"
    _gif(gif_path)

    frames = list(iter_animated_frames(gif_path, _device(), AnimationRenderSettings(fit="stretch", fps=2)))

    assert [frame.index for frame in frames] == [0, 1]
    assert [len(frame.frame) for frame in frames] == [4, 4]
    assert frames[0].duration_ms == 100


def test_iter_animated_frames_returns_jpeg_frames_for_jpeg_device(tmp_path: Path):
    gif_path = tmp_path / "blink.gif"
    _gif(gif_path)

    frames = list(
        iter_animated_frames(
            gif_path,
            _device(width=4, height=4, pixel_format=PixelFormat.JPEG),
            AnimationRenderSettings(fit="stretch", fps=2),
        )
    )

    assert [frame.index for frame in frames] == [0, 1]
    assert all(frame.frame.startswith(b"\xff\xd8") for frame in frames)
    with Image.open(BytesIO(frames[0].frame)) as jpeg:
        assert jpeg.size == (4, 4)


def test_iter_animated_frames_honors_max_frames(tmp_path: Path):
    gif_path = tmp_path / "blink.gif"
    _gif(gif_path)

    frames = list(iter_animated_frames(gif_path, _device(), AnimationRenderSettings(max_frames=1)))

    assert len(frames) == 1


def test_iter_animated_frames_rejects_static_images(tmp_path: Path):
    image_path = tmp_path / "static.png"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(image_path)

    with pytest.raises(ValueError, match="selected asset is not animated"):
        list(iter_animated_frames(image_path, _device()))


def test_iter_animated_frames_rejects_unsupported_device(tmp_path: Path):
    gif_path = tmp_path / "blink.gif"
    _gif(gif_path)

    with pytest.raises(ValueError, match="selected device does not support static images"):
        list(iter_animated_frames(gif_path, _device(capabilities=frozenset())))
