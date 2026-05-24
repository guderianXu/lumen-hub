from __future__ import annotations

import argparse
import os
import signal
import time
from pathlib import Path

from usb9_lcd.device import choose_interfaces, discover_from_sysfs
from usb9_lcd.protocol import LcdProtocol
from usb9_lcd.transport import HidrawTransport


DEFAULT_PID_FILE = Path(".cache/usb9-lcd/keepalive.pid")


def stop_existing_keepalive(pid_file: Path = DEFAULT_PID_FILE) -> None:
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return

    if pid == os.getpid():
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        return

    try:
        pid_file.unlink()
    except OSError:
        pass


def upload_frame_once(frame: bytes) -> None:
    control, data = choose_interfaces(discover_from_sysfs())
    with HidrawTransport(control.path) as control_transport, HidrawTransport(data.path) as data_transport:
        LcdProtocol(control=control_transport, data=data_transport, data_report_size=data.report_size).upload_frame(frame)


def run_keepalive(frame_path: Path, *, interval: float, pid_file: Path = DEFAULT_PID_FILE) -> int:
    if interval <= 0:
        raise ValueError("interval must be positive")

    stop_existing_keepalive(pid_file)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")

    frame = frame_path.read_bytes()
    if not frame:
        raise ValueError("frame is empty")

    try:
        while True:
            upload_frame_once(frame)
            time.sleep(interval)
    finally:
        try:
            if pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="usb9-lcd-keepalive")
    parser.add_argument("frame", type=Path, help="Raw prepared frame bytes to repeat")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between repeated uploads")
    parser.add_argument("--pid-file", type=Path, default=DEFAULT_PID_FILE)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run_keepalive(args.frame, interval=args.interval, pid_file=args.pid_file)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"keepalive failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
