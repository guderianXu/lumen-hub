from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps

FitMode = Literal["cover", "contain", "stretch"]
Rotation = Literal[0, 90, 180, 270]


def _parse_hex_color(value: str) -> tuple[int, int, int]:
    if len(value) != 7 or not value.startswith("#"):
        raise ValueError("background must be a hex color like #000000")

    try:
        red = int(value[1:3], 16)
        green = int(value[3:5], 16)
        blue = int(value[5:7], 16)
    except ValueError as error:
        raise ValueError("background must be a hex color like #000000") from error

    return red, green, blue


@dataclass(frozen=True)
class FrameConfig:
    width: int = 480
    height: int = 480
    fit: FitMode = "cover"
    rotate: Rotation = 0
    background: str = "#000000"

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("width and height must be positive")
        if self.rotate not in (0, 90, 180, 270):
            raise ValueError("rotate must be one of 0, 90, 180, 270")
        _parse_hex_color(self.background)


def image_to_rgb(path: str | Path, config: FrameConfig = FrameConfig()) -> bytes:
    with Image.open(path) as image:
        return image_to_rgb_bytes(image, config)


def image_to_rgb_bytes(image: Image.Image, config: FrameConfig = FrameConfig()) -> bytes:
    transposed = ImageOps.exif_transpose(image)
    fitted = _fit_image(transposed.convert("RGB"), config)
    rotated = _rotate_image(fitted, config.rotate)
    return rotated.tobytes()


def image_to_rgb565(path: str | Path, config: FrameConfig = FrameConfig()) -> bytes:
    with Image.open(path) as image:
        return image_to_rgb565_bytes(image, config)


def image_to_jpeg(path: str | Path, config: FrameConfig = FrameConfig(), quality: int = 95) -> bytes:
    with Image.open(path) as image:
        return image_to_jpeg_bytes(image, config, quality=quality)


def image_to_rgb565_bytes(image: Image.Image, config: FrameConfig = FrameConfig()) -> bytes:
    rgb = image_to_rgb_bytes(image, config)
    frame = bytearray(len(rgb) // 3 * 2)

    output_index = 0
    for input_index in range(0, len(rgb), 3):
        red = rgb[input_index]
        green = rgb[input_index + 1]
        blue = rgb[input_index + 2]
        value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        frame[output_index] = (value >> 8) & 0xFF
        frame[output_index + 1] = value & 0xFF
        output_index += 2

    return bytes(frame)


def image_to_jpeg_bytes(image: Image.Image, config: FrameConfig = FrameConfig(), quality: int = 95) -> bytes:
    if quality < 1 or quality > 100:
        raise ValueError("jpeg quality must be between 1 and 100")

    rgb = image_to_rgb_bytes(image, config)
    fitted = Image.frombytes("RGB", (config.width, config.height), rgb)
    output = BytesIO()
    fitted.save(output, format="JPEG", quality=quality, subsampling=0, optimize=False)
    return output.getvalue()


def _fit_image(image: Image.Image, config: FrameConfig) -> Image.Image:
    size = (config.width, config.height)

    if config.fit == "cover":
        return ImageOps.fit(
            image,
            size,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    if config.fit == "contain":
        contained = ImageOps.contain(image, size, method=Image.Resampling.LANCZOS)
        background = Image.new("RGB", size, _parse_hex_color(config.background))
        offset = (
            (config.width - contained.width) // 2,
            (config.height - contained.height) // 2,
        )
        background.paste(contained, offset)
        return background

    if config.fit == "stretch":
        return image.resize(size, resample=Image.Resampling.NEAREST)

    raise ValueError(f"unsupported image fit: {config.fit}")


def _rotate_image(image: Image.Image, rotate: int) -> Image.Image:
    if rotate == 0:
        return image
    if rotate == 90:
        return image.transpose(Image.Transpose.ROTATE_270)
    if rotate == 180:
        return image.transpose(Image.Transpose.ROTATE_180)
    if rotate == 270:
        return image.transpose(Image.Transpose.ROTATE_90)
    raise ValueError("rotate must be one of 0, 90, 180, 270")
