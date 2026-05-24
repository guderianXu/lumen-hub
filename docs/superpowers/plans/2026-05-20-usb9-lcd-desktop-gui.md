# USB9 LCD Desktop GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a PySide6 desktop GUI that controls the current ASUS LCD through a device-driver abstraction and previews static image output with a device-adaptive preview model.

**Architecture:** Add a `drivers` layer that exposes supported displays through a stable profile and upload API. Keep the existing CLI working by letting both CLI and GUI reuse the same image conversion and ASUS protocol implementation. Build the GUI as a thin PySide6 layer over testable non-visual services.

**Tech Stack:** Python 3.10+, PySide6, Pillow, pytest.

---

## File Structure

- Modify `pyproject.toml`: add `PySide6` runtime dependency.
- Create `usb9_lcd/drivers/__init__.py`: public driver exports.
- Create `usb9_lcd/drivers/base.py`: driver dataclasses, protocols, preview profile, capabilities.
- Create `usb9_lcd/drivers/asus_lc_iii.py`: ASUS driver wrapping existing sysfs discovery, transport, protocol, and RGB565 frame config.
- Create `usb9_lcd/render.py`: GUI-facing static-image render helpers and preview metadata.
- Create `usb9_lcd/gui/__init__.py`: GUI package marker.
- Create `usb9_lcd/gui/app.py`: PySide6 application entrypoint.
- Create `usb9_lcd/gui/main_window.py`: main window, left nav, image page, device page.
- Create `usb9_lcd/gui/preview.py`: adaptive preview widget and preview geometry helpers.
- Create `tests/test_drivers.py`: driver model and ASUS driver behavior with mocked hardware.
- Create `tests/test_render.py`: render settings to `FrameConfig` and frame bytes.
- Create `tests/test_preview.py`: preview geometry for square, rectangle, circle, matrix.
- Create `tests/test_gui_import.py`: GUI modules import when PySide6 is installed.
- Modify `README.md`: document GUI launch and first-release scope.

## Task 1: Add Driver Data Model

**Files:**
- Create: `usb9_lcd/drivers/__init__.py`
- Create: `usb9_lcd/drivers/base.py`
- Test: `tests/test_drivers.py`

- [ ] **Step 1: Write the failing driver model tests**

Create `tests/test_drivers.py`:

```python
from pathlib import Path

from usb9_lcd.drivers.base import (
    Capability,
    DeviceConnection,
    DisplayDevice,
    PixelFormat,
    PixelStyle,
    PreviewProfile,
    PreviewShape,
)


def test_preview_profile_describes_square_asus_screen_without_circle_assumption():
    profile = PreviewProfile(
        width=480,
        height=480,
        shape=PreviewShape.SQUARE,
        pixel_style=PixelStyle.CONTINUOUS,
        orientation=0,
        label="ASUS LC III",
    )

    assert profile.width == 480
    assert profile.height == 480
    assert profile.shape is PreviewShape.SQUARE
    assert profile.pixel_style is PixelStyle.CONTINUOUS
    assert profile.label == "ASUS LC III"


def test_display_device_exposes_driver_metadata_and_capabilities():
    profile = PreviewProfile(
        width=64,
        height=32,
        shape=PreviewShape.RECTANGLE,
        pixel_style=PixelStyle.MATRIX,
    )
    connection = DeviceConnection(
        driver_id="test.driver",
        display_name="Test Matrix",
        paths=(Path("/dev/hidraw-test"),),
        writable=True,
        readable=True,
        details="test device",
    )
    device = DisplayDevice(
        connection=connection,
        width=64,
        height=32,
        pixel_format=PixelFormat.RGB565,
        preview=profile,
        capabilities=frozenset({Capability.STATIC_IMAGE}),
    )

    assert device.driver_id == "test.driver"
    assert device.display_name == "Test Matrix"
    assert device.supports(Capability.STATIC_IMAGE) is True
    assert device.supports(Capability.ANIMATION) is False
```

- [ ] **Step 2: Run driver tests to verify they fail**

Run:

```bash
pytest tests/test_drivers.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'usb9_lcd.drivers'`.

- [ ] **Step 3: Implement the driver model**

Create `usb9_lcd/drivers/base.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class Capability(str, Enum):
    STATIC_IMAGE = "static_image"
    ANIMATION = "animation"
    SENSOR_MONITOR = "sensor_monitor"


class PixelFormat(str, Enum):
    RGB565 = "rgb565"


class PreviewShape(str, Enum):
    SQUARE = "square"
    RECTANGLE = "rectangle"
    CIRCLE = "circle"
    MATRIX = "matrix"


class PixelStyle(str, Enum):
    CONTINUOUS = "continuous"
    MATRIX = "matrix"


@dataclass(frozen=True)
class PreviewProfile:
    width: int
    height: int
    shape: PreviewShape
    pixel_style: PixelStyle
    orientation: int = 0
    label: str = ""

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("preview width and height must be positive")
        if self.orientation not in (0, 90, 180, 270):
            raise ValueError("orientation must be one of 0, 90, 180, 270")


@dataclass(frozen=True)
class DeviceConnection:
    driver_id: str
    display_name: str
    paths: tuple[Path, ...]
    writable: bool
    readable: bool
    details: str = ""


@dataclass(frozen=True)
class DisplayDevice:
    connection: DeviceConnection
    width: int
    height: int
    pixel_format: PixelFormat
    preview: PreviewProfile
    capabilities: frozenset[Capability]

    @property
    def driver_id(self) -> str:
        return self.connection.driver_id

    @property
    def display_name(self) -> str:
        return self.connection.display_name

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities


class DisplayDriver(Protocol):
    driver_id: str
    display_name: str

    def discover(self) -> list[DisplayDevice]:
        ...

    def upload_static_frame(self, device: DisplayDevice, frame: bytes) -> None:
        ...
```

Create `usb9_lcd/drivers/__init__.py`:

```python
from .base import (
    Capability,
    DeviceConnection,
    DisplayDevice,
    DisplayDriver,
    PixelFormat,
    PixelStyle,
    PreviewProfile,
    PreviewShape,
)

__all__ = [
    "Capability",
    "DeviceConnection",
    "DisplayDevice",
    "DisplayDriver",
    "PixelFormat",
    "PixelStyle",
    "PreviewProfile",
    "PreviewShape",
]
```

- [ ] **Step 4: Run driver model tests**

Run:

```bash
pytest tests/test_drivers.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add usb9_lcd/drivers tests/test_drivers.py
git commit -m "feat: add display driver model"
```

## Task 2: Add ASUS LC III Driver

**Files:**
- Create: `usb9_lcd/drivers/asus_lc_iii.py`
- Modify: `usb9_lcd/drivers/__init__.py`
- Test: `tests/test_drivers.py`

- [ ] **Step 1: Add failing ASUS driver tests**

Append to `tests/test_drivers.py`:

```python
from usb9_lcd.device import HidInterface
from usb9_lcd.drivers.asus_lc_iii import AsusLcIiiDriver


def test_asus_driver_discovers_square_static_image_device(monkeypatch, tmp_path):
    interfaces = [
        HidInterface(path=tmp_path / "hidraw0", name="hidraw0", report_size=440, can_read=True, can_write=True),
        HidInterface(path=tmp_path / "hidraw1", name="hidraw1", report_size=1024, can_read=False, can_write=True),
    ]
    monkeypatch.setattr("usb9_lcd.drivers.asus_lc_iii.discover_from_sysfs", lambda: interfaces)

    devices = AsusLcIiiDriver().discover()

    assert len(devices) == 1
    device = devices[0]
    assert device.driver_id == "asus.lc_iii"
    assert device.display_name == "ASUS TUF Gaming LC III LCD"
    assert device.width == 480
    assert device.height == 480
    assert device.pixel_format is PixelFormat.RGB565
    assert device.preview.shape is PreviewShape.SQUARE
    assert device.preview.pixel_style is PixelStyle.CONTINUOUS
    assert device.capabilities == frozenset({Capability.STATIC_IMAGE})
    assert device.connection.paths == (tmp_path / "hidraw0", tmp_path / "hidraw1")
    assert device.connection.writable is True


def test_asus_driver_returns_no_devices_when_interfaces_are_incomplete(monkeypatch):
    monkeypatch.setattr("usb9_lcd.drivers.asus_lc_iii.discover_from_sysfs", lambda: [])

    assert AsusLcIiiDriver().discover() == []
```

- [ ] **Step 2: Run ASUS driver tests to verify they fail**

Run:

```bash
pytest tests/test_drivers.py::test_asus_driver_discovers_square_static_image_device -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'usb9_lcd.drivers.asus_lc_iii'`.

- [ ] **Step 3: Implement ASUS discovery**

Create `usb9_lcd/drivers/asus_lc_iii.py`:

```python
from __future__ import annotations

from .base import Capability, DeviceConnection, DisplayDevice, PixelFormat, PixelStyle, PreviewProfile, PreviewShape
from ..device import choose_interfaces, discover_from_sysfs
from ..protocol import LcdProtocol
from ..transport import HidrawTransport


class AsusLcIiiDriver:
    driver_id = "asus.lc_iii"
    display_name = "ASUS TUF Gaming LC III LCD"
    width = 480
    height = 480

    def discover(self) -> list[DisplayDevice]:
        interfaces = discover_from_sysfs()
        try:
            control, data = choose_interfaces(interfaces)
        except ValueError:
            return []

        return [
            DisplayDevice(
                connection=DeviceConnection(
                    driver_id=self.driver_id,
                    display_name=self.display_name,
                    paths=(control.path, data.path),
                    writable=control.can_write and data.can_write,
                    readable=control.can_read and data.can_read,
                    details=f"control={control.path} data={data.path}",
                ),
                width=self.width,
                height=self.height,
                pixel_format=PixelFormat.RGB565,
                preview=PreviewProfile(
                    width=self.width,
                    height=self.height,
                    shape=PreviewShape.SQUARE,
                    pixel_style=PixelStyle.CONTINUOUS,
                    orientation=0,
                    label=self.display_name,
                ),
                capabilities=frozenset({Capability.STATIC_IMAGE}),
            )
        ]

    def upload_static_frame(self, device: DisplayDevice, frame: bytes) -> None:
        control_path, data_path = device.connection.paths
        with HidrawTransport(control_path) as control_transport, HidrawTransport(data_path) as data_transport:
            protocol = LcdProtocol(control=control_transport, data=data_transport)
            protocol.upload_frame(frame)
```

Modify `usb9_lcd/drivers/__init__.py` to export:

```python
from .asus_lc_iii import AsusLcIiiDriver
```

and add `"AsusLcIiiDriver"` to `__all__`.

- [ ] **Step 4: Run ASUS driver tests**

Run:

```bash
pytest tests/test_drivers.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add usb9_lcd/drivers tests/test_drivers.py
git commit -m "feat: add asus lcd driver"
```

## Task 3: Add Render Service For GUI And CLI Reuse

**Files:**
- Create: `usb9_lcd/render.py`
- Test: `tests/test_render.py`

- [ ] **Step 1: Write failing render tests**

Create `tests/test_render.py`:

```python
from pathlib import Path

import pytest
from PIL import Image

from usb9_lcd.drivers.base import Capability, DeviceConnection, DisplayDevice, PixelFormat, PixelStyle, PreviewProfile, PreviewShape
from usb9_lcd.render import ImageRenderSettings, render_static_image


def make_device(tmp_path: Path) -> DisplayDevice:
    return DisplayDevice(
        connection=DeviceConnection(
            driver_id="test.driver",
            display_name="Test Display",
            paths=(tmp_path / "hidraw-test",),
            writable=True,
            readable=True,
        ),
        width=2,
        height=1,
        pixel_format=PixelFormat.RGB565,
        preview=PreviewProfile(width=2, height=1, shape=PreviewShape.RECTANGLE, pixel_style=PixelStyle.CONTINUOUS),
        capabilities=frozenset({Capability.STATIC_IMAGE}),
    )


def test_render_static_image_uses_device_dimensions_and_settings(tmp_path: Path):
    image_path = tmp_path / "image.png"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(image_path)

    result = render_static_image(
        image_path,
        make_device(tmp_path),
        ImageRenderSettings(fit="stretch", rotate=0, background="#000000"),
    )

    assert result.frame == bytes([0xF8, 0x00, 0xF8, 0x00])
    assert result.width == 2
    assert result.height == 1
    assert result.byte_count == 4


def test_render_static_image_rejects_devices_without_static_image_support(tmp_path: Path):
    image_path = tmp_path / "image.png"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(image_path)
    device = make_device(tmp_path)
    unsupported = DisplayDevice(
        connection=device.connection,
        width=device.width,
        height=device.height,
        pixel_format=device.pixel_format,
        preview=device.preview,
        capabilities=frozenset(),
    )

    with pytest.raises(ValueError, match="selected device does not support static images"):
        render_static_image(image_path, unsupported, ImageRenderSettings())
```

- [ ] **Step 2: Run render tests to verify they fail**

Run:

```bash
pytest tests/test_render.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'usb9_lcd.render'`.

- [ ] **Step 3: Implement render service**

Create `usb9_lcd/render.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .drivers.base import Capability, DisplayDevice, PixelFormat
from .image import FitMode, FrameConfig, Rotation, image_to_rgb565


@dataclass(frozen=True)
class ImageRenderSettings:
    fit: FitMode = "cover"
    rotate: Rotation = 0
    background: str = "#000000"


@dataclass(frozen=True)
class RenderedFrame:
    frame: bytes
    width: int
    height: int
    byte_count: int


def render_static_image(path: str | Path, device: DisplayDevice, settings: ImageRenderSettings) -> RenderedFrame:
    if not device.supports(Capability.STATIC_IMAGE):
        raise ValueError("selected device does not support static images")
    if device.pixel_format is not PixelFormat.RGB565:
        raise ValueError(f"unsupported pixel format: {device.pixel_format.value}")

    config = FrameConfig(
        width=device.width,
        height=device.height,
        fit=settings.fit,
        rotate=settings.rotate,
        background=settings.background,
    )
    frame = image_to_rgb565(path, config)
    return RenderedFrame(frame=frame, width=device.width, height=device.height, byte_count=len(frame))
```

- [ ] **Step 4: Run render tests**

Run:

```bash
pytest tests/test_render.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add usb9_lcd/render.py tests/test_render.py
git commit -m "feat: add static image render service"
```

## Task 4: Add Adaptive Preview Geometry

**Files:**
- Create: `usb9_lcd/gui/__init__.py`
- Create: `usb9_lcd/gui/preview.py`
- Test: `tests/test_preview.py`

- [ ] **Step 1: Write failing preview tests**

Create `tests/test_preview.py`:

```python
from usb9_lcd.drivers.base import PixelStyle, PreviewProfile, PreviewShape
from usb9_lcd.gui.preview import preview_geometry


def test_preview_geometry_preserves_square_shape():
    profile = PreviewProfile(width=480, height=480, shape=PreviewShape.SQUARE, pixel_style=PixelStyle.CONTINUOUS)

    geometry = preview_geometry(profile, max_width=300, max_height=200)

    assert geometry.width == 200
    assert geometry.height == 200
    assert geometry.shape is PreviewShape.SQUARE
    assert geometry.show_grid is False


def test_preview_geometry_preserves_rectangle_aspect_ratio():
    profile = PreviewProfile(width=800, height=480, shape=PreviewShape.RECTANGLE, pixel_style=PixelStyle.CONTINUOUS)

    geometry = preview_geometry(profile, max_width=300, max_height=300)

    assert geometry.width == 300
    assert geometry.height == 180


def test_preview_geometry_marks_matrix_profiles_for_grid_display():
    profile = PreviewProfile(width=64, height=32, shape=PreviewShape.MATRIX, pixel_style=PixelStyle.MATRIX)

    geometry = preview_geometry(profile, max_width=300, max_height=300)

    assert geometry.width == 300
    assert geometry.height == 150
    assert geometry.show_grid is True
```

- [ ] **Step 2: Run preview tests to verify they fail**

Run:

```bash
pytest tests/test_preview.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'usb9_lcd.gui'`.

- [ ] **Step 3: Implement preview geometry**

Create `usb9_lcd/gui/__init__.py`:

```python
"""PySide6 desktop GUI for usb9-lcd."""
```

Create `usb9_lcd/gui/preview.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from usb9_lcd.drivers.base import PixelStyle, PreviewProfile, PreviewShape


@dataclass(frozen=True)
class PreviewGeometry:
    width: int
    height: int
    shape: PreviewShape
    show_grid: bool


def preview_geometry(profile: PreviewProfile, max_width: int, max_height: int) -> PreviewGeometry:
    if max_width <= 0 or max_height <= 0:
        raise ValueError("preview bounds must be positive")

    scale = min(max_width / profile.width, max_height / profile.height)
    width = max(1, round(profile.width * scale))
    height = max(1, round(profile.height * scale))
    return PreviewGeometry(
        width=width,
        height=height,
        shape=profile.shape,
        show_grid=profile.pixel_style is PixelStyle.MATRIX or profile.shape is PreviewShape.MATRIX,
    )
```

- [ ] **Step 4: Run preview tests**

Run:

```bash
pytest tests/test_preview.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add usb9_lcd/gui tests/test_preview.py
git commit -m "feat: add adaptive preview geometry"
```

## Task 5: Add PySide6 GUI Skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `usb9_lcd/gui/app.py`
- Create: `usb9_lcd/gui/main_window.py`
- Test: `tests/test_gui_import.py`

- [ ] **Step 1: Write failing GUI import test**

Create `tests/test_gui_import.py`:

```python
def test_gui_app_imports():
    from usb9_lcd.gui.app import main

    assert callable(main)
```

- [ ] **Step 2: Run GUI import test to verify it fails**

Run:

```bash
pytest tests/test_gui_import.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `usb9_lcd.gui.app` or `PySide6` if the dependency is not installed.

- [ ] **Step 3: Add PySide6 dependency**

Modify `pyproject.toml` dependencies:

```toml
dependencies = [
  "Pillow>=10.0",
  "PySide6>=6.7",
]
```

If PySide6 is not installed in the current environment, install the project in editable mode:

```bash
python -m pip install -e '.[dev]'
```

- [ ] **Step 4: Implement GUI skeleton**

Create `usb9_lcd/gui/main_window.py`:

```python
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QMainWindow, QSplitter, QStackedWidget, QVBoxLayout, QWidget

from usb9_lcd.drivers.asus_lc_iii import AsusLcIiiDriver


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("usb9-lcd")
        self.resize(980, 640)
        self.driver = AsusLcIiiDriver()
        self.devices = self.driver.discover()

        self.navigation = QListWidget()
        for label, enabled in (
            ("Image", True),
            ("GIF / Video", False),
            ("Monitor", False),
            ("Device", True),
        ):
            item = QListWidgetItem(label)
            if not enabled:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.navigation.addItem(item)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._image_page())
        self.pages.addWidget(self._placeholder_page("GIF / Video is planned for a later release."))
        self.pages.addWidget(self._placeholder_page("Monitor mode is planned for a later release."))
        self.pages.addWidget(self._device_page())
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0 if self.devices else 3)

        splitter = QSplitter()
        splitter.addWidget(self.navigation)
        splitter.addWidget(self.pages)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

    def _image_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Image")
        title.setObjectName("pageTitle")
        status = "Device ready" if self.devices else "No supported LCD detected"
        layout.addWidget(title)
        layout.addWidget(QLabel(status))
        layout.addWidget(QLabel("Image picker, adaptive preview, and send controls will be added next."))
        layout.addStretch()
        return page

    def _device_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Device"))
        if not self.devices:
            layout.addWidget(QLabel("No supported LCD detected."))
        for device in self.devices:
            layout.addWidget(QLabel(f"{device.display_name}: {device.width}x{device.height}, {device.preview.shape.value}"))
            layout.addWidget(QLabel(device.connection.details))
        layout.addStretch()
        return page

    def _placeholder_page(self, text: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(text))
        layout.addStretch()
        return page
```

Create `usb9_lcd/gui/app.py`:

```python
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    app = QApplication(sys.argv[:1] + (argv or []))
    window = MainWindow()
    window.show()
    return app.exec()
```

- [ ] **Step 5: Run GUI import test**

Run:

```bash
pytest tests/test_gui_import.py -q
```

Expected: PASS.

- [ ] **Step 6: Run all tests**

Run:

```bash
pytest -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml usb9_lcd/gui tests/test_gui_import.py
git commit -m "feat: add desktop gui skeleton"
```

## Task 6: Add Image Page Upload Controls

**Files:**
- Modify: `usb9_lcd/gui/main_window.py`
- Test: `tests/test_gui_import.py`

- [ ] **Step 1: Extend GUI smoke test for image page construction**

Replace `tests/test_gui_import.py` with:

```python
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_gui_app_imports():
    from usb9_lcd.gui.app import main

    assert callable(main)


def test_main_window_constructs_without_hardware(monkeypatch):
    from usb9_lcd.gui.main_window import MainWindow

    monkeypatch.setattr("usb9_lcd.gui.main_window.AsusLcIiiDriver.discover", lambda self: [])

    window = MainWindow()

    assert window.windowTitle() == "usb9-lcd"
```

- [ ] **Step 2: Run GUI tests to verify current behavior**

Run:

```bash
pytest tests/test_gui_import.py -q
```

Expected: PASS if Task 5 is complete. This is a safety baseline before replacing the page content.

- [ ] **Step 3: Implement first usable image controls**

Modify `usb9_lcd/gui/main_window.py`:

```python
from __future__ import annotations

from pathlib import Path

from PIL import UnidentifiedImageError
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from usb9_lcd.drivers.asus_lc_iii import AsusLcIiiDriver
from usb9_lcd.render import ImageRenderSettings, render_static_image


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("usb9-lcd")
        self.resize(980, 640)
        self.driver = AsusLcIiiDriver()
        self.devices = self.driver.discover()
        self.active_image: Path | None = None

        self.navigation = QListWidget()
        for label, enabled in (
            ("Image", True),
            ("GIF / Video", False),
            ("Monitor", False),
            ("Device", True),
        ):
            item = QListWidgetItem(label)
            if not enabled:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.navigation.addItem(item)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._image_page())
        self.pages.addWidget(self._placeholder_page("GIF / Video is planned for a later release."))
        self.pages.addWidget(self._placeholder_page("Monitor mode is planned for a later release."))
        self.pages.addWidget(self._device_page())
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0 if self.devices else 3)

        splitter = QSplitter()
        splitter.addWidget(self.navigation)
        splitter.addWidget(self.pages)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

    def _image_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.preview_label = QLabel("No image selected")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(320)

        self.path_label = QLabel("No file selected")
        choose_button = QPushButton("Choose Image")
        choose_button.clicked.connect(self.choose_image)

        self.fit_combo = QComboBox()
        self.fit_combo.addItems(["cover", "contain", "stretch"])
        self.rotate_combo = QComboBox()
        self.rotate_combo.addItems(["0", "90", "180", "270"])
        self.background_input = QLineEdit("#000000")

        form = QFormLayout()
        form.addRow("Fit", self.fit_combo)
        form.addRow("Rotate", self.rotate_combo)
        form.addRow("Background", self.background_input)

        send_button = QPushButton("Send To Screen")
        send_button.clicked.connect(self.send_image)
        send_button.setEnabled(bool(self.devices))

        self.status_label = QLabel("Device ready" if self.devices else "No supported LCD detected")
        layout.addWidget(self.preview_label)
        layout.addWidget(self.path_label)
        layout.addWidget(choose_button)
        layout.addLayout(form)
        layout.addWidget(send_button)
        layout.addWidget(self.status_label)
        layout.addStretch()
        return page

    def choose_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        self.active_image = Path(path)
        self.path_label.setText(str(self.active_image))
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self.preview_label.setPixmap(pixmap.scaled(self.preview_label.size(), Qt.AspectRatioMode.KeepAspectRatio))

    def send_image(self) -> None:
        if not self.devices:
            self.status_label.setText("No supported LCD detected")
            return
        if self.active_image is None:
            self.status_label.setText("Choose an image first")
            return

        settings = ImageRenderSettings(
            fit=self.fit_combo.currentText(),
            rotate=int(self.rotate_combo.currentText()),
            background=self.background_input.text(),
        )
        try:
            rendered = render_static_image(self.active_image, self.devices[0], settings)
            self.driver.upload_static_frame(self.devices[0], rendered.frame)
        except (OSError, ValueError, UnidentifiedImageError) as error:
            self.status_label.setText(f"Upload failed: {error}")
            return

        self.status_label.setText(f"Sent {rendered.byte_count} bytes")

    def _device_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Device"))
        if not self.devices:
            layout.addWidget(QLabel("No supported LCD detected."))
        for device in self.devices:
            layout.addWidget(QLabel(f"{device.display_name}: {device.width}x{device.height}, {device.preview.shape.value}"))
            layout.addWidget(QLabel(device.connection.details))
        layout.addStretch()
        return page

    def _placeholder_page(self, text: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(text))
        layout.addStretch()
        return page
```

- [ ] **Step 4: Run GUI tests**

Run:

```bash
pytest tests/test_gui_import.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add usb9_lcd/gui/main_window.py tests/test_gui_import.py
git commit -m "feat: add gui image controls"
```

## Task 7: Add GUI Launch Documentation And Manual Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README**

Add this section to `README.md` after Static Image:

````markdown
## Desktop GUI

Install GUI dependencies:

```bash
python -m pip install -e '.[dev]'
```

Launch the desktop GUI:

```bash
python -m usb9_lcd.gui.app
```

The first GUI release supports static image upload for the detected ASUS LCD. GIF/video and monitoring pages are visible as planned modes but disabled.
````

- [ ] **Step 2: Run all tests**

Run:

```bash
pytest -q
```

Expected: PASS.

- [ ] **Step 3: Run CLI hardware regression**

Run:

```bash
python -m usb9_lcd detect
timeout 20s python -m usb9_lcd show ./sample.png --dry-run --rotate 90 --fit contain --background '#101820'
```

Expected:

- `detect` prints control and data hidraw paths.
- dry-run prints `prepared frame: 460800 bytes`.

- [ ] **Step 4: Launch GUI manually**

Run:

```bash
python -m usb9_lcd.gui.app
```

Expected:

- Window opens.
- ASUS LC III appears on Device page when connected.
- Image page opens when a supported device is detected.
- Choosing an image shows a preview.
- Sending an image updates the LCD.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: add desktop gui usage"
```

## Plan Self-Review

- Spec coverage: driver abstraction is covered in Tasks 1-2; adaptive preview profile and geometry in Task 4; image page and device page in Tasks 5-6; PySide6 choice in Task 5; README and manual verification in Task 7.
- Scope check: GIF/video, sensor monitoring, tray mode, external plugins, installers, and remote control remain out of scope.
- Type consistency: `PreviewProfile`, `DisplayDevice`, `ImageRenderSettings`, and `AsusLcIiiDriver` are introduced before use in later tasks.
- No ASUS circular-screen assumption: ASUS profile uses `PreviewShape.SQUARE`; circular behavior exists only as a generic future profile shape.
