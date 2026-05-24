from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from usb9_lcd.device import choose_interfaces, discover_from_sysfs
from usb9_lcd.protocol import LcdProtocol
from usb9_lcd.transport import HidrawTransport


WIDTH = 320
HEIGHT = 320
PIXELS = WIDTH * HEIGHT
DATA_COMMAND = 0x08


@dataclass(frozen=True)
class Variant:
    name: str
    frame_factory: Callable[[], bytes]
    chunk_payload_size: int = 1020
    command: int = DATA_COMMAND
    first_count_delta: int = 0
    first_flag: int = 0x8000
    last_flag: int = 0
    write_report_id: bool = True
    pre_control: tuple[bytes, ...] = ()
    post_control: tuple[bytes, ...] = ()
    screen_type_before_data: int | None = None
    screen_type_after_data: int | None = 2


def rgb565_be(red: int, green: int, blue: int) -> bytes:
    value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
    return bytes([(value >> 8) & 0xFF, value & 0xFF])


def rgb565_le(red: int, green: int, blue: int) -> bytes:
    pair = rgb565_be(red, green, blue)
    return bytes([pair[1], pair[0]])


def repeat_pixel(pixel: bytes) -> bytes:
    return pixel * PIXELS


def red_rgb888() -> bytes:
    return bytes([0xFF, 0x00, 0x00]) * PIXELS


def red_bgr888() -> bytes:
    return bytes([0x00, 0x00, 0xFF]) * PIXELS


def red_bgra32() -> bytes:
    return bytes([0x00, 0x00, 0xFF, 0xFF]) * PIXELS


def red_rgba32() -> bytes:
    return bytes([0xFF, 0x00, 0x00, 0xFF]) * PIXELS


def red_argb32() -> bytes:
    return bytes([0xFF, 0xFF, 0x00, 0x00]) * PIXELS


def red_abgr32() -> bytes:
    return bytes([0xFF, 0x00, 0x00, 0xFF]) * PIXELS


def red_xrgb32() -> bytes:
    return bytes([0x00, 0xFF, 0x00, 0x00]) * PIXELS


def red_bgrx32() -> bytes:
    return bytes([0x00, 0x00, 0xFF, 0x00]) * PIXELS


def red_1bpp_expanded() -> bytes:
    return bytes([0xFF]) * (PIXELS // 8)


def red_with_size_prefix(frame_factory: Callable[[], bytes]) -> bytes:
    frame = frame_factory()
    return len(frame).to_bytes(4, "little") + frame


def red_with_xy_header(frame_factory: Callable[[], bytes]) -> bytes:
    frame = frame_factory()
    return WIDTH.to_bytes(2, "little") + HEIGHT.to_bytes(2, "little") + len(frame).to_bytes(4, "little") + frame


def variants() -> list[Variant]:
    rgb565_be_red = lambda: repeat_pixel(rgb565_be(255, 0, 0))
    rgb565_le_red = lambda: repeat_pixel(rgb565_le(255, 0, 0))
    return [
        Variant("rgb565_be_red_204800", rgb565_be_red),
        Variant("rgb565_le_red_204800", rgb565_le_red),
        Variant("rgb565_be_red_first_count_minus_one", rgb565_be_red, first_count_delta=-1),
        Variant("rgb565_be_red_first_flag_big_endian_bytes", rgb565_be_red, first_flag=0x0080),
        Variant("rgb565_be_red_last_flag_4000", rgb565_be_red, last_flag=0x4000),
        Variant("rgb565_be_red_last_flag_0001", rgb565_be_red, last_flag=0x0001),
        Variant("rgb565_be_red_size_prefix", lambda: red_with_size_prefix(rgb565_be_red)),
        Variant("rgb565_be_red_xy_size_prefix", lambda: red_with_xy_header(rgb565_be_red)),
        Variant("rgb888_red_307200", red_rgb888),
        Variant("bgr888_red_307200", red_bgr888),
        Variant("bgra32_red_409600", red_bgra32),
        Variant("rgba32_red_409600", red_rgba32),
        Variant("argb32_red_409600", red_argb32),
        Variant("xrgb32_red_409600", red_xrgb32),
        Variant("bgrx32_red_409600", red_bgrx32),
        Variant("bgra32_red_count_minus_one", red_bgra32, first_count_delta=-1),
        Variant("bgra32_red_last_flag_4000", red_bgra32, last_flag=0x4000),
        Variant("bgra32_red_size_prefix", lambda: red_with_size_prefix(red_bgra32)),
        Variant("bgra32_red_xy_size_prefix", lambda: red_with_xy_header(red_bgra32)),
        Variant("bgra32_red_pre_30_size", red_bgra32, pre_control=(bytes.fromhex("30010080") + (WIDTH * HEIGHT * 4).to_bytes(4, "little"),)),
        Variant("bgra32_red_post_30010080", red_bgra32, post_control=(bytes.fromhex("30010080"),)),
        Variant("bgra32_red_post_1001008000", red_bgra32, post_control=(bytes.fromhex("1001008000"),)),
        Variant("bgra32_red_post_1001008001", red_bgra32, post_control=(bytes.fromhex("1001008001"),)),
        Variant("bgra32_red_screen1_before_data_no_after", red_bgra32, screen_type_before_data=1, screen_type_after_data=None),
        Variant("bgra32_red_no_screen_switch", red_bgra32, screen_type_after_data=None),
        Variant("bgra32_red_screen1_after_data", red_bgra32, screen_type_after_data=1),
        Variant("rgb565_be_red_screen1_before_data_no_after", rgb565_be_red, screen_type_before_data=1, screen_type_after_data=None),
        Variant("rgb565_be_red_no_screen_switch", rgb565_be_red, screen_type_after_data=None),
        Variant(
            "bgra32_red_official_power_brightness_no_screen_switch",
            red_bgra32,
            pre_control=(bytes.fromhex("1001008001"), bytes.fromhex("1201008064")),
            screen_type_after_data=None,
        ),
        Variant(
            "bgra32_red_screen1_official_power_brightness_no_after",
            red_bgra32,
            screen_type_before_data=1,
            pre_control=(bytes.fromhex("1001008001"), bytes.fromhex("1201008064")),
            screen_type_after_data=None,
        ),
        Variant(
            "bgra32_red_official_power_brightness_screen1_after",
            red_bgra32,
            pre_control=(bytes.fromhex("1001008001"), bytes.fromhex("1201008064")),
            screen_type_after_data=1,
        ),
        Variant(
            "rgb565_red_official_power_brightness_no_screen_switch",
            rgb565_be_red,
            pre_control=(bytes.fromhex("1001008001"), bytes.fromhex("1201008064")),
            screen_type_after_data=None,
        ),
        Variant("bgra32_red_no_report_id", red_bgra32, write_report_id=False),
        Variant("rgb565_be_red_no_report_id", rgb565_be_red, write_report_id=False),
        Variant("rgb565_be_red_command_09", rgb565_be_red, command=0x09),
        Variant("bgra32_red_command_09", red_bgra32, command=0x09),
    ]


def write_exact(handle, payload: bytes) -> None:
    written = handle.write(payload)
    if written != len(payload):
        raise OSError(f"short HID write: wrote {written} of {len(payload)} bytes")


def control_packet(command: bytes, report_size: int) -> bytes:
    if len(command) > report_size:
        raise ValueError("control command is larger than report size")
    return b"\x00" + command + bytes(report_size - len(command))


def restore_default(protocol: LcdProtocol) -> None:
    protocol.set_display_power(False)
    protocol.set_display_brightness(100)
    protocol.set_screen_type(1)


def send_variant(protocol: LcdProtocol, data_transport, control_transport, control_report_size: int, data_report_size: int, variant: Variant) -> tuple[int, int]:
    frame = variant.frame_factory()
    chunks = [frame[offset : offset + variant.chunk_payload_size] for offset in range(0, len(frame), variant.chunk_payload_size)]
    if not chunks:
        raise ValueError("variant produced empty frame")

    if variant.screen_type_before_data is not None:
        protocol.set_screen_type(variant.screen_type_before_data)
        time.sleep(0.05)

    for command in variant.pre_control:
        write_exact(control_transport, control_packet(command, control_report_size))
        time.sleep(0.05)

    for index, chunk in enumerate(chunks):
        number = len(chunks) + variant.first_count_delta if index == 0 else index
        flag = variant.first_flag if index == 0 else (variant.last_flag if index == len(chunks) - 1 else 0)
        payload = bytes([variant.command, number & 0xFF]) + flag.to_bytes(2, "little") + chunk
        if len(payload) > data_report_size:
            raise ValueError(f"{variant.name}: packet payload {len(payload)} exceeds data report size {data_report_size}")
        payload += bytes(data_report_size - len(payload))
        report = (b"\x00" + payload) if variant.write_report_id else payload
        write_exact(data_transport, report)

    if variant.screen_type_after_data is not None:
        protocol.set_screen_type(variant.screen_type_after_data)

    for command in variant.post_control:
        time.sleep(0.05)
        write_exact(control_transport, control_packet(command, control_report_size))

    return len(chunks), len(frame)


def parse_range(value: str | None, total: int) -> range:
    if not value:
        return range(1, total + 1)
    if "-" in value:
        start_text, end_text = value.split("-", 1)
        start = int(start_text)
        end = int(end_text)
    else:
        start = end = int(value)
    if start < 1 or end > total or start > end:
        raise ValueError(f"range must be between 1 and {total}")
    return range(start, end + 1)


def countdown(seconds: float) -> None:
    whole = max(0, int(seconds))
    for remaining in range(whole, 0, -1):
        print(f"  observe now: {remaining}s remaining", flush=True)
        time.sleep(1)
    fraction = seconds - whole
    if fraction > 0:
        time.sleep(fraction)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run ASUS LCD protocol probes with visible pauses.")
    parser.add_argument("--pause", type=float, default=5.0, help="seconds to show each variant before restoring")
    parser.add_argument("--settle", type=float, default=0.4, help="seconds to wait after restoring default before each variant")
    parser.add_argument("--range", dest="variant_range", help="1-based variant number or range, for example 3 or 3-8")
    parser.add_argument("--list", action="store_true", help="list variants and exit")
    parser.add_argument("--no-final-restore", action="store_true", help="leave the last variant on screen")
    args = parser.parse_args(argv)

    all_variants = variants()
    if args.list:
        for index, variant in enumerate(all_variants, start=1):
            print(f"{index:02d}: {variant.name}")
        return 0

    try:
        selected_range = parse_range(args.variant_range, len(all_variants))
    except ValueError as error:
        print(error)
        return 1

    interfaces = discover_from_sysfs()
    if not interfaces:
        print("ASUS LCD 0b05:1c7b not found")
        return 1
    control, data = choose_interfaces(interfaces)

    print(f"control={control.path} report_size={control.report_size}")
    print(f"data={data.path} report_size={data.report_size}")
    print("Watch the LCD. If it turns red, note the variant number and press Ctrl+C.")

    try:
        with HidrawTransport(control.path) as control_transport, HidrawTransport(data.path) as data_transport:
            protocol = LcdProtocol(control_transport, data_transport, data_report_size=data.report_size)
            for index in selected_range:
                variant = all_variants[index - 1]
                print(f"\n[{index:02d}/{len(all_variants):02d}] {variant.name}", flush=True)
                restore_default(protocol)
                time.sleep(args.settle)
                packets, byte_count = send_variant(
                    protocol,
                    data_transport,
                    control_transport,
                    control.report_size,
                    data.report_size,
                    variant,
                )
                print(f"  sent packets={packets} bytes={byte_count}", flush=True)
                countdown(args.pause)

            if not args.no_final_restore:
                print("\nrestoring default", flush=True)
                restore_default(protocol)
    except KeyboardInterrupt:
        print("\ninterrupted by user", flush=True)
        return 130
    except Exception as error:
        print(f"\nprobe failed: {error}", file=sys.stderr)
        return 1

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
