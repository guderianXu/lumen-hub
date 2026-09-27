from __future__ import annotations


def gui_stylesheet() -> str:
    return """
    QMainWindow, QWidget {
        background: #0b0f12;
        color: #eef4f2;
        font-family: "HarmonyOS Sans SC", "MiSans", "Noto Sans CJK SC", "Microsoft YaHei UI", "Segoe UI Variable";
        font-size: 13px;
    }
    QLabel { background: transparent; }
    QScrollArea#PageScrollArea { background: #0b0f12; border: 0; }
    QScrollArea#PageScrollArea > QWidget > QWidget { background: #0b0f12; }

    QFrame#AppShell { background: #0b0f12; }
    QFrame#TopBar {
        background: #11181c;
        border-bottom: 1px solid #26343a;
    }
    QFrame#MainContentShell {
        background: #0d1215;
        border-left: 1px solid #1e2a30;
    }

    QLabel#AppTitle { font-size: 18px; font-weight: 800; color: #f5fbf8; }
    QLabel#DeviceBadge,
    QLabel#StatusPill {
        background: #102d28;
        border: 1px solid #2f8a78;
        border-radius: 10px;
        color: #d8fff6;
        padding: 7px 12px;
        font-weight: 800;
    }
    QLabel#PageTitle { font-size: 24px; font-weight: 800; color: #f5fbf8; }
    QLabel#PageSubtitle { color: #92a2aa; font-size: 13px; }
    QLabel#SectionLabel { color: #dce7e3; font-weight: 900; }
    QLabel#FieldHint { color: #8c9ba2; line-height: 1.35; }
    QLabel#FilePathLabel {
        background: #10171a;
        border: 1px solid #2b3a40;
        border-radius: 10px;
        color: #cbd9d5;
        padding: 8px;
    }
    QLabel#ChecklistItem { color: #9fb0b7; padding: 6px 0; }

    QListWidget {
        background: #10171a;
        border: 1px solid #27343b;
        border-radius: 10px;
        padding: 8px;
        outline: 0;
    }
    QListWidget#SideNav {
        background: #090d10;
        border: 0;
        border-right: 1px solid #1f2a2f;
        border-radius: 0;
        padding: 14px 10px;
    }
    QListWidget::item {
        padding: 12px 10px;
        border-radius: 10px;
        color: #93a1a7;
        margin: 3px 0;
    }
    QListWidget#SideNav::item {
        min-height: 34px;
        padding: 10px 12px;
        border-radius: 10px;
        color: #93a1a7;
        margin: 4px 0;
        font-weight: 700;
    }
    QListWidget::item:hover,
    QListWidget#SideNav::item:hover {
        background: #142026;
        color: #eaf2ef;
    }
    QListWidget::item:selected,
    QListWidget#SideNav::item:selected {
        background: #17382f;
        color: #f4fffb;
        border-left: 3px solid #6ee7d8;
    }

    QLabel#MetricValue { font-size: 30px; font-weight: 800; color: #f5fbf8; }
    QLabel#HomeMetricTitle { font-size: 15px; font-weight: 900; color: #f4f8f7; }
    QLabel#HomeMetricValue { font-size: 14px; font-weight: 700; color: #e5eeeb; }
    QLabel#HomeCompactValue { font-size: 12px; font-weight: 700; color: #b9c8c3; }
    QLabel#FanSummaryValue { font-size: 16px; font-weight: 800; color: #f5fbf8; }

    QFrame#MetricCard,
    QFrame#HomeStatusCard,
    QFrame#HomeFanProfilePanel,
    QFrame#HomeCommandDock,
    QFrame#HomeTimelinePanel,
    QFrame#FanDashboardPanel,
    QFrame#FanChartPanel,
    QFrame#FanStatusCard,
    QFrame#FanRoleMetricCard,
    QFrame#FanCurveEditorPanel,
    QFrame#FanPermissionPanel,
    QFrame#FanPermissionDetailPanel,
    QFrame#FanStressCard,
    QFrame#LightingTargetPanel,
    QFrame#LightingPresetPanel,
    QFrame#LightingActionPanel,
    QFrame#LightingSyncPanel,
    QFrame#LightingScenePanel,
    QFrame#ScreenPreviewCard {
        background: #12191d;
        border: 1px solid #27343b;
        border-radius: 8px;
    }
    QFrame#MetricCard:hover,
    QFrame#HomeStatusCard:hover,
    QFrame#FanDashboardPanel:hover,
    QFrame#FanChartPanel:hover,
    QFrame#FanStatusCard:hover,
    QFrame#FanRoleMetricCard:hover,
    QFrame#FanCurveEditorPanel:hover,
    QFrame#FanPermissionPanel:hover,
    QFrame#FanPermissionDetailPanel:hover,
    QFrame#FanStressCard:hover,
    QFrame#LightingTargetPanel:hover,
    QFrame#LightingPresetPanel:hover,
    QFrame#LightingActionPanel:hover,
    QFrame#LightingSyncPanel:hover,
    QFrame#LightingScenePanel:hover,
    QFrame#ScreenPreviewCard:hover {
        border-color: #3c555b;
        background: #151f23;
    }
    QFrame#HomeHeroPanel {
        min-height: 96px;
        background: #11181c;
        border: 1px solid #303b41;
        border-left: 3px solid #e45b65;
        border-radius: 8px;
    }
    QFrame#HomeMetricGrid { background: transparent; border: 0; }
    QFrame#HomeSubsystemStrip { background: transparent; border: 0; }
    QFrame#HomeStatusCard[statusRole="screen"] { border-top: 2px solid #59b7a5; }
    QFrame#HomeStatusCard[statusRole="cpu"] { border-top: 2px solid #e45b65; }
    QFrame#HomeStatusCard[statusRole="gpu"] { border-top: 2px solid #6d8ee8; }
    QFrame#HomeStatusCard[statusRole="fan"] { border-top: 2px solid #65c6a6; }
    QFrame#HomeStatusCard[statusRole="lighting"] { border-color: #5d5635; }
    QFrame#HomeStatusCard[statusRole="lianli"] { border-color: #365c58; }
    QFrame#HomeStatusCard[statusRole="device-tree"] { border-color: #394b5f; }
    QFrame#HomeStatusCard[statusRole="permission"] { border-color: #5f4930; }
    QFrame#HomeFanProfilePanel {
        background: #10171a;
        border: 1px solid #334148;
        border-left: 3px solid #65c6a6;
    }
    QProgressBar#HomeMetricBar {
        background: #263139;
        border: 0;
        border-radius: 2px;
    }
    QProgressBar#HomeMetricBar[metricRole="temperature"]::chunk {
        background: #e45b65;
        border-radius: 2px;
    }
    QProgressBar#HomeMetricBar[metricRole="load"]::chunk {
        background: #6d8ee8;
        border-radius: 2px;
    }
    QFrame#HomeCommandDock QPushButton { min-height: 30px; }
    QFrame#HomeTimelinePanel { min-height: 128px; }
    QLabel#TimelineItem {
        color: #9fb0b7;
        padding: 3px 0 3px 9px;
        border-left: 2px solid #2d6f65;
    }

    QFrame#FanCommandPanel,
    QFrame#FanControlToolbar,
    QFrame#FanBindingToolbar,
    QFrame#FanControlGroup,
    QFrame#FanBindingGroup,
    QFrame#FanControlRow,
    QFrame#FanBindingRow,
    QFrame#FanChannelEditorSection,
    QFrame#FanChannelEvidenceSection,
    QFrame#FanPermissionStatusChip {
        background: #10171a;
        border: 1px solid #27343b;
        border-radius: 12px;
    }
    QFrame#FanControlRow:hover,
    QFrame#FanBindingRow:hover { border-color: #3c555b; }
    QFrame#FanTrendChart,
    QWidget#FanCurveCanvas {
        background: #0f1518;
        border: 1px solid #2b3a40;
        border-radius: 12px;
    }
    QFrame#MetricCard[running="true"] {
        border-color: #d76f7a;
        background: #20171a;
    }
    QFrame#FanProfileSidebar,
    QWidget#FanSummaryPanel,
    QWidget#FanPermissionStatus,
    QWidget#FanControlIdentityPane,
    QWidget#FanControlWritePane,
    QWidget#FanBindingIdentityPane,
    QWidget#FanBindingPickerPane,
    QFrame#EmbeddedFanSlider {
        background: transparent;
        border: 0;
    }
    QWidget#FanPermissionActions QPushButton { min-height: 30px; }

    QLabel#FanOverviewHeadline {
        background: #101d1b;
        border: 1px solid #2f645b;
        border-radius: 10px;
        color: #dce7e3;
        font-size: 14px;
        font-weight: 900;
        padding: 10px 12px;
    }
    QLabel#FanPermissionStatusValue,
    QLabel#FanControlChannelTitle,
    QLabel#FanCardName,
    QLabel#FanRpmValue,
    QLabel#FanRoleMetricValue {
        color: #f5fbf8;
        font-weight: 900;
    }
    QLabel#FanControlChannelTitle { font-size: 15px; }
    QLabel#FanRpmValue { font-size: 20px; }
    QLabel#FanRoleMetricValue { font-size: 17px; }
    QLabel#FanControlPathLabel,
    QLabel#FanCardMeta { color: #8c9ba2; }
    QLabel#FanRoleMetricTitle,
    QLabel#FanControlPaneTitle {
        color: #9fb0b7;
        font-size: 12px;
        font-weight: 900;
    }
    QLabel#FanRoleSummary,
    QLabel#FanSpeedSummary,
    QLabel#FanIdentityNotice,
    QLabel#FanStressState {
        background: #10171a;
        border: 1px solid #2b3a40;
        border-radius: 10px;
        color: #cbd9d5;
        padding: 8px 10px;
        font-weight: 700;
    }
    QLabel#FanStressState {
        color: #f5fbf8;
        font-size: 18px;
        font-weight: 900;
        padding: 10px;
    }
    QLabel#FanRoleBadge {
        background: transparent;
        font-size: 12px;
        font-weight: 800;
    }
    QLabel#FanIdentityBadge {
        background: #182328;
        border: 1px solid #3b5059;
        border-radius: 7px;
        color: #dce7e3;
        padding: 2px 7px;
        font-size: 12px;
        font-weight: 800;
    }

    QLabel#LcdPreview,
    QLabel#AssetPreview {
        background: #0f1518;
        border: 1px solid #27343b;
        border-radius: 14px;
        color: #9fb0b7;
    }
    QLabel#LcdPreview {
        color: #bff8ec;
        font-size: 28px;
        font-weight: 900;
    }

    QPushButton {
        background: #172126;
        border: 1px solid #32444c;
        border-radius: 10px;
        padding: 8px 13px;
        color: #edf5f2;
        font-weight: 700;
    }
    QPushButton:hover { background: #1d2b31; border-color: #46616b; }
    QPushButton:pressed { background: #25363d; border-color: #5f7d87; }
    QPushButton:disabled {
        background: #10171a;
        border-color: #202b30;
        color: #65747b;
    }
    QPushButton:checked,
    QPushButton#PrimaryButton {
        background: #247766;
        border-color: #6ee7d8;
        color: #f7fffc;
        font-weight: 900;
    }
    QPushButton#PrimaryButton:disabled {
        background: #102520;
        border-color: #28433d;
        color: #78948f;
    }
    QPushButton#SecondaryButton {
        background: #172126;
        border-color: #41545c;
        color: #cbd9d5;
    }
    QPushButton#SecondaryButton:hover {
        background: #1c2a30;
        border-color: #58717a;
        color: #f5fbf8;
    }
    QPushButton#DangerButton {
        background: #351f24;
        border-color: #8b4752;
        color: #ffd9dc;
        font-weight: 900;
    }
    QPushButton#DangerButton:hover {
        background: #452630;
        border-color: #d76f7a;
    }
    QPushButton#DangerButton:checked {
        background: #8b4752;
        border-color: #d76f7a;
        color: #fff7f7;
    }
    QPushButton#DangerButton:disabled {
        background: #151b1e;
        border-color: #27343b;
        color: #65747b;
    }
    QPushButton#SegmentButton { text-align: left; }
    QPushButton#SegmentButton:checked {
        background: #17382f;
        border-color: #6ee7d8;
        color: #f4fffb;
        font-weight: 900;
    }
    QFrame#HomeHeroPanel QPushButton#SegmentButton {
        min-height: 26px;
        padding: 5px 8px;
        text-align: center;
    }
    QPushButton#FanStrategyButton {
        min-height: 32px;
        padding: 6px 10px;
        text-align: center;
    }
    QPushButton#FanStrategyButton:checked {
        background: #1f4037;
        border-color: #65c6a6;
        color: #f6fffc;
        font-weight: 900;
    }
    QPushButton#ColorSwatch {
        min-width: 30px;
        min-height: 30px;
        max-width: 30px;
        max-height: 30px;
        border-radius: 8px;
        border: 2px solid #5f7d87;
        padding: 0;
    }
    QPushButton#ColorSwatch:hover { border-color: #f5fbf8; }

    QLabel#LightingColorPreview {
        min-width: 56px;
        max-width: 56px;
        border: 1px solid #5f7d87;
        border-radius: 8px;
    }

    QComboBox,
    QLineEdit,
    QTextEdit,
    QSpinBox,
    QDoubleSpinBox {
        background: #10171a;
        border: 1px solid #2b3a40;
        border-radius: 10px;
        padding: 7px;
        color: #edf5f2;
        selection-background-color: #247766;
    }
    QComboBox:focus,
    QLineEdit:focus,
    QTextEdit:focus,
    QSpinBox:focus,
    QDoubleSpinBox:focus {
        border-color: #6ee7d8;
        background: #131d21;
    }
    QComboBox::drop-down { border: 0; width: 24px; }

    QTableWidget {
        background: #10171a;
        alternate-background-color: #131d21;
        border: 1px solid #27343b;
        border-radius: 10px;
        gridline-color: #223039;
        color: #dce7e3;
        selection-background-color: #17382f;
        selection-color: #f4fffb;
    }
    QHeaderView::section {
        background: #12191d;
        border: 0;
        border-bottom: 1px solid #27343b;
        color: #dce7e3;
        font-weight: 900;
        padding: 7px;
    }

    QCheckBox { background: transparent; color: #dce7e3; spacing: 8px; }
    QCheckBox::indicator {
        width: 16px;
        height: 16px;
        border-radius: 5px;
        border: 1px solid #5f7d87;
        background: #10171a;
    }
    QCheckBox::indicator:checked {
        background: #247766;
        border-color: #6ee7d8;
    }

    QSlider::groove:horizontal {
        height: 6px;
        background: #27343b;
        border-radius: 3px;
    }
    QSlider::handle:horizontal {
        background: #6ee7d8;
        border: 1px solid #d8fff6;
        width: 16px;
        margin: -6px 0;
        border-radius: 8px;
    }
    QProgressBar {
        background: #27343b;
        border: 0;
        border-radius: 3px;
    }
    QProgressBar::chunk {
        background: #6ee7d8;
        border-radius: 3px;
    }

    QTabWidget::pane {
        border: 1px solid #27343b;
        border-radius: 12px;
        top: -1px;
        background: #12191d;
    }
    QTabBar::tab {
        background: #10171a;
        color: #9fb0b7;
        padding: 8px 12px;
        border: 1px solid #27343b;
        border-bottom: 0;
        border-top-left-radius: 10px;
        border-top-right-radius: 10px;
        margin-right: 3px;
    }
    QTabBar::tab:selected {
        background: #17382f;
        color: #f4fffb;
        border-color: #6ee7d8;
    }
    QTabWidget#FanWorkspaceTabs::pane { background: #0f1518; border-color: #27343b; }
    QTabWidget#FanWorkspaceTabs QTabBar::tab { padding: 9px 14px; min-width: 64px; }
    QTabWidget#FanWorkspaceTabs QTabBar::tab:selected { background: #17382f; border-color: #6ee7d8; }
    QTabWidget#FanChannelDetailTabs::pane,
    QTabWidget#FanPermissionInfoTabs::pane,
    QTabWidget#FanProfileCurveTabs::pane {
        background: #10171a;
        border-color: #27343b;
    }
    QTabWidget#FanProfileCurveTabs QTabBar::tab {
        padding: 7px 12px;
        min-width: 84px;
    }

    QScrollArea#FanControlScrollArea,
    QScrollArea#FanBindingScrollArea {
        background: transparent;
        border: 0;
    }
    QScrollArea#FanControlScrollArea > QWidget > QWidget,
    QScrollArea#FanBindingScrollArea > QWidget > QWidget {
        background: transparent;
    }
    QWidget#FanControlContainer,
    QWidget#FanBindingContainer { background: transparent; }

    QScrollBar:vertical { background: #0b0f12; width: 10px; margin: 0; }
    QScrollBar::handle:vertical {
        background: #31424a;
        min-height: 36px;
        border-radius: 5px;
    }
    QScrollBar::handle:vertical:hover { background: #4b626b; }
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar:horizontal { background: #0b0f12; height: 10px; margin: 0; }
    QScrollBar::handle:horizontal {
        background: #31424a;
        min-width: 36px;
        border-radius: 5px;
    }
    QScrollBar::handle:horizontal:hover { background: #4b626b; }
    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal { width: 0; }

    QStatusBar {
        background: #0b0f12;
        border-top: 1px solid #26343a;
        color: #92a2aa;
    }

    QLabel[status="warning"], QLabel#WarningText { color: #f0b35a; }
    QLabel[status="danger"], QLabel#DangerText { color: #d76f7a; }
    QLabel[status="ok"], QLabel#OkText { color: #6ee7d8; }

    /* Armoury-style neutral shell and crimson interaction palette. */
    QMainWindow, QWidget {
        background: #0b0b0c;
        color: #eeeeef;
    }
    QScrollArea#PageScrollArea,
    QScrollArea#PageScrollArea > QWidget > QWidget,
    QFrame#AppShell { background: #0b0b0c; }
    QFrame#TopBar {
        background: #121212;
        border-bottom: 1px solid #202022;
    }
    QFrame#MainContentShell {
        background: #0d0d0e;
        border-left: 0;
    }
    QLabel#AppTitle {
        color: #f5f5f5;
        font-size: 14px;
        font-weight: 800;
    }
    QLabel#AppSubtitle {
        color: #f12b3f;
        font-size: 10px;
        font-weight: 800;
    }
    QLabel#DeviceBadge {
        background: transparent;
        border: 0;
        border-radius: 0;
        color: #b6b6b8;
        padding: 5px 9px;
        font-weight: 700;
    }
    QLabel#StatusPill {
        background: #251416;
        border: 1px solid #67242c;
        border-radius: 2px;
        color: #f2c7cc;
        padding: 5px 14px;
        font-weight: 800;
    }
    QLabel#PageTitle {
        color: #f4f4f5;
        font-size: 26px;
        font-weight: 900;
    }
    QLabel#PageSubtitle { color: #7f7f82; font-size: 12px; }
    QLabel#FieldHint { color: #77777a; }

    QListWidget#SideNav {
        background: #181819;
        border: 0;
        border-right: 1px solid #202022;
        border-radius: 0;
        padding: 10px 0;
    }
    QListWidget#SideNav::item {
        min-height: 34px;
        padding: 8px 20px;
        border-radius: 0;
        color: #a0a0a3;
        margin: 2px 0;
        font-weight: 600;
    }
    QListWidget#SideNav::item:hover {
        background: #202021;
        color: #efeff0;
    }
    QListWidget#SideNav::item:selected {
        background: #2a1719;
        color: #f5f5f5;
        border-left: 3px solid #f12b3f;
    }

    QFrame#HomeHeroPanel {
        min-height: 58px;
        background: transparent;
        border: 0;
        border-bottom: 1px solid #222224;
        border-radius: 0;
    }
    QLabel#HomeHeaderCaption {
        color: #77777a;
        font-size: 11px;
        font-weight: 700;
    }
    QWidget#HomeDeviceColumn,
    QFrame#HomeDeviceOverview,
    QFrame#HomeMetricGrid,
    QFrame#HomeCommandDock,
    QFrame#HomeTimelinePanel,
    QFrame#HomeFanProfilePanel {
        background: transparent;
        border: 0;
        border-radius: 0;
    }
    QFrame#HomeDeviceOverview {
        border-bottom: 1px solid #29292b;
    }
    QLabel#HomeHardwareImage {
        background: #0d0d0e;
        border: 1px solid #262628;
        border-radius: 0;
    }
    QLabel#HomeDeviceProduct {
        color: #f3f3f4;
        font-size: 16px;
        font-weight: 900;
    }
    QLabel#HomeDeviceIdentity {
        color: #a1a1a4;
        font-size: 12px;
        font-weight: 700;
    }
    QLabel#HomePlatformIdentity {
        color: #66666a;
        font-size: 10px;
    }
    QLabel#HomeMonitorKicker {
        color: #f12b3f;
        font-size: 12px;
        font-weight: 900;
    }
    QFrame#HomeMonitorSection {
        background: transparent;
        border: 0;
        border-radius: 0;
        min-height: 128px;
    }
    QLabel#HomeMonitorTitle {
        color: #e9e9ea;
        font-size: 22px;
        font-weight: 500;
        padding: 0 0 5px 0;
    }
    QLabel#HomeMetricName {
        color: #838386;
        font-size: 11px;
    }
    QLabel#HomeMetricValue {
        color: #a9a9ac;
        font-size: 11px;
        font-weight: 600;
    }
    QLabel#HomeFanListValue {
        color: #a9a9ac;
        font-size: 11px;
        line-height: 1.3;
    }
    QProgressBar#HomeMetricBar {
        background: #2c2c2f;
        border: 0;
        border-radius: 0;
    }
    QProgressBar#HomeMetricBar[metricRole="cpu"]::chunk {
        background: #f12b3f;
        border-radius: 0;
    }
    QProgressBar#HomeMetricBar[metricRole="gpu"]::chunk {
        background: #d4248c;
        border-radius: 0;
    }
    QProgressBar#HomeMetricBar[metricRole="vram"]::chunk {
        background: #7138ff;
        border-radius: 0;
    }

    QFrame#HomeFanProfilePanel {
        border-top: 1px solid #29292b;
    }
    QLabel#HomeFanModeState {
        color: #f12b3f;
        font-size: 11px;
        font-weight: 800;
    }
    QPushButton#FanStrategyButton {
        min-height: 42px;
        background: transparent;
        border: 0;
        border-bottom: 2px solid transparent;
        border-radius: 0;
        color: #9a9a9d;
        padding: 5px 10px;
        text-align: center;
    }
    QPushButton#FanStrategyButton:hover {
        background: #171718;
        color: #f0f0f1;
    }
    QPushButton#FanStrategyButton:checked {
        background: #171718;
        border: 0;
        border-bottom: 2px solid #f12b3f;
        color: #ffffff;
        font-weight: 900;
    }
    QPushButton#HomeFanSettingsButton {
        min-height: 42px;
        background: transparent;
        border: 1px solid #333336;
        border-radius: 2px;
        color: #b8b8ba;
    }

    QFrame#HomeDeviceTile {
        background: #111112;
        border: 1px solid #28282a;
        border-radius: 2px;
    }
    QPushButton#HomeDeviceButton {
        min-height: 20px;
        background: transparent;
        border: 0;
        border-radius: 0;
        color: #e5e5e6;
        padding: 2px 0;
        text-align: left;
        font-weight: 800;
    }
    QPushButton#HomeDeviceButton:hover { color: #f12b3f; }
    QLabel#HomeDeviceTileStatus {
        color: #77777a;
        font-size: 10px;
    }
    QPushButton#HomeSleepButton,
    QPushButton#HomeDeviceAction {
        min-height: 24px;
        border-radius: 2px;
        padding: 5px 8px;
    }
    QPushButton#HomeSleepButton {
        background: #281719;
        border: 1px solid #66252c;
        color: #e8b6bc;
    }
    QLabel#HomeMinorTitle {
        color: #b4b4b6;
        font-size: 11px;
        font-weight: 800;
    }
    QLabel#HomeInlineStatus {
        color: #66666a;
        font-size: 10px;
    }
    QLabel#TimelineItem {
        color: #747477;
        padding: 2px 0 2px 8px;
        border-left: 1px solid #6b222b;
        font-size: 10px;
    }

    QFrame#MetricCard,
    QFrame#HomeStatusCard,
    QFrame#FanDashboardPanel,
    QFrame#FanChartPanel,
    QFrame#FanStatusCard,
    QFrame#FanRoleMetricCard,
    QFrame#FanCurveEditorPanel,
    QFrame#FanPermissionPanel,
    QFrame#FanPermissionDetailPanel,
    QFrame#FanStressCard,
    QFrame#LightingTargetPanel,
    QFrame#LightingPresetPanel,
    QFrame#LightingActionPanel,
    QFrame#LightingSyncPanel,
    QFrame#LightingScenePanel,
    QFrame#ScreenPreviewCard {
        background: #131314;
        border: 1px solid #2b2b2d;
        border-radius: 3px;
    }
    QFrame#FanCommandPanel,
    QFrame#FanControlToolbar,
    QFrame#FanBindingToolbar,
    QFrame#FanControlGroup,
    QFrame#FanBindingGroup,
    QFrame#FanControlRow,
    QFrame#FanBindingRow,
    QFrame#FanChannelEditorSection,
    QFrame#FanChannelEvidenceSection,
    QFrame#FanPermissionStatusChip {
        background: #121213;
        border: 1px solid #2b2b2d;
        border-radius: 3px;
    }
    QFrame#FanTrendChart,
    QWidget#FanCurveCanvas,
    QLabel#LcdPreview,
    QLabel#AssetPreview {
        background: #101011;
        border-color: #2c2c2f;
        border-radius: 3px;
    }

    QPushButton {
        background: #1a1a1b;
        border: 1px solid #38383b;
        border-radius: 3px;
        color: #e8e8e9;
    }
    QPushButton:hover {
        background: #232325;
        border-color: #67676b;
    }
    QPushButton:pressed { background: #2b2b2e; border-color: #8a8a8e; }
    QPushButton:checked,
    QPushButton#PrimaryButton {
        background: #8b1f2b;
        border-color: #f12b3f;
        color: #ffffff;
    }
    QPushButton#SecondaryButton {
        background: #19191a;
        border-color: #3a3a3d;
        color: #c8c8ca;
    }
    QPushButton#DangerButton {
        background: #2a1719;
        border-color: #722832;
        color: #efc6cb;
    }
    QPushButton#SegmentButton:checked {
        background: #2a1719;
        border-color: #f12b3f;
        color: #ffffff;
    }
    QComboBox,
    QLineEdit,
    QTextEdit,
    QSpinBox,
    QDoubleSpinBox {
        background: #141415;
        border-color: #343437;
        border-radius: 3px;
        selection-background-color: #8b1f2b;
    }
    QComboBox:focus,
    QLineEdit:focus,
    QTextEdit:focus,
    QSpinBox:focus,
    QDoubleSpinBox:focus {
        border-color: #f12b3f;
        background: #181819;
    }
    QSlider::groove:horizontal { background: #303033; }
    QSlider::handle:horizontal {
        background: #f12b3f;
        border-color: #ffd1d6;
    }
    QProgressBar { background: #303033; }
    QProgressBar::chunk { background: #f12b3f; }
    QCheckBox::indicator { background: #141415; border-color: #5b5b5f; }
    QCheckBox::indicator:checked { background: #b42434; border-color: #f12b3f; }
    QTabWidget::pane { background: #131314; border-color: #2b2b2d; border-radius: 3px; }
    QTabBar::tab { background: #141415; border-color: #2b2b2d; border-radius: 0; }
    QTabBar::tab:selected { background: #2a1719; border-color: #f12b3f; color: #ffffff; }
    QScrollBar:vertical, QScrollBar:horizontal { background: #0b0b0c; }
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #3b3b3e; }
    QStatusBar { background: #0b0b0c; border-top-color: #242426; color: #77777a; }
    QLabel[status="ok"], QLabel#OkText { color: #62ba8b; }
    """
