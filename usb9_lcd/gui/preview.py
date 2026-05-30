from __future__ import annotations

from dataclasses import dataclass

from usb9_lcd.drivers.base import PixelStyle, PreviewProfile, PreviewShape


@dataclass(frozen=True)
class PreviewGeometry:
    viewport_width: int
    viewport_height: int
    screen_width: int
    screen_height: int
    offset_x: int
    offset_y: int
    scale: float
    border_radius: int
    pixel_gap: int


def fit_preview_geometry(
    profile: PreviewProfile,
    viewport_width: int,
    viewport_height: int,
    padding: int = 24,
) -> PreviewGeometry:
    """Fit a display preview inside a viewport without depending on GUI bindings."""
    if viewport_width <= 0 or viewport_height <= 0:
        raise ValueError("viewport width and height must be positive")
    if padding < 0:
        raise ValueError("padding must be zero or positive")

    available_width = viewport_width - (padding * 2)
    available_height = viewport_height - (padding * 2)
    if available_width <= 0 or available_height <= 0:
        raise ValueError("viewport and padding leave no available preview space")

    scale = min(available_width / profile.width, available_height / profile.height)
    screen_width = max(1, round(profile.width * scale))
    screen_height = max(1, round(profile.height * scale))
    offset_x = (viewport_width - screen_width) // 2
    offset_y = (viewport_height - screen_height) // 2

    if profile.shape is PreviewShape.CIRCLE:
        border_radius = min(screen_width, screen_height) // 2
    else:
        border_radius = min(8, screen_width // 2, screen_height // 2)

    pixel_gap = 0
    if profile.shape is PreviewShape.MATRIX or profile.pixel_style is PixelStyle.MATRIX:
        pixel_gap = max(1, round(scale))

    return PreviewGeometry(
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        screen_width=screen_width,
        screen_height=screen_height,
        offset_x=offset_x,
        offset_y=offset_y,
        scale=scale,
        border_radius=border_radius,
        pixel_gap=pixel_gap,
    )
