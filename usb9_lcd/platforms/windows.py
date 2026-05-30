from __future__ import annotations

import ctypes
import os
import shutil
from pathlib import Path

from usb9_lcd.platforms.base import PlatformAdapter, PlatformStatusItem, _unique_paths


class WindowsPlatformAdapter(PlatformAdapter):
    platform_id = "windows"
    display_name = "Windows"

    def config_dir(self) -> Path:
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "LumenHub"

    def legacy_config_dir(self) -> Path:
        return self.config_dir()

    def cache_dir(self) -> Path:
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "LumenHub" / "Cache"

    def log_dir(self) -> Path:
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "LumenHub" / "Logs"

    def openrgb_candidate_paths(self) -> list[Path]:
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))
        values = [
            os.environ.get("OPENRGB_PATH", ""),
            shutil.which("OpenRGB.exe") or "",
            shutil.which("openrgb.exe") or "",
            str(Path(program_files) / "OpenRGB/OpenRGB.exe"),
            str(Path(program_files_x86) / "OpenRGB/OpenRGB.exe"),
            str(Path(local_app_data) / "Programs/OpenRGB/OpenRGB.exe"),
        ]
        return _unique_paths(Path(value) for value in values if value)

    def diagnostic_items(self, *, openrgb_path: str | Path, openrgb_host: str, openrgb_port: int) -> list[PlatformStatusItem]:
        items = super().diagnostic_items(
            openrgb_path=openrgb_path,
            openrgb_host=openrgb_host,
            openrgb_port=openrgb_port,
        )
        items.extend(
            [
                PlatformStatusItem("管理员权限", _is_admin(), "Windows HID/驱动修复可能需要管理员"),
                PlatformStatusItem(
                    "OpenRGB 驱动",
                    True,
                    "若设备不可写，请在设备管理器或 OpenRGB 中检查 WinUSB/libusb 驱动",
                ),
            ]
        )
        return items


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
