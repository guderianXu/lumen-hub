from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from PIL import Image, ImageSequence, UnidentifiedImageError

from usb9_lcd.drivers.base import Capability, DisplayDevice, PixelFormat
from usb9_lcd.image import FitMode, FrameConfig, Rotation, image_to_jpeg_bytes, image_to_rgb565_bytes


@dataclass(frozen=True)
class AnimationRenderSettings:
    fit: FitMode = "contain"
    rotate: Rotation = 0
    background: str = "#000000"
    max_frames: int = 1000
    fps: int = 12
    jpeg_quality: int = 80

    def __post_init__(self) -> None:
        if self.max_frames <= 0:
            raise ValueError("max_frames must be positive")
        if self.fps <= 0:
            raise ValueError("fps must be positive")


@dataclass(frozen=True)
class AnimatedFrame:
    frame: bytes
    index: int
    duration_ms: int


def _validate_device(device: DisplayDevice) -> None:
    if not device.supports(Capability.STATIC_IMAGE):
        raise ValueError("selected device does not support static images")
    if device.pixel_format not in (PixelFormat.RGB565, PixelFormat.JPEG):
        raise ValueError(f"selected device pixel format is not supported: {device.pixel_format}")


def iter_animated_frames(
    path: str | Path,
    device: DisplayDevice,
    settings: AnimationRenderSettings = AnimationRenderSettings(),
) -> Iterator[AnimatedFrame]:
    _validate_device(device)
    try:
        image = Image.open(path)
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"failed to open animated asset: {error}") from error

    with image:
        frame_count = int(getattr(image, "n_frames", 1))
        if frame_count <= 1:
            raise ValueError("selected asset is not animated")

        fallback_duration_ms = max(1, int(1000 / settings.fps))
        config = FrameConfig(
            width=device.width,
            height=device.height,
            fit=settings.fit,
            rotate=settings.rotate,
            background=settings.background,
        )
        for index, frame in enumerate(ImageSequence.Iterator(image)):
            if index >= settings.max_frames:
                break
            duration_ms = _frame_duration_ms(frame, fallback_duration_ms)
            rgb = frame.convert("RGB")
            if device.pixel_format is PixelFormat.RGB565:
                encoded = image_to_rgb565_bytes(rgb, config)
            else:
                encoded = image_to_jpeg_bytes(rgb, config, quality=settings.jpeg_quality)

            yield AnimatedFrame(frame=encoded, index=index, duration_ms=duration_ms)


def _frame_duration_ms(frame: Image.Image, fallback_duration_ms: int) -> int:
    raw_duration = frame.info.get("duration", fallback_duration_ms)
    try:
        duration_ms = int(raw_duration)
    except (TypeError, ValueError):
        duration_ms = fallback_duration_ms
    return max(20, min(1000, duration_ms or fallback_duration_ms))
