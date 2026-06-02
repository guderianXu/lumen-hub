from __future__ import annotations

import atexit
import faulthandler
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QtMsgType, qInstallMessageHandler, qVersion

from usb9_lcd.platforms import current_platform

try:  # pragma: no cover - version attributes are integration details
    import PySide6
    import shiboken6
except Exception:  # pragma: no cover
    PySide6 = None
    shiboken6 = None


_LOG_FILE = None
_LOG_PATH: Path | None = None
_LOGGER = logging.getLogger("usb9_lcd.gui")
_QT_HANDLER_INSTALLED = False


def configure_debug_logging(log_path: str | Path | None = None) -> Path:
    path = Path(log_path or os.environ.get("USB9_LCD_LOG", current_platform().gui_log_path()))
    path.parent.mkdir(parents=True, exist_ok=True)

    global _LOG_FILE, _LOG_PATH
    _LOG_PATH = path
    if _LOG_FILE is not None:
        try:
            _LOG_FILE.close()
        except OSError:
            pass

    _LOG_FILE = path.open("a", encoding="utf-8", buffering=1)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[logging.StreamHandler(_LOG_FILE)],
        force=True,
    )
    faulthandler.enable(file=_LOG_FILE, all_threads=True)
    sys.excepthook = _log_uncaught_exception
    global _QT_HANDLER_INSTALLED
    qInstallMessageHandler(_log_qt_message)
    _QT_HANDLER_INSTALLED = True
    atexit.register(_close_log_file)
    atexit.register(shutdown_debug_logging)

    log_event(
        "gui_debug_logging_configured",
        log_path=str(path),
        executable=sys.executable,
        argv=sys.argv,
        python=sys.version.replace("\n", " "),
        platform=platform.platform(),
        qt=qVersion(),
        pyside=getattr(PySide6, "__version__", "unknown") if PySide6 is not None else "missing",
        shiboken=getattr(shiboken6, "__version__", "unknown") if shiboken6 is not None else "missing",
        display=os.environ.get("DISPLAY", ""),
        wayland_display=os.environ.get("WAYLAND_DISPLAY", ""),
        qt_qpa_platform=os.environ.get("QT_QPA_PLATFORM", ""),
        qt_im_module=os.environ.get("QT_IM_MODULE", ""),
    )
    return path


def shutdown_debug_logging() -> None:
    global _QT_HANDLER_INSTALLED
    if _QT_HANDLER_INSTALLED:
        qInstallMessageHandler(None)
        _QT_HANDLER_INSTALLED = False
    log_event("gui_debug_logging_shutdown")
    if _LOG_FILE is not None:
        try:
            _LOG_FILE.flush()
        except OSError:
            pass


def log_event(event: str, **fields: Any) -> None:
    if fields:
        details = " ".join(f"{key}={value!r}" for key, value in sorted(fields.items()))
        _LOGGER.info("%s %s", event, details)
    else:
        _LOGGER.info("%s", event)


def log_exception(event: str, error: BaseException, **fields: Any) -> None:
    details = " ".join(f"{key}={value!r}" for key, value in sorted(fields.items()))
    _LOGGER.exception("%s %s error=%r", event, details, error)


def recent_log_lines(log_path: str | Path | None = None, *, limit: int = 6) -> list[str]:
    if limit <= 0:
        return []
    path = Path(log_path) if log_path is not None else _LOG_PATH
    if path is None or not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [line for line in lines if line.strip()][-limit:]


def _log_uncaught_exception(exc_type, exc_value, exc_traceback) -> None:  # noqa: ANN001
    _LOGGER.critical("uncaught_python_exception", exc_info=(exc_type, exc_value, exc_traceback))
    sys.__excepthook__(exc_type, exc_value, exc_traceback)


def _log_qt_message(mode: QtMsgType, context, message: str) -> None:  # noqa: ANN001
    level = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }.get(mode, logging.INFO)
    _LOGGER.log(
        level,
        "qt_message file=%r line=%r function=%r message=%s",
        getattr(context, "file", None),
        getattr(context, "line", None),
        getattr(context, "function", None),
        message,
    )


def _close_log_file() -> None:
    global _LOG_FILE
    if _LOG_FILE is None:
        return
    try:
        _LOG_FILE.flush()
        _LOG_FILE.close()
    except OSError:
        pass
    _LOG_FILE = None
