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
