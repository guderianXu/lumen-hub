from __future__ import annotations

from collections.abc import Iterator

from PySide6.QtCore import QObject, Signal, Slot

from usb9_lcd.animation import AnimatedFrame
from usb9_lcd.drivers import DisplayDriver
from usb9_lcd.drivers.base import DisplayDevice


class AnimationUploadWorker(QObject):
    uploaded = Signal()
    finished = Signal()
    failed = Signal(str)

    def __init__(
        self,
        driver: DisplayDriver,
        device: DisplayDevice,
        frames: Iterator[AnimatedFrame],
    ) -> None:
        super().__init__()
        self._driver = driver
        self._device = device
        self._frames = frames

    @Slot()
    def run(self) -> None:
        try:
            animated_frame = next(self._frames)
            self._driver.upload_static_frame(self._device, animated_frame.frame)
        except StopIteration:
            self.finished.emit()
        except Exception as error:
            self.failed.emit(str(error))
            return

        self.uploaded.emit()
