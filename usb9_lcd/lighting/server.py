from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

from usb9_lcd.platforms import current_platform
from usb9_lcd.platforms.process import hidden_subprocess_kwargs


def resolve_openrgb_app_path(app_path: str | Path, platform_adapter=None) -> Path:  # noqa: ANN001
    configured = Path(app_path)
    if configured.is_file():
        return configured

    adapter = platform_adapter or current_platform()
    for candidate in adapter.openrgb_candidate_paths():
        candidate_path = Path(candidate)
        if candidate_path.is_file():
            return candidate_path
    return configured


class OpenRgbServerManager:
    def __init__(
        self,
        app_path: str | Path,
        host: str = "127.0.0.1",
        port: int = 6742,
        log_path: str | Path | None = None,
    ) -> None:
        self.app_path = resolve_openrgb_app_path(app_path)
        self.host = host
        self.port = port
        self.log_path = Path(log_path) if log_path is not None else current_platform().openrgb_server_log_path()
        self.process: subprocess.Popen | None = None

    def is_running(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=0.2):
                return True
        except OSError:
            return False

    def ensure_running(self, timeout: float = 20.0) -> bool:
        if self.is_running():
            return True
        self.app_path = resolve_openrgb_app_path(self.app_path)
        if not self.app_path.is_file():
            raise FileNotFoundError(f"OpenRGB executable not found: {self.app_path}")

        log_path = self.log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("ab")
        self.process = subprocess.Popen(
            [
                str(self.app_path),
                "--server",
                "--server-port",
                str(self.port),
                "--loglevel",
                "warning",
            ],
            cwd=str(self.app_path.parent),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            **hidden_subprocess_kwargs(),
        )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_running():
                return True
            if self.process.poll() is not None:
                return False
            time.sleep(0.2)
        return self.is_running()
