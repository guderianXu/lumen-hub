from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

from usb9_lcd.gui.fan_curve_model import (
    DEFAULT_FAN_CURVE_POINTS,
    interpolate_fan_curve_percent,
    sanitize_fan_curve_points,
)


class FanCurveEditor(QWidget):
    curve_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FanCurveCanvas")
        self.setMinimumSize(420, 260)
        self.setMouseTracking(True)
        self._points = sanitize_fan_curve_points(DEFAULT_FAN_CURVE_POINTS)
        self._dragging_idx = -1
        self._margin_left = 54
        self._margin_top = 28
        self._margin_right = 22
        self._margin_bottom = 38

    def set_points(self, points: object) -> None:
        self._points = sanitize_fan_curve_points(points)
        self.update()

    def points(self) -> list[list[int]]:
        return [list(point) for point in sanitize_fan_curve_points(self._points)]

    def output_for_temperature(self, temperature_c: float | int | None) -> int | None:
        return interpolate_fan_curve_percent(self.points(), temperature_c)

    def _plot_rect(self) -> tuple[float, float, float, float]:
        width = max(1, self.width() - self._margin_left - self._margin_right)
        height = max(1, self.height() - self._margin_top - self._margin_bottom)
        return float(self._margin_left), float(self._margin_top), float(width), float(height)

    def _to_screen(self, temp: int, percent: int) -> tuple[float, float]:
        left, top, width, height = self._plot_rect()
        x = left + max(0, min(100, temp)) / 100 * width
        y = top + (100 - max(0, min(100, percent))) / 100 * height
        return x, y

    def _from_screen(self, x: float, y: float) -> list[int]:
        left, top, width, height = self._plot_rect()
        temp = round(max(0, min(100, (x - left) / width * 100)))
        percent = round(max(0, min(100, 100 - (y - top) / height * 100)))
        return [int(temp), int(percent)]

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
        for index, percent in enumerate((100, 75, 50, 25, 0)):
            y = top + height * index / 4
            painter.drawText(8, int(y) + 4, f"{percent}%")

        points = self.points()
        if len(points) >= 2:
            painter.setPen(QPen(QColor("#4cc9f0"), 3))
            for index in range(len(points) - 1):
                x1, y1 = self._to_screen(points[index][0], points[index][1])
                x2, y2 = self._to_screen(points[index + 1][0], points[index + 1][1])
                painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        for index, (temp, percent) in enumerate(points):
            x, y = self._to_screen(temp, percent)
            painter.setBrush(QColor("#ff5a3d") if index == self._dragging_idx else QColor("#ffd166"))
            painter.setPen(QPen(QColor("#111516"), 2))
            painter.drawEllipse(QPointF(x, y), 7, 7)
            painter.setPen(QColor("#f2f3f0"))
            painter.drawText(int(x) + 10, int(y) - 8, f"{temp}°C {percent}%")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._open_context_menu(event)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        for index, (temp, percent) in enumerate(self.points()):
            x, y = self._to_screen(temp, percent)
            if (pos.x() - x) ** 2 + (pos.y() - y) ** 2 <= 196:
                self._dragging_idx = index
                self.update()
                return

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        for temp, percent in self.points():
            x, y = self._to_screen(temp, percent)
            if (pos.x() - x) ** 2 + (pos.y() - y) ** 2 <= 196:
                return
        points = self.points()
        points.append(self._from_screen(pos.x(), pos.y()))
        self._points = sanitize_fan_curve_points(points)
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
        self._points = sanitize_fan_curve_points(points)
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
        for index, (temp, percent) in enumerate(points):
            x, y = self._to_screen(temp, percent)
            if (pos.x() - x) ** 2 + (pos.y() - y) ** 2 <= 196:
                target_idx = index
                break
        if target_idx < 0 or len(points) <= 2:
            return
        menu = QMenu(self)
        action: QAction | None = menu.addAction(
            f"删除控制点 ({points[target_idx][0]}°C, {points[target_idx][1]}%)"
        )
        selected = menu.exec(event.globalPos())
        if selected == action:
            points.pop(target_idx)
            self._points = sanitize_fan_curve_points(points)
            self.curve_changed.emit(self.points())
            self.update()
