from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget


def _wheel(delta_y: int = -120) -> QWheelEvent:
    return QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(0, 0),
        QPoint(0, delta_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_wheel_guard_routes_spinbox_wheel_to_scroll_area():
    from usb9_lcd.gui.wheel_guard import install_wheel_guard

    app = QApplication.instance() or QApplication([])
    scroll = QScrollArea()
    container = QWidget()
    layout = QVBoxLayout(container)
    spin = QSpinBox()
    spin.setRange(0, 10)
    spin.setValue(5)
    filler = QWidget()
    filler.setMinimumHeight(1200)
    layout.addWidget(spin)
    layout.addWidget(filler)
    scroll.setWidgetResizable(True)
    scroll.setWidget(container)
    scroll.resize(240, 180)
    scroll.show()
    app.processEvents()
    install_wheel_guard(container)

    spin.setFocus()
    before_value = spin.value()
    before_scroll = scroll.verticalScrollBar().value()

    QApplication.sendEvent(spin, _wheel(-120))

    assert spin.value() == before_value
    assert scroll.verticalScrollBar().value() > before_scroll

    scroll.close()
    app.quit()


def test_wheel_guard_blocks_combo_and_slider_value_changes():
    from usb9_lcd.gui.wheel_guard import install_wheel_guard

    app = QApplication.instance() or QApplication([])
    root = QWidget()
    layout = QVBoxLayout(root)
    combo = QComboBox()
    combo.addItems(["A", "B", "C"])
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 10)
    slider.setValue(5)
    layout.addWidget(combo)
    layout.addWidget(slider)
    root.show()
    app.processEvents()
    install_wheel_guard(root)

    combo.setFocus()
    QApplication.sendEvent(combo, _wheel(-120))
    slider.setFocus()
    QApplication.sendEvent(slider, _wheel(120))

    assert combo.currentIndex() == 0
    assert slider.value() == 5

    root.close()
    app.quit()
