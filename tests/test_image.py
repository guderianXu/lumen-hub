from pathlib import Path

import pytest
from PIL import Image

from usb9_lcd.image import (
    FrameConfig,
    image_to_rgb,
    image_to_rgb565,
    image_to_rgb565_bytes,
    image_to_rgb_bytes,
)


def test_image_to_rgb_cover_center_crops_wide_image():
    image = Image.new("RGB", (6, 2))
    for y in range(2):
        image.putpixel((0, y), (255, 0, 0))
        image.putpixel((1, y), (255, 0, 0))
        image.putpixel((2, y), (0, 255, 0))
        image.putpixel((3, y), (0, 0, 255))
        image.putpixel((4, y), (255, 255, 255))
        image.putpixel((5, y), (255, 255, 255))

    frame = image_to_rgb_bytes(image, FrameConfig(width=2, height=2))

    assert frame == bytes(
        [
            0,
            255,
            0,
            0,
            0,
            255,
            0,
            255,
            0,
            0,
            0,
            255,
        ]
    )


def test_image_to_rgb_contain_adds_black_padding():
    image = Image.new("RGB", (2, 1), (255, 0, 0))

    frame = image_to_rgb_bytes(image, FrameConfig(width=4, height=4, fit="contain"))
    output = Image.frombytes("RGB", (4, 4), frame)

    assert output.getpixel((0, 0)) == (0, 0, 0)
    assert output.getpixel((3, 3)) == (0, 0, 0)
    assert output.getpixel((1, 1)) == (255, 0, 0)
    assert output.getpixel((2, 2)) == (255, 0, 0)


def test_image_to_rgb_contain_uses_configured_background_color():
    image = Image.new("RGB", (2, 1), (255, 0, 0))

    frame = image_to_rgb_bytes(image, FrameConfig(width=4, height=4, fit="contain", background="#123456"))
    output = Image.frombytes("RGB", (4, 4), frame)

    assert output.getpixel((0, 0)) == (0x12, 0x34, 0x56)
    assert output.getpixel((3, 3)) == (0x12, 0x34, 0x56)
    assert output.getpixel((1, 1)) == (255, 0, 0)


def test_image_to_rgb_stretch_resizes_without_preserving_aspect_ratio():
    image = Image.new("RGB", (2, 1))
    image.putpixel((0, 0), (255, 0, 0))
    image.putpixel((1, 0), (0, 0, 255))

    frame = image_to_rgb_bytes(image, FrameConfig(width=2, height=2, fit="stretch"))
    output = Image.frombytes("RGB", (2, 2), frame)

    assert output.getpixel((0, 0)) == (255, 0, 0)
    assert output.getpixel((0, 1)) == (255, 0, 0)
    assert output.getpixel((1, 0)) == (0, 0, 255)
    assert output.getpixel((1, 1)) == (0, 0, 255)


def test_image_to_rgb_rotates_after_resizing():
    image = Image.new("RGB", (2, 2))
    image.putpixel((0, 0), (255, 0, 0))
    image.putpixel((1, 0), (0, 255, 0))
    image.putpixel((0, 1), (0, 0, 255))
    image.putpixel((1, 1), (255, 255, 255))

    frame = image_to_rgb_bytes(image, FrameConfig(width=2, height=2, rotate=90))

    assert frame == bytes(
        [
            0,
            0,
            255,
            255,
            0,
            0,
            255,
            255,
            255,
            0,
            255,
            0,
        ]
    )


def test_image_to_rgb_accepts_path_strings(tmp_path: Path):
    image_path = tmp_path / "red.png"
    Image.new("RGB", (1, 1), "red").save(image_path)

    frame = image_to_rgb(str(image_path), FrameConfig(width=1, height=1))

    assert frame == bytes([255, 0, 0])


def test_image_to_rgb_applies_exif_orientation():
    image = Image.new("RGB", (1, 2))
    image.putpixel((0, 0), (255, 0, 0))
    image.putpixel((0, 1), (0, 0, 255))
    exif = Image.Exif()
    exif[274] = 6
    image.info["exif"] = exif.tobytes()

    frame = image_to_rgb_bytes(image, FrameConfig(width=2, height=1))

    assert frame == bytes([0, 0, 255, 255, 0, 0])


@pytest.mark.parametrize(
    ("rgb", "expected"),
    [
        ((255, 0, 0), bytes([0xF8, 0x00])),
        ((0, 255, 0), bytes([0x07, 0xE0])),
        ((0, 0, 255), bytes([0x00, 0x1F])),
        ((255, 255, 255), bytes([0xFF, 0xFF])),
        ((0, 0, 0), bytes([0x00, 0x00])),
        ((123, 45, 67), bytes([0x79, 0x68])),
    ],
)
def test_image_to_rgb565_bytes_encodes_known_colors(rgb, expected):
    image = Image.new("RGB", (1, 1), rgb)

    frame = image_to_rgb565_bytes(image, FrameConfig(width=1, height=1))

    assert frame == expected


def test_image_to_rgb565_path_wrapper(tmp_path: Path):
    image_path = tmp_path / "red.png"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(image_path)

    frame = image_to_rgb565(image_path, FrameConfig(width=1, height=1))

    assert frame == bytes([0xF8, 0x00])


@pytest.mark.parametrize("kwargs", [{"width": 0}, {"height": 0}])
def test_frame_config_rejects_non_positive_dimensions(kwargs):
    with pytest.raises(ValueError, match="width and height must be positive"):
        FrameConfig(**kwargs)


def test_frame_config_rejects_unsupported_rotation():
    with pytest.raises(ValueError, match="rotate must be one of"):
        FrameConfig(rotate=45)


def test_frame_config_rejects_invalid_background_color():
    with pytest.raises(ValueError, match="background must be a hex color"):
        FrameConfig(background="blue")
