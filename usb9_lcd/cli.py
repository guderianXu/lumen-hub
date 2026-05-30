from __future__ import annotations

import argparse
from pathlib import Path

from PIL import UnidentifiedImageError

from .device import choose_interfaces, discover_from_sysfs
from .image import FrameConfig, image_to_jpeg
from .protocol import LcdProtocol
from .transport import HidrawTransport


PERMISSION_MESSAGE = (
    "LCD hidraw nodes are not writable. Add a udev rule, add your user to plugdev, "
    "then re-login and replug the device."
)
UDEV_RULE = 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0b05", ATTRS{idProduct}=="1c7b", MODE="0660", GROUP="plugdev"'


class DeviceSelectionError(ValueError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="usb9-lcd")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("detect", help="Find the ASUS USB9 LCD HID interfaces")

    show = subparsers.add_parser("show", help="Display a static image on the LCD")
    show.add_argument("image", help="Path to a PNG or JPG image")
    show.add_argument("--width", type=int, default=320, help="Frame width in pixels")
    show.add_argument("--height", type=int, default=320, help="Frame height in pixels")
    show.add_argument("--fit", choices=["cover", "contain", "stretch"], default="cover", help="Image fitting mode")
    show.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=180, help="Rotate the prepared frame")
    show.add_argument("--background", default="#000000", help="Contain-mode background color, for example #000000")
    show.add_argument("--dry-run", action="store_true", help="Prepare the frame without writing to hardware")

    control = subparsers.add_parser("control", help="Send a safe LCD control command")
    control_subparsers = control.add_subparsers(dest="control_action", required=True)
    control_subparsers.add_parser("default", help="Switch the LCD back to its default display mode")

    power = control_subparsers.add_parser("power", help="Turn LCD display power on or off")
    power.add_argument("state", choices=["on", "off"], help="Display power state")

    brightness = control_subparsers.add_parser("brightness", help="Set LCD brightness")
    brightness.add_argument("value", type=int, help="Brightness level from 0 to 255")

    screen_type = control_subparsers.add_parser("screen-type", help="Set safe ASUS screen type 1 or 2")
    screen_type.add_argument("value", type=int, help="Screen type, limited to 1 or 2")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "detect":
        return _detect()

    if args.command == "show":
        return _show(args)

    if args.command == "control":
        return _control(args)

    return 0


def _detect() -> int:
    interfaces = discover_from_sysfs()
    if not interfaces:
        print("ASUS LCD 0b05:1c7b not found")
        return 1

    try:
        control, data = choose_interfaces(interfaces)
    except ValueError as error:
        print(error)
        return 1

    print(
        f"control: path={control.path} report_size={control.report_size} "
        f"read={control.can_read} write={control.can_write}"
    )
    print(
        f"data: path={data.path} report_size={data.report_size} "
        f"read={data.can_read} write={data.can_write}"
    )
    return 0


def _show(args: argparse.Namespace) -> int:
    try:
        config = FrameConfig(width=args.width, height=args.height, fit=args.fit, rotate=args.rotate, background=args.background)
        frame = image_to_jpeg(Path(args.image), config)
    except (OSError, UnidentifiedImageError, ValueError) as error:
        print(f"failed to prepare image: {error}")
        return 1

    if args.dry_run:
        print(f"prepared frame: {len(frame)} bytes")
        return 0

    try:
        control, data = _get_writable_interfaces()
        with HidrawTransport(control.path) as control_transport, HidrawTransport(data.path) as data_transport:
            protocol = LcdProtocol(control=control_transport, data=data_transport, data_report_size=data.report_size)
            protocol.upload_frame(frame)
    except DeviceSelectionError as error:
        print(error)
        return 1
    except PermissionError as error:
        print(f"failed to upload frame: {error}")
        print(PERMISSION_MESSAGE)
        print(UDEV_RULE)
        return 1
    except (OSError, ValueError) as error:
        print(f"failed to upload frame: {error}")
        return 1

    print(f"frame transfer completed: {len(frame)} bytes")
    return 0


def _control(args: argparse.Namespace) -> int:
    try:
        command_label = _run_control_command(args)
    except DeviceSelectionError as error:
        print(f"failed to run control command: {error}")
        return 1
    except PermissionError as error:
        print(f"failed to run control command: {error}")
        print(PERMISSION_MESSAGE)
        print(UDEV_RULE)
        return 1
    except (OSError, ValueError) as error:
        print(f"failed to run control command: {error}")
        return 1

    print(f"control command completed: {command_label}")
    return 0


def _run_control_command(args: argparse.Namespace) -> str:
    if args.control_action == "brightness":
        if not 0 <= args.value <= 255:
            raise ValueError("brightness must be between 0 and 255")
    elif args.control_action == "screen-type":
        if args.value not in (1, 2):
            raise ValueError("screen-type must be 1 or 2")

    control, data = _get_writable_interfaces()
    with HidrawTransport(control.path) as control_transport, HidrawTransport(data.path) as data_transport:
        protocol = LcdProtocol(control=control_transport, data=data_transport, data_report_size=data.report_size)

        if args.control_action == "default":
            protocol.set_display_power(True)
            protocol.set_display_brightness(100)
            protocol.set_screen_type(1)
            return "default"

        if args.control_action == "power":
            enabled = args.state == "on"
            protocol.set_display_power(enabled)
            return f"power {args.state}"

        if args.control_action == "brightness":
            protocol.set_display_brightness(args.value)
            return f"brightness {args.value}"

        if args.control_action == "screen-type":
            protocol.set_screen_type(args.value)
            return f"screen-type {args.value}"

    raise ValueError(f"unsupported control action: {args.control_action}")


def _get_writable_interfaces():
    interfaces = discover_from_sysfs()
    if not interfaces:
        raise DeviceSelectionError("ASUS LCD 0b05:1c7b not found")

    try:
        control, data = choose_interfaces(interfaces)
    except ValueError as error:
        raise DeviceSelectionError(str(error)) from error
    if not control.can_write or not data.can_write:
        raise PermissionError("LCD hidraw nodes are not writable")

    return control, data
