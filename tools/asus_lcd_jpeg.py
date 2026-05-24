from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from usb9_lcd.device import choose_interfaces, discover_from_sysfs
from usb9_lcd.image import FrameConfig, image_to_rgb_bytes
from usb9_lcd.transport import HidrawTransport


WIDTH = 320
HEIGHT = 320
CHUNK_SIZE = 1020
DATA_REPORT_SIZE = 1024
DATA_COMMAND = 0x08

LCD_ON = "1001008000"
BRIGHTNESS_PREFIX = "12010080"
SCREEN_DISPLAY_1 = "1f01008001"


def write_exact(handle, payload: bytes) -> None:
    written = handle.write(payload)
    if written != len(payload):
        raise OSError(f"short HID write: wrote {written} of {len(payload)} bytes")


def control_packet(command: bytes, report_size: int) -> bytes:
    if len(command) > report_size:
        raise ValueError("control command is larger than report size")
    return b"\x00" + command + bytes(report_size - len(command))


def send_control(handle, report_size: int, hex_command: str, delay: float = 0.12) -> None:
    write_exact(handle, control_packet(bytes.fromhex(hex_command), report_size))
    if delay > 0:
        time.sleep(delay)


def prepare_display(control_handle, report_size: int, brightness: int) -> None:
    if brightness < 0 or brightness > 255:
        raise ValueError("brightness must be between 0 and 255")
    send_control(control_handle, report_size, LCD_ON)
    send_control(control_handle, report_size, BRIGHTNESS_PREFIX + f"{brightness:02x}")
    send_control(control_handle, report_size, SCREEN_DISPLAY_1)


def image_to_jpeg_bytes(image: Image.Image, quality: int, rotate: int) -> bytes:
    rgb_bytes = image_to_rgb_bytes(
        image,
        FrameConfig(width=WIDTH, height=HEIGHT, fit="cover", rotate=rotate, background="#000000"),
    )
    fitted = Image.frombytes("RGB", (WIDTH, HEIGHT), rgb_bytes)
    output = io.BytesIO()
    fitted.save(output, format="JPEG", quality=quality, subsampling=0)
    return output.getvalue()


def red_jpeg_bytes(quality: int) -> bytes:
    with Image.new("RGB", (WIDTH, HEIGHT), (255, 0, 0)) as image:
        return image_to_jpeg_bytes(image, quality, rotate=0)


def file_jpeg_bytes(path: Path, quality: int, rotate: int) -> bytes:
    with Image.open(path) as image:
        return image_to_jpeg_bytes(image, quality, rotate=rotate)


def send_jpeg_frame(data_handle, jpeg_bytes: bytes) -> int:
    chunks = [jpeg_bytes[offset : offset + CHUNK_SIZE] for offset in range(0, len(jpeg_bytes), CHUNK_SIZE)]
    if not chunks:
        raise ValueError("empty JPEG frame")

    for index, chunk in enumerate(chunks):
        packet_number = len(chunks) if index == 0 else index
        flags = (0x8000 if index == 0 else 0).to_bytes(2, "little")
        payload = bytes([DATA_COMMAND, packet_number & 0xFF]) + flags + chunk
        if len(payload) > DATA_REPORT_SIZE:
            raise ValueError("data payload is larger than HID data report")
        payload += bytes(DATA_REPORT_SIZE - len(payload))
        write_exact(data_handle, b"\x00" + payload)

    return len(chunks)


def open_device():
    interfaces = discover_from_sysfs()
    if not interfaces:
        raise RuntimeError("ASUS LCD 0b05:1c7b not found")
    return choose_interfaces(interfaces)


def run_default(args: argparse.Namespace) -> int:
    control, _data = open_device()
    with HidrawTransport(control.path) as control_handle:
        prepare_display(control_handle, control.report_size, args.brightness)
    print("display prepared")
    return 0


def run_red(args: argparse.Namespace) -> int:
    return send_frame(red_jpeg_bytes(args.quality), args)


def run_image(args: argparse.Namespace) -> int:
    return send_frame(file_jpeg_bytes(args.path, args.quality, args.rotate), args)


def send_frame(jpeg_bytes: bytes, args: argparse.Namespace) -> int:
    control, data = open_device()
    print(f"jpeg bytes={len(jpeg_bytes)} packets={(len(jpeg_bytes) + CHUNK_SIZE - 1) // CHUNK_SIZE}")
    print(f"control={control.path} data={data.path}")

    with HidrawTransport(control.path) as control_handle, HidrawTransport(data.path) as data_handle:
        prepare_display(control_handle, control.report_size, args.brightness)
        loops = max(1, args.repeat)
        for index in range(loops):
            packets = send_jpeg_frame(data_handle, jpeg_bytes)
            print(f"sent frame {index + 1}/{loops}: packets={packets}", flush=True)
            if index + 1 < loops and args.interval > 0:
                time.sleep(args.interval)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send official-like JPEG frames to ASUS USB9 LCD.")
    parser.add_argument("--brightness", type=int, default=100, help="brightness value from 0 to 255")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("default", help="prepare the display with official on/display commands")

    red = subparsers.add_parser("red", help="send a solid red JPEG frame")
    red.add_argument("--quality", type=int, default=95, help="JPEG quality")
    red.add_argument("--repeat", type=int, default=1, help="number of times to send the frame")
    red.add_argument("--interval", type=float, default=0.25, help="seconds between repeated frames")

    image = subparsers.add_parser("image", help="send an image as a 320x320 JPEG frame")
    image.add_argument("path", type=Path, help="image file path")
    image.add_argument("--quality", type=int, default=95, help="JPEG quality")
    image.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=180, help="rotate image before upload")
    image.add_argument("--repeat", type=int, default=1, help="number of times to send the frame")
    image.add_argument("--interval", type=float, default=0.25, help="seconds between repeated frames")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "default":
            return run_default(args)
        if args.command == "red":
            return run_red(args)
        if args.command == "image":
            return run_image(args)
    except Exception as error:
        print(f"failed: {error}")
        return 1
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
