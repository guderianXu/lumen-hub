from __future__ import annotations

from collections import deque
import grp
import math
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
from types import ModuleType

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QApplication,
    QPushButton,
    QProgressBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


DEFAULT_FAN_CONTROL_PATH = Path("/home/xjw/code/风扇控制")
FAN_HWMON_MODULE_CANDIDATES = (
    "nct6775",
    "nct6683",
    "it87",
    "asus_wmi_sensors",
    "asus-ec-sensors",
    "w83627ehf",
)
CHART_COLORS = (
    "#6fb6a0",
    "#7aa2d6",
    "#d2a04c",
    "#d9847a",
    "#b89ad7",
    "#82b9b2",
)


def _is_temperature_chart_sensor(name: str, unit: str) -> bool:
    if unit != "°C":
        return False
    lowered = name.lower()
    return (
        name == "CPU Tctl"
        or name.startswith("CPU ")
        or "gpu" in lowered
        or name.startswith("集显")
        or "主板" in name
        or "chipset" in lowered
    )


def _temperature_sensor_priority(name: str) -> tuple[int, str]:
    lowered = name.lower()
    if name == "CPU Tctl":
        return (0, name)
    if name.startswith("CPU "):
        return (1, name)
    if "gpu" in lowered or name.startswith("集显"):
        return (2, name)
    if "主板" in name or "chipset" in lowered:
        return (3, name)
    return (9, name)


class FanTrendChart(QFrame):
    def __init__(self, title: str, unit: str, *, default_max: float) -> None:
        super().__init__()
        self.setObjectName("FanTrendChart")
        self.setMinimumHeight(228)
        self._title = title
        self._unit = unit
        self._default_max = default_max
        self._series: dict[str, list[float]] = {}

    def set_series(self, series: dict[str, list[float]]) -> None:
        self._series = {name: list(values)[-90:] for name, values in series.items() if values}
        self.update()

    def clear(self) -> None:
        self._series = {}
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001, D401
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(14, 12, -14, -12)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        painter.setPen(QColor("#eef1ee"))
        title_font = painter.font()
        title_font.setBold(True)
        title_font.setPointSize(max(10, title_font.pointSize()))
        painter.setFont(title_font)
        painter.drawText(rect.left(), rect.top(), rect.width(), 22, Qt.AlignmentFlag.AlignLeft, self._title)

        values = [value for items in self._series.values() for value in items if math.isfinite(value)]
        top_value = next((items[-1] for items in self._series.values() if items), None)
        if top_value is not None:
            painter.setPen(QColor("#969b9e"))
            badge = f"当前 {top_value:.0f} {self._unit}"
            painter.drawText(rect.left(), rect.top(), rect.width(), 22, Qt.AlignmentFlag.AlignRight, badge)

        legend_height = 26 if values else 0
        plot_rect = QRectF(rect.left() + 42, rect.top() + 42 + legend_height, rect.width() - 92, rect.height() - 72 - legend_height)
        if plot_rect.width() <= 4 or plot_rect.height() <= 4:
            return

        self._draw_plot_background(painter, plot_rect)

        if not values:
            self._draw_empty_state(painter, plot_rect)
            return

        min_value, max_value = self._value_range(values)
        if max_value <= min_value:
            max_value = min_value + 1

        self._draw_axes(painter, plot_rect, min_value, max_value)
        self._draw_legend(painter, rect, plot_rect)
        self._draw_series(painter, plot_rect, min_value, max_value)

    def _draw_plot_background(self, painter: QPainter, plot_rect: QRectF) -> None:
        gradient = QLinearGradient(plot_rect.topLeft(), plot_rect.bottomLeft())
        gradient.setColorAt(0.0, QColor("#1d2021"))
        gradient.setColorAt(1.0, QColor("#121314"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(plot_rect, 6, 6)

    def _draw_empty_state(self, painter: QPainter, plot_rect: QRectF) -> None:
        painter.setPen(QPen(QColor("#303438"), 1))
        for index in range(4):
            y = plot_rect.top() + plot_rect.height() * index / 3
            painter.drawLine(plot_rect.left(), y, plot_rect.right(), y)
        painter.setPen(QColor("#8f9698"))
        painter.drawText(plot_rect, Qt.AlignmentFlag.AlignCenter, "等待实时数据")

    def _value_range(self, values: list[float]) -> tuple[float, float]:
        if self._unit == "°C":
            low = min(values)
            high = max(values)
            min_value = max(0.0, math.floor((low - 6) / 5) * 5)
            max_value = max(80.0, math.ceil((high + 6) / 5) * 5)
            return min_value, max_value
        high = max(self._default_max, max(values) * 1.18)
        return 0.0, self._nice_ceiling(high)

    def _nice_ceiling(self, value: float) -> float:
        if value <= 0:
            return self._default_max
        magnitude = 10 ** math.floor(math.log10(value))
        normalized = value / magnitude
        if normalized <= 1:
            nice = 1
        elif normalized <= 2:
            nice = 2
        elif normalized <= 5:
            nice = 5
        else:
            nice = 10
        return nice * magnitude

    def _draw_axes(self, painter: QPainter, plot_rect: QRectF, min_value: float, max_value: float) -> None:
        label_font = painter.font()
        label_font.setBold(False)
        label_font.setPointSize(max(8, label_font.pointSize() - 1))
        painter.setFont(label_font)

        grid_pen = QPen(QColor("#303438"), 1)
        grid_pen.setStyle(Qt.PenStyle.DashLine)
        axis_pen = QPen(QColor("#484d50"), 1)
        painter.setPen(grid_pen)
        ticks = 4
        for index in range(ticks + 1):
            ratio = index / ticks
            y = plot_rect.top() + plot_rect.height() * ratio
            value = max_value - (max_value - min_value) * ratio
            painter.drawLine(plot_rect.left(), y, plot_rect.right(), y)
            painter.setPen(QColor("#8f9698"))
            painter.drawText(
                QRectF(plot_rect.left() - 40, y - 8, 34, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{value:.0f}",
            )
            painter.setPen(grid_pen)

        for index in range(4):
            x = plot_rect.left() + plot_rect.width() * index / 3
            painter.drawLine(x, plot_rect.top(), x, plot_rect.bottom())
        painter.setPen(axis_pen)
        painter.drawLine(plot_rect.left(), plot_rect.bottom(), plot_rect.right(), plot_rect.bottom())
        painter.setPen(QColor("#8f9698"))
        painter.drawText(
            QRectF(plot_rect.right() - 52, plot_rect.top() - 22, 52, 16),
            Qt.AlignmentFlag.AlignRight,
            self._unit,
        )

    def _draw_legend(self, painter: QPainter, rect, plot_rect: QRectF) -> None:  # noqa: ANN001
        painter.setPen(Qt.PenStyle.NoPen)
        legend_y = rect.top() + 38
        legend_x = plot_rect.left()
        metrics = QFontMetrics(painter.font())
        for index, (name, items) in enumerate(list(self._series.items())[:4]):
            if not items:
                continue
            color = QColor(CHART_COLORS[index % len(CHART_COLORS)])
            text = metrics.elidedText(f"{name} {items[-1]:.0f}", Qt.TextElideMode.ElideRight, 138)
            chip_width = min(154, max(82, metrics.horizontalAdvance(text) + 26))
            if legend_x + chip_width > plot_rect.right():
                break
            painter.setBrush(QColor("#202224"))
            painter.setPen(QColor("#3a3f42"))
            painter.drawRoundedRect(QRectF(legend_x, legend_y - 2, chip_width, 20), 5, 5)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QRectF(legend_x + 8, legend_y + 4, 8, 8))
            painter.setPen(QColor("#d4d9d6"))
            painter.drawText(
                QRectF(legend_x + 21, legend_y, chip_width - 26, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
            legend_x += chip_width + 8

    def _draw_series(self, painter: QPainter, plot_rect: QRectF, min_value: float, max_value: float) -> None:
        endpoint_labels: list[tuple[QColor, QPointF, str]] = []
        for index, (name, items) in enumerate(list(self._series.items())[:5]):
            color = QColor(CHART_COLORS[index % len(CHART_COLORS)])
            points = [value for value in items if math.isfinite(value)]
            if not points:
                continue
            screen_points = [
                QPointF(
                    plot_rect.left() + plot_rect.width() * point_index / max(1, len(points) - 1),
                    self._value_to_y(value, min_value, max_value, plot_rect),
                )
                for point_index, value in enumerate(points)
            ]
            if len(points) == 1:
                x = screen_points[0].x()
                y = screen_points[0].y()
                painter.setBrush(color)
                painter.drawEllipse(QRectF(x - 3, y - 3, 6, 6))
                endpoint_labels.append((color, screen_points[-1], f"{points[-1]:.0f}"))
                continue

            path = self._smooth_path(screen_points)
            area = QPainterPath(path)
            area.lineTo(screen_points[-1].x(), plot_rect.bottom())
            area.lineTo(screen_points[0].x(), plot_rect.bottom())
            area.closeSubpath()
            fill = QColor(color)
            fill.setAlpha(34 if index == 0 else 22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawPath(area)

            shadow = QColor(color)
            shadow.setAlpha(70)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(shadow, 6.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
            painter.setPen(QPen(color, 2.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)

            endpoint = screen_points[-1]
            painter.setPen(QPen(QColor("#121314"), 3))
            painter.setBrush(color)
            painter.drawEllipse(QRectF(endpoint.x() - 4, endpoint.y() - 4, 8, 8))
            endpoint_labels.append((color, endpoint, f"{points[-1]:.0f}"))

        self._draw_endpoint_labels(painter, plot_rect, endpoint_labels)

    def _smooth_path(self, points: list[QPointF]) -> QPainterPath:
        path = QPainterPath(points[0])
        if len(points) < 3:
            for point in points[1:]:
                path.lineTo(point)
            return path
        for index in range(len(points) - 1):
            p0 = points[max(index - 1, 0)]
            p1 = points[index]
            p2 = points[index + 1]
            p3 = points[min(index + 2, len(points) - 1)]
            c1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6, p1.y() + (p2.y() - p0.y()) / 6)
            c2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6, p2.y() - (p3.y() - p1.y()) / 6)
            path.cubicTo(c1, c2, p2)
        return path

    def _draw_endpoint_labels(
        self,
        painter: QPainter,
        plot_rect: QRectF,
        labels: list[tuple[QColor, QPointF, str]],
    ) -> None:
        used_y: list[float] = []
        for color, point, text in labels[:4]:
            y = point.y()
            for existing in used_y:
                if abs(y - existing) < 18:
                    y = existing + 18
            y = max(plot_rect.top() + 8, min(plot_rect.bottom() - 8, y))
            used_y.append(y)
            label_rect = QRectF(plot_rect.right() + 8, y - 10, 38, 20)
            fill = QColor("#202224")
            fill.setAlpha(235)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(label_rect, 5, 5)
            painter.setPen(color)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, text)

    def _value_to_y(self, value: float, min_value: float, max_value: float, plot_rect: QRectF) -> float:
        ratio = (value - min_value) / (max_value - min_value)
        ratio = max(0.0, min(1.0, ratio))
        return plot_rect.bottom() - plot_rect.height() * ratio


class FanStatusCard(QFrame):
    def __init__(self, fan_name: str) -> None:
        super().__init__()
        self.setObjectName("FanStatusCard")
        self.setMinimumHeight(104)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)

        top = QHBoxLayout()
        self.name_label = QLabel(fan_name)
        self.name_label.setObjectName("FanCardName")
        self.source_label = QLabel("--")
        self.source_label.setObjectName("FanCardMeta")
        top.addWidget(self.name_label, 1)
        top.addWidget(self.source_label)
        layout.addLayout(top)

        metric_row = QHBoxLayout()
        self.rpm_value = QLabel("-- RPM")
        self.rpm_value.setObjectName("FanRpmValue")
        self.pwm_value = QLabel("PWM --")
        self.pwm_value.setObjectName("FanCardMeta")
        metric_row.addWidget(self.rpm_value, 1)
        metric_row.addWidget(self.pwm_value)
        layout.addLayout(metric_row)

        self.rpm_bar = QProgressBar()
        self.rpm_bar.setTextVisible(False)
        self.rpm_bar.setRange(0, 1800)
        self.rpm_bar.setFixedHeight(7)
        layout.addWidget(self.rpm_bar)

        self.status_label = QLabel("等待转速")
        self.status_label.setObjectName("FanCardMeta")
        layout.addWidget(self.status_label)

    def update_status(self, *, name: str, rpm: int | None, pwm: str, source: str, status: str) -> None:
        self.name_label.setText(name)
        self.source_label.setText(source)
        if rpm is None:
            self.rpm_value.setText("-- RPM")
            self.rpm_bar.setValue(0)
        else:
            self.rpm_value.setText(f"{rpm} RPM")
            maximum = max(1800, int(rpm * 1.25))
            self.rpm_bar.setMaximum(maximum)
            self.rpm_bar.setValue(max(0, min(rpm, maximum)))
        self.pwm_value.setText(f"PWM {pwm}")
        self.status_label.setText(status)
        color = "#b7dfd2" if rpm and rpm > 0 else "#d9bc79"
        if "无转速" in status:
            color = "#e0a2a7"
        self.status_label.setStyleSheet(f"color: {color};")


class StressTestPanel(QWidget):
    _LABELS = {
        "cpu": ("CPU 压力", "stress-ng CPU"),
        "fpu": ("FPU 压力", "stress-ng FFT"),
        "gpu": ("GPU 压力", "gpu-burn"),
    }

    def __init__(self) -> None:
        super().__init__()
        self.burner = None
        self._tool_available: dict[str, bool] = {key: False for key in self._LABELS}
        self._buttons: dict[str, QPushButton] = {}
        self._state_labels: dict[str, QLabel] = {}
        self._tool_labels: dict[str, QLabel] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("压力测试")
        title.setObjectName("SectionLabel")
        self.status_label = QLabel("加载风扇监控后可启动压力测试")
        self.status_label.setObjectName("FieldHint")
        self.stop_all_button = QPushButton("停止全部")
        self.stop_all_button.setObjectName("DangerButton")
        self.stop_all_button.setEnabled(False)
        self.stop_all_button.clicked.connect(self.stop_all)
        header.addWidget(title)
        header.addWidget(self.status_label, 1)
        header.addWidget(self.stop_all_button)
        layout.addLayout(header)

        cards = QGridLayout()
        cards.setHorizontalSpacing(12)
        cards.setVerticalSpacing(12)
        for column, (kind, (title_text, tool_text)) in enumerate(self._LABELS.items()):
            card = QFrame()
            card.setObjectName("MetricCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(8)

            name = QLabel(title_text)
            name.setObjectName("SectionLabel")
            tool = QLabel(tool_text)
            tool.setObjectName("FieldHint")
            state = QLabel("未运行")
            state.setObjectName("HomeMetricValue")
            button = QPushButton("启动")
            button.setCheckable(True)
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, current=kind: self.toggle(current))

            card_layout.addWidget(name)
            card_layout.addWidget(tool)
            card_layout.addWidget(state)
            card_layout.addStretch(1)
            card_layout.addWidget(button)
            cards.addWidget(card, 0, column)
            self._buttons[kind] = button
            self._state_labels[kind] = state
            self._tool_labels[kind] = tool
        layout.addLayout(cards)
        layout.addStretch(1)

    def set_burner(self, burner) -> None:  # noqa: ANN001
        self.burner = burner
        if hasattr(burner, "state_changed"):
            burner.state_changed.connect(self._burner_state_changed)
        if hasattr(burner, "tool_missing"):
            burner.tool_missing.connect(self._tool_missing)
        self.refresh_tools()
        self.refresh_state()

    def clear_burner(self) -> None:
        self.burner = None
        self._tool_available = {key: False for key in self._LABELS}
        self.status_label.setText("加载风扇监控后可启动压力测试")
        self.stop_all_button.setEnabled(False)
        for kind in self._LABELS:
            self._set_kind_state(kind, False)
            self._buttons[kind].setEnabled(False)
            self._tool_labels[kind].setText(self._LABELS[kind][1])

    def refresh_tools(self) -> None:
        if self.burner is None or not hasattr(self.burner, "check_tools"):
            return
        self._tool_available = dict(self.burner.check_tools())
        for kind, available in self._tool_available.items():
            self._buttons[kind].setEnabled(available)
            tool_name = self._LABELS[kind][1]
            self._tool_labels[kind].setText(f"{tool_name} · {'可用' if available else '未安装'}")
        missing = [self._LABELS[kind][1] for kind, ok in self._tool_available.items() if not ok]
        self.status_label.setText("缺少工具：" + "、".join(missing) if missing else "压力测试工具可用")

    def refresh_state(self) -> None:
        running = getattr(self.burner, "running", {}) if self.burner is not None else {}
        for kind in self._LABELS:
            self._set_kind_state(kind, bool(running.get(kind)))
        self.stop_all_button.setEnabled(any(bool(value) for value in running.values()))

    def toggle(self, kind: str) -> None:
        if self.burner is None:
            self.status_label.setText("请先加载风扇监控")
            return
        running = bool(getattr(self.burner, "running", {}).get(kind))
        if running:
            self.burner.stop(kind)
        else:
            self.burner.start(kind)
        self.refresh_state()

    def stop_all(self) -> None:
        if self.burner is None:
            return
        self.burner.stop_all()
        self.refresh_state()

    def _burner_state_changed(self, kind: str, running: bool) -> None:
        self._set_kind_state(kind, running)
        self.refresh_state()

    def _tool_missing(self, tool: str) -> None:
        self.status_label.setText(f"缺少压力测试工具：{tool}")
        self.refresh_tools()

    def _set_kind_state(self, kind: str, running: bool) -> None:
        if kind not in self._buttons:
            return
        self._buttons[kind].blockSignals(True)
        self._buttons[kind].setChecked(running)
        self._buttons[kind].setText("停止" if running else "启动")
        self._buttons[kind].blockSignals(False)
        self._state_labels[kind].setText("运行中" if running else "未运行")
        self._state_labels[kind].setStyleSheet("color: #d9847a;" if running else "color: #f2f3f0;")


class EmbeddedProfileEditor(QWidget):
    profile_activated = Signal()

    def __init__(self, profile_manager, curve_editor_cls, fan_curve_cls, profile_cls, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._pm = profile_manager
        self._fan_curve_cls = fan_curve_cls
        self._profile_cls = profile_cls
        self._fan_rpm_data: dict[str, int] = {}
        self._fan_pwm_data: dict[str, int] = {}
        self._cpu_power = ""
        self._gpu_power = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        top_card = QFrame()
        top_card.setObjectName("MetricCard")
        top_layout = QGridLayout(top_card)
        top_layout.setContentsMargins(14, 12, 14, 12)
        top_layout.setHorizontalSpacing(10)
        top_layout.setVerticalSpacing(8)
        title = QLabel("配置文件")
        title.setObjectName("SectionLabel")
        top_layout.addWidget(title, 0, 0)
        self._profile_state_label = QLabel("--")
        self._profile_state_label.setObjectName("FieldHint")
        top_layout.addWidget(self._profile_state_label, 0, 1, 1, 4, Qt.AlignmentFlag.AlignRight)
        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(260)
        self._profile_combo.currentTextChanged.connect(self._on_select)
        top_layout.addWidget(self._profile_combo, 1, 0, 1, 2)
        self._btn_new = QPushButton("新建")
        self._btn_delete = QPushButton("删除")
        self._btn_save = QPushButton("保存")
        self._btn_activate = QPushButton("激活此配置")
        self._btn_activate.setObjectName("PrimaryButton")
        for column, button in enumerate((self._btn_new, self._btn_delete, self._btn_save, self._btn_activate), start=2):
            top_layout.addWidget(button, 1, column)
        top_layout.setColumnStretch(0, 1)
        top_layout.setColumnStretch(1, 1)
        layout.addWidget(top_card)

        status = QFrame()
        status.setObjectName("MetricCard")
        status_layout = QHBoxLayout(status)
        status_layout.setContentsMargins(12, 8, 12, 8)
        status_layout.setSpacing(8)
        self._cpu_temp_label = self._status_chip("CPU: --°C")
        self._gpu_temp_label = self._status_chip("GPU: --°C")
        self._fan_rpm_label = self._status_chip("风扇: -- RPM")
        status_layout.addWidget(self._cpu_temp_label)
        status_layout.addWidget(self._gpu_temp_label)
        status_layout.addWidget(self._fan_rpm_label, 1)
        layout.addWidget(status)

        self._curve_tabs = QTabWidget()
        self._cpu_editor = self._make_curve_editor(curve_editor_cls)
        self._gpu_editor = self._make_curve_editor(curve_editor_cls)
        self._curve_tabs.addTab(self._curve_page("CPU 风扇曲线", self._cpu_editor), "CPU 曲线")
        self._curve_tabs.addTab(self._curve_page("GPU 风扇曲线", self._gpu_editor), "GPU 曲线")
        layout.addWidget(self._curve_tabs, 1)

        self._populate_list()
        self._btn_new.clicked.connect(self._new_profile)
        self._btn_delete.clicked.connect(self._delete_profile)
        self._btn_save.clicked.connect(self._save_profile)
        self._btn_activate.clicked.connect(self._activate_profile)

    def _status_chip(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FilePathLabel")
        return label

    def _make_curve_editor(self, curve_editor_cls):  # noqa: ANN001
        editor = curve_editor_cls()
        editor.setMinimumSize(520, 300)
        editor.setAutoFillBackground(True)
        editor.setStyleSheet("background: #eef0ed; border: 1px solid #62686c; border-radius: 6px;")
        return editor

    def _curve_page(self, title: str, editor) -> QWidget:  # noqa: ANN001
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("SectionLabel")
        layout.addWidget(label)
        layout.addWidget(editor, 1)
        return page

    def update_sensor(self, name: str, value: float) -> None:
        if math.isnan(value):
            return
        if name.startswith("CPU T") or name.startswith("CPU C"):
            self._cpu_temp_label.setText(f"CPU: {value:.0f}°C{self._cpu_power}")
        elif "温度" in name and ("GPU" in name or "集显" in name):
            self._gpu_temp_label.setText(f"GPU: {value:.0f}°C{self._gpu_power}")
        elif name.startswith("CPU ") and "package" in name:
            self._cpu_power = f"  {value:.0f}W"
        elif "GPU" in name and "功耗" in name:
            self._gpu_power = f"  {value:.0f}W"

    def update_fan_rpm(self, fan_name: str, rpm: int) -> None:
        self._fan_rpm_data[fan_name] = rpm
        self._refresh_fan_label()

    def update_fan_pwm(self, fan_name: str, pwm: int) -> None:
        self._fan_pwm_data[fan_name] = int(pwm / 255 * 100)
        self._refresh_fan_label()

    def _refresh_fan_label(self) -> None:
        parts = []
        for name, rpm in sorted(self._fan_rpm_data.items()):
            if rpm > 0:
                pct = self._fan_pwm_data.get(name, 0)
                short = name.replace("主板 ", "").replace("PWM", "FAN")
                parts.append(f"{short}: {rpm} RPM ({pct}%)")
        self._fan_rpm_label.setText(" | ".join(parts) if parts else "风扇: -- RPM")

    def _populate_list(self) -> None:
        current = self._profile_combo.currentText().strip()
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        names = sorted(self._pm.list_names())
        self._profile_combo.addItems(names)
        wanted = current or (names[0] if names else "")
        if wanted:
            index = self._profile_combo.findText(wanted)
            if index >= 0:
                self._profile_combo.setCurrentIndex(index)
        self._profile_combo.blockSignals(False)
        self._on_select(wanted)

    def _on_select(self, name: str) -> None:
        if not name:
            return
        profile = self._pm.load(name)
        if profile:
            cpu_pts = profile.curves.get("CPU", [(30, 25), (50, 50), (70, 80), (85, 100)])
            gpu_pts = profile.curves.get("GPU", [(30, 25), (50, 50), (70, 80), (85, 100)])
            self._cpu_editor.set_curve(self._fan_curve_cls(points=cpu_pts))
            self._gpu_editor.set_curve(self._fan_curve_cls(points=gpu_pts))
        active_name = self._active_profile_name()
        if name == active_name:
            self._profile_state_label.setText(f"当前启用：{name}")
        elif active_name:
            self._profile_state_label.setText(f"已启用：{active_name}，当前编辑：{name}")
        else:
            self._profile_state_label.setText(f"当前编辑：{name}")

    def _active_profile_name(self) -> str:
        get_active = getattr(self._pm, "get_active", None)
        if not callable(get_active):
            return ""
        active = get_active()
        return str(getattr(active, "name", "") if active else "")

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "新建配置", "配置名称:")
        if ok and name:
            self._pm.save(self._profile_cls(name=name))
            self._populate_list()
            index = self._profile_combo.findText(name)
            if index >= 0:
                self._profile_combo.setCurrentIndex(index)

    def _delete_profile(self) -> None:
        name = self._profile_combo.currentText().strip()
        if not name:
            return
        if QMessageBox.question(self, "删除", f"删除配置 '{name}'?") == QMessageBox.StandardButton.Yes:
            self._pm.delete(name)
            self._populate_list()

    def _save_profile(self) -> None:
        name = self._profile_combo.currentText().strip()
        if not name:
            return
        profile = self._pm.load(name)
        if profile:
            profile.curves["CPU"] = self._cpu_editor.get_curve().points
            profile.curves["GPU"] = self._gpu_editor.get_curve().points
            self._pm.save(profile)

    def _activate_profile(self) -> None:
        name = self._profile_combo.currentText().strip()
        if not name:
            return
        self._pm.set_active(name)
        self._on_select(name)
        self.profile_activated.emit()
        QMessageBox.information(self, "已激活", f"已切换到 '{name}'")


class FanControlHostPage(QWidget):
    def __init__(
        self,
        project_path: Path | None = None,
        auto_grant_pwm_permissions: bool = True,
        auto_probe_hwmon_drivers: bool = True,
        auto_load: bool = False,
        auto_enable_pwm_control: bool = True,
    ) -> None:
        super().__init__()
        self.project_path = project_path or Path(os.environ.get("USB9_FAN_CONTROL_PATH", DEFAULT_FAN_CONTROL_PATH))
        self.auto_grant_pwm_permissions = auto_grant_pwm_permissions
        self.auto_probe_hwmon_drivers = auto_probe_hwmon_drivers
        self.auto_enable_pwm_control = auto_enable_pwm_control
        self.monitor = None
        self.burner = None
        self._tabs: QTabWidget | None = None
        self._loaded = False
        self._loading = False
        self._profile_manager = None
        self._fans: list[object] = []
        self._sensors: list[object] = []
        self._fan_rows: dict[str, int] = {}
        self._latest_rpm: dict[str, int] = {}
        self._latest_pwm: dict[str, int] = {}
        self._latest_sensor_values: dict[str, float] = {}
        self._rpm_history: dict[str, deque[int]] = {}
        self._sensor_history: dict[str, deque[float]] = {}
        self._fan_cards: dict[str, FanStatusCard] = {}
        self._driver_probe_message = ""
        self._embedded_widgets: list[QWidget] = []

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(24, 22, 24, 24)
        self.layout.setSpacing(16)

        header_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("风扇控制")
        title.setObjectName("PageTitle")
        subtitle = QLabel("先以只读方式查看传感器和风扇状态，需要时再明确启用 PWM 控制")
        subtitle.setObjectName("PageSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_row.addLayout(title_box, 1)
        self.load_button = QPushButton("加载只读监控")
        self.load_button.setObjectName("PrimaryButton")
        self.load_button.clicked.connect(self.load_fan_control)
        header_row.addWidget(self.load_button)
        self.enable_control_button = QPushButton("启用 PWM 控制")
        self.enable_control_button.setObjectName("DangerButton")
        self.enable_control_button.setCheckable(True)
        self.enable_control_button.setEnabled(False)
        self.enable_control_button.clicked.connect(self.toggle_pwm_control)
        header_row.addWidget(self.enable_control_button)
        self.layout.addLayout(header_row)

        self.status_label = QLabel("尚未加载风扇监控。加载只读监控不会写入 PWM。")
        self.status_label.setObjectName("FieldHint")
        self.layout.addWidget(self.status_label)

        summary = QGridLayout()
        summary.setSpacing(12)
        self.control_state_value = QLabel("未加载")
        self.fan_count_value = QLabel("--")
        self.sensor_count_value = QLabel("--")
        self.active_profile_value = QLabel("--")
        self.permission_value = QLabel(self._permission_summary_text())
        summary.addWidget(self._summary_card("控制状态", self.control_state_value), 0, 0)
        summary.addWidget(self._summary_card("风扇通道", self.fan_count_value), 0, 1)
        summary.addWidget(self._summary_card("传感器", self.sensor_count_value), 0, 2)
        summary.addWidget(self._summary_card("当前策略", self.active_profile_value), 0, 3)
        summary.addWidget(self._summary_card("PWM 权限", self.permission_value), 0, 4)
        self.layout.addLayout(summary)

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setObjectName("FanWorkspaceTabs")
        self.workspace_tabs.setMaximumHeight(560)
        self.workspace_tabs.currentChanged.connect(self._workspace_tab_changed)
        self.overview_tab = QWidget()
        self.overview_layout = QVBoxLayout(self.overview_tab)
        self.overview_layout.setContentsMargins(0, 0, 0, 0)
        self.overview_layout.setSpacing(12)

        self.control_tab = QWidget()
        self.control_layout = QVBoxLayout(self.control_tab)
        self.control_layout.setContentsMargins(0, 0, 0, 0)
        self.control_placeholder = self._tab_placeholder("加载后显示风扇曲线和手动调速控件")
        self.control_layout.addWidget(self.control_placeholder, 1)

        self.strategy_tab = QWidget()
        self.strategy_layout = QVBoxLayout(self.strategy_tab)
        self.strategy_layout.setContentsMargins(0, 0, 0, 0)
        self.strategy_layout.setSpacing(12)
        self.strategy_tabs = QTabWidget()
        self.strategy_quick_tab = QWidget()
        self.strategy_quick_layout = QVBoxLayout(self.strategy_quick_tab)
        self.strategy_quick_layout.setContentsMargins(0, 0, 0, 0)
        self.strategy_quick_layout.setSpacing(12)
        self.strategy_editor_tab = QWidget()
        self.strategy_editor_layout = QVBoxLayout(self.strategy_editor_tab)
        self.strategy_editor_layout.setContentsMargins(0, 0, 0, 0)
        self.strategy_placeholder = self._tab_placeholder("加载后显示完整策略编辑器")
        self.strategy_editor_layout.addWidget(self.strategy_placeholder, 1)
        self.strategy_tabs.addTab(self.strategy_quick_tab, "选择策略")
        self.strategy_tabs.addTab(self.strategy_editor_tab, "编辑曲线")
        self.strategy_tabs.currentChanged.connect(self._strategy_tab_changed)
        self.strategy_layout.addWidget(self.strategy_tabs, 1)

        self.permission_tab = QWidget()
        self.permission_layout = QVBoxLayout(self.permission_tab)
        self.permission_layout.setContentsMargins(0, 0, 0, 0)
        self.permission_layout.setSpacing(12)

        self.details_tab = QWidget()
        self.details_layout = QVBoxLayout(self.details_tab)
        self.details_layout.setContentsMargins(0, 0, 0, 0)
        self.details_layout.setSpacing(12)

        self.test_tab = QWidget()
        self.test_layout = QVBoxLayout(self.test_tab)
        self.test_layout.setContentsMargins(0, 0, 0, 0)
        self.stress_panel = StressTestPanel()
        self.test_layout.addWidget(self.stress_panel, 1)

        self.history_tab = QWidget()
        self.history_layout = QVBoxLayout(self.history_tab)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_placeholder = self._tab_placeholder("加载后显示历史数据曲线")
        self.history_layout.addWidget(self.history_placeholder, 1)

        self.visual_panel = QFrame()
        self.visual_panel.setObjectName("FanDashboardPanel")
        visual_layout = QGridLayout(self.visual_panel)
        visual_layout.setContentsMargins(16, 14, 16, 16)
        visual_layout.setHorizontalSpacing(14)
        visual_layout.setVerticalSpacing(12)
        visual_title = QLabel("实时风扇仪表盘")
        visual_title.setObjectName("SectionLabel")
        self.visual_status_label = QLabel("等待加载监控")
        self.visual_status_label.setObjectName("FieldHint")
        visual_layout.addWidget(visual_title, 0, 0)
        visual_layout.addWidget(self.visual_status_label, 0, 1, Qt.AlignmentFlag.AlignRight)

        self.fan_cards_container = QWidget()
        self.fan_cards_container.setObjectName("FanCardsContainer")
        self.fan_cards_layout = QGridLayout(self.fan_cards_container)
        self.fan_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.fan_cards_layout.setHorizontalSpacing(10)
        self.fan_cards_layout.setVerticalSpacing(10)
        self.fan_cards_empty_label = QLabel("加载后显示每个通道的转速、PWM 和状态")
        self.fan_cards_empty_label.setObjectName("FieldHint")
        self.fan_cards_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fan_cards_layout.addWidget(self.fan_cards_empty_label, 0, 0)
        visual_layout.addWidget(self.fan_cards_container, 1, 0, 1, 2)

        self.rpm_chart = FanTrendChart("RPM 实时趋势", "RPM", default_max=1800)
        self.temperature_chart = FanTrendChart("温度趋势", "°C", default_max=90)
        visual_layout.addWidget(self.rpm_chart, 2, 0)
        visual_layout.addWidget(self.temperature_chart, 2, 1)
        visual_layout.setColumnStretch(0, 1)
        visual_layout.setColumnStretch(1, 1)
        visual_layout.setRowStretch(0, 0)
        visual_layout.setRowStretch(1, 0)
        visual_layout.setRowStretch(2, 1)
        self.overview_layout.addWidget(self.visual_panel)
        self.overview_layout.addStretch(1)

        strategy_panel = QFrame()
        strategy_panel.setObjectName("MetricCard")
        strategy_layout = QGridLayout(strategy_panel)
        strategy_layout.setContentsMargins(14, 12, 14, 12)
        strategy_layout.setHorizontalSpacing(12)
        strategy_layout.setVerticalSpacing(8)
        strategy_title = QLabel("风扇策略")
        strategy_title.setObjectName("SectionLabel")
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(180)
        self.profile_combo.currentIndexChanged.connect(self._preview_profile_selection)
        self.apply_profile_button = QPushButton("设为当前策略")
        self.apply_profile_button.clicked.connect(self.apply_selected_profile)
        strategy_hint = QLabel("策略只在 PWM 启用后写入，当前曲线仍由旧风扇后端执行。")
        strategy_hint.setObjectName("FieldHint")
        strategy_hint.setWordWrap(True)
        strategy_layout.addWidget(strategy_title, 0, 0)
        strategy_layout.addWidget(self.profile_combo, 0, 1)
        strategy_layout.addWidget(self.apply_profile_button, 0, 2)
        strategy_layout.addWidget(strategy_hint, 1, 0, 1, 3)
        self.strategy_quick_layout.addWidget(strategy_panel)
        self.strategy_quick_layout.addStretch(1)

        permission_panel = QFrame()
        permission_panel.setObjectName("MetricCard")
        permission_layout = QGridLayout(permission_panel)
        permission_layout.setContentsMargins(14, 12, 14, 12)
        permission_layout.setHorizontalSpacing(10)
        permission_layout.setVerticalSpacing(8)
        permission_title = QLabel("PWM 权限管理")
        permission_title.setObjectName("SectionLabel")
        permission_hint = QLabel("点击“请求系统权限”会调用 pkexec，并由系统弹窗要求输入密码。")
        permission_hint.setObjectName("FieldHint")
        permission_hint.setWordWrap(True)
        self.refresh_permissions_button = QPushButton("刷新权限")
        self.refresh_permissions_button.clicked.connect(self.refresh_pwm_permissions)
        self.grant_permissions_button = QPushButton("请求系统权限")
        self.grant_permissions_button.clicked.connect(self.grant_pwm_permissions)
        self.copy_permission_commands_button = QPushButton("复制授权命令")
        self.copy_permission_commands_button.clicked.connect(self.copy_pwm_permission_commands)
        self.permission_detail_text = QTextEdit()
        self.permission_detail_text.setReadOnly(True)
        self.permission_detail_text.setMaximumHeight(72)
        permission_layout.addWidget(permission_title, 0, 0)
        permission_layout.addWidget(self.refresh_permissions_button, 0, 1)
        permission_layout.addWidget(self.grant_permissions_button, 0, 2)
        permission_layout.addWidget(self.copy_permission_commands_button, 0, 3)
        permission_layout.addWidget(permission_hint, 1, 0, 1, 4)
        permission_layout.addWidget(self.permission_detail_text, 2, 0, 1, 4)
        self.permission_layout.addWidget(permission_panel)
        self.permission_layout.addStretch(1)
        self._update_permission_summary()

        details_panel = QFrame()
        details_panel.setObjectName("MetricCard")
        details_layout = QVBoxLayout(details_panel)
        details_layout.setContentsMargins(14, 12, 14, 14)
        details_layout.setSpacing(8)
        details_title = QLabel("通道明细")
        details_title.setObjectName("SectionLabel")
        self.fan_table = QTableWidget(0, 5)
        self.fan_table.setObjectName("FanChannelTable")
        self.fan_table.setHorizontalHeaderLabels(["风扇", "RPM", "PWM", "来源", "状态"])
        self.fan_table.verticalHeader().setVisible(False)
        self.fan_table.verticalHeader().setDefaultSectionSize(34)
        self.fan_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.fan_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.fan_table.setAlternatingRowColors(True)
        self.fan_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4):
            self.fan_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.fan_table.setMinimumHeight(170)
        self.fan_table.setMaximumHeight(280)
        self.fan_table_hint = QLabel("加载只读监控后，这里会显示每个风扇的 RPM、PWM、来源和写入状态。")
        self.fan_table_hint.setObjectName("FieldHint")
        self.fan_table_hint.setWordWrap(True)
        details_layout.addWidget(details_title)
        details_layout.addWidget(self.fan_table_hint)
        details_layout.addWidget(self.fan_table)
        self.details_layout.addWidget(details_panel, 1)

        self.workspace_tabs.addTab(self.overview_tab, "概览")
        self.workspace_tabs.addTab(self.control_tab, "调速")
        self.workspace_tabs.addTab(self.strategy_tab, "策略")
        self.workspace_tabs.addTab(self.permission_tab, "权限")
        self.workspace_tabs.addTab(self.details_tab, "明细")
        self.workspace_tabs.addTab(self.history_tab, "历史")
        self.workspace_tabs.addTab(self.test_tab, "压力测试")
        self._workspace_tab_changed(0)
        self.layout.addWidget(self.workspace_tabs)
        self.layout.addStretch(1)
        if auto_load:
            QTimer.singleShot(0, self.load_fan_control)

    def load_fan_control(self) -> None:
        if self._loaded or self._loading:
            return
        self._loading = True
        try:
            if self.auto_probe_hwmon_drivers:
                self._ensure_fan_hwmon_drivers()
            modules = self._import_modules()
            self._build_embedded_ui(modules)
        except Exception as error:
            self.release()
            self.status_label.setText(f"加载失败：{error}")
            self._loading = False
            return
        self._loading = False
        self._loaded = True
        self.load_button.setEnabled(False)
        self.enable_control_button.setEnabled(True)
        self.control_state_value.setText("只读")
        self._update_permission_summary()
        permission_grant_attempted = False
        if self.auto_grant_pwm_permissions:
            permission_grant_attempted = bool(self._permission_grant_commands())
            self.grant_pwm_permissions(silent=True)
        if not self._fans:
            self.enable_control_button.setEnabled(False)
            self.control_state_value.setText("未发现风扇")
            self.status_label.setText("风扇监控已加载，但旧风扇控制后端没有发现 fan/pwm 通道")
            self._update_fan_table_hint()
            return
        if self.auto_enable_pwm_control:
            self.enable_pwm_control()
            return
        if not permission_grant_attempted:
            self.status_label.setText("风扇监控已加载：当前为只读模式，不会写入 PWM")

    def toggle_pwm_control(self) -> None:
        if self.monitor is None:
            self.enable_control_button.setChecked(False)
            self.status_label.setText("请先加载风扇监控")
            return
        if not self._fans:
            self.enable_control_button.setChecked(False)
            self.enable_control_button.setEnabled(False)
            self.status_label.setText("未发现风扇通道：当前系统没有暴露 fan/pwm hwmon 文件")
            self._update_fan_table_hint()
            return
        set_control_enabled = getattr(self.monitor, "set_control_enabled", None)
        if not callable(set_control_enabled):
            self.enable_control_button.setChecked(False)
            self.status_label.setText("当前风扇模块不支持运行时启用 PWM 控制")
            return
        enabled = self.enable_control_button.isChecked()
        if enabled and not self._ensure_pwm_permissions_for_enable():
            return
        try:
            set_control_enabled(enabled)
        except Exception as error:
            self.enable_control_button.setChecked(not enabled)
            self.status_label.setText(f"PWM 状态切换失败：{error}")
            return
        self._set_control_state(enabled)
        self._refresh_fan_table_status()
        if enabled and self.permission_value.text() == "需 sudo/udev":
            self.status_label.setText("PWM 控制已启用；如果风扇不响应，请为 hwmon PWM 文件配置 sudo/udev 写权限")
        else:
            self.status_label.setText("PWM 控制已启用：正在写入当前曲线和配置" if enabled else "PWM 写入已暂停：当前为只读模式")

    def enable_pwm_control(self) -> None:
        self.enable_control_button.setChecked(True)
        self.toggle_pwm_control()

    def _ensure_pwm_permissions_for_enable(self) -> bool:
        self._update_permission_summary()
        if self._can_enable_pwm_control():
            return True
        if not self._permission_grant_commands():
            self.enable_control_button.setChecked(False)
            self.status_label.setText("PWM 文件不可写：没有可自动授权的 sysfs 文件，可复制授权命令手动执行")
            return False
        if not self.grant_pwm_permissions(silent=False):
            self.enable_control_button.setChecked(False)
            return False
        self._update_permission_summary()
        if self._can_enable_pwm_control():
            return True
        self.enable_control_button.setChecked(False)
        self.status_label.setText("PWM 授权命令已执行，但文件仍不可写；请刷新权限或复制授权命令手动执行")
        return False

    def release(self) -> None:
        if self.burner is not None:
            self.burner.stop_all()
        if self.monitor is not None:
            self.monitor.stop()
        self.burner = None
        self.monitor = None
        self._loaded = False
        self._loading = False
        self._profile_manager = None
        self._fans = []
        self._sensors = []
        self._fan_rows = {}
        self._latest_rpm = {}
        self._latest_pwm = {}
        self._latest_sensor_values = {}
        self._rpm_history = {}
        self._sensor_history = {}
        self._clear_embedded_widgets()
        self._clear_fan_cards()
        self.rpm_chart.clear()
        self.temperature_chart.clear()
        self.visual_status_label.setText("等待加载监控")
        self.fan_cards_empty_label.setText("加载后显示每个通道的转速、PWM 和状态")
        self.fan_cards_empty_label.setVisible(True)
        self.fan_cards_layout.addWidget(self.fan_cards_empty_label, 0, 0)
        self.control_placeholder.setVisible(True)
        self.strategy_placeholder.setVisible(True)
        self.history_placeholder.setVisible(True)
        self.strategy_tabs.setCurrentWidget(self.strategy_quick_tab)
        self.stress_panel.clear_burner()
        self.workspace_tabs.setCurrentWidget(self.overview_tab)
        if self._tabs is not None:
            self.layout.removeWidget(self._tabs)
            self._tabs.deleteLater()
            self._tabs = None
        self.fan_table.setRowCount(0)
        self.fan_table_hint.setVisible(True)
        self.load_button.setEnabled(True)
        self.enable_control_button.setChecked(False)
        self.enable_control_button.setEnabled(False)
        self.enable_control_button.setText("启用 PWM 写入")
        self.control_state_value.setText("未加载")
        self.fan_count_value.setText("--")
        self.sensor_count_value.setText("--")
        self.active_profile_value.setText("--")
        self.permission_value.setText(self._permission_summary_text())

    def _workspace_tab_changed(self, index: int) -> None:
        if not hasattr(self, "workspace_tabs"):
            return
        widget = self.workspace_tabs.widget(index)
        if widget is self.overview_tab:
            height = 560
        elif widget is self.control_tab or widget is self.history_tab:
            height = 560
        elif widget is self.details_tab:
            height = 430
        elif widget is self.test_tab:
            height = 330
        elif widget is self.strategy_tab:
            height = 680 if self.strategy_tabs.currentWidget() is self.strategy_editor_tab else 205
        else:
            height = 230
        self.workspace_tabs.setMaximumHeight(height)

    def _strategy_tab_changed(self, _index: int) -> None:
        if hasattr(self, "workspace_tabs") and self.workspace_tabs.currentWidget() is self.strategy_tab:
            self._workspace_tab_changed(self.workspace_tabs.currentIndex())

    def _import_modules(self) -> dict[str, ModuleType | None]:
        if not self.project_path.is_dir():
            raise FileNotFoundError(f"找不到风扇控制项目：{self.project_path}")
        project_root = str(self.project_path)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        import importlib

        modules: dict[str, ModuleType | None] = {
            "monitor": importlib.import_module("src.core.monitor"),
            "burner": importlib.import_module("src.core.burner"),
            "profile": importlib.import_module("src.core.profile"),
            "fan_engine": importlib.import_module("src.core.fan_engine"),
            "fan_control": importlib.import_module("src.ui.fan_control"),
            "profile_editor": importlib.import_module("src.ui.profile_editor"),
            "curve_editor": importlib.import_module("src.ui.widgets.curve_editor"),
        }
        for key, module_name in (
            ("dashboard", "src.ui.dashboard"),
            ("history_view", "src.ui.history_view"),
        ):
            try:
                modules[key] = importlib.import_module(module_name)
            except ModuleNotFoundError as error:
                if error.name != "PySide6.QtCharts":
                    raise
                modules[key] = None
        return modules

    def _ensure_fan_hwmon_drivers(self) -> None:
        if self._system_has_fan_pwm_files():
            return
        ok, message = self._load_fan_hwmon_drivers()
        self._driver_probe_message = message
        if ok and self._system_has_fan_pwm_files():
            self.status_label.setText("已加载主板风扇 hwmon 驱动，正在读取风扇通道...")

    def _system_has_fan_pwm_files(self) -> bool:
        root = Path("/sys/class/hwmon")
        if not root.is_dir():
            return False
        for hwmon in root.glob("hwmon*"):
            try:
                if any(self._is_fan_control_file(path.name) for path in hwmon.iterdir()):
                    return True
            except OSError:
                continue
        return False

    def _is_fan_control_file(self, name: str) -> bool:
        return (
            (name.startswith("fan") and name.endswith("_input"))
            or (name.startswith("pwm") and name[-1:].isdigit())
        )

    def _load_fan_hwmon_drivers(self) -> tuple[bool, str]:
        module_list = " ".join(shlex.quote(module) for module in FAN_HWMON_MODULE_CANDIDATES)
        commands = (
            "for module in "
            + module_list
            + "; do /sbin/modprobe \"$module\" >/dev/null 2>&1 || /usr/sbin/modprobe \"$module\" >/dev/null 2>&1 || true; done"
        )
        return self._run_privileged_shell(commands, timeout=60)

    def _build_embedded_ui(self, modules: dict[str, ModuleType | None]) -> None:
        monitor = modules["monitor"].Monitor()
        burner = modules["burner"].Burner()
        try:
            self._start_monitor_read_only(monitor)

            fan_control = modules["fan_control"].FanControlPanel()
            profile_editor = EmbeddedProfileEditor(
                monitor.profiles,
                modules["curve_editor"].CurveEditor,
                modules["fan_engine"].FanCurve,
                modules["profile"].Profile,
            )
            history_view = modules["history_view"].HistoryView(monitor.history) if modules["history_view"] is not None else _FallbackHistoryView()

            monitor.sensor_updated.connect(self._update_sensor_value)
            monitor.fan_rpm_updated.connect(fan_control.update_fan_rpm)
            monitor.fan_rpm_updated.connect(self._update_fan_rpm)
            monitor.fan_pwm_updated.connect(self._update_fan_pwm)
            if hasattr(monitor, "pwm_error"):
                monitor.pwm_error.connect(self.status_label.setText)
            monitor.sensor_updated.connect(profile_editor.update_sensor)
            monitor.fan_rpm_updated.connect(profile_editor.update_fan_rpm)
            monitor.fan_pwm_updated.connect(profile_editor.update_fan_pwm)
            monitor.history_tick.connect(history_view.refresh_sensors)
            profile_editor.profile_activated.connect(monitor._load_active_profile)

            self.monitor = monitor
            self.burner = burner
            self._profile_manager = monitor.profiles
            self._fans = monitor.fans
            self._sensors = monitor.sensors
            self._populate_fan_control(fan_control, monitor)
            self._refresh_profile_options()
            self._refresh_summary()
            self._refresh_fan_table()

            self._mount_embedded_widgets(
                fan_control=fan_control,
                profile_editor=profile_editor,
                history_view=history_view,
            )
        except Exception:
            burner.stop_all()
            monitor.stop()
            raise

    def _mount_embedded_widgets(self, *, fan_control, profile_editor, history_view) -> None:  # noqa: ANN001
        self._clear_embedded_widgets()
        self.control_placeholder.setVisible(False)
        self.strategy_placeholder.setVisible(False)
        self.history_placeholder.setVisible(False)

        self.control_layout.addWidget(fan_control, 1)
        self.strategy_editor_layout.addWidget(profile_editor, 1)
        self.history_layout.addWidget(history_view, 1)
        self.stress_panel.set_burner(self.burner)
        self._embedded_widgets = [fan_control, profile_editor, history_view]
        self.workspace_tabs.setCurrentWidget(self.overview_tab)
        self._tabs = None

    def _prepare_profile_editor_for_embedding(self, profile_editor) -> None:  # noqa: ANN001
        profile_editor.setMinimumHeight(620)
        profile_list = getattr(profile_editor, "_profile_list", None)
        if profile_list is not None:
            profile_list.setMaximumHeight(58)
        for attr in ("_cpu_editor", "_gpu_editor"):
            editor = getattr(profile_editor, attr, None)
            if editor is not None:
                editor.setMinimumSize(360, 220)
                editor.setMaximumHeight(240)
        status_frame = getattr(profile_editor, "_status_frame", None)
        if status_frame is not None:
            status_frame.setStyleSheet("background: #1a1b1c; border: 1px solid #33363a; border-radius: 6px;")
        for attr in ("_cpu_temp_label", "_gpu_temp_label", "_fan_rpm_label"):
            label = getattr(profile_editor, attr, None)
            if label is not None:
                label.setStyleSheet("color: #e8e9e6; font-size: 13px; font-weight: 800; padding: 4px 10px;")

    def _clear_embedded_widgets(self) -> None:
        for widget in self._embedded_widgets:
            parent = widget.parentWidget()
            layout = parent.layout() if parent is not None else None
            if layout is not None:
                layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._embedded_widgets = []

    def _connect_burn_buttons(self, dashboard) -> None:  # noqa: ANN001
        if self.burner is None:
            return

        def toggle(burn_type: str) -> None:
            if self.burner.running.get(burn_type):
                self.burner.stop(burn_type)
            else:
                self.burner.start(burn_type)

        for attr, burn_type in (("_btn_cpu", "cpu"), ("_btn_fpu", "fpu"), ("_btn_gpu", "gpu")):
            button = getattr(dashboard, attr, None)
            if button is not None:
                button.clicked.connect(lambda _checked=False, kind=burn_type: toggle(kind))
        stop_button = getattr(dashboard, "_btn_stop", None)
        if stop_button is not None:
            stop_button.clicked.connect(self.burner.stop_all)

    def _populate_fan_control(self, fan_control, monitor) -> None:  # noqa: ANN001
        if getattr(fan_control, "_sliders", None):
            return
        temp_sensors = [sensor for sensor in monitor.sensors if getattr(sensor, "unit", "") == "°C"]
        fan_control.populate_fans(monitor.fans, temp_sensors)
        fan_control.connect_signals(monitor)

    def _populate_when_ready(self, dashboard, fan_control, profile_editor, monitor) -> None:  # noqa: ANN001
        def populate_sensors_once(_name: str, _value: float) -> None:
            if hasattr(dashboard, "populate_sensors") and not dashboard._cards:
                dashboard.populate_sensors(monitor.sensors)
                try:
                    monitor.sensor_updated.disconnect(populate_sensors_once)
                except Exception:
                    pass

        def populate_fans_once(_name: str, _value: float) -> None:
            if not fan_control._sliders:
                temp_sensors = [sensor for sensor in monitor.sensors if sensor.unit == "°C"]
                fan_control.populate_fans(monitor.fans, temp_sensors)
                fan_control.connect_signals(monitor)
                try:
                    monitor.sensor_updated.disconnect(populate_fans_once)
                except Exception:
                    pass

        monitor.sensor_updated.connect(populate_sensors_once)
        monitor.sensor_updated.connect(populate_fans_once)

    def _start_monitor_read_only(self, monitor) -> None:  # noqa: ANN001
        try:
            monitor.start(control_enabled=False)
        except TypeError:
            monitor.start()
            set_control_enabled = getattr(monitor, "set_control_enabled", None)
            if callable(set_control_enabled):
                set_control_enabled(False)

    def apply_selected_profile(self) -> None:
        profile_name = self.profile_combo.currentText().strip()
        if not profile_name:
            self.status_label.setText("没有可用风扇策略")
            return
        try:
            if self._profile_manager is None:
                modules = self._import_modules()
                self._profile_manager = modules["profile"].ProfileManager()
            self._profile_manager.set_active(profile_name)
            if self.monitor is not None:
                self.monitor._load_active_profile()
        except Exception as error:
            self.status_label.setText(f"策略切换失败：{error}")
            return
        self.active_profile_value.setText(profile_name)
        state = "已应用" if self._loaded else "已设为启用后使用"
        self.status_label.setText(f"{state}：{profile_name}")

    def _refresh_profile_options(self) -> None:
        if self._profile_manager is None:
            return
        current = self.profile_combo.currentText()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        names = sorted(self._profile_manager.list_names())
        self.profile_combo.addItems(names)
        active = self._profile_manager.get_active()
        active_name = active.name if active else ""
        wanted = current or active_name
        if wanted:
            index = self.profile_combo.findText(wanted)
            if index >= 0:
                self.profile_combo.setCurrentIndex(index)
        self.profile_combo.blockSignals(False)
        self._preview_profile_selection()

    def _refresh_summary(self) -> None:
        if not self._fans and self._loaded:
            self.control_state_value.setText("未发现风扇")
        else:
            self.control_state_value.setText("PWM 已启用" if self._control_is_enabled() else "只读")
        self.fan_count_value.setText(str(len(self._fans)) if self._fans else "--")
        self.sensor_count_value.setText(str(len(self._sensors)) if self._sensors else "--")
        self._update_permission_summary()
        self._update_fan_table_hint()
        self._refresh_fan_visuals()
        self._preview_profile_selection()

    def _refresh_fan_table(self) -> None:
        self.fan_table.setRowCount(len(self._fans))
        self._update_fan_table_hint()
        self._fan_rows = {}
        for row, fan in enumerate(self._fans):
            name = getattr(fan, "name", "Unknown")
            self._fan_rows[name] = row
            self._rpm_history.setdefault(name, deque(maxlen=90))
            self._set_fan_table_item(row, 0, name)
            rpm = self._latest_rpm.get(name)
            pwm = self._latest_pwm.get(name)
            self._set_fan_table_item(row, 1, f"{rpm} RPM" if rpm is not None else "--")
            self._set_fan_table_item(row, 2, self._format_pwm(pwm))
            self._set_fan_table_item(row, 3, self._fan_source(fan))
            self._set_fan_table_item(row, 4, self._fan_status_text(name))
        self.fan_table.resizeColumnsToContents()
        self._refresh_fan_visuals()

    def _update_fan_table_hint(self) -> None:
        if self._fans:
            self.fan_table_hint.setVisible(False)
            return
        self.fan_table_hint.setVisible(True)
        if not self._loaded:
            self.fan_table_hint.setText("加载只读监控后，这里会显示每个风扇的 RPM、PWM、来源和写入状态。")
            return
        self.fan_table_hint.setText(self._fan_discovery_diagnostics())

    def _fan_discovery_diagnostics(self) -> str:
        hwmon_root = Path("/sys/class/hwmon")
        if not hwmon_root.is_dir():
            return "未发现 /sys/class/hwmon；旧风扇控制后端无法读取主板风扇。"
        chips: list[str] = []
        fan_like_count = 0
        for hwmon in sorted(hwmon_root.glob("hwmon*")):
            name_path = hwmon / "name"
            try:
                chip_name = name_path.read_text(encoding="utf-8").strip()
            except OSError:
                chip_name = hwmon.name
            try:
                fan_like = sorted(
                    path.name
                    for path in hwmon.iterdir()
                    if path.name.startswith("fan") or path.name.startswith("pwm")
                )
            except OSError:
                fan_like = []
            fan_like_count += len(fan_like)
            suffix = ", ".join(fan_like[:6]) if fan_like else "无 fan/pwm"
            chips.append(f"{chip_name or hwmon.name}: {suffix}")
        if fan_like_count:
            return "检测到 fan/pwm 文件，但旧后端没有生成风扇通道：\n" + "\n".join(chips[:8])
        return (
            "旧风扇控制后端已运行，但当前 /sys/class/hwmon 没有 fan*_input 或 pwm* 文件。\n"
            "通常需要让主板 Super I/O 驱动暴露风扇，例如 nct6775 / nct6683 / it87；"
            "也可以先运行 sensors-detect 后重启或加载对应内核模块。\n"
            + (f"自动加载结果：{self._driver_probe_message}\n" if self._driver_probe_message else "")
            + "\n".join(chips[:8])
        )

    def _update_fan_rpm(self, fan_name: str, rpm: int) -> None:
        self._latest_rpm[fan_name] = rpm
        self._rpm_history.setdefault(fan_name, deque(maxlen=90)).append(max(0, int(rpm)))
        row = self._fan_row(fan_name)
        if row is not None:
            self._set_fan_table_item(row, 1, f"{rpm} RPM")
            self._set_fan_table_item(row, 4, self._fan_status_text(fan_name))
        self._refresh_fan_visuals()

    def _update_fan_pwm(self, fan_name: str, pwm: int) -> None:
        self._latest_pwm[fan_name] = pwm
        row = self._fan_row(fan_name)
        if row is not None:
            self._set_fan_table_item(row, 2, self._format_pwm(pwm))
            self._set_fan_table_item(row, 4, self._fan_status_text(fan_name))
        self._refresh_fan_visuals()

    def _update_sensor_value(self, sensor_name: str, value: float) -> None:
        if not math.isfinite(value):
            return
        unit = self._sensor_unit(sensor_name)
        if not _is_temperature_chart_sensor(sensor_name, unit):
            return
        self._latest_sensor_values[sensor_name] = float(value)
        self._sensor_history.setdefault(sensor_name, deque(maxlen=90)).append(float(value))
        self._refresh_temperature_chart()

    def _refresh_fan_visuals(self) -> None:
        if not hasattr(self, "rpm_chart"):
            return
        self._rebuild_fan_cards_if_needed()
        fan_names = [str(getattr(fan, "name", "Unknown")) for fan in self._fans]
        for fan in self._fans:
            name = str(getattr(fan, "name", "Unknown"))
            card = self._fan_cards.get(name)
            if card is None:
                continue
            rpm = self._latest_rpm.get(name)
            pwm = self._latest_pwm.get(name)
            card.update_status(
                name=name,
                rpm=rpm,
                pwm=self._format_pwm(pwm),
                source=self._fan_source(fan),
                status=self._fan_status_text(name),
            )
        active_count = sum(1 for name in fan_names if self._latest_rpm.get(name, 0) > 0)
        if fan_names:
            mode = "PWM 写入中" if self._control_is_enabled() else "只读监控"
            if active_count:
                inactive = len(fan_names) - active_count
                suffix = f" · {inactive} 个无转速在明细中" if inactive else ""
                self.visual_status_label.setText(f"{active_count}/{len(fan_names)} 个通道有转速{suffix} · {mode}")
            else:
                self.visual_status_label.setText(f"0/{len(fan_names)} 个通道有转速 · {mode}")
        elif self._loaded:
            self.visual_status_label.setText("未发现风扇通道")
        else:
            self.visual_status_label.setText("等待加载监控")
        self._refresh_rpm_chart()
        self._refresh_temperature_chart()

    def _rebuild_fan_cards_if_needed(self) -> None:
        fan_names = self._overview_fan_names()
        if fan_names == list(self._fan_cards):
            return
        self._clear_fan_cards()
        if not fan_names:
            self.fan_cards_empty_label.setVisible(True)
            self.fan_cards_layout.addWidget(self.fan_cards_empty_label, 0, 0)
            return
        self.fan_cards_empty_label.setVisible(False)
        columns = 3 if len(fan_names) > 2 else max(1, len(fan_names))
        for index, name in enumerate(fan_names):
            card = FanStatusCard(name)
            self._fan_cards[name] = card
            self.fan_cards_layout.addWidget(card, index // columns, index % columns)

    def _overview_fan_names(self) -> list[str]:
        fan_names = [str(getattr(fan, "name", "Unknown")) for fan in self._fans]
        active_names = [name for name in fan_names if self._latest_rpm.get(name, 0) > 0]
        if active_names:
            return sorted(active_names, key=lambda name: (-self._latest_rpm.get(name, 0), name))
        return fan_names

    def _clear_fan_cards(self) -> None:
        if not hasattr(self, "fan_cards_layout"):
            return
        while self.fan_cards_layout.count():
            item = self.fan_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            widget.setParent(self.fan_cards_container)
            if widget is not self.fan_cards_empty_label:
                widget.deleteLater()
        self._fan_cards = {}

    def _refresh_rpm_chart(self) -> None:
        if not hasattr(self, "rpm_chart"):
            return
        fan_names = [str(getattr(fan, "name", "Unknown")) for fan in self._fans]
        active_names = [name for name in fan_names if self._latest_rpm.get(name, 0) > 0]
        chart_names = active_names or fan_names
        ordered_names = sorted(chart_names, key=lambda name: (-self._latest_rpm.get(name, 0), name))
        series = {
            name: list(self._rpm_history.get(name, ()))
            for name in ordered_names[:6]
            if self._rpm_history.get(name)
        }
        self.rpm_chart.set_series(series)

    def _refresh_temperature_chart(self) -> None:
        if not hasattr(self, "temperature_chart"):
            return
        names = sorted(self._sensor_history, key=_temperature_sensor_priority)[:4]
        series = {name: list(self._sensor_history[name]) for name in names if self._sensor_history[name]}
        self.temperature_chart.set_series(series)

    def _sensor_unit(self, sensor_name: str) -> str:
        for sensor in self._sensors:
            if str(getattr(sensor, "name", "")) == sensor_name:
                return str(getattr(sensor, "unit", ""))
        return ""

    def _fan_row(self, fan_name: str) -> int | None:
        return self._fan_rows.get(fan_name)

    def _set_fan_table_item(self, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        if column in (1, 2):
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        else:
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        if column == 4:
            if "PWM 写入中" in text or "已连接" in text:
                item.setForeground(QColor("#b7dfd2"))
            elif "无转速" in text:
                item.setForeground(QColor("#e9c0c4"))
            elif "等待" in text:
                item.setForeground(QColor("#d9bc79"))
        self.fan_table.setItem(row, column, item)

    def _fan_source(self, fan) -> str:  # noqa: ANN001
        pwm_path = str(getattr(fan, "pwm_path", ""))
        if pwm_path.startswith("nvidia:"):
            return "NVIDIA"
        return "主板"

    def _control_is_enabled(self) -> bool:
        return bool(getattr(self.monitor, "control_enabled", False))

    def _set_control_state(self, enabled: bool) -> None:
        self.enable_control_button.setText("暂停 PWM 控制" if enabled else "启用 PWM 控制")
        self.control_state_value.setText("PWM 已启用" if enabled else "只读")
        self._update_permission_summary()
        self._refresh_fan_visuals()

    def _can_enable_pwm_control(self) -> bool:
        if os.geteuid() == 0:
            return True
        details = self._pwm_permission_details()
        if not details:
            return True
        return all(
            bool(
                detail["exists"]
                and detail["writable"]
                and (not detail["enable_exists"] or detail["enable_writable"])
            )
            for detail in details
        )

    def _update_permission_summary(self) -> None:
        self.permission_value.setText(self._permission_summary_text())
        if hasattr(self, "permission_detail_text"):
            self.permission_detail_text.setPlainText(self._permission_detail_text())
        if hasattr(self, "copy_permission_commands_button"):
            commands_available = bool(self._permission_fix_commands())
            self.copy_permission_commands_button.setEnabled(commands_available)
        if hasattr(self, "grant_permissions_button"):
            self.grant_permissions_button.setEnabled(bool(self._permission_grant_commands()))

    def _permission_summary_text(self) -> str:
        if os.geteuid() == 0:
            return "root 会话"
        sysfs_fans = [
            fan
            for fan in self._fans
            if not str(getattr(fan, "pwm_path", "")).startswith("nvidia:")
        ]
        if not sysfs_fans:
            return "普通用户"
        writable = sum(1 for fan in sysfs_fans if os.access(str(getattr(fan, "pwm_path", "")), os.W_OK))
        if writable == len(sysfs_fans):
            return "PWM 可写"
        if writable:
            return f"部分可写 {writable}/{len(sysfs_fans)}"
        return "需 sudo/udev"

    def refresh_pwm_permissions(self) -> None:
        self._update_permission_summary()
        self.status_label.setText(f"PWM 权限已刷新：{self.permission_value.text()}")

    def grant_pwm_permissions(self, silent: bool = False) -> bool:
        commands = self._permission_grant_commands()
        if not commands:
            self._update_permission_summary()
            ready = self._can_enable_pwm_control()
            if not silent:
                if ready:
                    self.status_label.setText(f"无需额外授权：{self.permission_value.text()}")
                else:
                    self.status_label.setText("没有可自动授权的 PWM 文件；可复制授权命令后手动执行。")
            return ready
        interactive = not silent
        if interactive:
            self.status_label.setText("正在请求系统权限；请在系统弹窗中输入密码...")
            QApplication.processEvents()
        ok, message = self._run_permission_grant(commands, interactive=interactive)
        self._update_permission_summary()
        if ok:
            ready = self._can_enable_pwm_control()
            if ready:
                self.status_label.setText(f"PWM 权限已授权：{self.permission_value.text()}")
            else:
                self.status_label.setText("PWM 授权命令已执行，但文件仍不可写；请刷新权限后再试。")
            return ready
        if silent:
            self.status_label.setText(f"PWM 自动授权失败：{message}。可点击复制授权命令后手动执行。")
        else:
            self.status_label.setText(f"PWM 系统授权失败：{message}。可复制授权命令后手动执行。")
        return False

    def copy_pwm_permission_commands(self) -> None:
        commands = self._permission_fix_commands()
        if not commands:
            self.status_label.setText("当前没有需要复制的 PWM 授权命令")
            return
        QApplication.clipboard().setText(commands)
        self.status_label.setText("已复制临时 PWM 授权命令；在终端执行后点击刷新权限")

    def _permission_detail_text(self) -> str:
        details = self._pwm_permission_details()
        if os.geteuid() == 0:
            return "当前 GUI 以 root 身份运行，sysfs PWM 写入权限通常可用。"
        if not self._fans:
            return "尚未加载风扇监控。加载后会列出每个主板 PWM 文件的写入权限。"
        if not details:
            return "未发现需要 sysfs 授权的主板 PWM 通道；NVIDIA 风扇由 NVIDIA 接口控制。"
        lines = []
        for detail in details:
            path_status = self._permission_path_status(detail["exists"], detail["writable"])
            enable_status = self._permission_path_status(detail["enable_exists"], detail["enable_writable"])
            lines.append(
                f"{detail['name']} | PWM {path_status}: {detail['path']}\n"
                f"  enable {enable_status}: {detail['enable_path']}"
            )
        commands = self._permission_fix_commands()
        if commands:
            lines.append("可点击“请求系统权限”弹出系统认证窗口，或复制授权命令后在终端执行。")
        return "\n".join(lines)

    def _permission_path_status(self, exists: bool, writable: bool) -> str:
        if not exists:
            return "不存在"
        return "可写" if writable else "不可写"

    def _permission_fix_commands(self) -> str:
        if os.geteuid() == 0:
            return ""
        command_paths = self._permission_command_paths()
        if not command_paths:
            return ""
        quoted_paths = " ".join(shlex.quote(path) for path in command_paths)
        return "\n".join(
            (
                "# 临时授权当前用户组写入 PWM；重启或设备重枚举后可能失效",
                f"sudo chgrp \"$(id -gn)\" {quoted_paths}",
                f"sudo chmod g+rw {quoted_paths}",
            )
        )

    def _permission_grant_commands(self) -> str:
        command_paths = self._permission_command_paths()
        if not command_paths:
            return ""
        quoted_paths = " ".join(shlex.quote(path) for path in command_paths)
        group_name = self._current_group_name()
        return "\n".join(
            (
                f"chgrp {shlex.quote(group_name)} {quoted_paths}",
                f"chmod g+rw {quoted_paths}",
            )
        )

    def _permission_command_paths(self) -> list[str]:
        command_paths: list[str] = []
        for detail in self._pwm_permission_details():
            if detail["exists"] and not detail["writable"]:
                command_paths.append(str(detail["path"]))
            if detail["enable_exists"] and not detail["enable_writable"]:
                command_paths.append(str(detail["enable_path"]))
        return list(dict.fromkeys(command_paths))

    def _current_group_name(self) -> str:
        try:
            return grp.getgrgid(os.getgid()).gr_name
        except KeyError:
            return str(os.getgid())

    def _run_permission_grant(self, commands: str, *, interactive: bool = True) -> tuple[bool, str]:
        timeout = 120 if interactive else 45
        return self._run_privileged_shell(commands, timeout=timeout, interactive=interactive)

    def _run_privileged_shell(self, commands: str, *, timeout: int, interactive: bool = False) -> tuple[bool, str]:
        if os.geteuid() == 0:
            command = ["/bin/sh", "-c", commands]
        elif interactive:
            pkexec = shutil.which("pkexec")
            if not pkexec:
                return False, "未找到 pkexec，无法弹出系统授权窗口"
            command = [pkexec, "/bin/sh", "-c", commands]
        else:
            sudo = shutil.which("sudo")
            if not sudo:
                return False, "未找到可用的非交互式 sudo"
            command = [sudo, "-n", "/bin/sh", "-c", commands]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, "授权等待超时"
        except OSError as error:
            return False, str(error)
        if result.returncode == 0:
            return True, "ok"
        message = (result.stderr or result.stdout or "").strip()
        if not message:
            message = f"授权命令退出码 {result.returncode}"
        return False, message.splitlines()[-1]

    def _pwm_permission_details(self) -> list[dict[str, object]]:
        details: list[dict[str, object]] = []
        for fan in self._fans:
            path = str(getattr(fan, "pwm_path", ""))
            if not path or path.startswith("nvidia:"):
                continue
            enable_path = f"{path}_enable"
            details.append(
                {
                    "name": str(getattr(fan, "name", "Unknown")),
                    "path": path,
                    "exists": os.path.exists(path),
                    "writable": os.path.exists(path) and os.access(path, os.W_OK),
                    "enable_path": enable_path,
                    "enable_exists": os.path.exists(enable_path),
                    "enable_writable": os.path.exists(enable_path) and os.access(enable_path, os.W_OK),
                }
            )
        return details

    def _preview_profile_selection(self) -> None:
        if self._profile_manager is None:
            self.active_profile_value.setText("--")
            return
        selected_name = self.profile_combo.currentText().strip()
        active = self._profile_manager.get_active()
        active_name = active.name if active else ""
        if active_name:
            suffix = "" if selected_name == active_name else f"（待切换到 {selected_name}）"
            self.active_profile_value.setText(f"{active_name}{suffix}")
        else:
            self.active_profile_value.setText(f"{selected_name}（待应用）" if selected_name else "--")

    def _format_pwm(self, pwm: int | None) -> str:
        if pwm is None:
            return "--"
        return f"{round(pwm / 255 * 100)}% ({pwm})"

    def _fan_status_text(self, fan_name: str) -> str:
        if self.monitor is None:
            return "--"
        if self._control_is_enabled():
            return "PWM 写入中" if fan_name in self._latest_pwm else "等待 PWM"
        rpm = self._latest_rpm.get(fan_name)
        if rpm is None:
            return "等待转速"
        return "已连接，只读" if rpm > 0 else "无转速，只读"

    def _refresh_fan_table_status(self) -> None:
        for fan_name, row in self._fan_rows.items():
            self._set_fan_table_item(row, 4, self._fan_status_text(fan_name))
        self._refresh_fan_visuals()

    def _summary_card(self, title: str, value: QLabel) -> QFrame:
        card = QFrame()
        card.setObjectName("MetricCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(6)
        title_label = QLabel(title)
        title_label.setObjectName("SectionLabel")
        value.setObjectName("HomeMetricValue")
        value.setWordWrap(True)
        card_layout.addWidget(title_label)
        card_layout.addWidget(value, 1)
        return card

    def _tab_placeholder(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldHint")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        return label


class _FallbackFanDashboard(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        self._cards: dict[str, QLabel] = {}

        burn_layout = QHBoxLayout()
        self._btn_cpu = QPushButton("CPU 烤机")
        self._btn_fpu = QPushButton("FPU 烤机")
        self._btn_gpu = QPushButton("GPU 烤机")
        self._btn_stop = QPushButton("停止全部")
        for button in (self._btn_cpu, self._btn_fpu, self._btn_gpu, self._btn_stop):
            burn_layout.addWidget(button)
        burn_layout.addStretch(1)
        layout.addLayout(burn_layout)

        self._sensor_layout = QVBoxLayout()
        self._sensor_layout.setSpacing(6)
        layout.addLayout(self._sensor_layout)
        layout.addStretch(1)

    def populate_sensors(self, sensors) -> None:  # noqa: ANN001
        for sensor in sensors:
            if sensor.name in self._cards:
                continue
            label = QLabel(f"{sensor.name}: -- {sensor.unit}")
            label.setObjectName("ChecklistItem")
            self._cards[sensor.name] = label
            self._sensor_layout.addWidget(label)

    def update_sensor(self, name: str, value: float) -> None:
        label = self._cards.get(name)
        if label is None:
            return
        label.setText(f"{name}: {value:.1f}")


class _FallbackHistoryView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        label = QLabel("当前环境缺少 PySide6.QtCharts，历史曲线图不可用。")
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch(1)

    def refresh_sensors(self) -> None:
        return
