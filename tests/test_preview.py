import pytest

from usb9_lcd.drivers.base import PixelStyle, PreviewProfile, PreviewShape
from usb9_lcd.gui.preview import PreviewGeometry, fit_preview_geometry


def test_asus_square_preview_is_not_treated_as_circle():
    profile = PreviewProfile(
        width=480,
        height=480,
        shape=PreviewShape.SQUARE,
        pixel_style=PixelStyle.CONTINUOUS,
    )

    geometry = fit_preview_geometry(profile, viewport_width=600, viewport_height=400)

    assert isinstance(geometry, PreviewGeometry)
    assert geometry.screen_width == 352
    assert geometry.screen_height == 352
    assert geometry.offset_x == 124
    assert geometry.offset_y == 24
    assert geometry.scale == pytest.approx(352 / 480)
    assert geometry.border_radius == 8
    assert geometry.pixel_gap == 0


def test_wide_rectangle_preview_fits_available_viewport_with_aspect_ratio():
    profile = PreviewProfile(
        width=800,
        height=200,
        shape=PreviewShape.RECTANGLE,
        pixel_style=PixelStyle.CONTINUOUS,
    )

    geometry = fit_preview_geometry(profile, viewport_width=500, viewport_height=500, padding=50)

    assert geometry.screen_width == 400
    assert geometry.screen_height == 100
    assert geometry.offset_x == 50
    assert geometry.offset_y == 200
    assert geometry.scale == pytest.approx(0.5)
    assert geometry.border_radius == 8


def test_circle_preview_uses_half_display_size_for_radius():
    profile = PreviewProfile(
        width=320,
        height=320,
        shape=PreviewShape.CIRCLE,
        pixel_style=PixelStyle.CONTINUOUS,
    )

    geometry = fit_preview_geometry(profile, viewport_width=400, viewport_height=300, padding=20)

    assert geometry.screen_width == 260
    assert geometry.screen_height == 260
    assert geometry.border_radius == 130
    assert geometry.pixel_gap == 0


@pytest.mark.parametrize(
    ("shape", "pixel_style"),
    [
        (PreviewShape.MATRIX, PixelStyle.CONTINUOUS),
        (PreviewShape.RECTANGLE, PixelStyle.MATRIX),
    ],
)
def test_matrix_preview_uses_positive_pixel_gap(shape, pixel_style):
    profile = PreviewProfile(width=64, height=32, shape=shape, pixel_style=pixel_style)

    geometry = fit_preview_geometry(profile, viewport_width=200, viewport_height=120, padding=10)

    assert geometry.pixel_gap > 0


@pytest.mark.parametrize(
    ("viewport_width", "viewport_height", "padding"),
    [
        (0, 100, 24),
        (100, -1, 24),
        (48, 100, 24),
        (100, 100, -1),
    ],
)
def test_fit_preview_geometry_rejects_invalid_viewport_or_padding(
    viewport_width, viewport_height, padding
):
    profile = PreviewProfile(
        width=480,
        height=480,
        shape=PreviewShape.SQUARE,
        pixel_style=PixelStyle.CONTINUOUS,
    )

    with pytest.raises(ValueError, match="viewport|padding|available"):
        fit_preview_geometry(profile, viewport_width, viewport_height, padding=padding)
