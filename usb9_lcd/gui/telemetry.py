from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal, Slot

from usb9_lcd.monitoring.models import SystemTelemetry


class TelemetryWorker(QObject):
    finished = Signal(object)
    failed = Signal()

    def __init__(self, telemetry_provider: Callable[[], SystemTelemetry]) -> None:
        super().__init__()
        self._telemetry_provider = telemetry_provider

    @Slot()
    def run(self) -> None:
        try:
            telemetry = self._telemetry_provider()
        except Exception:
            self.failed.emit()
            return

        self.finished.emit(telemetry)
