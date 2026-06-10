from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QScrollArea, QSlider, QWidget


_GUARDED_TYPES = (QAbstractSpinBox, QComboBox, QSlider)


class _WheelGuard(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, _GUARDED_TYPES):
            return False
        _route_wheel_to_scroll_area(watched, event)
        return True


def install_wheel_guard(root: QWidget) -> None:
    """Route wheel events away from parameter widgets to avoid accidental edits."""
    if getattr(root, "_lumen_hub_wheel_guard", None) is not None:
        return
    guard = _WheelGuard(root)
    for widget in _guarded_widgets(root):
        widget.installEventFilter(guard)
    setattr(root, "_lumen_hub_wheel_guard", guard)


def _guarded_widgets(root: QWidget) -> list[QWidget]:
    widgets: list[QWidget] = []
    if isinstance(root, _GUARDED_TYPES):
        widgets.append(root)
    for widget_type in _GUARDED_TYPES:
        widgets.extend(root.findChildren(widget_type))
    return widgets


def _route_wheel_to_scroll_area(widget: QWidget, event: QEvent) -> None:
    scroll_area = _nearest_scroll_area(widget)
    if scroll_area is None:
        event.accept()
        return

    angle_delta = event.angleDelta()
    pixel_delta = event.pixelDelta()
    vertical_delta = pixel_delta.y() if not pixel_delta.isNull() else _wheel_steps_to_pixels(angle_delta.y())
    horizontal_delta = pixel_delta.x() if not pixel_delta.isNull() else _wheel_steps_to_pixels(angle_delta.x())

    if vertical_delta:
        bar = scroll_area.verticalScrollBar()
        bar.setValue(bar.value() - vertical_delta)
    if horizontal_delta:
        bar = scroll_area.horizontalScrollBar()
        bar.setValue(bar.value() - horizontal_delta)
    event.accept()


def _wheel_steps_to_pixels(delta: int) -> int:
    if not delta:
        return 0
    lines = max(1, QApplication.wheelScrollLines())
    return int(delta / 120 * lines * 20)


def _nearest_scroll_area(widget: QWidget) -> QAbstractScrollArea | None:
    current = widget.parentWidget()
    while current is not None:
        if isinstance(current, (QScrollArea, QAbstractScrollArea)):
            return current
        current = current.parentWidget()
    return None
