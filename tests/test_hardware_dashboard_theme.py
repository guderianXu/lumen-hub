from __future__ import annotations

from usb9_lcd.gui.theme import gui_stylesheet


def test_hardware_dashboard_theme_exposes_core_selectors():
    qss = gui_stylesheet()

    required_selectors = (
        "QFrame#AppShell",
        "QFrame#TopBar",
        "QFrame#MainContentShell",
        "QListWidget#SideNav",
        "QFrame#MetricCard",
        "QFrame#HomeHeroPanel",
        "QFrame#HomeStatusCard",
        "QFrame#HomeCommandDock",
        "QFrame#HomeTimelinePanel",
        "QPushButton#PrimaryButton",
        "QPushButton#DangerButton",
        "QPushButton#SecondaryButton",
        "QLabel#StatusPill",
    )
    missing = [selector for selector in required_selectors if selector not in qss]
    assert missing == []


def test_hardware_dashboard_theme_uses_approved_palette_not_generic_purple():
    qss = gui_stylesheet().lower()

    assert "#6ee7d8" in qss or "#67d8c5" in qss
    assert "#f0b35a" in qss or "#d99a3d" in qss
    assert "#d76f7a" in qss or "#b85c66" in qss
    assert "#7c3aed" not in qss
    assert "#a855f7" not in qss
