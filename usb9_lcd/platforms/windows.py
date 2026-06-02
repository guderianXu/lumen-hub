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

    def libre_hardware_monitor_candidate_paths(self) -> list[Path]:
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        values = [
            os.environ.get("LIBREHARDWAREMONITOR_DLL", ""),
            str(program_files / "LibreHardwareMonitor/LibreHardwareMonitorLib.dll"),
            str(program_files_x86 / "LibreHardwareMonitor/LibreHardwareMonitorLib.dll"),
            str(local_app_data / "Programs/LibreHardwareMonitor/LibreHardwareMonitorLib.dll"),
        ]
        winget_root = local_app_data / "Microsoft/WinGet/Packages"
        if winget_root.is_dir():
            values.extend(str(path) for path in winget_root.glob("**/LibreHardwareMonitorLib.dll"))
        return _unique_paths(Path(value) for value in values if value)

    def diagnostic_items(self, *, openrgb_path: str | Path, openrgb_host: str, openrgb_port: int) -> list[PlatformStatusItem]:
        items = super().diagnostic_items(
            openrgb_path=openrgb_path,
            openrgb_host=openrgb_host,
            openrgb_port=openrgb_port,
        )
        lhm_candidates = self.libre_hardware_monitor_candidate_paths()
        lhm_path = next((path for path in lhm_candidates if path.is_file()), None)
        powershell_path = shutil.which("powershell.exe") or shutil.which("powershell")
        items.extend(
            [
                PlatformStatusItem("Windows 管理员权限", _is_admin(), "HID/WinUSB 驱动修复和部分传感器写入可能需要管理员"),
                PlatformStatusItem(
                    "PowerShell",
                    powershell_path is not None,
                    powershell_path or "未找到 powershell.exe，Windows 传感器采集不可用",
                ),
                PlatformStatusItem(
                    "LibreHardwareMonitorLib.dll",
                    lhm_path is not None,
                    str(lhm_path or (lhm_candidates[0] if lhm_candidates else "未找到候选路径")),
                ),
                PlatformStatusItem(
                    "Windows 普通风扇后端",
                    lhm_path is not None,
                    (
                        "LibreHardwareMonitor Control 传感器可用于主板/控制器风扇；GPU 风扇保持只读"
                        if lhm_path is not None
                        else "需要 LibreHardwareMonitorLib.dll；否则通常只能读 WMI/传感器，不能写风扇"
                    ),
                ),
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
