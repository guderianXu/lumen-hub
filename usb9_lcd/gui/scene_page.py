from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QComboBox, QFrame, QGridLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from usb9_lcd.gui.scenes import build_builtin_scenes


class SceneCenterPage(QWidget):
    def __init__(self, apply_scene: Callable[[str], object]) -> None:
        super().__init__()
        self.apply_scene = apply_scene
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(14)

        title = QLabel("场景中心")
        title.setObjectName("PageTitle")
        subtitle = QLabel("统一应用屏幕、灯效、风扇与联力无线状态")
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        panel = QFrame()
        panel.setObjectName("SceneCenterPanel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        self.scene_combo = QComboBox()
        for key, scene in build_builtin_scenes().items():
            self.scene_combo.addItem(scene.name, key)
        self.apply_button = QPushButton("应用场景")
        self.apply_button.setObjectName("PrimaryButton")
        self.apply_button.clicked.connect(self._apply_current_scene)
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setFixedHeight(120)
        self.scene_combo.currentIndexChanged.connect(self._refresh_summary)

        grid.addWidget(QLabel("场景"), 0, 0)
        grid.addWidget(self.scene_combo, 0, 1)
        grid.addWidget(self.apply_button, 0, 2)
        grid.addWidget(self.summary_text, 1, 0, 1, 3)
        layout.addWidget(panel)
        layout.addStretch(1)
        self._refresh_summary()

    def _apply_current_scene(self) -> None:
        key = str(self.scene_combo.currentData() or "")
        if key:
            self.apply_scene(key)

    def _refresh_summary(self) -> None:
        key = str(self.scene_combo.currentData() or "")
        scene = build_builtin_scenes().get(key)
        if scene is None:
            self.summary_text.setPlainText("未选择场景")
            return
        self.summary_text.setPlainText(f"{scene.name}\n{scene.description}")
