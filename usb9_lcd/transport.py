from pathlib import Path
from types import TracebackType
from typing import BinaryIO


class HidrawTransport:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._handle: BinaryIO | None = None

    def __enter__(self) -> "HidrawTransport":
        self._handle = self.path.open("r+b", buffering=0)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def write(self, payload: bytes) -> int:
        if self._handle is None:
            raise RuntimeError("transport is not open")
        return self._handle.write(payload)


class HidApiTransport:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._device = None

    def __enter__(self) -> "HidApiTransport":
        try:
            import hid  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError("hidapi is required for Windows ASUS LCD access; install the hidapi package") from error

        device = hid.device()
        device.open_path(self.path.encode("utf-8"))
        self._device = device
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._device is not None:
            self._device.close()
            self._device = None

    def write(self, payload: bytes) -> int:
        if self._device is None:
            raise RuntimeError("transport is not open")
        return int(self._device.write(payload))
