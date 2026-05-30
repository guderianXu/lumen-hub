from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from usb9_lcd.gui.settings import GuiSettings
from usb9_lcd.platforms import PlatformAdapter, current_platform


def render_platform_diagnostic_report(
    settings: GuiSettings,
    adapter: PlatformAdapter | None = None,
) -> str:
    platform_adapter = adapter or current_platform()
    items = platform_adapter.diagnostic_items(
        openrgb_path=settings.openrgb.app_path,
        openrgb_host=settings.openrgb.host,
        openrgb_port=settings.openrgb.port,
    )
    lines = ["平台诊断", ""]
    for item in items:
        lines.append(f"{'OK' if item.ok else 'WARN'}  {item.label}: {item.detail}")

    candidates = platform_adapter.openrgb_candidate_paths()
    if candidates:
        lines.extend(["", "OpenRGB 候选路径"])
        for candidate in candidates:
            marker = "OK" if candidate.is_file() else "--"
            lines.append(f"{marker}  {candidate}")
    return "\n".join(lines)


class PlatformDiagnosticsDialog(QDialog):
    def __init__(self, settings: GuiSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("系统诊断 / 平台状态")
        self.resize(760, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("系统诊断 / 平台状态")
        title.setObjectName("SectionLabel")
        layout.addWidget(title)

        hint = QLabel("用于检查 Linux/Windows 路径、日志、OpenRGB Server、USB/HID 权限和常见依赖。")
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.report_text = QTextEdit()
        self.report_text.setReadOnly(True)
        layout.addWidget(self.report_text, 1)

        button_row = QHBoxLayout()
        self.refresh_button = QPushButton("刷新诊断")
        self.refresh_button.clicked.connect(self.refresh_report)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.close)
        button_row.addStretch(1)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.refresh_report()

    def refresh_report(self) -> None:
        self.report_text.setPlainText(render_platform_diagnostic_report(self.settings))
