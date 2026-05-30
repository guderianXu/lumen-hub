from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path


class OpenRgbServerManager:
    def __init__(self, app_path: str | Path, host: str = "127.0.0.1", port: int = 6742) -> None:
        self.app_path = Path(app_path)
        self.host = host
        self.port = port
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
        if not self.app_path.is_file():
            raise FileNotFoundError(f"OpenRGB executable not found: {self.app_path}")

        log_path = self.app_path.parent.parent / "openrgb-server.log"
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
        )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_running():
                return True
            if self.process.poll() is not None:
                return False
            time.sleep(0.2)
        return self.is_running()
