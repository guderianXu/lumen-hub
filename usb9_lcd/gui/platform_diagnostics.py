from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from usb9_lcd.gui.settings import GuiSettings
from usb9_lcd.gui.system_status import SystemStatusSnapshot, render_system_status_report
from usb9_lcd.platforms import PlatformAdapter, current_platform


def render_platform_diagnostic_report(
    settings: GuiSettings,
    adapter: PlatformAdapter | None = None,
    snapshot: SystemStatusSnapshot | None = None,
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
    if snapshot is not None:
        lines.extend(["", render_system_status_report(snapshot)])
    return "\n".join(lines)


def render_support_report(
    settings: GuiSettings,
    adapter: PlatformAdapter | None = None,
    snapshot: SystemStatusSnapshot | None = None,
    *,
    generated_at: datetime | None = None,
) -> str:
    timestamp = (generated_at or datetime.now()).isoformat(timespec="seconds")
    return "\n".join(
        [
            "Lumen Hub 支持报告",
            f"生成时间: {timestamp}",
            "",
            render_platform_diagnostic_report(settings, adapter, snapshot),
        ]
    )


class PlatformDiagnosticsDialog(QDialog):
    def __init__(
        self,
        settings: GuiSettings,
        parent: QWidget | None = None,
        status_provider: Callable[[], SystemStatusSnapshot] | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.status_provider = status_provider
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
        self.copy_button = QPushButton("复制报告")
        self.copy_button.clicked.connect(self.copy_report)
        self.save_button = QPushButton("保存报告")
        self.save_button.clicked.connect(self._save_report_dialog)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.close)
        button_row.addStretch(1)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.copy_button)
        button_row.addWidget(self.save_button)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.refresh_report()

    def refresh_report(self) -> None:
        snapshot = self.status_provider() if self.status_provider is not None else None
        self.report_text.setPlainText(render_support_report(self.settings, snapshot=snapshot))

    def copy_report(self) -> bool:
        QApplication.clipboard().setText(self.report_text.toPlainText())
        return True

    def save_report(self, path: str | Path | None = None) -> bool:
        if path is None:
            return self._save_report_dialog()
        report_path = Path(path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(self.report_text.toPlainText(), encoding="utf-8")
        return True

    def _save_report_dialog(self) -> bool:
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "保存 Lumen Hub 支持报告",
            "lumen-hub-support-report.txt",
            "Text Files (*.txt);;All Files (*)",
        )
        if not selected:
            return False
        return self.save_report(selected)
