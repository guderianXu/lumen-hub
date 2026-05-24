from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from usb9_lcd.drivers.base import Capability, DisplayDevice, PixelFormat
from usb9_lcd.image import FrameConfig, image_to_jpeg_bytes, image_to_rgb565_bytes

from .models import SystemTelemetry


Color = tuple[int, int, int]
Box = tuple[int, int, int, int]

BACKGROUND: Color = (7, 10, 15)
PANEL: Color = (13, 18, 27)
CARD: Color = (20, 27, 38)
BORDER: Color = (48, 59, 76)
MUTED: Color = (148, 163, 184)
TEXT: Color = (226, 232, 240)
CPU_ACCENT: Color = (70, 229, 150)
GPU_ACCENT: Color = (96, 185, 255)
WARN_ACCENT: Color = (245, 158, 66)
SMALL_LAYOUT_MIN_WIDTH = 120
SMALL_LAYOUT_MIN_HEIGHT = 100
MonitorLayout = Literal["balanced", "gpu_focus", "minimal", "details", "overlay"]
MonitorPaletteName = Literal["neon", "amber", "ice", "terminal", "contrast"]


@dataclass(frozen=True)
class MonitorPalette:
    background: Color
    panel: Color
    card: Color
    border: Color
    muted: Color
    text: Color
    cpu: Color
    gpu: Color
    warn: Color


PALETTES: dict[MonitorPaletteName, MonitorPalette] = {
    "neon": MonitorPalette(
        background=BACKGROUND,
        panel=PANEL,
        card=CARD,
        border=BORDER,
        muted=MUTED,
        text=TEXT,
        cpu=CPU_ACCENT,
        gpu=GPU_ACCENT,
        warn=WARN_ACCENT,
    ),
    "amber": MonitorPalette(
        background=(10, 9, 7),
        panel=(24, 20, 14),
        card=(36, 29, 18),
        border=(86, 66, 37),
        muted=(193, 166, 112),
        text=(255, 244, 214),
        cpu=(255, 193, 92),
        gpu=(255, 126, 67),
        warn=(255, 72, 72),
    ),
    "ice": MonitorPalette(
        background=(5, 12, 18),
        panel=(10, 24, 35),
        card=(14, 35, 50),
        border=(48, 91, 115),
        muted=(150, 187, 205),
        text=(232, 249, 255),
        cpu=(117, 255, 214),
        gpu=(111, 190, 255),
        warn=(255, 210, 105),
    ),
    "terminal": MonitorPalette(
        background=(2, 9, 5),
        panel=(6, 22, 12),
        card=(8, 33, 18),
        border=(31, 99, 54),
        muted=(119, 176, 137),
        text=(221, 255, 229),
        cpu=(88, 255, 139),
        gpu=(53, 224, 106),
        warn=(255, 204, 77),
    ),
    "contrast": MonitorPalette(
        background=(0, 0, 0),
        panel=(14, 14, 14),
        card=(28, 28, 28),
        border=(110, 110, 110),
        muted=(190, 190, 190),
        text=(255, 255, 255),
        cpu=(0, 255, 160),
        gpu=(0, 176, 255),
        warn=(255, 68, 68),
    ),
}


@dataclass(frozen=True)
class MonitorRenderSettings:
    layout: MonitorLayout = "balanced"
    palette: MonitorPaletteName = "neon"
    background_path: Path | None = None
    show_cpu_temp: bool = True
    show_gpu_temp: bool = True
    show_gpu_load: bool = True
    show_gpu_power: bool = True
    show_vram: bool = True
    show_gpu_clock: bool = False
    show_cpu_load: bool = True
    show_time: bool = True
    show_labels: bool = True
    background_dim: float = 0.55
    jpeg_quality: int = 92

    def __post_init__(self) -> None:
        if self.layout not in ("balanced", "gpu_focus", "minimal", "details", "overlay"):
            raise ValueError("monitor layout is not supported")
        if self.palette not in PALETTES:
            raise ValueError("monitor palette is not supported")
        if not 0 <= self.background_dim <= 1:
            raise ValueError("background_dim must be between 0 and 1")
        if not 1 <= self.jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be between 1 and 100")


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _fit_font(text: str, size: int, max_width: int, min_size: int = 8) -> ImageFont.ImageFont:
    size = max(size, min_size)
    while size > min_size:
        font = _font(size)
        if font.getlength(text) <= max_width:
            return font
        size -= 1
    return _font(min_size)


def _value(value: object, suffix: str = "", decimals: int = 0) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{decimals}f}{suffix}"
    return f"{value}{suffix}"


def _palette(settings: MonitorRenderSettings) -> MonitorPalette:
    return PALETTES[settings.palette]


def _round_box(draw: ImageDraw.ImageDraw, box: Box, fill: Color, outline: Color) -> None:
    x1, y1, x2, y2 = box
    if x2 <= x1 or y2 <= y1:
        return
    radius = max(1, min(x2 - x1, y2 - y1) // 10)
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=2)


def _card(draw: ImageDraw.ImageDraw, box: Box, title: str, value: str, accent: Color, palette: MonitorPalette) -> None:
    x1, y1, x2, y2 = box
    width = x2 - x1
    height = y2 - y1
    if width <= 0 or height <= 0:
        return

    _round_box(draw, box, palette.card, palette.border)
    pad = max(4, min(8, min(width, height) // 9))

    title_font = _fit_font(title, max(10, height // 8), width - pad * 2)
    value_font = _fit_font(value, max(20, height // 3), width - pad * 2, min_size=12)
    draw.text((x1 + pad, y1 + pad), title, fill=palette.muted, font=title_font)
    draw.text((x1 + pad, y1 + height // 2 - value_font.size // 2), value, fill=accent, font=value_font)
    draw.line((x1 + pad, y2 - pad, x2 - pad, y2 - pad), fill=accent, width=max(2, height // 28))


def _draw_info_lines(draw: ImageDraw.ImageDraw, lines: list[tuple[str, Color]], box: Box) -> None:
    x1, y1, x2, y2 = box
    if x2 <= x1 or y2 <= y1:
        return
    available_height = max(1, y2 - y1)
    line_height = max(16, available_height // max(1, len(lines)))
    font_size = max(10, min(24, int(line_height * 0.58)))
    for index, (line, fill) in enumerate(lines):
        y = y1 + index * line_height
        font = _fit_font(line, font_size, x2 - x1, min_size=8)
        draw.text((x1, y), line, fill=fill, font=font)


def _draw_text_fit(
    draw: ImageDraw.ImageDraw,
    box: Box,
    text: str,
    fill: Color,
    *,
    max_size: int,
    min_size: int = 10,
    anchor_center: bool = False,
) -> None:
    x1, y1, x2, y2 = box
    font = _fit_font(text, max_size, max(1, x2 - x1), min_size=min_size)
    if anchor_center:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = x1 + max(0, (x2 - x1 - text_width) // 2)
        y = y1 + max(0, (y2 - y1 - text_height) // 2)
    else:
        x, y = x1, y1
    draw.text((x, y), text, fill=fill, font=font)


def _draw_small_layout(
    draw: ImageDraw.ImageDraw,
    telemetry: SystemTelemetry,
    width: int,
    height: int,
    settings: MonitorRenderSettings,
) -> None:
    palette = _palette(settings)
    block = max(1, min(width, height) // 4)
    gap = max(1, min(width, height) // 16)
    cpu_color = palette.cpu if telemetry.cpu.available else palette.warn
    gpu_color = palette.gpu if telemetry.gpu.available else palette.warn

    def rectangle(box: Box, fill: Color) -> None:
        x1, y1, x2, y2 = box
        x1 = max(0, min(width - 1, x1))
        y1 = max(0, min(height - 1, y1))
        x2 = max(0, min(width - 1, x2))
        y2 = max(0, min(height - 1, y2))
        if x2 >= x1 and y2 >= y1:
            draw.rectangle((x1, y1, x2, y2), fill=fill)

    rectangle((0, 0, width - 1, height - 1), palette.background)
    rectangle((gap, gap, gap + block, gap + block), cpu_color)

    right_x = max(gap, width - gap - block - 1)
    rectangle((right_x, gap, right_x + block, gap + block), gpu_color)

    if width >= 48 and height >= 24:
        text = _value(telemetry.cpu.package_temperature_c, "°C")
        font = _fit_font(text, max(8, height // 5), max(1, width - gap * 2), min_size=8)
        draw.text((gap, min(height - 1, gap * 2 + block)), text, fill=palette.text, font=font)


def _background_image(width: int, height: int, settings: MonitorRenderSettings) -> Image.Image:
    if settings.background_path is not None:
        try:
            with Image.open(settings.background_path) as background:
                image = ImageOps.fit(
                    ImageOps.exif_transpose(background).convert("RGB"),
                    (width, height),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
        except OSError:
            image = Image.new("RGB", (width, height), _palette(settings).background)
    else:
        image = Image.new("RGB", (width, height), _palette(settings).background)

    if settings.background_dim > 0:
        image = ImageEnhance.Brightness(image).enhance(max(0.0, 1.0 - settings.background_dim))
    return image


def _data_lines(telemetry: SystemTelemetry, settings: MonitorRenderSettings) -> list[tuple[str, Color]]:
    palette = _palette(settings)
    lines: list[tuple[str, Color]] = []
    if settings.show_gpu_load:
        lines.append((f"GPU LOAD {_value(telemetry.gpu.utilization_percent, '%')}", palette.gpu))
    if settings.show_gpu_power:
        lines.append((f"GPU PWR  {_value(telemetry.gpu.power_w, 'W')}", palette.gpu))
    if settings.show_vram:
        lines.append(
            (
                f"VRAM {_value(telemetry.gpu.memory_used_mb, 'MB')} / "
                f"{_value(telemetry.gpu.memory_total_mb, 'MB')}",
                palette.muted,
            )
        )
    if settings.show_gpu_clock:
        lines.append((f"GPU CLK  {_value(telemetry.gpu.graphics_clock_mhz, 'MHz')}", palette.muted))
    if settings.show_cpu_load:
        lines.append((f"CPU LOAD {_value(telemetry.cpu.utilization_percent, '%')}", palette.cpu))
    return lines


def _draw_header(
    draw: ImageDraw.ImageDraw,
    telemetry: SystemTelemetry,
    settings: MonitorRenderSettings,
    box: Box,
) -> int:
    x1, y1, x2, _y2 = box
    palette = _palette(settings)
    title_text = telemetry.gpu.name if telemetry.gpu.available and telemetry.gpu.name else "USB9 LCD MONITOR"
    header_font = _fit_font(title_text, max(14, (x2 - x1) // 16), x2 - x1)
    draw.text((x1, y1), title_text, fill=palette.text, font=header_font)
    used = header_font.size
    if settings.show_time:
        time_text = telemetry.captured_at.strftime("%H:%M:%S")
        time_font = _fit_font(time_text, max(10, (x2 - x1) // 24), x2 - x1, min_size=8)
        draw.text((x1, y1 + used + 2), time_text, fill=palette.muted, font=time_font)
        used += time_font.size + 2
    return used


def _temp_cards(telemetry: SystemTelemetry, settings: MonitorRenderSettings) -> list[tuple[str, str, Color]]:
    palette = _palette(settings)
    cards: list[tuple[str, str, Color]] = []
    if settings.show_cpu_temp:
        cards.append(("CPU TEMP" if settings.show_labels else "CPU", _value(telemetry.cpu.package_temperature_c, "°C"), palette.cpu))
    if settings.show_gpu_temp:
        cards.append(("GPU TEMP" if settings.show_labels else "GPU", _value(telemetry.gpu.temperature_c, "°C"), palette.gpu))
    return cards


def _draw_balanced_layout(
    draw: ImageDraw.ImageDraw,
    telemetry: SystemTelemetry,
    settings: MonitorRenderSettings,
    width: int,
    height: int,
    margin: int,
) -> None:
    palette = _palette(settings)
    panel_box = (margin, margin, width - margin, height - margin)
    _round_box(draw, panel_box, palette.panel, palette.border)

    inner_x = margin * 2
    inner_right = width - margin * 2
    header_y = margin + max(7, height // 48)
    header_used = _draw_header(draw, telemetry, settings, (inner_x, header_y, inner_right, height))

    cards_top = header_y + header_used + max(10, height // 28)
    card_gap = max(8, width // 48)
    card_height = max(1, min(height // 3, height - cards_top - margin * 5))
    card_width = max(1, (inner_right - inner_x - card_gap) // 2)
    cards = _temp_cards(telemetry, settings)

    if len(cards) == 1:
        _card(draw, (inner_x, cards_top, inner_right, cards_top + card_height), *cards[0], palette)
    elif len(cards) >= 2:
        _card(draw, (inner_x, cards_top, inner_x + card_width, cards_top + card_height), *cards[0], palette)
        _card(draw, (inner_x + card_width + card_gap, cards_top, inner_right, cards_top + card_height), *cards[1], palette)

    info_top = cards_top + card_height + max(10, height // 28)
    _draw_info_lines(draw, _data_lines(telemetry, settings), (inner_x, info_top, inner_right, height - margin * 2))
    draw.line((inner_x, height - margin * 2, inner_right, height - margin * 2), fill=palette.warn, width=1)


def _draw_gpu_focus_layout(
    draw: ImageDraw.ImageDraw,
    telemetry: SystemTelemetry,
    settings: MonitorRenderSettings,
    width: int,
    height: int,
    margin: int,
) -> None:
    palette = _palette(settings)
    _round_box(draw, (margin, margin, width - margin, height - margin), palette.panel, palette.border)
    inner_x = margin * 2
    inner_right = width - margin * 2
    top = margin * 2
    gpu_value = _value(telemetry.gpu.temperature_c, "°C")
    _draw_text_fit(draw, (inner_x, top, inner_right, top + height // 3), gpu_value, palette.gpu, max_size=height // 4, min_size=26, anchor_center=True)
    label = "GPU TEMP" if settings.show_labels else "GPU"
    _draw_text_fit(draw, (inner_x, top + height // 3, inner_right, top + height // 3 + 28), label, palette.text, max_size=22, min_size=10, anchor_center=True)
    lines = _data_lines(telemetry, settings)
    if settings.show_cpu_temp:
        lines.append((f"CPU TEMP {_value(telemetry.cpu.package_temperature_c, '°C')}", palette.cpu))
    _draw_info_lines(draw, lines, (inner_x, top + height // 3 + 40, inner_right, height - margin * 2))


def _draw_minimal_layout(
    draw: ImageDraw.ImageDraw,
    telemetry: SystemTelemetry,
    settings: MonitorRenderSettings,
    width: int,
    height: int,
    margin: int,
) -> None:
    palette = _palette(settings)
    left = margin * 2
    right = width - margin * 2
    mid = height // 2
    if settings.show_gpu_temp:
        _draw_text_fit(draw, (left, margin, right, mid), f"GPU {_value(telemetry.gpu.temperature_c, '°C')}", palette.gpu, max_size=height // 5, min_size=18, anchor_center=True)
    if settings.show_cpu_temp:
        _draw_text_fit(draw, (left, mid, right, height - margin), f"CPU {_value(telemetry.cpu.package_temperature_c, '°C')}", palette.cpu, max_size=height // 5, min_size=18, anchor_center=True)
    if settings.show_time:
        _draw_text_fit(draw, (left, height - margin * 3, right, height - margin), telemetry.captured_at.strftime("%H:%M"), palette.muted, max_size=18, min_size=8, anchor_center=True)


def _draw_details_layout(
    draw: ImageDraw.ImageDraw,
    telemetry: SystemTelemetry,
    settings: MonitorRenderSettings,
    width: int,
    height: int,
    margin: int,
) -> None:
    palette = _palette(settings)
    _round_box(draw, (margin, margin, width - margin, height - margin), palette.panel, palette.border)
    inner_x = margin * 2
    inner_right = width - margin * 2
    y = margin * 2
    y += _draw_header(draw, telemetry, settings, (inner_x, y, inner_right, height)) + max(8, height // 32)
    lines: list[tuple[str, Color]] = []
    if settings.show_gpu_temp:
        lines.append((f"GPU TEMP {_value(telemetry.gpu.temperature_c, '°C')}", palette.gpu))
    if settings.show_cpu_temp:
        lines.append((f"CPU TEMP {_value(telemetry.cpu.package_temperature_c, '°C')}", palette.cpu))
    lines.extend(_data_lines(telemetry, settings))
    _draw_info_lines(draw, lines, (inner_x, y, inner_right, height - margin * 2))


def _draw_overlay_layout(
    draw: ImageDraw.ImageDraw,
    telemetry: SystemTelemetry,
    settings: MonitorRenderSettings,
    width: int,
    height: int,
    margin: int,
) -> None:
    palette = _palette(settings)
    top_band = (margin, margin, width - margin, margin + height // 4)
    bottom_band = (margin, height - margin - height // 4, width - margin, height - margin)
    _round_box(draw, top_band, (0, 0, 0), outline=palette.border)
    _round_box(draw, bottom_band, (0, 0, 0), outline=palette.border)
    if settings.show_gpu_temp:
        _draw_text_fit(draw, top_band, f"GPU {_value(telemetry.gpu.temperature_c, '°C')}", palette.gpu, max_size=height // 8, min_size=16, anchor_center=True)
    bottom_text = []
    if settings.show_cpu_temp:
        bottom_text.append(f"CPU {_value(telemetry.cpu.package_temperature_c, '°C')}")
    if settings.show_gpu_load:
        bottom_text.append(f"LOAD {_value(telemetry.gpu.utilization_percent, '%')}")
    _draw_text_fit(draw, bottom_band, "  ".join(bottom_text) or telemetry.captured_at.strftime("%H:%M"), palette.text, max_size=height // 12, min_size=12, anchor_center=True)


def render_monitoring_image(
    telemetry: SystemTelemetry,
    device: DisplayDevice,
    settings: MonitorRenderSettings = MonitorRenderSettings(),
) -> Image.Image:
    width = device.width
    height = device.height
    image = _background_image(width, height, settings)
    draw = ImageDraw.Draw(image)

    if width < SMALL_LAYOUT_MIN_WIDTH or height < SMALL_LAYOUT_MIN_HEIGHT:
        _draw_small_layout(draw, telemetry, width, height, settings)
        return image

    margin = max(8, min(width, height) // 24)
    if settings.layout == "gpu_focus":
        _draw_gpu_focus_layout(draw, telemetry, settings, width, height, margin)
    elif settings.layout == "minimal":
        _draw_minimal_layout(draw, telemetry, settings, width, height, margin)
    elif settings.layout == "details":
        _draw_details_layout(draw, telemetry, settings, width, height, margin)
    elif settings.layout == "overlay":
        _draw_overlay_layout(draw, telemetry, settings, width, height, margin)
    else:
        _draw_balanced_layout(draw, telemetry, settings, width, height, margin)
    return image


def render_monitoring_frame(
    telemetry: SystemTelemetry,
    device: DisplayDevice,
    settings: MonitorRenderSettings = MonitorRenderSettings(),
) -> bytes:
    if not device.supports(Capability.STATIC_IMAGE):
        raise ValueError("selected device does not support static images")
    image = render_monitoring_image(telemetry, device, settings)
    config = FrameConfig(
        width=device.width,
        height=device.height,
        fit="stretch",
        rotate=device.preview.orientation,
    )
    if device.pixel_format is PixelFormat.RGB565:
        return image_to_rgb565_bytes(image, config)
    if device.pixel_format is PixelFormat.JPEG:
        return image_to_jpeg_bytes(image, config, quality=settings.jpeg_quality)
    raise ValueError(f"selected device pixel format is not supported: {device.pixel_format}")
