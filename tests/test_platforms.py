from __future__ import annotations

import json
from pathlib import Path

from usb9_lcd.gui.platform_diagnostics import PlatformDiagnosticsDialog, render_support_report
from usb9_lcd.gui.settings import GuiSettings
from usb9_lcd.gui.device_inventory import DeviceTreeItem, DeviceTreeSnapshot
from usb9_lcd.gui.system_status import StatusItem, SystemStatusSnapshot
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

    report = render_support_report(settings, LinuxPlatformAdapter())

    assert "Lumen Hub 支持报告" in report
    assert "平台诊断" in report
    assert "OpenRGB 路径" in report
    assert str(tmp_path / "state" / "lumen-hub" / "logs") in report


def test_support_report_includes_device_tree_permissions_and_recent_events(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    settings = GuiSettings()
    snapshot = SystemStatusSnapshot(
        components=[StatusItem("OpenRGB", "ok", "已连接 4 个目标")],
        permissions=[StatusItem("PWM 写权限", "warn", "2 个 pwm* 文件需授权")],
        device_tree=DeviceTreeSnapshot(
            roots=[
                DeviceTreeItem(
                    "灯效",
                    "lighting",
                    "ok",
                    "OpenRGB",
                    [DeviceTreeItem("ARGB Header", "zone", "ok", "30 LED")],
                )
            ]
        ),
        recent_events=["联力无线关灯完成"],
    )

    report = render_support_report(settings, LinuxPlatformAdapter(), snapshot=snapshot)

    assert "Lumen Hub 支持报告" in report
    assert "OpenRGB" in report
    assert "PWM 写权限" in report
    assert "设备树" in report
    assert "ARGB Header" in report
    assert "联力无线关灯完成" in report


def test_platform_diagnostics_dialog_can_copy_and_save_report(tmp_path):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    settings = GuiSettings()
    snapshot = SystemStatusSnapshot(
        components=[StatusItem("CPU", "ok", "温度可读")],
        permissions=[StatusItem("LCD 写权限", "ok", "1/1 个设备可写")],
        recent_events=["诊断测试事件"],
    )
    dialog = PlatformDiagnosticsDialog(settings, status_provider=lambda: snapshot)

    dialog.copy_report()
    save_path = tmp_path / "support-report.txt"
    assert dialog.save_report(save_path) is True

    assert "复制报告" == dialog.copy_button.text()
    assert "保存报告" == dialog.save_button.text()
    assert "诊断测试事件" in QApplication.clipboard().text()
    assert "诊断测试事件" in save_path.read_text(encoding="utf-8")

    dialog.close()
    app.quit()


def test_openrgb_server_uses_installed_candidate_when_configured_path_is_missing(tmp_path):
    missing = tmp_path / "missing" / "OpenRGB.exe"
    installed = tmp_path / "installed" / "OpenRGB.exe"
    installed.parent.mkdir()
    installed.write_text("placeholder", encoding="utf-8")

    class Adapter:
        def openrgb_candidate_paths(self):
            return [missing, installed]

    assert resolve_openrgb_app_path(missing, Adapter()) == installed


def test_linux_install_artifacts_define_gui_entrypoint_and_permissions():
    desktop = Path("packaging/linux/lumen-hub.desktop").read_text(encoding="utf-8")
    udev = Path("packaging/linux/lumen-hub-udev.rules").read_text(encoding="utf-8")
    tmpfiles = Path("packaging/linux/lumen-hub-tmpfiles.conf").read_text(encoding="utf-8")

    assert "[Desktop Entry]" in desktop
    assert "Name=Lumen Hub" in desktop
    assert "Exec=lumen-hub-gui" in desktop
    assert "Type=Application" in desktop
    assert "Categories=Utility;System;" in desktop
    assert "0b05" in udev and "1c7b" in udev
    assert "0416" in udev and "8040" in udev and "8041" in udev and "7372" in udev
    assert "04fc" in udev and "7393" in udev
    assert "1cbe" in udev and "0006" in udev
    assert 'GROUP="plugdev"' in udev
    assert 'TAG+="uaccess"' in udev
    assert "OpenRGB" in udev and "i2c-dev" in udev
    assert "pwm*" in tmpfiles
    assert "pwm*_enable" in tmpfiles
    assert "plugdev" in tmpfiles


def test_windows_autostart_script_registers_lumen_hub_gui():
    script = Path("packaging/windows/lumen-hub-autostart.ps1").read_text(encoding="utf-8")

    assert "lumen-hub-gui" in script
    assert "Lumen Hub" in script
    assert "WScript.Shell" in script
    assert "Startup" in script
    assert "ScheduledTask" in script
    assert "Uninstall" in script


def test_readme_documents_install_and_autostart_artifacts():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "packaging/linux/lumen-hub.desktop" in readme
    assert "packaging/linux/lumen-hub-udev.rules" in readme
    assert "packaging/linux/lumen-hub-tmpfiles.conf" in readme
    assert "packaging/windows/lumen-hub-autostart.ps1" in readme
    assert "lumen-hub-gui" in readme


def test_permission_request_models_cover_safe_operations(tmp_path, monkeypatch):
    import usb9_lcd.service.permissions as permissions

    monkeypatch.setattr(permissions.os, "getuid", lambda: 1234)
    monkeypatch.setattr(permissions.os, "getgid", lambda: 5678)
    pwm_path = tmp_path / "pwm1"
    power_path = tmp_path / "energy_uj"
    hidraw_path = tmp_path / "hidraw0"
    openrgb_path = tmp_path / "OpenRGB"

    pwm = permissions.build_pwm_write_request([pwm_path])
    powercap = permissions.build_powercap_read_request([power_path])
    hidraw = permissions.build_hidraw_write_request([hidraw_path])
    openrgb = permissions.build_openrgb_path_check_request(openrgb_path)
    status = permissions.detect_permission_helper_status()
    items = permissions.permission_helper_status_items(status)

    assert pwm.operation == "pwm-write"
    assert pwm.access == "read-write"
    assert "chown 1234:5678" in pwm.shell_command
    assert "chmod u+rw,g+rw" in pwm.shell_command
    assert powercap.operation == "powercap-read"
    assert powercap.access == "read"
    assert "chmod u+r,g+r" in powercap.shell_command
    assert hidraw.operation == "hidraw-write"
    assert hidraw.access == "read-write"
    assert openrgb.operation == "openrgb-path-check"
    assert openrgb.shell_command == ""
    assert status.backend == "direct-shell-fallback"
    assert items[0].label == "权限 Helper"
    assert "direct-shell-fallback" in items[0].detail


def test_settings_persist_lighting_and_lianli_physical_layout(tmp_path):
    from usb9_lcd.gui.settings import GuiSettings, LianLiWirelessTargetSettings, load_settings, save_settings

    settings = GuiSettings()
    settings.lighting.physical_layout["device:0:zone:1"] = {
        "order": 2,
        "led_count": 26,
        "direction": "reverse",
        "port_label": "top",
    }
    settings.lianli_wireless.targets["aa:bb:cc:dd:ee:ff"] = LianLiWirelessTargetSettings(
        mac="aa:bb:cc:dd:ee:ff",
        led_count=26,
        layout_order=3,
        direction="reverse",
        port_label="rear",
    )
    settings.host_fan.channel_roles["/sys/class/hwmon/hwmon0/pwm1"] = "cpu"
    path = tmp_path / "settings.json"

    save_settings(settings, path)
    loaded = load_settings(path)

    assert loaded.lighting.physical_layout["device:0:zone:1"] == {
        "order": 2,
        "led_count": 26,
        "direction": "reverse",
        "port_label": "top",
    }
    target = loaded.lianli_wireless.targets["aa:bb:cc:dd:ee:ff"]
    assert target.layout_order == 3
    assert target.direction == "reverse"
    assert target.port_label == "rear"
    assert loaded.host_fan.channel_roles["/sys/class/hwmon/hwmon0/pwm1"] == "cpu"
