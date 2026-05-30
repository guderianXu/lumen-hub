from __future__ import annotations

import sys

from usb9_lcd.platforms.base import PlatformAdapter, PlatformStatusItem
from usb9_lcd.platforms.linux import LinuxPlatformAdapter
from usb9_lcd.platforms.windows import WindowsPlatformAdapter


def current_platform() -> PlatformAdapter:
    if sys.platform.startswith("win"):
        return WindowsPlatformAdapter()
    if sys.platform.startswith("linux"):
        return LinuxPlatformAdapter()
    return PlatformAdapter()


__all__ = [
    "LinuxPlatformAdapter",
    "PlatformAdapter",
    "PlatformStatusItem",
    "WindowsPlatformAdapter",
    "current_platform",
]
