from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
import pytest

from usb9_lcd.drivers.base import (
    Capability,
    DeviceConnection,
    DisplayDevice,
    PixelFormat,
    PixelStyle,
    PreviewProfile,
    PreviewShape,
)
from usb9_lcd.monitoring.models import CpuTelemetry, FanTelemetry, GpuTelemetry, SystemTelemetry
from usb9_lcd.lighting import LightingTarget


def _fake_device(
    *,
    display_name: str = "ASUS Test LCD",
    driver_id: str = "test.driver",
    control_path: Path = Path("/dev/hidraw-control"),
    data_path: Path = Path("/dev/hidraw-data"),
    width: int = 2,
    height: int = 2,
) -> DisplayDevice:
    return DisplayDevice(
        connection=DeviceConnection(
            driver_id=driver_id,
            display_name=display_name,
            paths=(control_path, data_path),
            writable=True,
            readable=True,
            details=f"control={control_path} data={data_path}",
        ),
        width=width,
        height=height,
        pixel_format=PixelFormat.RGB565,
        preview=PreviewProfile(
            width=width,
            height=height,
            shape=PreviewShape.SQUARE,
            pixel_style=PixelStyle.CONTINUOUS,
            label=display_name,
        ),
        capabilities=frozenset({Capability.STATIC_IMAGE}),
    )


class FakeDriver:
    driver_id = "test.driver"
    display_name = "ASUS Test Driver"

    def __init__(self) -> None:
        self.device = _fake_device()
        self.uploads: list[tuple[DisplayDevice, bytes]] = []

    def discover(self) -> list[DisplayDevice]:
        return [self.device]

    def upload_static_frame(self, device: DisplayDevice, frame: bytes) -> None:
        self.uploads.append((device, frame))


class MultiDeviceDriver:
    driver_id = "test.driver"
    display_name = "ASUS Test Driver"

    def __init__(self) -> None:
        self.devices = [
            _fake_device(
                display_name="ASUS Test LCD 1",
                control_path=Path("/dev/hidraw-control-1"),
                data_path=Path("/dev/hidraw-data-1"),
            ),
            _fake_device(
                display_name="ASUS Test LCD 2",
                control_path=Path("/dev/hidraw-control-2"),
                data_path=Path("/dev/hidraw-data-2"),
            ),
        ]
        self.uploads: list[tuple[DisplayDevice, bytes]] = []

    def discover(self) -> list[DisplayDevice]:
        return list(self.devices)

    def upload_static_frame(self, device: DisplayDevice, frame: bytes) -> None:
        self.uploads.append((device, frame))


class SleepAwareMultiDeviceDriver(MultiDeviceDriver):
    def __init__(self) -> None:
        super().__init__()
        self.brightness_calls: list[tuple[DisplayDevice, int]] = []
        self.power_calls: list[tuple[DisplayDevice, bool]] = []

    def set_display_brightness(self, device: DisplayDevice, level: int) -> None:
        self.brightness_calls.append((device, level))

    def set_display_power(self, device: DisplayDevice, enabled: bool) -> None:
        self.power_calls.append((device, enabled))


class EmptyDriver:
    driver_id = "test.empty"
    display_name = "Empty Driver"

    def __init__(self) -> None:
        self.uploads: list[tuple[DisplayDevice, bytes]] = []

    def discover(self) -> list[DisplayDevice]:
        return []

    def upload_static_frame(self, device: DisplayDevice, frame: bytes) -> None:
        self.uploads.append((device, frame))


class UploadFailingDriver(FakeDriver):
    def upload_static_frame(self, device: DisplayDevice, frame: bytes) -> None:
        raise OSError("usb write failed")


class BlockingUploadDriver(FakeDriver):
    def __init__(self, release_upload: threading.Event) -> None:
        super().__init__()
        self.release_upload = release_upload

    def upload_static_frame(self, device: DisplayDevice, frame: bytes) -> None:
        self.release_upload.wait(timeout=1)
        super().upload_static_frame(device, frame)


class FakeLightingController:
    def __init__(self) -> None:
        self.host = "127.0.0.1"
        self.port = 6742
        self.connected = False
        self.applied = []
        self.targets = [
            LightingTarget(
                id="device:0",
                name="ASUS Motherboard / 全部",
                device_index=0,
                zone_index=None,
                modes=("Static", "Breathing", "Rainbow"),
            ),
            LightingTarget(
                id="device:0:zone:1",
                name="ASUS Motherboard / ARGB Header",
                device_index=0,
                zone_index=1,
                modes=("Static", "Direct"),
            ),
        ]

    def connect(self):
        self.connected = True
        return list(self.targets)

    def refresh(self):
        return list(self.targets)

    def apply(self, settings):
        self.applied.append(settings)


class MultiDeviceLightingController(FakeLightingController):
    def __init__(self) -> None:
        super().__init__()
        self.targets.extend(
            [
                LightingTarget(
                    id="device:1",
                    name="G.Skill Memory / 全部",
                    device_index=1,
                    zone_index=None,
                    modes=("Static", "Rainbow"),
                ),
                LightingTarget(
                    id="device:1:zone:0",
                    name="G.Skill Memory / DIMM 1",
                    device_index=1,
                    zone_index=0,
                    modes=("Static", "Rainbow"),
                ),
            ]
        )


def _fake_telemetry() -> SystemTelemetry:
    return SystemTelemetry(
        cpu=CpuTelemetry(package_temperature_c=54.0, utilization_percent=18, power_w=86.4, available=True),
        gpu=GpuTelemetry(
            name="RTX",
            temperature_c=61,
            utilization_percent=42,
            power_w=216.0,
            memory_used_mb=8000,
            memory_total_mb=24000,
            graphics_clock_mhz=2700,
            fan_speed_percent=48,
            available=True,
        ),
        captured_at=datetime(2026, 5, 20, 12, 0, 0),
        fans=[
            FanTelemetry(name="CPU Fan", rpm=987, percent=82, available=True),
            FanTelemetry(name="GPU Fan", rpm=1530, percent=48, available=True),
        ],
    )


def _unavailable_telemetry() -> SystemTelemetry:
    return SystemTelemetry(
        cpu=CpuTelemetry(available=False, error="no sensor"),
        gpu=GpuTelemetry(available=False, error="nvidia-smi missing"),
        captured_at=datetime(2026, 5, 20, 12, 1, 0),
    )


def _process_events_until(app, condition, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    app.processEvents()
    return condition()


class ExplodingDriver:
    driver_id = "test.exploding"
    display_name = "Exploding Driver"

    def discover(self) -> list[DisplayDevice]:
        raise AssertionError("discover should not run during construction")

    def upload_static_frame(self, device: DisplayDevice, frame: bytes) -> None:
        raise AssertionError("upload should not run during construction")


class ExplodingAssetLibrary:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def list_media(self) -> list[object]:
        self.calls.append("list_media")
        raise AssertionError("list_media should not run during construction")

    def load_links(self) -> list[object]:
        self.calls.append("load_links")
        raise AssertionError("load_links should not run during construction")

    def import_file(self, source: Path | str) -> Path:
        self.calls.append("import_file")
        raise AssertionError("import_file should not run during construction")


def test_gui_app_imports_main_entrypoint():
    from usb9_lcd.gui.app import configure_qt_environment, main

    assert callable(main)
    assert callable(configure_qt_environment)


def test_gui_app_defaults_to_compose_input_context(monkeypatch):
    from usb9_lcd.gui.app import configure_qt_environment

    monkeypatch.delenv("QT_IM_MODULE", raising=False)

    configure_qt_environment()

    assert os.environ["QT_IM_MODULE"] == "compose"


def test_gui_app_replaces_ibus_input_context(monkeypatch):
    from usb9_lcd.gui.app import configure_qt_environment

    monkeypatch.setenv("QT_IM_MODULE", "ibus")

    configure_qt_environment()

    assert os.environ["QT_IM_MODULE"] == "compose"


def test_gui_debug_logging_writes_log_file(tmp_path: Path):
    from usb9_lcd.gui.debug import configure_debug_logging, log_event, recent_log_lines

    log_path = tmp_path / "gui.log"

    configured_path = configure_debug_logging(log_path)
    log_event("test_debug_event", value=7)

    assert configured_path == log_path
    text = log_path.read_text(encoding="utf-8")
    assert "gui_debug_logging_configured" in text
    assert "test_debug_event" in text
    assert "value=7" in text
    assert any("test_debug_event" in line for line in recent_log_lines(log_path, limit=2))
    assert recent_log_lines(log_path, limit=0) == []


def test_system_status_report_includes_permission_and_component_state():
    from usb9_lcd.gui.device_inventory import build_device_tree_snapshot
    from usb9_lcd.gui.system_status import StatusItem, SystemStatusSnapshot, render_system_status_report

    device_tree = build_device_tree_snapshot(
        lcd_devices=[_fake_device(display_name="ASUS LCD")],
        telemetry=_fake_telemetry(),
        fan_status="2 个传感器 / 1 可控",
        lighting_status="已连接 4 个目标",
        lianli_status="接收器 1 个",
    )
    snapshot = SystemStatusSnapshot(
        components=[StatusItem("CPU", "ok", "温度/负载可读"), StatusItem("OpenRGB", "warn", "未连接")],
        permissions=[StatusItem("CPU 功耗", "warn", "/sys/class/powercap/.../energy_uj 需授权")],
        device_tree=device_tree,
        recent_events=["CPU 功耗权限已授权"],
    )

    report = render_system_status_report(snapshot)

    assert "系统状态" in report
    assert "CPU" in report
    assert "OpenRGB" in report
    assert "CPU 功耗" in report
    assert "设备树" in report
    assert "ASUS LCD" in report
    assert "GPU Fan" in report
    assert "联力无线" in report
    assert "CPU 功耗权限已授权" in report


def test_main_window_constructs_with_dark_dashboard_pages():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.settings import GuiSettings
    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
        settings=GuiSettings(),
    )

    window.refresh_telemetry()

    assert window.windowTitle() == "usb9-lcd"
    assert [window.navigation.item(index).text() for index in range(window.navigation.count())] == [
        "首页",
        "屏幕",
        "风扇",
        "灯效",
        "设备",
        "联力无线",
        "设置",
    ]
    assert window.navigation.currentRow() == 0
    assert window.pages.count() == 7
    assert "CPU" in window.cpu_temp_value.text()
    assert "GPU" in window.gpu_temp_value.text()
    assert "54°C" in window.home_page.cpu_value.text()
    assert "功耗 86W" in window.home_page.cpu_value.text()
    assert "61°C" in window.home_page.gpu_value.text()
    assert "功耗 216W" in window.home_page.gpu_value.text()
    assert "风扇 1530 RPM" in window.home_page.gpu_value.text()
    assert "GPU Fan 1530 RPM" in window.home_page.fan_value.text()
    assert window.home_page.permission_value.text() != "权限未检查"
    assert window.home_page.mode_value.text() == "日常"
    assert "CPU Fan 987 RPM" in window.home_page.fan_value.text()
    assert window.home_page.lighting_value.text() == "默认关闭"
    assert (
        window.home_page.lianli_value.text() in {"未连接", "正在准备自动连接..."}
        or window.home_page.lianli_value.text().startswith("USB ")
    )
    assert window.home_page.event_labels[0].text() == "控制中心已就绪"
    assert window.lighting_page.brightness_slider.value() == 0
    assert window.device_summary_label.text() == "未发现设备"

    window.close()
    app.quit()


def test_main_window_uses_hardware_dashboard_shell_hooks():
    from PySide6.QtWidgets import QApplication, QFrame, QPushButton

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    assert window.findChild(QFrame, "AppShell") is not None
    assert window.findChild(QFrame, "TopBar") is not None
    assert window.findChild(QFrame, "MainContentShell") is not None
    assert window.navigation.objectName() == "SideNav"
    assert any(
        button.objectName() == "SecondaryButton" and button.text() == "平台诊断"
        for button in window.findChildren(QPushButton)
    )

    window.close()
    app.quit()


def test_control_center_exposes_hardware_dashboard_sections():
    from PySide6.QtWidgets import QApplication, QFrame, QPushButton

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    required_frames = (
        "HomeHeroPanel",
        "HomeMetricGrid",
        "HomeCommandDock",
        "HomeTimelinePanel",
    )
    missing = [name for name in required_frames if window.home_page.findChild(QFrame, name) is None]
    assert missing == []

    groups = {
        button.property("commandGroup")
        for button in window.home_page.findChildren(QPushButton)
        if button.property("commandGroup")
    }
    assert {"safety", "screen", "fan", "lighting", "lianli"}.issubset(groups)

    window.close()
    app.quit()


def test_gui_screenshot_tool_imports_without_starting_qt():
    import importlib.util
    from pathlib import Path

    path = Path("tools/capture_gui_screenshots.py")
    spec = importlib.util.spec_from_file_location("capture_gui_screenshots", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.PAGE_ROWS == {
        "home": 0,
        "screen": 1,
        "fan": 2,
        "lighting": 3,
        "lianli": 5,
    }


def test_home_dashboard_shows_nvidia_gpu_fan_speed_percent_when_rpm_is_unavailable():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.home import ControlCenterPage

    app = QApplication.instance() or QApplication([])
    page = ControlCenterPage(
        navigate=lambda _page: None,
        upload_monitor=lambda: None,
        load_fan_control=lambda: None,
        connect_lighting=lambda: None,
        sleep_all_off=lambda: None,
    )
    telemetry = SystemTelemetry(
        cpu=CpuTelemetry(package_temperature_c=54.0, available=True),
        gpu=GpuTelemetry(name="RTX", temperature_c=61, power_w=216, fan_speed_percent=48, available=True),
        captured_at=datetime(2026, 5, 20, 12, 0, 0),
    )

    page.update_telemetry(telemetry)

    assert "风扇 48%" in page.gpu_value.text()

    page.close()
    app.quit()


def test_home_page_shows_permission_status_and_operation_feedback():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.home import ControlCenterPage

    app = QApplication.instance() or QApplication([])
    page = ControlCenterPage(lambda _page: None, lambda: None, lambda: None, lambda: None, lambda: None)

    page.update_permission_status("CPU 功耗: 已授权\nPWM: 可写")
    page.add_event("OpenRGB 应用成功：3 个目标")

    assert "CPU 功耗" in page.permission_value.text()
    assert page.event_labels[0].text() == "OpenRGB 应用成功：3 个目标"
    assert page.recent_events()[0] == "OpenRGB 应用成功：3 个目标"

    page.close()
    app.quit()


def test_main_window_settings_page_shows_saved_feedback(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.main_window as main_window
    from usb9_lcd.gui.main_window import MainWindow
    from usb9_lcd.gui.settings import GuiSettings

    saved = []
    monkeypatch.setattr(main_window, "save_settings", lambda settings: saved.append(settings))
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
        settings=GuiSettings(),
    )

    window.save_settings_page()

    assert saved
    assert "设置已保存" in window.settings_save_status_label.text()
    assert "设置已保存" in window.statusBar().currentMessage()
    assert window.home_page.event_labels[0].text() == "设置已保存"

    window.close()
    app.quit()


def test_main_window_reset_settings_replaces_child_page_settings(monkeypatch, tmp_path: Path):
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QApplication, QWidget

    import usb9_lcd.gui.main_window as main_window
    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.main_window import MainWindow
    from usb9_lcd.gui.settings import GuiSettings

    class FakeLianLiPage(QWidget):
        status_changed = Signal(str)

        def __init__(self, settings: GuiSettings) -> None:
            super().__init__()
            self.settings = settings

        def home_status_text(self) -> str:
            return "lianli"

        def turn_off_all_lighting(self) -> None:
            return None

        def update_telemetry(self, _telemetry: SystemTelemetry) -> None:
            return None

    saved: list[GuiSettings] = []
    monkeypatch.setattr(main_window, "DEFAULT_SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(main_window, "save_settings", lambda settings: saved.append(settings))
    monkeypatch.setattr(pages, "save_settings", lambda settings: saved.append(settings))
    old_settings = GuiSettings()
    old_settings.openrgb.port = 12345
    old_settings.lighting.palette = "warm"
    old_settings.lighting.target_profiles["device:0:zone:1"] = {"color": "#ff0000"}
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
        settings=old_settings,
        lianli_page_factory=FakeLianLiPage,
    )

    window.reset_settings_file()
    new_settings = window.settings
    window.lighting_page.lighting_palette_combo.setCurrentIndex(window.lighting_page.lighting_palette_combo.findData("cool"))

    assert new_settings is not old_settings
    assert window.lighting_page.settings is new_settings
    assert getattr(window.fan_page, "settings", None) is new_settings
    assert window.lianli_page.settings is new_settings
    assert window.settings_openrgb_port.value() == 6742
    assert window.lighting_page.argb_zone_size.value() == 30
    assert old_settings.openrgb.port == 12345
    assert old_settings.lighting.palette == "warm"
    assert saved[-1] is new_settings

    window.close()
    app.quit()


def test_control_center_navigation_buttons_change_pages():
    from PySide6.QtWidgets import QApplication, QPushButton

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    screen_button = next(
        button
        for button in window.home_page.findChildren(QPushButton)
        if button.property("moduleAction") == "打开屏幕"
    )
    screen_button.click()

    assert window.navigation.currentRow() == 1
    assert window.screen_tabs.currentIndex() == 0

    assets_button = next(
        button
        for button in window.home_page.findChildren(QPushButton)
        if button.property("moduleAction") == "素材库"
    )
    assets_button.click()

    assert window.navigation.currentRow() == 1
    assert window.screen_tabs.currentIndex() == 1

    lighting_button = next(
        button
        for button in window.home_page.findChildren(QPushButton)
        if button.property("moduleAction") == "打开灯效"
    )
    lighting_button.click()

    assert window.navigation.currentRow() == 3

    window.close()
    app.quit()


def test_control_center_mode_buttons_update_dashboard_events():
    from PySide6.QtWidgets import QApplication, QPushButton

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    game_button = next(
        button
        for button in window.home_page.findChildren(QPushButton)
        if button.property("modeAction") == "游戏"
    )
    game_button.click()

    assert window.home_page.mode_value.text() == "游戏"
    assert window.home_page.event_labels[0].text() == "切换到游戏模式"
    assert game_button.isChecked()

    window.close()
    app.quit()


def test_home_dashboard_tracks_subsystem_status_signals():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    window.fan_page.status_changed.emit("2 通道\n1 有转速 · 只读监控")
    window.lighting_page.status_changed.emit("已连接 3\n关闭")
    window.lianli_page.status_changed.emit("接收器 1 个")

    assert window.home_page.fan_value.text() == "2 通道\n1 有转速 · 只读监控"
    assert window.home_page.lighting_value.text() == "已连接 3\n关闭"
    assert window.home_page.lianli_value.text() == "接收器 1 个"

    window.close()
    app.quit()


def test_main_window_system_status_snapshot_includes_device_tree():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow
    from usb9_lcd.gui.system_status import render_system_status_report

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    window.refresh_devices()
    window.refresh_telemetry()
    report = render_system_status_report(window._system_status_snapshot())

    assert "设备树" in report
    assert "ASUS Test LCD" in report
    assert "GPU Fan" in report
    assert "灯效" in report
    assert "联力无线" in report
    assert "正常" in window.home_page.device_tree_value.text()

    window.close()
    app.quit()


def test_main_window_sleep_all_off_blanks_lcds_and_turns_off_openrgb():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow
    from usb9_lcd.gui.settings import GuiSettings

    app = QApplication.instance() or QApplication([])
    settings = GuiSettings()
    settings.openrgb.auto_start_server = False
    driver = SleepAwareMultiDeviceDriver()
    window = MainWindow(
        driver=driver,
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
        settings=settings,
    )
    controller = FakeLightingController()
    window.lighting_page.controller = controller
    lianli_calls = []
    window.lianli_page.turn_off_all_lighting = lambda: lianli_calls.append("off")

    window.refresh_devices()
    driver.uploads.clear()
    window.sleep_all_off_button.click()

    expected_frame = b"\x00\x00" * 4
    assert len(driver.uploads) == 2
    assert [device.display_name for device, _frame in driver.uploads] == [
        "ASUS Test LCD 1",
        "ASUS Test LCD 2",
    ]
    assert all(frame == expected_frame for _device, frame in driver.uploads)
    assert [level for _device, level in driver.brightness_calls] == [0, 0]
    assert [enabled for _device, enabled in driver.power_calls] == [False, False]
    assert _process_events_until(app, lambda: len(controller.applied) == 1)
    assert controller.applied[0].target_id == "device:0"
    assert controller.applied[0].effect == "off"
    assert controller.applied[0].brightness_percent == 0
    assert lianli_calls == ["off"]
    assert window.home_page.mode_value.text() == "睡眠"
    assert "睡眠全关已执行" in window.statusBar().currentMessage()
    assert "联力" in window.statusBar().currentMessage()

    window.close()
    app.quit()


def test_main_window_sleep_all_off_prevents_keepalive_restart(monkeypatch, tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow
    from usb9_lcd.gui.settings import GuiSettings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("usb9_lcd.gui.main_window.stop_existing_keepalive", lambda: None)
    started = []

    class FakePopen:
        def __init__(self, args, **kwargs):  # noqa: ANN001
            started.append((args, kwargs))

    monkeypatch.setattr("usb9_lcd.gui.main_window.subprocess.Popen", FakePopen)
    driver = FakeDriver()
    driver.device = _fake_device(driver_id="asus.lc_iii")
    settings = GuiSettings()
    settings.openrgb.auto_start_server = False
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=driver,
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
        settings=settings,
    )
    window.lighting_page.controller = FakeLightingController()

    window.refresh_devices()
    window.sleep_all_off_button.click()
    assert _process_events_until(app, lambda: window._sleep_mode_active)
    window.close()

    assert started == []

    app.quit()


def test_control_center_opens_lianli_wireless_page():
    from PySide6.QtWidgets import QApplication, QPushButton

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    lianli_button = next(
        button
        for button in window.home_page.findChildren(QPushButton)
        if button.property("moduleAction") == "打开联力"
    )
    lianli_button.click()

    assert window.navigation.currentRow() == 5

    window.close()
    app.quit()


def test_main_window_pages_are_scroll_wrapped():
    from PySide6.QtWidgets import QApplication, QScrollArea

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    expected_widgets = [
        window.home_page,
        window.screen_page,
        window.fan_page,
        window.lighting_page,
    ]
    for index, expected in enumerate(expected_widgets):
        page = window.pages.widget(index)
        assert isinstance(page, QScrollArea)
        assert page.widgetResizable() is True
        assert page.widget() is expected

    window.close()
    app.quit()


def test_lianli_wireless_page_reads_snapshot_and_unlocks_writes_without_manual_token():
    from PySide6.QtWidgets import QApplication, QLabel

    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo, WirelessSnapshot

    class FakeLianLiBackend:
        def __init__(self):
            self.sent_pwm: list[tuple[WirelessDeviceInfo, list[int]]] = []

        def list_devices(self):
            return WirelessSnapshot(raw=b"snapshot", devices=[device])

        def query_master_mac(self, *, channel):
            return "10:20:30:40:50:60", channel

        def send_pwm(self, target, pwm_values):
            self.sent_pwm.append((target, list(pwm_values)))
            return 4

    device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: backend)

    page.refresh_live_devices()
    assert _process_events_until(app, lambda: '"device_count": 1' in page.lianli_snapshot_text.toPlainText())
    assert "aa:bb:cc:dd:ee:ff" in page.lianli_snapshot_text.toPlainText()
    assert not page.lianli_pwm_button.isEnabled()
    assert not page.lianli_daily_pwm_button.isEnabled()
    assert "写入未启用" in page.lianli_next_action_label.text()
    assert page.lianli_confirm_input.isHidden()
    assert "确认令牌" not in {label.text() for label in page.findChildren(QLabel)}

    page.lianli_write_enable.setChecked(True)
    assert page.lianli_pwm_button.isEnabled()
    assert page.lianli_daily_pwm_button.isEnabled()
    assert "可以写入" in page.lianli_next_action_label.text()

    page.lianli_mac_input.setText("aa:bb:cc:dd:ee:ff")
    page.lianli_pwm_value.setValue(120)
    page.send_live_pwm()
    assert _process_events_until(app, lambda: bool(backend.sent_pwm))
    assert backend.sent_pwm[0][0].mac == "aa:bb:cc:dd:ee:ff"
    assert backend.sent_pwm[0][1] == [120]

    page.close()
    app.quit()


def test_lianli_wireless_page_applies_custom_curve_from_cpu_temperature():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.gui.settings import GuiSettings, LianLiWirelessTargetSettings
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo, WirelessSnapshot

    class FakeLianLiBackend:
        def __init__(self):
            self.sent_pwm: list[tuple[WirelessDeviceInfo, list[int]]] = []

        def list_devices(self):
            return WirelessSnapshot(raw=b"snapshot", devices=[device])

        def send_pwm(self, target, pwm_values):
            self.sent_pwm.append((target, list(pwm_values)))
            return 4

    device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(0, 0, 0, 0),
        fan_rpm=(900, 900, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    settings = GuiSettings()
    settings.lianli_wireless.write_enabled = True
    settings.lianli_wireless.auto_curve_enabled = True
    settings.lianli_wireless.active_target_mac = device.mac
    settings.lianli_wireless.fan_mode = "custom"
    settings.lianli_wireless.fan_curve_points = [[40, 600], [80, 1800]]
    settings.lianli_wireless.targets[device.mac] = LianLiWirelessTargetSettings(
        mac=device.mac,
        master_mac=device.master_mac,
        channel=device.channel,
        rx_type=device.rx_type,
        device_type=device.device_type,
        fan_count=device.fan_count,
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: backend, settings=settings, background_refresh=False)

    telemetry = SystemTelemetry(
        cpu=CpuTelemetry(package_temperature_c=60.0, available=True),
        gpu=GpuTelemetry(available=False),
        captured_at=datetime(2026, 5, 20, 12, 3, 0),
    )
    page.update_telemetry(telemetry)

    assert _process_events_until(app, lambda: bool(backend.sent_pwm))
    assert backend.sent_pwm[0][0].mac == device.mac
    assert backend.sent_pwm[0][1] == [170]
    assert page.lianli_rpm_value.value() == 1200
    assert settings.lianli_wireless.fan_rpm == 1200
    assert settings.lianli_wireless.pwm == 170

    page.close()
    app.quit()


def test_lianli_wireless_page_skips_duplicate_custom_curve_pwm():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.gui.settings import GuiSettings, LianLiWirelessTargetSettings
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo, WirelessSnapshot

    class FakeLianLiBackend:
        def __init__(self):
            self.sent_pwm: list[tuple[WirelessDeviceInfo, list[int]]] = []

        def list_devices(self):
            return WirelessSnapshot(raw=b"snapshot", devices=[device])

        def send_pwm(self, target, pwm_values):
            self.sent_pwm.append((target, list(pwm_values)))
            return 4

    device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(0, 0, 0, 0),
        fan_rpm=(900, 900, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    settings = GuiSettings()
    settings.lianli_wireless.write_enabled = True
    settings.lianli_wireless.auto_curve_enabled = True
    settings.lianli_wireless.active_target_mac = device.mac
    settings.lianli_wireless.fan_mode = "custom"
    settings.lianli_wireless.fan_curve_points = [[40, 600], [80, 1800]]
    settings.lianli_wireless.targets[device.mac] = LianLiWirelessTargetSettings(
        mac=device.mac,
        master_mac=device.master_mac,
        channel=device.channel,
        rx_type=device.rx_type,
        device_type=device.device_type,
        fan_count=device.fan_count,
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: backend, settings=settings, background_refresh=False)

    page.update_telemetry(
        SystemTelemetry(
            cpu=CpuTelemetry(package_temperature_c=60.0, available=True),
            gpu=GpuTelemetry(available=False),
            captured_at=datetime(2026, 5, 20, 12, 3, 0),
        )
    )
    assert _process_events_until(app, lambda: len(backend.sent_pwm) == 1)

    page.update_telemetry(
        SystemTelemetry(
            cpu=CpuTelemetry(package_temperature_c=61.0, available=True),
            gpu=GpuTelemetry(available=False),
            captured_at=datetime(2026, 5, 20, 12, 3, 2),
        )
    )
    _process_events_until(app, lambda: len(backend.sent_pwm) > 1, timeout=0.2)
    assert len(backend.sent_pwm) == 1

    page.close()
    app.quit()


def test_lianli_wireless_page_does_not_apply_curve_without_auto_curve_enable():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.gui.settings import GuiSettings, LianLiWirelessTargetSettings
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo, WirelessSnapshot

    class FakeLianLiBackend:
        def __init__(self):
            self.sent_pwm: list[tuple[WirelessDeviceInfo, list[int]]] = []

        def list_devices(self):
            return WirelessSnapshot(raw=b"snapshot", devices=[device])

        def send_pwm(self, target, pwm_values):
            self.sent_pwm.append((target, list(pwm_values)))
            return 4

    device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(0, 0, 0, 0),
        fan_rpm=(900, 900, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    settings = GuiSettings()
    settings.lianli_wireless.write_enabled = True
    settings.lianli_wireless.auto_curve_enabled = False
    settings.lianli_wireless.active_target_mac = device.mac
    settings.lianli_wireless.fan_mode = "custom"
    settings.lianli_wireless.fan_curve_points = [[40, 600], [80, 1800]]
    settings.lianli_wireless.targets[device.mac] = LianLiWirelessTargetSettings(
        mac=device.mac,
        master_mac=device.master_mac,
        channel=device.channel,
        rx_type=device.rx_type,
        device_type=device.device_type,
        fan_count=device.fan_count,
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: backend, settings=settings, background_refresh=False)

    page.update_telemetry(
        SystemTelemetry(
            cpu=CpuTelemetry(package_temperature_c=60.0, available=True),
            gpu=GpuTelemetry(available=False),
            captured_at=datetime(2026, 5, 20, 12, 3, 0),
        )
    )
    _process_events_until(app, lambda: bool(backend.sent_pwm), timeout=0.2)

    assert backend.sent_pwm == []
    assert settings.lianli_wireless.fan_rpm != 1200

    page.close()
    app.quit()


def test_lianli_wireless_page_edits_curve_for_selected_preset_mode():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.gui.settings import GuiSettings

    settings = GuiSettings()
    settings.lianli_wireless.fan_mode = "quiet"
    settings.lianli_wireless.fan_curve_profiles["quiet"] = [[30, 400], [70, 900]]
    settings.lianli_wireless.fan_curve_profiles["normal"] = [[30, 600], [70, 1300]]
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: None, settings=settings, background_refresh=False)

    assert page.lianli_curve_editor.points() == [[30, 400], [70, 900]]

    page.lianli_fan_mode_combo.setCurrentIndex(page.lianli_fan_mode_combo.findData("normal"))
    assert page.lianli_curve_editor.points() == [[30, 600], [70, 1300]]

    page._lianli_curve_changed([[35, 700], [75, 1500]])

    assert settings.lianli_wireless.fan_mode == "normal"
    assert settings.lianli_wireless.fan_curve_profiles["normal"] == [[35, 700], [75, 1500]]
    assert settings.lianli_wireless.fan_curve_profiles["quiet"] == [[30, 400], [70, 900]]

    page.close()
    app.quit()


def test_lianli_wireless_page_applies_preset_curve_from_cpu_temperature():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.gui.settings import GuiSettings, LianLiWirelessTargetSettings
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo, WirelessSnapshot

    class FakeLianLiBackend:
        def __init__(self):
            self.sent_pwm: list[tuple[WirelessDeviceInfo, list[int]]] = []

        def list_devices(self):
            return WirelessSnapshot(raw=b"snapshot", devices=[device])

        def send_pwm(self, target, pwm_values):
            self.sent_pwm.append((target, list(pwm_values)))
            return 4

    device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(0, 0, 0, 0),
        fan_rpm=(900, 900, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    settings = GuiSettings()
    settings.lianli_wireless.write_enabled = True
    settings.lianli_wireless.auto_curve_enabled = True
    settings.lianli_wireless.active_target_mac = device.mac
    settings.lianli_wireless.fan_mode = "quiet"
    settings.lianli_wireless.fan_curve_profiles["quiet"] = [[40, 600], [80, 1800]]
    settings.lianli_wireless.targets[device.mac] = LianLiWirelessTargetSettings(
        mac=device.mac,
        master_mac=device.master_mac,
        channel=device.channel,
        rx_type=device.rx_type,
        device_type=device.device_type,
        fan_count=device.fan_count,
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: backend, settings=settings, background_refresh=False)

    page.update_telemetry(
        SystemTelemetry(
            cpu=CpuTelemetry(package_temperature_c=60.0, available=True),
            gpu=GpuTelemetry(available=False),
            captured_at=datetime(2026, 5, 20, 12, 3, 0),
        )
    )

    assert _process_events_until(app, lambda: bool(backend.sent_pwm))
    assert backend.sent_pwm[0][1] == [170]
    assert page.lianli_rpm_value.value() == 1200
    assert settings.lianli_wireless.fan_mode == "quiet"

    page.close()
    app.quit()


def test_lianli_wireless_page_requires_write_gate_when_configured():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LIANLI_WRITE_CONFIRM_TOKEN, LianLiWirelessPage

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: None, require_write_gate=True)

    page.lianli_mac_input.setText("aa:bb:cc:dd:ee:ff")

    page.lianli_write_enable.setChecked(True)
    page.lianli_confirm_input.setText(LIANLI_WRITE_CONFIRM_TOKEN)

    assert not page.lianli_pwm_button.isEnabled()
    assert "写入门禁：未检查" in page.lianli_write_gate_label.text()
    page.send_live_pwm()
    assert "写入门禁未通过" in page.lianli_status_label.text()

    page.apply_lianli_write_gate(
        {
            "operation": "linux-control-write-gate",
            "status": "needs-packet-compare",
            "allows_any_guarded_write": False,
            "ready_action_count": 0,
            "blocked_action_count": 1,
            "blocked_action_ids": ["safe-experiment:live-pwm"],
            "next_command": "python tools/lianli_wireless_probe.py linux-control-packet-compare",
        }
    )
    assert not page.lianli_pwm_button.isEnabled()
    assert "needs-packet-compare" in page.lianli_write_gate_label.text()

    page.apply_lianli_write_gate(
        {
            "operation": "linux-control-write-gate",
            "status": "write-enabled",
            "allows_any_guarded_write": True,
            "ready_action_count": 1,
            "blocked_action_count": 0,
            "ready_action_ids": ["safe-experiment:live-pwm"],
        }
    )
    assert page.lianli_pwm_button.isEnabled()
    assert "write-enabled" in page.lianli_write_gate_label.text()

    page.close()
    app.quit()


def test_lianli_wireless_write_buttons_require_cached_target():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: None, background_refresh=False)

    page.lianli_write_enable.setChecked(True)

    assert page.lianli_pwm_button.isEnabled() is False
    assert page.lianli_daily_pwm_button.isEnabled() is False
    assert "未识别到风扇组" in page.lianli_write_target_label.text()

    page.close()
    app.quit()


def test_lianli_wireless_page_runs_write_gate_report(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage

    calls: list[tuple[Path, Path]] = []

    def fake_write_gate(path, *, experiment_dir):  # noqa: ANN001
        calls.append((path, experiment_dir))
        return {
            "operation": "linux-control-write-gate",
            "status": "write-enabled",
            "allows_any_guarded_write": True,
            "ready_action_count": 1,
            "blocked_action_count": 0,
        }

    capture_dir = tmp_path / "captures"
    experiment_dir = tmp_path / "experiment"
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(
        backend_factory=lambda: None,
        require_write_gate=True,
        write_gate_capture_dir=capture_dir,
        write_gate_experiment_dir=experiment_dir,
        write_gate_report_factory=fake_write_gate,
    )

    page.run_lianli_write_gate()

    assert _process_events_until(app, lambda: '"operation": "linux-control-write-gate"' in page.lianli_snapshot_text.toPlainText())
    assert calls == [(capture_dir, experiment_dir)]
    assert "write-enabled" in page.lianli_write_gate_label.text()

    page.close()
    app.quit()


def test_lianli_wireless_page_reads_lcd_info():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage

    class FakeLcdBackend:
        def handshake(self):
            return {"mode": 5, "frame_index": 7}

        def firmware_version(self):
            return {"version": "1.2.3", "build": ""}

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(
        backend_factory=lambda: None,
        lcd_backend_factory=lambda: FakeLcdBackend(),
    )

    page.query_live_lcd_info()

    assert _process_events_until(app, lambda: '"operation": "live-lcd-info"' in page.lianli_snapshot_text.toPlainText())
    assert '"version": "1.2.3"' in page.lianli_snapshot_text.toPlainText()

    page.close()
    app.quit()


def test_lianli_wireless_page_runs_readonly_validation(monkeypatch, tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo, WirelessSnapshot

    class FakeLianLiBackend:
        def list_devices(self):
            return WirelessSnapshot(raw=b"snapshot", devices=[device])

        def query_master_mac(self, *, channel):
            return "10:20:30:40:50:60", channel

    class FakeLcdBackend:
        def handshake(self):
            return {"mode": 5, "frame_index": 7}

        def firmware_version(self):
            return {"version": "1.2.3", "build": ""}

    device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    monkeypatch.setattr(pages, "scan_known_usb_devices", lambda: [])

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(
        backend_factory=lambda: FakeLianLiBackend(),
        lcd_backend_factory=lambda: FakeLcdBackend(),
        validation_output_dir=tmp_path,
    )

    page.run_readonly_validation()

    assert _process_events_until(app, lambda: '"operation": "gui-validate-readonly"' in page.lianli_snapshot_text.toPlainText())
    payload = json.loads(page.lianli_snapshot_text.toPlainText())
    assert payload["ok_count"] == 4
    assert payload["error_count"] == 0
    for name in ("scan-usb", "live-list", "live-master", "live-lcd-info"):
        assert (tmp_path / f"{name}.json").exists()
    lcd_payload = json.loads((tmp_path / "live-lcd-info.json").read_text(encoding="utf-8"))
    assert lcd_payload["firmware"]["version"] == "1.2.3"

    page.close()
    app.quit()


def test_lianli_wireless_page_lcd_control_uses_write_gate():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LIANLI_WRITE_CONFIRM_TOKEN, LianLiWirelessPage

    calls: list[tuple[str, int]] = []

    class FakeLcdBackend:
        def set_brightness(self, value):
            calls.append(("brightness", value))
            return 512

        def set_rotation(self, degrees):
            calls.append(("rotation", degrees))
            return 512

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(
        backend_factory=lambda: None,
        lcd_backend_factory=lambda: FakeLcdBackend(),
    )

    assert not page.lianli_lcd_control_button.isEnabled()
    page.send_live_lcd_control()
    assert not calls
    assert "写入未启用" in page.lianli_status_label.text()

    page.lianli_write_enable.setChecked(True)
    page.lianli_confirm_input.setText(LIANLI_WRITE_CONFIRM_TOKEN)
    page.lianli_lcd_brightness.setValue(65)
    page.lianli_lcd_rotation.setCurrentIndex(page.lianli_lcd_rotation.findData(180))
    assert page.lianli_lcd_control_button.isEnabled()

    page.send_live_lcd_control()

    assert _process_events_until(
        app,
        lambda: calls == [("brightness", 65), ("rotation", 180)]
        and '"operation": "live-lcd-control"' in page.lianli_snapshot_text.toPlainText(),
    )
    assert '"operation": "live-lcd-control"' in page.lianli_snapshot_text.toPlainText()
    assert '"bytes_written": 512' in page.lianli_snapshot_text.toPlainText()

    page.close()
    app.quit()


def test_lianli_wireless_page_runs_safe_pwm_experiment(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LIANLI_WRITE_CONFIRM_TOKEN, LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo, WirelessSnapshot

    class FakeLianLiBackend:
        def __init__(self):
            self.list_count = 0
            self.sent_pwm: list[tuple[str, list[int]]] = []

        def list_devices(self):
            self.list_count += 1
            if self.list_count == 1:
                return WirelessSnapshot(raw=b"before", devices=[before_device])
            return WirelessSnapshot(raw=b"after", devices=[after_device])

        def send_pwm(self, target, pwm_values):
            self.sent_pwm.append((target.mac, list(pwm_values)))
            return 4

    before_device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    after_device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(120, 120, 120, 120),
        fan_rpm=(1300, 1500, 0, 0),
        command_sequence=8,
        raw=bytes(42),
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(
        backend_factory=lambda: backend,
        experiment_output_dir=tmp_path,
    )

    assert not page.lianli_safe_pwm_button.isEnabled()
    page.run_safe_pwm_experiment()
    assert not backend.sent_pwm
    assert "写入未启用" in page.lianli_status_label.text()

    page.lianli_write_enable.setChecked(True)
    page.lianli_confirm_input.setText(LIANLI_WRITE_CONFIRM_TOKEN)
    page.lianli_mac_input.setText("aa:bb:cc:dd:ee:ff")
    page.lianli_pwm_value.setValue(120)
    page.run_safe_pwm_experiment()

    assert _process_events_until(
        app,
        lambda: '"operation": "gui-safe-pwm-experiment"' in page.lianli_snapshot_text.toPlainText(),
    )
    assert backend.sent_pwm == [("aa:bb:cc:dd:ee:ff", [120])]
    for name in (
        "live-list-before.json",
        "live-pwm.json",
        "live-list-after.json",
        "analyze-live-pwm.json",
        "summary.json",
    ):
        assert (tmp_path / name).exists()
    payload = json.loads(page.lianli_snapshot_text.toPlainText())
    assert payload["likely_effective"] is True
    assert payload["summary"]["operation_stats"]["live-pwm"]["changed_count"] == 1

    page.close()
    app.quit()


def test_lianli_wireless_page_runs_safe_rgb_experiment(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LIANLI_WRITE_CONFIRM_TOKEN, LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo, WirelessSnapshot

    class FakeLianLiBackend:
        def __init__(self):
            self.sent_rgb: list[tuple[str, tuple[int, int, int]]] = []

        def list_devices(self):
            return WirelessSnapshot(raw=b"snapshot", devices=[device])

        def send_static_rgb(self, target, color):
            self.sent_rgb.append((target.mac, color))
            return 16

    device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(
        backend_factory=lambda: backend,
        rgb_experiment_output_dir=tmp_path,
    )

    assert not page.lianli_safe_rgb_button.isEnabled()
    page.run_safe_rgb_experiment()
    assert not backend.sent_rgb
    assert "写入未启用" in page.lianli_status_label.text()

    page.lianli_write_enable.setChecked(True)
    page.lianli_confirm_input.setText(LIANLI_WRITE_CONFIRM_TOKEN)
    page.lianli_mac_input.setText("aa:bb:cc:dd:ee:ff")
    page.run_safe_rgb_experiment()

    assert _process_events_until(
        app,
        lambda: '"operation": "gui-safe-rgb-experiment"' in page.lianli_snapshot_text.toPlainText(),
    )
    assert backend.sent_rgb == [("aa:bb:cc:dd:ee:ff", (0, 0, 0))]
    for name in (
        "live-list-before.json",
        "live-rgb.json",
        "live-list-after.json",
        "analyze-live-rgb.json",
        "summary.json",
    ):
        assert (tmp_path / name).exists()
    payload = json.loads(page.lianli_snapshot_text.toPlainText())
    assert payload["likely_effective"] is False
    assert payload["visual_confirmation_required"] is True
    assert payload["summary"]["operation_stats"]["live-rgb"]["unchanged_count"] == 1

    page.close()
    app.quit()


def test_lianli_wireless_page_sleep_off_requires_write_unlock(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.lianli_wireless_page as lianli_page_module
    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.gui.settings import LianLiWirelessTargetSettings

    class FakeSender:
        def __init__(self, vid, pid):
            self.vid = vid
            self.pid = pid
            self.closed = False

        def close(self):
            self.closed = True

    class FakeLianLiBackend:
        def __init__(self, *, sender):
            self.sender = sender

        def send_static_rgb(self, target, color, **kwargs):
            sent.append((target.mac, color, kwargs, self.sender.vid, self.sender.pid))
            return 8

    sent = []
    monkeypatch.setattr(lianli_page_module, "PyUsbEndpointTransport", FakeSender)
    monkeypatch.setattr(lianli_page_module, "LianLiWirelessBackend", FakeLianLiBackend)
    monkeypatch.setattr(lianli_page_module, "save_settings", lambda _settings: None)

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: None)
    page.settings.lianli_wireless.active_target_mac = "aa:bb:cc:dd:ee:ff"
    page.settings.lianli_wireless.targets["aa:bb:cc:dd:ee:ff"] = LianLiWirelessTargetSettings(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        led_count=26,
    )
    page.lianli_direct_led_count.setValue(26)
    page._write_unlocked = lambda: False

    page.turn_off_all_lighting()

    _process_events_until(app, lambda: bool(sent), timeout=0.2)
    assert sent == []
    assert "写入未启用" in page.lianli_status_label.text()

    page.close()
    app.quit()


def test_lianli_wireless_page_sleep_off_writes_when_unlocked(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.lianli_wireless_page as lianli_page_module
    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.gui.settings import LianLiWirelessTargetSettings

    class FakeSender:
        def __init__(self, vid, pid):
            self.vid = vid
            self.pid = pid
            self.closed = False

        def close(self):
            self.closed = True

    class FakeLianLiBackend:
        def __init__(self, *, sender):
            self.sender = sender

        def send_static_rgb(self, target, color, **kwargs):
            sent.append((target.mac, color, kwargs, self.sender.vid, self.sender.pid))
            return 8

    sent = []
    monkeypatch.setattr(lianli_page_module, "PyUsbEndpointTransport", FakeSender)
    monkeypatch.setattr(lianli_page_module, "LianLiWirelessBackend", FakeLianLiBackend)
    monkeypatch.setattr(lianli_page_module, "save_settings", lambda _settings: None)

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: None)
    page.settings.lianli_wireless.active_target_mac = "aa:bb:cc:dd:ee:ff"
    page.settings.lianli_wireless.targets["aa:bb:cc:dd:ee:ff"] = LianLiWirelessTargetSettings(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        led_count=26,
    )
    page.lianli_direct_led_count.setValue(26)
    page._write_unlocked = lambda: True

    page.turn_off_all_lighting()

    assert _process_events_until(app, lambda: bool(sent))
    assert sent == [
        (
            "aa:bb:cc:dd:ee:ff",
            (0, 0, 0),
            {"led_count": 26},
            lianli_page_module.RF_SENDER_VID,
            lianli_page_module.RF_SENDER_PID,
        )
    ]
    assert page.settings.lianli_wireless.effect == "off"

    page.close()
    app.quit()


def test_lianli_wireless_page_runs_safe_rainbow_experiment(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LIANLI_WRITE_CONFIRM_TOKEN, LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo, WirelessSnapshot

    class FakeLianLiBackend:
        def __init__(self):
            self.sent_rainbow: list[tuple[str, int, int]] = []

        def list_devices(self):
            return WirelessSnapshot(raw=b"snapshot", devices=[device])

        def send_rainbow_rgb(self, target, *, frame_count, interval_ms):
            self.sent_rainbow.append((target.mac, frame_count, interval_ms))
            return 44

    device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(
        backend_factory=lambda: backend,
        rainbow_experiment_output_dir=tmp_path,
    )

    assert not page.lianli_safe_rainbow_button.isEnabled()
    page.run_safe_rainbow_experiment()
    assert not backend.sent_rainbow
    assert "写入未启用" in page.lianli_status_label.text()

    page.lianli_write_enable.setChecked(True)
    page.lianli_confirm_input.setText(LIANLI_WRITE_CONFIRM_TOKEN)
    page.lianli_mac_input.setText("aa:bb:cc:dd:ee:ff")
    page.lianli_rainbow_frame_count.setValue(3)
    page.lianli_rainbow_interval.setValue(40)
    page.run_safe_rainbow_experiment()

    assert _process_events_until(
        app,
        lambda: '"operation": "gui-safe-rainbow-experiment"' in page.lianli_snapshot_text.toPlainText(),
    )
    assert backend.sent_rainbow == [("aa:bb:cc:dd:ee:ff", 3, 40)]
    for name in (
        "live-list-before.json",
        "live-rainbow.json",
        "live-list-after.json",
        "analyze-live-rainbow.json",
        "summary.json",
    ):
        assert (tmp_path / name).exists()
    payload = json.loads(page.lianli_snapshot_text.toPlainText())
    assert payload["likely_effective"] is False
    assert payload["visual_confirmation_required"] is True
    assert payload["frame_count"] == 3
    assert payload["interval_ms"] == 40
    assert payload["led_count"] == 132
    assert payload["summary"]["operation_stats"]["live-rainbow"]["unchanged_count"] == 1

    page.close()
    app.quit()


def test_lianli_wireless_page_sends_dynamic_effects_as_tlv2_frames(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo

    class FakeLianLiBackend:
        def __init__(self):
            self.static_calls: list[tuple[str, tuple[int, int, int]]] = []
            self.tlv2_calls: list[tuple[str, str, int, int]] = []
            self.tlv2_kwargs: list[dict[str, object]] = []

        def send_static_rgb(self, target, color, **_kwargs):
            self.static_calls.append((target.mac, color))
            return 8

        def send_tlv2_effect(self, target, effect, *, led_count, brightness, **kwargs):
            self.tlv2_calls.append((target.mac, effect, led_count, brightness))
            self.tlv2_kwargs.append(kwargs)
            return 20

    target = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: backend)

    page.lianli_direct_led_count.setValue(26)
    page.lianli_brightness_slider.setValue(80)
    page.set_lianli_static_color("#0000ff")
    packets = page._send_lianli_effect_with_backend(backend, target, "breathing")

    assert packets == 20
    assert backend.static_calls == []
    assert backend.tlv2_calls == [("aa:bb:cc:dd:ee:ff", "breathing", 26, 80)]
    assert backend.tlv2_kwargs[0]["color"] == (0, 0, 255)
    assert backend.tlv2_kwargs[0]["effect_index"] == 0x0209539F

    page.close()
    app.quit()


def _set_lianli_effect(page, effect: str) -> None:
    index = page.lianli_effect_combo.findData(effect)
    if index < 0 and effect == "twinkle":
        index = page.lianli_effect_combo.findData("starry")
    assert index >= 0
    page.lianli_effect_combo.setCurrentIndex(index)
    page._update_lianli_effect_fields()


def test_lianli_wireless_page_uses_effect_capabilities_for_parameter_visibility():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage()
    page.show()
    app.processEvents()

    _set_lianli_effect(page, "breathing")
    assert page.lianli_color_button.isVisible() is True
    assert page.lianli_accent_color_button.isVisible() is False
    assert page.lianli_rotation_colors.isVisible() is False
    assert page.lianli_direction_controls.isVisible() is False

    _set_lianli_effect(page, "twinkle")
    assert page.lianli_color_button.isVisible() is True
    assert page.lianli_accent_color_button.isVisible() is True
    assert page.lianli_rotation_colors.isVisible() is False

    _set_lianli_effect(page, "runway")
    assert page.lianli_color_button.isVisible() is True
    assert page.lianli_accent_color_button.isVisible() is True
    assert page.lianli_rotation_colors.isVisible() is False

    _set_lianli_effect(page, "ripple")
    assert page.lianli_color_button.isVisible() is False
    assert page.lianli_accent_color_button.isVisible() is False
    assert page.lianli_rotation_colors.isVisible() is True
    assert page.lianli_direction_controls.isVisible() is True

    _set_lianli_effect(page, "rainbow")
    assert page.lianli_color_button.isVisible() is False
    assert page.lianli_rotation_colors.isVisible() is False
    assert page.lianli_direction_controls.isVisible() is True

    page.close()
    app.quit()


def test_lianli_wireless_page_sends_palette_and_accent_from_capabilities():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo, tlv2_color_effect_index

    class FakeLianLiBackend:
        def __init__(self):
            self.calls: list[tuple[str, dict[str, object]]] = []

        def send_tlv2_effect(self, target, effect, *, led_count, brightness, **kwargs):
            self.calls.append((effect, kwargs))
            return 20

    target = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: backend)
    page.lianli_direct_led_count.setValue(26)
    page.set_lianli_static_color("#112233")
    page.set_lianli_accent_color("#aabbcc")
    page._set_lianli_rotation_colors(["#010203", "#040506", "#070809"])
    page.lianli_direction_combo.setCurrentIndex(page.lianli_direction_combo.findData("right"))

    page._send_lianli_effect_with_backend(backend, target, "ripple")
    assert backend.calls[-1][0] == "ripple"
    assert backend.calls[-1][1]["palette"] == [(1, 2, 3), (4, 5, 6), (7, 8, 9), (255, 214, 10)]
    assert backend.calls[-1][1]["direction"] == "right"
    assert backend.calls[-1][1]["effect_index"] == tlv2_color_effect_index(
        "ripple",
        (17, 34, 51),
        accent_color=(170, 187, 204),
        palette=[(1, 2, 3), (4, 5, 6), (7, 8, 9), (255, 214, 10)],
        direction="right",
    )

    page._send_lianli_effect_with_backend(backend, target, "starry")
    assert backend.calls[-1][0] == "twinkle"
    assert backend.calls[-1][1]["color"] == (17, 34, 51)
    assert backend.calls[-1][1]["accent_color"] == (170, 187, 204)
    assert backend.calls[-1][1]["effect_index"] == tlv2_color_effect_index(
        "twinkle",
        (17, 34, 51),
        accent_color=(170, 187, 204),
        palette=[(1, 2, 3), (4, 5, 6), (7, 8, 9), (255, 214, 10)],
        direction="right",
    )

    page._send_lianli_effect_with_backend(backend, target, "runway")
    assert backend.calls[-1][0] == "runway"
    assert backend.calls[-1][1]["color"] == (17, 34, 51)
    assert backend.calls[-1][1]["accent_color"] == (170, 187, 204)
    assert backend.calls[-1][1]["effect_index"] == tlv2_color_effect_index(
        "runway",
        (17, 34, 51),
        accent_color=(170, 187, 204),
        palette=[(1, 2, 3), (4, 5, 6), (7, 8, 9), (255, 214, 10)],
        direction="right",
    )
    page._remember_lianli_effect_settings("ripple")
    assert page.settings.lianli_wireless.accent_color == "#aabbcc"
    assert page.settings.lianli_wireless.rotation_colors == "#010203,#040506,#070809,#ffd60a"

    page.close()
    app.quit()


def test_lianli_wireless_page_edits_palette_as_color_swatches():
    from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

    from usb9_lcd.gui.pages import LianLiWirelessPage

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage()

    assert not isinstance(page.lianli_rotation_colors, QLineEdit)
    assert page._rotation_colors()[:4] == ["#fe0000", "#00fe00", "#0000fe", "#ffd60a"]

    page.set_lianli_rotation_color(1, "#123456")
    assert page._rotation_colors()[:4] == ["#fe0000", "#123456", "#0000fe", "#ffd60a"]

    _set_lianli_effect(page, "ripple")
    page._set_lianli_rotation_colors(["#010203", "#040506", "#070809", "#0a0b0c", "#abcdef"])
    assert page._rotation_colors() == ["#010203", "#040506", "#070809", "#0a0b0c"]
    buttons = page.lianli_rotation_colors.findChildren(QPushButton)
    assert len(buttons) == 2
    assert {button.text() for button in buttons} == {""}

    _set_lianli_effect(page, "color-cycle")
    buttons = page.lianli_rotation_colors.findChildren(QPushButton)
    assert len(buttons) == 3

    _set_lianli_effect(page, "meteor-shower")
    buttons = page.lianli_rotation_colors.findChildren(QPushButton)
    assert len(buttons) == 4

    page._remember_lianli_effect_settings("ripple")
    assert page.settings.lianli_wireless.rotation_colors == "#010203,#040506,#070809,#0a0b0c"

    page.close()
    app.quit()


def test_lianli_wireless_page_renders_primary_and_accent_as_swatches():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage()

    page.set_lianli_static_color("#112233")
    page.set_lianli_accent_color("#aabbcc")

    assert "#112233" not in page.lianli_color_button.text()
    assert "#aabbcc" not in page.lianli_accent_color_button.text()
    assert page.lianli_color_button.toolTip().endswith("#112233")
    assert page.lianli_accent_color_button.toolTip().endswith("#aabbcc")
    assert page.lianli_color_button.text() == ""
    assert page.lianli_accent_color_button.text() == ""

    page.close()
    app.quit()


def test_lianli_wireless_page_uses_lconnect_style_direction_buttons():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage()
    page.show()
    app.processEvents()
    _set_lianli_effect(page, "ripple")

    assert page.lianli_direction_controls.isVisible() is True
    assert page.lianli_direction_left_button.text() == "<<<"
    assert page.lianli_direction_right_button.text() == ">>>"

    page._set_lianli_direction("right")
    assert page.lianli_direction_combo.currentData() == "right"
    assert page.lianli_direction_right_button.isChecked() is True
    assert page.lianli_direction_left_button.isChecked() is False

    page._set_lianli_direction("left")
    assert page.lianli_direction_combo.currentData() == "left"
    assert page.lianli_direction_left_button.isChecked() is True
    assert page.lianli_direction_right_button.isChecked() is False

    page.close()
    app.quit()


def test_lianli_wireless_page_sends_remaining_effects_as_tlv2_frames():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo

    class FakeLianLiBackend:
        def __init__(self):
            self.effects: list[str] = []

        def send_tlv2_effect(self, target, effect, *, led_count, brightness, **kwargs):
            self.effects.append(effect)
            return 12

    target = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: backend)

    remaining = [
        "staggered",
        "tide",
        "mixing",
        "voice",
        "door",
        "render",
        "reflect",
        "tail-chasing",
        "paint",
        "ping-pong",
        "stack",
        "cover-cycle",
        "racing",
        "lottery",
        "intertwine",
        "collide",
    ]

    for effect in remaining:
        page._send_lianli_effect_with_backend(backend, target, effect)

    assert backend.effects == remaining

    page.close()
    app.quit()


def test_lianli_wireless_page_apply_rainbow_uses_tlv2_once_path():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage()
    applied: list[str] = []

    index = page.lianli_effect_combo.findData("rainbow")
    assert index >= 0
    page.lianli_effect_combo.setCurrentIndex(index)

    def fail_loop() -> None:
        raise AssertionError("rainbow apply should not start the legacy streaming loop")

    def run_immediately(_message, operation):
        operation()

    page.start_lianli_lighting_loop = fail_loop
    page._run_lianli_operation = run_immediately
    page._send_lianli_lighting_effect = lambda effect: applied.append(effect) or {
        "operation": "gui-lianli-lighting",
        "effect": effect,
    }

    page.apply_lianli_lighting_once()

    assert applied == ["rainbow"]

    page.close()
    app.quit()


def test_lianli_wireless_page_only_exposes_official_lconnect_effects():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage()

    available = [
        (page.lianli_effect_combo.itemText(index), str(page.lianli_effect_combo.itemData(index)))
        for index in range(page.lianli_effect_combo.count())
    ]

    assert available == [
        ("关灯", "off"),
        ("彩虹 (W*)", "rainbow"),
        ("渐变彩虹 (W*)", "gradient-rainbow"),
        ("单色 (W*)", "static"),
        ("呼吸 (W*)", "breathing"),
        ("流星 (W*)", "meteor"),
        ("跑道 (W*)", "runway"),
        ("星空 (W*)", "starry"),
        ("色彩循环 (W*)", "color-cycle"),
        ("覆盖周期 (W*)", "cover-cycle"),
        ("波浪 (W*)", "wave"),
        ("流星雨 (W*)", "meteor-shower"),
        ("迪斯科 (W*)", "disco"),
        ("爆破 (W*)", "blow-up"),
        ("心跳 (W*)", "heartbeat"),
        ("警示 (W*)", "warning"),
        ("海洋 (W*)", "ocean"),
        ("涟漪 (W*)", "ripple"),
        ("回声 (W*)", "echo"),
    ]

    page.close()
    app.quit()


def test_lianli_wireless_page_static_effect_uses_backend_default_color_slot():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo

    class FakeLianLiBackend:
        def __init__(self):
            self.static_kwargs: list[dict[str, object]] = []

        def send_static_rgb(self, target, color, **kwargs):
            self.static_kwargs.append(kwargs)
            return 8

    target = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: backend)

    page.lianli_direct_led_count.setValue(26)
    packets = page._send_lianli_effect_with_backend(backend, target, "static")

    assert packets == 8
    assert backend.static_kwargs == [{"led_count": 26}]

    page.close()
    app.quit()


def test_lianli_wireless_page_uses_cached_physical_layout_for_effects():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LianLiWirelessPage
    from usb9_lcd.gui.settings import LianLiWirelessTargetSettings
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo

    class FakeLianLiBackend:
        def __init__(self):
            self.calls: list[dict[str, object]] = []

        def send_tlv2_effect(self, target, effect, *, led_count, brightness, **kwargs):
            self.calls.append({"effect": effect, "led_count": led_count, "brightness": brightness, **kwargs})
            return 20

    target = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: backend)
    page.settings.lianli_wireless.targets[target.mac] = LianLiWirelessTargetSettings(
        mac=target.mac,
        master_mac=target.master_mac,
        led_count=52,
        direction="right",
        layout_order=2,
        port_label="rear",
    )
    page.settings.lianli_wireless.active_target_mac = target.mac
    page.lianli_direct_led_count.setValue(26)
    page.lianli_direction_combo.setCurrentIndex(page.lianli_direction_combo.findData("left"))

    packets = page._send_lianli_effect_with_backend(backend, target, "runway")

    assert packets == 20
    assert backend.calls[-1]["led_count"] == 52
    assert backend.calls[-1]["direction"] == "right"
    assert page.lianli_direct_led_count.value() == 52
    assert page.lianli_direction_combo.currentData() == "right"

    page.close()
    app.quit()


def test_lianli_wireless_page_runs_safe_sync_experiment(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LIANLI_WRITE_CONFIRM_TOKEN, LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo, WirelessSnapshot

    class FakeLianLiBackend:
        def __init__(self):
            self.list_count = 0
            self.sent_sync: list[tuple[str, bool, int]] = []

        def list_devices(self):
            self.list_count += 1
            if self.list_count == 1:
                return WirelessSnapshot(raw=b"before", devices=[before_device])
            return WirelessSnapshot(raw=b"after", devices=[after_device])

        def send_motherboard_pwm_sync(self, target, *, enable=True, fallback_pwm=100):
            self.sent_sync.append((target.mac, enable, fallback_pwm))
            return 4

    before_device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    after_device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(6, 6, 6, 6),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=8,
        raw=bytes(42),
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(
        backend_factory=lambda: backend,
        sync_experiment_output_dir=tmp_path,
    )

    assert not page.lianli_safe_sync_button.isEnabled()
    page.run_safe_sync_experiment()
    assert not backend.sent_sync
    assert "写入未启用" in page.lianli_status_label.text()

    page.lianli_write_enable.setChecked(True)
    page.lianli_confirm_input.setText(LIANLI_WRITE_CONFIRM_TOKEN)
    page.lianli_mac_input.setText("aa:bb:cc:dd:ee:ff")
    page.run_safe_sync_experiment()

    assert _process_events_until(
        app,
        lambda: '"operation": "gui-safe-sync-experiment"' in page.lianli_snapshot_text.toPlainText(),
    )
    assert backend.sent_sync == [("aa:bb:cc:dd:ee:ff", True, 100)]
    for name in (
        "live-list-before.json",
        "live-pwm-sync.json",
        "live-list-after.json",
        "analyze-live-pwm-sync.json",
        "summary.json",
    ):
        assert (tmp_path / name).exists()
    payload = json.loads(page.lianli_snapshot_text.toPlainText())
    assert payload["expected_pwm_values"] == [6, 6, 6, 6]
    assert payload["likely_effective"] is True
    assert payload["summary"]["operation_stats"]["live-pwm-sync"]["changed_count"] == 1

    page.close()
    app.quit()


def test_lianli_wireless_page_runs_live_pwm_mirror():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LIANLI_WRITE_CONFIRM_TOKEN, LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo, WirelessSnapshot

    class FakeLianLiBackend:
        def __init__(self):
            self.list_count = 0
            self.sent_mirror: list[tuple[str, int]] = []

        def list_devices(self):
            self.list_count += 1
            if self.list_count == 1:
                return WirelessSnapshot(raw=bytes.fromhex("10000a0a"), devices=[before_device])
            return WirelessSnapshot(raw=bytes.fromhex("10000a0a"), devices=[after_device])

        def send_motherboard_pwm_mirror(self, target, motherboard_pwm):
            self.sent_mirror.append((target.mac, motherboard_pwm))
            return 4

    before_device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    after_device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(127, 127, 127, 127),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=8,
        raw=bytes(42),
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: backend)

    assert not page.lianli_pwm_mirror_button.isEnabled()
    page.send_live_pwm_mirror()
    assert not backend.sent_mirror
    assert "写入未启用" in page.lianli_status_label.text()

    page.lianli_write_enable.setChecked(True)
    page.lianli_confirm_input.setText(LIANLI_WRITE_CONFIRM_TOKEN)
    page.lianli_mac_input.setText("aa:bb:cc:dd:ee:ff")
    page.send_live_pwm_mirror()

    assert _process_events_until(
        app,
        lambda: '"operation": "live-pwm-mirror"' in page.lianli_snapshot_text.toPlainText(),
    )
    assert backend.sent_mirror == [("aa:bb:cc:dd:ee:ff", 127)]
    payload = json.loads(page.lianli_snapshot_text.toPlainText())
    assert payload["motherboard_pwm"] == 127
    assert payload["pwm_values"] == [127, 127, 127, 127]

    page.close()
    app.quit()


def test_lianli_wireless_page_runs_safe_pwm_mirror_experiment(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LIANLI_WRITE_CONFIRM_TOKEN, LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo, WirelessSnapshot

    class FakeLianLiBackend:
        def __init__(self):
            self.list_count = 0
            self.sent_mirror: list[tuple[str, int]] = []

        def list_devices(self):
            self.list_count += 1
            if self.list_count == 1:
                return WirelessSnapshot(raw=bytes.fromhex("10000a0a"), devices=[before_device])
            return WirelessSnapshot(raw=bytes.fromhex("10000a0a"), devices=[after_device])

        def send_motherboard_pwm_mirror(self, target, motherboard_pwm):
            self.sent_mirror.append((target.mac, motherboard_pwm))
            return 4

    before_device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    after_device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(127, 127, 127, 127),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=8,
        raw=bytes(42),
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(
        backend_factory=lambda: backend,
        mirror_experiment_output_dir=tmp_path,
    )

    assert not page.lianli_safe_mirror_button.isEnabled()
    page.run_safe_pwm_mirror_experiment()
    assert not backend.sent_mirror
    assert "写入未启用" in page.lianli_status_label.text()

    page.lianli_write_enable.setChecked(True)
    page.lianli_confirm_input.setText(LIANLI_WRITE_CONFIRM_TOKEN)
    page.lianli_mac_input.setText("aa:bb:cc:dd:ee:ff")
    page.run_safe_pwm_mirror_experiment()

    assert _process_events_until(
        app,
        lambda: '"operation": "gui-safe-pwm-mirror-experiment"' in page.lianli_snapshot_text.toPlainText(),
    )
    assert backend.sent_mirror == [("aa:bb:cc:dd:ee:ff", 127)]
    for name in (
        "live-list-before.json",
        "live-pwm-mirror.json",
        "live-list-after.json",
        "analyze-live-pwm-mirror.json",
        "summary.json",
    ):
        assert (tmp_path / name).exists()
    payload = json.loads(page.lianli_snapshot_text.toPlainText())
    assert payload["motherboard_pwm"] == 127
    assert payload["pwm_values"] == [127, 127, 127, 127]
    assert payload["likely_effective"] is True
    assert payload["summary"]["operation_stats"]["live-pwm-mirror"]["changed_count"] == 1

    page.close()
    app.quit()


def test_lianli_wireless_page_runs_safe_bind_experiment(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LIANLI_WRITE_CONFIRM_TOKEN, LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo, WirelessSnapshot

    class FakeLianLiBackend:
        def __init__(self):
            self.list_count = 0
            self.sent_bind: list[tuple[str, str, int, object]] = []

        def list_devices(self):
            self.list_count += 1
            if self.list_count == 1:
                return WirelessSnapshot(raw=b"before", devices=[before_device])
            return WirelessSnapshot(raw=b"after", devices=[after_device])

        def query_master_mac(self, *, channel):
            assert channel == 8
            return "10:20:30:40:50:60", channel

        def send_bind(self, target, *, master_mac, rx_type, channel):
            self.sent_bind.append((target.mac, master_mac, rx_type, channel))
            return 4

    before_device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="00:00:00:00:00:00",
        channel=8,
        rx_type=0,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    after_device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=8,
        raw=bytes(42),
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(
        backend_factory=lambda: backend,
        bind_experiment_output_dir=tmp_path,
    )

    assert not page.lianli_safe_bind_button.isEnabled()
    page.run_safe_bind_experiment()
    assert not backend.sent_bind
    assert "写入未启用" in page.lianli_status_label.text()

    page.lianli_write_enable.setChecked(True)
    page.lianli_confirm_input.setText(LIANLI_WRITE_CONFIRM_TOKEN)
    page.lianli_mac_input.setText("aa:bb:cc:dd:ee:ff")
    page.lianli_rx_type_value.setValue(3)
    page.run_safe_bind_experiment()

    assert _process_events_until(
        app,
        lambda: '"operation": "gui-safe-bind-experiment"' in page.lianli_snapshot_text.toPlainText(),
    )
    assert backend.sent_bind == [("aa:bb:cc:dd:ee:ff", "10:20:30:40:50:60", 3, None)]
    for name in (
        "live-list-before.json",
        "live-bind.json",
        "live-list-after.json",
        "analyze-live-bind.json",
        "summary.json",
    ):
        assert (tmp_path / name).exists()
    payload = json.loads(page.lianli_snapshot_text.toPlainText())
    assert payload["master_mac"] == "10:20:30:40:50:60"
    assert payload["rx_type"] == 3
    assert payload["likely_effective"] is True
    assert payload["summary"]["operation_stats"]["live-bind"]["changed_count"] == 1

    page.close()
    app.quit()


def test_lianli_wireless_page_runs_safe_unbind_experiment(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LIANLI_WRITE_CONFIRM_TOKEN, LianLiWirelessPage
    from usb9_lcd.lianli.wireless import WirelessDeviceInfo, WirelessSnapshot

    class FakeLianLiBackend:
        def __init__(self):
            self.list_count = 0
            self.sent_unbind: list[tuple[str, object]] = []

        def list_devices(self):
            self.list_count += 1
            if self.list_count == 1:
                return WirelessSnapshot(raw=b"before", devices=[before_device])
            return WirelessSnapshot(raw=b"after", devices=[after_device])

        def send_unbind(self, target, *, channel):
            self.sent_unbind.append((target.mac, channel))
            return 4

    before_device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="10:20:30:40:50:60",
        channel=8,
        rx_type=3,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=7,
        raw=bytes(42),
    )
    after_device = WirelessDeviceInfo(
        mac="aa:bb:cc:dd:ee:ff",
        master_mac="00:00:00:00:00:00",
        channel=8,
        rx_type=0,
        device_type=2,
        fan_count=3,
        pwm_values=(80, 90, 100, 110),
        fan_rpm=(1234, 1500, 0, 0),
        command_sequence=8,
        raw=bytes(42),
    )
    backend = FakeLianLiBackend()
    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(
        backend_factory=lambda: backend,
        unbind_experiment_output_dir=tmp_path,
    )

    assert not page.lianli_safe_unbind_button.isEnabled()
    page.run_safe_unbind_experiment()
    assert not backend.sent_unbind
    assert "写入未启用" in page.lianli_status_label.text()

    page.lianli_write_enable.setChecked(True)
    page.lianli_confirm_input.setText(LIANLI_WRITE_CONFIRM_TOKEN)
    page.lianli_mac_input.setText("aa:bb:cc:dd:ee:ff")
    page.run_safe_unbind_experiment()

    assert _process_events_until(
        app,
        lambda: '"operation": "gui-safe-unbind-experiment"' in page.lianli_snapshot_text.toPlainText(),
    )
    assert backend.sent_unbind == [("aa:bb:cc:dd:ee:ff", None)]
    for name in (
        "live-list-before.json",
        "live-unbind.json",
        "live-list-after.json",
        "analyze-live-unbind.json",
        "summary.json",
    ):
        assert (tmp_path / name).exists()
    payload = json.loads(page.lianli_snapshot_text.toPlainText())
    assert payload["likely_effective"] is True
    assert payload["summary"]["operation_stats"]["live-unbind"]["changed_count"] == 1

    page.close()
    app.quit()


def test_lianli_wireless_page_saves_snapshot(monkeypatch, tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LianLiWirelessPage

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: None)
    output_path = tmp_path / "snapshot.json"

    monkeypatch.setattr(
        pages.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(output_path), "JSON (*.json)"),
    )

    page.lianli_snapshot_text.setPlainText('{"operation": "live-list", "device_count": 0}')
    page.save_lianli_snapshot()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload == {"operation": "live-list", "device_count": 0}
    assert "已保存" in page.lianli_status_label.text()

    page.close()
    app.quit()


def test_lianli_wireless_page_analyzes_saved_log(monkeypatch, tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LianLiWirelessPage

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: None)
    log_path = tmp_path / "live-pwm.json"
    log_path.write_text(
        json.dumps(
            {
                "operation": "live-pwm",
                "target": "aa:bb:cc:dd:ee:ff",
                "packets_written": 4,
                "before": _lianli_device_payload(pwm=[80, 90, 100, 110]),
                "after": {
                    "device_count": 1,
                    "devices": [_lianli_device_payload(pwm=[120, 120, 120, 120])],
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        pages.QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(log_path), "JSON (*.json)"),
    )

    page.analyze_lianli_log()

    assert _process_events_until(app, lambda: '"operation": "analyze-log"' in page.lianli_snapshot_text.toPlainText())
    assert '"likely_effective": true' in page.lianli_snapshot_text.toPlainText()

    page.close()
    app.quit()


def test_lianli_wireless_page_diffs_saved_snapshots(monkeypatch, tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LianLiWirelessPage

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: None)
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(
        json.dumps({"operation": "live-list", "devices": [_lianli_device_payload(pwm=[80, 90, 100, 110])]}),
        encoding="utf-8",
    )
    after_path.write_text(
        json.dumps({"operation": "live-list", "devices": [_lianli_device_payload(pwm=[120, 120, 120, 120])]}),
        encoding="utf-8",
    )
    selected = iter((str(before_path), str(after_path)))

    monkeypatch.setattr(
        pages.QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (next(selected), "JSON (*.json)"),
    )

    page.diff_lianli_snapshots()

    assert _process_events_until(app, lambda: '"operation": "diff-snapshots"' in page.lianli_snapshot_text.toPlainText())
    assert '"changed_count": 1' in page.lianli_snapshot_text.toPlainText()

    page.close()
    app.quit()


def test_lianli_wireless_page_summarizes_experiment_directory(monkeypatch, tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LianLiWirelessPage

    pwm_path = tmp_path / "live-pwm.json"
    rgb_path = tmp_path / "live-rgb.json"
    pwm_path.write_text(
        json.dumps(
            {
                "operation": "live-pwm",
                "target": "aa:bb:cc:dd:ee:ff",
                "packets_written": 4,
                "before": _lianli_device_payload(pwm=[80, 90, 100, 110]),
                "after": {
                    "device_count": 1,
                    "devices": [_lianli_device_payload(pwm=[120, 120, 120, 120])],
                },
            }
        ),
        encoding="utf-8",
    )
    rgb_path.write_text(
        json.dumps(
            {
                "operation": "live-rgb",
                "target": "aa:bb:cc:dd:ee:ff",
                "packets_written": 16,
                "before": _lianli_device_payload(),
                "after": {
                    "device_count": 1,
                    "devices": [_lianli_device_payload()],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        pages.QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(tmp_path),
    )

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: None)

    page.summarize_lianli_experiments()

    assert _process_events_until(app, lambda: '"operation": "summarize-experiments"' in page.lianli_snapshot_text.toPlainText())
    payload = json.loads(page.lianli_snapshot_text.toPlainText())
    assert payload["analyzed_live_log_count"] == 2
    assert payload["field_change_counts"] == {"pwm_values": 1}
    assert payload["operation_stats"]["live-pwm"]["changed_count"] == 1
    assert payload["operation_stats"]["live-rgb"]["unchanged_count"] == 1

    page.close()
    app.quit()


def test_lianli_wireless_page_surfaces_receiver_next_action(monkeypatch, tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LianLiWirelessPage

    (tmp_path / "receiver-validation-bundle.json").write_text(
        json.dumps(
            {
                "operation": "receiver-validation-bundle",
                "output_dir": str(tmp_path),
                "capture_dir": str(tmp_path / "captures"),
                "experiment_dir": str(tmp_path / "experiments"),
                "step_count": 7,
                "ok_count": 7,
                "error_count": 0,
                "ready_for_guarded_write": True,
                "write_gate_status": "write-enabled",
                "steps": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "live-list.json").write_text(
        json.dumps(
            {
                "operation": "live-list",
                "device_count": 1,
                "devices": [_lianli_device_payload()],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "live-master.json").write_text(
        json.dumps({"operation": "live-master", "detected": True, "master_mac": "10:20:30:40:50:60"}),
        encoding="utf-8",
    )
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    (readonly_dir / "live-list.json").write_text(
        json.dumps({"operation": "live-list", "device_count": 1, "devices": [_lianli_device_payload()]}),
        encoding="utf-8",
    )
    (readonly_dir / "live-master.json").write_text(
        json.dumps({"operation": "live-master", "detected": True, "master_mac": "10:20:30:40:50:60"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        pages.QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(tmp_path),
    )

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: None)

    page.summarize_lianli_experiments()

    assert _process_events_until(app, lambda: "receiver_control_next_action" in page.lianli_snapshot_text.toPlainText())
    assert "写入门禁已通过" in page.lianli_next_action_label.text()
    assert "aa:bb:cc:dd:ee:ff" in page.lianli_next_action_label.text()
    assert page.lianli_mac_input.text() == "aa:bb:cc:dd:ee:ff"

    page.close()
    app.quit()


def test_lianli_summary_identity_conflict_blocks_auto_mac_fill(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui import pages
    from usb9_lcd.gui.pages import LianLiWirelessPage

    (tmp_path / "receiver-validation-bundle.json").write_text(
        json.dumps(
            {
                "operation": "receiver-validation-bundle",
                "output_dir": str(tmp_path),
                "capture_dir": str(tmp_path / "captures"),
                "experiment_dir": str(tmp_path / "experiments"),
                "step_count": 7,
                "ok_count": 7,
                "error_count": 0,
                "ready_for_guarded_write": True,
                "write_gate_status": "write-enabled",
                "steps": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "live-list.json").write_text(
        json.dumps({"operation": "live-list", "device_count": 1, "devices": [_lianli_device_payload()]}),
        encoding="utf-8",
    )
    (tmp_path / "live-master.json").write_text(
        json.dumps({"operation": "live-master", "detected": True, "master_mac": "10:20:30:40:50:60"}),
        encoding="utf-8",
    )
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    conflict_device = _lianli_device_payload()
    conflict_device["mac"] = "11:22:33:44:55:66"
    (readonly_dir / "live-list.json").write_text(
        json.dumps({"operation": "live-list", "device_count": 1, "devices": [conflict_device]}),
        encoding="utf-8",
    )
    (readonly_dir / "live-master.json").write_text(
        json.dumps({"operation": "live-master", "detected": True, "master_mac": "10:20:30:40:50:60"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        pages.QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(tmp_path),
    )

    app = QApplication.instance() or QApplication([])
    page = LianLiWirelessPage(backend_factory=lambda: None)

    page.summarize_lianli_experiments()

    assert _process_events_until(app, lambda: "receiver-identity-conflict" in page.lianli_snapshot_text.toPlainText())
    assert "身份日志互相矛盾" in page.lianli_next_action_label.text()
    assert page.lianli_mac_input.text() == ""

    page.close()
    app.quit()


def _lianli_device_payload(
    *,
    pwm: list[int] | None = None,
    rpm: list[int] | None = None,
    master_mac: str = "10:20:30:40:50:60",
) -> dict[str, object]:
    return {
        "mac": "aa:bb:cc:dd:ee:ff",
        "master_mac": master_mac,
        "is_bound": master_mac != "00:00:00:00:00:00",
        "channel": 8,
        "rx_type": 3,
        "device_type": 2,
        "fan_count": 3,
        "pwm_values": pwm or [80, 90, 100, 110],
        "fan_rpm": rpm or [1234, 1500, 0, 0],
        "command_sequence": 7,
    }


def test_fan_page_is_lazy_loaded_on_main_window_startup():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    assert window.fan_page.monitor is None
    assert window.fan_page.load_button.isEnabled()

    window.close()
    app.quit()


def test_main_window_keeps_fan_page_lazy_when_auto_refresh_is_enabled():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=True,
    )

    assert window.fan_page.monitor is None
    assert not window.fan_page._loaded

    window.close()
    app.quit()


def test_main_window_does_not_autoconnect_openrgb_on_startup(monkeypatch):
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QApplication, QWidget

    import usb9_lcd.gui.main_window as main_window
    from usb9_lcd.gui.main_window import MainWindow

    seen = {}

    class FakeLightingPage(QWidget):
        status_changed = Signal(str)

        def __init__(self, *, auto_connect, **_kwargs):
            super().__init__()
            seen["auto_connect"] = auto_connect

        def home_status_text(self):
            return "lighting"

        def connect_openrgb(self):
            seen["connect_openrgb_called"] = True

        def release_lighting_resources(self):
            seen["release_lighting_resources"] = True

    monkeypatch.setattr(main_window, "LightingPage", FakeLightingPage)

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=True,
    )

    assert seen["auto_connect"] is False
    assert "connect_openrgb_called" not in seen

    window.close()
    app.quit()


def test_main_window_fan_navigation_loads_readonly_without_driver_probe(monkeypatch):
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QApplication, QWidget

    import usb9_lcd.gui.main_window as main_window
    from usb9_lcd.gui.main_window import MainWindow

    seen = {}

    class FakeFanPage(QWidget):
        status_changed = Signal(str)

        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            super().__init__()
            self.monitor = None

        def home_status_text(self):
            return "未加载"

        def reload_fan_control(self, *, interactive_driver_probe=False):  # noqa: ANN001
            self.load_fan_control(interactive_driver_probe=interactive_driver_probe)

        def load_fan_control(self, *, interactive_driver_probe=False):  # noqa: ANN001
            seen["interactive_driver_probe"] = interactive_driver_probe

        def release(self):
            return

    monkeypatch.setattr(main_window, "FanControlHostPage", FakeFanPage)

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    window.navigation.setCurrentRow(window.page_indexes["fan"])

    assert seen["interactive_driver_probe"] is False

    window.close()
    app.quit()


def test_main_window_opens_platform_diagnostics_window():
    from PySide6.QtWidgets import QApplication, QPushButton

    from usb9_lcd.gui.settings import GuiSettings
    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
        settings=GuiSettings(),
    )

    button = next(button for button in window.findChildren(QPushButton) if button.text() == "平台诊断")
    button.click()

    assert window._platform_diagnostics_dialog is not None
    report = window._platform_diagnostics_dialog.report_text.toPlainText()
    assert "平台诊断" in report
    assert "系统状态" in report
    assert "权限" in report

    window.close()
    app.quit()


def test_main_window_home_fan_shortcut_uses_interactive_driver_probe(monkeypatch):
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget

    import usb9_lcd.gui.main_window as main_window
    from usb9_lcd.gui.main_window import MainWindow

    seen = {}

    class FakeFanPage(QWidget):
        status_changed = Signal(str)

        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            super().__init__()
            self.monitor = None

        def home_status_text(self):
            return "未加载"

        def reload_fan_control(self, *, interactive_driver_probe=False):  # noqa: ANN001
            seen["interactive_driver_probe"] = interactive_driver_probe

        def load_fan_control(self, *, interactive_driver_probe=False):  # noqa: ANN001
            seen["navigation_interactive_driver_probe"] = interactive_driver_probe

        def release(self):
            return

    monkeypatch.setattr(main_window, "FanControlHostPage", FakeFanPage)

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    buttons = window.home_page.findChildren(QPushButton)
    fan_button = next(button for button in buttons if button.property("moduleAction") == "扫描风扇")
    fan_button.click()

    assert seen["interactive_driver_probe"] is True

    window.close()
    app.quit()


def test_fan_page_exposes_control_center_layout_before_loading():
    from PySide6.QtWidgets import QApplication, QTableWidget

    from usb9_lcd.gui.fan_host import FanControlHostPage

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage()

    assert page.control_state_value.text() == "未加载"
    assert page.fan_count_value.text() == "--"
    assert page.sensor_count_value.text() == "--"
    assert page.load_button.text() == "加载/扫描风扇"
    assert page.enable_control_button.text() == "启用 PWM 控制"
    assert not page.enable_control_button.isEnabled()
    assert isinstance(page.fan_table, QTableWidget)
    assert page.fan_table.columnCount() == 8
    assert page.fan_table.horizontalHeaderItem(3).text() == "关联传感器"
    assert [page.workspace_tabs.tabText(index) for index in range(page.workspace_tabs.count())] == [
        "仪表盘",
        "曲线",
        "调速",
        "绑定",
        "策略",
        "标定",
        "权限",
        "压测",
        "历史",
    ]
    assert [page.strategy_tabs.tabText(index) for index in range(page.strategy_tabs.count())] == ["选择策略", "编辑曲线"]
    assert page.workspace_tabs.currentWidget() is page.overview_tab
    assert "长期方案" in page.permission_wizard_text.toPlainText()

    page.close()
    app.quit()


def test_fan_page_updates_summary_strategy_and_channel_table():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    class FakeProfile:
        name = "标准模式"

    class FakeProfileManager:
        def __init__(self):
            self.active = "标准模式"

        def list_names(self):
            return ["静音模式", "标准模式", "全速模式"]

        def get_active(self):
            return FakeProfile()

        def set_active(self, name):
            self.active = name

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage()
    page._profile_manager = FakeProfileManager()
    page._fans = [
        type("Fan", (), {"name": "CPU Fan", "pwm_path": "/sys/class/hwmon/pwm1"})(),
        type("Fan", (), {"name": "GPU Fan", "pwm_path": "nvidia:0"})(),
    ]
    page._sensors = [object(), object(), object()]

    page._refresh_profile_options()
    page._refresh_summary()
    page._refresh_fan_table()
    page._update_fan_rpm("CPU Fan", 1380)
    page._update_fan_pwm("CPU Fan", 192)

    assert page.profile_combo.count() == 3
    assert page.active_profile_value.text() == "标准模式"
    assert page.fan_count_value.text() == "2 通道\nCPU 风扇 1 · GPU 风扇 1"
    assert page.sensor_count_value.text() == "3"
    assert page.fan_table.item(0, 1).text() == "--"
    assert page.fan_table.item(0, 3).text() == "--"
    assert page.fan_table.item(0, 4).text() == "1380 RPM"
    assert page.fan_table.item(0, 5).text() == "75% (192)"
    assert page.fan_table.item(1, 6).text() == "NVIDIA"

    page.close()
    app.quit()


def test_fan_page_apply_profile_repairs_unwritable_active_file(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    class FakeProfile:
        def __init__(self, name):
            self.name = name

    class FakeProfileManager:
        def __init__(self):
            self.config_dir = tmp_path
            self._active_path = tmp_path / ".active"
            self._active_path.write_text("silent", encoding="utf-8")
            self._active_path.chmod(0o444)

        def list_names(self):
            return ["silent", "performance"]

        def get_active(self):
            return FakeProfile(self._active_path.read_text(encoding="utf-8").strip())

        def set_active(self, name):
            self._active_path.write_text(name, encoding="utf-8")

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage()
    page._profile_manager = FakeProfileManager()

    page._refresh_profile_options()
    page.profile_combo.setCurrentText("performance")
    page.apply_selected_profile()

    assert page._profile_manager._active_path.read_text(encoding="utf-8") == "performance"
    assert page.active_profile_value.text() == "performance"

    page.close()
    app.quit()


def test_fan_page_apply_profile_repairs_set_active_permission_error(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    class FakeProfile:
        def __init__(self, name):
            self.name = name

    class FakeProfileManager:
        def __init__(self):
            self.config_dir = tmp_path
            self._active_path = tmp_path / ".active"
            self._active_path.write_text("silent", encoding="utf-8")
            self.set_active_calls = 0

        def list_names(self):
            return ["silent", "performance"]

        def get_active(self):
            return FakeProfile(self._active_path.read_text(encoding="utf-8").strip())

        def set_active(self, name):
            self.set_active_calls += 1
            if self.set_active_calls == 1:
                raise PermissionError("denied")
            self._active_path.write_text(name, encoding="utf-8")

    repaired = {}

    def fake_repair(config_dir, *, interactive=True):  # noqa: ANN001
        repaired["call"] = (config_dir, interactive)
        return True, "ok"

    monkeypatch.setattr(fan_host, "_repair_profile_config_permissions", fake_repair)

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._profile_manager = FakeProfileManager()

    page._refresh_profile_options()
    page.profile_combo.setCurrentText("performance")
    page.apply_selected_profile()

    assert page._profile_manager.set_active_calls == 2
    assert repaired["call"] == (tmp_path, True)
    assert page._profile_manager._active_path.read_text(encoding="utf-8") == "performance"
    assert page.active_profile_value.text() == "performance"
    assert "策略配置权限已修复" in page.status_label.text()

    page.close()
    app.quit()


def test_fan_page_visual_dashboard_updates_cards_and_charts():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage()
    page._fans = [
        type("Fan", (), {"name": "CPU Fan", "pwm_path": "/sys/class/hwmon/pwm1"})(),
        type("Fan", (), {"name": "Case Fan", "pwm_path": "/sys/class/hwmon/pwm2"})(),
    ]
    page._sensors = [
        type("Sensor", (), {"name": "CPU Tctl", "unit": "°C"})(),
        type("Sensor", (), {"name": "GPU 温度", "unit": "°C"})(),
    ]

    page._refresh_summary()
    page._refresh_fan_table()
    page._update_fan_rpm("CPU Fan", 1380)
    page._update_fan_pwm("CPU Fan", 192)
    page._update_fan_rpm("Case Fan", 0)
    page._update_sensor_value("CPU Tctl", 61.5)

    assert "1/2 个通道有转速" in page.visual_status_label.text()
    assert page._fan_cards["CPU Fan"].rpm_value.text() == "1380 RPM"
    assert page._fan_cards["CPU Fan"].pwm_value.text() == "PWM 输出 75% (192)"
    assert page.rpm_chart._series["CPU Fan"][-1] == 1380
    assert "Case Fan" not in page.rpm_chart._series
    assert page.temperature_chart._series["CPU Tctl"][-1] == 61.5

    page.close()
    app.quit()


def test_fan_page_classifies_mainboard_fan_roles_from_hwmon_labels(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6798", encoding="utf-8")
    for index, label in (
        (1, "CPU Fan"),
        (2, "AIO Pump"),
        (3, "Chassis Fan 1"),
    ):
        (hwmon / f"pwm{index}").write_text("128", encoding="utf-8")
        (hwmon / f"fan{index}_input").write_text(str(900 + index), encoding="utf-8")
        (hwmon / f"fan{index}_label").write_text(label, encoding="utf-8")

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    raw_fans = [
        type("Fan", (), {"name": "主板 PWM1", "pwm_path": str(hwmon / "pwm1"), "rpm_input": str(hwmon / "fan1_input")})(),
        type("Fan", (), {"name": "主板 PWM2", "pwm_path": str(hwmon / "pwm2"), "rpm_input": str(hwmon / "fan2_input")})(),
        type("Fan", (), {"name": "主板 PWM3", "pwm_path": str(hwmon / "pwm3"), "rpm_input": str(hwmon / "fan3_input")})(),
    ]

    page._fans = page._display_fans_from_monitor(raw_fans, [])
    page._loaded = True
    page._refresh_summary()
    page._refresh_fan_table()

    names = [page.fan_table.item(row, 0).text() for row in range(page.fan_table.rowCount())]
    assert names == [
        "CPU_FAN · PWM1/FAN1",
        "AIO_PUMP · PWM2/FAN2",
        "CHA_FAN1 · PWM3/FAN3",
    ]
    assert page.fan_table.item(0, 1).text() == "CPU 风扇"
    assert page.fan_table.item(1, 1).text() == "水泵/AIO"
    assert page.fan_table.item(2, 1).text() == "机箱风扇"
    assert page.fan_table.item(0, 2).text() == "CPU_FAN · PWM1/FAN1"
    assert page.fan_table.item(0, 3).text() == "--"
    assert "nct6798" in page.fan_table.item(0, 6).text()
    assert "label: CPU Fan" in page.fan_table.item(0, 0).toolTip()
    assert "AIO Pump" in page.permission_detail_text.toPlainText()

    page.close()
    app.quit()


def test_fan_page_maps_display_fan_names_back_to_backend_channels(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    class FakeSignal:
        def __init__(self):
            self.handlers = []

        def connect(self, handler):
            self.handlers.append(handler)

        def emit(self, *args):
            for handler in list(self.handlers):
                handler(*args)

    class FakeSlider:
        def __init__(self):
            self.pwm_changed = FakeSignal()
            self.auto_toggled = FakeSignal()

    class FakeFanControl:
        def __init__(self, display_name):
            self._sliders = {display_name: FakeSlider()}
            self.control_states = []

        def set_control_enabled(self, enabled):
            self.control_states.append(enabled)

    class FakeMonitor:
        control_enabled = True

        def __init__(self):
            self.control_state_changed = FakeSignal()
            self.manual_calls = []
            self.auto_calls = []

        def set_fan_manual(self, name, pwm):
            self.manual_calls.append((name, pwm))

        def set_fan_auto(self, name):
            self.auto_calls.append(name)

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6798", encoding="utf-8")
    (hwmon / "pwm1").write_text("128", encoding="utf-8")
    (hwmon / "fan1_input").write_text("1200", encoding="utf-8")
    (hwmon / "fan1_label").write_text("CPU Fan", encoding="utf-8")

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    raw_fan = type(
        "Fan",
        (),
        {"name": "主板 PWM1", "pwm_path": str(hwmon / "pwm1"), "rpm_input": str(hwmon / "fan1_input")},
    )()
    page._fans = page._display_fans_from_monitor([raw_fan], [])
    display_name = page._fans[0].name
    page.monitor = FakeMonitor()
    page._refresh_fan_table()

    fan_control = FakeFanControl(display_name)
    page._connect_display_fan_control(fan_control, page.monitor)
    fan_control._sliders[display_name].pwm_changed.emit(display_name, 180)
    fan_control._sliders[display_name].auto_toggled.emit(display_name, True)
    page._update_fan_rpm("主板 PWM1", 1210)
    page._update_fan_pwm("主板 PWM1", 180)

    assert page.monitor.manual_calls == [("主板 PWM1", 180)]
    assert page.monitor.auto_calls == ["主板 PWM1"]
    assert page._latest_rpm[display_name] == 1210
    assert page.fan_table.item(0, 1).text() == "CPU 风扇"
    assert page.fan_table.item(0, 3).text() == "--"
    assert page.fan_table.item(0, 4).text() == "1210 RPM"
    assert page.fan_table.item(0, 5).text() == "71% (180)"

    page.close()
    app.quit()


def test_fan_page_stress_panel_controls_burner():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    class FakeBurner:
        def __init__(self):
            self.started: list[str] = []
            self.stopped: list[str] = []
            self._running: dict[str, bool] = {}

        def check_tools(self):
            return {"cpu": True, "fpu": True, "gpu": False}

        @property
        def running(self):
            return dict(self._running)

        def start(self, kind):
            self.started.append(kind)
            self._running[kind] = True

        def stop(self, kind):
            self.stopped.append(kind)
            self._running[kind] = False

        def stop_all(self):
            for kind in list(self._running):
                self.stop(kind)

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage()
    burner = FakeBurner()

    page.stress_panel.set_burner(burner)
    page.stress_panel._buttons["cpu"].click()

    assert burner.started == ["cpu"]
    assert page.stress_panel._state_labels["cpu"].text() == "运行中"
    assert page.stress_panel.stop_all_button.isEnabled()
    assert not page.stress_panel._buttons["gpu"].isEnabled()

    page.stress_panel.stop_all_button.click()

    assert burner.stopped == ["cpu"]
    assert page.stress_panel._state_labels["cpu"].text() == "未运行"

    page.close()
    app.quit()


def test_embedded_profile_editor_uses_compact_profile_selector():
    from PySide6.QtWidgets import QApplication, QComboBox, QWidget

    from usb9_lcd.gui.fan_host import EmbeddedProfileEditor, FanCurveCanvas

    class FakeCurve:
        def __init__(self, points):
            self.points = list(points)

    class FakeProfile:
        def __init__(self, name):
            self.name = name
            self.curves = {
                "CPU": [(30, 25), (70, 80)],
                "GPU": [(35, 25), (75, 75)],
            }

    class FakeManager:
        def __init__(self):
            self.active = "silent"
            self.profiles = {
                "silent": FakeProfile("silent"),
                "performance": FakeProfile("performance"),
            }
            self.saved = None

        def list_names(self):
            return list(self.profiles)

        def load(self, name):
            return self.profiles.get(name)

        def get_active(self):
            return self.profiles[self.active]

        def save(self, profile):
            self.saved = profile
            self.profiles[profile.name] = profile

        def set_active(self, name):
            self.active = name

    class FakeCurveEditor(QWidget):
        def __init__(self):
            super().__init__()
            self._curve = FakeCurve([])

        def set_curve(self, curve):
            self._curve = curve

        def get_curve(self):
            return self._curve

    app = QApplication.instance() or QApplication([])
    manager = FakeManager()
    editor = EmbeddedProfileEditor(manager, FakeCurveEditor, FakeCurve, FakeProfile)

    assert isinstance(editor._profile_combo, QComboBox)
    assert isinstance(editor._cpu_editor, FanCurveCanvas)
    assert editor._cpu_editor.objectName() == "FanCurveCanvas"
    assert editor._profile_combo.currentText() == "performance"
    editor._profile_combo.setCurrentText("silent")
    assert editor._profile_state_label.text() == "当前启用：silent"

    editor._profile_combo.setCurrentText("performance")
    assert "当前编辑：performance" in editor._profile_state_label.text()
    editor._cpu_editor.set_curve(FakeCurve([(40, 30), (80, 90)]))
    editor._save_profile()

    assert manager.saved.name == "performance"
    assert manager.saved.curves["CPU"] == [(40, 30), (80, 90)]

    for index in range(1, 6):
        editor.update_fan_pwm(f"主板 PWM{index}", 128)
        editor.update_fan_rpm(f"主板 PWM{index}", 900 + index)

    assert "另 2 路" in editor._fan_rpm_label.text()
    assert "PWM5/FAN5" in editor._fan_rpm_label.toolTip()

    editor.close()
    app.quit()


def test_embedded_profile_editor_repairs_unwritable_active_profile(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

    from usb9_lcd.gui.fan_host import EmbeddedProfileEditor

    class FakeCurve:
        def __init__(self, points):
            self.points = list(points)

    class FakeProfile:
        def __init__(self, name):
            self.name = name
            self.curves = {
                "CPU": [(30, 25), (70, 80)],
                "GPU": [(35, 25), (75, 75)],
            }

    class FakeManager:
        def __init__(self):
            self.config_dir = tmp_path
            self._active_path = tmp_path / ".active"
            self.profiles = {
                "silent": FakeProfile("silent"),
                "performance": FakeProfile("performance"),
            }

        def _path(self, name):
            return self.config_dir / f"{name}.json"

        def list_names(self):
            return list(self.profiles)

        def load(self, name):
            return self.profiles.get(name)

        def get_active(self):
            return self.profiles.get(self._active_path.read_text(encoding="utf-8").strip())

        def save(self, profile):
            self.profiles[profile.name] = profile

        def set_active(self, name):
            self._active_path.write_text(name, encoding="utf-8")

    class FakeCurveEditor(QWidget):
        def __init__(self):
            super().__init__()
            self._curve = FakeCurve([])

        def set_curve(self, curve):
            self._curve = curve

        def get_curve(self):
            return self._curve

    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    app = QApplication.instance() or QApplication([])
    manager = FakeManager()
    manager._active_path.write_text("silent", encoding="utf-8")
    manager._active_path.chmod(0o444)
    editor = EmbeddedProfileEditor(manager, FakeCurveEditor, FakeCurve, FakeProfile)

    editor._profile_combo.setCurrentText("performance")
    editor._activate_profile()

    assert manager._active_path.read_text(encoding="utf-8") == "performance"
    assert editor._profile_state_label.text() == "当前启用：performance"

    editor.close()
    app.quit()


def test_embedded_profile_editor_repairs_set_active_permission_error(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import EmbeddedProfileEditor

    class FakeCurve:
        def __init__(self, points):
            self.points = list(points)

    class FakeProfile:
        def __init__(self, name):
            self.name = name
            self.curves = {
                "CPU": [(30, 25), (70, 80)],
                "GPU": [(35, 25), (75, 75)],
            }

    class FakeManager:
        def __init__(self):
            self.config_dir = tmp_path
            self._active_path = tmp_path / ".active"
            self.profiles = {
                "silent": FakeProfile("silent"),
                "performance": FakeProfile("performance"),
            }
            self.set_active_calls = 0

        def _path(self, name):
            return self.config_dir / f"{name}.json"

        def list_names(self):
            return list(self.profiles)

        def load(self, name):
            return self.profiles.get(name)

        def get_active(self):
            if not self._active_path.exists():
                return None
            return self.profiles.get(self._active_path.read_text(encoding="utf-8").strip())

        def save(self, profile):
            self.profiles[profile.name] = profile

        def set_active(self, name):
            self.set_active_calls += 1
            if self.set_active_calls == 1:
                raise PermissionError("denied")
            self._active_path.write_text(name, encoding="utf-8")

    class FakeCurveEditor(QWidget):
        def __init__(self):
            super().__init__()
            self._curve = FakeCurve([])

        def set_curve(self, curve):
            self._curve = curve

        def get_curve(self):
            return self._curve

    repaired = {}

    def fake_repair(config_dir, *, interactive=True):  # noqa: ANN001
        repaired["call"] = (config_dir, interactive)
        return True, "ok"

    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(fan_host, "_repair_profile_config_permissions", fake_repair)

    app = QApplication.instance() or QApplication([])
    manager = FakeManager()
    editor = EmbeddedProfileEditor(manager, FakeCurveEditor, FakeCurve, FakeProfile)

    editor._profile_combo.setCurrentText("performance")
    editor._activate_profile()

    assert manager.set_active_calls == 2
    assert repaired["call"] == (tmp_path, True)
    assert manager._active_path.read_text(encoding="utf-8") == "performance"
    assert editor._profile_state_label.text() == "当前启用：performance"

    editor.close()
    app.quit()


def test_embedded_profile_editor_stops_before_unwritable_profile_dir(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

    from usb9_lcd.gui.fan_host import EmbeddedProfileEditor

    class FakeCurve:
        def __init__(self, points):
            self.points = list(points)

    class FakeProfile:
        def __init__(self, name):
            self.name = name
            self.curves = {
                "CPU": [(30, 25), (70, 80)],
                "GPU": [(35, 25), (75, 75)],
            }

    class FakeManager:
        def __init__(self):
            self.config_dir = tmp_path
            self._active_path = tmp_path / ".active"
            self.set_active_called = False
            self.profiles = {
                "silent": FakeProfile("silent"),
                "performance": FakeProfile("performance"),
            }

        def _path(self, name):
            return self.config_dir / f"{name}.json"

        def list_names(self):
            return list(self.profiles)

        def load(self, name):
            return self.profiles.get(name)

        def get_active(self):
            return self.profiles["silent"]

        def save(self, profile):
            self.profiles[profile.name] = profile

        def set_active(self, name):
            self.set_active_called = True

    class FakeCurveEditor(QWidget):
        def __init__(self):
            super().__init__()
            self._curve = FakeCurve([])

        def set_curve(self, curve):
            self._curve = curve

        def get_curve(self):
            return self._curve

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args))

    app = QApplication.instance() or QApplication([])
    manager = FakeManager()
    editor = EmbeddedProfileEditor(manager, FakeCurveEditor, FakeCurve, FakeProfile)

    tmp_path.chmod(0o555)
    if os.access(tmp_path, os.W_OK):
        tmp_path.chmod(0o755)
        pytest.skip("当前用户仍可写 chmod 0555 目录，无法模拟普通用户权限不足")
    try:
        editor._profile_combo.setCurrentText("performance")
        editor._activate_profile()
    finally:
        tmp_path.chmod(0o755)

    assert not manager.set_active_called
    assert warnings
    assert "策略配置文件权限不足" in editor._profile_state_label.text()

    editor.close()
    app.quit()


def test_profile_permission_repair_command_restores_owner_and_user_write(monkeypatch, tmp_path: Path):
    import usb9_lcd.gui.fan_host as fan_host

    monkeypatch.setattr(fan_host.os, "getuid", lambda: 1234)
    monkeypatch.setattr(fan_host.os, "getgid", lambda: 5678)
    monkeypatch.delenv("SUDO_UID", raising=False)
    monkeypatch.delenv("SUDO_GID", raising=False)

    command = fan_host._profile_config_repair_commands(tmp_path)

    assert f"chown -R 1234:5678 -- {tmp_path}" in command
    assert f"find {tmp_path} -type d -exec chmod u+rwx {{}} +" in command
    assert f"find {tmp_path} -type f -exec chmod u+rw {{}} +" in command


def test_fan_page_permission_panel_generates_temp_fix_commands(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    existing_paths = {"/tmp/hwmon/pwm1", "/tmp/hwmon/pwm1_enable"}
    monkeypatch.setattr(fan_host.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(fan_host.os.path, "exists", lambda path: str(path) in existing_paths)
    monkeypatch.setattr(fan_host.os, "access", lambda path, mode: False)

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage()
    page._fans = [
        type("Fan", (), {"name": "CPU Fan", "pwm_path": "/tmp/hwmon/pwm1"})(),
        type("Fan", (), {"name": "GPU Fan", "pwm_path": "nvidia:0:pwm"})(),
    ]

    page._refresh_summary()

    assert page.permission_value.text() == "需 sudo/udev"
    assert "CPU Fan" in page.permission_detail_text.toPlainText()
    assert "pwm1_enable" in page.permission_detail_text.toPlainText()
    assert page.copy_permission_commands_button.isEnabled()
    commands = page._permission_fix_commands()
    assert 'sudo chgrp "$(id -gn)" /tmp/hwmon/pwm1 /tmp/hwmon/pwm1_enable' in commands
    assert "sudo chmod g+rw /tmp/hwmon/pwm1 /tmp/hwmon/pwm1_enable" in commands

    page.close()
    app.quit()


def test_fan_page_blocks_pwm_enable_when_system_auth_fails(monkeypatch):
    from PySide6.QtWidgets import QApplication, QWidget

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    monkeypatch.setattr(fan_host.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(fan_host.os.path, "exists", lambda path: str(path) == "/tmp/hwmon/pwm1")
    monkeypatch.setattr(fan_host.os, "access", lambda path, mode: False)
    seen = {}

    class FakeMonitor:
        def __init__(self):
            self.control_enabled = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.control_enabled = control_enabled

        def set_control_enabled(self, enabled):  # noqa: ANN001
            self.control_enabled = enabled

    class FakeFanPage(FanControlHostPage):
        def _system_has_fan_pwm_files(self):
            return False

        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = FakeMonitor()
            self.monitor.start(control_enabled=False)
            self._fans = [type("Fan", (), {"name": "CPU Fan", "pwm_path": "/tmp/hwmon/pwm1"})()]
            self._tabs = QWidget()
            self._refresh_summary()

        def _run_permission_grant(self, commands, *, interactive=True):  # noqa: ANN001
            seen["commands"] = commands
            seen["interactive"] = interactive
            return False, "cancelled"

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage(
        auto_grant_pwm_permissions=False,
        auto_probe_hwmon_drivers=False,
        auto_enable_pwm_control=False,
    )

    page.load_fan_control()
    page.enable_control_button.click()

    assert page.monitor.control_enabled is False
    assert not page.enable_control_button.isChecked()
    assert seen["interactive"] is True
    assert "chmod g+rw /tmp/hwmon/pwm1" in seen["commands"]
    assert "PWM 系统授权失败" in page.status_label.text()

    page.close()
    app.quit()


def test_fan_page_enable_pwm_requests_system_auth_and_continues(monkeypatch):
    from PySide6.QtWidgets import QApplication, QWidget

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    writable = {"value": False}
    seen = {}
    monkeypatch.setattr(fan_host.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(fan_host.os.path, "exists", lambda path: str(path) == "/tmp/hwmon/pwm1")
    monkeypatch.setattr(fan_host.os, "access", lambda path, mode: writable["value"])

    class FakeMonitor:
        def __init__(self):
            self.control_enabled = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.control_enabled = control_enabled

        def set_control_enabled(self, enabled):  # noqa: ANN001
            self.control_enabled = enabled

    class FakeFanPage(FanControlHostPage):
        def _system_has_fan_pwm_files(self):
            return False

        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = FakeMonitor()
            self.monitor.start(control_enabled=False)
            self._fans = [type("Fan", (), {"name": "CPU Fan", "pwm_path": "/tmp/hwmon/pwm1"})()]
            self._tabs = QWidget()
            self._refresh_summary()

        def _run_permission_grant(self, commands, *, interactive=True):  # noqa: ANN001
            seen["commands"] = commands
            seen["interactive"] = interactive
            writable["value"] = True
            return True, "ok"

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage(
        auto_grant_pwm_permissions=False,
        auto_probe_hwmon_drivers=False,
        auto_enable_pwm_control=False,
    )

    page.load_fan_control()
    page.enable_control_button.click()

    assert seen["interactive"] is True
    assert "chmod g+rw /tmp/hwmon/pwm1" in seen["commands"]
    assert page.monitor.control_enabled is True
    assert page.enable_control_button.isChecked()
    assert page.permission_value.text() == "PWM 可写"
    assert "PWM 控制已启用" in page.status_label.text()

    page.close()
    app.quit()


def test_fan_page_auto_grants_pwm_permissions_on_load(monkeypatch):
    from PySide6.QtWidgets import QApplication, QWidget

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    seen = {}
    writable = {"value": False}
    monkeypatch.setattr(fan_host.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(fan_host.os.path, "exists", lambda path: str(path) == "/tmp/hwmon/pwm1")
    monkeypatch.setattr(fan_host.os, "access", lambda path, mode: writable["value"])

    class FakeMonitor:
        control_enabled = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.control_enabled = control_enabled

    class FakeFanPage(FanControlHostPage):
        def _system_has_fan_pwm_files(self):
            return False

        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = FakeMonitor()
            self.monitor.start(control_enabled=False)
            self._fans = [type("Fan", (), {"name": "CPU Fan", "pwm_path": "/tmp/hwmon/pwm1"})()]
            self._tabs = QWidget()
            self._refresh_summary()

        def _run_permission_grant(self, commands, *, interactive=True):  # noqa: ANN001
            assert "chmod g+rw /tmp/hwmon/pwm1" in commands
            assert "sudo" not in commands
            seen["interactive"] = interactive
            writable["value"] = True
            return True, "ok"

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage(
        auto_grant_pwm_permissions=True,
        auto_probe_hwmon_drivers=False,
        auto_enable_pwm_control=False,
    )

    page.load_fan_control()

    assert page.permission_value.text() == "PWM 可写"
    assert seen["interactive"] is False
    assert "PWM 权限已授权" in page.status_label.text()
    assert not page.copy_permission_commands_button.isEnabled()

    page.close()
    app.quit()


def test_fan_page_does_not_auto_grant_pwm_permissions_by_default(monkeypatch):
    from PySide6.QtWidgets import QApplication, QWidget

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    grant_calls = []
    monkeypatch.setattr(fan_host.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(fan_host.os.path, "exists", lambda path: str(path) == "/tmp/hwmon/pwm1")
    monkeypatch.setattr(fan_host.os, "access", lambda path, mode: False)

    class FakeMonitor:
        control_enabled = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.control_enabled = control_enabled

    class FakeFanPage(FanControlHostPage):
        def _system_has_fan_pwm_files(self):
            return False

        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = FakeMonitor()
            self.monitor.start(control_enabled=False)
            self._fans = [type("Fan", (), {"name": "CPU Fan", "pwm_path": "/tmp/hwmon/pwm1"})()]
            self._tabs = QWidget()
            self._refresh_summary()

        def _run_permission_grant(self, commands, *, interactive=True):  # noqa: ANN001
            grant_calls.append((commands, interactive))
            return True, "ok"

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage(auto_probe_hwmon_drivers=False, auto_enable_pwm_control=False)

    page.load_fan_control()

    assert page.permission_value.text() == "需 sudo/udev"
    assert grant_calls == []
    assert "只读模式" in page.status_label.text()

    page.close()
    app.quit()


def test_fan_page_manual_permission_request_uses_system_auth(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    seen = {}
    writable = {"value": False}
    monkeypatch.setattr(fan_host.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(fan_host.os.path, "exists", lambda path: str(path) == "/tmp/hwmon/pwm1")
    monkeypatch.setattr(fan_host.os, "access", lambda path, mode: writable["value"])

    class FakeFanPage(FanControlHostPage):
        def _run_permission_grant(self, commands, *, interactive=True):  # noqa: ANN001
            seen["commands"] = commands
            seen["interactive"] = interactive
            writable["value"] = True
            return True, "ok"

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fans = [type("Fan", (), {"name": "CPU Fan", "pwm_path": "/tmp/hwmon/pwm1"})()]
    page._update_permission_summary()

    page.grant_pwm_permissions()

    assert seen["interactive"] is True
    assert "chmod g+rw /tmp/hwmon/pwm1" in seen["commands"]
    assert page.permission_value.text() == "PWM 可写"
    assert "PWM 权限已授权" in page.status_label.text()

    page.close()
    app.quit()


def test_fan_page_interactive_privileged_shell_uses_pkexec(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    captured = {}
    monkeypatch.setattr(fan_host.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        fan_host.shutil,
        "which",
        lambda name: (
            "/usr/bin/pkexec" if name == "pkexec" else "/usr/bin/sudo" if name == "sudo" else None
        ),
    )

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):  # noqa: ANN001
        captured["command"] = command
        captured["timeout"] = kwargs["timeout"]
        return FakeResult()

    monkeypatch.setattr(fan_host.subprocess, "run", fake_run)

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_load=False, auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)

    ok, message = page._run_privileged_shell("id", timeout=120, interactive=True)

    assert ok is True
    assert message == "ok"
    assert captured["command"] == ["/usr/bin/pkexec", "/bin/sh", "-c", "id"]
    assert captured["timeout"] == 120

    page.close()
    app.quit()


def test_fan_page_load_failure_releases_partial_resources():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    class BrokenFanPage(FanControlHostPage):
        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = type("FakeMonitor", (), {"stopped": False, "stop": lambda item: setattr(item, "stopped", True)})()
            raise RuntimeError("boom")

    app = QApplication.instance() or QApplication([])
    page = BrokenFanPage()

    page.load_fan_control()

    assert "加载失败" in page.status_label.text()
    assert page.monitor is None
    assert page.load_button.isEnabled()

    page.close()
    app.quit()


def test_fan_page_loads_read_only_then_enables_pwm_control(monkeypatch):
    from PySide6.QtWidgets import QApplication, QWidget

    from usb9_lcd.gui.fan_host import FanControlHostPage

    class FakeMonitor:
        def __init__(self):
            self.started_with = None
            self.control_enabled = False
            self.stopped = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.started_with = control_enabled
            self.control_enabled = control_enabled

        def set_control_enabled(self, enabled):  # noqa: ANN001
            self.control_enabled = enabled

        def stop(self):
            self.stopped = True

    class FakeFanPage(FanControlHostPage):
        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = FakeMonitor()
            self.monitor.start(control_enabled=False)
            self._fans = [type("Fan", (), {"name": "GPU Fan", "pwm_path": "nvidia:0:pwm"})()]
            self._tabs = QWidget()

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage(auto_probe_hwmon_drivers=False, auto_enable_pwm_control=False)

    page.load_fan_control()

    assert page.monitor.started_with is False
    assert page.monitor.control_enabled is False
    assert page.enable_control_button.isEnabled()
    assert "只读模式" in page.status_label.text()

    page.enable_control_button.click()

    assert page.monitor.control_enabled is True
    assert page.enable_control_button.isEnabled()
    assert "PWM 控制已启用" in page.status_label.text()

    page.close()
    app.quit()


def test_fan_page_reports_missing_hwmon_fan_channels(monkeypatch):
    from PySide6.QtWidgets import QApplication, QWidget

    from usb9_lcd.gui.fan_host import FanControlHostPage

    class FakeMonitor:
        control_enabled = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.control_enabled = control_enabled

        def stop(self):
            return

    class FakeFanPage(FanControlHostPage):
        def _system_has_fan_pwm_files(self):
            return False

        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = FakeMonitor()
            self.monitor.start(control_enabled=False)
            self._sensors = [object(), object()]
            self._fans = []
            self._tabs = QWidget()
            self._refresh_summary()
            self._refresh_fan_table()

        def _fan_discovery_diagnostics(self) -> str:
            return "旧风扇控制后端已运行，但当前 /sys/class/hwmon 没有 fan*_input 或 pwm* 文件。"

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage(auto_probe_hwmon_drivers=False)

    page.load_fan_control()

    assert page.control_state_value.text() == "未发现风扇"
    assert page.enable_control_button.isEnabled()
    assert page.enable_control_button.text() == "授权加载主板 PWM"
    assert page.load_button.isEnabled()
    assert page.load_button.text() == "授权扫描主板风扇"
    assert page.driver_probe_button.isEnabled()
    assert "没有发现 fan/pwm 通道" in page.status_label.text()
    assert "没有 fan*_input 或 pwm*" in page.fan_table_hint.text()
    assert "没有 fan*_input 或 pwm*" in page.permission_detail_text.toPlainText()

    page.close()
    app.quit()


def test_fan_page_reload_recovers_after_missing_hwmon_channels():
    from PySide6.QtWidgets import QApplication, QWidget

    from usb9_lcd.gui.fan_host import FanControlHostPage

    class FakeMonitor:
        control_enabled = False

        def __init__(self):
            self.stopped = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.control_enabled = control_enabled

        def stop(self):
            self.stopped = True

    class FakeFanPage(FanControlHostPage):
        def __init__(self):
            self.build_count = 0
            self.stopped_monitors = []
            super().__init__(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)

        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.build_count += 1
            monitor = FakeMonitor()
            monitor.start(control_enabled=False)
            self.monitor = monitor
            self._sensors = []
            self._fans = (
                []
                if self.build_count == 1
                else [type("Fan", (), {"name": "主板 PWM1", "pwm_path": "/tmp/hwmon/pwm1"})()]
            )
            self._tabs = QWidget()
            self._refresh_summary()
            self._refresh_fan_table()

        def release(self):
            monitor = self.monitor
            super().release()
            if monitor is not None:
                self.stopped_monitors.append(monitor)

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage()

    page.load_fan_control()
    assert page.fan_count_value.text() == "0 通道"
    assert page.load_button.text() == "重新扫描风扇"

    page.reload_fan_control()

    assert page.build_count == 2
    assert page.stopped_monitors and page.stopped_monitors[0].stopped is True
    assert page.fan_count_value.text() == "1 通道\n未识别 1"
    assert page.load_button.text() == "重新扫描风扇"
    assert page.load_button.isEnabled()

    page.close()
    app.quit()


def test_fan_page_uses_nvidia_smi_readonly_fallback(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    monkeypatch.setattr(fan_host.shutil, "which", lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)
    monkeypatch.setattr(FanControlHostPage, "_system_has_fan_pwm_files", lambda self: False)

    class FakeResult:
        returncode = 0
        stdout = "0, NVIDIA GeForce RTX 5080, 35, 0\n"
        stderr = ""

    monkeypatch.setattr(fan_host.subprocess, "run", lambda *args, **kwargs: FakeResult())

    class FakeMonitor:
        control_enabled = False

        def stop(self):
            return

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page.monitor = FakeMonitor()
    page._loaded = True

    page._start_nvidia_smi_fallback_if_needed()
    page._refresh_summary()
    page._refresh_fan_table()

    assert page.fan_count_value.text() == "1 通道\nGPU 风扇 1"
    assert page.fan_table.item(0, 1).text() == "GPU 风扇"
    assert page.fan_table.item(0, 3).text() == "--"
    assert page.fan_table.item(0, 4).text() == "0%"
    assert page.fan_table.item(0, 6).text() == "NVIDIA 只读"
    assert page.fan_table.item(0, 7).text() == "已连接，只读"
    assert page.temperature_chart._series["GPU0 温度"][-1] == 35
    assert page.enable_control_button.isEnabled()
    assert page.enable_control_button.text() == "授权加载主板 PWM"

    page.release()
    page.close()
    app.quit()


def test_fan_page_distinguishes_readonly_fallback_from_mainboard_pwm_loading():
    from PySide6.QtWidgets import QApplication, QWidget

    from usb9_lcd.gui.fan_host import FanControlHostPage, ReadOnlyFanChannel

    class FakeMonitor:
        control_enabled = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.control_enabled = control_enabled

        def stop(self):
            return

    class FakeFanPage(FanControlHostPage):
        def _system_has_fan_pwm_files(self):
            return False

        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = FakeMonitor()
            self.monitor.start(control_enabled=False)
            self._driver_probe_message = "自动加载已跳过：当前会话没有免密 sudo"
            self._fans = [
                ReadOnlyFanChannel(
                    name="GPU0 风扇",
                    pwm_path="readonly:nvidia-smi:0:fan",
                    rpm_input="nvidia-smi:0:fan",
                    rpm_unit="%",
                )
            ]
            self._tabs = QWidget()
            self._refresh_summary()
            self._refresh_fan_table()

        def _fan_discovery_diagnostics(self) -> str:
            return "主板风扇诊断：没有 fan*_input 或 pwm* 文件。"

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)

    page.load_fan_control()
    page._update_fan_rpm("GPU0 风扇", 42)

    assert "主板 fan/pwm 没有暴露" in page.status_label.text()
    assert page.home_status_text() == "主板未暴露\n1/1 只读通道"
    assert page.load_button.text() == "授权扫描主板风扇"
    assert page.load_button.isEnabled()
    assert page.enable_control_button.isEnabled()
    assert page.enable_control_button.text() == "授权加载主板 PWM"
    assert "主板 PWM 文件：未暴露" in page.permission_wizard_text.toPlainText()
    assert "主板风扇诊断" in page.permission_detail_text.toPlainText()

    page.close()
    app.quit()


def test_fan_page_pwm_action_requests_driver_probe_when_mainboard_hidden():
    from PySide6.QtWidgets import QApplication, QWidget

    from usb9_lcd.gui.fan_host import FanControlHostPage, ReadOnlyFanChannel

    class FakeMonitor:
        control_enabled = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.control_enabled = control_enabled

        def set_control_enabled(self, enabled):  # noqa: ANN001
            self.control_enabled = enabled

        def stop(self):
            return

    class FakeFanPage(FanControlHostPage):
        def __init__(self):
            self.driver_loaded = False
            self.probe_interactive = None
            super().__init__(
                auto_grant_pwm_permissions=False,
                auto_probe_hwmon_drivers=False,
                auto_enable_pwm_control=False,
            )

        def _system_has_fan_pwm_files(self):
            return self.driver_loaded

        def _load_fan_hwmon_drivers(self, *, interactive=False):  # noqa: ANN001
            self.probe_interactive = interactive
            self.driver_loaded = True
            return True, "ok"

        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = FakeMonitor()
            self.monitor.start(control_enabled=False)
            self._fans = (
                [type("Fan", (), {"name": "主板 PWM1", "pwm_path": "/tmp/hwmon/pwm1"})()]
                if self.driver_loaded
                else [
                    ReadOnlyFanChannel(
                        name="GPU0 风扇",
                        pwm_path="readonly:nvidia-smi:0:fan",
                        rpm_input="nvidia-smi:0:fan",
                        rpm_unit="%",
                    )
                ]
            )
            self._tabs = QWidget()
            self._refresh_summary()
            self._refresh_fan_table()

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage()

    page.load_fan_control()
    assert page.enable_control_button.text() == "授权加载主板 PWM"

    page.enable_control_button.click()

    assert page.probe_interactive is True
    assert page.fan_count_value.text() == "1 通道\n未识别 1"
    assert page.fan_table.item(0, 0).text() == "主板 PWM1"
    assert page.enable_control_button.text() == "启用 PWM 控制"
    assert page.enable_control_button.isEnabled()

    page.close()
    app.quit()


def test_fan_page_load_button_requests_driver_probe_when_mainboard_hidden():
    from PySide6.QtWidgets import QApplication, QWidget

    from usb9_lcd.gui.fan_host import FanControlHostPage, ReadOnlyFanChannel

    class FakeMonitor:
        control_enabled = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.control_enabled = control_enabled

        def stop(self):
            return

    class FakeFanPage(FanControlHostPage):
        def __init__(self):
            self.driver_loaded = False
            self.probe_interactive = None
            super().__init__(
                auto_grant_pwm_permissions=False,
                auto_probe_hwmon_drivers=False,
                auto_enable_pwm_control=False,
            )

        def _system_has_fan_pwm_files(self):
            return self.driver_loaded

        def _load_fan_hwmon_drivers(self, *, interactive=False):  # noqa: ANN001
            self.probe_interactive = interactive
            self.driver_loaded = True
            return True, "ok"

        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = FakeMonitor()
            self.monitor.start(control_enabled=False)
            self._fans = (
                [type("Fan", (), {"name": "主板 PWM1", "pwm_path": "/tmp/hwmon/pwm1"})()]
                if self.driver_loaded
                else [
                    ReadOnlyFanChannel(
                        name="GPU0 风扇",
                        pwm_path="readonly:nvidia-smi:0:fan",
                        rpm_input="nvidia-smi:0:fan",
                        rpm_unit="%",
                    )
                ]
            )
            self._tabs = QWidget()
            self._refresh_summary()
            self._refresh_fan_table()

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage()

    page.load_fan_control()
    assert page.load_button.text() == "授权扫描主板风扇"

    page.load_button.click()

    assert page.probe_interactive is True
    assert page.fan_count_value.text() == "1 通道\n未识别 1"
    assert page.fan_table.item(0, 0).text() == "主板 PWM1"
    assert page.load_button.text() == "重新扫描风扇"

    page.close()
    app.quit()


def test_fan_page_loads_readonly_rpm_sensors_as_channels():
    from PySide6.QtWidgets import QApplication, QWidget

    from usb9_lcd.gui.fan_host import FanControlHostPage

    class FakeMonitor:
        control_enabled = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.control_enabled = control_enabled

        def stop(self):
            return

    class FakeFanPage(FanControlHostPage):
        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = FakeMonitor()
            self.monitor.start(control_enabled=False)
            self._sensors = [
                type(
                    "Sensor",
                    (),
                    {
                        "name": "主板 风扇#1",
                        "unit": "RPM",
                        "internal_id": "/sys/class/hwmon/hwmon0/fan1_input",
                    },
                )(),
                type(
                    "Sensor",
                    (),
                    {
                        "name": "CPU Tctl",
                        "unit": "°C",
                        "internal_id": "/sys/class/hwmon/hwmon1/temp1_input",
                    },
                )(),
            ]
            self._fans = self._display_fans_from_monitor([], self._sensors)
            self._tabs = QWidget()
            self._refresh_summary()
            self._refresh_fan_table()

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage(auto_probe_hwmon_drivers=False)

    page.load_fan_control()
    page._update_sensor_value("主板 风扇#1", 1234)

    assert page.fan_count_value.text() == "1 通道\n未识别 1"
    assert page.fan_table.item(0, 0).text() == "主板 风扇#1 · FAN1"
    assert page.fan_table.item(0, 1).text() == "未识别风扇"
    assert page.fan_table.item(0, 3).text() == "--"
    assert page.fan_table.item(0, 4).text() == "1234 RPM"
    assert page.fan_table.item(0, 5).text() == "只读转速"
    assert page.fan_table.item(0, 6).text() == "RPM 只读"
    assert not page.enable_control_button.isEnabled()
    assert "只有只读风扇通道" in page.status_label.text()
    assert "1/1 个通道有转速" in page.visual_status_label.text()

    page.close()
    app.quit()


def test_fan_page_probes_hwmon_driver_before_loading(monkeypatch):
    from PySide6.QtWidgets import QApplication, QWidget

    from usb9_lcd.gui.fan_host import FanControlHostPage

    class FakeMonitor:
        control_enabled = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.control_enabled = control_enabled

    class FakeFanPage(FanControlHostPage):
        def __init__(self):
            self.check_count = 0
            self.probe_commands = ""
            super().__init__(auto_grant_pwm_permissions=False, auto_enable_pwm_control=False)

        def _system_has_fan_pwm_files(self):
            self.check_count += 1
            return self.check_count > 1

        def _run_privileged_shell(self, commands, *, timeout, interactive=False):  # noqa: ANN001
            self.probe_commands = commands
            self.probe_interactive = interactive
            return True, "ok"

        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = FakeMonitor()
            self.monitor.start(control_enabled=False)
            self._fans = [type("Fan", (), {"name": "主板 PWM1", "pwm_path": "/tmp/hwmon/pwm1"})()]
            self._tabs = QWidget()
            self._refresh_summary()

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage()

    page.load_fan_control(interactive_driver_probe=True)

    assert "nct6775" in page.probe_commands
    assert page.probe_interactive is True
    assert page._driver_probe_message == "ok"
    assert page.fan_count_value.text() == "1 通道\n未识别 1"

    page.close()
    app.quit()


def test_fan_page_interactive_driver_probe_adds_nct6683_force_fallback():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)

    interactive_shell = page._fan_hwmon_probe_shell(include_forced_probes=True)
    automatic_shell = page._fan_hwmon_probe_shell(include_forced_probes=False)

    assert "nct6683 force=1" in interactive_shell
    assert "新主板 NCT6683/NCT6687" in interactive_shell
    assert "nct6683 force=1" not in automatic_shell

    page.close()
    app.quit()


def test_fan_page_auto_load_uses_noninteractive_driver_probe():
    from PySide6.QtWidgets import QApplication, QWidget

    from usb9_lcd.gui.fan_host import FanControlHostPage

    seen: dict[str, object] = {}

    class FakeMonitor:
        control_enabled = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.control_enabled = control_enabled

        def stop(self):
            return

    class FakeFanPage(FanControlHostPage):
        def _system_has_fan_pwm_files(self):
            return False

        def _noninteractive_driver_probe_ready(self):
            return True, "sudo 免密可用"

        def _run_privileged_shell(self, commands, *, timeout, interactive=False):  # noqa: ANN001
            seen["commands"] = commands
            seen["interactive"] = interactive
            return True, "nct6683=已加载"

        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = FakeMonitor()
            self.monitor.start(control_enabled=False)
            self._fans = []
            self._tabs = QWidget()
            self._refresh_summary()

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage(auto_grant_pwm_permissions=False, auto_enable_pwm_control=False, auto_load=True)

    assert _process_events_until(app, lambda: page.monitor is not None)
    assert seen["interactive"] is False
    assert "nct6683" in seen["commands"]
    assert "仍未暴露 fan/pwm" in page._driver_probe_message

    page.close()
    app.quit()


def test_fan_page_auto_load_skips_driver_probe_without_passwordless_sudo():
    from PySide6.QtWidgets import QApplication, QWidget

    from usb9_lcd.gui.fan_host import FanControlHostPage

    seen = {"probe_called": False}

    class FakeMonitor:
        control_enabled = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.control_enabled = control_enabled

        def stop(self):
            return

    class FakeFanPage(FanControlHostPage):
        def _system_has_fan_pwm_files(self):
            return False

        def _noninteractive_driver_probe_ready(self):
            return False, "自动加载已跳过：当前会话没有免密 sudo；请点击“授权加载主板驱动”触发系统授权。"

        def _load_fan_hwmon_drivers(self, *, interactive=False):  # noqa: ANN001
            seen["probe_called"] = True
            return True, "unexpected"

        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = FakeMonitor()
            self.monitor.start(control_enabled=False)
            self._sensors = []
            self._fans = []
            self._tabs = QWidget()
            self._refresh_summary()
            self._refresh_fan_table()

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage(auto_grant_pwm_permissions=False, auto_enable_pwm_control=False)

    page.load_fan_control(interactive_driver_probe=False)

    assert seen["probe_called"] is False
    assert "自动加载已跳过" in page._driver_probe_message
    assert "授权加载主板驱动" in page.status_label.text()
    assert page.home_status_text() == "需要驱动授权"

    page.close()
    app.quit()


def test_fan_page_manual_hwmon_driver_probe_uses_system_auth_and_reloads():
    from PySide6.QtWidgets import QApplication, QWidget

    from usb9_lcd.gui.fan_host import FanControlHostPage

    seen = {}

    class FakeMonitor:
        control_enabled = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.control_enabled = control_enabled

        def stop(self):
            return

    class FakeFanPage(FanControlHostPage):
        def __init__(self):
            self.driver_loaded = False
            super().__init__(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=True, auto_enable_pwm_control=False)

        def _load_fan_hwmon_drivers(self, *, interactive=False):  # noqa: ANN001
            seen["interactive"] = interactive
            self.driver_loaded = True
            return True, "ok"

        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = FakeMonitor()
            self.monitor.start(control_enabled=False)
            self._sensors = []
            self._fans = (
                [type("Fan", (), {"name": "主板 PWM1", "pwm_path": "/tmp/hwmon/pwm1"})()]
                if self.driver_loaded
                else []
            )
            self._tabs = QWidget()
            self._refresh_summary()
            self._refresh_fan_table()

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage()

    page.request_hwmon_driver_probe()

    assert seen["interactive"] is True
    assert page.fan_count_value.text() == "1 通道\n未识别 1"
    assert page.load_button.text() == "重新扫描风扇"
    assert page.load_button.isEnabled()

    page.close()
    app.quit()


def test_fan_page_auto_loads_and_enables_pwm_control():
    from PySide6.QtWidgets import QApplication, QWidget

    from usb9_lcd.gui.fan_host import FanControlHostPage

    class FakeMonitor:
        def __init__(self):
            self.started_with = None
            self.control_enabled = False

        def start(self, control_enabled=True):  # noqa: ANN001
            self.started_with = control_enabled
            self.control_enabled = control_enabled

        def set_control_enabled(self, enabled):  # noqa: ANN001
            self.control_enabled = enabled

        def stop(self):
            return

    class FakeFanPage(FanControlHostPage):
        def _import_modules(self):  # noqa: ANN001
            return {}

        def _build_embedded_ui(self, modules):  # noqa: ANN001
            self.monitor = FakeMonitor()
            self.monitor.start(control_enabled=False)
            self._fans = [type("Fan", (), {"name": "GPU Fan", "pwm_path": "nvidia:0:pwm"})()]
            self._tabs = QWidget()

    app = QApplication.instance() or QApplication([])
    page = FakeFanPage(auto_probe_hwmon_drivers=False, auto_load=True)

    assert _process_events_until(app, lambda: page.monitor is not None and page.monitor.control_enabled)
    assert page.control_state_value.text() == "PWM 已启用"
    assert page.enable_control_button.text() == "暂停 PWM 控制"

    page.close()
    app.quit()


def test_lighting_page_connects_to_openrgb_and_applies_settings():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LightingPage

    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    page = LightingPage(controller=controller)

    page.connect_openrgb()
    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    page.lighting_target_combo.setCurrentIndex(1)
    page.apply_all_lighting_checkbox.setChecked(False)
    page.set_selected_color("#00e5ff")
    page.brightness_slider.setValue(60)
    page.speed_slider.setValue(7)
    for button in page.effect_group.buttons():
        if button.text() == "呼吸":
            button.setChecked(True)
            break

    page.apply_lighting()

    assert _process_events_until(app, lambda: page.openrgb_status_label.text() == "灯效已应用")
    assert page.lighting_target_combo.count() == 2
    assert "ARGB Header" in page.lighting_target_combo.currentText()
    assert "Direct" in page.openrgb_modes_text.toPlainText()
    assert len(controller.applied) == 1
    settings = controller.applied[0]
    assert settings.target_id == "device:0:zone:1"
    assert settings.effect == "breathing"
    assert settings.color == "#00e5ff"
    assert settings.brightness_percent == 60
    assert settings.speed_percent == 70
    assert page.openrgb_status_label.text() == "灯效已应用"

    page.close()
    app.quit()


def test_lighting_page_apply_saves_on_qt_thread(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LightingPage
    from usb9_lcd.gui.settings import GuiSettings

    saved_threads: list[str] = []
    monkeypatch.setattr(pages, "save_settings", lambda _settings: saved_threads.append(threading.current_thread().name))
    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    page = LightingPage(controller=controller, settings=GuiSettings())

    page.connect_openrgb()
    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    saved_threads.clear()
    page.lighting_target_combo.setCurrentIndex(1)
    page.apply_all_lighting_checkbox.setChecked(False)
    page.set_selected_color("#00e5ff")
    page.brightness_slider.setValue(60)
    for button in page.effect_group.buttons():
        if button.text() == "静态":
            button.setChecked(True)
            break

    page.apply_lighting()

    assert _process_events_until(app, lambda: page.openrgb_status_label.text() == "灯效已应用")
    assert saved_threads
    assert "usb9-lcd-openrgb" not in saved_threads

    page.close()
    app.quit()


def test_lighting_page_apply_saves_snapshot_from_before_worker_start(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LightingPage
    from usb9_lcd.gui.settings import GuiSettings

    class BlockingApplyController(FakeLightingController):
        def __init__(self) -> None:
            super().__init__()
            self.apply_started = threading.Event()
            self.release_apply = threading.Event()

        def apply(self, settings):  # noqa: ANN001
            self.apply_started.set()
            self.release_apply.wait(timeout=1)
            super().apply(settings)

    monkeypatch.setattr(pages, "save_settings", lambda _settings: None)
    app = QApplication.instance() or QApplication([])
    controller = BlockingApplyController()
    settings = GuiSettings()
    page = LightingPage(controller=controller, settings=settings)

    page.connect_openrgb()
    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    page.lighting_target_combo.setCurrentIndex(1)
    page.apply_all_lighting_checkbox.setChecked(False)
    page.set_selected_color("#111111")
    page.brightness_slider.setValue(60)
    for button in page.effect_group.buttons():
        if button.text() == "静态":
            button.setChecked(True)
            break

    page.apply_lighting()
    assert controller.apply_started.wait(timeout=1)
    page.set_selected_color("#222222")
    controller.release_apply.set()

    assert _process_events_until(app, lambda: page.openrgb_status_label.text() == "灯效已应用")
    assert controller.applied[-1].color == "#111111"
    assert settings.lighting.target_profiles["device:0:zone:1"]["color"] == "#111111"

    page.close()
    app.quit()


def test_lighting_page_reports_target_partitions_after_connect():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LightingPage
    from usb9_lcd.gui.settings import GuiSettings

    app = QApplication.instance() or QApplication([])
    controller = MultiDeviceLightingController()
    page = LightingPage(controller=controller, settings=GuiSettings())

    page.connect_openrgb()

    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 4)
    report = page.openrgb_modes_text.toPlainText()
    preview = page.lighting_apply_preview_text.toPlainText()
    assert "分区统计" in report
    assert "设备 2 个" in report
    assert "全设备目标 2 个" in report
    assert "区域 2 个" in report
    assert "G.Skill Memory" in report
    assert "应用预览" in preview
    assert "目标：2 个" in preview

    page.close()
    app.quit()


def test_lighting_page_updates_apply_preview_from_controls():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LightingPage

    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    page = LightingPage(controller=controller)

    page.connect_openrgb()
    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    page.lighting_target_combo.setCurrentIndex(1)
    page.apply_all_lighting_checkbox.setChecked(False)
    page.set_selected_color("#00e5ff")
    page.brightness_slider.setValue(60)
    page.speed_slider.setValue(7)
    for button in page.effect_group.buttons():
        if button.text() == "静态":
            button.click()
            break

    preview = page.lighting_apply_preview_text.toPlainText()

    assert "只写入当前目标" in preview
    assert "ARGB Header" in preview
    assert "静态" in preview
    assert "#00e5ff" in preview
    assert "亮度 60%" in preview
    assert "速度 70%" in preview

    page.close()
    app.quit()


def test_lighting_page_shows_saved_feedback_after_target_profile(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LightingPage
    from usb9_lcd.gui.settings import GuiSettings

    saved = []
    monkeypatch.setattr(pages, "save_settings", lambda settings: saved.append(settings))
    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    page = LightingPage(controller=controller, settings=GuiSettings())

    page.connect_openrgb()
    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    page.save_current_target_profile()

    assert saved
    assert "区域配置已保存" in page.lighting_save_status_label.text()
    assert page.openrgb_status_label.text() == "区域配置已保存"

    page.close()
    app.quit()


def test_lighting_page_saves_physical_layout_for_current_target(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LightingPage
    from usb9_lcd.gui.settings import GuiSettings

    saved = []
    monkeypatch.setattr(pages, "save_settings", lambda settings: saved.append(settings))
    app = QApplication.instance() or QApplication([])
    settings = GuiSettings()
    page = LightingPage(controller=FakeLightingController(), settings=settings)

    page.connect_openrgb()
    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    page.lighting_target_combo.setCurrentIndex(1)
    page.layout_order_spin.setValue(2)
    page.layout_led_count_spin.setValue(26)
    page.layout_direction_combo.setCurrentIndex(page.layout_direction_combo.findData("reverse"))
    page.layout_port_input.setText("top")

    page.save_lighting_layout()

    assert saved
    assert settings.lighting.physical_layout["device:0:zone:1"] == {
        "order": 2,
        "led_count": 26,
        "direction": "reverse",
        "port_label": "top",
    }
    settings_for_apply = page._current_lighting_settings("device:0:zone:1")
    assert settings_for_apply.physical_layout is not None
    assert settings_for_apply.physical_layout.targets[0].target_id == "device:0:zone:1"
    assert "物理布局已保存" in page.lighting_save_status_label.text()

    page.close()
    app.quit()


def test_lighting_defaults_to_off():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LightingPage
    from usb9_lcd.gui.settings import GuiSettings

    app = QApplication.instance() or QApplication([])
    page = LightingPage(controller=FakeLightingController(), settings=GuiSettings())

    assert page._selected_effect() == "off"
    assert page.selected_color_input.text() == "#000000"
    assert page.brightness_slider.value() == 0
    assert page.brightness_value_label.text() == "0%"
    assert page.speed_value_label.text() == "50%"

    page.close()
    app.quit()


def test_lighting_page_exposes_expanded_openrgb_effects():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LightingPage

    app = QApplication.instance() or QApplication([])
    page = LightingPage(controller=FakeLightingController())
    labels = {button.text() for button in page.effect_group.buttons()}

    assert {"波浪", "颜色循环", "颜色脉冲", "闪烁", "星空", "流星", "彗星", "扫描", "遮罩", "矩阵", "渐变"} <= labels
    assert page.effect_map["波浪"] == "wave"
    assert page.effect_map["颜色脉冲"] == "color_pulse"

    page.close()
    app.quit()


def test_lighting_starts_off_even_with_saved_lighting_state(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LightingPage
    from usb9_lcd.gui.settings import GuiSettings

    monkeypatch.setattr(pages, "save_settings", lambda settings: None)
    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    settings = GuiSettings()
    settings.lighting.target_id = "device:0:zone:1"
    settings.lighting.effect = "static"
    settings.lighting.color = "#ffffff"
    settings.lighting.brightness_percent = 90
    settings.lighting.sync_mode = 2
    settings.lighting.target_profiles["device:0:zone:1"] = {
        "effect": "static",
        "color": "#ffffff",
        "brightness_percent": 80,
        "speed": 6,
        "sync_mode": 2,
        "argb_zone_size": 30,
    }

    page = LightingPage(controller=controller, settings=settings)

    assert page._selected_effect() == "off"
    assert page.selected_color_input.text() == "#000000"
    assert page.brightness_slider.value() == 0
    assert page.sync_mode_combo.currentIndex() == 0

    page.connect_openrgb()
    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    assert page.lighting_target_combo.currentData() == "device:0:zone:1"
    assert page._selected_effect() == "off"
    assert page.brightness_slider.value() == 0

    page.load_current_target_profile()

    assert page._selected_effect() == "static"
    assert page.selected_color_input.text() == "#ffffff"
    assert page.brightness_slider.value() == 80
    assert page.sync_mode_combo.currentIndex() == 2

    page.close()
    app.quit()


def test_lighting_page_structure_separates_common_scene_and_sync_controls():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LightingPage

    app = QApplication.instance() or QApplication([])
    page = LightingPage(controller=FakeLightingController())

    assert page.lighting_workspace_tabs.count() == 4
    assert [page.lighting_workspace_tabs.tabText(index) for index in range(4)] == [
        "快速应用",
        "区域",
        "场景",
        "联动",
    ]
    assert page.lighting_target_combo.parentWidget().objectName() == "LightingTargetPanel"
    assert page.scene_combo.parentWidget().objectName() == "LightingScenePanel"
    assert page.sync_mode_combo.parentWidget().objectName() == "LightingSyncPanel"

    page.close()
    app.quit()


def test_lighting_connect_does_not_restore_previous_effect(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LightingPage
    from usb9_lcd.gui.settings import GuiSettings

    monkeypatch.setattr(pages, "save_settings", lambda settings: None)
    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    settings = GuiSettings()
    settings.lighting.target_id = "device:0:zone:1"
    settings.lighting.target_profiles["device:0:zone:1"] = {
        "effect": "static",
        "color": "#ffffff",
        "brightness_percent": 80,
        "speed": 5,
        "argb_zone_size": 30,
    }
    page = LightingPage(controller=controller, settings=settings)

    page.connect_openrgb()

    assert _process_events_until(app, lambda: page.openrgb_status_label.text().startswith("已连接"))
    assert controller.applied == []
    assert "默认保持关闭" in page.openrgb_status_label.text()
    assert page._selected_effect() == "off"
    assert page.brightness_slider.value() == 0

    page.close()
    app.quit()


def test_lighting_page_palette_updates_swatches_and_saved_profile(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LIGHTING_PALETTES, LightingPage
    from usb9_lcd.gui.settings import GuiSettings

    monkeypatch.setattr(pages, "save_settings", lambda settings: None)
    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    settings = GuiSettings()
    page = LightingPage(controller=controller, settings=settings)

    page.connect_openrgb()
    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    page.lighting_target_combo.setCurrentIndex(1)
    page.lighting_palette_combo.setCurrentIndex(page.lighting_palette_combo.findData("cool"))
    page.lighting_swatch_buttons[0].click()
    page.save_current_target_profile()

    assert page.selected_color_input.text() == LIGHTING_PALETTES["cool"][1][0]
    assert page.selected_color_preview.toolTip() == LIGHTING_PALETTES["cool"][1][0]
    assert settings.lighting.palette == "cool"
    assert settings.lighting.target_profiles["device:0:zone:1"]["palette"] == "cool"

    page.close()
    app.quit()


def test_lighting_sync_tab_applies_current_sync_settings(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LightingPage

    monkeypatch.setattr(pages, "save_settings", lambda settings: None)
    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    page = LightingPage(
        controller=controller,
        sync_color_provider=lambda mode: "#ff2d55" if mode == 4 else None,
    )

    page.connect_openrgb()
    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    page.lighting_target_combo.setCurrentIndex(1)
    page.apply_all_lighting_checkbox.setChecked(False)
    page.sync_mode_combo.setCurrentIndex(4)
    page.brightness_slider.setValue(70)
    page.apply_sync_lighting_button.click()

    assert _process_events_until(app, lambda: page.openrgb_status_label.text() == "灯效已应用")
    assert controller.applied[-1].target_id == "device:0:zone:1"
    assert controller.applied[-1].color == "#ff2d55"
    assert controller.applied[-1].brightness_percent == 70

    page.close()
    app.quit()


def test_lighting_page_custom_color_picker_updates_selected_color(monkeypatch):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LightingPage

    monkeypatch.setattr(pages.QColorDialog, "getColor", lambda initial, parent, title: QColor("#123abc"))
    app = QApplication.instance() or QApplication([])
    page = LightingPage(controller=FakeLightingController())

    page.choose_lighting_color()

    assert page.selected_color_input.text() == "#123abc"

    page.close()
    app.quit()


def test_lighting_page_auto_connects_on_startup():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LightingPage

    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    page = LightingPage(controller=controller, auto_connect=True)

    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    assert page.lighting_target_combo.count() == 2
    assert "已连接" in page.openrgb_status_label.text()

    page.close()
    app.quit()


def test_lighting_page_apply_auto_connects_when_needed():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LightingPage

    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    page = LightingPage(controller=controller)
    page.set_selected_color("#00e5ff")

    page.apply_lighting()

    assert _process_events_until(app, lambda: "已连接并应用灯效" in page.openrgb_status_label.text())
    assert controller.connected is True
    assert page.lighting_target_combo.count() == 2
    assert controller.applied[0].target_id == "device:0"
    assert "已连接并应用灯效" in page.openrgb_status_label.text()

    page.close()
    app.quit()


def test_lighting_page_prefers_argb_zone_after_connect():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LightingPage

    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    page = LightingPage(controller=controller)

    page.connect_openrgb()

    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    assert page.lighting_target_combo.currentData() == "device:0:zone:1"
    assert "ARGB Header" in page.lighting_target_combo.currentText()

    page.close()
    app.quit()


def test_lighting_page_visible_effect_auto_uses_visible_argb_output():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LightingPage

    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    page = LightingPage(controller=controller)
    for button in page.effect_group.buttons():
        if button.text() == "静态":
            button.setChecked(True)
            break

    page.apply_lighting()

    assert _process_events_until(app, lambda: "已连接并应用灯效" in page.openrgb_status_label.text())
    assert controller.applied[0].target_id == "device:0"
    assert controller.applied[0].effect == "static"
    assert controller.applied[0].color == "#ffffff"
    assert controller.applied[0].brightness_percent == 80

    page.close()
    app.quit()


def test_lighting_page_static_default_syncs_all_openrgb_devices(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LightingPage
    from usb9_lcd.gui.settings import GuiSettings

    monkeypatch.setattr(pages, "save_settings", lambda settings: None)
    app = QApplication.instance() or QApplication([])
    controller = MultiDeviceLightingController()
    settings = GuiSettings()
    page = LightingPage(controller=controller, settings=settings)
    for button in page.effect_group.buttons():
        if button.text() == "静态":
            button.setChecked(True)
            break
    page.set_selected_color("#00e5ff")
    page.brightness_slider.setValue(60)

    page.apply_lighting()

    assert _process_events_until(app, lambda: "2 个目标" in page.openrgb_status_label.text())
    assert [item.target_id for item in controller.applied] == ["device:0", "device:1"]
    assert all(item.color == "#00e5ff" for item in controller.applied)
    assert all(item.brightness_percent == 60 for item in controller.applied)
    assert {"device:0", "device:1"} <= set(settings.lighting.target_profiles)

    page.close()
    app.quit()


def test_lighting_page_openrgb_test_window_runs_static_probe(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LightingPage
    from usb9_lcd.gui.settings import GuiSettings

    monkeypatch.setattr(pages, "save_settings", lambda settings: None)
    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    page = LightingPage(controller=controller, settings=GuiSettings())

    page.connect_openrgb()
    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    page.open_openrgb_test_window()
    dialog = page._openrgb_test_dialog

    assert dialog is not None
    assert dialog.target_combo.count() == 2

    dialog.static_red_button.click()

    assert _process_events_until(app, lambda: "OpenRGB 测试完成" in page.openrgb_status_label.text())
    assert controller.applied[-1].target_id == "device:0"
    assert controller.applied[-1].effect == "static"
    assert controller.applied[-1].color == "#ff0000"

    dialog.close()
    page.close()
    app.quit()


def test_lighting_page_queues_openrgb_operations(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LightingPage
    from usb9_lcd.gui.settings import GuiSettings

    monkeypatch.setattr(pages, "save_settings", lambda settings: None)
    app = QApplication.instance() or QApplication([])
    page = LightingPage(controller=FakeLightingController(), settings=GuiSettings())
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def first_operation() -> str:
        started.set()
        release.wait(timeout=1)
        calls.append("first")
        return "first done"

    def second_operation() -> str:
        calls.append("second")
        return "second done"

    page._run_lighting_operation("first busy", "first_failed", first_operation)
    assert started.wait(timeout=1)

    page._run_lighting_operation("second busy", "second_failed", second_operation)
    assert "1 个等待" in page.openrgb_status_label.text()

    release.set()

    assert _process_events_until(
        app,
        lambda: calls == ["first", "second"] and page.openrgb_status_label.text() == "second done",
        timeout=2.0,
    )

    page.close()
    app.quit()


def test_lighting_page_reconnects_when_selected_target_is_disconnected():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import LightingPage

    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    page = LightingPage(controller=controller)

    page.connect_openrgb()
    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    controller.connected = False
    page.apply_all_lighting_checkbox.setChecked(False)
    page.set_selected_color("#00e5ff")
    page.brightness_slider.setValue(60)
    for button in page.effect_group.buttons():
        if button.text() == "静态":
            button.setChecked(True)
            break

    page.apply_lighting()

    assert _process_events_until(app, lambda: "已连接并应用灯效" in page.openrgb_status_label.text())
    assert controller.connected is True
    assert controller.applied[0].target_id == "device:0:zone:1"
    assert controller.applied[0].color == "#00e5ff"
    assert controller.applied[0].brightness_percent == 60

    page.close()
    app.quit()


def test_lighting_page_saves_and_applies_multi_target_scene(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LightingPage
    from usb9_lcd.gui.settings import GuiSettings

    monkeypatch.setattr(pages, "save_settings", lambda settings: None)
    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    settings = GuiSettings()
    page = LightingPage(controller=controller, settings=settings)

    page.connect_openrgb()
    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    page.lighting_target_combo.setCurrentIndex(1)
    page.set_selected_color("#00e5ff")
    page.brightness_slider.setValue(60)
    page.speed_slider.setValue(7)
    page.save_current_target_profile()
    page.scene_combo.setCurrentText("ARGB 场景")
    page.save_lighting_scene()

    controller.applied.clear()
    page.apply_saved_lighting_scene()

    assert _process_events_until(app, lambda: page.openrgb_status_label.text().startswith("场景已应用"))
    assert settings.lighting.active_scene == "ARGB 场景"
    assert len(controller.applied) == 1
    assert controller.applied[0].target_id == "device:0:zone:1"
    assert controller.applied[0].color == "#00e5ff"
    assert controller.applied[0].speed_percent == 70

    page.close()
    app.quit()


def test_lighting_page_identifies_argb_targets(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LightingPage

    monkeypatch.setattr(pages, "save_settings", lambda settings: None)
    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    page = LightingPage(controller=controller)

    page.connect_openrgb()
    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    page.identify_argb_targets()

    assert _process_events_until(app, lambda: page.openrgb_status_label.text().startswith("ARGB 向导"))
    assert not page.save_next_argb_button.isHidden()
    assert len(controller.applied) == 1
    assert controller.applied[0].target_id == "device:0:zone:1"
    assert controller.applied[0].color == "#ff0000"
    page.target_alias_input.setText("顶部风扇")
    page.save_argb_alias_and_continue()

    assert _process_events_until(app, lambda: page.openrgb_status_label.text() == "ARGB 命名完成", timeout=2.0)
    assert page.settings.lighting.target_aliases["device:0:zone:1"] == "顶部风扇"
    assert page.save_next_argb_button.isHidden()
    assert controller.applied[1].target_id == "device:0:zone:1"

    page.close()
    app.quit()


def test_lighting_page_renames_and_deletes_scene(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.pages as pages
    from usb9_lcd.gui.pages import LightingPage
    from usb9_lcd.gui.settings import GuiSettings

    monkeypatch.setattr(pages, "save_settings", lambda settings: None)
    app = QApplication.instance() or QApplication([])
    controller = FakeLightingController()
    settings = GuiSettings()
    settings.lighting.target_aliases["device:0:zone:1"] = "顶部风扇"
    settings.lighting.scenes["旧场景"] = {
        "targets": {
            "device:0:zone:1": {
                "effect": "static",
                "color": "#ffffff",
                "brightness_percent": 80,
                "speed": 5,
                "argb_zone_size": 30,
            }
        }
    }
    settings.lighting.active_scene = "旧场景"
    page = LightingPage(controller=controller, settings=settings)

    page.connect_openrgb()
    assert _process_events_until(app, lambda: page.lighting_target_combo.count() == 2)
    assert "顶部风扇" in page.scene_summary_text.toPlainText()
    page.scene_combo.setCurrentText("新场景")
    page.rename_lighting_scene()

    assert "旧场景" not in settings.lighting.scenes
    assert "新场景" in settings.lighting.scenes
    assert settings.lighting.active_scene == "新场景"
    assert "顶部风扇" in page.scene_summary_text.toPlainText()
    page.delete_lighting_scene()

    assert settings.lighting.scenes == {}
    assert settings.lighting.active_scene == ""
    assert "未选择场景" in page.scene_summary_text.toPlainText()

    page.close()
    app.quit()


def test_main_window_refreshes_devices_and_uploads_selected_image(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    driver = FakeDriver()
    image_path = tmp_path / "red.png"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(image_path)

    window = MainWindow(
        driver=driver,
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )
    window.refresh_devices()

    assert window.device_combo.count() == 1
    assert window.device_combo.itemText(0) == "ASUS Test LCD"
    assert "ASUS Test LCD" in window.device_status_label.text()
    assert "/dev/hidraw-control" in window.device_status_label.text()
    assert "可写" in window.device_status_label.text()
    assert "static_image" in window.device_details.toPlainText()

    window.set_image_path(image_path)
    window.fit_combo.setCurrentText("stretch")
    window.rotate_combo.setCurrentText("0")
    window.background_input.setText("#000000")
    window.upload_selected_image()

    assert len(driver.uploads) == 1
    uploaded_device, uploaded_frame = driver.uploads[0]
    assert uploaded_device is driver.device
    assert uploaded_frame == bytes([0xF8, 0x00, 0xF8, 0x00, 0xF8, 0x00, 0xF8, 0x00])
    assert "上传成功" in window.statusBar().currentMessage()

    window.close()
    app.quit()


def test_preview_widget_uses_device_aspect_ratio_not_source_image_ratio(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import PreviewWidget

    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "square.png"
    Image.new("RGB", (20, 20), (255, 0, 0)).save(image_path)

    preview = PreviewWidget()
    preview.resize(500, 300)
    preview.preview_label.resize(500, 300)
    preview.set_device(_fake_device(width=320, height=160))
    preview.set_image_path(image_path)

    pixmap = preview.preview_label.pixmap()

    assert pixmap is not None
    assert pixmap.width() > pixmap.height()
    assert pixmap.width() / pixmap.height() == 2

    preview.close()
    app.quit()


def test_main_window_uploads_monitoring_frame_from_latest_telemetry():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    driver = FakeDriver()

    window = MainWindow(
        driver=driver,
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )
    window.refresh_devices()

    window.upload_monitoring_frame()

    assert len(driver.uploads) == 1
    uploaded_device, uploaded_frame = driver.uploads[0]
    assert uploaded_device is driver.device
    assert len(uploaded_frame) == driver.device.width * driver.device.height * 2
    assert "监控画面已上传" in window.statusBar().currentMessage()

    window.close()
    app.quit()


def test_main_window_close_starts_keepalive_for_asus_last_frame(monkeypatch, tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    started = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("usb9_lcd.gui.main_window.stop_existing_keepalive", lambda: None)

    driver = FakeDriver()
    driver.device = _fake_device(driver_id="asus.lc_iii")

    window = MainWindow(
        driver=driver,
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )
    window.refresh_devices()
    window.upload_monitoring_frame()

    class FakePopen:
        def __init__(self, args, **kwargs):  # noqa: ANN001
            started.append((args, kwargs))

    monkeypatch.setattr("usb9_lcd.gui.main_window.subprocess.Popen", FakePopen)
    window.close()

    assert started
    assert started[0][0][1:3] == ["-m", "usb9_lcd.keepalive"]
    assert Path(started[0][0][3]).is_file()

    app.quit()


def test_monitor_page_render_settings_include_selected_layout():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    window.monitor_page.monitor_layout_combo.setCurrentIndex(
        window.monitor_page.monitor_layout_combo.findData("gpu_focus")
    )
    window.monitor_page.monitor_palette_combo.setCurrentIndex(
        window.monitor_page.monitor_palette_combo.findData("ice")
    )
    window.monitor_page.show_gpu_clock_checkbox.setChecked(True)
    settings = window.monitor_page.render_settings()

    assert settings.layout == "gpu_focus"
    assert settings.palette == "ice"
    assert settings.show_gpu_clock is True

    window.close()
    app.quit()


def test_main_window_temperature_color_uses_blue_to_red_gradient():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    assert window._temperature_color(35) == "#0088ff"
    assert window._temperature_color(55) == "#00e5ff"
    assert window._temperature_color(70) == "#ffd60a"
    assert window._temperature_color(85) == "#ff2d55"
    assert window._utilization_color(0) == "#0088ff"
    assert window._utilization_color(40) == "#00e5ff"
    assert window._utilization_color(75) == "#ffd60a"
    assert window._utilization_color(100) == "#ff2d55"

    window.close()
    app.quit()


def test_main_window_constructs_with_saved_monitor_palette_before_device_combo_exists():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow
    from usb9_lcd.gui.settings import GuiSettings

    app = QApplication.instance() or QApplication([])
    settings = GuiSettings()
    settings.monitor.palette = "ice"

    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
        settings=settings,
    )

    assert window.monitor_page.monitor_palette_combo.currentData() == "ice"

    window.close()
    app.quit()


def test_lighting_sync_supports_cpu_and_gpu_utilization():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )
    window.latest_telemetry = _fake_telemetry()

    assert window.lighting_page.sync_mode_combo.findText("根据 CPU 利用率变色") >= 0
    assert window.lighting_page.sync_mode_combo.findText("根据 GPU 利用率变色") >= 0
    assert window.lighting_sync_color(3) == window._utilization_color(18)
    assert window.lighting_sync_color(4) == window._utilization_color(42)

    window.close()
    app.quit()


def test_main_window_does_not_upload_stale_monitoring_after_telemetry_refresh_failure():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    driver = FakeDriver()

    window = MainWindow(
        driver=driver,
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )
    window.refresh_devices()
    window.refresh_telemetry()

    def exploding_provider() -> SystemTelemetry:
        raise RuntimeError("sensor offline")

    window.telemetry_provider = exploding_provider
    window.refresh_telemetry()
    window.upload_monitoring_frame()

    assert driver.uploads == []
    assert "监控画面上传失败：sensor offline" in window.statusBar().currentMessage()

    window.close()
    app.quit()


def test_main_window_refresh_devices_preserves_selected_device_for_monitoring_upload():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    driver = MultiDeviceDriver()

    window = MainWindow(
        driver=driver,
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )
    window.refresh_devices()
    window.device_combo.setCurrentIndex(1)
    selected_device = driver.devices[1]

    window.refresh_devices()
    window.upload_monitoring_frame()

    assert window.device_combo.currentIndex() == 1
    assert len(driver.uploads) == 1
    uploaded_device, _ = driver.uploads[0]
    assert uploaded_device is selected_device

    window.close()
    app.quit()


def test_main_window_upload_monitoring_frame_requires_selected_device():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    driver = EmptyDriver()

    window = MainWindow(
        driver=driver,
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )
    window.refresh_devices()
    window.upload_monitoring_frame()

    assert driver.uploads == []
    assert window.statusBar().currentMessage() == "请先选择设备"

    window.close()
    app.quit()


def test_main_window_upload_monitoring_frame_shows_upload_failure():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    driver = UploadFailingDriver()

    window = MainWindow(
        driver=driver,
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )
    window.refresh_devices()
    window.upload_monitoring_frame()

    assert "监控画面上传失败" in window.statusBar().currentMessage()
    assert "usb write failed" in window.statusBar().currentMessage()

    window.close()
    app.quit()


def test_main_window_live_monitoring_repeats_uploads_until_stopped():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    driver = FakeDriver()
    window = MainWindow(
        driver=driver,
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )
    window.refresh_devices()

    window.start_live_monitoring()

    assert _process_events_until(app, lambda: len(driver.uploads) >= 2, timeout=2.5)
    assert window.monitor_page.live_monitor_button.text() == "停止实时监控"

    window.stop_live_monitoring()

    assert window.monitor_page.live_monitor_button.text() == "开始实时监控"
    assert window._monitor_thread is None

    window.close()
    app.quit()


def test_main_window_play_animation_uploads_one_frame(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    driver = FakeDriver()
    library = AssetLibrary(tmp_path / "assets")
    gif_path = tmp_path / "blink.gif"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(
        gif_path,
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), (0, 255, 0))],
        duration=100,
        loop=0,
    )
    imported = library.import_file(gif_path)

    window = MainWindow(
        driver=driver,
        telemetry_provider=lambda: _fake_telemetry(),
        asset_library=library,
        auto_refresh=False,
    )
    window.refresh_devices()
    window.select_asset_for_playback(imported)

    window.play_animation()

    assert _process_events_until(app, lambda: len(driver.uploads) == 1)
    assert len(driver.uploads) == 1
    assert len(driver.uploads[0][1]) == driver.device.width * driver.device.height * 2
    assert "动画循环播放中" in window.statusBar().currentMessage()
    assert window.animation_timer.isActive() is True
    assert window._animation_frame_index == 1
    assert _process_events_until(app, lambda: len(driver.uploads) >= 3)
    window.stop_animation()
    assert len(driver.uploads) >= 3
    assert driver.uploads[0][1] == driver.uploads[2][1]

    window.close()
    app.quit()


def test_main_window_stop_animation_stops_timer_and_updates_status():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    window.animation_timer.start()
    window.stop_animation()

    assert window.animation_timer.isActive() is False
    assert window.statusBar().currentMessage() == "动画播放已停止"

    window.close()
    app.quit()


def test_main_window_play_animation_requires_selected_device(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=EmptyDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )
    window.selected_animation_path = tmp_path / "blink.gif"
    window.refresh_devices()

    window.play_animation()

    assert window.statusBar().currentMessage() == "请先选择设备"

    window.close()
    app.quit()


def test_main_window_play_animation_requires_selected_asset():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )
    window.refresh_devices()

    window.play_animation()

    assert window.statusBar().currentMessage() == "请先选择动图素材"

    window.close()
    app.quit()


def test_main_window_play_animation_stops_on_upload_failure(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    driver = UploadFailingDriver()
    library = AssetLibrary(tmp_path / "assets")
    gif_path = tmp_path / "blink.gif"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(
        gif_path,
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), (0, 255, 0))],
        duration=100,
        loop=0,
    )
    imported = library.import_file(gif_path)
    window = MainWindow(
        driver=driver,
        telemetry_provider=lambda: _fake_telemetry(),
        asset_library=library,
        auto_refresh=False,
    )
    window.refresh_devices()
    window.select_asset_for_playback(imported)

    window.play_animation()
    window.play_next_animation_frame()

    assert _process_events_until(app, lambda: not window.animation_timer.isActive())
    assert window.animation_timer.isActive() is False
    assert "动画播放失败：usb write failed" in window.statusBar().currentMessage()

    window.close()
    app.quit()


def test_main_window_play_animation_stops_when_selected_device_changes(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    driver = MultiDeviceDriver()
    library = AssetLibrary(tmp_path / "assets")
    gif_path = tmp_path / "blink.gif"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(
        gif_path,
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), (0, 255, 0))],
        duration=100,
        loop=0,
    )
    imported = library.import_file(gif_path)
    window = MainWindow(
        driver=driver,
        telemetry_provider=lambda: _fake_telemetry(),
        asset_library=library,
        auto_refresh=False,
    )
    window.refresh_devices()
    window.select_asset_for_playback(imported)

    window.play_animation()
    window.device_combo.setCurrentIndex(1)
    window.play_next_animation_frame()

    assert len(driver.uploads) == 1
    assert window.animation_timer.isActive() is False
    assert window.statusBar().currentMessage() == "设备已切换，动画播放已停止"

    window.close()
    app.quit()


def test_main_window_play_animation_stops_when_device_disappears(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    driver = FakeDriver()
    library = AssetLibrary(tmp_path / "assets")
    gif_path = tmp_path / "blink.gif"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(
        gif_path,
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), (0, 255, 0))],
        duration=100,
        loop=0,
    )
    imported = library.import_file(gif_path)
    window = MainWindow(
        driver=driver,
        telemetry_provider=lambda: _fake_telemetry(),
        asset_library=library,
        auto_refresh=False,
    )
    window.refresh_devices()
    window.select_asset_for_playback(imported)

    window.play_animation()
    window.devices = []
    window.device_combo.clear()
    window.play_next_animation_frame()

    assert len(driver.uploads) == 1
    assert window.animation_timer.isActive() is False
    assert window.statusBar().currentMessage() == "设备不可用，动画播放已停止"

    window.close()
    app.quit()


def test_main_window_play_animation_uses_background_python_thread(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    driver = FakeDriver()
    library = AssetLibrary(tmp_path / "assets")
    gif_path = tmp_path / "blink.gif"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(
        gif_path,
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), (0, 255, 0))],
        duration=100,
        loop=0,
    )
    imported = library.import_file(gif_path)
    window = MainWindow(
        driver=driver,
        telemetry_provider=lambda: _fake_telemetry(),
        asset_library=library,
        auto_refresh=False,
    )
    window.refresh_devices()
    window.select_asset_for_playback(imported)
    window.play_animation()

    assert _process_events_until(app, lambda: len(driver.uploads) >= 1)
    assert window._animation_uploading is True
    assert window._animation_thread is not None
    assert window._animation_thread.is_alive() is True

    window.stop_animation()
    window.close()
    app.quit()


def test_main_window_reports_permission_helper_in_status_and_device_tree():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow
    from usb9_lcd.gui.system_status import render_system_status_report
    from usb9_lcd.service.permissions import PermissionHelperStatus

    class Helper:
        def status(self):  # noqa: ANN001
            return PermissionHelperStatus(
                available=True,
                backend="test-helper",
                detail="test helper ready",
                supported_operations=("pwm-write", "powercap-read"),
            )

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
        permission_helper=Helper(),
    )

    report = render_system_status_report(window._system_status_snapshot())

    assert "权限 Helper" in report
    assert "test-helper" in report
    assert "pwm-write" in report
    assert "权限 Helper [permission-helper]" in report

    window.close()
    app.quit()


def test_main_window_refresh_telemetry_updates_dashboard_labels():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    samples = [_fake_telemetry(), _unavailable_telemetry()]

    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: samples.pop(0),
        auto_refresh=False,
    )

    window.refresh_telemetry()

    assert "54" in window.cpu_temp_value.text()
    assert "61" in window.gpu_temp_value.text()
    assert "RTX" in window.gpu_detail_value.text()

    window.refresh_telemetry()

    assert "不可用" in window.cpu_temp_value.text()
    assert "不可用" in window.gpu_temp_value.text()

    window.close()
    app.quit()


def test_main_window_can_construct_without_startup_refresh_work():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    def exploding_provider() -> SystemTelemetry:
        raise AssertionError("telemetry should not run during construction")

    window = MainWindow(
        driver=ExplodingDriver(),
        telemetry_provider=exploding_provider,
        auto_refresh=False,
    )

    assert window.windowTitle() == "usb9-lcd"

    window.close()
    app.quit()


def test_main_window_configures_telemetry_timer_for_auto_refresh():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=True,
    )

    assert window.telemetry_timer.interval() == 2000
    assert window.telemetry_timer.isActive() is True

    window.close()
    assert window.telemetry_timer.isActive() is False
    app.quit()


def test_main_window_cpu_power_permission_grant_uses_privileged_runner(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.main_window as main_window
    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    power_path = Path("/sys/class/powercap/intel-rapl:0/energy_uj")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(main_window.sys, "platform", "linux")
    monkeypatch.setattr(main_window, "cpu_power_permission_paths", lambda: [power_path])
    monkeypatch.setattr(main_window, "cpu_power_permission_shell", lambda paths: f"grant {paths[0]}")
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    def fake_runner(commands, *, timeout, interactive, action_label):  # noqa: ANN001
        calls.append(
            {
                "commands": commands,
                "timeout": timeout,
                "interactive": interactive,
                "action_label": action_label,
            }
        )
        return True, "ok"

    window.fan_page._run_privileged_shell_compat = fake_runner

    window.request_cpu_power_permission_grant(interactive=True)
    window.request_cpu_power_permission_grant(interactive=True)

    assert calls == [
        {
            "commands": f"grant {power_path}",
            "timeout": 30,
            "interactive": True,
            "action_label": "授权 CPU 功耗权限",
        }
    ]
    assert "CPU 功耗权限已授权" in window.statusBar().currentMessage()

    window.close()
    app.quit()


def test_main_window_auto_refresh_does_not_collect_telemetry_during_construction():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    provider_calls = 0

    def counting_provider() -> SystemTelemetry:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("telemetry should not run during construction")

    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=counting_provider,
        auto_refresh=True,
    )

    assert provider_calls == 0

    window.close()
    app.quit()


def test_main_window_does_not_start_telemetry_timer_without_auto_refresh():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    window = MainWindow(
        driver=ExplodingDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    assert window.telemetry_timer.interval() == 2000
    assert window.telemetry_timer.isActive() is False

    window.close()
    app.quit()


def test_main_window_request_telemetry_refresh_updates_dashboard_synchronously():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    calls = []

    def provider() -> SystemTelemetry:
        calls.append("called")
        return SystemTelemetry(
            cpu=CpuTelemetry(package_temperature_c=61.2, utilization_percent=20, available=True),
            gpu=GpuTelemetry(name="RTX", temperature_c=72, available=True),
            captured_at=datetime(2026, 5, 20, 12, 2, 0),
        )

    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=provider,
        auto_refresh=False,
    )

    window.request_telemetry_refresh()

    assert calls == ["called"]
    assert "CPU 61°C" in window.cpu_temp_value.text()
    assert "Load 20%" in window.cpu_temp_value.text()
    assert window.gpu_temp_value.text() == "GPU 72°C"

    window.close()
    app.quit()


def test_main_window_enforces_lianli_write_gate_in_production_page():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        auto_refresh=False,
    )

    assert window.lianli_page.require_write_gate is True

    window.close()
    app.quit()


def test_main_window_forwards_telemetry_to_lianli_page_synchronously():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    def provider() -> SystemTelemetry:
        return SystemTelemetry(
            cpu=CpuTelemetry(package_temperature_c=61.2, utilization_percent=20, available=True),
            gpu=GpuTelemetry(name="RTX", temperature_c=72, available=True),
            captured_at=datetime(2026, 5, 20, 12, 2, 0),
        )

    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=provider,
        auto_refresh=False,
    )
    forwarded: list[float | None] = []
    window.lianli_page.update_telemetry = lambda telemetry: forwarded.append(telemetry.cpu.package_temperature_c)

    window.request_telemetry_refresh()

    assert forwarded == [61.2]

    window.close()
    app.quit()


def test_main_window_forwards_telemetry_to_lianli_page_asynchronously():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    def provider() -> SystemTelemetry:
        return SystemTelemetry(
            cpu=CpuTelemetry(package_temperature_c=63.5, utilization_percent=25, available=True),
            gpu=GpuTelemetry(name="RTX", temperature_c=72, available=True),
            captured_at=datetime(2026, 5, 20, 12, 2, 0),
        )

    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=provider,
        auto_refresh=True,
    )
    forwarded: list[float | None] = []
    window.lianli_page.update_telemetry = lambda telemetry: forwarded.append(telemetry.cpu.package_temperature_c)

    window.request_telemetry_refresh()

    assert _process_events_until(app, lambda: forwarded)
    assert forwarded[-1] == 63.5

    window.close()
    app.quit()


def test_main_window_request_telemetry_refresh_handles_provider_failure():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    def provider() -> SystemTelemetry:
        raise RuntimeError("sensor failed")

    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=provider,
        auto_refresh=False,
    )

    window.request_telemetry_refresh()

    assert window.latest_telemetry is None
    assert window.cpu_temp_value.text() == "CPU 不可用"

    window.close()
    app.quit()


def test_main_window_does_not_refresh_assets_during_construction():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    library = ExplodingAssetLibrary()

    window = MainWindow(
        driver=ExplodingDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        asset_library=library,
        auto_refresh=False,
    )

    assert library.calls == []

    window.close()
    app.quit()


def test_main_window_auto_refresh_loads_assets_after_startup(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    library = AssetLibrary(tmp_path / "assets")
    image_path = tmp_path / "logo.png"
    Image.new("RGB", (4, 4), (255, 0, 0)).save(image_path)
    library.import_file(image_path)

    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        asset_library=library,
        auto_refresh=True,
    )

    assert _process_events_until(app, lambda: window.asset_page.asset_list.count() == 1)

    window.close()
    app.quit()


def test_asset_page_refresh_assets_shows_load_failure():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.pages import AssetLibraryPage

    app = QApplication.instance() or QApplication([])
    page = AssetLibraryPage(ExplodingAssetLibrary())

    page.refresh_assets()

    assert "素材加载失败：" in page.asset_list_text.toPlainText()

    page.close()
    app.quit()


def test_main_window_asset_page_lists_links_and_media(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    library = AssetLibrary(tmp_path)
    Image.new("RGB", (2, 2), (255, 0, 0)).save(tmp_path / "user" / "red.png")

    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        asset_library=library,
        auto_refresh=False,
    )
    window.refresh_assets()

    assert "red.png" in window.asset_list_text.toPlainText()
    assert "2x2" in window.asset_list_text.toPlainText()
    assert "png" in window.asset_list_text.toPlainText()
    assert "静态" in window.asset_list_text.toPlainText()
    assert "1 帧" in window.asset_list_text.toPlainText()
    assert "ROG official GIPHY" in window.asset_links_text.toPlainText()
    assert "https://giphy.com/GlobalROG" in window.asset_links_text.toPlainText()
    assert "rog, gif" in window.asset_links_text.toPlainText()

    window.close()
    app.quit()


def test_asset_page_refresh_assets_populates_clickable_media_list(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.pages import AssetLibraryPage

    app = QApplication.instance() or QApplication([])
    library = AssetLibrary(tmp_path / "assets")
    source = tmp_path / "red.png"
    Image.new("RGB", (2, 2), (255, 0, 0)).save(source)
    imported = library.import_file(source)
    page = AssetLibraryPage(library)

    page.refresh_assets()

    assert page.asset_list.count() >= 1
    names = [page.asset_list.item(index).text() for index in range(page.asset_list.count())]
    assert any(imported.name in name for name in names)

    page.close()
    app.quit()


def test_asset_page_filters_media_by_category(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.pages import AssetLibraryPage

    app = QApplication.instance() or QApplication([])
    library = AssetLibrary(tmp_path / "assets")
    Image.new("RGB", (4, 4), (0, 0, 255)).save(library.monitor_backgrounds_dir / "neon_meter.png")
    gif_path = tmp_path / "blink.gif"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(
        gif_path,
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), (0, 255, 0))],
        duration=100,
        loop=0,
    )
    library.import_file(gif_path)
    page = AssetLibraryPage(library)

    page.refresh_assets()
    assert "neon_meter.png" in page.asset_list_text.toPlainText()
    assert "blink.gif" in page.asset_list_text.toPlainText()

    index = page.asset_category_combo.findData("monitoring")
    assert index >= 0
    page.asset_category_combo.setCurrentIndex(index)
    page.refresh_assets()

    assert page.asset_list.count() == 1
    assert "neon_meter.png" in page.asset_list_text.toPlainText()
    assert "blink.gif" not in page.asset_list_text.toPlainText()
    assert "监控仪表盘" in page.asset_list_text.toPlainText()

    page.close()
    app.quit()


def test_asset_page_clicking_media_updates_preview(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.pages import AssetLibraryPage

    app = QApplication.instance() or QApplication([])
    library = AssetLibrary(tmp_path / "assets")
    static_path = tmp_path / "red.png"
    gif_path = tmp_path / "blink.gif"
    Image.new("RGB", (2, 2), (255, 0, 0)).save(static_path)
    Image.new("RGB", (1, 1), (255, 0, 0)).save(
        gif_path,
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), (0, 255, 0))],
        duration=100,
        loop=0,
    )
    imported_static = library.import_file(static_path)
    imported_gif = library.import_file(gif_path)
    page = AssetLibraryPage(library)
    page.refresh_assets()

    for index in range(page.asset_list.count()):
        item = page.asset_list.item(index)
        if item.data(page.asset_path_role) == str(imported_static):
            page.asset_list.setCurrentItem(item)
            break

    assert page.selected_asset_path == imported_static
    assert page.asset_preview.pixmap() is not None
    assert imported_static.name in page.asset_preview_caption.text()

    for index in range(page.asset_list.count()):
        item = page.asset_list.item(index)
        if item.data(page.asset_path_role) == str(imported_gif):
            page.asset_list.setCurrentItem(item)
            break

    assert page.selected_asset_path == imported_gif
    assert page.asset_preview.movie() is None
    assert page.asset_preview.pixmap() is not None
    assert page.asset_preview.pixmap().isNull() is False
    assert page._gif_preview_frames
    assert page._gif_preview_durations == [100, 100]
    assert page.gif_preview_timer.interval() == 100
    assert "GIF 解码预览" in page.asset_preview_caption.text()

    page.release_preview_resources()

    assert page._gif_preview_frames == []
    assert page.asset_preview.pixmap().isNull()

    page.close()
    app.quit()


def test_asset_page_selects_animated_asset_for_playback(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    library = AssetLibrary(tmp_path / "assets")
    gif_path = tmp_path / "blink.gif"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(
        gif_path,
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), (0, 255, 0))],
        duration=100,
        loop=0,
    )
    imported = library.import_file(gif_path)

    window = MainWindow(
        driver=FakeDriver(),
        telemetry_provider=lambda: _fake_telemetry(),
        asset_library=library,
        auto_refresh=False,
    )
    window.select_asset_for_playback(imported)

    assert window.selected_animation_path == imported
    assert f"已选择动画素材：{imported.name}" == window.statusBar().currentMessage()

    window.close()
    app.quit()


def test_asset_page_selected_media_paths_returns_only_animated_assets(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.pages import AssetLibraryPage

    app = QApplication.instance() or QApplication([])
    library = AssetLibrary(tmp_path / "assets")
    static_path = tmp_path / "red.png"
    gif_path = tmp_path / "blink.gif"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(static_path)
    Image.new("RGB", (1, 1), (255, 0, 0)).save(
        gif_path,
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), (0, 255, 0))],
        duration=100,
        loop=0,
    )
    imported_static = library.import_file(static_path)
    imported_gif = library.import_file(gif_path)

    page = AssetLibraryPage(library)
    page.refresh_assets()
    selected_paths = page.selected_media_paths()

    assert imported_gif in selected_paths
    assert imported_static not in selected_paths
    assert set(selected_paths) == {asset.path for asset in library.list_media() if asset.animated}

    page.close()
    app.quit()


def test_asset_page_select_current_animation_uses_cached_list(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.pages import AssetLibraryPage

    app = QApplication.instance() or QApplication([])
    library = AssetLibrary(tmp_path / "assets")
    gif_path = tmp_path / "blink.gif"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(
        gif_path,
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), (0, 255, 0))],
        duration=100,
        loop=0,
    )
    imported_gif = library.import_file(gif_path)
    selected: list[Path] = []
    page = AssetLibraryPage(library, select_asset_for_playback=selected.append)
    page.refresh_assets()
    for index in range(page.asset_list.count()):
        item = page.asset_list.item(index)
        if item.data(page.asset_path_role) == str(imported_gif):
            page.asset_list.setCurrentItem(item)
            break

    def exploding_list_media():
        raise AssertionError("selecting a cached asset should not rescan media")

    library.list_media = exploding_list_media  # type: ignore[method-assign]

    page.select_selected_or_first_animated_asset()

    assert selected == [imported_gif]

    page.close()
    app.quit()


def test_asset_page_import_asset_path_imports_valid_image(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.pages import AssetLibraryPage

    app = QApplication.instance() or QApplication([])
    library = AssetLibrary(tmp_path / "assets")
    source = tmp_path / "source.png"
    Image.new("RGB", (2, 2), (255, 0, 0)).save(source)
    page = AssetLibraryPage(library)

    page.import_asset_path(source)

    assert "source.png" in page.asset_list_text.toPlainText()

    page.close()
    app.quit()


def test_asset_page_import_asset_path_shows_failure_for_bad_image(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.pages import AssetLibraryPage

    app = QApplication.instance() or QApplication([])
    library = AssetLibrary(tmp_path / "assets")
    source = tmp_path / "bad.png"
    source.write_bytes(b"not a real png")
    page = AssetLibraryPage(library)

    page.import_asset_path(source)

    assert "导入失败：" in page.asset_list_text.toPlainText()

    page.close()
    app.quit()


_LEGACY_FANCONTROL_WORKSPACE_TESTS = (
    "test_fan_page_exposes_control_center_layout_before_loading",
    "test_fan_page_updates_summary_strategy_and_channel_table",
    "test_fan_page_apply_profile_repairs_unwritable_active_file",
    "test_fan_page_apply_profile_repairs_set_active_permission_error",
    "test_fan_page_visual_dashboard_updates_cards_and_charts",
    "test_fan_page_classifies_mainboard_fan_roles_from_hwmon_labels",
    "test_fan_page_maps_display_fan_names_back_to_backend_channels",
    "test_fan_page_stress_panel_controls_burner",
    "test_embedded_profile_editor_uses_compact_profile_selector",
    "test_embedded_profile_editor_repairs_unwritable_active_profile",
    "test_embedded_profile_editor_repairs_set_active_permission_error",
    "test_embedded_profile_editor_stops_before_unwritable_profile_dir",
    "test_profile_permission_repair_command_restores_owner_and_user_write",
    "test_fan_page_permission_panel_generates_temp_fix_commands",
    "test_fan_page_blocks_pwm_enable_when_system_auth_fails",
    "test_fan_page_enable_pwm_requests_system_auth_and_continues",
    "test_fan_page_auto_grants_pwm_permissions_on_load",
    "test_fan_page_does_not_auto_grant_pwm_permissions_by_default",
    "test_fan_page_manual_permission_request_uses_system_auth",
    "test_fan_page_load_failure_releases_partial_resources",
    "test_fan_page_loads_read_only_then_enables_pwm_control",
    "test_fan_page_reports_missing_hwmon_fan_channels",
    "test_fan_page_reload_recovers_after_missing_hwmon_channels",
    "test_fan_page_uses_nvidia_smi_readonly_fallback",
    "test_fan_page_distinguishes_readonly_fallback_from_mainboard_pwm_loading",
    "test_fan_page_pwm_action_requests_driver_probe_when_mainboard_hidden",
    "test_fan_page_load_button_requests_driver_probe_when_mainboard_hidden",
    "test_fan_page_loads_readonly_rpm_sensors_as_channels",
    "test_fan_page_probes_hwmon_driver_before_loading",
    "test_fan_page_interactive_driver_probe_adds_nct6683_force_fallback",
    "test_fan_page_auto_load_uses_noninteractive_driver_probe",
    "test_fan_page_auto_load_skips_driver_probe_without_passwordless_sudo",
    "test_fan_page_manual_hwmon_driver_probe_uses_system_auth_and_reloads",
    "test_fan_page_auto_loads_and_enables_pwm_control",
)

_LEGACY_FANCONTROL_SKIP_REASON = (
    "legacy FanControl workspace GUI was replaced by the current generic fan page; "
    "current fan behavior is covered in tests/test_fan_host.py"
)

for _legacy_name in _LEGACY_FANCONTROL_WORKSPACE_TESTS:
    if _legacy_name in globals():
        globals()[_legacy_name] = pytest.mark.skip(reason=_LEGACY_FANCONTROL_SKIP_REASON)(globals()[_legacy_name])
