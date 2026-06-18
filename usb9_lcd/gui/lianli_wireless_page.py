from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QMouseEvent, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSlider,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from usb9_lcd.gui.debug import log_event, log_exception
from usb9_lcd.gui.settings import (
    DEFAULT_LIANLI_FAN_CURVE_PROFILES,
    LIANLI_FAN_CURVE_MODES,
    GuiSettings,
    LianLiWirelessTargetSettings,
    save_settings as _save_settings_impl,
)
from usb9_lcd.gui.wheel_guard import install_wheel_guard
from usb9_lcd.lianli.analysis import analyze_live_log, diff_snapshot_files, summarize_experiment_dir
from usb9_lcd.lianli.capture import linux_control_write_gate_report
from usb9_lcd.lianli.effects import (
    OFFICIAL_LIANLI_WIRELESS_EFFECT_OPTIONS,
    lianli_wireless_effect,
)
from usb9_lcd.lianli.lcd import LianLiWirelessLcdBackend, create_pyusb_lcd_backend
from usb9_lcd.lianli.wireless import (
    LianLiWirelessBackend,
    PyUsbEndpointTransport,
    RGB_FIRST_PAYLOAD_REPEAT_COUNT,
    RF_SENDER_PID,
    RF_SENDER_VID,
    WirelessDeviceInfo,
    build_rgb_frame_payloads,
    build_rf_chunks,
    create_pyusb_backend,
    infer_led_count,
    scan_known_usb_devices as _scan_known_usb_devices_impl,
    static_rgb_effect_index,
    tlv2_color_effect_index,
    tlv2_effect_capability,
)
from usb9_lcd.monitoring.models import SystemTelemetry
from usb9_lcd.platforms.process import hidden_subprocess_kwargs


LIANLI_WRITE_CONFIRM_TOKEN = "WRITE-LIANLI"
LIANLI_DEFAULT_ROTATION_COLORS = ("#fe0000", "#00fe00", "#0000fe", "#ffd60a")
LIANLI_OFFICIAL_EFFECT_OPTIONS = OFFICIAL_LIANLI_WIRELESS_EFFECT_OPTIONS
def _pages_override(name: str, fallback: Callable):
    pages_module = sys.modules.get("usb9_lcd.gui.pages")
    override = getattr(pages_module, name, None) if pages_module is not None else None
    if override is not None and override is not globals().get(name):
        return override
    return fallback


def save_settings(settings: GuiSettings) -> None:
    _pages_override("save_settings", _save_settings_impl)(settings)


def scan_known_usb_devices():
    return _pages_override("scan_known_usb_devices", _scan_known_usb_devices_impl)()


def _wireless_device_payload(device: WirelessDeviceInfo) -> dict[str, object]:
    return {
        "mac": device.mac,
        "master_mac": device.master_mac,
        "is_bound": device.is_bound,
        "channel": device.channel,
        "rx_type": device.rx_type,
        "device_type": device.device_type,
        "fan_count": device.fan_count,
        "pwm_values": list(device.pwm_values),
        "fan_rpm": list(device.fan_rpm),
        "command_sequence": device.command_sequence,
        "raw_hex": device.raw.hex(),
    }


class LianLiWirelessTestDialog(QDialog):
    def __init__(self, page: "LianLiWirelessPage") -> None:
        super().__init__(page)
        self.page = page
        self.setWindowTitle("测试窗口")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(520, 260)
        self._pending_probe_request: tuple[int, int, tuple[int, int, int]] | None = None
        self._pending_force_off = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("联力无线测试窗口")
        title.setObjectName("SectionLabel")
        layout.addWidget(title)

        hint = QLabel("用于验证联力无线控制器功能。当前先提供灯珠数量测试，后续功能可以继续放在这里。")
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.target_label = QLabel("")
        self.target_label.setObjectName("FieldHint")
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)

        probe_box = QFrame()
        probe_box.setObjectName("MetricCard")
        probe_layout = QGridLayout(probe_box)
        probe_layout.setHorizontalSpacing(10)
        probe_layout.setVerticalSpacing(8)

        self.probe_count = QSpinBox()
        self.probe_count.setRange(0, 255)
        self.probe_count.setValue(self._initial_probe_count())
        self.probe_count.setSuffix(" 颗")

        self.probe_total_count = QSpinBox()
        self.probe_total_count.setRange(1, 255)
        self.probe_total_count.setValue(self._initial_probe_total())
        self.probe_total_count.setSuffix(" 颗")

        self.probe_color_combo = QComboBox()
        self.probe_color_combo.addItem("红色通道3（默认）", "0,0,254")
        self.probe_color_combo.addItem("红色通道1", "254,0,0")
        self.probe_color_combo.addItem("红色通道2", "0,254,0")

        self.minus_button = QPushButton("-1 并验证")
        self.minus_button.clicked.connect(lambda: self._adjust_and_apply(-1))
        self.plus_button = QPushButton("+1 并验证")
        self.plus_button.clicked.connect(lambda: self._adjust_and_apply(1))
        self.off_button = QPushButton("全灭")
        self.off_button.clicked.connect(self.apply_off)
        self.force_off_button = QPushButton("强制清除残留")
        self.force_off_button.clicked.connect(self.apply_force_off)
        self.apply_button = QPushButton("验证亮灯")
        self.apply_button.setObjectName("PrimaryButton")
        self.apply_button.clicked.connect(self.apply_probe)

        probe_layout.addWidget(QLabel("亮灯数量"), 0, 0)
        probe_layout.addWidget(self.probe_count, 0, 1)
        probe_layout.addWidget(QLabel("写入/清除范围"), 1, 0)
        probe_layout.addWidget(self.probe_total_count, 1, 1)
        probe_layout.addWidget(QLabel("红色通道"), 2, 0)
        probe_layout.addWidget(self.probe_color_combo, 2, 1, 1, 2)
        probe_layout.addWidget(self.minus_button, 3, 0)
        probe_layout.addWidget(self.plus_button, 3, 1)
        probe_layout.addWidget(self.off_button, 3, 2)
        probe_layout.addWidget(self.apply_button, 4, 0, 1, 2)
        probe_layout.addWidget(self.force_off_button, 4, 2)
        probe_layout.addWidget(QLabel("快捷键：Ctrl+↑ 增加并验证，Ctrl+↓ 减少并验证。"), 5, 0, 1, 3)
        probe_layout.addWidget(QLabel("说明：如果默认通道显示绿色，切换红色通道后再验证；残留灯用强制清除。"), 6, 0, 1, 3)
        layout.addWidget(probe_box)

        self.status_label = QLabel("等待测试")
        self.status_label.setObjectName("FieldHint")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)

        self.shortcut_inc = QShortcut(QKeySequence("Ctrl+Up"), self)
        self.shortcut_inc.activated.connect(lambda: self._adjust_and_apply(1))
        self.shortcut_dec = QShortcut(QKeySequence("Ctrl+Down"), self)
        self.shortcut_dec.activated.connect(lambda: self._adjust_and_apply(-1))

        self.page.operation_finished.connect(self._operation_finished)
        install_wheel_guard(self)
        self.update_controls()

    def _initial_probe_count(self) -> int:
        return 0

    def _initial_probe_total(self) -> int:
        target = self.page._cached_lianli_target()
        if target is not None:
            return max(1, min(255, int(target.led_count or 26)))
        if hasattr(self.page, "lianli_direct_led_count"):
            return max(1, min(255, int(self.page.lianli_direct_led_count.value())))
        return 26

    def _selected_probe_color(self) -> tuple[int, int, int]:
        raw = str(self.probe_color_combo.currentData() or "0,0,254")
        try:
            red, green, blue = (int(part) for part in raw.split(",", 2))
        except ValueError:
            return (0, 0, 254)
        return (
            max(0, min(255, red)),
            max(0, min(255, green)),
            max(0, min(255, blue)),
        )

    def _adjust_and_apply(self, delta: int) -> None:
        value = max(self.probe_count.minimum(), min(self.probe_count.maximum(), self.probe_count.value() + int(delta)))
        self.probe_count.setValue(value)
        self.apply_probe()

    def apply_probe(self) -> None:
        request = (
            int(self.probe_count.value()),
            int(self.probe_total_count.value()),
            self._selected_probe_color(),
        )
        if self.page._operation_active:
            self._pending_probe_request = request
            if self.page._lianli_loop_effect is not None:
                self.page.stop_lianli_lighting_loop()
                self.status_label.setText(f"正在停止灯效循环，随后验证：{request[0]} 颗")
            else:
                self.status_label.setText(f"操作进行中，已排队：{request[0]} 颗")
            return
        self._send_probe_request(request)

    def apply_off(self) -> None:
        self.probe_count.setValue(0)
        self.apply_probe()

    def apply_force_off(self) -> None:
        self.probe_count.setValue(0)
        self._pending_probe_request = None
        self._pending_force_off = False
        self.status_label.setText("正在强制清除残留灯光...")
        if self.page._operation_active and self.page._lianli_loop_effect is not None:
            self._pending_force_off = True
            self.page.stop_lianli_lighting_loop()
            return
        self.page.apply_lianli_led_force_off()

    def _send_probe_request(self, request: tuple[int, int, tuple[int, int, int]]) -> None:
        lit_count, led_total, color = request
        self.status_label.setText(f"正在验证亮灯数量：{lit_count} 颗")
        self.page.apply_lianli_led_probe(lit_count, led_total, color)

    def update_controls(self) -> None:
        target = self.page._cached_lianli_target()
        has_target = target is not None
        busy = self.page._operation_active
        for widget in (
            self.probe_count,
            self.probe_total_count,
            self.probe_color_combo,
            self.minus_button,
            self.plus_button,
            self.off_button,
            self.force_off_button,
            self.apply_button,
        ):
            widget.setEnabled(has_target and not busy)
        if target is None:
            self.target_label.setText("当前目标：未识别到联力无线风扇组，请先在主界面点击重新识别。")
        else:
            self.target_label.setText(
                f"当前目标：{target.label or '风扇组'} | {target.mac} | {target.led_count} LED | {target.fan_count} 把风扇"
            )

    def _operation_finished(self, message: str, result: object) -> None:
        self.update_controls()
        if isinstance(result, Exception):
            self.status_label.setText(f"测试失败：{result}")
            return
        if isinstance(result, dict) and result.get("operation") == "gui-lianli-led-probe":
            self.status_label.setText(
                f"测试完成：点亮 {result.get('lit_count')} 颗，写黑到 {result.get('led_total')} 颗范围，写入包 {result.get('packets_written')}"
            )
        if isinstance(result, dict) and result.get("operation") == "gui-lianli-led-force-off":
            self.status_label.setText(
                f"强制清除完成：范围 {result.get('led_totals')}，写入包 {result.get('packets_written')}"
            )
        if self._pending_probe_request is not None and not self.page._operation_active:
            request = self._pending_probe_request
            self._pending_probe_request = None
            QTimer.singleShot(0, lambda: self._send_probe_request(request))
        if self._pending_force_off and not self.page._operation_active:
            self._pending_force_off = False
            QTimer.singleShot(0, self.apply_force_off)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        try:
            self.page.operation_finished.disconnect(self._operation_finished)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(event)


class LianLiFanCurveEditor(QWidget):
    curve_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FanCurveCanvas")
        self.setMinimumSize(420, 260)
        self.setMouseTracking(True)
        self._points: list[list[int]] = [[30, 450], [50, 900], [70, 1440], [85, 1800]]
        self._dragging_idx = -1
        self._margin_left = 54
        self._margin_top = 28
        self._margin_right = 22
        self._margin_bottom = 38

    def set_points(self, points: list[list[int]]) -> None:
        self._points = self._sanitize_points(points)
        self.update()

    def points(self) -> list[list[int]]:
        return [list(point) for point in sorted(self._points, key=lambda item: item[0])]

    def _sanitize_points(self, points: list[list[int]]) -> list[list[int]]:
        parsed: list[list[int]] = []
        for item in points[:12]:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            parsed.append([
                max(0, min(100, int(item[0]))),
                max(0, min(1800, int(item[1]))),
            ])
        while len(parsed) < 2:
            parsed.append([[30, 450], [85, 1800]][len(parsed)])
        return sorted(parsed, key=lambda item: item[0])

    def _plot_rect(self) -> tuple[float, float, float, float]:
        width = max(1, self.width() - self._margin_left - self._margin_right)
        height = max(1, self.height() - self._margin_top - self._margin_bottom)
        return float(self._margin_left), float(self._margin_top), float(width), float(height)

    def _to_screen(self, temp: int, rpm: int) -> tuple[float, float]:
        left, top, width, height = self._plot_rect()
        x = left + max(0, min(100, temp)) / 100 * width
        y = top + (1800 - max(0, min(1800, rpm))) / 1800 * height
        return x, y

    def _from_screen(self, x: float, y: float) -> list[int]:
        left, top, width, height = self._plot_rect()
        temp = round(max(0, min(100, (x - left) / width * 100)))
        rpm = round(max(0, min(1800, 1800 - (y - top) / height * 1800)) / 10) * 10
        return [int(temp), int(rpm)]

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, top, width, height = self._plot_rect()
        right = left + width
        bottom = top + height

        painter.setPen(QPen(QColor("#344044"), 1, Qt.PenStyle.DotLine))
        for index in range(5):
            x = left + width * index / 4
            y = top + height * index / 4
            painter.drawLine(QPointF(x, top), QPointF(x, bottom))
            painter.drawLine(QPointF(left, y), QPointF(right, y))

        painter.setPen(QPen(QColor("#5c686c"), 1))
        painter.drawRect(int(left), int(top), int(width), int(height))

        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor("#aeb7b4"))
        for index, temp in enumerate((0, 25, 50, 75, 100)):
            x = left + width * index / 4
            painter.drawText(int(x) - 14, self.height() - 10, f"{temp}°C")
        for index, rpm in enumerate((1800, 1350, 900, 450, 0)):
            y = top + height * index / 4
            painter.drawText(6, int(y) + 4, f"{rpm}")

        points = self.points()
        if len(points) >= 2:
            painter.setPen(QPen(QColor("#4cc9f0"), 3))
            for index in range(len(points) - 1):
                x1, y1 = self._to_screen(points[index][0], points[index][1])
                x2, y2 = self._to_screen(points[index + 1][0], points[index + 1][1])
                painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        for index, (temp, rpm) in enumerate(points):
            x, y = self._to_screen(temp, rpm)
            painter.setBrush(QColor("#ff5a3d") if index == self._dragging_idx else QColor("#ffd166"))
            painter.setPen(QPen(QColor("#111516"), 2))
            painter.drawEllipse(QPointF(x, y), 7, 7)
            painter.setPen(QColor("#f2f3f0"))
            painter.drawText(int(x) + 10, int(y) - 8, f"{temp}°C {rpm}RPM")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._open_context_menu(event)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        for index, (temp, rpm) in enumerate(self.points()):
            x, y = self._to_screen(temp, rpm)
            if (pos.x() - x) ** 2 + (pos.y() - y) ** 2 <= 196:
                self._dragging_idx = index
                self.update()
                return

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        for temp, rpm in self.points():
            x, y = self._to_screen(temp, rpm)
            if (pos.x() - x) ** 2 + (pos.y() - y) ** 2 <= 196:
                return
        points = self.points()
        points.append(self._from_screen(pos.x(), pos.y()))
        self._points = self._sanitize_points(points)
        self.curve_changed.emit(self.points())
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging_idx < 0:
            return
        points = self.points()
        if self._dragging_idx >= len(points):
            return
        pos = event.position()
        points[self._dragging_idx] = self._from_screen(pos.x(), pos.y())
        self._points = self._sanitize_points(points)
        self._dragging_idx = min(self._dragging_idx, len(self._points) - 1)
        self.curve_changed.emit(self.points())
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        self._dragging_idx = -1
        self.update()

    def _open_context_menu(self, event: QMouseEvent) -> None:
        points = self.points()
        pos = event.position()
        target_idx = -1
        for index, (temp, rpm) in enumerate(points):
            x, y = self._to_screen(temp, rpm)
            if (pos.x() - x) ** 2 + (pos.y() - y) ** 2 <= 196:
                target_idx = index
                break
        if target_idx < 0 or len(points) <= 2:
            return
        menu = QMenu(self)
        action: QAction | None = menu.addAction(
            f"删除控制点 ({points[target_idx][0]}°C, {points[target_idx][1]} RPM)"
        )
        selected = menu.exec(event.globalPos())
        if selected == action:
            points.pop(target_idx)
            self._points = self._sanitize_points(points)
            self.curve_changed.emit(self.points())
            self.update()


class LianLiWirelessPage(QWidget):

    operation_finished = Signal(str, object)

    status_changed = Signal(str)

    rpm_refreshed = Signal(object)



    def __init__(

        self,

        backend_factory: Callable[[], LianLiWirelessBackend] = create_pyusb_backend,

        lcd_backend_factory: Callable[[], LianLiWirelessLcdBackend] = create_pyusb_lcd_backend,

        validation_output_dir: Path = Path(".cache/lianli/gui-validation"),

        experiment_output_dir: Path = Path(".cache/lianli/gui-pwm-experiment"),

        sync_experiment_output_dir: Path = Path(".cache/lianli/gui-sync-experiment"),

        mirror_experiment_output_dir: Path = Path(".cache/lianli/gui-pwm-mirror-experiment"),

        rgb_experiment_output_dir: Path = Path(".cache/lianli/gui-rgb-experiment"),

        rainbow_experiment_output_dir: Path = Path(".cache/lianli/gui-rainbow-experiment"),

        bind_experiment_output_dir: Path = Path(".cache/lianli/gui-bind-experiment"),

        unbind_experiment_output_dir: Path = Path(".cache/lianli/gui-unbind-experiment"),

        write_gate_capture_dir: Path = Path(".cache/lianli"),

        write_gate_experiment_dir: Path | None = None,

        require_write_gate: bool = False,

        write_gate_report_factory: Callable[..., dict[str, object]] = linux_control_write_gate_report,

        settings: GuiSettings | None = None,

        background_refresh: bool | None = None,

    ) -> None:

        super().__init__()

        self.settings = settings or GuiSettings()

        self.backend_factory = backend_factory

        self.lcd_backend_factory = lcd_backend_factory

        self.validation_output_dir = validation_output_dir

        self.experiment_output_dir = experiment_output_dir

        self.sync_experiment_output_dir = sync_experiment_output_dir

        self.mirror_experiment_output_dir = mirror_experiment_output_dir

        self.rgb_experiment_output_dir = rgb_experiment_output_dir

        self.rainbow_experiment_output_dir = rainbow_experiment_output_dir

        self.bind_experiment_output_dir = bind_experiment_output_dir

        self.unbind_experiment_output_dir = unbind_experiment_output_dir

        self.write_gate_capture_dir = write_gate_capture_dir

        self.write_gate_experiment_dir = write_gate_experiment_dir or experiment_output_dir

        self.require_write_gate = require_write_gate

        self.write_gate_report_factory = write_gate_report_factory

        if background_refresh is None:
            self.background_refresh = os.environ.get("QT_QPA_PLATFORM") != "offscreen"
        else:
            self.background_refresh = bool(background_refresh)

        self._lianli_write_gate_payload: dict[str, object] | None = None
        self._lianli_live_write_gate_payload: dict[str, object] | None = None

        self._operation_active = False

        self._closed = False

        self._rpm_refresh_inflight = False

        self._updating_lianli_fan_controls = False

        self._lianli_hardware_lock = threading.RLock()

        self._lianli_led_effect_index = 78000000

        self._lianli_loop_effect: str | None = None

        self._pending_lianli_effect: str | None = None

        self._lianli_live_rpm_by_mac: dict[str, tuple[int, int, int, int]] = {}

        self._lianli_last_valid_rpm_by_mac: dict[str, tuple[int, int, int, int]] = {}

        self._lianli_live_device_by_mac: dict[str, WirelessDeviceInfo] = {}

        self._lianli_latest_telemetry: SystemTelemetry | None = None

        self._lianli_curve_last_rpm: int | None = None

        self._lianli_curve_last_pwm: int | None = None

        self._lianli_curve_pending_pwm: int | None = None

        self._lianli_test_dialog: LianLiWirelessTestDialog | None = None

        self._rainbow_runtime_config: dict[str, object] = {

            "led_count": 26,

            "speed": 75,

            "brightness": 100,

            "direction": "left",

        }

        self._lighting_loop_stop = threading.Event()

        self._threads: list[threading.Thread] = []

        self.operation_finished.connect(self._operation_finished)

        self.rpm_refreshed.connect(self._apply_live_rpm_snapshot)



        layout = QVBoxLayout(self)

        layout.setContentsMargins(24, 22, 24, 24)

        layout.setSpacing(16)



        header = QLabel("联力无线")

        header.setObjectName("PageTitle")

        subtitle = QLabel("自动连接控制器，识别已绑定风扇组，并提供日常风扇/灯效控制")

        subtitle.setObjectName("PageSubtitle")

        layout.addWidget(header)

        layout.addWidget(subtitle)



        self.lianli_snapshot_text = QTextEdit()

        self.lianli_snapshot_text.setReadOnly(True)

        layout.addWidget(self._daily_control_panel(), 1)

        self._rpm_refresh_timer = QTimer(self)

        self._rpm_refresh_timer.setInterval(1500)

        self._rpm_refresh_timer.timeout.connect(self._refresh_lianli_rpm_async)

        if self.background_refresh:

            self._rpm_refresh_timer.start()



        if self.background_refresh and self.settings.lianli_wireless.auto_connect:

            QTimer.singleShot(0, self.auto_connect_lianli)



    def _daily_control_panel(self) -> QFrame:

        panel = QFrame()

        panel.setObjectName("MetricCard")

        layout = QVBoxLayout(panel)

        layout.setSpacing(14)



        top_row = QHBoxLayout()

        title = QLabel("风扇与灯光")

        title.setObjectName("SectionLabel")

        self.lianli_status_label = QLabel("正在准备自动连接...")

        self.lianli_status_label.setObjectName("FieldHint")

        self.lianli_status_label.setWordWrap(True)

        self.lianli_refresh_button = QPushButton("重新识别")

        self.lianli_refresh_button.clicked.connect(self.auto_connect_lianli)

        top_row.addWidget(title)

        top_row.addStretch(1)

        top_row.addWidget(self.lianli_refresh_button)

        layout.addLayout(top_row)

        layout.addWidget(self.lianli_status_label)



        self.lianli_target_combo = QComboBox()

        self.lianli_target_combo.currentIndexChanged.connect(self._selected_lianli_target_changed)

        target_row = QHBoxLayout()

        target_row.addWidget(QLabel("风扇组"))

        target_row.addWidget(self.lianli_target_combo, 1)

        layout.addLayout(target_row)



        self.lianli_direct_mac_input = QLineEdit()

        self.lianli_direct_master_input = QLineEdit()

        self.lianli_direct_led_count = QSpinBox()

        self.lianli_direct_led_count.setRange(1, 255)

        self.lianli_direct_channel = QSpinBox()

        self.lianli_direct_channel.setRange(0, 255)

        self.lianli_direct_rx_type = QSpinBox()

        self.lianli_direct_rx_type.setRange(0, 255)

        # Legacy experiment helpers still use these attribute names for auto-fill.
        self.lianli_mac_input = self.lianli_direct_mac_input
        self.lianli_master_mac_input = self.lianli_direct_master_input
        self.lianli_rx_type_value = self.lianli_direct_rx_type

        self.lianli_next_action_label = QLabel("下一步：先重新识别接收器，确认目标风扇组后再写入")
        self.lianli_next_action_label.setObjectName("FieldHint")
        self.lianli_next_action_label.setWordWrap(True)
        layout.addWidget(self.lianli_next_action_label)



        fan_box = QFrame()

        fan_box.setObjectName("MetricCard")

        fan_layout = QGridLayout(fan_box)

        fan_layout.setHorizontalSpacing(12)

        fan_layout.setVerticalSpacing(10)

        fan_title = QLabel("风扇转速")

        fan_title.setObjectName("SectionLabel")

        self.lianli_fan_summary = QLabel("等待识别风扇组")

        self.lianli_fan_summary.setObjectName("FieldHint")

        self.lianli_fan_rpm_value = QLabel("当前转速: -- RPM")

        self.lianli_fan_rpm_value.setObjectName("FieldHint")

        self.lianli_fan_mode_combo = QComboBox()

        for label, mode in (
            ("安静", "quiet"),
            ("标准", "normal"),
            ("高速", "high"),
            ("全速", "full"),
            ("自定义", "custom"),
        ):

            self.lianli_fan_mode_combo.addItem(label, mode)

        self.lianli_fan_mode_combo.currentIndexChanged.connect(self._lianli_fan_mode_changed)

        self.lianli_rpm_slider = QSlider(Qt.Orientation.Horizontal)

        self.lianli_rpm_slider.setRange(0, 1800)

        self.lianli_rpm_slider.setValue(self.settings.lianli_wireless.fan_rpm)

        self.lianli_rpm_value = QSpinBox()

        self.lianli_rpm_value.setRange(0, 1800)

        self.lianli_rpm_value.setValue(self.settings.lianli_wireless.fan_rpm)

        self.lianli_rpm_value.setSuffix(" RPM")

        self.lianli_rpm_slider.valueChanged.connect(self.lianli_rpm_value.setValue)

        self.lianli_rpm_value.valueChanged.connect(self.lianli_rpm_slider.setValue)

        self.lianli_rpm_value.valueChanged.connect(self._lianli_target_rpm_changed)

        self.lianli_curve_hint = QLabel("")

        self.lianli_curve_hint.setObjectName("FieldHint")

        self.lianli_auto_curve_enable = QCheckBox("启用自动温度曲线写入")

        self.lianli_auto_curve_enable.setChecked(bool(getattr(self.settings.lianli_wireless, "auto_curve_enabled", False)))

        self.lianli_auto_curve_enable.toggled.connect(self._set_lianli_auto_curve_enabled)

        self.lianli_curve_editor = LianLiFanCurveEditor()

        self.lianli_curve_editor.set_points(self._lianli_curve_points(self.settings.lianli_wireless.fan_mode))

        self.lianli_curve_editor.curve_changed.connect(self._lianli_curve_changed)

        self.lianli_daily_pwm_button = QPushButton("应用到当前风扇组")

        self.lianli_daily_pwm_button.setObjectName("PrimaryButton")

        self.lianli_daily_pwm_button.clicked.connect(self.send_lianli_target_rpm)

        self.lianli_apply_all_fans_button = QPushButton("应用到全部风扇组")

        self.lianli_apply_all_fans_button.clicked.connect(self.send_lianli_target_rpm_all)

        self.lianli_daily_pwm_sync_button = QPushButton("主板 PWM 同步")

        self.lianli_daily_pwm_sync_button.clicked.connect(self.send_live_pwm_sync)

        fan_layout.addWidget(fan_title, 0, 0)

        fan_layout.addWidget(self.lianli_fan_summary, 0, 1, 1, 3)

        fan_layout.addWidget(self.lianli_fan_rpm_value, 1, 1, 1, 3)

        fan_layout.addWidget(QLabel("模式"), 1, 0)

        fan_layout.addWidget(self.lianli_fan_mode_combo, 2, 1)

        fan_layout.addWidget(QLabel("目标转速"), 3, 0)

        fan_layout.addWidget(self.lianli_rpm_slider, 3, 1, 1, 2)

        fan_layout.addWidget(self.lianli_rpm_value, 3, 3)

        fan_layout.addWidget(self.lianli_curve_hint, 4, 1, 1, 3)

        fan_layout.addWidget(self.lianli_auto_curve_enable, 5, 1, 1, 3)

        fan_layout.addWidget(self.lianli_curve_editor, 6, 1, 1, 3)

        fan_layout.addWidget(self.lianli_daily_pwm_button, 7, 0, 1, 2)

        fan_layout.addWidget(self.lianli_apply_all_fans_button, 7, 2)

        fan_layout.addWidget(self.lianli_daily_pwm_sync_button, 7, 3)

        self._restore_lianli_fan_mode_selection()

        self._update_lianli_curve_hint()

        layout.addWidget(fan_box)

        light_box = QFrame()

        light_box.setObjectName("MetricCard")

        light_layout = QGridLayout(light_box)

        light_layout.setHorizontalSpacing(12)

        light_layout.setVerticalSpacing(10)

        light_title = QLabel("灯光")

        light_title.setObjectName("SectionLabel")

        self.lianli_effect_combo = QComboBox()

        for label, key in LIANLI_OFFICIAL_EFFECT_OPTIONS:

            self.lianli_effect_combo.addItem(label, key)

        effect_index = max(0, self.lianli_effect_combo.findData(self.settings.lianli_wireless.effect))

        self.lianli_effect_combo.setCurrentIndex(effect_index)

        self.lianli_effect_combo.currentIndexChanged.connect(self._update_lianli_effect_fields)

        self.lianli_speed_slider = QSlider(Qt.Orientation.Horizontal)

        self.lianli_speed_slider.setRange(0, 100)

        self.lianli_speed_slider.setValue(self.settings.lianli_wireless.speed)

        self.lianli_speed_value = QLabel(f"{self.lianli_speed_slider.value()}%")

        self.lianli_speed_slider.valueChanged.connect(self._on_lianli_speed_changed)

        self.lianli_brightness_slider = QSlider(Qt.Orientation.Horizontal)

        self.lianli_brightness_slider.setRange(0, 100)

        self.lianli_brightness_slider.setValue(self.settings.lianli_wireless.brightness)

        self.lianli_brightness_value = QLabel(f"{self.lianli_brightness_slider.value()}%")

        self.lianli_brightness_slider.valueChanged.connect(lambda value: self.lianli_brightness_value.setText(f"{value}%"))

        self.lianli_direction_combo = QComboBox()

        self.lianli_direction_combo.addItem("左方向", "left")

        self.lianli_direction_combo.addItem("右方向", "right")

        direction_index = max(0, self.lianli_direction_combo.findData(self.settings.lianli_wireless.direction))

        self.lianli_direction_combo.setCurrentIndex(direction_index)
        self.lianli_direction_combo.currentIndexChanged.connect(self._refresh_lianli_direction_buttons)

        self.lianli_direction_controls = QWidget()

        self.lianli_direction_controls_layout = QHBoxLayout(self.lianli_direction_controls)

        self.lianli_direction_controls_layout.setContentsMargins(0, 0, 0, 0)

        self.lianli_direction_controls_layout.setSpacing(10)

        self.lianli_direction_left_button = QPushButton("<<<")

        self.lianli_direction_left_button.setCheckable(True)

        self.lianli_direction_left_button.clicked.connect(lambda: self._set_lianli_direction("left"))

        self.lianli_direction_right_button = QPushButton(">>>")

        self.lianli_direction_right_button.setCheckable(True)

        self.lianli_direction_right_button.clicked.connect(lambda: self._set_lianli_direction("right"))

        self.lianli_direction_controls_layout.addWidget(self.lianli_direction_left_button)

        self.lianli_direction_controls_layout.addWidget(self.lianli_direction_right_button)

        self.lianli_direction_controls_layout.addStretch(1)

        self._refresh_lianli_direction_buttons()

        self.lianli_static_color = self.settings.lianli_wireless.color

        self.lianli_color_button = QPushButton("主色")

        self.lianli_color_button.clicked.connect(self.choose_lianli_static_color)

        self.lianli_accent_color = str(getattr(self.settings.lianli_wireless, "accent_color", "#ffffff") or "#ffffff").lower()

        self.lianli_accent_color_button = QPushButton("点缀色")

        self.lianli_accent_color_button.clicked.connect(self.choose_lianli_accent_color)

        self._update_lianli_color_button(self.lianli_color_button, self.lianli_static_color, "主色")

        self._update_lianli_color_button(self.lianli_accent_color_button, self.lianli_accent_color, "点缀色")

        self.lianli_color_controls = QWidget()

        self.lianli_color_controls_layout = QHBoxLayout(self.lianli_color_controls)

        self.lianli_color_controls_layout.setContentsMargins(0, 0, 0, 0)

        self.lianli_color_controls_layout.setSpacing(10)

        self.lianli_color_controls_layout.addWidget(self.lianli_color_button)

        self.lianli_color_controls_layout.addWidget(self.lianli_accent_color_button)

        self.lianli_color_controls_layout.addStretch(1)

        self._lianli_rotation_color_values = self._parse_lianli_color_list(
            str(getattr(self.settings.lianli_wireless, "rotation_colors", ",".join(LIANLI_DEFAULT_ROTATION_COLORS)) or ",".join(LIANLI_DEFAULT_ROTATION_COLORS))
        )

        self.lianli_rotation_colors = QWidget()

        self.lianli_rotation_colors_layout = QHBoxLayout(self.lianli_rotation_colors)

        self.lianli_rotation_colors_layout.setContentsMargins(0, 0, 0, 0)

        self.lianli_rotation_colors_layout.setSpacing(8)

        self._refresh_lianli_rotation_color_buttons()

        self.lianli_rotation_hold = QSpinBox()

        self.lianli_rotation_hold.setRange(100, 10000)

        self.lianli_rotation_hold.setValue(800)

        self.lianli_rotation_hold.setSuffix(" ms")

        self.lianli_apply_effect_button = QPushButton("执行")

        self.lianli_apply_effect_button.setObjectName("PrimaryButton")

        self.lianli_apply_effect_button.clicked.connect(self.apply_lianli_lighting_once)

        self.lianli_start_loop_button = QPushButton("开始旋转")

        self.lianli_start_loop_button.clicked.connect(self.start_lianli_lighting_loop)

        self.lianli_stop_loop_button = QPushButton("停止旋转")

        self.lianli_stop_loop_button.clicked.connect(self.stop_lianli_lighting_loop)

        self.lianli_speed_label = QLabel("速度")

        self.lianli_brightness_label = QLabel("亮度")

        self.lianli_direction_label = QLabel("方向")

        self.lianli_color_label = QLabel("颜色")

        self.lianli_rotation_label = QLabel("颜色队列")

        self.lianli_hold_label = QLabel("停留")

        self.lianli_effect_hint = QLabel("不同灯效会显示不同参数；未完成逆向的效果会先复用当前 RGB 写入能力。")

        self.lianli_effect_hint.setObjectName("FieldHint")

        light_layout.addWidget(light_title, 0, 0)

        light_layout.addWidget(QLabel("灯光效果"), 1, 0)

        light_layout.addWidget(self.lianli_effect_combo, 1, 1, 1, 2)

        light_layout.addWidget(self.lianli_color_label, 2, 0)

        light_layout.addWidget(self.lianli_color_controls, 2, 1, 1, 2)

        light_layout.addWidget(self.lianli_rotation_label, 3, 0)

        light_layout.addWidget(self.lianli_rotation_colors, 3, 1, 1, 2)

        light_layout.addWidget(self.lianli_speed_label, 4, 0)

        light_layout.addWidget(self.lianli_speed_slider, 4, 1)

        light_layout.addWidget(self.lianli_speed_value, 4, 2)

        light_layout.addWidget(self.lianli_brightness_label, 5, 0)

        light_layout.addWidget(self.lianli_brightness_slider, 5, 1)

        light_layout.addWidget(self.lianli_brightness_value, 5, 2)

        light_layout.addWidget(self.lianli_direction_label, 6, 0)

        light_layout.addWidget(self.lianli_direction_controls, 6, 1, 1, 2)

        light_layout.addWidget(self.lianli_hold_label, 7, 0)

        light_layout.addWidget(self.lianli_rotation_hold, 7, 1, 1, 2)

        self.lianli_test_window_button = QPushButton("测试窗口")

        self.lianli_test_window_button.clicked.connect(self.open_lianli_test_window)

        light_layout.addWidget(self.lianli_effect_hint, 8, 0, 1, 3)

        light_layout.addWidget(self.lianli_apply_effect_button, 9, 0)

        light_layout.addWidget(self.lianli_start_loop_button, 9, 1)

        light_layout.addWidget(self.lianli_stop_loop_button, 9, 2)

        light_layout.addWidget(self.lianli_test_window_button, 10, 0, 1, 3)

        self._update_lianli_effect_fields()

        layout.addWidget(light_box)

        layout.addWidget(self._write_panel())

        layout.addStretch(1)

        self._populate_cached_lianli_targets()

        self._update_daily_controls()

        self._update_write_controls()

        return panel

    def _read_panel(self) -> QFrame:

        panel = QFrame()

        panel.setObjectName("MetricCard")

        layout = QGridLayout(panel)

        layout.setHorizontalSpacing(8)

        layout.setVerticalSpacing(8)

        title = QLabel("L-Wireless")

        title.setObjectName("SectionLabel")

        self.lianli_status_label = QLabel("默认只读")

        self.lianli_status_label.setObjectName("FieldHint")

        self.lianli_status_label.setWordWrap(True)

        self.scan_lianli_button = QPushButton("扫描 USB")

        self.scan_lianli_button.clicked.connect(self.scan_local_devices)

        self.live_lianli_button = QPushButton("读取接收器")

        self.live_lianli_button.setObjectName("PrimaryButton")

        self.live_lianli_button.clicked.connect(self.refresh_live_devices)

        self.master_lianli_button = QPushButton("读取 Master")

        self.master_lianli_button.clicked.connect(self.query_live_master)

        self.lcd_info_button = QPushButton("读取 LCD")

        self.lcd_info_button.clicked.connect(self.query_live_lcd_info)

        self.validate_lianli_button = QPushButton("只读验证")

        self.validate_lianli_button.clicked.connect(self.run_readonly_validation)

        self.save_lianli_snapshot_button = QPushButton("保存快照")

        self.save_lianli_snapshot_button.clicked.connect(self.save_lianli_snapshot)

        self.analyze_lianli_log_button = QPushButton("分析日志")

        self.analyze_lianli_log_button.clicked.connect(self.analyze_lianli_log)

        self.diff_lianli_snapshots_button = QPushButton("对比快照")

        self.diff_lianli_snapshots_button.clicked.connect(self.diff_lianli_snapshots)

        self.summarize_lianli_experiments_button = QPushButton("汇总实验")

        self.summarize_lianli_experiments_button.clicked.connect(self.summarize_lianli_experiments)

        self.lianli_write_gate_button = QPushButton("写入门禁")

        self.lianli_write_gate_button.clicked.connect(self.run_lianli_write_gate)

        layout.addWidget(title, 0, 0)

        layout.addWidget(self.lianli_status_label, 0, 1, 1, 4)

        for index, button in enumerate(

            (

                self.scan_lianli_button,

                self.live_lianli_button,

                self.master_lianli_button,

                self.lcd_info_button,

                self.validate_lianli_button,

                self.save_lianli_snapshot_button,

                self.analyze_lianli_log_button,

                self.diff_lianli_snapshots_button,

                self.summarize_lianli_experiments_button,

                self.lianli_write_gate_button,

            )

        ):

            layout.addWidget(button, 1 + index // 5, index % 5)

        return panel



    def _experiment_guide_panel(self) -> QFrame:

        panel = QFrame()

        panel.setObjectName("MetricCard")

        layout = QVBoxLayout(panel)

        layout.setContentsMargins(14, 12, 14, 12)

        layout.setSpacing(6)

        title = QLabel("实验流程")

        title.setObjectName("SectionLabel")

        guide = QLabel(

            "1. 扫描 USB 并保存只读快照；2. 读取接收器和 Master；3. 只对单个 MAC 做安全实验；"

            "4. 对比写入前后快照，确认有效后再长期使用。"

        )

        guide.setObjectName("FieldHint")

        guide.setWordWrap(True)

        self.lianli_next_action_label = QLabel("下一步：先完成只读验证、写入门禁和实验汇总")

        self.lianli_next_action_label.setObjectName("FieldHint")

        self.lianli_next_action_label.setWordWrap(True)

        layout.addWidget(title)

        layout.addWidget(guide)

        layout.addWidget(self.lianli_next_action_label)

        return panel



    def _lighting_control_panel(self) -> QFrame:

        panel = QFrame()

        panel.setObjectName("MetricCard")

        layout = QVBoxLayout(panel)

        layout.setSpacing(12)



        header = QHBoxLayout()

        title = QLabel("联力无线灯光")

        title.setObjectName("SectionLabel")

        self.lianli_lighting_badge = QLabel("direct sender")

        self.lianli_lighting_badge.setObjectName("FieldHint")

        header.addWidget(title)

        header.addStretch(1)

        header.addWidget(self.lianli_lighting_badge)

        layout.addLayout(header)



        target_row = QGridLayout()

        target_row.setHorizontalSpacing(8)

        target_row.setVerticalSpacing(6)

        self.lianli_direct_mac_input = QLineEdit("14:55:f9:62:32:e1")

        self.lianli_direct_master_input = QLineEdit("24:69:dd:62:32:dc")

        self.lianli_direct_led_count = QSpinBox()

        self.lianli_direct_led_count.setRange(1, 255)

        self.lianli_direct_led_count.setValue(26)

        self.lianli_direct_channel = QSpinBox()

        self.lianli_direct_channel.setRange(0, 255)

        self.lianli_direct_channel.setValue(8)

        self.lianli_direct_rx_type = QSpinBox()

        self.lianli_direct_rx_type.setRange(0, 255)

        self.lianli_direct_rx_type.setValue(1)

        target_row.addWidget(QLabel("Target MAC"), 0, 0)

        target_row.addWidget(self.lianli_direct_mac_input, 0, 1)

        target_row.addWidget(QLabel("Master MAC"), 0, 2)

        target_row.addWidget(self.lianli_direct_master_input, 0, 3)

        target_row.addWidget(QLabel("Channel"), 1, 0)

        target_row.addWidget(self.lianli_direct_channel, 1, 1)

        target_row.addWidget(QLabel("RX Type"), 1, 2)

        target_row.addWidget(self.lianli_direct_rx_type, 1, 3)

        target_row.addWidget(QLabel("LED Count"), 1, 4)

        target_row.addWidget(self.lianli_direct_led_count, 1, 5)

        layout.addLayout(target_row)



        effect_row = QHBoxLayout()

        self.lianli_effect_combo = QComboBox()

        for label, key in LIANLI_OFFICIAL_EFFECT_OPTIONS:

            self.lianli_effect_combo.addItem(label, key)

        effect_row.addWidget(QLabel("灯效"))

        effect_row.addWidget(self.lianli_effect_combo, 1)

        layout.addLayout(effect_row)



        parameter_grid = QGridLayout()

        parameter_grid.setHorizontalSpacing(10)

        parameter_grid.setVerticalSpacing(8)

        self.lianli_speed_slider = QSlider(Qt.Orientation.Horizontal)

        self.lianli_speed_slider.setRange(0, 100)

        self.lianli_speed_slider.setValue(75)

        self.lianli_speed_value = QLabel("75%")

        self.lianli_speed_slider.valueChanged.connect(lambda value: self.lianli_speed_value.setText(f"{value}%"))

        self.lianli_brightness_slider = QSlider(Qt.Orientation.Horizontal)

        self.lianli_brightness_slider.setRange(0, 100)

        self.lianli_brightness_slider.setValue(100)

        self.lianli_brightness_value = QLabel("100%")

        self.lianli_brightness_slider.valueChanged.connect(lambda value: self.lianli_brightness_value.setText(f"{value}%"))

        self.lianli_direction_combo = QComboBox()

        self.lianli_direction_combo.addItem("左方向", "left")

        self.lianli_direction_combo.addItem("右方向", "right")

        parameter_grid.addWidget(QLabel("速度"), 0, 0)

        parameter_grid.addWidget(self.lianli_speed_slider, 0, 1)

        parameter_grid.addWidget(self.lianli_speed_value, 0, 2)

        parameter_grid.addWidget(QLabel("亮度"), 1, 0)

        parameter_grid.addWidget(self.lianli_brightness_slider, 1, 1)

        parameter_grid.addWidget(self.lianli_brightness_value, 1, 2)

        parameter_grid.addWidget(QLabel("方向"), 2, 0)

        parameter_grid.addWidget(self.lianli_direction_combo, 2, 1, 1, 2)

        layout.addLayout(parameter_grid)



        color_row = QHBoxLayout()

        self.lianli_static_color = "#00fe00"

        self.lianli_color_button = QPushButton("静态颜色 #00fe00")

        self.lianli_color_button.clicked.connect(self.choose_lianli_static_color)

        color_row.addWidget(self.lianli_color_button)

        for label, color in (

            ("红", "#fe0000"),

            ("绿", "#00fe00"),

            ("蓝", "#0000fe"),

            ("白", "#fefefe"),

            ("黄", "#fefe00"),

            ("青", "#00fefe"),

            ("紫", "#7f00fe"),

        ):

            button = QPushButton(label)

            button.clicked.connect(lambda _checked=False, value=color: self.set_lianli_static_color(value))

            color_row.addWidget(button)

        layout.addLayout(color_row)



        rotation_row = QGridLayout()

        self.lianli_rotation_colors = QLineEdit("#fe0000,#00fe00,#0000fe,#ffd60a")

        self.lianli_rotation_hold = QSpinBox()

        self.lianli_rotation_hold.setRange(100, 10000)

        self.lianli_rotation_hold.setValue(800)

        self.lianli_rotation_hold.setSuffix(" ms")

        self.lianli_rotation_transition = QSpinBox()

        self.lianli_rotation_transition.setRange(0, 5000)

        self.lianli_rotation_transition.setValue(300)

        self.lianli_rotation_transition.setSuffix(" ms")

        rotation_row.addWidget(QLabel("颜色旋转队列"), 0, 0)

        rotation_row.addWidget(self.lianli_rotation_colors, 0, 1, 1, 3)

        rotation_row.addWidget(QLabel("停留"), 1, 0)

        rotation_row.addWidget(self.lianli_rotation_hold, 1, 1)

        rotation_row.addWidget(QLabel("过渡"), 1, 2)

        rotation_row.addWidget(self.lianli_rotation_transition, 1, 3)

        layout.addLayout(rotation_row)

        probe_row = QHBoxLayout()

        self.lianli_probe_count = QSpinBox()

        self.lianli_probe_count.setRange(1, 255)

        self.lianli_probe_count.setValue(1)

        self.lianli_probe_count.setSuffix(" 颗")

        self.lianli_probe_button = QPushButton("验证亮灯")

        self.lianli_probe_button.clicked.connect(self.apply_lianli_led_probe)

        probe_row.addWidget(QLabel("亮灯数量"))

        probe_row.addWidget(self.lianli_probe_count)

        probe_row.addWidget(self.lianli_probe_button)

        probe_row.addStretch(1)

        layout.addLayout(probe_row)


        action_row = QHBoxLayout()

        self.lianli_preview_packet_button = QPushButton("预览参数")

        self.lianli_preview_packet_button.clicked.connect(self.preview_lianli_lighting_payload)

        self.lianli_apply_effect_button = QPushButton("应用一次")

        self.lianli_apply_effect_button.setObjectName("PrimaryButton")

        self.lianli_apply_effect_button.clicked.connect(self.apply_lianli_lighting_once)

        self.lianli_start_loop_button = QPushButton("开始循环")

        self.lianli_start_loop_button.clicked.connect(self.start_lianli_lighting_loop)

        self.lianli_stop_loop_button = QPushButton("停止循环")

        self.lianli_stop_loop_button.clicked.connect(self.stop_lianli_lighting_loop)

        for button in (

            self.lianli_preview_packet_button,

            self.lianli_apply_effect_button,

            self.lianli_start_loop_button,

            self.lianli_stop_loop_button,

        ):

            action_row.addWidget(button)

        layout.addLayout(action_row)



        self.lianli_lighting_log = QTextEdit()

        self.lianli_lighting_log.setReadOnly(True)

        self.lianli_lighting_log.setMinimumHeight(120)

        self.lianli_lighting_log.setPlainText(

            "ready target=14:55:f9:62:32:e1 led_count=26\n"

            "note 彩虹需要循环发送；当前先用 direct sender 绕过 receiver snapshot\n"

        )

        layout.addWidget(self.lianli_lighting_log)

        return panel



    def _snapshot_panel(self) -> QFrame:

        panel = QFrame()

        panel.setObjectName("MetricCard")

        layout = QVBoxLayout(panel)

        title = QLabel("接收器快照")

        title.setObjectName("SectionLabel")

        self.lianli_snapshot_text = QTextEdit()

        self.lianli_snapshot_text.setReadOnly(True)

        self.lianli_snapshot_text.setMinimumHeight(320)

        layout.addWidget(title)

        layout.addWidget(self.lianli_snapshot_text, 1)

        return panel



    def _write_panel(self) -> QFrame:

        panel = QFrame()

        panel.setObjectName("MetricCard")

        layout = QVBoxLayout(panel)

        layout.setSpacing(8)

        title = QLabel("受保护写入")

        title.setObjectName("SectionLabel")

        layout.addWidget(title)

        hint = QLabel("写入会影响真实风扇/灯光，只对指定 MAC 单设备发送。")

        hint.setObjectName("FieldHint")

        hint.setWordWrap(True)

        layout.addWidget(hint)

        self.lianli_write_gate_label = QLabel("")

        self.lianli_write_gate_label.setObjectName("FieldHint")

        self.lianli_write_gate_label.setWordWrap(True)

        layout.addWidget(self.lianli_write_gate_label)



        self.lianli_write_enable = QCheckBox("允许联力真实写入")

        self.lianli_write_enable.setChecked(bool(getattr(self.settings.lianli_wireless, "write_enabled", False)))

        self.lianli_write_enable.toggled.connect(self._set_lianli_write_enabled)

        self.lianli_confirm_input = QLineEdit(LIANLI_WRITE_CONFIRM_TOKEN)

        self.lianli_confirm_input.setPlaceholderText(LIANLI_WRITE_CONFIRM_TOKEN)

        self.lianli_confirm_input.hide()

        self.lianli_mac_input = QLineEdit()

        self.lianli_mac_input.setPlaceholderText("aa:bb:cc:dd:ee:ff")
        self.lianli_mac_input.hide()

        self.lianli_master_mac_input = QLineEdit()

        self.lianli_master_mac_input.setPlaceholderText("可留空自动读取 Master")
        self.lianli_master_mac_input.hide()

        self.lianli_rx_type_value = QSpinBox()

        self.lianli_rx_type_value.setRange(1, 15)

        self.lianli_rx_type_value.setValue(3)
        self.lianli_rx_type_value.hide()

        self.lianli_pwm_value = QSpinBox()

        self.lianli_pwm_value.setRange(40, 255)

        self.lianli_pwm_value.setValue(120)

        self.lianli_pwm_value.setSuffix(" PWM")

        self.lianli_lcd_brightness = QSpinBox()

        self.lianli_lcd_brightness.setRange(0, 100)

        self.lianli_lcd_brightness.setValue(60)

        self.lianli_lcd_brightness.setSuffix(" %")

        self.lianli_lcd_rotation = QComboBox()

        for degrees in (0, 90, 180, 270):

            self.lianli_lcd_rotation.addItem(f"{degrees}°", degrees)

        self.lianli_rainbow_frame_count = QSpinBox()

        self.lianli_rainbow_frame_count.setRange(1, 65535)

        self.lianli_rainbow_frame_count.setValue(24)

        self.lianli_rainbow_frame_count.setSuffix(" 帧")

        self.lianli_rainbow_interval = QSpinBox()

        self.lianli_rainbow_interval.setRange(0, 65535)

        self.lianli_rainbow_interval.setValue(48)

        self.lianli_rainbow_interval.setSuffix(" ms")

        layout.addWidget(self.lianli_write_enable)

        self.lianli_write_target_label = QLabel("")

        self.lianli_write_target_label.setObjectName("FieldHint")

        self.lianli_write_target_label.setWordWrap(True)

        layout.addWidget(self.lianli_write_target_label)

        form = QGridLayout()

        form.setHorizontalSpacing(8)

        form.setVerticalSpacing(6)

        form.addWidget(QLabel("PWM"), 0, 0)

        form.addWidget(self.lianli_pwm_value, 0, 1)

        form.addWidget(QLabel("LCD 亮度"), 0, 2)

        form.addWidget(self.lianli_lcd_brightness, 0, 3)

        form.addWidget(QLabel("LCD 旋转"), 1, 0)

        form.addWidget(self.lianli_lcd_rotation, 1, 1)

        form.addWidget(QLabel("彩虹帧数"), 1, 2)

        form.addWidget(self.lianli_rainbow_frame_count, 1, 3)

        form.addWidget(QLabel("彩虹间隔"), 2, 0)

        form.addWidget(self.lianli_rainbow_interval, 2, 1)

        layout.addLayout(form)



        self.lianli_pwm_button = QPushButton("发送 PWM")

        self.lianli_pwm_button.clicked.connect(self.send_live_pwm)

        self.lianli_safe_pwm_button = QPushButton("安全 PWM 实验")

        self.lianli_safe_pwm_button.clicked.connect(self.run_safe_pwm_experiment)

        self.lianli_pwm_sync_button = QPushButton("主板 PWM 同步")

        self.lianli_pwm_sync_button.clicked.connect(self.send_live_pwm_sync)

        self.lianli_safe_sync_button = QPushButton("安全 Sync 实验")

        self.lianli_safe_sync_button.clicked.connect(self.run_safe_sync_experiment)

        self.lianli_pwm_mirror_button = QPushButton("镜像主板 PWM")

        self.lianli_pwm_mirror_button.clicked.connect(self.send_live_pwm_mirror)

        self.lianli_safe_mirror_button = QPushButton("安全 Mirror 实验")

        self.lianli_safe_mirror_button.clicked.connect(self.run_safe_pwm_mirror_experiment)

        self.lianli_rgb_off_button = QPushButton("关闭无线灯光")

        self.lianli_rgb_off_button.clicked.connect(self.send_live_rgb_off)

        self.lianli_safe_rgb_button = QPushButton("安全 RGB 实验")

        self.lianli_safe_rgb_button.clicked.connect(self.run_safe_rgb_experiment)

        self.lianli_rainbow_button = QPushButton("彩虹灯效")

        self.lianli_rainbow_button.clicked.connect(self.send_live_rainbow)

        self.lianli_safe_rainbow_button = QPushButton("安全 Rainbow 实验")

        self.lianli_safe_rainbow_button.clicked.connect(self.run_safe_rainbow_experiment)

        self.lianli_safe_bind_button = QPushButton("安全 Bind 实验")

        self.lianli_safe_bind_button.clicked.connect(self.run_safe_bind_experiment)

        self.lianli_safe_unbind_button = QPushButton("安全 Unbind 实验")

        self.lianli_safe_unbind_button.clicked.connect(self.run_safe_unbind_experiment)

        self.lianli_lcd_info_button = QPushButton("读取 LCD 信息")

        self.lianli_lcd_info_button.clicked.connect(self.query_live_lcd_info)

        self.lianli_lcd_control_button = QPushButton("应用 LCD")

        self.lianli_lcd_control_button.clicked.connect(self.send_live_lcd_control)

        action_grid = QGridLayout()

        action_grid.setHorizontalSpacing(8)

        action_grid.setVerticalSpacing(8)

        for index, button in enumerate((

            self.lianli_pwm_button,

            self.lianli_safe_pwm_button,

            self.lianli_pwm_sync_button,

            self.lianli_safe_sync_button,

            self.lianli_pwm_mirror_button,

            self.lianli_safe_mirror_button,

            self.lianli_rgb_off_button,

            self.lianli_safe_rgb_button,

            self.lianli_rainbow_button,

            self.lianli_safe_rainbow_button,

            self.lianli_safe_bind_button,

            self.lianli_safe_unbind_button,

            self.lianli_lcd_info_button,

            self.lianli_lcd_control_button,

        )):

            action_grid.addWidget(button, index // 2, index % 2)

        layout.addLayout(action_grid)

        layout.addStretch(1)

        self._update_write_controls()

        return panel



    def auto_connect_lianli(self) -> None:

        if self._operation_active:

            return

        self._operation_active = True

        self._update_daily_controls()

        self._set_lianli_status("正在自动连接联力无线控制器...")



        def worker() -> None:

            try:

                with self._lianli_hardware_lock:

                    payload = self._auto_connect_lianli_payload()

                if not self._closed:

                    self.operation_finished.emit(str(payload.get("message", "联力无线已连接")), payload)

            except Exception as error:  # noqa: BLE001

                if not self._closed:

                    self.operation_finished.emit("联力无线自动连接失败", error)



        thread = threading.Thread(target=worker, daemon=True)

        self._threads.append(thread)

        thread.start()



    def _auto_connect_lianli_payload(self) -> dict[str, object]:

        service_actions: list[str] = []

        if sys.platform.startswith("win"):

            service_actions = self._stop_lconnect_services()

        usb_devices = scan_known_usb_devices()

        detected_targets: list[WirelessDeviceInfo] = []

        source = "cache"

        receiver_error = ""

        for attempt in range(3):

            backend = None

            try:

                backend = self.backend_factory()

                snapshot = backend.list_devices(page_count=1)

                self._remember_lianli_live_devices(snapshot.devices)

                detected_targets = [device for device in snapshot.devices if device.is_bound]

                source = "receiver"

                receiver_error = ""

                break

            except Exception as error:  # noqa: BLE001

                receiver_error = str(error)

                log_exception("lianli_auto_receiver_scan_failed", error)

                time.sleep(0.35 + attempt * 0.35)

            finally:

                self._close_lianli_backend(backend)

        if detected_targets:

            self._store_lianli_targets(detected_targets)
            self._mark_lianli_live_write_gate(detected_targets, source="auto-connect")

            message = f"已连接控制器，识别到 {len(detected_targets)} 个已绑定风扇组"

        elif self.settings.lianli_wireless.targets:

            message = "接收器暂时不可读，已使用上次保存的绑定。若刚用过 L-Connect，请先完全退出官方软件后点“重新识别”。"

        else:

            seeded = self._seed_lianli_target_from_direct_inputs()

            if seeded is not None:

                message = "接收器暂时不可读，已用当前手动参数创建临时绑定。建议退出 L-Connect 后再点“重新识别”以校准。"

                source = "manual-seed"

            else:

                message = "已发现控制器，但接收器不可读且没有缓存绑定。请完全退出 L-Connect 后重新识别。"

        return {

            "operation": "lianli-auto-connect",

            "message": message,

            "source": source,

            "service_actions": service_actions,

            "usb_device_count": len(usb_devices),

            "target_count": len(self.settings.lianli_wireless.targets),

            "active_target_mac": self.settings.lianli_wireless.active_target_mac,

            "receiver_error": receiver_error,

        }



    def _seed_lianli_target_from_direct_inputs(self) -> LianLiWirelessTargetSettings | None:

        if not hasattr(self, "lianli_direct_mac_input") or not hasattr(self, "lianli_direct_master_input"):

            return None

        mac = self.lianli_direct_mac_input.text().strip().lower()

        master_mac = self.lianli_direct_master_input.text().strip().lower()

        if not self._is_valid_mac(mac) or not self._is_valid_mac(master_mac):

            return None

        if set(master_mac.split(":")) == {"00"}:

            return None

        seeded = LianLiWirelessTargetSettings(

            mac=mac,

            master_mac=master_mac,

            channel=int(self.lianli_direct_channel.value()),

            rx_type=int(self.lianli_direct_rx_type.value()),

            device_type=0,

            fan_count=4,

            led_count=int(self.lianli_direct_led_count.value()) if hasattr(self, "lianli_direct_led_count") else 26,

            label="手动临时绑定",

        )

        self.settings.lianli_wireless.targets = {seeded.mac: seeded}

        self.settings.lianli_wireless.active_target_mac = seeded.mac

        save_settings(self.settings)

        return seeded



    def _is_valid_mac(self, value: str) -> bool:

        parts = value.split(":")

        if len(parts) != 6:

            return False

        for part in parts:

            if len(part) != 2:

                return False

            try:

                int(part, 16)

            except ValueError:

                return False

        return True



    def _stop_lconnect_services(self) -> list[str]:

        actions: list[str] = []

        # Stop watcher first so it does not immediately restart the main service.

        for service_name in ("L-Connect Service Watcher", "L-Connect Service"):

            try:

                completed = subprocess.run(

                    ["sc", "stop", service_name],

                    capture_output=True,

                    text=True,

                    timeout=4,

                    check=False,
                    **hidden_subprocess_kwargs(),

                )

                output = f"{completed.stdout} {completed.stderr}".lower()

                if completed.returncode == 0 or "service has not been started" in output or "正在停止" in output:

                    actions.append(f"stopped:{service_name}")

                else:

                    actions.append(f"skip:{service_name}")

            except Exception as error:  # noqa: BLE001

                log_exception("lconnect_service_stop_failed", error)

                actions.append(f"error:{service_name}")

        # Kill by service PID (more reliable than image name only).

        for service_name in ("L-Connect Service Watcher", "L-Connect Service"):

            try:

                query = subprocess.run(

                    ["sc", "queryex", service_name],

                    capture_output=True,

                    text=True,

                    timeout=4,

                    check=False,
                    **hidden_subprocess_kwargs(),

                )

                output = f"{query.stdout}\n{query.stderr}"

                pid = 0

                for line in output.splitlines():

                    if "PID" in line.upper():

                        parts = line.split(":")

                        if len(parts) >= 2:

                            try:

                                pid = int(parts[1].strip())

                            except Exception:

                                pid = 0

                        break

                if pid > 0:

                    killed = subprocess.run(

                        ["taskkill", "/F", "/PID", str(pid)],

                        capture_output=True,

                        text=True,

                        timeout=4,

                        check=False,
                        **hidden_subprocess_kwargs(),

                    )

                    if killed.returncode == 0:

                        actions.append(f"killed-pid:{service_name}:{pid}")

                    else:

                        actions.append(f"kill-pid-skip:{service_name}:{pid}")

            except Exception as error:  # noqa: BLE001

                log_exception("lconnect_service_pid_kill_failed", error)

                actions.append(f"kill-pid-error:{service_name}")

        # Force-kill residual processes when SCM stop is not enough.

        for process_name in (

            "L-Connect Service.exe",

            "L-Connect Service Watcher.exe",

            "LConnectService.exe",

            "LConnectServiceWatcher.exe",

        ):

            try:

                completed = subprocess.run(

                    ["taskkill", "/F", "/IM", process_name],

                    capture_output=True,

                    text=True,

                    timeout=4,

                    check=False,
                    **hidden_subprocess_kwargs(),

                )

                output = f"{completed.stdout} {completed.stderr}".lower()

                if completed.returncode == 0:

                    actions.append(f"killed:{process_name}")

                elif "not found" in output or "没有运行的任务" in output:

                    actions.append(f"not-running:{process_name}")

                else:

                    actions.append(f"kill-skip:{process_name}")

            except Exception as error:  # noqa: BLE001

                log_exception("lconnect_process_kill_failed", error)

                actions.append(f"kill-error:{process_name}")

        # Wildcard fallback for variants not covered by exact image names.

        try:

            wildcard = subprocess.run(

                [

                    "powershell",

                    "-NoProfile",

                    "-Command",

                    "Get-Process | Where-Object { $_.ProcessName -like 'L-Connect*' -or $_.ProcessName -like 'LConnect*' } | Stop-Process -Force -ErrorAction SilentlyContinue",

                ],

                capture_output=True,

                text=True,

                timeout=5,

                check=False,
                **hidden_subprocess_kwargs(),

            )

            if wildcard.returncode == 0:

                actions.append("killed-wildcard:lconnect")

            else:

                actions.append("kill-wildcard-skip:lconnect")

        except Exception as error:  # noqa: BLE001

            log_exception("lconnect_wildcard_kill_failed", error)

            actions.append("kill-wildcard-error:lconnect")

        # Give SCM a short grace period before USB re-open.

        time.sleep(0.35)

        return actions



    def _close_lianli_backend(self, backend: LianLiWirelessBackend | None) -> None:

        if backend is None:

            return

        for transport in (getattr(backend, "sender", None), getattr(backend, "receiver", None)):

            close = getattr(transport, "close", None)

            if callable(close):

                try:

                    close()

                except Exception:

                    pass



    def _close_lianli_lcd_backend(self, backend: object | None) -> None:

        if backend is None:

            return

        for transport in (backend, getattr(backend, "transport", None)):

            close = getattr(transport, "close", None)

            if callable(close):

                try:

                    close()

                except Exception:

                    pass



    def _store_lianli_targets(self, targets: list[WirelessDeviceInfo]) -> None:

        stored: dict[str, LianLiWirelessTargetSettings] = {}

        sorted_targets = sorted(
            targets,
            key=lambda item: (int(infer_led_count(item)), str(item.mac)),
        )

        for index, target in enumerate(sorted_targets, start=1):

            led_count = infer_led_count(target)

            stored[target.mac] = LianLiWirelessTargetSettings(

                mac=target.mac,

                master_mac=target.master_mac,

                channel=target.channel,

                rx_type=target.rx_type,

                device_type=target.device_type,

                fan_count=target.fan_count,

                led_count=led_count,

                label=f"风扇组 {index}",

            )

        self.settings.lianli_wireless.targets = stored

        if not self.settings.lianli_wireless.active_target_mac or self.settings.lianli_wireless.active_target_mac not in stored:

            self.settings.lianli_wireless.active_target_mac = next(iter(stored), "")

        save_settings(self.settings)


    def _mark_lianli_live_write_gate(self, targets: list[WirelessDeviceInfo], *, source: str) -> None:

        bound_targets = [target for target in targets if target.is_bound]

        if not bound_targets:

            return

        self._lianli_live_write_gate_payload = {

            "operation": "linux-control-write-gate",

            "status": "live-receiver-write-enabled",

            "allows_any_guarded_write": True,

            "ready_action_count": len(bound_targets),

            "blocked_action_count": 0,

            "ready_action_ids": ["live-receiver-bound-target"],

            "source": source,

            "target_macs": [target.mac for target in bound_targets],

        }



    def _populate_cached_lianli_targets(self) -> None:

        if not hasattr(self, "lianli_target_combo"):

            return

        self.lianli_target_combo.blockSignals(True)

        self.lianli_target_combo.clear()

        for mac, target in sorted(
            self.settings.lianli_wireless.targets.items(),
            key=lambda pair: (int(pair[1].led_count), str(pair[0])),
        ):

            label = target.label or mac

            self.lianli_target_combo.addItem(f"{label}  {mac}", mac)

        active_mac = self.settings.lianli_wireless.active_target_mac

        active_index = self.lianli_target_combo.findData(active_mac)

        if active_index >= 0:

            self.lianli_target_combo.setCurrentIndex(active_index)

        self.lianli_target_combo.blockSignals(False)

        self._selected_lianli_target_changed()



    def _selected_lianli_target_changed(self) -> None:

        mac = str(self.lianli_target_combo.currentData() or "")

        if mac:

            self.settings.lianli_wireless.active_target_mac = mac

            save_settings(self.settings)

        target = self._cached_lianli_target()

        if target is not None:

            self.lianli_direct_mac_input.setText(target.mac)

            self.lianli_direct_master_input.setText(target.master_mac)

            self.lianli_direct_channel.setValue(target.channel)

            self.lianli_direct_rx_type.setValue(target.rx_type)

            self.lianli_direct_led_count.setValue(target.led_count)

            if hasattr(self, "lianli_mac_input"):

                self.lianli_mac_input.setText(target.mac)

            if hasattr(self, "lianli_master_mac_input"):

                self.lianli_master_mac_input.setText(target.master_mac)

            if hasattr(self, "lianli_rx_type_value"):

                self.lianli_rx_type_value.setValue(max(1, min(15, int(target.rx_type or 3))))

            if hasattr(self, "lianli_fan_summary"):

                self.lianli_fan_summary.setText(

                    f"{target.label or '风扇组'} | {target.fan_count} 把风扇 | {target.led_count} LED | 0-1800 RPM"

                )

            if hasattr(self, "lianli_fan_rpm_value"):

                rpm_values = self._lianli_last_valid_rpm_by_mac.get(

                    target.mac,

                    self._lianli_live_rpm_by_mac.get(target.mac, (0, 0, 0, 0)),

                )

                fan_count = max(1, min(4, int(target.fan_count or 1)))

                active_rpm = [value for value in rpm_values[:fan_count] if value > 0]

                if active_rpm:

                    avg_rpm = round(sum(active_rpm) / len(active_rpm))

                    detail = " / ".join(str(value) for value in active_rpm)

                    self.lianli_fan_rpm_value.setText(f"当前转速: {avg_rpm} RPM ({detail})")

                else:

                    self.lianli_fan_rpm_value.setText("当前转速: -- RPM")

        self._update_daily_controls()



    def _refresh_lianli_rpm_async(self) -> None:

        if self._closed or self._operation_active or self._rpm_refresh_inflight:

            return

        if not self.settings.lianli_wireless.targets:

            return

        self._rpm_refresh_inflight = True



        def worker() -> None:

            try:

                with self._lianli_hardware_lock:

                    payload = self._refresh_lianli_rpm_payload()

                if not self._closed:

                    self.rpm_refreshed.emit(payload)

            except Exception as error:  # noqa: BLE001

                log_exception("lianli_rpm_refresh_failed", error)

            finally:

                self._rpm_refresh_inflight = False



        thread = threading.Thread(target=worker, daemon=True)

        self._threads.append(thread)

        thread.start()



    def _refresh_lianli_rpm_payload(self) -> dict[str, tuple[int, int, int, int]]:

        backend = None

        try:

            backend = self.backend_factory()

            snapshot = self._list_lianli_devices_with_backend(backend)

            self._remember_lianli_live_devices(snapshot.devices)

            return {

                device.mac: tuple(int(value) for value in device.fan_rpm)

                for device in snapshot.devices

            }

        finally:

            self._close_lianli_backend(backend)



    def _list_lianli_devices_with_backend(self, backend: LianLiWirelessBackend) -> object:

        try:

            return backend.list_devices(page_count=1)

        except TypeError:

            return backend.list_devices()



    def _find_lianli_snapshot_device(
        self,
        devices: list[WirelessDeviceInfo],
        mac: str,
    ) -> WirelessDeviceInfo | None:

        return next((device for device in devices if device.mac.lower() == mac.lower()), None)



    def _apply_live_rpm_snapshot(self, payload: object) -> None:

        if not isinstance(payload, dict):

            return

        parsed: dict[str, tuple[int, int, int, int]] = {}

        for mac, values in payload.items():

            if isinstance(mac, str) and isinstance(values, tuple) and len(values) == 4:

                parsed[mac] = tuple(int(value) for value in values)

        if not parsed:

            return

        self._lianli_live_rpm_by_mac.update(parsed)

        self._lianli_last_valid_rpm_by_mac = self._merge_valid_rpm(

            self._lianli_last_valid_rpm_by_mac,

            parsed,

        )

        self._selected_lianli_target_changed()



    def _remember_lianli_live_devices(self, devices: list[WirelessDeviceInfo]) -> None:

        self._lianli_live_device_by_mac = {device.mac: device for device in devices}

        self._lianli_live_rpm_by_mac = {

            device.mac: tuple(int(value) for value in device.fan_rpm)

            for device in devices

        }

        self._lianli_last_valid_rpm_by_mac = self._merge_valid_rpm(

            self._lianli_last_valid_rpm_by_mac,

            self._lianli_live_rpm_by_mac,

        )



    def _merge_valid_rpm(

        self,

        previous: dict[str, tuple[int, int, int, int]],

        incoming: dict[str, tuple[int, int, int, int]],

    ) -> dict[str, tuple[int, int, int, int]]:

        merged = dict(previous)

        for mac, values in incoming.items():

            old = merged.get(mac, (0, 0, 0, 0))

            stabilized = tuple(

                int(new_value) if int(new_value) > 0 else int(old_value)

                for old_value, new_value in zip(old, values, strict=False)

            )

            merged[mac] = stabilized

        return merged



    def _cached_lianli_target(self) -> LianLiWirelessTargetSettings | None:

        mac = self.settings.lianli_wireless.active_target_mac

        if not mac and hasattr(self, "lianli_target_combo"):

            mac = str(self.lianli_target_combo.currentData() or "")

        return self.settings.lianli_wireless.targets.get(mac)



    def _selected_lianli_wireless_target(self) -> WirelessDeviceInfo:

        target = self._cached_lianli_target()

        if target is None:

            raise ValueError("还没有识别到可用的联力无线风扇组")

        live_target = self._lianli_live_device_by_mac.get(target.mac)

        if live_target is not None and live_target.is_bound:

            return live_target

        return WirelessDeviceInfo(

            mac=target.mac,

            master_mac=target.master_mac,

            channel=target.channel,

            rx_type=target.rx_type,

            device_type=target.device_type,

            fan_count=target.fan_count,

            pwm_values=(0, 0, 0, 0),

            fan_rpm=(0, 0, 0, 0),

            command_sequence=0,

            raw=bytes(42),

        )



    def _update_daily_controls(self) -> None:

        has_target = bool(self.settings.lianli_wireless.targets)

        busy = self._operation_active
        write_unlocked = self._write_unlocked()

        for name in (

            "lianli_daily_pwm_button",

            "lianli_apply_all_fans_button",

            "lianli_daily_pwm_sync_button",

            "lianli_start_loop_button",

        ):

            if hasattr(self, name):

                getattr(self, name).setEnabled(has_target and not busy and write_unlocked)

        if hasattr(self, "lianli_apply_effect_button"):

            # Keep apply clickable during a lighting loop so it can stop and replace the loop.

            self.lianli_apply_effect_button.setEnabled(

                has_target
                and write_unlocked
                and (not busy or self._lianli_loop_effect is not None)

            )

        if hasattr(self, "lianli_stop_loop_button"):

            self.lianli_stop_loop_button.setEnabled(busy)

        if hasattr(self, "lianli_refresh_button"):

            self.lianli_refresh_button.setEnabled(not busy)

        if hasattr(self, "lianli_test_window_button"):

            self.lianli_test_window_button.setEnabled(not busy)

        if self._lianli_test_dialog is not None:

            self._lianli_test_dialog.update_controls()
        if hasattr(self, "lianli_next_action_label"):
            self.lianli_next_action_label.setText(self._daily_lianli_next_action_text(has_target, busy, write_unlocked))


    def _daily_lianli_next_action_text(self, has_target: bool, busy: bool, write_unlocked: bool) -> str:

        if not has_target:

            return "下一步：点击重新识别，读取接收器并选择已绑定风扇组"

        if busy:

            return "下一步：等待当前联力操作完成"

        if not write_unlocked:

            return f"写入锁定：{self._write_blocked_text()}；请勾选“允许联力真实写入”"

        return "可以写入：选择目标转速后，点击“应用到当前风扇组”"



    def scan_local_devices(self) -> None:

        payload = self._scan_usb_payload()

        self.lianli_snapshot_text.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))

        self._set_lianli_status(f"USB 扫描完成：{payload['device_count']} 个已知设备")



    def refresh_live_devices(self) -> None:

        self._run_lianli_operation("正在读取接收器...", self._live_devices_payload)



    def query_live_master(self) -> None:

        self._run_lianli_operation("正在读取 Master...", self._live_master_payload)



    def query_live_lcd_info(self) -> None:

        self._run_lianli_operation("正在读取 LCD 信息...", self._live_lcd_info_payload)



    def run_readonly_validation(self) -> None:

        self._run_lianli_operation("正在执行只读验证...", self._readonly_validation_payload)



    def run_lianli_write_gate(self) -> None:

        self._run_lianli_operation("正在检查写入门禁...", self._write_gate_payload)



    def save_lianli_snapshot(self) -> None:

        text = self.lianli_snapshot_text.toPlainText().strip()

        if not text:

            self._set_lianli_status("没有可保存的联力快照")

            return

        default_path = str(Path(".cache/lianli/gui-snapshot.json"))

        path, _selected_filter = QFileDialog.getSaveFileName(

            self,

            "保存联力快照",

            default_path,

            "JSON (*.json);;All Files (*)",

        )

        if not path:

            return

        destination = Path(path)

        try:

            json.loads(text)

            destination.parent.mkdir(parents=True, exist_ok=True)

            destination.write_text(text + "\n", encoding="utf-8")

        except Exception as error:  # noqa: BLE001

            self._set_lianli_status(f"保存失败：{error}")

            return

        self._set_lianli_status(f"已保存：{destination}")



    def analyze_lianli_log(self) -> None:

        path, _selected_filter = QFileDialog.getOpenFileName(

            self,

            "选择联力 live 写入日志",

            str(Path(".cache/lianli")),

            "JSON (*.json);;All Files (*)",

        )

        if not path:

            return

        self._run_lianli_operation(

            "正在分析日志...",

            lambda: analyze_live_log(Path(path)),

        )



    def diff_lianli_snapshots(self) -> None:

        before_path, _selected_filter = QFileDialog.getOpenFileName(

            self,

            "选择写入前快照",

            str(Path(".cache/lianli")),

            "JSON (*.json);;All Files (*)",

        )

        if not before_path:

            return

        after_path, _selected_filter = QFileDialog.getOpenFileName(

            self,

            "选择写入后快照",

            str(Path(before_path).parent),

            "JSON (*.json);;All Files (*)",

        )

        if not after_path:

            return

        self._run_lianli_operation(

            "正在对比快照...",

            lambda: diff_snapshot_files(Path(before_path), Path(after_path)),

        )



    def summarize_lianli_experiments(self) -> None:

        path = QFileDialog.getExistingDirectory(

            self,

            "选择联力实验日志目录",

            str(Path(".cache/lianli")),

        )

        if not path:

            return

        self._run_lianli_operation(

            "正在汇总实验...",

            lambda: summarize_experiment_dir(Path(path)),

        )



    def choose_lianli_static_color(self) -> None:

        color = QColorDialog.getColor(QColor(self.lianli_static_color), self, "选择联力静态颜色")

        if color.isValid():

            self.set_lianli_static_color(color.name())



    def set_lianli_static_color(self, color: str) -> None:

        self.lianli_static_color = color.lower()

        self._update_lianli_color_button(self.lianli_color_button, self.lianli_static_color, "主色")


    def choose_lianli_accent_color(self) -> None:

        color = QColorDialog.getColor(QColor(self.lianli_accent_color), self, "选择联力点缀色")

        if color.isValid():

            self.set_lianli_accent_color(color.name())


    def set_lianli_accent_color(self, color: str) -> None:

        self.lianli_accent_color = color.lower()

        self._update_lianli_color_button(self.lianli_accent_color_button, self.lianli_accent_color, "点缀色")


    def _update_lianli_color_button(self, button: QPushButton, color: str, label: str) -> None:

        normalized = self._normalize_lianli_hex_color(color)

        button.setObjectName("ColorSwatch")

        button.setFixedSize(34, 28)

        button.setText("")

        button.setToolTip(f"{label} {normalized}")

        button.setStyleSheet(
            f"background: {normalized}; border: 1px solid #6b7376;"
        )

    def _set_lianli_direction(self, direction: str) -> None:

        index = self.lianli_direction_combo.findData(direction)

        if index < 0:

            return

        self.lianli_direction_combo.setCurrentIndex(index)

        self._refresh_lianli_direction_buttons()


    def _refresh_lianli_direction_buttons(self) -> None:

        current = str(self.lianli_direction_combo.currentData() or "left")

        for direction, button in (
            ("left", self.lianli_direction_left_button),
            ("right", self.lianli_direction_right_button),
        ):

            selected = current == direction

            button.setChecked(selected)

            if selected:

                button.setStyleSheet(
                    "background: #12323b; color: #21caff; border: 1px solid #21caff; font-weight: 900;"
                )

            else:

                button.setStyleSheet(
                    "background: #1d2226; color: #6f7780; border: 1px solid #3a4248; font-weight: 900;"
                )


    def choose_lianli_rotation_color(self, index: int) -> None:

        current = self._rotation_colors()[index] if 0 <= index < len(self._rotation_colors()) else "#ffffff"

        color = QColorDialog.getColor(QColor(current), self, "选择联力队列颜色")

        if color.isValid():

            self.set_lianli_rotation_color(index, color.name())


    def set_lianli_rotation_color(self, index: int, color: str) -> None:

        colors = self._rotation_colors()

        if not 0 <= index < len(colors):

            raise IndexError("rotation color index out of range")

        colors[index] = self._normalize_lianli_hex_color(color)

        self._set_lianli_rotation_colors(colors)


    def _set_lianli_rotation_colors(self, colors: list[str]) -> None:

        self._lianli_rotation_color_values = self._fixed_lianli_rotation_colors(colors)

        self._refresh_lianli_rotation_color_buttons()


    def _refresh_lianli_rotation_color_buttons(self) -> None:

        while self.lianli_rotation_colors_layout.count():

            item = self.lianli_rotation_colors_layout.takeAt(0)

            widget = item.widget()

            if widget is not None:

                widget.setParent(None)

                widget.deleteLater()

        for index, color in enumerate(self._rotation_colors()[: self._lianli_rotation_color_slot_count()]):

            button = QPushButton()

            button.setObjectName("ColorSwatch")

            button.setFixedSize(34, 28)

            button.setToolTip(color)

            button.setStyleSheet(f"background: {color}; border: 1px solid #6b7376;")

            button.clicked.connect(lambda _checked=False, color_index=index: self.choose_lianli_rotation_color(color_index))

            self.lianli_rotation_colors_layout.addWidget(button)

            button.show()

        self.lianli_rotation_colors_layout.addStretch(1)


    def _lianli_rotation_color_slot_count(self) -> int:

        try:

            effect = lianli_wireless_effect(str(self.lianli_effect_combo.currentData()))

        except ValueError:

            return 0

        if effect.color_mode != "palette":

            return 0

        return max(1, min(len(self._rotation_colors()), int(effect.color_slots)))



    def preview_lianli_lighting_payload(self) -> None:

        effect = str(self.lianli_effect_combo.currentData())

        target = self._direct_lianli_target()

        payload = {

            "operation": "gui-lianli-lighting-preview",

            "effect": effect,

            "target": target.mac,

            "master_mac": target.master_mac,

            "channel": target.channel,

            "rx_type": target.rx_type,

            "led_count": self.lianli_direct_led_count.value(),

            "speed": self.lianli_speed_slider.value(),

            "interval_ms": self._rainbow_interval_ms(),

            "brightness": self.lianli_brightness_slider.value(),

            "direction": self.lianli_direction_combo.currentData(),

            "static_color": self.lianli_static_color,

            "accent_color": self.lianli_accent_color,

            "rotation_colors": self._rotation_colors(),

        }

        self.lianli_snapshot_text.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))

        self._append_lianli_lighting_log(f"preview effect={effect} interval={payload['interval_ms']}ms")





    def open_lianli_test_window(self) -> None:

        if self._lianli_test_dialog is None:

            self._lianli_test_dialog = LianLiWirelessTestDialog(self)

            self._lianli_test_dialog.destroyed.connect(lambda: setattr(self, "_lianli_test_dialog", None))

        self._lianli_test_dialog.update_controls()

        self._lianli_test_dialog.show()

        self._lianli_test_dialog.raise_()

        self._lianli_test_dialog.activateWindow()



    def apply_lianli_led_probe(
        self,
        probe_count: int | None = None,
        led_total: int | None = None,
        probe_color: tuple[int, int, int] | None = None,
    ) -> None:

        if probe_count is None:

            probe_count = int(self.lianli_probe_count.value()) if hasattr(self, "lianli_probe_count") else 0

        if self._operation_active:

            self.stop_lianli_lighting_loop()

        self._run_lianli_operation(

            f"正在验证灯珠数量（纯色）：点亮前 {probe_count} 颗...",

            lambda: self._send_lianli_led_probe(int(probe_count), led_total, probe_color),

        )

    def apply_lianli_led_force_off(self) -> None:

        if self._operation_active:

            self.stop_lianli_lighting_loop()

        self._run_lianli_operation(

            "正在强制清除联力无线灯光残留...",

            self._send_lianli_led_force_off,

        )

    def _adjust_lianli_probe_count(self, delta: int) -> None:

        if not hasattr(self, "lianli_probe_count"):

            return

        current = int(self.lianli_probe_count.value())

        next_value = current + int(delta)

        next_value = max(self.lianli_probe_count.minimum(), min(self.lianli_probe_count.maximum(), next_value))

        self.lianli_probe_count.setValue(next_value)



    def _send_lianli_led_probe(
        self,
        probe_count: int | None = None,
        led_total: int | None = None,
        probe_color: tuple[int, int, int] | None = None,
    ) -> dict[str, object]:

        if not self._write_unlocked():

            raise ValueError(self._write_blocked_text())

        target = self._selected_lianli_wireless_target()

        if led_total is None:

            led_total = int(self.lianli_direct_led_count.value())

        led_total = max(1, min(255, int(led_total)))

        if probe_count is None:

            probe_count = int(self.lianli_probe_count.value()) if hasattr(self, "lianli_probe_count") else led_total

        lit_count = max(0, min(int(probe_count), led_total))

        if probe_color is None:

            probe_color = (0, 0, 254)

        probe_color = tuple(max(0, min(255, int(value))) for value in probe_color)

        probe_color_text = self._rgb_to_hex(probe_color)



        sender = PyUsbEndpointTransport(RF_SENDER_VID, RF_SENDER_PID)

        packets_written = 0

        try:

            raw = bytearray()

            for led_index in range(led_total):

                if led_index < lit_count:

                    raw.extend(probe_color)

                else:

                    raw.extend((0, 0, 0))

            payloads = build_rgb_frame_payloads(

                target,

                bytes(raw),

                led_count=led_total,

                frame_count=1,

                interval_ms=0,

                effect_index=static_rgb_effect_index(probe_color),

            )

            for payload_index, payload in enumerate(payloads):

                repeat = RGB_FIRST_PAYLOAD_REPEAT_COUNT if payload_index == 0 else 1

                for _ in range(repeat):

                    for packet in build_rf_chunks(target.channel, target.rx_type, payload):

                        written = sender.write(packet)

                        if written != len(packet):

                            raise ValueError(f"联力灯珠验证发送不完整（{written}/{len(packet)}）")

                        packets_written += 1

        finally:

            sender.close()



        self._append_lianli_lighting_log(

            f"led-probe lit={lit_count}/{led_total} color={probe_color_text} packets={packets_written}"

        )

        return {

            "operation": "gui-lianli-led-probe",

            "target": target.mac,

            "master_mac": target.master_mac,

            "led_total": led_total,

            "lit_count": lit_count,

            "color": probe_color_text,
            "raw_color": list(probe_color),

            "packets_written": packets_written,

        }



    def _send_lianli_led_force_off(self) -> dict[str, object]:

        if not self._write_unlocked():

            raise ValueError(self._write_blocked_text())

        target = self._selected_lianli_wireless_target()

        led_totals = sorted({

            max(1, min(255, int(self.lianli_direct_led_count.value()))),

            max(1, min(255, int(infer_led_count(target)))),

            26,

            58,

            96,

            132,

            255,

        })

        sender = PyUsbEndpointTransport(RF_SENDER_VID, RF_SENDER_PID)

        packets_written = 0

        try:

            for led_total in led_totals:

                raw = bytes(led_total * 3)

                payloads = build_rgb_frame_payloads(

                    target,

                    raw,

                    led_count=led_total,

                    frame_count=1,

                    interval_ms=0,

                    effect_index=static_rgb_effect_index((0, 0, 0)),

                )

                for payload_index, payload in enumerate(payloads):

                    repeat = RGB_FIRST_PAYLOAD_REPEAT_COUNT if payload_index == 0 else 1

                    for _ in range(repeat):

                        for packet in build_rf_chunks(target.channel, target.rx_type, payload):

                            written = sender.write(packet)

                            if written != len(packet):

                                raise ValueError(f"联力强制清除发送不完整（{written}/{len(packet)}）")

                            packets_written += 1

        finally:

            sender.close()

        self._append_lianli_lighting_log(

            f"led-force-off totals={led_totals} packets={packets_written}"

        )

        return {

            "operation": "gui-lianli-led-force-off",

            "target": target.mac,

            "master_mac": target.master_mac,

            "led_totals": led_totals,

            "packets_written": packets_written,

        }



    def _next_lianli_led_effect_index(self) -> int:

        self._lianli_led_effect_index += 1

        if self._lianli_led_effect_index > 78999999:

            self._lianli_led_effect_index = 78000001

        return self._lianli_led_effect_index


    def apply_lianli_lighting_once(self) -> None:

        effect = str(self.lianli_effect_combo.currentData())

        if self._operation_active and self._lianli_loop_effect is not None:

            self._pending_lianli_effect = effect

            self.stop_lianli_lighting_loop()

            self._set_lianli_status(f"正在停止 {self._lianli_loop_effect} 循环，随后应用 {effect}...")

            return

        self._run_lianli_operation(

            f"正在应用联力灯效：{effect}...",

            lambda: self._send_lianli_lighting_effect(effect),

        )



    def start_lianli_lighting_loop(self) -> None:

        if self._operation_active:

            self._set_lianli_status("联力操作仍在进行")

            return

        if not self._write_unlocked():

            self._set_lianli_status(self._write_blocked_text())

            return

        self._lighting_loop_stop.clear()

        effect = str(self.lianli_effect_combo.currentData())

        if effect == "rainbow":

            # Snapshot UI values before worker starts; avoids touching deleted Qt widgets in threads.

            try:

                self._rainbow_runtime_config = {

                    "led_count": int(self.lianli_direct_led_count.value()),

                    "speed": int(self.lianli_speed_slider.value()),

                    "brightness": int(self.lianli_brightness_slider.value()),

                    "direction": str(self.lianli_direction_combo.currentData()),

                }

            except Exception:

                pass

        self._set_lianli_busy(True, f"正在循环联力灯效：{effect}...")

        self._lianli_loop_effect = effect

        self._append_lianli_lighting_log(f"loop-start effect={effect}")



        def worker() -> None:

            try:

                result = self._run_lianli_lighting_loop(effect)

                if not self._closed:

                    try:

                        self.operation_finished.emit("灯效循环已停止", result)

                    except Exception:

                        self.status_changed.emit("灯效循环已停止")

            except Exception as error:  # noqa: BLE001

                if not self._closed:

                    self.operation_finished.emit("灯效循环失败", error)



        thread = threading.Thread(target=worker, daemon=True)

        self._threads.append(thread)

        thread.start()



    def stop_lianli_lighting_loop(self) -> None:

        self._lighting_loop_stop.set()

        self._append_lianli_lighting_log("loop-stop requested")


    def turn_off_all_lighting(self) -> None:

        self._lianli_loop_effect = None

        self._pending_lianli_effect = None

        self.stop_lianli_lighting_loop()

        if not self._write_unlocked():

            self._set_lianli_status(f"联力无线灯光未关闭：{self._write_blocked_text()}")

            return

        self._run_lianli_operation(

            "正在关闭联力无线灯光...",

            self._send_lianli_sleep_off,

        )


    def _send_lianli_sleep_off(self) -> dict[str, object]:

        target = self._selected_lianli_wireless_target()

        sender = PyUsbEndpointTransport(RF_SENDER_VID, RF_SENDER_PID)

        try:

            backend = LianLiWirelessBackend(sender=sender)

            packets_written = self._send_lianli_effect_with_backend(backend, target, "off")

        finally:

            sender.close()

        self._append_lianli_lighting_log(f"sleep-off packets={packets_written}")

        self._remember_lianli_effect_settings("off")

        return {

            **self._lighting_result_payload("off", target, packets_written),

            "sleep_off": True,

        }



    def _send_lianli_lighting_effect(self, effect: str) -> dict[str, object]:

        if not self._write_unlocked():

            raise ValueError(self._write_blocked_text())

        target = self._selected_lianli_wireless_target()

        sender = PyUsbEndpointTransport(RF_SENDER_VID, RF_SENDER_PID)

        try:

            backend = LianLiWirelessBackend(sender=sender)

            packets_written = self._send_lianli_effect_with_backend(backend, target, effect)

        finally:

            sender.close()

        self._append_lianli_lighting_log(f"write effect={effect} packets={packets_written}")

        self._remember_lianli_effect_settings(effect)

        return self._lighting_result_payload(effect, target, packets_written)



    def _run_lianli_lighting_loop(self, effect: str) -> dict[str, object]:

        target = self._selected_lianli_wireless_target()

        sender = PyUsbEndpointTransport(RF_SENDER_VID, RF_SENDER_PID)

        total_packets = 0

        iterations = 0

        try:

            backend = LianLiWirelessBackend(sender=sender)

            if effect == "rotate":

                colors = self._rotation_colors()

                hold_seconds = self.lianli_rotation_hold.value() / 1000.0

                while not self._lighting_loop_stop.is_set():

                    color = colors[iterations % len(colors)]

                    total_packets += backend.send_static_rgb(

                        target,

                        self._hex_to_rgb(color),

                        led_count=self.lianli_direct_led_count.value(),

                    )

                    iterations += 1

                    self._lighting_loop_stop.wait(hold_seconds)

            elif effect == "rainbow":

                total_packets, iterations = self._stream_rainbow_with_backend(backend, target)

            else:

                delay = 0.8

                while not self._lighting_loop_stop.is_set():

                    total_packets += self._send_lianli_effect_with_backend(backend, target, effect)

                    iterations += 1

                    self._lighting_loop_stop.wait(delay)

        finally:

            sender.close()

        return {

            **self._lighting_result_payload(effect, target, total_packets),

            "iterations": iterations,

            "loop_stopped": True,

        }


    def _lianli_tlv2_effect_name(self, effect: str) -> str:
        return lianli_wireless_effect(effect).backend_key



    def _send_lianli_effect_with_backend(

        self,

        backend: LianLiWirelessBackend,

        target: WirelessDeviceInfo,

        effect: str,

    ) -> int:

        cached_layout = self.settings.lianli_wireless.targets.get(target.mac)

        led_count = self.lianli_direct_led_count.value()

        direction_override = ""

        if cached_layout is not None:

            led_count = max(1, min(255, int(cached_layout.led_count or led_count)))

            self.lianli_direct_led_count.setValue(led_count)

            direction_override = str(cached_layout.direction or "")

            if direction_override in {"left", "right"}:

                index = self.lianli_direction_combo.findData(direction_override)

                if index >= 0:

                    self.lianli_direction_combo.setCurrentIndex(index)

        if effect == "static":

            return backend.send_static_rgb(

                target,

                self._hex_to_rgb(self.lianli_static_color),

                led_count=led_count,

            )

        if effect == "off":

            return backend.send_static_rgb(target, (0, 0, 0), led_count=led_count)

        try:

            effect_info = lianli_wireless_effect(effect)

        except ValueError as error:

            raise ValueError(str(error)) from error

        if effect_info.backend_key in {"static", "off"}:

            raise ValueError(f"unexpected catalog dispatch for {effect_info.backend_key}")

        primary_color = self._hex_to_rgb(self.lianli_static_color)

        accent_color = self._hex_to_rgb(self.lianli_accent_color)

        palette = [self._hex_to_rgb(color) for color in self._rotation_colors()[: max(1, effect_info.color_slots)]]

        effect_name = effect_info.backend_key

        capability = tlv2_effect_capability(effect_name)

        if capability.uses_palette:

            if capability.uses_primary_color and palette:

                primary_color = palette[0]

            if capability.uses_accent_color and len(palette) > 1:

                accent_color = palette[1]

        direction = direction_override if direction_override in {"left", "right"} else str(self.lianli_direction_combo.currentData() or "left")

        effect_index = tlv2_color_effect_index(

            effect_name,

            primary_color,

            accent_color=accent_color,

            palette=palette,

            direction=direction,
        )

        kwargs: dict[str, object] = {
            "brightness": self.lianli_brightness_slider.value(),
            "led_count": led_count,
            "effect_index": effect_index,
        }

        if capability.uses_primary_color:
            kwargs["color"] = primary_color

        if capability.uses_accent_color:
            kwargs["accent_color"] = accent_color

        if capability.uses_palette:
            kwargs["palette"] = palette

        if capability.uses_direction:
            kwargs["direction"] = direction

        return backend.send_tlv2_effect(target, effect_name, **kwargs)

    def _speed_to_rainbow_refresh_s(self, speed: int) -> float:

        clamped = max(0, min(100, int(speed)))

        # 0 -> 120ms, 100 -> 12ms

        return 0.12 - (0.108 * clamped / 100.0)



    def _send_single_rainbow_frame(

        self,

        backend: LianLiWirelessBackend,

        target: WirelessDeviceInfo,

        *,

        phase: float,

        effect_index: int,

        runtime: dict[str, object] | None = None,

    ) -> int:

        cfg = dict(self._rainbow_runtime_config if runtime is None else runtime)

        detected_led_count = infer_led_count(target)

        led_count = max(int(cfg.get("led_count", 26)), int(detected_led_count))

        # Some LianLi fan models expose dual lighting zones while snapshots report 26.

        # Expand to 52 for rainbow streaming so the full ring/dual-zone animates.

        if led_count == 26:

            led_count = 52

        brightness_scale = max(0.0, min(1.0, int(cfg.get("brightness", 100)) / 100.0))

        direction = str(cfg.get("direction", "left"))

        shift = int(phase) % max(1, led_count)

        if direction != "left":

            shift = (-shift) % max(1, led_count)

        raw = bytearray()

        for led_index in range(led_count):

            shifted_index = (led_index + shift) % max(1, led_count)

            hue = shifted_index / max(1, led_count)

            r_f, g_f, b_f = colorsys.hsv_to_rgb(hue, 1.0, brightness_scale)

            raw.extend((int(r_f * 255), int(g_f * 255), int(b_f * 255)))

        payloads = build_rgb_frame_payloads(

            target,

            bytes(raw),

            led_count=led_count,

            frame_count=1,

            interval_ms=0,

            effect_index=effect_index,

        )

        packet_count = 0

        for payload in payloads:

            for packet in build_rf_chunks(target.channel, target.rx_type, payload):

                written = backend.sender.write(packet)

                if written != len(packet):

                    raise ValueError(f"联力彩虹帧发送不完整（{written}/{len(packet)}）")

                packet_count += 1

        return packet_count



    def _stream_rainbow_with_backend(

        self,

        backend: LianLiWirelessBackend,

        target: WirelessDeviceInfo,

    ) -> tuple[int, int]:

        total_packets = 0

        frame_counter = 0

        phase = 0.0

        effect_index = 77200001

        runtime = dict(self._rainbow_runtime_config)

        while not self._lighting_loop_stop.is_set():

            speed = int(runtime.get("speed", 75))

            sleep_seconds = self._speed_to_rainbow_refresh_s(speed)

            total_packets += self._send_single_rainbow_frame(

                backend,

                target,

                phase=float(frame_counter),

                effect_index=effect_index,

                runtime=runtime,

            )

            frame_counter += 1

            effect_index += 1

            if effect_index > 77299999:

                effect_index = 77200001

            self._lighting_loop_stop.wait(sleep_seconds)

        return total_packets, frame_counter



    def _send_lianli_direct_pwm(self, pwm: int) -> dict[str, object]:

        target = self._selected_lianli_wireless_target()

        backend = None

        try:

            backend = self.backend_factory()

            before = self._list_lianli_devices_with_backend(backend)

            self._remember_lianli_live_devices(before.devices)

            live_target = self._find_lianli_snapshot_device(before.devices, target.mac)

            if live_target is not None and live_target.is_bound:

                target = live_target

            packets_written = backend.send_pwm(target, [pwm])

            time.sleep(0.25)

            after = self._list_lianli_devices_with_backend(backend)

            self._remember_lianli_live_devices(after.devices)

        finally:

            self._close_lianli_backend(backend)

        return {

            "operation": "lianli-fan-pwm",

            "target": target.mac,

            "pwm": pwm,

            "packets_written": packets_written,

            "before": {
                "device_count": before.device_count,
                "devices": [_wireless_device_payload(device) for device in before.devices],
            },

            "after": {
                "device_count": after.device_count,
                "devices": [_wireless_device_payload(device) for device in after.devices],
            },

        }



    def _send_lianli_direct_pwm_all(self, pwm: int) -> dict[str, object]:

        backend = None

        packets_written = 0

        targets_written: list[str] = []

        try:

            backend = self.backend_factory()

            before = self._list_lianli_devices_with_backend(backend)

            self._remember_lianli_live_devices(before.devices)

            for target_settings in self.settings.lianli_wireless.targets.values():

                target = WirelessDeviceInfo(

                    mac=target_settings.mac,

                    master_mac=target_settings.master_mac,

                    channel=target_settings.channel,

                    rx_type=target_settings.rx_type,

                    device_type=target_settings.device_type,

                    fan_count=target_settings.fan_count,

                    pwm_values=(0, 0, 0, 0),

                    fan_rpm=(0, 0, 0, 0),

                    command_sequence=0,

                    raw=bytes(42),

                )

                live_target = self._find_lianli_snapshot_device(before.devices, target.mac)

                if live_target is not None and live_target.is_bound:

                    target = live_target

                packets_written += backend.send_pwm(target, [pwm])

                targets_written.append(target.mac)

            time.sleep(0.25)

            after = self._list_lianli_devices_with_backend(backend)

            self._remember_lianli_live_devices(after.devices)

        finally:

            self._close_lianli_backend(backend)

        return {

            "operation": "lianli-fan-pwm-all",

            "targets": targets_written,

            "pwm": pwm,

            "packets_written": packets_written,

            "before": {
                "device_count": before.device_count,
                "devices": [_wireless_device_payload(device) for device in before.devices],
            },

            "after": {
                "device_count": after.device_count,
                "devices": [_wireless_device_payload(device) for device in after.devices],
            },

        }



    def _send_lianli_direct_pwm_sync(self) -> dict[str, object]:

        target = self._selected_lianli_wireless_target()

        backend = None

        try:

            backend = self.backend_factory()

            before = self._list_lianli_devices_with_backend(backend)

            self._remember_lianli_live_devices(before.devices)

            live_target = self._find_lianli_snapshot_device(before.devices, target.mac)

            if live_target is not None and live_target.is_bound:

                target = live_target

            packets_written = backend.send_motherboard_pwm_sync(target)

            time.sleep(0.25)

            after = self._list_lianli_devices_with_backend(backend)

            self._remember_lianli_live_devices(after.devices)

        finally:

            self._close_lianli_backend(backend)

        return {

            "operation": "lianli-fan-pwm-sync",

            "target": target.mac,

            "packets_written": packets_written,

            "before": {
                "device_count": before.device_count,
                "devices": [_wireless_device_payload(device) for device in before.devices],
            },

            "after": {
                "device_count": after.device_count,
                "devices": [_wireless_device_payload(device) for device in after.devices],
            },

        }



    def _remember_lianli_effect_settings(self, effect: str) -> None:

        self.settings.lianli_wireless.effect = effect

        self.settings.lianli_wireless.color = self.lianli_static_color

        self.settings.lianli_wireless.accent_color = self.lianli_accent_color

        self.settings.lianli_wireless.rotation_colors = ",".join(self._rotation_colors())

        self.settings.lianli_wireless.brightness = self.lianli_brightness_slider.value()

        self.settings.lianli_wireless.speed = self.lianli_speed_slider.value()

        self.settings.lianli_wireless.direction = str(self.lianli_direction_combo.currentData())

        save_settings(self.settings)



    def _lighting_result_payload(

        self,

        effect: str,

        target: WirelessDeviceInfo,

        packets_written: int,

    ) -> dict[str, object]:

        return {

            "operation": "gui-lianli-lighting",

            "effect": effect,

            "target": target.mac,

            "master_mac": target.master_mac,

            "channel": target.channel,

            "rx_type": target.rx_type,

            "led_count": self.lianli_direct_led_count.value(),

            "speed": self.lianli_speed_slider.value(),

            "interval_ms": self._rainbow_interval_ms(),

            "brightness": self.lianli_brightness_slider.value(),

            "direction": self.lianli_direction_combo.currentData(),

            "packets_written": packets_written,

        }



    def _direct_lianli_target(self) -> WirelessDeviceInfo:

        return WirelessDeviceInfo(

            mac=self.lianli_direct_mac_input.text().strip(),

            master_mac=self.lianli_direct_master_input.text().strip(),

            channel=self.lianli_direct_channel.value(),

            rx_type=self.lianli_direct_rx_type.value(),

            device_type=0,

            fan_count=4,

            pwm_values=(0, 0, 0, 0),

            fan_rpm=(0, 0, 0, 0),

            command_sequence=0,

            raw=bytes(42),

        )



    def _rainbow_interval_ms(self) -> int:

        speed = self.lianli_speed_slider.value()

        # Captured from official L-Connect rainbow:

        # speed25->72ms, speed50->60ms, speed75->48ms, speed100->36ms.

        # Keep the same slope but clamp to verified interval range.

        return max(36, min(72, round(84 - speed * 0.48)))



    def _on_lianli_speed_changed(self, value: int) -> None:

        self.lianli_speed_value.setText(f"{value}%")

        if hasattr(self, "lianli_rainbow_interval"):

            self.lianli_rainbow_interval.setValue(self._rainbow_interval_ms())



    def _rotation_colors(self) -> list[str]:

        return list(getattr(self, "_lianli_rotation_color_values", ["#00fe00"])) or ["#00fe00"]


    def _parse_lianli_color_list(self, text: str) -> list[str]:

        colors = [item.strip() for item in str(text).split(",") if item.strip()]

        return self._fixed_lianli_rotation_colors(colors)


    def _fixed_lianli_rotation_colors(self, colors: list[str]) -> list[str]:

        normalized = [self._normalize_lianli_hex_color(color) for color in colors[: len(LIANLI_DEFAULT_ROTATION_COLORS)]]

        while len(normalized) < len(LIANLI_DEFAULT_ROTATION_COLORS):

            normalized.append(LIANLI_DEFAULT_ROTATION_COLORS[len(normalized)])

        return normalized


    def _normalize_lianli_hex_color(self, color: str) -> str:

        value = str(color).strip().lower()

        if not value.startswith("#"):

            value = f"#{value}"

        if len(value) != 7:

            raise ValueError(f"无效 RGB 颜色：{color}")

        int(value[1:], 16)

        return value



    def _hex_to_rgb(self, color: str) -> tuple[int, int, int]:

        value = color.strip().lower()

        if value.startswith("#"):

            value = value[1:]

        if len(value) != 6:

            raise ValueError(f"无效 RGB 颜色：{color}")

        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))

    def _rgb_to_hex(self, color: tuple[int, int, int]) -> str:

        red, green, blue = (max(0, min(255, int(value))) for value in color)

        return f"#{red:02x}{green:02x}{blue:02x}"



    def _append_lianli_lighting_log(self, text: str) -> None:

        if hasattr(self, "lianli_lighting_log"):

            self.lianli_lighting_log.append(text)



    def _lianli_fan_mode_changed(self) -> None:

        if self._updating_lianli_fan_controls:

            return

        mode = str(self.lianli_fan_mode_combo.currentData() or "custom")

        self.settings.lianli_wireless.fan_mode = mode
        self._reset_lianli_curve_write_cache()

        self._set_lianli_curve_editor_points(mode)

        self._update_lianli_curve_hint()

        save_settings(self.settings)

        if self._lianli_latest_telemetry is not None:

            self._apply_lianli_active_curve(self._lianli_latest_telemetry)



    def _lianli_target_rpm_changed(self, value: int) -> None:

        if self._updating_lianli_fan_controls:

            return

        rpm = int(value)

        mode = "custom"

        self._set_lianli_fan_mode_combo(mode)

        self.settings.lianli_wireless.fan_mode = mode

        self.settings.lianli_wireless.fan_rpm = rpm

        self.settings.lianli_wireless.pwm = self._lianli_rpm_to_pwm(rpm)
        self._reset_lianli_curve_write_cache()

        self._set_lianli_curve_editor_points(mode)

        self._update_lianli_curve_hint()

        save_settings(self.settings)



    def _restore_lianli_fan_mode_selection(self) -> None:

        mode = str(getattr(self.settings.lianli_wireless, "fan_mode", "") or "")

        if mode not in {"quiet", "normal", "high", "full", "custom"}:

            mode = self._lianli_fan_mode_for_rpm(int(self.settings.lianli_wireless.fan_rpm))

        self._updating_lianli_fan_controls = True

        try:

            self._set_lianli_fan_mode_combo(mode)

        finally:

            self._updating_lianli_fan_controls = False

        self._set_lianli_curve_editor_points(mode)



    def _set_lianli_fan_mode_combo(self, mode: str) -> None:

        index = self.lianli_fan_mode_combo.findData(mode)

        if index < 0:

            index = self.lianli_fan_mode_combo.findData("custom")

        if index >= 0 and self.lianli_fan_mode_combo.currentIndex() != index:

            blocked = self.lianli_fan_mode_combo.blockSignals(True)

            try:

                self.lianli_fan_mode_combo.setCurrentIndex(index)

            finally:

                self.lianli_fan_mode_combo.blockSignals(blocked)



    def _lianli_fan_mode_rpm(self, mode: str) -> int | None:

        return {

            "quiet": 600,

            "normal": 1000,

            "high": 1400,

            "full": 1800,

        }.get(mode)



    def _lianli_fan_mode_for_rpm(self, rpm: int) -> str:

        return {

            600: "quiet",

            1000: "normal",

            1400: "high",

            1800: "full",

        }.get(int(rpm), "custom")



    def _lianli_fan_mode_label(self, mode: str) -> str:

        return {

            "quiet": "安静",

            "normal": "标准",

            "high": "高速",

            "full": "全速",

            "custom": "自定义",

        }.get(mode, "自定义")



    def _lianli_active_fan_mode(self) -> str:

        mode = str(self.lianli_fan_mode_combo.currentData() or self.settings.lianli_wireless.fan_mode or "custom")

        return mode if mode in LIANLI_FAN_CURVE_MODES else "custom"



    def _default_lianli_curve_points(self, mode: str) -> list[list[int]]:

        points = DEFAULT_LIANLI_FAN_CURVE_PROFILES.get(mode, DEFAULT_LIANLI_FAN_CURVE_PROFILES["custom"])

        return [list(point) for point in points]



    def _sanitize_lianli_curve_points(self, points: object, mode: str = "custom") -> list[list[int]]:

        default = self._default_lianli_curve_points(mode)

        if not isinstance(points, list) or len(points) < 2:

            return default

        parsed: list[list[int]] = []

        for item in points[:12]:

            if isinstance(item, (list, tuple)) and len(item) == 2:

                parsed.append([

                    max(0, min(100, int(item[0]))),

                    max(0, min(1800, int(item[1]))),

                ])

        while len(parsed) < 2:

            parsed.append(default[len(parsed)])

        return sorted(parsed, key=lambda item: item[0])



    def _lianli_curve_points(self, mode: str | None = None) -> list[list[int]]:

        mode = mode if mode in LIANLI_FAN_CURVE_MODES else self._lianli_active_fan_mode()

        if mode == "custom":

            default_custom = self._default_lianli_curve_points("custom")

            profiles = getattr(self.settings.lianli_wireless, "fan_curve_profiles", None)

            profile_points = (

                self._sanitize_lianli_curve_points(profiles.get("custom"), "custom")

                if isinstance(profiles, dict) and "custom" in profiles

                else default_custom

            )

            legacy_points = self._sanitize_lianli_curve_points(

                getattr(self.settings.lianli_wireless, "fan_curve_points", None),

                mode,

            )

            if profile_points != default_custom:

                return profile_points

            if legacy_points != default_custom:

                return legacy_points

            return profile_points

        profiles = getattr(self.settings.lianli_wireless, "fan_curve_profiles", None)

        if isinstance(profiles, dict) and mode in profiles:

            return self._sanitize_lianli_curve_points(profiles.get(mode), mode)

        return self._default_lianli_curve_points(mode)



    def _set_lianli_curve_editor_points(self, mode: str | None = None) -> None:

        if not hasattr(self, "lianli_curve_editor"):

            return

        self._updating_lianli_fan_controls = True

        try:

            self.lianli_curve_editor.set_points(self._lianli_curve_points(mode))

        finally:

            self._updating_lianli_fan_controls = False



    def _lianli_curve_changed(self, points: object | None = None) -> None:

        if self._updating_lianli_fan_controls:

            return

        if points is None:

            points = self.lianli_curve_editor.points()

        if not isinstance(points, list):

            return

        mode = self._lianli_active_fan_mode()

        sanitized = self._sanitize_lianli_curve_points(points, mode)

        profiles = getattr(self.settings.lianli_wireless, "fan_curve_profiles", None)

        if not isinstance(profiles, dict):

            profiles = {}

            self.settings.lianli_wireless.fan_curve_profiles = profiles

        profiles[mode] = sanitized

        if mode == "custom":

            self.settings.lianli_wireless.fan_curve_points = sanitized

        self._reset_lianli_curve_write_cache()

        save_settings(self.settings)

        self._update_lianli_curve_hint()



    def _update_lianli_curve_hint(self) -> None:

        if not hasattr(self, "lianli_curve_hint"):

            return

        mode = self._lianli_active_fan_mode()

        points = sorted(self._lianli_curve_points(mode), key=lambda item: item[0])

        summary = "，".join(f"{temp}°C→{rpm} RPM" for temp, rpm in points)

        self.lianli_curve_hint.setText(
            f"{self._lianli_fan_mode_label(mode)} CPU 曲线：{summary}；"
            "只有同时启用自动曲线和真实写入时才会随温度写入"
        )



    def update_telemetry(self, telemetry: SystemTelemetry | None) -> None:

        self._lianli_latest_telemetry = telemetry

        if telemetry is None:

            return

        self._apply_lianli_active_curve(telemetry)



    def _apply_lianli_active_curve(self, telemetry: SystemTelemetry) -> None:

        if self._closed or self._operation_active:

            return

        mode = self._lianli_active_fan_mode()

        if mode not in LIANLI_FAN_CURVE_MODES:

            return

        if not self._auto_curve_unlocked() or not self._write_unlocked() or not self.settings.lianli_wireless.targets:

            return

        temperature_c = telemetry.cpu.package_temperature_c

        if temperature_c is None:

            return

        rpm = self._lianli_curve_rpm_for_temperature(float(temperature_c))

        if rpm is None:

            return

        pwm = self._lianli_rpm_to_pwm(rpm)

        if self._lianli_curve_last_rpm is not None and abs(rpm - self._lianli_curve_last_rpm) < 50:

            return

        self._set_lianli_target_rpm_display(rpm)

        self.settings.lianli_wireless.fan_mode = mode

        self.settings.lianli_wireless.fan_rpm = rpm

        self.settings.lianli_wireless.pwm = pwm

        save_settings(self.settings)

        self._lianli_curve_last_rpm = rpm

        self._lianli_curve_last_pwm = pwm

        self._lianli_curve_pending_pwm = pwm

        def operation() -> dict[str, object]:

            result = self._send_lianli_direct_pwm(pwm)

            result["curve_source"] = "cpu"

            result["curve_mode"] = mode

            result["curve_temperature_c"] = float(temperature_c)

            result["curve_rpm"] = rpm

            return result

        self._run_lianli_operation(

            f"联力温度曲线：CPU {float(temperature_c):.0f}°C -> {rpm} RPM...",

            operation,

        )



    def _lianli_curve_rpm_for_temperature(self, temperature_c: float) -> int | None:

        points = sorted(self._lianli_curve_points(), key=lambda item: item[0])

        if len(points) < 2:

            return None

        if temperature_c <= points[0][0]:

            return int(points[0][1])

        if temperature_c >= points[-1][0]:

            return int(points[-1][1])

        for index in range(len(points) - 1):

            left_temp, left_rpm = points[index]

            right_temp, right_rpm = points[index + 1]

            if left_temp <= temperature_c <= right_temp:

                span = max(1.0, float(right_temp - left_temp))

                ratio = (temperature_c - left_temp) / span

                return round((left_rpm + (right_rpm - left_rpm) * ratio) / 10) * 10

        return int(points[-1][1])



    def _set_lianli_target_rpm_display(self, rpm: int) -> None:

        self._updating_lianli_fan_controls = True

        try:

            self.lianli_rpm_value.setValue(rpm)

            self.lianli_rpm_slider.setValue(rpm)

        finally:

            self._updating_lianli_fan_controls = False



    def _reset_lianli_curve_write_cache(self) -> None:

        self._lianli_curve_last_rpm = None

        self._lianli_curve_last_pwm = None

        self._lianli_curve_pending_pwm = None



    def _lianli_rpm_to_pwm(self, rpm: int) -> int:

        return max(0, min(255, round(max(0, min(1800, int(rpm))) * 255 / 1800)))



    def _update_lianli_effect_fields(self) -> None:

        effect = str(self.lianli_effect_combo.currentData() or "off")

        try:

            effect_info = lianli_wireless_effect(effect)

        except ValueError:

            effect_info = lianli_wireless_effect("off")

        has_speed = effect_info.uses_speed

        has_brightness = effect_info.uses_brightness

        has_color = effect_info.color_mode in {"primary", "primary_accent"}

        has_accent = effect_info.color_mode == "primary_accent" and effect_info.color_slots >= 2

        has_rotation = effect_info.color_mode == "palette"

        has_direction = effect_info.uses_direction

        has_hold = False

        for widget in (self.lianli_speed_label, self.lianli_speed_slider, self.lianli_speed_value):

            widget.setVisible(has_speed)

        for widget in (self.lianli_brightness_label, self.lianli_brightness_slider, self.lianli_brightness_value):

            widget.setVisible(has_brightness)

        has_color_controls = has_color or has_accent

        for widget in (self.lianli_color_label, self.lianli_color_controls):

            widget.setVisible(has_color_controls)

        self.lianli_color_button.setVisible(has_color)

        self.lianli_accent_color_button.setVisible(has_accent)

        for widget in (self.lianli_rotation_label, self.lianli_rotation_colors):

            widget.setVisible(has_rotation)

        if has_rotation:

            self._refresh_lianli_rotation_color_buttons()

        for widget in (self.lianli_direction_label, self.lianli_direction_controls):

            widget.setVisible(has_direction)

        for widget in (self.lianli_hold_label, self.lianli_rotation_hold):

            widget.setVisible(has_hold)

        self.lianli_start_loop_button.setVisible(False)

        self.lianli_stop_loop_button.setVisible(False)



    def send_lianli_target_rpm(self) -> None:

        if not self._write_unlocked():

            self.lianli_status_label.setText(self._write_blocked_text())

            return

        rpm = self.lianli_rpm_value.value()

        pwm = self._lianli_rpm_to_pwm(rpm)

        self.settings.lianli_wireless.fan_rpm = rpm

        self.settings.lianli_wireless.pwm = pwm

        save_settings(self.settings)

        self._run_lianli_operation(

            f"正在应用联力风扇转速：{rpm} RPM...",

            lambda: self._send_lianli_direct_pwm(pwm),

        )



    def send_lianli_target_rpm_all(self) -> None:

        if not self._write_unlocked():

            self.lianli_status_label.setText(self._write_blocked_text())

            return

        rpm = self.lianli_rpm_value.value()

        pwm = self._lianli_rpm_to_pwm(rpm)

        self.settings.lianli_wireless.fan_rpm = rpm

        self.settings.lianli_wireless.pwm = pwm

        save_settings(self.settings)

        self._run_lianli_operation(

            f"正在应用全部联力风扇组：{rpm} RPM...",

            lambda: self._send_lianli_direct_pwm_all(pwm),

        )



    def send_live_pwm(self) -> None:

        if not self._write_unlocked():

            self.lianli_status_label.setText(self._write_blocked_text())

            return

        pwm = self.lianli_pwm_value.value() if hasattr(self, "lianli_pwm_value") else self.settings.lianli_wireless.pwm

        self._run_guarded_write(

            f"正在发送原始 PWM：{pwm}...",

            lambda backend, target: backend.send_pwm(target, [pwm]),

        )



    def run_safe_pwm_experiment(self) -> None:

        if not self._write_unlocked():

            self.lianli_status_label.setText(self._write_blocked_text())

            return

        mac = self.lianli_mac_input.text().strip()

        if not mac:

            self.lianli_status_label.setText("请填写目标 MAC")

            return

        pwm = self.lianli_pwm_value.value()

        self._run_safe_receiver_experiment(

            busy_message="正在执行安全 PWM 实验...",

            mac=mac,

            output_dir=self.experiment_output_dir,

            result_operation="gui-safe-pwm-experiment",

            write_operation="live-pwm",

            write_filename="live-pwm.json",

            analysis_filename="analyze-live-pwm.json",

            write_fields={"pwm_values": [pwm, pwm, pwm, pwm]},

            result_fields={"pwm_values": [pwm, pwm, pwm, pwm]},

            writer=lambda backend, target: backend.send_pwm(target, [pwm]),

        )



    def send_live_pwm_sync(self) -> None:

        if not self._write_unlocked():

            self.lianli_status_label.setText(self._write_blocked_text())

            return

        self._run_lianli_operation(

            "正在启用主板 PWM 同步...",

            self._send_lianli_direct_pwm_sync,

        )



    def run_safe_sync_experiment(self) -> None:

        if not self._write_unlocked():

            self.lianli_status_label.setText(self._write_blocked_text())

            return

        mac = self.lianli_mac_input.text().strip()

        if not mac:

            self.lianli_status_label.setText("请填写目标 MAC")

            return

        expected_pwm_values = [6, 6, 6, 6]

        fields = {

            "enabled": True,

            "fallback_pwm": 100,

            "expected_pwm_values": expected_pwm_values,

        }

        self._run_safe_receiver_experiment(

            busy_message="正在执行安全 Sync 实验...",

            mac=mac,

            output_dir=self.sync_experiment_output_dir,

            result_operation="gui-safe-sync-experiment",

            write_operation="live-pwm-sync",

            write_filename="live-pwm-sync.json",

            analysis_filename="analyze-live-pwm-sync.json",

            write_fields=fields,

            result_fields=fields,

            writer=lambda backend, target: backend.send_motherboard_pwm_sync(target),

        )



    def send_live_pwm_mirror(self) -> None:

        if not self._write_unlocked():

            self.lianli_status_label.setText(self._write_blocked_text())

            return

        mac = self.lianli_mac_input.text().strip()

        if not mac:

            self.lianli_status_label.setText("请填写目标 MAC")

            return



        def operation() -> dict[str, object]:

            backend = None

            try:

                backend = self.backend_factory()

                before = backend.list_devices()

                motherboard_pwm = self._safe_snapshot_motherboard_pwm(before.motherboard_pwm)

                target = next((device for device in before.devices if device.mac.lower() == mac.lower()), None)

                if target is None:

                    raise ValueError(f"未找到接收器 MAC：{mac}")

                packets_written = backend.send_motherboard_pwm_mirror(target, motherboard_pwm)

                after = backend.list_devices()

                return {

                    "operation": "live-pwm-mirror",

                    "target": target.mac,

                    "motherboard_pwm": motherboard_pwm,

                    "pwm_values": [motherboard_pwm] * 4,

                    "packets_written": packets_written,

                    "before": _wireless_device_payload(target),

                    "after": {

                        "device_count": after.device_count,

                        "motherboard_pwm": after.motherboard_pwm,

                        "devices": [_wireless_device_payload(device) for device in after.devices],

                    },

                }

            finally:

                self._close_lianli_backend(backend)



        self._run_lianli_operation("正在镜像主板 PWM...", operation)



    def run_safe_pwm_mirror_experiment(self) -> None:

        if not self._write_unlocked():

            self.lianli_status_label.setText(self._write_blocked_text())

            return

        mac = self.lianli_mac_input.text().strip()

        if not mac:

            self.lianli_status_label.setText("请填写目标 MAC")

            return



        def operation() -> dict[str, object]:

            backend = None

            try:

                backend = self.backend_factory()

                output_dir = self.mirror_experiment_output_dir

                output_dir.mkdir(parents=True, exist_ok=True)

                before = backend.list_devices()

                motherboard_pwm = self._safe_snapshot_motherboard_pwm(before.motherboard_pwm)

                pwm_values = [motherboard_pwm] * 4

                before_payload = {

                    "operation": "live-list-before",

                    "device_count": before.device_count,

                    "motherboard_pwm": before.motherboard_pwm,

                    "devices": [_wireless_device_payload(device) for device in before.devices],

                }

                before_path = output_dir / "live-list-before.json"

                before_path.write_text(json.dumps(before_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")



                target = next((device for device in before.devices if device.mac.lower() == mac.lower()), None)

                if target is None:

                    raise ValueError(f"未找到接收器 MAC：{mac}")

                packets_written = backend.send_motherboard_pwm_mirror(target, motherboard_pwm)



                after = backend.list_devices()

                after_payload = {

                    "operation": "live-list-after",

                    "device_count": after.device_count,

                    "motherboard_pwm": after.motherboard_pwm,

                    "devices": [_wireless_device_payload(device) for device in after.devices],

                }

                after_path = output_dir / "live-list-after.json"

                after_path.write_text(json.dumps(after_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")



                write_payload = {

                    "operation": "live-pwm-mirror",

                    "target": target.mac,

                    "motherboard_pwm": motherboard_pwm,

                    "pwm_values": pwm_values,

                    "packets_written": packets_written,

                    "before": _wireless_device_payload(target),

                    "after": after_payload,

                }

                write_path = output_dir / "live-pwm-mirror.json"

                write_path.write_text(json.dumps(write_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")



                analysis_payload = analyze_live_log(write_path)

                analysis_path = output_dir / "analyze-live-pwm-mirror.json"

                analysis_path.write_text(json.dumps(analysis_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                summary_payload = summarize_experiment_dir(output_dir)

                summary_path = output_dir / "summary.json"

                summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                return {

                    "operation": "gui-safe-pwm-mirror-experiment",

                    "target": target.mac,

                    "motherboard_pwm": motherboard_pwm,

                    "pwm_values": pwm_values,

                    "output_dir": str(output_dir),

                    "packets_written": packets_written,

                    "likely_effective": analysis_payload["likely_effective"],

                    "steps": [

                        {"name": "before", "path": str(before_path)},

                        {"name": "write", "path": str(write_path)},

                        {"name": "after", "path": str(after_path)},

                        {"name": "analysis", "path": str(analysis_path)},

                        {"name": "summary", "path": str(summary_path)},

                    ],

                    "analysis": analysis_payload,

                    "summary": summary_payload,

                }

            finally:

                self._close_lianli_backend(backend)



        self._run_lianli_operation("正在执行安全 Mirror 实验...", operation)



    def send_live_rgb_off(self) -> None:

        self._run_guarded_write(

            "正在关闭无线灯光...",

            lambda backend, target: backend.send_static_rgb(target, (0, 0, 0)),

        )



    def send_live_rainbow(self) -> None:

        frame_count = int(self.lianli_rainbow_frame_count.value())

        interval_ms = int(self.lianli_rainbow_interval.value())

        self._run_guarded_write(

            "正在发送彩虹灯效...",

            lambda backend, target: backend.send_rainbow_rgb(

                target,

                frame_count=frame_count,

                interval_ms=interval_ms,

            ),

        )



    def run_safe_rgb_experiment(self) -> None:

        if not self._write_unlocked():

            self.lianli_status_label.setText(self._write_blocked_text())

            return

        mac = self.lianli_mac_input.text().strip()

        if not mac:

            self.lianli_status_label.setText("请填写目标 MAC")

            return

        color = (0, 0, 0)
        effect_index = static_rgb_effect_index(color)



        def mark_visual_confirmation(analysis_payload: dict[str, object]) -> dict[str, object]:

            required = not bool(analysis_payload["snapshot_changed"])

            analysis_payload["visual_confirmation_required"] = required

            return {"visual_confirmation_required": required}



        self._run_safe_receiver_experiment(

            busy_message="正在执行安全 RGB 实验...",

            mac=mac,

            output_dir=self.rgb_experiment_output_dir,

            result_operation="gui-safe-rgb-experiment",

            write_operation="live-rgb",

            write_filename="live-rgb.json",

            analysis_filename="analyze-live-rgb.json",

            write_fields={"color": list(color), "effect_index": effect_index},

            result_fields={"color": list(color), "effect_index": effect_index},

            writer=lambda backend, target: backend.send_static_rgb(target, color),

            analysis_enricher=mark_visual_confirmation,

        )



    def run_safe_rainbow_experiment(self) -> None:

        if not self._write_unlocked():

            self.lianli_status_label.setText(self._write_blocked_text())

            return

        mac = self.lianli_mac_input.text().strip()

        if not mac:

            self.lianli_status_label.setText("请填写目标 MAC")

            return

        frame_count = int(self.lianli_rainbow_frame_count.value())

        interval_ms = int(self.lianli_rainbow_interval.value())



        def mark_visual_confirmation(analysis_payload: dict[str, object]) -> dict[str, object]:

            required = not bool(analysis_payload["snapshot_changed"])

            analysis_payload["visual_confirmation_required"] = required

            return {"visual_confirmation_required": required}



        def send_rainbow(

            backend: LianLiWirelessBackend,

            target: WirelessDeviceInfo,

        ) -> tuple[int, dict[str, object], dict[str, object]]:

            led_count = infer_led_count(target)

            packets_written = backend.send_rainbow_rgb(

                target,

                frame_count=frame_count,

                interval_ms=interval_ms,

            )

            dynamic_fields = {"led_count": led_count}

            return packets_written, dynamic_fields, dynamic_fields



        self._run_safe_receiver_experiment(

            busy_message="正在执行安全 Rainbow 实验...",

            mac=mac,

            output_dir=self.rainbow_experiment_output_dir,

            result_operation="gui-safe-rainbow-experiment",

            write_operation="live-rainbow",

            write_filename="live-rainbow.json",

            analysis_filename="analyze-live-rainbow.json",

            write_fields={

                "frame_count": frame_count,

                "interval_ms": interval_ms,

                "effect_index": 1,

            },

            result_fields={

                "frame_count": frame_count,

                "interval_ms": interval_ms,

                "effect_index": 1,

            },

            writer=send_rainbow,

            analysis_enricher=mark_visual_confirmation,

        )



    def run_safe_bind_experiment(self) -> None:

        if not self._write_unlocked():

            self.lianli_status_label.setText(self._write_blocked_text())

            return

        mac = self.lianli_mac_input.text().strip()

        if not mac:

            self.lianli_status_label.setText("请填写目标 MAC")

            return

        requested_master_mac = self.lianli_master_mac_input.text().strip()

        rx_type = self.lianli_rx_type_value.value()



        def bind_writer(

            backend: LianLiWirelessBackend,

            target: WirelessDeviceInfo,

        ) -> tuple[int, dict[str, object], dict[str, object]]:

            if target.is_bound:

                raise ValueError(f"接收器已绑定：{target.mac}")

            master_query_channel = target.channel or 8

            master_mac = requested_master_mac

            if not master_mac:

                master_query = backend.query_master_mac(channel=master_query_channel)

                if master_query is None:

                    raise ValueError("无法自动读取 Master MAC，请手动填写")

                master_mac = master_query[0]

            packets_written = backend.send_bind(

                target,

                master_mac=master_mac,

                rx_type=rx_type,

                channel=None,

            )

            fields = {

                "master_mac": master_mac,

                "rx_type": rx_type,

                "channel": None,

                "master_query_channel": master_query_channel,

            }

            return packets_written, fields, fields



        self._run_safe_receiver_experiment(

            busy_message="正在执行安全 Bind 实验...",

            mac=mac,

            output_dir=self.bind_experiment_output_dir,

            result_operation="gui-safe-bind-experiment",

            write_operation="live-bind",

            write_filename="live-bind.json",

            analysis_filename="analyze-live-bind.json",

            write_fields={},

            result_fields={},

            writer=bind_writer,

        )



    def run_safe_unbind_experiment(self) -> None:

        if not self._write_unlocked():

            self.lianli_status_label.setText(self._write_blocked_text())

            return

        mac = self.lianli_mac_input.text().strip()

        if not mac:

            self.lianli_status_label.setText("请填写目标 MAC")

            return



        def unbind_writer(

            backend: LianLiWirelessBackend,

            target: WirelessDeviceInfo,

        ) -> tuple[int, dict[str, object], dict[str, object]]:

            if not target.is_bound:

                raise ValueError(f"接收器已解绑：{target.mac}")

            packets_written = backend.send_unbind(target, channel=None)

            fields = {"channel": None}

            return packets_written, fields, fields



        self._run_safe_receiver_experiment(

            busy_message="正在执行安全 Unbind 实验...",

            mac=mac,

            output_dir=self.unbind_experiment_output_dir,

            result_operation="gui-safe-unbind-experiment",

            write_operation="live-unbind",

            write_filename="live-unbind.json",

            analysis_filename="analyze-live-unbind.json",

            write_fields={},

            result_fields={},

            writer=unbind_writer,

        )



    def send_live_lcd_control(self) -> None:

        if not self._write_unlocked():

            self.lianli_status_label.setText(self._write_blocked_text())

            return

        brightness = self.lianli_lcd_brightness.value()

        rotation = int(self.lianli_lcd_rotation.currentData())



        def operation() -> dict[str, object]:

            backend = None

            try:

                backend = self.lcd_backend_factory()

                return {

                    "operation": "live-lcd-control",

                    "applied": {

                        "brightness": {

                            "value": brightness,

                            "bytes_written": backend.set_brightness(brightness),

                        },

                        "rotation": {

                            "degrees": rotation,

                            "bytes_written": backend.set_rotation(rotation),

                        },

                    },

                }

            finally:

                self._close_lianli_lcd_backend(backend)



        self._run_lianli_operation("正在应用 LCD...", operation)



    def closeEvent(self, event) -> None:  # noqa: ANN001

        self._closed = True

        self._lighting_loop_stop.set()

        if hasattr(self, "_rpm_refresh_timer"):

            self._rpm_refresh_timer.stop()

        super().closeEvent(event)



    def _live_devices_payload(self) -> dict[str, object]:

        backend = None

        try:

            backend = self.backend_factory()

            snapshot = self._list_lianli_devices_with_backend(backend)

            self._remember_lianli_live_devices(snapshot.devices)

            bound_targets = [device for device in snapshot.devices if device.is_bound]

            if bound_targets:

                self._store_lianli_targets(bound_targets)
                self._mark_lianli_live_write_gate(bound_targets, source="live-list")

        finally:

            self._close_lianli_backend(backend)

        return {

            "operation": "live-list",

            "device_count": snapshot.device_count,

            "motherboard_pwm": snapshot.motherboard_pwm,

            "bound_target_count": len(bound_targets),

            "devices": [_wireless_device_payload(device) for device in snapshot.devices],

        }



    def _live_master_payload(self) -> dict[str, object]:

        backend = None

        try:

            backend = self.backend_factory()

            result = backend.query_master_mac(channel=8)

        finally:

            self._close_lianli_backend(backend)

        return {

            "operation": "live-master",

            "detected": result is not None,

            "master_mac": result[0] if result else None,

            "channel": result[1] if result else 8,

        }



    def _live_lcd_info_payload(self) -> dict[str, object]:

        backend = None

        try:

            backend = self.lcd_backend_factory()

            return {

                "operation": "live-lcd-info",

                "mode": "both",

                "handshake": backend.handshake(),

                "firmware": backend.firmware_version(),

            }

        finally:

            self._close_lianli_lcd_backend(backend)



    def _scan_usb_payload(self) -> dict[str, object]:

        devices = scan_known_usb_devices()

        return {

            "operation": "scan-usb",

            "device_count": len(devices),

            "devices": [

                {

                    "vid_pid": device.vid_pid,

                    "label": device.label,

                    "manufacturer": device.manufacturer,

                    "product": device.product,

                    "serial": device.serial,

                    "sysfs_path": device.sysfs_path,

                }

                for device in devices

            ],

        }



    def _readonly_validation_payload(self) -> dict[str, object]:

        output_dir = self.validation_output_dir

        output_dir.mkdir(parents=True, exist_ok=True)

        steps: list[dict[str, object]] = []

        operations: tuple[tuple[str, Callable[[], dict[str, object]]], ...] = (

            ("scan-usb", self._scan_usb_payload),

            ("live-list", self._live_devices_payload),

            ("live-master", self._live_master_payload),

            ("live-lcd-info", self._live_lcd_info_payload),

        )

        for name, operation in operations:

            path = output_dir / f"{name}.json"

            try:

                payload = operation()

                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                steps.append({"name": name, "status": "ok", "path": str(path)})

            except Exception as error:  # noqa: BLE001

                payload = {

                    "operation": name,

                    "status": "error",

                    "error": str(error),

                }

                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                steps.append({"name": name, "status": "error", "path": str(path), "error": str(error)})

        return {

            "operation": "gui-validate-readonly",

            "output_dir": str(output_dir),

            "step_count": len(steps),

            "ok_count": sum(1 for step in steps if step["status"] == "ok"),

            "error_count": sum(1 for step in steps if step["status"] == "error"),

            "steps": steps,

        }



    def _write_gate_payload(self) -> dict[str, object]:

        return self.write_gate_report_factory(

            self.write_gate_capture_dir,

            experiment_dir=self.write_gate_experiment_dir,

        )



    def _run_safe_receiver_experiment(

        self,

        *,

        busy_message: str,

        mac: str,

        output_dir: Path,

        result_operation: str,

        write_operation: str,

        write_filename: str,

        analysis_filename: str,

        write_fields: dict[str, object],

        result_fields: dict[str, object],

        writer: Callable[

            [LianLiWirelessBackend, WirelessDeviceInfo],

            int | tuple[int, dict[str, object], dict[str, object]],

        ],

        analysis_enricher: Callable[[dict[str, object]], dict[str, object]] | None = None,

    ) -> None:

        def operation() -> dict[str, object]:

            backend = None

            try:

                backend = self.backend_factory()

                output_dir.mkdir(parents=True, exist_ok=True)

                before = backend.list_devices()

                before_payload = {

                    "operation": "live-list-before",

                    "device_count": before.device_count,

                    "motherboard_pwm": before.motherboard_pwm,

                    "devices": [_wireless_device_payload(device) for device in before.devices],

                }

                before_path = output_dir / "live-list-before.json"

                before_path.write_text(json.dumps(before_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")



                target = next((device for device in before.devices if device.mac.lower() == mac.lower()), None)

                if target is None:

                    raise ValueError(f"未找到接收器 MAC：{mac}")

                write_result = writer(backend, target)

                dynamic_write_fields: dict[str, object] = {}

                dynamic_result_fields: dict[str, object] = {}

                if isinstance(write_result, tuple):

                    packets_written, dynamic_write_fields, dynamic_result_fields = write_result

                else:

                    packets_written = write_result

                after = backend.list_devices()

                after_payload = {

                    "operation": "live-list-after",

                    "device_count": after.device_count,

                    "motherboard_pwm": after.motherboard_pwm,

                    "devices": [_wireless_device_payload(device) for device in after.devices],

                }

                after_path = output_dir / "live-list-after.json"

                after_path.write_text(json.dumps(after_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")



                write_payload = {

                    "operation": write_operation,

                    "target": target.mac,

                    **write_fields,

                    **dynamic_write_fields,

                    "packets_written": packets_written,

                    "before": _wireless_device_payload(target),

                    "after": {

                        "device_count": after.device_count,

                        "motherboard_pwm": after.motherboard_pwm,

                        "devices": [_wireless_device_payload(device) for device in after.devices],

                    },

                }

                write_path = output_dir / write_filename

                write_path.write_text(json.dumps(write_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")



                analysis_payload = analyze_live_log(write_path)

                enriched_result_fields = {**result_fields, **dynamic_result_fields}

                if analysis_enricher is not None:

                    enriched_result_fields.update(analysis_enricher(analysis_payload))

                analysis_path = output_dir / analysis_filename

                analysis_path.write_text(json.dumps(analysis_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                summary_payload = summarize_experiment_dir(output_dir)

                summary_path = output_dir / "summary.json"

                summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                return {

                    "operation": result_operation,

                    "target": target.mac,

                    **enriched_result_fields,

                    "output_dir": str(output_dir),

                    "packets_written": packets_written,

                    "likely_effective": analysis_payload["likely_effective"],

                    "steps": [

                        {"name": "before", "path": str(before_path)},

                        {"name": "write", "path": str(write_path)},

                        {"name": "after", "path": str(after_path)},

                        {"name": "analysis", "path": str(analysis_path)},

                        {"name": "summary", "path": str(summary_path)},

                    ],

                    "analysis": analysis_payload,

                    "summary": summary_payload,

                }

            finally:

                self._close_lianli_backend(backend)



        self._run_lianli_operation(busy_message, operation)



    def _run_guarded_write(

        self,

        busy_message: str,

        writer: Callable[[LianLiWirelessBackend, WirelessDeviceInfo], int],

    ) -> None:

        if not self._write_unlocked():

            self.lianli_status_label.setText(self._write_blocked_text())

            return

        mac = self.lianli_mac_input.text().strip()

        if not mac:

            self.lianli_status_label.setText("请填写目标 MAC")

            return



        def operation() -> dict[str, object]:

            backend = None

            try:

                backend = self.backend_factory()

                before = backend.list_devices()

                target = next((device for device in before.devices if device.mac.lower() == mac.lower()), None)

                if target is None:

                    raise ValueError(f"未找到接收器 MAC：{mac}")

                packets_written = writer(backend, target)

                after = backend.list_devices()

                return {

                    "operation": "live-write",

                    "target": target.mac,

                    "packets_written": packets_written,

                    "before": _wireless_device_payload(target),

                    "after": {

                        "device_count": after.device_count,

                        "motherboard_pwm": after.motherboard_pwm,

                        "devices": [_wireless_device_payload(device) for device in after.devices],

                    },

                }

            finally:

                self._close_lianli_backend(backend)



        self._run_lianli_operation(busy_message, operation)



    def _safe_snapshot_motherboard_pwm(self, value: int | None) -> int:

        if value is None:

            raise ValueError("接收器快照没有有效的主板 PWM 值")

        minimum = self.lianli_pwm_value.minimum()

        if value < minimum:

            raise ValueError(f"主板 PWM {value} 低于安全下限 {minimum}")

        return max(0, min(255, int(value)))



    def _run_lianli_operation(self, busy_message: str, operation: Callable[[], dict[str, object]]) -> None:

        if self._operation_active:

            self._set_lianli_status("联力操作仍在进行")

            return

        self._set_lianli_busy(True, busy_message)



        def worker() -> None:

            try:

                with self._lianli_hardware_lock:

                    result = operation()

                if not self._closed:

                    self.operation_finished.emit("完成", result)

            except Exception as error:  # noqa: BLE001

                if not self._closed:

                    self.operation_finished.emit("失败", error)



        thread = threading.Thread(target=worker, daemon=True)

        self._threads.append(thread)

        thread.start()



    def _operation_finished(self, message: str, result: object) -> None:

        self._set_lianli_busy(False, "")

        if isinstance(result, Exception):

            self._lianli_loop_effect = None

            if self._lianli_curve_pending_pwm is not None:

                self._reset_lianli_curve_write_cache()

            self._set_lianli_status(f"{message}：{result}")

            return

        if isinstance(result, dict) and result.get("operation") == "lianli-auto-connect":

            self._populate_cached_lianli_targets()

            message = str(result.get("message") or message)

        if isinstance(result, dict) and result.get("operation") == "live-list":

            self._populate_cached_lianli_targets()

            bound_count = result.get("bound_target_count")

            if isinstance(bound_count, int) and bound_count > 0:

                message = f"接收器读取完成：已绑定 {bound_count} 个联力风扇组"

        if isinstance(result, dict) and result.get("operation") == "linux-control-write-gate":

            self.apply_lianli_write_gate(result)

            message = self._write_gate_status_text(result)

        if isinstance(result, dict) and result.get("operation") == "summarize-experiments":

            message = self._apply_receiver_next_action(result)

        if isinstance(result, dict) and result.get("operation") in {
            "lianli-fan-pwm",
            "lianli-fan-pwm-all",
            "lianli-fan-pwm-sync",
        }:

            self._selected_lianli_target_changed()

            message = self._lianli_pwm_result_message(result, message)

            self._lianli_curve_pending_pwm = None

        if isinstance(result, dict) and result.get("operation") == "gui-lianli-lighting":

            if result.get("loop_stopped"):

                self._lianli_loop_effect = None

        self.lianli_status_label.setText(message)

        self.lianli_snapshot_text.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))

        self.status_changed.emit(self.home_status_text())

        if self._pending_lianli_effect is not None and not self._operation_active:

            pending_effect = self._pending_lianli_effect

            self._pending_lianli_effect = None

            QTimer.singleShot(
                0,
                lambda effect=pending_effect: self._run_lianli_operation(
                    f"正在应用联力灯效：{effect}...",
                    lambda: self._send_lianli_lighting_effect(effect),
                ),
            )



    def _lianli_pwm_result_message(self, payload: dict[str, object], fallback: str) -> str:

        target_mac = str(payload.get("target") or self.settings.lianli_wireless.active_target_mac or "")

        after = payload.get("after")

        if not isinstance(after, dict):

            return fallback

        devices = after.get("devices")

        if not isinstance(devices, list):

            return fallback

        selected = None

        for device in devices:

            if not isinstance(device, dict):

                continue

            mac = str(device.get("mac") or "")

            if not target_mac or mac.lower() == target_mac.lower():

                selected = device

                break

        if selected is None:

            if target_mac:

                return f"联力风扇写入完成：目标 {target_mac} 未在回读中出现"

            return fallback

        pwm_values = selected.get("pwm_values")

        fan_rpm = selected.get("fan_rpm")

        pwm_text = "/".join(str(int(value)) for value in pwm_values if isinstance(value, int)) if isinstance(pwm_values, list) else "--"

        rpm_text = "/".join(str(int(value)) for value in fan_rpm if isinstance(value, int) and value > 0) if isinstance(fan_rpm, list) else "--"

        packets = payload.get("packets_written")

        return f"联力风扇写入完成：包 {packets}，PWM 回读 {pwm_text}，RPM {rpm_text or '--'}"



    def _apply_receiver_next_action(self, payload: dict[str, object]) -> str:

        action = payload.get("receiver_control_next_action")

        if not isinstance(action, dict):

            text = "下一步：汇总完成，但没有接收器控制建议"

            self.lianli_next_action_label.setText(text)

            return "实验汇总完成"

        text = self._receiver_next_action_text(action)

        self.lianli_next_action_label.setText(text)

        ready_candidates = [

            candidate

            for candidate in action.get("candidates", [])

            if isinstance(candidate, dict) and candidate.get("status") == "ready"

        ] if isinstance(action.get("candidates"), list) else []

        action_ready = (

            str(action.get("status") or "") == "ready-for-single-target-safe-pwm"

            and bool(action.get("can_run_safe_pwm"))

        )

        if action_ready and len(ready_candidates) == 1 and not self.lianli_mac_input.text().strip():

            mac = str(ready_candidates[0].get("mac") or "").strip()

            if mac:

                self.lianli_mac_input.setText(mac)

        return text



    def _receiver_next_action_text(self, action: dict[str, object]) -> str:

        status = str(action.get("status") or "unknown")

        candidates = action.get("candidates")

        ready_macs = [

            str(candidate.get("mac") or "")

            for candidate in candidates

            if isinstance(candidate, dict) and candidate.get("status") == "ready" and candidate.get("mac")

        ] if isinstance(candidates, list) else []

        messages = {

            "ready-for-single-target-safe-pwm": "下一步：写入门禁已通过，可对一个已绑定 MAC 运行安全 PWM",

            "ready-for-safe-lighting-validation": "下一步：PWM 已确认，可一次运行一个安全灯光实验",

            "ready-for-pairing-risk-review": "下一步：PWM 和灯光已确认，配对/解绑只进入风险复盘",

            "write-validation-needs-observation": "下一步：已有写入机器日志，先补观察记录",

            "write-validation-conflict": "下一步：写入证据存在冲突，先复盘 receiver-evidence-report",

            "write-validation-incomplete": "下一步：写入证据不完整，先补 before/write/after/analysis",

            "write-validation-already-observed": "下一步：已经有安全写入实验记录，先复盘结果再继续扩展",

            "validation-errors": "下一步：验证日志有错误，先查 validation_errors",

            "needs-bound-target": "下一步：门禁已过，但 live-list 没有可写的已绑定 MAC",

            "needs-receiver-validation-bundle": "下一步：先运行 receiver-validation-bundle",

            "needs-live-list": "下一步：先刷新 live-list 接收器快照",

            "needs-write-gate": "下一步：写入门禁未过，继续补抓包/packet compare",

            "receiver-identity-conflict": "下一步：接收器身份日志互相矛盾，先重新采集 receiver-validation-bundle",

            "needs-receiver-identity-validation": "下一步：接收器身份日志不完整，先重新采集 receiver-validation-bundle",

        }

        text = messages.get(status, f"下一步：{status}")

        if ready_macs:

            text += "：" + ", ".join(ready_macs[:3])

        return text



    def _set_lianli_busy(self, active: bool, message: str) -> None:

        self._operation_active = active

        for name in (

            "scan_lianli_button",

            "live_lianli_button",

            "master_lianli_button",

            "lcd_info_button",

            "validate_lianli_button",

            "save_lianli_snapshot_button",

            "analyze_lianli_log_button",

            "diff_lianli_snapshots_button",

            "summarize_lianli_experiments_button",

            "lianli_write_gate_button",

        ):

            if hasattr(self, name):

                getattr(self, name).setEnabled(not active)

        self._update_write_controls()

        self._update_daily_controls()

        if message:

            self._set_lianli_status(message)



    def home_status_text(self) -> str:

        text = self.lianli_status_label.text().strip() or "未连接"

        snapshot = self.lianli_snapshot_text.toPlainText().strip()

        if snapshot:

            try:

                payload = json.loads(snapshot)

            except json.JSONDecodeError:

                payload = {}

            if isinstance(payload, dict):

                count = payload.get("device_count")

                operation = payload.get("operation")

                if isinstance(count, int):

                    if operation == "scan-usb":

                        return "未连接" if count == 0 else f"USB {count} 设备"

                    if operation == "live-list":

                        return f"接收器 {count} 个"

        if text.startswith("USB 扫描完成"):

            return text.removeprefix("USB 扫描完成：")

        if text == "默认只读":

            return "未连接"

        return text



    def _set_lianli_status(self, text: str) -> None:

        self.lianli_status_label.setText(text)

        self.status_changed.emit(self.home_status_text())



    def apply_lianli_write_gate(self, payload: dict[str, object]) -> None:

        self._lianli_write_gate_payload = dict(payload)
        if payload.get("allows_any_guarded_write"):
            self._lianli_live_write_gate_payload = None

        self._update_write_controls()


    def _set_lianli_write_enabled(self, enabled: bool) -> None:

        self.settings.lianli_wireless.write_enabled = bool(enabled)

        self._update_write_controls()



    def _set_lianli_auto_curve_enabled(self, enabled: bool) -> None:

        self.settings.lianli_wireless.auto_curve_enabled = bool(enabled)

        save_settings(self.settings)

        self._reset_lianli_curve_write_cache()

        self._update_daily_controls()

        if enabled and self._lianli_latest_telemetry is not None:

            self._apply_lianli_active_curve(self._lianli_latest_telemetry)



    def _auto_curve_unlocked(self) -> bool:

        if not hasattr(self, "lianli_auto_curve_enable"):

            return bool(getattr(self.settings.lianli_wireless, "auto_curve_enabled", False))

        return self.lianli_auto_curve_enable.isChecked()



    def _write_unlocked(self) -> bool:

        if not hasattr(self, "lianli_write_enable"):

            return True

        return (

            self.lianli_write_enable.isChecked()

            and self._write_gate_unlocked()

        )



    def _write_gate_unlocked(self) -> bool:

        if not self.require_write_gate:

            return True

        return bool(

            self._lianli_write_gate_payload

            and self._lianli_write_gate_payload.get("allows_any_guarded_write")

        ) or bool(

            self._lianli_live_write_gate_payload

            and self._lianli_live_write_gate_payload.get("allows_any_guarded_write")

        )



    def _write_blocked_text(self) -> str:

        if not hasattr(self, "lianli_write_enable"):

            return "还没有识别到可用的联力无线风扇组"

        if not self.lianli_write_enable.isChecked():

            return "写入未启用"

        if self.require_write_gate and not self._write_gate_unlocked():

            return "写入门禁未通过：请先点击写入门禁，完成官方抓包对比后再写入"

        return "写入未启用"



    def _update_write_controls(self) -> None:

        has_write_target = self._has_lianli_write_target()

        write_enabled = (not self._operation_active) and self._write_unlocked()

        targeted_write_enabled = write_enabled and has_write_target

        for name in (

            "lianli_pwm_button",

            "lianli_pwm_sync_button",

            "lianli_safe_pwm_button",

            "lianli_safe_sync_button",

            "lianli_pwm_mirror_button",

            "lianli_safe_mirror_button",

            "lianli_rgb_off_button",

            "lianli_safe_rgb_button",

            "lianli_rainbow_button",

            "lianli_safe_rainbow_button",

            "lianli_safe_bind_button",

            "lianli_safe_unbind_button",

        ):

            if hasattr(self, name):

                getattr(self, name).setEnabled(targeted_write_enabled)

        if hasattr(self, "lianli_lcd_control_button"):

            self.lianli_lcd_control_button.setEnabled(write_enabled)

        if hasattr(self, "lianli_lcd_info_button"):

            self.lianli_lcd_info_button.setEnabled(not self._operation_active)

        for name in (

            "lianli_apply_effect_button",

            "lianli_start_loop_button",

            "lianli_preview_packet_button",

        ):

            if hasattr(self, name):

                getattr(self, name).setEnabled(
                    targeted_write_enabled if name != "lianli_preview_packet_button" else not self._operation_active
                )

        if hasattr(self, "lianli_stop_loop_button"):

            self.lianli_stop_loop_button.setEnabled(self._operation_active)

        if hasattr(self, "lianli_write_gate_label"):

            self.lianli_write_gate_label.setText(self._write_gate_label_text())

        if hasattr(self, "lianli_write_target_label"):

            self.lianli_write_target_label.setText(self._lianli_write_target_text())

        self._update_daily_controls()


    def _lianli_write_target_text(self) -> str:

        target = self._cached_lianli_target()

        if target is None:

            return "写入目标：未识别到风扇组。点击上方“重新识别”后会自动填充 MAC、Master 和 RX Type。"

        return (

            f"写入目标：{target.label or '风扇组'} | MAC {target.mac} | Master {target.master_mac or '未读取'} | "

            f"Channel {target.channel} | RX Type {target.rx_type} | {target.fan_count} 把风扇 | {target.led_count} LED"

        )



    def _has_lianli_write_target(self) -> bool:

        if self._cached_lianli_target() is not None:

            return True

        if hasattr(self, "lianli_mac_input"):

            return bool(self.lianli_mac_input.text().strip())

        return False



    def _write_gate_label_text(self) -> str:

        if not self.require_write_gate:

            return "写入门禁：当前实例未强制；真实 GUI 会要求门禁通过后再写入。"

        payload = self._lianli_write_gate_payload or self._lianli_live_write_gate_payload

        if not payload:

            return (

                "写入门禁：未检查；需要先完成官方抓包、packet preview/compare，"

                "再点击写入门禁；未通过前写入按钮保持锁定。"

            )

        return self._write_gate_status_text(payload)



    def _write_gate_status_text(self, payload: dict[str, object]) -> str:

        status = str(payload.get("status") or "unknown")

        ready = int(payload.get("ready_action_count") or 0)

        blocked = int(payload.get("blocked_action_count") or 0)

        next_command = str(payload.get("next_command") or "")

        status_text = {

            "write-enabled": f"写入门禁通过 [{status}]，{ready} 个安全实验已允许写入",
            "live-receiver-write-enabled": f"写入门禁通过 [{status}]：已由 live-receiver 验证 {ready} 个已绑定目标",

            "needs-packet-compare": f"写入门禁未通过 [{status}]：需要先运行 packet preview/compare",

            "refresh-live-snapshot": f"写入门禁未通过 [{status}]：需要刷新 live-list 快照后重新对比",

            "needs-recompare-after-refresh": f"写入门禁未通过 [{status}]：需要用刷新后的快照重新 packet compare",

            "incomplete-packet-compare": f"写入门禁未通过 [{status}]：仍有抓包对比未通过",

            "packet-compare-failed": f"写入门禁未通过 [{status}]：packet compare 失败",

            "invalid-packet-compare-schema": f"写入门禁未通过 [{status}]：对比产物 schema 已过期",

            "blocked-by-preflight": f"写入门禁阻塞 [{status}]：硬件、权限或抓包证据不足",

            "needs-capture-evidence": f"写入门禁未通过 [{status}]：需要先补官方 L-Connect 抓包证据",

        }.get(status, f"写入门禁状态[{status}]：ready {ready} / blocked {blocked}")

        if blocked and status == "write-enabled":

            status_text += f"，另有 {blocked} 个动作仍阻塞"

        if next_command and status != "write-enabled":

            status_text += f"；下一步：{next_command}"

        return status_text
