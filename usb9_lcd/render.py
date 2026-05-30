from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .drivers.base import Capability, DisplayDevice, PixelFormat
from .image import FitMode, FrameConfig, Rotation, image_to_jpeg, image_to_rgb565


@dataclass(frozen=True)
class ImageRenderSettings:
    fit: FitMode = "cover"
    rotate: Rotation = 0
    background: str = "#000000"
    jpeg_quality: int = 95


@dataclass(frozen=True)
class RenderedFrame:
    frame: bytes
    width: int
    height: int
    byte_count: int


def render_static_image(
    path: str | Path,
    device: DisplayDevice,
    settings: ImageRenderSettings = ImageRenderSettings(),
) -> RenderedFrame:
    if not device.supports(Capability.STATIC_IMAGE):
        raise ValueError("selected device does not support static images")
    config = FrameConfig(
        width=device.width,
        height=device.height,
        fit=settings.fit,
        rotate=settings.rotate,
        background=settings.background,
    )
    if device.pixel_format is PixelFormat.RGB565:
        frame = image_to_rgb565(path, config)
    elif device.pixel_format is PixelFormat.JPEG:
        frame = image_to_jpeg(path, config, quality=settings.jpeg_quality)
    else:
        raise ValueError(f"selected device pixel format is not supported: {device.pixel_format}")

    return RenderedFrame(
        frame=frame,
        width=device.width,
        height=device.height,
        byte_count=len(frame),
    )
