from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import QApplication

from usb9_lcd.gui.debug import configure_debug_logging, log_event, shutdown_debug_logging
from usb9_lcd.gui.main_window import MainWindow


def configure_qt_environment() -> None:
    if os.environ.get("QT_IM_MODULE", "").lower() in {"", "ibus"}:
        os.environ["QT_IM_MODULE"] = "compose"


def main(argv: list[str] | None = None) -> int:
    configure_qt_environment()
    log_path = configure_debug_logging()
    log_event("gui_app_starting", log_path=str(log_path))
    app = QApplication(sys.argv[:1] + (argv or []))
    app.aboutToQuit.connect(shutdown_debug_logging)
    window = MainWindow()
    window.show()
    exit_code = app.exec()
    log_event("gui_app_exited", exit_code=exit_code)
    shutdown_debug_logging()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
