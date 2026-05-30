from __future__ import annotations

import json

from usb9_lcd.gui.platform_diagnostics import render_platform_diagnostic_report
from usb9_lcd.gui.settings import GuiSettings
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
