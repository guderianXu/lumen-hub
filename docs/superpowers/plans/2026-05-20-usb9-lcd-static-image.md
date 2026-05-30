# USB9 LCD Static Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI that detects the ASUS `0b05:1c7b` LCD, prepares a static image frame, and routes it through an isolated HID protocol layer.

**Architecture:** The project is a small Python package with focused modules for device discovery, image conversion, HID transport, packet encoding, and CLI orchestration. Hardware-specific protocol details stay in `usb9_lcd/protocol.py`; all higher-level code works with `upload_frame(frame_bytes)`.

**Tech Stack:** Python 3.10+, Pillow, pytest, stdlib argparse/pathlib/dataclasses.

---

## File Structure

- Create `pyproject.toml`: package metadata, dependencies, pytest config.
- Create `README.md`: setup, permission, and first commands.
- Create `usb9_lcd/__init__.py`: version export.
- Create `usb9_lcd/__main__.py`: `python -m usb9_lcd` entrypoint.
- Create `usb9_lcd/cli.py`: argparse commands and user-facing errors.
- Create `usb9_lcd/device.py`: hidraw/sysfs discovery for USB ID `0b05:1c7b`.
- Create `usb9_lcd/image.py`: image load, crop, resize, RGB/RGB565 conversion.
- Create `usb9_lcd/protocol.py`: report sizing, chunking, and frame upload API.
- Create `usb9_lcd/transport.py`: small hidraw file writer abstraction.
- Create `usb9_lcd/errors.py`: shared exception types.
- Create `tests/test_device.py`: device discovery tests with fake sysfs.
- Create `tests/test_image.py`: image conversion tests.
- Create `tests/test_protocol.py`: packet chunking/upload tests.
- Create `tests/test_cli.py`: CLI command tests.

## Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `usb9_lcd/__init__.py`
- Create: `usb9_lcd/__main__.py`
- Create: `usb9_lcd/errors.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write the failing CLI import test**

Create `tests/test_cli.py`:

```python
from usb9_lcd.cli import build_parser


def test_parser_accepts_detect_command():
    parser = build_parser()
    args = parser.parse_args(["detect"])

    assert args.command == "detect"


def test_parser_accepts_show_command():
    parser = build_parser()
    args = parser.parse_args(["show", "sample.png"])

    assert args.command == "show"
    assert args.image == "sample.png"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'usb9_lcd'`.

- [ ] **Step 3: Create package scaffold**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "usb9-lcd"
version = "0.1.0"
description = "Control an ASUS TUF GAMING LC III ARGB LCD over motherboard USB 9-pin HID"
requires-python = ">=3.10"
dependencies = [
  "Pillow>=10.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

Create `usb9_lcd/__init__.py`:

```python
__version__ = "0.1.0"
```

Create `usb9_lcd/errors.py`:

```python
class Usb9LcdError(Exception):
    """Base exception for user-facing usb9-lcd failures."""
```

Create `usb9_lcd/cli.py`:

```python
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="usb9-lcd")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("detect", help="Find the ASUS USB9 LCD HID interfaces")

    show = subparsers.add_parser("show", help="Display a static image on the LCD")
    show.add_argument("image", help="Path to a PNG or JPG image")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    return 0
```

Create `usb9_lcd/__main__.py`:

```python
from __future__ import annotations

import sys

from .cli import main


raise SystemExit(main(sys.argv[1:]))
```

Create `README.md`:

```markdown
# usb9-lcd

Python tools for controlling the ASUS TUF GAMING LC III 360 ARGB LCD connected through an internal motherboard USB 9-pin header.

Detected target:

- USB ID: `0b05:1c7b`
- Product: `ASUSTek Computer, Inc. TUF GAMING LC III 360 ARGB LCD`

First commands:

```bash
python -m usb9_lcd detect
python -m usb9_lcd show ./image.png
```
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`

Expected: PASS for both parser tests.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml README.md usb9_lcd tests/test_cli.py
git commit -m "chore: scaffold usb9 lcd package"
```

## Task 2: Device Discovery

**Files:**
- Create: `usb9_lcd/device.py`
- Modify: `usb9_lcd/cli.py`
- Test: `tests/test_device.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing device tests**

Create `tests/test_device.py`:

```python
from pathlib import Path

from usb9_lcd.device import HidInterface, discover_from_sysfs


def make_hidraw(root: Path, name: str, hid_id: str, report: bytes, mode: int = 0o660) -> Path:
    hidraw = root / "class" / "hidraw" / name
    device = hidraw / "device"
    device.mkdir(parents=True)
    (device / "uevent").write_text(
        f"HID_ID=0003:00000B05:00001C7B\nHID_NAME=ASUS TUF GAMING LC III 360 ARGB LCD\nHID_UNIQ=A247392SS000000\n",
        encoding="utf-8",
    )
    (device / "report_descriptor").write_bytes(report)
    dev = root / "dev" / name
    dev.parent.mkdir(parents=True, exist_ok=True)
    dev.write_bytes(b"")
    dev.chmod(mode)
    return dev


def test_discover_from_sysfs_matches_asus_lcd(tmp_path):
    make_hidraw(tmp_path, "hidraw10", "0003:00000B05:00001C7B", bytes.fromhex("0606ff0901a101150026ff00750896b8010901810296b80109019102c0"))
    make_hidraw(tmp_path, "hidraw11", "0003:00000B05:00001C7B", bytes.fromhex("0606ff0901a101150026ff0075089610000901810296000409019102c0"))

    interfaces = discover_from_sysfs(sys_root=tmp_path, dev_root=tmp_path / "dev")

    assert interfaces == [
        HidInterface(path=tmp_path / "dev" / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
        HidInterface(path=tmp_path / "dev" / "hidraw11", name="hidraw11", report_size=1024, can_read=True, can_write=True),
    ]


def test_discover_from_sysfs_ignores_other_asus_devices(tmp_path):
    hidraw = tmp_path / "class" / "hidraw" / "hidraw9" / "device"
    hidraw.mkdir(parents=True)
    (hidraw / "uevent").write_text("HID_ID=0003:00000B05:000019AF\n", encoding="utf-8")
    (hidraw / "report_descriptor").write_bytes(b"")

    assert discover_from_sysfs(sys_root=tmp_path, dev_root=tmp_path / "dev") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_device.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'usb9_lcd.device'`.

- [ ] **Step 3: Implement sysfs discovery**

Create `usb9_lcd/device.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

USB_VENDOR = "0B05"
USB_PRODUCT = "1C7B"


@dataclass(frozen=True)
class HidInterface:
    path: Path
    name: str
    report_size: int
    can_read: bool
    can_write: bool


def discover_from_sysfs(sys_root: Path = Path("/sys"), dev_root: Path = Path("/dev")) -> list[HidInterface]:
    hidraw_root = sys_root / "class" / "hidraw"
    if not hidraw_root.exists():
        return []

    interfaces: list[HidInterface] = []
    for hidraw in sorted(hidraw_root.iterdir(), key=lambda p: p.name):
        device = hidraw / "device"
        uevent = _read_text(device / "uevent")
        if not _is_target_lcd(uevent):
            continue

        dev_path = dev_root / hidraw.name
        interfaces.append(
            HidInterface(
                path=dev_path,
                name=hidraw.name,
                report_size=_report_size(device / "report_descriptor"),
                can_read=_can_access(dev_path, 4),
                can_write=_can_access(dev_path, 2),
            )
        )
    return interfaces


def choose_interfaces(interfaces: list[HidInterface]) -> tuple[HidInterface, HidInterface]:
    if len(interfaces) < 2:
        raise ValueError("expected two HID interfaces for ASUS LCD")

    ordered = sorted(interfaces, key=lambda item: item.report_size)
    control = ordered[0]
    data = ordered[-1]
    return control, data


def _is_target_lcd(uevent: str) -> bool:
    compact = uevent.upper().replace(":", "")
    return USB_VENDOR in compact and USB_PRODUCT in compact


def _report_size(report_descriptor: Path) -> int:
    data = report_descriptor.read_bytes()
    if bytes.fromhex("96b801") in data:
        return 440
    if bytes.fromhex("960004") in data:
        return 1024
    if bytes.fromhex("961000") in data:
        return 16
    return 0


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _can_access(path: Path, mode: int) -> bool:
    try:
        return path.exists() and path.stat().st_mode & mode != 0
    except OSError:
        return False
```

- [ ] **Step 4: Add CLI detect command**

Modify `usb9_lcd/cli.py`:

```python
from __future__ import annotations

import argparse

from .device import choose_interfaces, discover_from_sysfs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="usb9-lcd")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("detect", help="Find the ASUS USB9 LCD HID interfaces")

    show = subparsers.add_parser("show", help="Display a static image on the LCD")
    show.add_argument("image", help="Path to a PNG or JPG image")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "detect":
        interfaces = discover_from_sysfs()
        if not interfaces:
            print("ASUS LCD 0b05:1c7b not found")
            return 1
        control, data = choose_interfaces(interfaces)
        print(f"control: {control.path} report_size={control.report_size} read={control.can_read} write={control.can_write}")
        print(f"data: {data.path} report_size={data.report_size} read={data.can_read} write={data.can_write}")
        return 0

    return 0
```

- [ ] **Step 5: Run device and CLI tests**

Run: `pytest tests/test_device.py tests/test_cli.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add usb9_lcd/device.py usb9_lcd/cli.py tests/test_device.py tests/test_cli.py
git commit -m "feat: detect ASUS USB9 LCD hidraw interfaces"
```

## Task 3: Image Frame Conversion

**Files:**
- Create: `usb9_lcd/image.py`
- Test: `tests/test_image.py`

- [ ] **Step 1: Write failing image tests**

Create `tests/test_image.py`:

```python
from pathlib import Path

from PIL import Image

from usb9_lcd.image import FrameConfig, image_to_rgb, image_to_rgb565


def test_image_to_rgb_center_crops_to_square(tmp_path: Path):
    image_path = tmp_path / "wide.png"
    image = Image.new("RGB", (800, 400), "red")
    image.save(image_path)

    frame = image_to_rgb(image_path, FrameConfig(width=480, height=480))

    assert len(frame) == 480 * 480 * 3


def test_image_to_rgb565_encodes_known_color(tmp_path: Path):
    image_path = tmp_path / "red.png"
    image = Image.new("RGB", (1, 1), (255, 0, 0))
    image.save(image_path)

    frame = image_to_rgb565(image_path, FrameConfig(width=1, height=1))

    assert frame == bytes([0xF8, 0x00])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_image.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'usb9_lcd.image'`.

- [ ] **Step 3: Implement image conversion**

Create `usb9_lcd/image.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps


@dataclass(frozen=True)
class FrameConfig:
    width: int = 480
    height: int = 480
    fit: str = "cover"


def image_to_rgb(path: str | Path, config: FrameConfig = FrameConfig()) -> bytes:
    with Image.open(path) as source:
        image = source.convert("RGB")
        resized = _fit_image(image, config)
        return resized.tobytes()


def image_to_rgb565(path: str | Path, config: FrameConfig = FrameConfig()) -> bytes:
    rgb = image_to_rgb(path, config)
    output = bytearray()
    for index in range(0, len(rgb), 3):
        red, green, blue = rgb[index], rgb[index + 1], rgb[index + 2]
        value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        output.extend(value.to_bytes(2, "big"))
    return bytes(output)


def _fit_image(image: Image.Image, config: FrameConfig) -> Image.Image:
    size = (config.width, config.height)
    if config.fit == "contain":
        contained = ImageOps.contain(image, size)
        canvas = Image.new("RGB", size, "black")
        left = (config.width - contained.width) // 2
        top = (config.height - contained.height) // 2
        canvas.paste(contained, (left, top))
        return canvas
    if config.fit == "cover":
        return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    raise ValueError(f"unsupported fit mode: {config.fit}")
```

- [ ] **Step 4: Run image tests**

Run: `pytest tests/test_image.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add usb9_lcd/image.py tests/test_image.py
git commit -m "feat: convert images to LCD frames"
```

## Task 4: HID Transport and Protocol Packet Boundaries

**Files:**
- Create: `usb9_lcd/transport.py`
- Create: `usb9_lcd/protocol.py`
- Test: `tests/test_protocol.py`

- [ ] **Step 1: Write failing protocol tests**

Create `tests/test_protocol.py`:

```python
from usb9_lcd.protocol import LcdProtocol, chunk_frame


class FakeTransport:
    def __init__(self):
        self.writes: list[bytes] = []

    def write(self, payload: bytes) -> int:
        self.writes.append(payload)
        return len(payload)


def test_chunk_frame_uses_payload_size_after_header():
    chunks = list(chunk_frame(bytes(range(10)), report_size=8, header_size=4))

    assert chunks == [bytes(range(4)), bytes(range(4, 8)), bytes(range(8, 10))]


def test_upload_frame_writes_start_data_and_finish_reports():
    control = FakeTransport()
    data = FakeTransport()
    protocol = LcdProtocol(control=control, data=data, data_report_size=16)

    protocol.upload_frame(b"abcdef")

    assert control.writes[0] == b"US9L" + bytes([1, 0, 6, 0])
    assert data.writes == [b"US9D" + bytes([0, 0]) + b"abcdef" + bytes(4)]
    assert control.writes[-1] == b"US9L" + bytes([2, 0, 6, 0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_protocol.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'usb9_lcd.protocol'`.

- [ ] **Step 3: Implement transport and protocol shell**

Create `usb9_lcd/transport.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import BinaryIO


class HidrawTransport:
    def __init__(self, path: Path):
        self.path = path
        self._handle: BinaryIO | None = None

    def __enter__(self) -> "HidrawTransport":
        self._handle = self.path.open("r+b", buffering=0)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def write(self, payload: bytes) -> int:
        if self._handle is None:
            raise RuntimeError("transport is not open")
        return self._handle.write(payload)
```

Create `usb9_lcd/protocol.py`:

```python
from __future__ import annotations

from typing import Iterable, Protocol


class WritableTransport(Protocol):
    def write(self, payload: bytes) -> int:
        ...


def chunk_frame(frame: bytes, report_size: int, header_size: int) -> Iterable[bytes]:
    payload_size = report_size - header_size
    if payload_size <= 0:
        raise ValueError("report_size must be larger than header_size")
    for offset in range(0, len(frame), payload_size):
        yield frame[offset : offset + payload_size]


class LcdProtocol:
    def __init__(self, control: WritableTransport, data: WritableTransport, data_report_size: int = 1024):
        self.control = control
        self.data = data
        self.data_report_size = data_report_size

    def upload_frame(self, frame: bytes) -> None:
        self._write_exact(self.control, _control_packet(command=1, frame_size=len(frame)))
        for sequence, chunk in enumerate(chunk_frame(frame, self.data_report_size, header_size=6)):
            self._write_exact(self.data, _data_packet(sequence=sequence, chunk=chunk, report_size=self.data_report_size))
        self._write_exact(self.control, _control_packet(command=2, frame_size=len(frame)))

    def _write_exact(self, transport: WritableTransport, payload: bytes) -> None:
        written = transport.write(payload)
        if written != len(payload):
            raise OSError(f"short HID write: wrote {written} of {len(payload)} bytes")


def _control_packet(command: int, frame_size: int) -> bytes:
    return b"US9L" + bytes([command, 0]) + frame_size.to_bytes(2, "little")


def _data_packet(sequence: int, chunk: bytes, report_size: int) -> bytes:
    header = b"US9D" + sequence.to_bytes(2, "little")
    payload = header + chunk
    return payload.ljust(report_size, b"\x00")
```

- [ ] **Step 4: Run protocol tests**

Run: `pytest tests/test_protocol.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add usb9_lcd/transport.py usb9_lcd/protocol.py tests/test_protocol.py
git commit -m "feat: add HID protocol upload shell"
```

## Task 5: Wire Static Image CLI

**Files:**
- Modify: `usb9_lcd/cli.py`
- Modify: `README.md`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Extend CLI tests**

Replace `tests/test_cli.py` with:

```python
from pathlib import Path

from PIL import Image

from usb9_lcd.cli import build_parser, main


def test_parser_accepts_detect_command():
    parser = build_parser()
    args = parser.parse_args(["detect"])

    assert args.command == "detect"


def test_parser_accepts_show_command():
    parser = build_parser()
    args = parser.parse_args(["show", "sample.png", "--dry-run"])

    assert args.command == "show"
    assert args.image == "sample.png"
    assert args.dry_run is True


def test_show_dry_run_converts_image(tmp_path: Path, capsys):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (20, 20), "blue").save(image_path)

    exit_code = main(["show", str(image_path), "--dry-run", "--width", "10", "--height", "10"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "prepared frame: 200 bytes" in captured.out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`

Expected: FAIL because `--dry-run`, `--width`, and `--height` are not implemented.

- [ ] **Step 3: Implement show command wiring**

Modify `usb9_lcd/cli.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path

from .device import choose_interfaces, discover_from_sysfs
from .image import FrameConfig, image_to_rgb565
from .protocol import LcdProtocol
from .transport import HidrawTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="usb9-lcd")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("detect", help="Find the ASUS USB9 LCD HID interfaces")

    show = subparsers.add_parser("show", help="Display a static image on the LCD")
    show.add_argument("image", help="Path to a PNG or JPG image")
    show.add_argument("--width", type=int, default=480, help="LCD frame width")
    show.add_argument("--height", type=int, default=480, help="LCD frame height")
    show.add_argument("--fit", choices=["cover", "contain"], default="cover", help="Image fit mode")
    show.add_argument("--dry-run", action="store_true", help="Prepare the frame without writing to the LCD")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "detect":
        return _detect()
    if args.command == "show":
        return _show(args)
    return 0


def _detect() -> int:
    interfaces = discover_from_sysfs()
    if not interfaces:
        print("ASUS LCD 0b05:1c7b not found")
        return 1
    control, data = choose_interfaces(interfaces)
    print(f"control: {control.path} report_size={control.report_size} read={control.can_read} write={control.can_write}")
    print(f"data: {data.path} report_size={data.report_size} read={data.can_read} write={data.can_write}")
    return 0


def _show(args: argparse.Namespace) -> int:
    config = FrameConfig(width=args.width, height=args.height, fit=args.fit)
    frame = image_to_rgb565(Path(args.image), config)
    if args.dry_run:
        print(f"prepared frame: {len(frame)} bytes")
        return 0

    interfaces = discover_from_sysfs()
    if not interfaces:
        print("ASUS LCD 0b05:1c7b not found")
        return 1
    control, data = choose_interfaces(interfaces)
    if not control.can_write or not data.can_write:
        print("LCD hidraw nodes are not writable. Add a udev rule or run with suitable permissions.")
        print('Example rule: SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0b05", ATTRS{idProduct}=="1c7b", MODE="0660", GROUP="plugdev"')
        return 1

    with HidrawTransport(control.path) as control_transport, HidrawTransport(data.path) as data_transport:
        protocol = LcdProtocol(control=control_transport, data=data_transport, data_report_size=data.report_size)
        protocol.upload_frame(frame)
    print(f"uploaded frame: {len(frame)} bytes")
    return 0
```

- [ ] **Step 4: Update README**

Append to `README.md`:

```markdown
## Static Image

Prepare a frame without touching the hardware:

```bash
python -m usb9_lcd show ./image.png --dry-run
```

Send a frame to the LCD:

```bash
python -m usb9_lcd show ./image.png
```

If Linux denies access to `/dev/hidraw*`, add a udev rule similar to:

```udev
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0b05", ATTRS{idProduct}=="1c7b", MODE="0660", GROUP="plugdev"
```
```

- [ ] **Step 5: Run full test suite**

Run: `pytest -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add usb9_lcd/cli.py README.md tests/test_cli.py
git commit -m "feat: wire static image CLI"
```

## Task 6: Local Hardware Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Install package in editable mode**

Run: `python -m pip install -e ".[dev]"`

Expected: installation succeeds and installs Pillow and pytest.

- [ ] **Step 2: Run automated tests**

Run: `pytest -v`

Expected: PASS.

- [ ] **Step 3: Detect the LCD**

Run: `python -m usb9_lcd detect`

Expected output includes:

```text
control: /dev/hidraw10 report_size=440
data: /dev/hidraw11 report_size=1024
```

The hidraw numbers may differ after reboot; the command must still find two interfaces for USB ID `0b05:1c7b`.

- [ ] **Step 4: Create a sample image**

Run:

```bash
python - <<'PY'
from PIL import Image, ImageDraw

image = Image.new("RGB", (480, 480), "black")
draw = ImageDraw.Draw(image)
draw.rectangle((40, 40, 440, 440), outline="white", width=8)
draw.text((150, 225), "USB9 LCD", fill="white")
image.save("sample.png")
PY
```

Expected: `sample.png` exists in the project root.

- [ ] **Step 5: Dry-run image conversion**

Run: `python -m usb9_lcd show ./sample.png --dry-run`

Expected output:

```text
prepared frame: 460800 bytes
```

- [ ] **Step 6: Attempt hardware upload**

Run: `python -m usb9_lcd show ./sample.png`

Expected: Either the LCD changes, or the command prints a specific permission/protocol failure. If the LCD does not change but the write succeeds, capture the exact command output and continue protocol reverse engineering in a new plan focused only on ASUS packet framing.

- [ ] **Step 7: Document verification result**

Append the observed hardware result to `README.md` under a `## Hardware Notes` heading:

```markdown
## Hardware Notes

- `python -m usb9_lcd detect` found the ASUS LCD at the current hidraw nodes.
- `python -m usb9_lcd show ./sample.png --dry-run` prepared a 460800-byte RGB565 frame.
- Hardware upload changed the LCD.

If the LCD did not change, use this line instead:

- Hardware upload did not change the LCD; protocol framing requires a follow-up plan.
```

- [ ] **Step 8: Commit**

```bash
git add README.md sample.png
git commit -m "docs: record local LCD verification"
```

## Self-Review

- Spec coverage: covered device discovery, image conversion, CLI commands, protocol isolation, user-facing permission errors, tests, and future usability boundaries.
- Red-flag scan: no task contains unfinished markers or unspecified implementation steps.
- Type consistency: `FrameConfig`, `HidInterface`, `LcdProtocol.upload_frame`, `HidrawTransport.write`, and CLI argument names are consistent across tasks.
