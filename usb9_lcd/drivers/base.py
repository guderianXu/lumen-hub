from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Protocol


class Capability(str, Enum):
    STATIC_IMAGE = "static_image"
    ANIMATION = "animation"
    SENSOR_MONITOR = "sensor_monitor"


class PixelFormat(str, Enum):
    RGB565 = "rgb565"
    JPEG = "jpeg"


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


class DisplayUploadSession(Protocol):
    def __enter__(self) -> "DisplayUploadSession":
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        ...

    def upload_static_frame(self, frame: bytes) -> None:
        ...


class SessionDisplayDriver(DisplayDriver, Protocol):
    def open_upload_session(self, device: DisplayDevice) -> DisplayUploadSession:
        ...
