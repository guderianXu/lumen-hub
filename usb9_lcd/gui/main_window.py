from __future__ import annotations

from contextlib import AbstractContextManager
from collections.abc import Callable
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import cast
from weakref import ref

from PIL import Image
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from usb9_lcd.animation import AnimatedFrame, AnimationRenderSettings, iter_animated_frames
from usb9_lcd.assets import AssetLibrary
from usb9_lcd.drivers import AsusLcIiiDriver, DisplayDriver
from usb9_lcd.drivers.base import DisplayDevice, PixelFormat
from usb9_lcd.gui.debug import log_event, log_exception, recent_log_lines
try:
    from usb9_lcd.gui.fan_host import FanControlHostPage
except Exception as fan_host_import_error:  # noqa: BLE001 - keep the GUI usable on Windows if Linux fan page is unavailable.
    FAN_HOST_IMPORT_ERROR = fan_host_import_error

    class FanControlHostPage(QWidget):
        status_changed = Signal(str)

        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            super().__init__()
            self._error = FAN_HOST_IMPORT_ERROR
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 22, 24, 24)
            title = QLabel("Linux fan control unavailable on this platform")
            title.setObjectName("PageTitle")
            message = QLabel(
                "The Linux PWM fan control page could not be loaded. "
                "Other Windows-compatible pages, including LIAN LI tooling, remain available."
            )
            message.setWordWrap(True)
            details = QTextEdit()
            details.setReadOnly(True)
            details.setPlainText(str(self._error))
            layout.addWidget(title)
            layout.addWidget(message)
            layout.addWidget(details, 1)
            layout.addStretch(1)

        def home_status_text(self) -> str:
            return "Fan page unavailable on Windows"

        def load_fan_control(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            self.status_changed.emit(self.home_status_text())

        def reload_fan_control(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            self.load_fan_control(*args, **kwargs)

        def release(self) -> None:
            return None
from usb9_lcd.gui.home import ControlCenterPage
from usb9_lcd.gui.asset_page import AssetLibraryPage
from usb9_lcd.gui.monitor_page import MonitorPage
from usb9_lcd.gui.lianli_wireless_page import LianLiWirelessPage
from usb9_lcd.gui.lighting_page import LightingPage
from usb9_lcd.gui.platform_diagnostics import PlatformDiagnosticsDialog
from usb9_lcd.gui.preview import fit_preview_geometry
from usb9_lcd.gui.settings import DEFAULT_SETTINGS_PATH, GuiSettings, load_settings, save_settings
from usb9_lcd.gui.system_status import (
    StatusItem,
    SystemStatusSnapshot,
    render_system_status_report,
    summarize_permission_status,
)
from usb9_lcd.gui.theme import gui_stylesheet
from usb9_lcd.keepalive import DEFAULT_PID_FILE, stop_existing_keepalive
from usb9_lcd.image import FitMode, FrameConfig, Rotation, image_to_jpeg_bytes
from usb9_lcd.monitoring.models import SystemTelemetry
from usb9_lcd.monitoring.cpu import cpu_power_permission_paths, cpu_power_permission_shell
from usb9_lcd.monitoring.render import render_monitoring_frame, render_monitoring_image
from usb9_lcd.monitoring.service import collect_system_telemetry
from usb9_lcd.platforms import current_platform
from usb9_lcd.render import ImageRenderSettings, render_static_image


def _mix_color(start: tuple[int, int, int], end: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    clamped = max(0.0, min(1.0, ratio))
    return tuple(round(start[index] + (end[index] - start[index]) * clamped) for index in range(3))


def _hex_color(color: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)


def _compact_log_line(line: str, *, max_length: int = 140) -> str:
    compact = " ".join(line.split())
    if len(compact) <= max_length:
        return compact
    return compact[-max_length:]


def _state_from_summary(text: str) -> str:
    lowered = text.lower()
    if any(marker in text for marker in ("失败", "错误", "不可用", "不可写", "未发现")):
        return "warn"
    if any(marker in text for marker in ("可控", "已连接", "已就绪", "成功", "可写")):
        return "ok"
    if "unavailable" in lowered or "failed" in lowered:
        return "warn"
    return "info"


def _cpu_status_detail(telemetry: SystemTelemetry) -> str:
    if not telemetry.cpu.available:
        return telemetry.cpu.error or "不可用"
    temperature = "--" if telemetry.cpu.package_temperature_c is None else f"{telemetry.cpu.package_temperature_c:.0f}°C"
    load = "--" if telemetry.cpu.utilization_percent is None else f"{telemetry.cpu.utilization_percent:.0f}%"
    power = "--" if telemetry.cpu.power_w is None else f"{telemetry.cpu.power_w:.0f}W"
    return f"温度 {temperature} / 负载 {load} / 功耗 {power}"


def _gpu_status_detail(telemetry: SystemTelemetry) -> str:
    if not telemetry.gpu.available:
        return telemetry.gpu.error or "不可用"
    temperature = "--" if telemetry.gpu.temperature_c is None else f"{telemetry.gpu.temperature_c:.0f}°C"
    load = "--" if telemetry.gpu.utilization_percent is None else f"{telemetry.gpu.utilization_percent:.0f}%"
    power = "--" if telemetry.gpu.power_w is None else f"{telemetry.gpu.power_w:.0f}W"
    return f"{telemetry.gpu.name or 'GPU'} / 温度 {temperature} / 负载 {load} / 功耗 {power}"


class _OneShotUploadSession(AbstractContextManager):
    def __init__(self, driver: DisplayDriver, device: DisplayDevice) -> None:
        self.driver = driver
        self.device = device

    def __enter__(self) -> "_OneShotUploadSession":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:  # noqa: ANN001
        return None

    def upload_static_frame(self, frame: bytes) -> None:
        self.driver.upload_static_frame(self.device, frame)


def _scrollable_page(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setObjectName("PageScrollArea")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setWidget(widget)
    return scroll


class PreviewWidget(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.device: DisplayDevice | None = None
        self.image_path: Path | None = None
        self.setMinimumSize(260, 260)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("ScreenPreviewCard")
        layout = QVBoxLayout(self)
        self.preview_label = QLabel("未发现设备")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label, 1)

    def set_device(self, device: DisplayDevice | None) -> None:
        self.device = device
        self._update_preview()

    def set_image_path(self, image_path: Path | None) -> None:
        self.image_path = image_path
        self._update_preview()

    def _update_preview(self) -> None:
        if self.device is None:
            self.preview_label.clear()
            self.preview_label.setText("未发现设备")
            return

        path = self.image_path
        if path is None:
            self.preview_label.clear()
            self.preview_label.setText(f"{self.device.width}x{self.device.height} 预览")
            return

        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.preview_label.clear()
            self.preview_label.setText("无法预览")
            return

        self.preview_label.setText("")
        self.preview_label.setPixmap(self._render_preview_pixmap(pixmap))

    def _render_preview_pixmap(self, source: QPixmap) -> QPixmap:
        if self.device is None:
            return source

        viewport_width = max(1, self.preview_label.width())
        viewport_height = max(1, self.preview_label.height())
        geometry = fit_preview_geometry(
            self.device.preview,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            padding=24,
        )
        canvas = QPixmap(geometry.screen_width, geometry.screen_height)
        canvas.fill(QColor("#0d0e0f"))
        source_for_device = source
        if self.device.preview.orientation:
            source_for_device = source.transformed(
                QTransform().rotate(self.device.preview.orientation),
                Qt.TransformationMode.SmoothTransformation,
            )

        fitted = source_for_device.scaled(
            geometry.screen_width,
            geometry.screen_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        offset_x = (geometry.screen_width - fitted.width()) // 2
        offset_y = (geometry.screen_height - fitted.height()) // 2

        painter = QPainter(canvas)
        painter.drawPixmap(offset_x, offset_y, fitted)
        painter.end()
        return canvas


class MainWindow(QMainWindow):
    def __init__(
        self,
        driver: DisplayDriver | None = None,
        telemetry_provider: Callable[[], SystemTelemetry] = collect_system_telemetry,
        asset_library: AssetLibrary | None = None,
        auto_refresh: bool = True,
        settings: GuiSettings | None = None,
    ) -> None:
        super().__init__()
        log_event("main_window_init", auto_refresh=auto_refresh)
        self.driver = driver or AsusLcIiiDriver()
        self.telemetry_provider = telemetry_provider
        self.asset_library = asset_library or AssetLibrary()
        self.settings = settings or load_settings()
        self.platform_adapter = current_platform()
        self._lcd_output_lock = threading.Lock()
        self.devices: list[DisplayDevice] = []
        self.image_path: Path | None = None
        self.selected_animation_path: Path | None = None
        self.latest_telemetry: SystemTelemetry | None = None
        self._animation_frames: list[AnimatedFrame] | None = None
        self._animation_frame_index = 0
        self._animation_device: DisplayDevice | None = None
        self._animation_device_key: tuple[str, tuple[Path, ...]] | None = None
        self._animation_thread: threading.Thread | None = None
        self._animation_stop_event: threading.Event | None = None
        self._animation_error: str | None = None
        self._animation_uploading = False
        self._monitor_thread: threading.Thread | None = None
        self._monitor_stop_event: threading.Event | None = None
        self._monitor_error: str | None = None
        self._monitor_frame_count = 0
        self._monitor_presented_frame_count = 0
        self._telemetry_thread: threading.Thread | None = None
        self._telemetry_result: SystemTelemetry | None = None
        self._telemetry_error: Exception | None = None
        self._cpu_power_permission_grant_attempted = False
        self._last_uploaded_frame_path = self.platform_adapter.last_frame_path()
        self._last_uploaded_device: DisplayDevice | None = None
        self._sleep_mode_active = False
        self._platform_diagnostics_dialog: PlatformDiagnosticsDialog | None = None
        stop_existing_keepalive()

        self.setWindowTitle("usb9-lcd")
        self.resize(1180, 760)
        self._apply_theme()

        self.navigation = QListWidget()
        self.navigation.setObjectName("SideNav")
        self.navigation.setFixedWidth(196)
        for label in ("首页", "屏幕", "风扇", "灯效", "设备", "联力无线", "设置"):
            self.navigation.addItem(QListWidgetItem(label))

        self.pages = QStackedWidget()
        self.monitor_page = MonitorPage(
            self.refresh_telemetry,
            self.upload_monitoring_frame,
            self.toggle_live_monitoring,
        )
        self._connect_monitor_customization()
        self.cpu_temp_value = self.monitor_page.cpu_temp_value
        self.gpu_temp_value = self.monitor_page.gpu_temp_value
        self.gpu_detail_value = self.monitor_page.gpu_detail_value
        self.monitor_preview = self.monitor_page.monitor_preview
        self.monitor_interval_combo = self.monitor_page.monitor_interval_combo
        self.monitor_interval_combo.setCurrentText(f"{self.settings.monitor.live_interval_seconds}s")
        self.monitor_interval_combo.currentIndexChanged.connect(self._monitor_interval_changed)
        self.monitor_page.monitor_palette_combo.blockSignals(True)
        self.monitor_page.monitor_palette_combo.setCurrentIndex(
            max(0, self.monitor_page.monitor_palette_combo.findData(self.settings.monitor.palette))
        )
        self.monitor_page.monitor_palette_combo.blockSignals(False)
        self.monitor_page.set_profile_names(sorted(self.settings.monitor.profiles), self.settings.monitor.active_profile)
        self.monitor_page.save_monitor_profile_button.clicked.connect(self.save_monitor_profile)
        self.monitor_page.load_monitor_profile_button.clicked.connect(self.load_monitor_profile)
        window_ref = ref(self)

        def select_asset_for_playback(path: Path) -> None:
            window = window_ref()
            if window is not None:
                window.select_asset_for_playback(path)

        def play_animation() -> None:
            window = window_ref()
            if window is not None:
                window.play_animation()

        def stop_animation() -> None:
            window = window_ref()
            if window is not None:
                window.stop_animation()

        self.lighting_page = LightingPage(
            auto_connect=False,
            settings=self.settings,
            sync_color_provider=self.lighting_sync_color,
        )
        self.asset_page = AssetLibraryPage(
            self.asset_library,
            auto_refresh_assets=False,
            select_asset_for_playback=select_asset_for_playback,
            play_animation=play_animation,
            stop_animation=stop_animation,
        )
        self.asset_list_text = self.asset_page.asset_list_text
        self.asset_links_text = self.asset_page.asset_links_text
        self.screen_page = self._screen_page()
        self.fan_page = FanControlHostPage(
            auto_load=False,
            auto_grant_pwm_permissions=True,
            auto_enable_pwm_control=False,
            auto_probe_hwmon_drivers=True,
            settings=self.settings,
        )
        self.lianli_page = LianLiWirelessPage(settings=self.settings)
        self.page_indexes = {
            "home": 0,
            "screen": 1,
            "monitor": 1,
            "assets": 1,
            "fan": 2,
            "lighting": 3,
            "device": 4,
            "lianli": 5,
            "settings": 6,
        }
        self.home_page = ControlCenterPage(
            self._navigate_to_page,
            self.upload_monitoring_frame,
            lambda: self.fan_page.reload_fan_control(interactive_driver_probe=True),
            self.lighting_page.connect_openrgb,
            self.sleep_all_off,
        )
        self.fan_page.status_changed.connect(self._fan_status_changed)
        self.lighting_page.status_changed.connect(self._lighting_status_changed)
        self.lianli_page.status_changed.connect(self._lianli_status_changed)
        self.home_page.update_fan_status(self.fan_page.home_status_text())
        self.home_page.update_lighting_status(self.lighting_page.home_status_text())
        self.home_page.update_lianli_status(self.lianli_page.home_status_text())
        self._refresh_home_permission_status()
        self.pages.addWidget(_scrollable_page(self.home_page))
        self.pages.addWidget(_scrollable_page(self.screen_page))
        self.pages.addWidget(_scrollable_page(self.fan_page))
        self.pages.addWidget(_scrollable_page(self.lighting_page))
        self.pages.addWidget(_scrollable_page(self._device_page()))
        self.pages.addWidget(_scrollable_page(self.lianli_page))
        self.pages.addWidget(_scrollable_page(self._settings_page()))

        self.navigation.currentRowChanged.connect(self._navigation_changed)
        self.navigation.setCurrentRow(0)

        shell = QFrame()
        shell.setObjectName("AppShell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(self._top_bar())

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        content.addWidget(self.navigation)
        content.addWidget(self.pages, 1)
        shell_layout.addLayout(content, 1)
        self.setCentralWidget(shell)
        self.statusBar().showMessage("就绪")

        self.telemetry_timer = QTimer(self)
        self.telemetry_timer.setInterval(2000)
        self.telemetry_timer.timeout.connect(self.request_telemetry_refresh)
        self.telemetry_poll_timer = QTimer(self)
        self.telemetry_poll_timer.setInterval(100)
        self.telemetry_poll_timer.timeout.connect(self._poll_telemetry_worker)
        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(250)
        self.animation_timer.timeout.connect(self._poll_animation_worker)
        self.monitor_upload_timer = QTimer(self)
        self.monitor_upload_timer.setInterval(250)
        self.monitor_upload_timer.timeout.connect(self._poll_monitor_worker)
        if auto_refresh:
            self.telemetry_timer.start()
            if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
                QTimer.singleShot(0, lambda: self.request_cpu_power_permission_grant(interactive=True))
            QTimer.singleShot(0, self.request_telemetry_refresh)
            QTimer.singleShot(0, self.refresh_devices)
            QTimer.singleShot(0, self.refresh_assets)
            if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
                QTimer.singleShot(0, lambda: self.fan_page.load_fan_control(interactive_driver_probe=True))

    def _navigate_to_page(self, page: str) -> None:
        tab_indexes = {"monitor": 0, "assets": 1}
        if page in tab_indexes and hasattr(self, "screen_tabs"):
            self.screen_tabs.setCurrentIndex(tab_indexes[page])
            page = "screen"
        index = self.page_indexes.get(page, -1)
        if 0 <= index < self.pages.count():
            self.navigation.setCurrentRow(index)

    def _navigation_changed(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if index == self.page_indexes.get("fan"):
            self.fan_page.load_fan_control(interactive_driver_probe=False)

    def _fan_status_changed(self, text: str) -> None:
        self.home_page.update_fan_status(text)
        self._refresh_home_permission_status()

    def _lighting_status_changed(self, text: str) -> None:
        self.home_page.update_lighting_status(text)

    def _lianli_status_changed(self, text: str) -> None:
        self.home_page.update_lianli_status(text)

    def _refresh_home_permission_status(self) -> None:
        if not hasattr(self, "home_page"):
            return
        self.home_page.update_permission_status(summarize_permission_status(self._system_status_snapshot()))

    def _system_status_snapshot(self) -> SystemStatusSnapshot:
        events = self.home_page.recent_events() if hasattr(self, "home_page") else []
        log_lines = [_compact_log_line(line) for line in recent_log_lines(limit=3)]
        recent_events = events + [f"日志: {line}" for line in log_lines if line not in events]
        return SystemStatusSnapshot(
            components=self._system_component_status_items(log_lines),
            permissions=self._permission_status_items(),
            recent_events=recent_events[:6],
        )

    def _system_component_status_items(self, log_lines: list[str]) -> list[StatusItem]:
        components = [
            StatusItem(
                "LCD 设备",
                "ok" if self.devices else "warn",
                f"{len(self.devices)} 个设备" if self.devices else "未发现设备",
            )
        ]
        telemetry = self.latest_telemetry
        if telemetry is None:
            components.extend(
                [
                    StatusItem("CPU", "info", "等待遥测采集"),
                    StatusItem("GPU", "info", "等待遥测采集"),
                ]
            )
        else:
            components.extend(
                [
                    StatusItem(
                        "CPU",
                        "ok" if telemetry.cpu.available else "warn",
                        _cpu_status_detail(telemetry),
                    ),
                    StatusItem(
                        "GPU",
                        "ok" if telemetry.gpu.available else "warn",
                        _gpu_status_detail(telemetry),
                    ),
                ]
            )
        components.extend(
            [
                StatusItem("普通风扇", _state_from_summary(self.fan_page.home_status_text()), self.fan_page.home_status_text()),
                StatusItem("灯效", _state_from_summary(self.lighting_page.home_status_text()), self.lighting_page.home_status_text()),
                StatusItem("联力无线", _state_from_summary(self.lianli_page.home_status_text()), self.lianli_page.home_status_text()),
                StatusItem(
                    "GUI 日志",
                    "ok" if log_lines else "info",
                    f"最近 {len(log_lines)} 条可读" if log_lines else "暂无日志或未配置日志路径",
                ),
            ]
        )
        return components

    def _permission_status_items(self) -> list[StatusItem]:
        items: list[StatusItem] = []
        if self.devices:
            writable_count = sum(1 for device in self.devices if device.connection.writable)
            items.append(
                StatusItem(
                    "LCD 写权限",
                    "ok" if writable_count else "warn",
                    f"{writable_count}/{len(self.devices)} 个设备可写",
                )
            )
        else:
            items.append(StatusItem("LCD 写权限", "info", "等待设备扫描"))

        if sys.platform.startswith("linux"):
            power_paths = cpu_power_permission_paths()
            items.append(
                StatusItem(
                    "CPU 功耗权限",
                    "warn" if power_paths else "ok",
                    f"{len(power_paths)} 个 powercap 文件需授权" if power_paths else "无需授权或已可读",
                )
            )
        else:
            items.append(StatusItem("CPU 功耗权限", "info", "非 Linux 平台无需 powercap 授权"))

        pwm_paths: list[Path] = []
        pwm_permission_paths = getattr(self.fan_page, "_pwm_permission_paths", None)
        if callable(pwm_permission_paths):
            try:
                pwm_paths = list(pwm_permission_paths())
            except Exception as error:  # pragma: no cover - defensive diagnostic boundary
                log_exception("fan_pwm_permission_status_failed", error)
        fan_status = self.fan_page.home_status_text()
        if pwm_paths:
            items.append(StatusItem("PWM 写权限", "warn", f"{len(pwm_paths)} 个 pwm* 文件需授权"))
        elif "可控" in fan_status:
            items.append(StatusItem("PWM 写权限", "ok", fan_status))
        else:
            items.append(StatusItem("PWM 写权限", "info", "未发现需要授权的 PWM 文件"))
        return items

    def _apply_theme(self) -> None:
        self.setStyleSheet(gui_stylesheet())

    def _top_bar(self) -> QFrame:
        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        layout = QHBoxLayout(top_bar)
        layout.setContentsMargins(20, 12, 20, 12)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        app_title = QLabel("USB9 LCD Control Center")
        app_title.setObjectName("AppTitle")
        app_subtitle = QLabel("屏幕内容、硬件监控、风扇曲线、灯效与联力无线")
        app_subtitle.setObjectName("PageSubtitle")
        title_box.addWidget(app_title)
        title_box.addWidget(app_subtitle)
        layout.addLayout(title_box, 1)

        self.device_summary_label = QLabel("未发现设备")
        self.device_summary_label.setObjectName("DeviceBadge")
        layout.addWidget(self.device_summary_label)
        self.sleep_all_off_button = QPushButton("睡眠全关")
        self.sleep_all_off_button.setObjectName("DangerButton")
        self.sleep_all_off_button.setToolTip("关闭屏幕并尝试关闭 OpenRGB 和联力无线灯光")
        self.sleep_all_off_button.clicked.connect(self.sleep_all_off)
        layout.addWidget(self.sleep_all_off_button)
        platform_diagnostics_button = QPushButton("平台诊断")
        platform_diagnostics_button.clicked.connect(self.open_platform_diagnostics)
        layout.addWidget(platform_diagnostics_button)
        return top_bar

    def _screen_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(16)

        header_row = QHBoxLayout()
        title_box = QVBoxLayout()
        header = QLabel("USB9 屏幕")
        header.setObjectName("PageTitle")
        subtitle = QLabel("集中管理 LCD 设备、硬件监控画面和本地图片/GIF 素材")
        subtitle.setObjectName("PageSubtitle")
        title_box.addWidget(header)
        title_box.addWidget(subtitle)
        header_row.addLayout(title_box, 1)
        refresh_button = QPushButton("刷新设备")
        refresh_button.clicked.connect(self.refresh_devices)
        header_row.addWidget(refresh_button)
        layout.addLayout(header_row)

        device_panel = QFrame()
        device_panel.setObjectName("MetricCard")
        device_layout = QGridLayout(device_panel)
        device_layout.setContentsMargins(14, 12, 14, 12)
        device_layout.setHorizontalSpacing(10)
        device_layout.setVerticalSpacing(8)

        device_title = QLabel("LCD 设备与静态图片")
        device_title.setObjectName("SectionLabel")
        self.device_combo = QComboBox()
        self.device_combo.currentIndexChanged.connect(self._selected_device_changed)
        self.device_status_label = QLabel("未发现设备")
        self.device_status_label.setWordWrap(True)
        self.device_status_label.setObjectName("FieldHint")
        self.image_path_label = QLabel("未选择图片")
        self.image_path_label.setWordWrap(True)
        self.image_path_label.setObjectName("FilePathLabel")
        choose_button = QPushButton("选择图片")
        choose_button.clicked.connect(self.choose_image)

        self.fit_combo = QComboBox()
        self.fit_combo.addItems(["cover", "contain", "stretch"])

        self.rotate_combo = QComboBox()
        self.rotate_combo.addItems(["0", "90", "180", "270"])
        self.rotate_combo.setCurrentText("180")

        self.background_input = QLineEdit("#000000")

        upload_button = QPushButton("发送静态图片")
        upload_button.setObjectName("PrimaryButton")
        upload_button.clicked.connect(self.upload_selected_image)

        self.preview = PreviewWidget()

        device_layout.addWidget(device_title, 0, 0, 1, 4)
        device_layout.addWidget(QLabel("目标设备"), 1, 0)
        device_layout.addWidget(self.device_combo, 1, 1, 1, 3)
        device_layout.addWidget(self.device_status_label, 2, 0, 1, 4)
        device_layout.addWidget(QLabel("图片"), 3, 0)
        device_layout.addWidget(choose_button, 3, 1)
        device_layout.addWidget(self.image_path_label, 3, 2, 1, 2)
        device_layout.addWidget(QLabel("适配"), 4, 0)
        device_layout.addWidget(self.fit_combo, 4, 1)
        device_layout.addWidget(QLabel("旋转"), 4, 2)
        device_layout.addWidget(self.rotate_combo, 4, 3)
        device_layout.addWidget(QLabel("背景"), 5, 0)
        device_layout.addWidget(self.background_input, 5, 1)
        device_layout.addWidget(upload_button, 5, 2, 1, 2)
        device_layout.addWidget(self.preview, 0, 4, 6, 1)
        device_layout.setColumnStretch(2, 1)
        device_layout.setColumnStretch(4, 1)
        layout.addWidget(device_panel)

        self.screen_tabs = QTabWidget()
        self.screen_tabs.addTab(self.monitor_page, "监控画面")
        self.screen_tabs.addTab(self.asset_page, "素材库")
        layout.addWidget(self.screen_tabs, 1)
        return page

    def _device_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(16)
        header_row = QHBoxLayout()
        title_box = QVBoxLayout()
        header = QLabel("设备")
        header.setObjectName("PageTitle")
        subtitle = QLabel("查看 LCD 设备发现结果、连接路径和协议诊断输出")
        subtitle.setObjectName("PageSubtitle")
        title_box.addWidget(header)
        title_box.addWidget(subtitle)
        header_row.addLayout(title_box, 1)
        diagnostics_button = QPushButton("运行诊断")
        diagnostics_button.clicked.connect(self.run_diagnostics)
        header_row.addWidget(diagnostics_button)
        layout.addLayout(header_row)

        details_panel = QFrame()
        details_panel.setObjectName("MetricCard")
        details_layout = QVBoxLayout(details_panel)
        details_title = QLabel("发现到的设备")
        details_title.setObjectName("SectionLabel")
        self.device_details = QTextEdit()
        self.device_details.setReadOnly(True)
        details_layout.addWidget(details_title)
        details_layout.addWidget(self.device_details, 1)
        layout.addWidget(details_panel, 1)

        diagnostics_panel = QFrame()
        diagnostics_panel.setObjectName("MetricCard")
        diagnostics_layout = QVBoxLayout(diagnostics_panel)
        diagnostics_title = QLabel("诊断")
        diagnostics_title.setObjectName("SectionLabel")
        self.diagnostics_text = QTextEdit()
        self.diagnostics_text.setReadOnly(True)
        self.diagnostics_text.setMaximumHeight(170)
        diagnostics_layout.addWidget(diagnostics_title)
        diagnostics_layout.addWidget(self.diagnostics_text)
        layout.addWidget(diagnostics_panel)
        return page

    def _settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 22, 24, 24)
        title = QLabel("设置")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        openrgb_panel = QFrame()
        openrgb_panel.setObjectName("MetricCard")
        form = QFormLayout(openrgb_panel)
        self.settings_openrgb_path = QLineEdit(self.settings.openrgb.app_path)
        self.settings_openrgb_autostart = QCheckBox("启动 GUI 时自动启动/连接 OpenRGB Server")
        self.settings_openrgb_autostart.setChecked(self.settings.openrgb.auto_start_server)
        self.settings_openrgb_port = QSpinBox()
        self.settings_openrgb_port.setRange(1, 65535)
        self.settings_openrgb_port.setValue(self.settings.openrgb.port)
        form.addRow("OpenRGB 路径", self.settings_openrgb_path)
        form.addRow("OpenRGB 端口", self.settings_openrgb_port)
        form.addRow("", self.settings_openrgb_autostart)
        layout.addWidget(openrgb_panel)

        behavior_panel = QFrame()
        behavior_panel.setObjectName("MetricCard")
        behavior_form = QFormLayout(behavior_panel)
        self.settings_default_argb_size = QSpinBox()
        self.settings_default_argb_size.setRange(1, 500)
        self.settings_default_argb_size.setValue(self.settings.lighting.argb_zone_size)
        self.settings_keepalive = QCheckBox("退出 GUI 后保持最后一帧")
        self.settings_keepalive.setChecked(self.settings.keepalive_enabled)
        behavior_form.addRow("默认 ARGB 灯珠数量", self.settings_default_argb_size)
        behavior_form.addRow("", self.settings_keepalive)
        layout.addWidget(behavior_panel)

        save_button = QPushButton("保存设置")
        save_button.setObjectName("PrimaryButton")
        save_button.clicked.connect(self.save_settings_page)
        layout.addWidget(save_button)
        self.settings_save_status_label = QLabel(f"配置文件：{DEFAULT_SETTINGS_PATH}")
        self.settings_save_status_label.setObjectName("FieldHint")
        self.settings_save_status_label.setWordWrap(True)
        layout.addWidget(self.settings_save_status_label)
        maintenance_row = QHBoxLayout()
        clear_cache_button = QPushButton("清理 GIF 缓存")
        clear_cache_button.clicked.connect(self.clear_gif_cache)
        clear_logs_button = QPushButton("清理日志")
        clear_logs_button.clicked.connect(self.clear_logs)
        reset_settings_button = QPushButton("重置配置")
        reset_settings_button.clicked.connect(self.reset_settings_file)
        maintenance_row.addWidget(clear_cache_button)
        maintenance_row.addWidget(clear_logs_button)
        maintenance_row.addWidget(reset_settings_button)
        layout.addLayout(maintenance_row)
        layout.addStretch(1)
        return page

    def _show_config_saved_feedback(self, message: str) -> None:
        stamped = f"{message}（{time.strftime('%H:%M:%S')}）"
        if hasattr(self, "settings_save_status_label"):
            self.settings_save_status_label.setText(stamped)
        self.statusBar().showMessage(message)
        self.home_page.add_event(message)

    def save_settings_page(self) -> None:
        self.settings.openrgb.app_path = self.settings_openrgb_path.text().strip()
        self.settings.openrgb.port = self.settings_openrgb_port.value()
        self.settings.openrgb.auto_start_server = self.settings_openrgb_autostart.isChecked()
        self.settings.lighting.argb_zone_size = self.settings_default_argb_size.value()
        self.settings.keepalive_enabled = self.settings_keepalive.isChecked()
        self.lighting_page.argb_zone_size.setValue(self.settings.lighting.argb_zone_size)
        self.lighting_page.openrgb_port_input.setValue(self.settings.openrgb.port)
        save_settings(self.settings)
        self._show_config_saved_feedback("设置已保存")

    def clear_gif_cache(self) -> None:
        cache = self.platform_adapter.gif_preview_cache_dir()
        if cache.exists():
            shutil.rmtree(cache)
        self.statusBar().showMessage("GIF 缓存已清理")
        self.home_page.add_event("GIF 缓存已清理")

    def clear_logs(self) -> None:
        for log_path in self.platform_adapter.log_dir().glob("*.log"):
            try:
                log_path.unlink()
            except OSError:
                pass
        self.statusBar().showMessage("日志已清理")
        self.home_page.add_event("日志已清理")

    def open_platform_diagnostics(self) -> None:
        if self._platform_diagnostics_dialog is not None and self._platform_diagnostics_dialog.isVisible():
            self._platform_diagnostics_dialog.raise_()
            self._platform_diagnostics_dialog.activateWindow()
            return
        self._platform_diagnostics_dialog = PlatformDiagnosticsDialog(
            self.settings,
            self,
            status_provider=self._system_status_snapshot,
        )
        self._platform_diagnostics_dialog.destroyed.connect(lambda: setattr(self, "_platform_diagnostics_dialog", None))
        self._platform_diagnostics_dialog.show()

    def reset_settings_file(self) -> None:
        try:
            DEFAULT_SETTINGS_PATH.unlink()
        except FileNotFoundError:
            pass
        self.settings = GuiSettings()
        save_settings(self.settings)
        self._show_config_saved_feedback("配置已重置，重启 GUI 后完全生效")

    def refresh_telemetry(self) -> None:
        try:
            telemetry = self.telemetry_provider()
        except Exception:  # pragma: no cover - defensive UI boundary
            self.latest_telemetry = None
            self.monitor_page.show_unavailable()
            return

        self.latest_telemetry = telemetry
        self.monitor_page.update_telemetry(telemetry)
        self.home_page.update_telemetry(telemetry)
        self._refresh_home_permission_status()
        self.update_monitor_preview()

    def request_cpu_power_permission_grant(self, *, interactive: bool = True) -> None:
        if self._cpu_power_permission_grant_attempted:
            return
        self._cpu_power_permission_grant_attempted = True
        if not sys.platform.startswith("linux"):
            return
        if os.environ.get("LUMEN_HUB_SKIP_CPU_POWER_PERMISSION_GRANT") == "1":
            return
        paths = cpu_power_permission_paths()
        if not paths:
            return
        run_privileged_shell = getattr(self.fan_page, "_run_privileged_shell_compat", None)
        if not callable(run_privileged_shell):
            self.home_page.add_event("CPU 功耗权限授权不可用")
            self._refresh_home_permission_status()
            return
        ok, output = run_privileged_shell(
            cpu_power_permission_shell(paths),
            timeout=30,
            interactive=interactive,
            action_label="授权 CPU 功耗权限",
        )
        if ok:
            self.statusBar().showMessage(f"CPU 功耗权限已授权：{len(paths)} 个文件")
            self.home_page.add_event("CPU 功耗权限已授权")
            self._refresh_home_permission_status()
            return
        message = output or "CPU 功耗权限授权失败"
        self.statusBar().showMessage(message)
        self.home_page.add_event(message)
        self._refresh_home_permission_status()

    def request_telemetry_refresh(self) -> None:
        thread = self._telemetry_thread
        if thread is not None and thread.is_alive():
            log_event("telemetry_refresh_skipped_busy")
            return
        log_event("telemetry_async_refresh_started")
        self._telemetry_result = None
        self._telemetry_error = None
        self._telemetry_thread = threading.Thread(
            target=self._run_telemetry_refresh,
            name="usb9-lcd-telemetry-refresh",
            daemon=True,
        )
        self._telemetry_thread.start()
        self.telemetry_poll_timer.start()

    def _run_telemetry_refresh(self) -> None:
        try:
            self._telemetry_result = self.telemetry_provider()
        except Exception as error:  # pragma: no cover - polled by GUI
            self._telemetry_error = error
            log_exception("telemetry_async_refresh_failed", error)

    def _poll_telemetry_worker(self) -> None:
        thread = self._telemetry_thread
        if thread is not None and thread.is_alive():
            return
        self.telemetry_poll_timer.stop()
        self._telemetry_thread = None
        if self._telemetry_error is not None:
            self.latest_telemetry = None
            self.monitor_page.show_unavailable()
            self._telemetry_error = None
            return
        telemetry = self._telemetry_result
        self._telemetry_result = None
        if telemetry is None:
            return
        self.latest_telemetry = telemetry
        self.monitor_page.update_telemetry(telemetry)
        self.home_page.update_telemetry(telemetry)
        self._refresh_home_permission_status()
        self.update_monitor_preview()

    def _connect_monitor_customization(self) -> None:
        self.monitor_page.monitor_background_combo.currentIndexChanged.connect(self.update_monitor_preview)
        self.monitor_page.monitor_layout_combo.currentIndexChanged.connect(self.update_monitor_preview)
        self.monitor_page.monitor_palette_combo.currentIndexChanged.connect(self._monitor_palette_changed)
        for checkbox in (
            self.monitor_page.show_cpu_temp_checkbox,
            self.monitor_page.show_gpu_temp_checkbox,
            self.monitor_page.show_gpu_load_checkbox,
            self.monitor_page.show_gpu_power_checkbox,
            self.monitor_page.show_vram_checkbox,
            self.monitor_page.show_gpu_clock_checkbox,
            self.monitor_page.show_cpu_load_checkbox,
            self.monitor_page.show_time_checkbox,
        ):
            checkbox.stateChanged.connect(self.update_monitor_preview)

    def update_monitor_preview(self, *args) -> None:  # noqa: ANN002
        device = self._selected_device()
        telemetry = self.latest_telemetry
        if device is None or telemetry is None:
            return
        try:
            image = render_monitoring_image(telemetry, device, self.monitor_page.render_settings())
        except Exception as error:
            log_exception("update_monitor_preview_failed", error)
            return
        self.monitor_page.update_preview_image(image)

    def _monitor_interval_changed(self) -> None:
        interval = int(self.monitor_interval_combo.currentData() or 1)
        self.settings.monitor.live_interval_seconds = interval
        save_settings(self.settings)
        self._show_config_saved_feedback(f"监控间隔已保存：{interval}s")

    def _monitor_palette_changed(self) -> None:
        self.settings.monitor.palette = str(self.monitor_page.monitor_palette_combo.currentData() or "neon")
        save_settings(self.settings)
        self._show_config_saved_feedback(f"监控调色板已保存：{self.settings.monitor.palette}")
        self.update_monitor_preview()

    def save_monitor_profile(self) -> None:
        name = self.monitor_page.monitor_profile_combo.currentText().strip() or "默认配置"
        profile = {
            "layout": self.monitor_page.monitor_layout_combo.currentData() or "balanced",
            "palette": self.monitor_page.monitor_palette_combo.currentData() or "neon",
            "background": self.monitor_page.monitor_background_combo.currentData() or "",
            "show_cpu_temp": self.monitor_page.show_cpu_temp_checkbox.isChecked(),
            "show_gpu_temp": self.monitor_page.show_gpu_temp_checkbox.isChecked(),
            "show_gpu_load": self.monitor_page.show_gpu_load_checkbox.isChecked(),
            "show_gpu_power": self.monitor_page.show_gpu_power_checkbox.isChecked(),
            "show_vram": self.monitor_page.show_vram_checkbox.isChecked(),
            "show_gpu_clock": self.monitor_page.show_gpu_clock_checkbox.isChecked(),
            "show_cpu_load": self.monitor_page.show_cpu_load_checkbox.isChecked(),
            "show_time": self.monitor_page.show_time_checkbox.isChecked(),
        }
        self.settings.monitor.profiles[name] = profile
        self.settings.monitor.active_profile = name
        save_settings(self.settings)
        self.monitor_page.set_profile_names(sorted(self.settings.monitor.profiles), name)
        self._show_config_saved_feedback(f"监控配置已保存：{name}")

    def load_monitor_profile(self) -> None:
        name = self.monitor_page.monitor_profile_combo.currentText().strip()
        profile = self.settings.monitor.profiles.get(name)
        if not profile:
            self.statusBar().showMessage("请选择要加载的监控配置")
            return
        self.monitor_page.monitor_layout_combo.setCurrentIndex(
            max(0, self.monitor_page.monitor_layout_combo.findData(profile.get("layout", "balanced")))
        )
        self.monitor_page.monitor_palette_combo.setCurrentIndex(
            max(0, self.monitor_page.monitor_palette_combo.findData(profile.get("palette", "neon")))
        )
        background = str(profile.get("background", ""))
        self.monitor_page.monitor_background_combo.setCurrentIndex(
            max(0, self.monitor_page.monitor_background_combo.findData(background))
        )
        for key, checkbox in (
            ("show_cpu_temp", self.monitor_page.show_cpu_temp_checkbox),
            ("show_gpu_temp", self.monitor_page.show_gpu_temp_checkbox),
            ("show_gpu_load", self.monitor_page.show_gpu_load_checkbox),
            ("show_gpu_power", self.monitor_page.show_gpu_power_checkbox),
            ("show_vram", self.monitor_page.show_vram_checkbox),
            ("show_gpu_clock", self.monitor_page.show_gpu_clock_checkbox),
            ("show_cpu_load", self.monitor_page.show_cpu_load_checkbox),
            ("show_time", self.monitor_page.show_time_checkbox),
        ):
            checkbox.setChecked(bool(profile.get(key, checkbox.isChecked())))
        self.settings.monitor.active_profile = name
        self.settings.monitor.palette = str(self.monitor_page.monitor_palette_combo.currentData() or "neon")
        save_settings(self.settings)
        self.update_monitor_preview()
        self._show_config_saved_feedback(f"监控配置已加载：{name}")

    def lighting_sync_color(self, mode_index: int) -> str | None:
        if mode_index == 1 and self.latest_telemetry is not None:
            return self._temperature_color(self.latest_telemetry.cpu.package_temperature_c)
        if mode_index == 2 and self.latest_telemetry is not None:
            return self._temperature_color(self.latest_telemetry.gpu.temperature_c)
        if mode_index == 3 and self.latest_telemetry is not None:
            return self._utilization_color(self.latest_telemetry.cpu.utilization_percent)
        if mode_index == 4 and self.latest_telemetry is not None:
            return self._utilization_color(self.latest_telemetry.gpu.utilization_percent)
        if mode_index == 5:
            return self._current_asset_color()
        return None

    def _temperature_color(self, temperature: float | int | None) -> str | None:
        if temperature is None:
            return None
        stops = (
            (35.0, (0, 136, 255)),
            (55.0, (0, 229, 255)),
            (70.0, (255, 214, 10)),
            (85.0, (255, 45, 85)),
        )
        value = float(temperature)
        if value <= stops[0][0]:
            return _hex_color(stops[0][1])
        for (lower_temp, lower_color), (upper_temp, upper_color) in zip(stops, stops[1:], strict=False):
            if value <= upper_temp:
                ratio = (value - lower_temp) / (upper_temp - lower_temp)
                return _hex_color(_mix_color(lower_color, upper_color, ratio))
        return _hex_color(stops[-1][1])

    def _utilization_color(self, utilization_percent: float | int | None) -> str | None:
        if utilization_percent is None:
            return None
        stops = (
            (0.0, (0, 136, 255)),
            (40.0, (0, 229, 255)),
            (75.0, (255, 214, 10)),
            (100.0, (255, 45, 85)),
        )
        value = max(0.0, min(100.0, float(utilization_percent)))
        for (lower_load, lower_color), (upper_load, upper_color) in zip(stops, stops[1:], strict=False):
            if value <= upper_load:
                ratio = (value - lower_load) / (upper_load - lower_load)
                return _hex_color(_mix_color(lower_color, upper_color, ratio))
        return _hex_color(stops[-1][1])

    def _current_asset_color(self) -> str | None:
        path = self.selected_animation_path or self.image_path
        if path is None or not path.is_file():
            return None
        try:
            with Image.open(path) as image:
                rgb = image.convert("RGB").resize((1, 1))
                red, green, blue = rgb.getpixel((0, 0))
        except OSError:
            return None
        return f"#{red:02x}{green:02x}{blue:02x}"

    def refresh_assets(self) -> None:
        self.asset_page.refresh_assets()

    def import_asset(self) -> None:
        self.asset_page.import_asset()

    def select_asset_for_playback(self, path: str | Path) -> None:
        self.selected_animation_path = Path(path)
        self.statusBar().showMessage(f"已选择动画素材：{self.selected_animation_path.name}")

    def _open_upload_session(self, device: DisplayDevice):
        open_upload_session = getattr(self.driver, "open_upload_session", None)
        if callable(open_upload_session):
            return open_upload_session(device)
        return _OneShotUploadSession(self.driver, device)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        log_event("main_window_close")
        for name, cleanup in (
            ("asset_preview_release_failed", self.asset_page.release_preview_resources),
            ("lighting_release_failed", self.lighting_page.release_lighting_resources),
            ("fan_release_failed", self.fan_page.release),
            ("animation_stop_failed", self.stop_animation),
            ("live_monitor_stop_failed", lambda: self.stop_live_monitoring(message="")),
            ("keepalive_start_failed", self._start_keepalive_after_exit),
        ):
            try:
                cleanup()
            except Exception as error:  # pragma: no cover - defensive shutdown
                log_exception(name, error)
        self.telemetry_timer.stop()
        self.telemetry_poll_timer.stop()
        super().closeEvent(event)

    def _remember_uploaded_frame(self, device: DisplayDevice, frame: bytes, *, sleep_mode: bool = False) -> None:
        self._sleep_mode_active = sleep_mode
        try:
            self._last_uploaded_frame_path.parent.mkdir(parents=True, exist_ok=True)
            self._last_uploaded_frame_path.write_bytes(frame)
            self._last_uploaded_device = device
            log_event("last_uploaded_frame_saved", byte_count=len(frame), device=device.display_name)
        except OSError as error:
            log_exception("last_uploaded_frame_save_failed", error)

    def _start_keepalive_after_exit(self) -> None:
        device = self._last_uploaded_device
        if self._sleep_mode_active:
            return
        if not self.settings.keepalive_enabled:
            return
        if device is None or device.driver_id != "asus.lc_iii":
            return
        if not self._last_uploaded_frame_path.is_file():
            return

        log_event("keepalive_starting", frame=str(self._last_uploaded_frame_path))
        try:
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "usb9_lcd.keepalive",
                    str(self._last_uploaded_frame_path),
                    "--interval",
                    "1.0",
                    "--pid-file",
                    str(DEFAULT_PID_FILE),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            log_exception("keepalive_start_failed", error)

    def refresh_devices(self) -> None:
        log_event("refresh_devices_started")
        selected_device = self._selected_device()
        selected_key = self._device_selection_key(selected_device) if selected_device is not None else None
        try:
            self.devices = self.driver.discover()
        except Exception as error:  # pragma: no cover - defensive UI boundary
            log_exception("refresh_devices_failed", error)
            self.devices = []
            self.device_combo.clear()
            self.home_page.update_device(None)
            self.home_page.add_event("设备发现失败")
            self._stop_animation_if_target_missing()
            message = self._friendly_error(error)
            self.device_status_label.setText(f"设备发现失败：{message}")
            self.device_details.setPlainText(f"设备发现失败：{message}")
            self.preview.set_device(None)
            self.statusBar().showMessage(f"设备发现失败：{message}")
            self._refresh_home_permission_status()
            return

        self.device_combo.clear()
        for device in self.devices:
            self.device_combo.addItem(device.display_name)

        if not self.devices:
            log_event("refresh_devices_empty")
            self.device_summary_label.setText("未发现设备")
            self.home_page.update_device(None)
            self.home_page.add_event("未发现 LCD 设备")
            self.device_status_label.setText(f"{self.driver.display_name}：未发现设备")
            self.device_details.setPlainText("未发现设备")
            self.preview.set_device(None)
            self._stop_animation_if_target_missing()
            self.statusBar().showMessage("未发现设备")
            self._refresh_home_permission_status()
            return

        selected_index = 0
        if selected_key is not None:
            selected_index = next(
                (
                    index
                    for index, device in enumerate(self.devices)
                    if self._device_selection_key(device) == selected_key
                ),
                0,
            )

        self.device_combo.setCurrentIndex(selected_index)
        self._selected_device_changed(selected_index)
        self.home_page.update_device(self.devices[selected_index])
        self.home_page.add_event(f"发现 {len(self.devices)} 个 LCD 设备")
        log_event("refresh_devices_finished", count=len(self.devices), selected_index=selected_index)
        self.statusBar().showMessage(f"发现 {len(self.devices)} 个设备")
        self._refresh_home_permission_status()
        self.run_diagnostics()

    def choose_image(self) -> None:
        log_event("choose_image_dialog_open")
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*)",
        )
        if selected:
            log_event("choose_image_selected", path=selected)
            self.set_image_path(selected)

    def set_image_path(self, image_path: str | Path) -> None:
        self.image_path = Path(image_path)
        log_event("set_image_path", path=str(self.image_path))
        self.image_path_label.setText(str(self.image_path))
        self.preview.set_image_path(self.image_path)
        self.statusBar().showMessage("已选择图片")

    def upload_selected_image(self) -> None:
        log_event("upload_selected_image_started", image_path=str(self.image_path) if self.image_path else "")
        device = self._selected_device()
        if device is None:
            self.statusBar().showMessage("请先选择设备")
            return
        if self.image_path is None:
            self.statusBar().showMessage("请先选择图片")
            return

        try:
            self.stop_live_monitoring(message="")
            self.stop_animation(message="")
            settings = ImageRenderSettings(
                fit=cast(FitMode, self.fit_combo.currentText()),
                rotate=cast(Rotation, int(self.rotate_combo.currentText())),
                background=self.background_input.text().strip() or "#000000",
            )
            log_event(
                "upload_selected_image_render_settings",
                fit=settings.fit,
                rotate=settings.rotate,
                background=settings.background,
                pixel_format=device.pixel_format.value,
            )
            rendered = render_static_image(self.image_path, device, settings)
            with self._lcd_output_lock:
                self.driver.upload_static_frame(device, rendered.frame)
            self._remember_uploaded_frame(device, rendered.frame)
        except Exception as error:
            log_exception("upload_selected_image_failed", error)
            self.statusBar().showMessage(f"上传失败：{self._friendly_error(error)}")
            return

        log_event("upload_selected_image_finished", byte_count=rendered.byte_count)
        self.statusBar().showMessage(
            f"上传成功：{rendered.width}x{rendered.height}，{rendered.byte_count} 字节"
        )

    def upload_monitoring_frame(self) -> None:
        log_event("upload_monitoring_frame_started")
        device = self._selected_device()
        if device is None:
            self.statusBar().showMessage("请先选择设备")
            return

        try:
            self.stop_animation(message="")
            self.monitor_page.upload_monitor_button.setEnabled(False)
            self.statusBar().showMessage("正在上传监控画面...")
            telemetry = self.latest_telemetry
            if telemetry is None:
                telemetry = self.telemetry_provider()
                self.latest_telemetry = telemetry
                self.monitor_page.update_telemetry(telemetry)
            frame = render_monitoring_frame(telemetry, device, self.monitor_page.render_settings())
            with self._lcd_output_lock:
                self.driver.upload_static_frame(device, frame)
            self._remember_uploaded_frame(device, frame)
        except Exception as error:
            log_exception("upload_monitoring_frame_failed", error)
            self.statusBar().showMessage(f"监控画面上传失败：{self._friendly_error(error)}")
            self.home_page.add_event("监控画面上传失败")
            self.monitor_page.upload_monitor_button.setEnabled(True)
            return

        self.monitor_page.upload_monitor_button.setEnabled(True)
        log_event("upload_monitoring_frame_finished", byte_count=len(frame))
        self.home_page.add_event("监控画面已上传到 LCD")
        self.statusBar().showMessage(f"监控画面已上传：{device.width}x{device.height}，{len(frame)} 字节")

    def sleep_all_off(self) -> None:
        log_event("sleep_all_off_started")
        self.home_page.set_mode_indicator("睡眠")
        self.stop_live_monitoring(message="")
        self.stop_animation(message="")
        devices = self._sleep_mode_devices()
        screen_count = 0
        errors: list[str] = []
        for device in devices:
            try:
                frame = self._black_frame_for_device(device)
                with self._lcd_output_lock:
                    self.driver.upload_static_frame(device, frame)
                    self._set_display_sleep_state(device)
                self._remember_uploaded_frame(device, frame, sleep_mode=True)
                screen_count += 1
            except Exception as error:
                log_exception("sleep_all_off_lcd_failed", error)
                errors.append(f"{device.display_name}: {self._friendly_error(error)}")

        self.lighting_page.turn_off_all_lighting()
        self.lianli_page.turn_off_all_lighting()
        if errors:
            self.home_page.add_event(f"睡眠全关部分失败：{errors[0]}")
            self.statusBar().showMessage(
                f"睡眠全关部分失败：屏幕 {screen_count}/{len(devices)} 个已黑屏；{errors[0]}"
            )
            return
        self.home_page.add_event("睡眠全关已执行")
        self.statusBar().showMessage(
            f"睡眠全关已执行：屏幕 {screen_count}/{len(devices)} 个已黑屏，OpenRGB/联力灯光正在关闭"
        )

    def _sleep_mode_devices(self) -> list[DisplayDevice]:
        if self.devices:
            return list(self.devices)
        try:
            self.refresh_devices()
        except Exception as error:  # pragma: no cover - refresh_devices owns normal errors
            log_exception("sleep_all_off_discovery_failed", error)
        return list(self.devices)

    def _black_frame_for_device(self, device: DisplayDevice) -> bytes:
        if device.pixel_format == PixelFormat.RGB565:
            return b"\x00\x00" * (device.width * device.height)
        if device.pixel_format == PixelFormat.JPEG:
            image = Image.new("RGB", (device.width, device.height), (0, 0, 0))
            return image_to_jpeg_bytes(
                image,
                FrameConfig(width=device.width, height=device.height, fit="stretch", rotate=0),
                quality=95,
            )
        raise ValueError(f"不支持的屏幕像素格式：{device.pixel_format.value}")

    def _set_display_sleep_state(self, device: DisplayDevice) -> None:
        set_brightness = getattr(self.driver, "set_display_brightness", None)
        if callable(set_brightness):
            set_brightness(device, 0)
        set_power = getattr(self.driver, "set_display_power", None)
        if callable(set_power):
            set_power(device, False)

    def toggle_live_monitoring(self) -> None:
        if self._monitor_thread is not None:
            self.stop_live_monitoring()
            return
        self.start_live_monitoring()

    def start_live_monitoring(self) -> None:
        device = self._selected_device()
        if device is None:
            self.statusBar().showMessage("请先选择设备")
            return

        self.stop_animation(message="")
        self._monitor_error = None
        self._monitor_frame_count = 0
        interval_seconds = int(self.monitor_interval_combo.currentData() or 1)
        stop_event = threading.Event()
        settings = self.monitor_page.render_settings()
        self._monitor_stop_event = stop_event
        self._monitor_thread = threading.Thread(
            target=self._run_monitor_upload_loop,
            args=(device, settings, interval_seconds, stop_event),
            name="usb9-lcd-monitor-upload",
            daemon=True,
        )
        self._monitor_thread.start()
        self.monitor_upload_timer.start()
        self.monitor_page.set_live_monitoring_active(True)
        self.statusBar().showMessage("实时监控上传中...")

    def _run_monitor_upload_loop(self, device: DisplayDevice, settings, interval_seconds: int, stop_event: threading.Event) -> None:  # noqa: ANN001
        log_event("monitor_upload_loop_started", device=device.display_name)
        try:
            with self._lcd_output_lock:
                with self._open_upload_session(device) as upload_session:
                    while not stop_event.is_set():
                        started_at = time.monotonic()
                        telemetry = self.telemetry_provider()
                        frame = render_monitoring_frame(telemetry, device, settings)
                        upload_session.upload_static_frame(frame)
                        self._remember_uploaded_frame(device, frame)
                        self.latest_telemetry = telemetry
                        self._monitor_frame_count += 1
                        elapsed = time.monotonic() - started_at
                        if stop_event.wait(max(0.0, interval_seconds - elapsed)):
                            break
        except Exception as error:  # pragma: no cover - polled by GUI
            self._monitor_error = self._friendly_error(error)
            log_exception("monitor_upload_loop_failed", error)
        finally:
            log_event("monitor_upload_loop_finished", frame_count=self._monitor_frame_count)

    def _poll_monitor_worker(self) -> None:
        thread = self._monitor_thread
        if thread is not None and thread.is_alive():
            if self._monitor_frame_count != self._monitor_presented_frame_count and self.latest_telemetry is not None:
                self._monitor_presented_frame_count = self._monitor_frame_count
                self.monitor_page.update_telemetry(self.latest_telemetry)
                self.update_monitor_preview()
            self.statusBar().showMessage(f"实时监控上传中... 已发送 {self._monitor_frame_count} 帧")
            return
        if thread is None:
            self.monitor_upload_timer.stop()
            return

        error = self._monitor_error
        if error:
            self.stop_live_monitoring(message=f"实时监控失败：{error}")
        else:
            self.stop_live_monitoring(message="实时监控已停止")

    def stop_live_monitoring(self, message: str = "实时监控已停止") -> None:
        stop_event = self._monitor_stop_event
        if stop_event is not None:
            stop_event.set()
        thread = self._monitor_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self.monitor_upload_timer.stop()
        self._monitor_thread = None
        self._monitor_stop_event = None
        self._monitor_error = None
        self._monitor_presented_frame_count = 0
        self.monitor_page.set_live_monitoring_active(False)
        if message:
            self.statusBar().showMessage(message)

    def play_animation(self) -> None:
        log_event(
            "play_animation_started",
            selected_animation_path=str(self.selected_animation_path) if self.selected_animation_path else "",
        )
        device = self._selected_device()
        if device is None:
            self.statusBar().showMessage("请先选择设备")
            return
        if self.selected_animation_path is None:
            self.statusBar().showMessage("请先选择动图素材")
            return

        self.stop_animation(message="")
        target_key = self._device_selection_key(device)
        try:
            frames = list(
                iter_animated_frames(
                    self.selected_animation_path,
                    device,
                    AnimationRenderSettings(
                        rotate=cast(Rotation, device.preview.orientation),
                        fps=12,
                        jpeg_quality=80,
                        max_frames=600,
                    ),
                )
            )
        except Exception as error:
            log_exception("play_animation_failed_to_create_iterator", error)
            self.statusBar().showMessage(f"动画播放失败：{self._friendly_error(error)}")
            return
        if not frames:
            self.statusBar().showMessage("动画播放失败：没有可播放帧")
            return

        self._animation_frames = frames
        self._animation_frame_index = 0
        self._animation_device = device
        self._animation_device_key = target_key
        self._animation_error = None
        self._animation_stop_event = threading.Event()
        self._animation_uploading = True
        self._animation_thread = threading.Thread(
            target=self._run_animation_upload_loop,
            args=(device, frames, self._animation_stop_event),
            name="usb9-lcd-animation-upload",
            daemon=True,
        )
        self._animation_thread.start()
        self.animation_timer.setInterval(250)
        self.animation_timer.start()
        log_event(
            "play_animation_timer_started",
            device=device.display_name,
            frame_count=len(frames),
            first_duration_ms=frames[0].duration_ms,
        )
        self.statusBar().showMessage(f"动画循环播放中：{len(frames)} 帧")

    def play_next_animation_frame(self) -> None:
        thread = self._animation_thread
        if thread is not None and thread.is_alive():
            return
        if self._animation_frames is None:
            return

        if not self._animation_target_is_selected():
            if self._selected_device() is None:
                self.stop_animation(message="设备不可用，动画播放已停止")
            else:
                self.stop_animation(message="设备已切换，动画播放已停止")
            return

        device = self._animation_device
        if device is None:
            self.stop_animation(message="设备不可用，动画播放已停止")
            return

        try:
            animated_frame = self._animation_frames[self._animation_frame_index]
            with self._lcd_output_lock:
                self.driver.upload_static_frame(device, animated_frame.frame)
            self._remember_uploaded_frame(device, animated_frame.frame)
        except Exception as error:
            self._animation_uploading = False
            self.stop_animation(message=f"动画播放失败：{self._friendly_error(error)}")
            return

        self._animation_frame_index = (self._animation_frame_index + 1) % len(self._animation_frames)

    def _run_animation_upload_loop(
        self,
        device: DisplayDevice,
        frames: list[AnimatedFrame],
        stop_event: threading.Event,
    ) -> None:
        log_event("animation_upload_loop_started", frame_count=len(frames), device=device.display_name)
        try:
            with self._lcd_output_lock:
                with self._open_upload_session(device) as upload_session:
                    while not stop_event.is_set():
                        for index, animated_frame in enumerate(frames):
                            if stop_event.is_set():
                                break
                            started_at = time.monotonic()
                            upload_session.upload_static_frame(animated_frame.frame)
                            self._remember_uploaded_frame(device, animated_frame.frame)
                            self._animation_frame_index = (index + 1) % len(frames)
                            elapsed = time.monotonic() - started_at
                            delay = max(0.0, animated_frame.duration_ms / 1000.0 - elapsed)
                            if stop_event.wait(delay):
                                break
        except Exception as error:  # pragma: no cover - exercised through GUI polling
            self._animation_error = self._friendly_error(error)
            log_exception("animation_upload_loop_failed", error)
        finally:
            log_event("animation_upload_loop_finished")

    def _poll_animation_worker(self) -> None:
        thread = self._animation_thread
        if thread is not None and thread.is_alive():
            return
        if self._animation_frames is None:
            self.animation_timer.stop()
            return

        error = self._animation_error
        if error:
            self.stop_animation(message=f"动画播放失败：{error}")
            return

        self.stop_animation(message="动画播放已停止")

    def stop_animation(self, message: str = "动画播放已停止") -> None:
        log_event("stop_animation", message=message)
        self.animation_timer.stop()
        stop_event = self._animation_stop_event
        if stop_event is not None:
            stop_event.set()
        thread = self._animation_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._animation_frames = None
        self._animation_frame_index = 0
        self._animation_device = None
        self._animation_device_key = None
        self._animation_thread = None
        self._animation_stop_event = None
        self._animation_error = None
        self._animation_uploading = False
        if message:
            self.statusBar().showMessage(message)

    def _selected_device(self) -> DisplayDevice | None:
        if not hasattr(self, "device_combo"):
            return None
        index = self.device_combo.currentIndex()
        if index < 0 or index >= len(self.devices):
            return None
        return self.devices[index]

    def _device_selection_key(self, device: DisplayDevice) -> tuple[str, tuple[Path, ...]]:
        return (device.driver_id, tuple(device.connection.paths))

    def _selected_device_changed(self, index: int) -> None:
        device = self.devices[index] if 0 <= index < len(self.devices) else None
        log_event("selected_device_changed", index=index, device=device.display_name if device else "")
        self.preview.set_device(device)
        if device is not None and hasattr(self, "rotate_combo"):
            self.rotate_combo.setCurrentText(str(device.preview.orientation))
        self._update_device_text(device)
        self.update_monitor_preview()
        if self._animation_frames is not None and not self._animation_target_is_selected():
            if device is None:
                self.stop_animation(message="设备不可用，动画播放已停止")
            else:
                self.stop_animation(message="设备已切换，动画播放已停止")

    def _animation_target_is_selected(self) -> bool:
        device = self._selected_device()
        if device is None or self._animation_device_key is None:
            return False
        return self._device_selection_key(device) == self._animation_device_key

    def _stop_animation_if_target_missing(self) -> None:
        if self._animation_frames is None:
            return
        if not self._animation_target_is_selected():
            self.stop_animation(message="设备不可用，动画播放已停止")

    def _update_device_text(self, device: DisplayDevice | None) -> None:
        if device is None:
            self.device_summary_label.setText("未发现设备")
            self.home_page.update_device(None)
            self.device_status_label.setText("未发现设备")
            self.device_details.setPlainText("未发现设备")
            self._refresh_home_permission_status()
            return

        paths = ", ".join(str(path) for path in device.connection.paths) or "无路径"
        permissions = []
        permissions.append("可读" if device.connection.readable else "不可读")
        permissions.append("可写" if device.connection.writable else "不可写")
        capabilities = ", ".join(capability.value for capability in device.capabilities) or "无"
        writable = "可写" if device.connection.writable else "只读"
        self.device_summary_label.setText(f"{device.display_name} · {device.width}x{device.height} · {writable}")
        self.home_page.update_device(device)
        self.device_status_label.setText(
            f"{device.display_name} | 路径：{paths} | 权限：{' / '.join(permissions)}"
        )
        self.device_details.setPlainText(
            "\n".join(
                [
                    f"名称：{device.display_name}",
                    f"驱动：{device.driver_id}",
                    f"路径：{paths}",
                    f"权限：{' / '.join(permissions)}",
                    f"尺寸：{device.width}x{device.height}",
                    f"像素格式：{device.pixel_format.value}",
                    f"预览：{device.preview.width}x{device.preview.height} {device.preview.shape.value}",
                    f"能力：{capabilities}",
                    f"详情：{device.connection.details}",
                ]
            )
        )
        self._refresh_home_permission_status()

    def run_diagnostics(self) -> None:
        items = self.platform_adapter.diagnostic_items(
            openrgb_path=self.settings.openrgb.app_path,
            openrgb_host=self.settings.openrgb.host,
            openrgb_port=self.settings.openrgb.port,
        )
        rows = [self._diagnostic_row(item.label, item.ok, item.detail) for item in items]
        rows.extend(
            [
                self._diagnostic_row("LCD 设备", bool(self.devices), "已发现" if self.devices else "未发现"),
                self._diagnostic_row(
                    "LCD 写权限",
                    any(device.connection.writable for device in self.devices),
                    "可写" if any(device.connection.writable for device in self.devices) else "不可写，检查 USB/HID 权限",
                ),
            ]
        )
        self.diagnostics_text.setPlainText("\n".join(rows) + "\n\n" + render_system_status_report(self._system_status_snapshot()))
        self.home_page.add_event("诊断已刷新")
        self._refresh_home_permission_status()

    def _diagnostic_row(self, label: str, ok: bool, detail: str) -> str:
        return f"{'OK' if ok else 'WARN'}  {label}: {detail}"

    def _friendly_error(self, error: Exception) -> str:
        if isinstance(error, PermissionError):
            return "权限不足，请检查 hidraw 或 OpenRGB udev 规则"
        text = str(error)
        if "nvidia-smi" in text:
            return "未找到 nvidia-smi 或 NVIDIA 驱动未就绪"
        if "OpenRGB" in text or "6742" in text:
            return "OpenRGB SDK Server 未连接，请确认 6742 端口已监听"
        if "hidraw" in text:
            return f"{text}；请检查设备权限"
        return text or error.__class__.__name__
