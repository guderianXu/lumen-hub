from __future__ import annotations


def gui_stylesheet() -> str:
    return """
    QMainWindow, QWidget {
        background: #101112;
        color: #e8e9e6;
        font-family: "Inter", "Noto Sans CJK SC", "Microsoft YaHei";
        font-size: 13px;
    }
    QLabel { background: transparent; }
    QScrollArea#PageScrollArea { background: #101112; border: 0; }
    QScrollArea#PageScrollArea > QWidget > QWidget { background: #101112; }
    QFrame#AppShell { background: #101112; }
    QFrame#TopBar {
        background: #171819;
        border-bottom: 1px solid #303236;
    }
    QLabel#AppTitle { font-size: 18px; font-weight: 800; color: #f2f3f0; }
    QLabel#DeviceBadge {
        background: #1b2f2a;
        border: 1px solid #4f8c7a;
        border-radius: 6px;
        color: #cbe6dc;
        padding: 6px 10px;
    }
    QLabel#PageTitle { font-size: 25px; font-weight: 800; color: #f2f3f0; }
    QLabel#PageSubtitle { color: #a3a7aa; }
    QLabel#SectionLabel { color: #dfe1dd; font-weight: 800; }
    QLabel#FieldHint { color: #969b9e; line-height: 1.35; }
    QLabel#FilePathLabel {
        background: #18191a;
        border: 1px solid #313438;
        border-radius: 6px;
        color: #cdd1cf;
        padding: 8px;
    }
    QLabel#ChecklistItem { color: #adb2b0; padding: 6px 0; }
    QListWidget {
        background: #18191a;
        border: 1px solid #313438;
        border-radius: 6px;
        padding: 8px;
        outline: 0;
    }
    QListWidget#SideNav {
        background: #121314;
        border: 0;
        border-right: 1px solid #303236;
        border-radius: 0;
        padding: 12px 10px;
    }
    QListWidget::item {
        padding: 12px 10px;
        border-radius: 6px;
        color: #adb2b0;
        margin: 3px 0;
    }
    QListWidget::item:hover { background: #222426; color: #edf0ed; }
    QListWidget::item:selected {
        background: #1f332e;
        color: #f2f3f0;
        border-left: 3px solid #6fb6a0;
    }
    QLabel#MetricValue { font-size: 32px; font-weight: 800; color: #f2f3f0; }
    QLabel#HomeMetricValue { font-size: 20px; font-weight: 800; color: #f2f3f0; }
    QFrame#MetricCard {
        background: #1a1b1c;
        border: 1px solid #33363a;
        border-radius: 8px;
    }
    QFrame#MetricCard:hover { border-color: #484d50; }
    QFrame#LightingTargetPanel,
    QFrame#LightingPresetPanel,
    QFrame#LightingActionPanel,
    QFrame#LightingSyncPanel,
    QFrame#LightingScenePanel {
        background: #1a1b1c;
        border: 1px solid #33363a;
        border-radius: 8px;
    }
    QFrame#LightingTargetPanel:hover,
    QFrame#LightingPresetPanel:hover,
    QFrame#LightingActionPanel:hover,
    QFrame#LightingSyncPanel:hover,
    QFrame#LightingScenePanel:hover {
        border-color: #484d50;
    }
    QFrame#FanDashboardPanel {
        background: #18191a;
        border: 1px solid #34383b;
        border-radius: 8px;
    }
    QFrame#FanTrendChart {
        background: #121314;
        border: 1px solid #303438;
        border-radius: 8px;
    }
    QFrame#FanStatusCard {
        background: #1c1d1e;
        border: 1px solid #34383b;
        border-radius: 8px;
    }
    QFrame#FanRoleMetricCard {
        background: #17191a;
        border: 1px solid #34383b;
        border-radius: 8px;
    }
    QScrollArea#FanControlScrollArea {
        background: transparent;
        border: 0;
    }
    QScrollArea#FanControlScrollArea > QWidget > QWidget {
        background: transparent;
    }
    QWidget#FanControlContainer { background: transparent; }
    QFrame#FanControlGroup {
        background: #18191a;
        border: 1px solid #33373a;
        border-radius: 8px;
    }
    QFrame#FanControlRow {
        background: #1e2021;
        border: 1px solid #34383b;
        border-radius: 7px;
    }
    QFrame#EmbeddedFanSlider {
        background: transparent;
        border: 0;
    }
    QLabel#FanControlChannelTitle {
        color: #f2f3f0;
        font-size: 15px;
        font-weight: 900;
    }
    QLabel#FanCardName { color: #f0f2ef; font-weight: 800; }
    QLabel#FanRpmValue { color: #f2f3f0; font-size: 20px; font-weight: 900; }
    QLabel#FanCardMeta { color: #969b9e; }
    QLabel#FanRoleMetricTitle {
        font-size: 12px;
        font-weight: 900;
    }
    QLabel#FanRoleMetricValue {
        color: #f2f3f0;
        font-size: 17px;
        font-weight: 900;
    }
    QLabel#FanRoleSummary {
        background: #141617;
        border: 1px solid #303438;
        border-radius: 6px;
        color: #cdd4d0;
        padding: 8px 10px;
    }
    QLabel#FanSpeedSummary {
        background: #101213;
        border: 1px solid #2f3838;
        border-radius: 6px;
        color: #dfe5e1;
        padding: 8px 10px;
        font-weight: 700;
    }
    QLabel#FanIdentityNotice {
        background: #141719;
        border: 1px solid #3a4142;
        border-radius: 6px;
        color: #d9dedb;
        padding: 8px 10px;
    }
    QLabel#FanRoleBadge {
        background: transparent;
        font-size: 12px;
        font-weight: 800;
    }
    QLabel#FanIdentityBadge {
        background: #232527;
        border: 1px solid #41474a;
        border-radius: 5px;
        color: #d7dcd9;
        padding: 2px 6px;
        font-size: 12px;
        font-weight: 800;
    }
    QFrame#ScreenPreviewCard {
        background: #171819;
        border: 1px solid #313438;
        border-radius: 8px;
    }
    QLabel#LcdPreview {
        background: #0c0d0e;
        border: 1px solid #393d40;
        border-radius: 8px;
        color: #b7dfd2;
        font-size: 28px;
        font-weight: 900;
    }
    QLabel#AssetPreview {
        background: #0c0d0e;
        border: 1px solid #313438;
        border-radius: 8px;
        color: #8e9497;
    }
    QPushButton {
        background: #232527;
        border: 1px solid #3d4245;
        border-radius: 6px;
        padding: 8px 12px;
        color: #e8e9e6;
    }
    QPushButton:hover { background: #2d3032; border-color: #555b5f; }
    QPushButton:pressed { background: #34383a; border-color: #656c70; }
    QPushButton:disabled {
        background: #18191a;
        border-color: #2a2d30;
        color: #70777a;
    }
    QPushButton:checked, QPushButton#PrimaryButton {
        background: #5d9b8a;
        border-color: #6fb6a0;
        color: #f7faf7;
        font-weight: 800;
    }
    QPushButton#PrimaryButton:disabled {
        background: #20312d;
        border-color: #334842;
        color: #829894;
    }
    QPushButton#DangerButton {
        background: #332527;
        border-color: #704a50;
        color: #e6c4c6;
        font-weight: 800;
    }
    QPushButton#DangerButton:hover {
        background: #442b2f;
        border-color: #94616a;
    }
    QPushButton#DangerButton:checked {
        background: #b76b74;
        border-color: #d7959b;
        color: #fff7f7;
    }
    QPushButton#DangerButton:disabled {
        background: #232527;
        border-color: #3d4245;
        color: #737a7c;
    }
    QPushButton#SegmentButton { text-align: left; }
    QPushButton#SegmentButton:checked {
        background: #1f332e;
        border-color: #6fb6a0;
        color: #f2f3f0;
        font-weight: 800;
    }
    QPushButton#ColorSwatch {
        min-width: 30px;
        min-height: 30px;
        max-width: 30px;
        max-height: 30px;
        border-radius: 6px;
        border: 2px solid #62686c;
        padding: 0;
    }
    QPushButton#ColorSwatch:hover { border-color: #f2f3f0; }
    QLabel#LightingColorPreview {
        min-width: 56px;
        max-width: 56px;
        border: 1px solid #555b5f;
        border-radius: 6px;
    }
    QComboBox, QLineEdit, QTextEdit, QSpinBox {
        background: #18191a;
        border: 1px solid #33373a;
        border-radius: 6px;
        padding: 7px;
        color: #e8e9e6;
        selection-background-color: #426f62;
    }
    QComboBox:focus, QLineEdit:focus, QTextEdit:focus, QSpinBox:focus {
        border-color: #6fb6a0;
        background: #1c1d1e;
    }
    QComboBox::drop-down {
        border: 0;
        width: 24px;
    }
    QTableWidget {
        background: #18191a;
        alternate-background-color: #1c1d1e;
        border: 1px solid #33373a;
        border-radius: 6px;
        gridline-color: #2c2f31;
        color: #dfe1dd;
        selection-background-color: #1f332e;
        selection-color: #f2f3f0;
    }
    QHeaderView::section {
        background: #1c1d1e;
        border: 0;
        border-bottom: 1px solid #33373a;
        color: #d7dcd9;
        font-weight: 800;
        padding: 7px;
    }
    QCheckBox { background: transparent; color: #dfe1dd; spacing: 8px; }
    QCheckBox::indicator {
        width: 16px;
        height: 16px;
        border-radius: 4px;
        border: 1px solid #555b5f;
        background: #18191a;
    }
    QCheckBox::indicator:checked {
        background: #6fb6a0;
        border-color: #a9d9cc;
    }
    QSlider::groove:horizontal {
        height: 6px;
        background: #34383a;
        border-radius: 3px;
    }
    QSlider::handle:horizontal {
        background: #6fb6a0;
        border: 1px solid #c2e7dd;
        width: 16px;
        margin: -6px 0;
        border-radius: 8px;
    }
    QProgressBar {
        background: #34383a;
        border: 0;
        border-radius: 3px;
    }
    QProgressBar::chunk {
        background: #6fb6a0;
        border-radius: 3px;
    }
    QTabWidget::pane {
        border: 1px solid #33363a;
        border-radius: 8px;
        top: -1px;
        background: #171819;
    }
    QTabBar::tab {
        background: #18191a;
        color: #adb2b0;
        padding: 8px 12px;
        border: 1px solid #33363a;
        border-bottom: 0;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        margin-right: 3px;
    }
    QTabBar::tab:selected {
        background: #1f332e;
        color: #f2f3f0;
        border-color: #6fb6a0;
    }
    QScrollBar:vertical {
        background: #101112;
        width: 10px;
        margin: 0;
    }
    QScrollBar::handle:vertical {
        background: #3d4245;
        min-height: 32px;
        border-radius: 5px;
    }
    QScrollBar::handle:vertical:hover { background: #555b5f; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar:horizontal {
        background: #101112;
        height: 10px;
        margin: 0;
    }
    QScrollBar::handle:horizontal {
        background: #3d4245;
        min-width: 32px;
        border-radius: 5px;
    }
    QScrollBar::handle:horizontal:hover { background: #555b5f; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    QStatusBar {
        background: #101112;
        border-top: 1px solid #303236;
        color: #a3a7aa;
    }
    """
