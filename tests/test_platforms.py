from __future__ import annotations

import json

from usb9_lcd.gui.platform_diagnostics import render_platform_diagnostic_report
from usb9_lcd.gui.settings import GuiSettings
from usb9_lcd.lighting.server import resolve_openrgb_app_path
from usb9_lcd.platforms.linux import LinuxPlatformAdapter
from usb9_lcd.platforms.windows import WindowsPlatformAdapter


def test_linux_platform_prefers_legacy_settings_until_new_config_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    adapter = LinuxPlatformAdapter()
    legacy = adapter.legacy_config_dir() / "settings.json"
    current = adapter.config_dir() / "settings.json"

    assert adapter.settings_path() == current

    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"keepalive_enabled": True}), encoding="utf-8")

    assert adapter.settings_path() == legacy

    current.parent.mkdir(parents=True)
    current.write_text(json.dumps({"keepalive_enabled": False}), encoding="utf-8")

    assert adapter.settings_path() == current


def test_windows_platform_uses_appdata_localappdata_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    adapter = WindowsPlatformAdapter()

    assert adapter.settings_path() == tmp_path / "Roaming" / "LumenHub" / "settings.json"
    assert adapter.cache_dir() == tmp_path / "Local" / "LumenHub" / "Cache"
    assert adapter.log_dir() == tmp_path / "Local" / "LumenHub" / "Logs"


def test_windows_diagnostics_mentions_admin_lhm_and_fan_backend(tmp_path, monkeypatch):
    import usb9_lcd.platforms.windows as windows_platform

    program_files = tmp_path / "ProgramFiles"
    local_appdata = tmp_path / "Local"
    lhm_path = local_appdata / "Microsoft" / "WinGet" / "Packages" / "LibreHardwareMonitor" / "LibreHardwareMonitorLib.dll"
    lhm_path.parent.mkdir(parents=True)
    lhm_path.write_text("placeholder", encoding="utf-8")
    powershell = tmp_path / "PowerShell" / "powershell.exe"
    powershell.parent.mkdir()
    powershell.write_text("placeholder", encoding="utf-8")
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "ProgramFilesX86"))
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(windows_platform, "_is_admin", lambda: False)
    monkeypatch.setattr(windows_platform.shutil, "which", lambda name: str(powershell) if name == "powershell.exe" else None)
    adapter = WindowsPlatformAdapter()

    items = adapter.diagnostic_items(openrgb_path=tmp_path / "OpenRGB.exe", openrgb_host="127.0.0.1", openrgb_port=6742)
    by_label = {item.label: item for item in items}

    assert "Windows 管理员权限" in by_label
    assert by_label["PowerShell"].ok is True
    assert by_label["LibreHardwareMonitorLib.dll"].ok is True
    assert str(lhm_path) in by_label["LibreHardwareMonitorLib.dll"].detail
    assert by_label["Windows 普通风扇后端"].ok is True
    assert "LibreHardwareMonitor Control" in by_label["Windows 普通风扇后端"].detail


def test_platform_diagnostic_report_includes_openrgb_and_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    settings = GuiSettings()
    settings.openrgb.app_path = str(tmp_path / "OpenRGB")

    report = render_platform_diagnostic_report(settings, LinuxPlatformAdapter())

    assert "平台诊断" in report
    assert "OpenRGB 路径" in report
    assert str(tmp_path / "state" / "lumen-hub" / "logs") in report


def test_openrgb_server_uses_installed_candidate_when_configured_path_is_missing(tmp_path):
    missing = tmp_path / "missing" / "OpenRGB.exe"
    installed = tmp_path / "installed" / "OpenRGB.exe"
    installed.parent.mkdir()
    installed.write_text("placeholder", encoding="utf-8")

    class Adapter:
        def openrgb_candidate_paths(self):
            return [missing, installed]

    assert resolve_openrgb_app_path(missing, Adapter()) == installed
