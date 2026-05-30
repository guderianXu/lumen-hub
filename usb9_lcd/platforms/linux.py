from __future__ import annotations

import os
import shutil
from pathlib import Path

from usb9_lcd.platforms.base import APP_SLUG, LEGACY_APP_SLUG, PlatformAdapter, PlatformStatusItem, _unique_paths


class LinuxPlatformAdapter(PlatformAdapter):
    platform_id = "linux"
    display_name = "Linux"

    def config_dir(self) -> Path:
        return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_SLUG

    def legacy_config_dir(self) -> Path:
        return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / LEGACY_APP_SLUG

    def cache_dir(self) -> Path:
        return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP_SLUG

    def log_dir(self) -> Path:
        state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        return state_home / APP_SLUG / "logs"

    def openrgb_candidate_paths(self) -> list[Path]:
        values = [
            os.environ.get("OPENRGB_PATH", ""),
            shutil.which("openrgb") or "",
            str(Path.home() / ".local/share/openrgb-usb9/squashfs-root/AppRun"),
            "/usr/bin/openrgb",
            "/usr/local/bin/openrgb",
            "/snap/bin/openrgb",
        ]
        return _unique_paths(Path(value) for value in values if value)

    def diagnostic_items(self, *, openrgb_path: str | Path, openrgb_host: str, openrgb_port: int) -> list[PlatformStatusItem]:
        items = super().diagnostic_items(
            openrgb_path=openrgb_path,
            openrgb_host=openrgb_host,
            openrgb_port=openrgb_port,
        )
        hidraw_nodes = list(Path("/dev").glob("hidraw*"))
        writable_hidraw = [path for path in hidraw_nodes if os.access(path, os.R_OK | os.W_OK)]
        items.extend(
            [
                PlatformStatusItem(
                    "hidraw 权限",
                    bool(writable_hidraw),
                    f"{len(writable_hidraw)}/{len(hidraw_nodes)} 个节点可读写",
                ),
                PlatformStatusItem(
                    "OpenRGB udev",
                    Path("/etc/udev/rules.d/60-openrgb.rules").is_file(),
                    "/etc/udev/rules.d/60-openrgb.rules",
                ),
                PlatformStatusItem(
                    "CPU hwmon",
                    Path("/sys/class/hwmon").is_dir() and any(Path("/sys/class/hwmon").glob("hwmon*/temp*_input")),
                    "/sys/class/hwmon",
                ),
                PlatformStatusItem("NVIDIA telemetry", shutil.which("nvidia-smi") is not None, "nvidia-smi"),
            ]
        )
        return items
