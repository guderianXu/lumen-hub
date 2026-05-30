from __future__ import annotations

import os
import platform
import shutil
import socket
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


APP_SLUG = "lumen-hub"
LEGACY_APP_SLUG = "usb9-lcd"


@dataclass(frozen=True)
class PlatformStatusItem:
    label: str
    ok: bool
    detail: str


class PlatformAdapter:
    platform_id = "unknown"
    display_name = "Unknown"

    def config_dir(self) -> Path:
        return Path.home() / f".config/{APP_SLUG}"

    def legacy_config_dir(self) -> Path:
        return Path.home() / f".config/{LEGACY_APP_SLUG}"

    def cache_dir(self) -> Path:
        return Path.home() / f".cache/{APP_SLUG}"

    def log_dir(self) -> Path:
        return self.cache_dir() / "logs"

    def settings_path(self) -> Path:
        current = self.config_dir() / "settings.json"
        legacy = self.legacy_config_dir() / "settings.json"
        if legacy.exists() and not current.exists():
            return legacy
        return current

    def gui_log_path(self) -> Path:
        return self.log_dir() / "lumen-hub-gui.log"

    def openrgb_server_log_path(self) -> Path:
        return self.log_dir() / "openrgb-server.log"

    def keepalive_pid_path(self) -> Path:
        return self.cache_dir() / "keepalive.pid"

    def last_frame_path(self) -> Path:
        return self.cache_dir() / "last-frame.bin"

    def gif_preview_cache_dir(self) -> Path:
        return self.cache_dir() / "gif-preview"

    def openrgb_candidate_paths(self) -> list[Path]:
        values = [os.environ.get("OPENRGB_PATH", ""), shutil.which("openrgb") or ""]
        return _unique_paths(Path(value) for value in values if value)

    def default_openrgb_path(self) -> Path:
        candidates = self.openrgb_candidate_paths()
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0] if candidates else Path("openrgb")

    def diagnostic_items(self, *, openrgb_path: str | Path, openrgb_host: str, openrgb_port: int) -> list[PlatformStatusItem]:
        path = Path(openrgb_path)
        return [
            PlatformStatusItem("系统", True, f"{self.display_name} | {platform.platform()}"),
            PlatformStatusItem("Python", True, sys.version.replace("\n", " ")),
            PlatformStatusItem("配置路径", True, str(self.settings_path())),
            PlatformStatusItem("日志目录", True, str(self.log_dir())),
            PlatformStatusItem("缓存目录", True, str(self.cache_dir())),
            PlatformStatusItem("OpenRGB 路径", path.is_file(), str(path)),
            PlatformStatusItem(
                "OpenRGB SDK",
                _port_open(openrgb_host, openrgb_port),
                f"{openrgb_host}:{openrgb_port}",
            ),
        ]


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _unique_paths(paths: Iterable[str | Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        candidate = Path(path).expanduser()
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique
