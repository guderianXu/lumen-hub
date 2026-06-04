from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QComboBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from usb9_lcd.assets import ASSET_CATEGORIES, ASSET_CATEGORY_ORDER, AssetLibrary, MediaAsset
from usb9_lcd.gui.debug import log_event, log_exception
from usb9_lcd.gui.gif_preview import decode_gif_preview_frames


GIF_PREVIEW_MIN_FRAME_MS = 66


class AssetLibraryPage(QWidget):

    def __init__(

        self,

        asset_library: AssetLibrary,

        auto_refresh_assets: bool = False,

        select_asset_for_playback: Callable[[Path], None] | None = None,

        play_animation: Callable[[], None] | None = None,

        stop_animation: Callable[[], None] | None = None,

    ) -> None:

        super().__init__()

        self.asset_library = asset_library

        self.select_asset_for_playback = select_asset_for_playback

        self.play_animation = play_animation

        self.stop_animation = stop_animation

        self.asset_path_role = int(Qt.ItemDataRole.UserRole)

        self.asset_animated_role = int(Qt.ItemDataRole.UserRole) + 1

        self.selected_asset_path: Path | None = None

        self._media_assets: list[MediaAsset] = []

        self._gif_preview_frames: list[QPixmap] = []

        self._gif_preview_durations: list[int] = []

        self._gif_preview_index = 0

        self.gif_preview_timer = QTimer(self)

        self.gif_preview_timer.setTimerType(Qt.TimerType.PreciseTimer)

        self.gif_preview_timer.timeout.connect(self._show_next_gif_preview_frame)



        layout = QVBoxLayout(self)

        layout.setContentsMargins(24, 22, 24, 24)

        layout.setSpacing(16)



        header = QLabel("素材库")

        header.setObjectName("PageTitle")

        layout.addWidget(header)

        subtitle = QLabel("管理本地图片/GIF 素材，预览后可直接发送到小屏。")

        subtitle.setObjectName("PageSubtitle")

        layout.addWidget(subtitle)



        button_row = QHBoxLayout()

        self.refresh_assets_button = QPushButton("刷新素材")

        self.refresh_assets_button.clicked.connect(self.refresh_assets)

        self.import_asset_button = QPushButton("导入素材")

        self.import_asset_button.clicked.connect(self.import_asset)

        button_row.addWidget(self.refresh_assets_button)

        button_row.addWidget(self.import_asset_button)

        if self.select_asset_for_playback is not None:

            self.select_first_animation_button = QPushButton("选择当前动图")

            self.select_first_animation_button.clicked.connect(self.select_selected_or_first_animated_asset)

            button_row.addWidget(self.select_first_animation_button)

        if self.play_animation is not None:

            self.play_animation_button = QPushButton("播放到屏幕")

            self.play_animation_button.clicked.connect(self.play_animation)

            button_row.addWidget(self.play_animation_button)

        if self.stop_animation is not None:

            self.stop_animation_button = QPushButton("停止播放")

            self.stop_animation_button.clicked.connect(self.stop_animation)

            button_row.addWidget(self.stop_animation_button)

        button_row.addStretch(1)

        layout.addLayout(button_row)



        asset_label = QLabel("本地素材")

        asset_label.setObjectName("SectionLabel")

        layout.addWidget(asset_label)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("分类"))
        self.asset_category_combo = QComboBox()
        self.asset_category_combo.addItem("全部素材", "")
        for category in ASSET_CATEGORY_ORDER:
            self.asset_category_combo.addItem(ASSET_CATEGORIES[category], category)
        self.asset_category_combo.currentIndexChanged.connect(self.refresh_assets)
        filter_row.addWidget(self.asset_category_combo)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)



        browser_row = QHBoxLayout()

        self.asset_list = QListWidget()

        self.asset_list.setMinimumWidth(280)

        self.asset_list.currentItemChanged.connect(self._asset_selection_changed)

        browser_row.addWidget(self.asset_list, 1)



        preview_panel = QFrame()

        preview_panel.setObjectName("MetricCard")

        preview_layout = QVBoxLayout(preview_panel)

        self.asset_preview = QLabel("选择素材预览")

        self.asset_preview.setObjectName("AssetPreview")

        self.asset_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.asset_preview.setMinimumSize(280, 220)

        self.asset_preview_caption = QLabel("未选择素材")

        self.asset_preview_caption.setWordWrap(True)

        self.asset_preview_caption.setObjectName("FieldHint")

        preview_layout.addWidget(self.asset_preview, 1)

        preview_layout.addWidget(self.asset_preview_caption)

        browser_row.addWidget(preview_panel, 1)

        layout.addLayout(browser_row, 3)



        self.asset_list_text = QTextEdit()

        self.asset_list_text.setReadOnly(True)

        self.asset_list_text.setMaximumHeight(96)

        layout.addWidget(self.asset_list_text)



        link_label = QLabel("链接")

        link_label.setObjectName("SectionLabel")

        layout.addWidget(link_label)



        self.asset_links_text = QTextEdit()

        self.asset_links_text.setReadOnly(True)

        layout.addWidget(self.asset_links_text, 1)



        if auto_refresh_assets:

            self.refresh_assets()



    def refresh_assets(self) -> None:

        log_event("asset_refresh_started")

        try:

            self._media_assets = self.asset_library.list_media(category=self._selected_asset_category())

            media_lines = [

                f"{asset.path.name} | {asset.category_label} | {'模板' if asset.template else '用户'} | "
                f"{asset.width}x{asset.height} | {asset.kind} | "

                f"{'动图' if asset.animated else '静态'} | {asset.frame_count} 帧"

                for asset in self._media_assets

            ]

            link_lines = [

                f"{link.title} | {link.url} | {', '.join(link.tags)}"

                for link in self.asset_library.load_links()

            ]

        except Exception as error:

            log_exception("asset_refresh_failed", error)

            self._media_assets = []

            self.asset_list.clear()

            self.asset_list_text.setPlainText(f"素材加载失败：{error}")

            return



        self.asset_list.clear()

        for asset in self._media_assets:

            item = QListWidgetItem(

                f"{asset.path.name}\n{asset.category_label} | {asset.width}x{asset.height} | "

                f"{'动图' if asset.animated else '静态'} | {asset.frame_count} 帧"

            )

            item.setData(self.asset_path_role, str(asset.path))

            item.setData(self.asset_animated_role, asset.animated)

            self.asset_list.addItem(item)



        self.asset_list_text.setPlainText("\n".join(media_lines) or "暂无本地素材")

        self.asset_links_text.setPlainText("\n".join(link_lines) or "暂无链接")

        log_event("asset_refresh_finished", media_count=len(self._media_assets), link_count=len(link_lines))

        if self.asset_list.count() > 0 and self.asset_list.currentRow() < 0:

            self.asset_list.setCurrentRow(0)

    def _selected_asset_category(self) -> str | None:

        category = self.asset_category_combo.currentData()

        return str(category) if category else None



    def selected_media_paths(self) -> list[Path]:

        return [asset.path for asset in self._media_assets if asset.animated]



    def select_first_animated_asset(self) -> None:

        self.select_selected_or_first_animated_asset()



    def select_selected_or_first_animated_asset(self) -> None:

        log_event("asset_select_animation_clicked", selected_asset_path=str(self.selected_asset_path or ""))

        if self.select_asset_for_playback is None:

            return



        paths = self.selected_media_paths()

        if not paths:

            self.asset_list_text.setPlainText("暂无动图素材")

            return



        selected_path = self.selected_asset_path

        if selected_path not in paths:

            selected_path = paths[0]



        self.select_asset_for_playback(selected_path)



    def _asset_selection_changed(self, current: QListWidgetItem | None) -> None:

        self._stop_gif_preview()

        self.asset_preview.clear()

        if current is None:

            self.selected_asset_path = None

            self.asset_preview.setText("选择素材预览")

            self.asset_preview_caption.setText("未选择素材")

            return



        path = Path(str(current.data(self.asset_path_role)))

        self.selected_asset_path = path

        animated = bool(current.data(self.asset_animated_role))

        log_event("asset_selection_changed", path=str(path), animated=animated)

        if animated:

            self._load_gif_preview(path)

            return



        pixmap = QPixmap(str(path))

        if pixmap.isNull():

            self.asset_preview.setText("无法预览")

        else:

            self.asset_preview.setPixmap(

                pixmap.scaled(

                    280,

                    220,

                    Qt.AspectRatioMode.KeepAspectRatio,

                    Qt.TransformationMode.SmoothTransformation,

                )

            )

        self.asset_preview_caption.setText(path.name)



    def _load_gif_preview(self, path: Path) -> None:

        log_event("gif_preview_decode_started", path=str(path))

        try:

            frame_paths = decode_gif_preview_frames(path)

        except Exception as error:

            log_exception("gif_preview_decode_failed", error, path=str(path))

            self.asset_preview.setText("GIF 预览解码失败")

            self.asset_preview_caption.setText(f"{path.name} | {error}")

            return



        decoded_frames = [

            (pixmap, frame.duration_ms)

            for frame in frame_paths

            if not (pixmap := self._raw_preview_pixmap(frame)).isNull()

        ]

        self._gif_preview_frames = [pixmap for pixmap, _duration_ms in decoded_frames]

        self._gif_preview_durations = [

            max(GIF_PREVIEW_MIN_FRAME_MS, duration_ms)

            for _pixmap, duration_ms in decoded_frames

        ]

        if not self._gif_preview_frames:

            log_event("gif_preview_decode_no_frames", path=str(path))

            self.asset_preview.setText("GIF 预览解码失败")

            self.asset_preview_caption.setText(path.name)

            return



        self._gif_preview_index = 0

        self.asset_preview_caption.setText(f"{path.name} | GIF 解码预览 · {len(self._gif_preview_frames)} 帧")

        self._show_next_gif_preview_frame()

        if len(self._gif_preview_frames) > 1:

            self.gif_preview_timer.setInterval(self._gif_preview_durations[0])

            self.gif_preview_timer.start()

        log_event("gif_preview_decode_finished", path=str(path), frame_count=len(self._gif_preview_frames))



    def _raw_preview_pixmap(self, frame) -> QPixmap:  # noqa: ANN001

        try:

            data = frame.path.read_bytes()

        except OSError:

            return QPixmap()



        expected_size = frame.width * frame.height * 4

        if len(data) != expected_size:

            return QPixmap()



        image = QImage(

            data,

            frame.width,

            frame.height,

            frame.width * 4,

            QImage.Format.Format_RGBA8888,

        ).copy()

        return QPixmap.fromImage(image)



    def _show_next_gif_preview_frame(self) -> None:

        if not self._gif_preview_frames:

            self.gif_preview_timer.stop()

            return



        pixmap = self._gif_preview_frames[self._gif_preview_index]

        self.asset_preview.setPixmap(pixmap)

        if self._gif_preview_durations:

            self.gif_preview_timer.setInterval(self._gif_preview_durations[self._gif_preview_index])

        self._gif_preview_index = (self._gif_preview_index + 1) % len(self._gif_preview_frames)



    def _stop_gif_preview(self) -> None:

        self.gif_preview_timer.stop()

        self._gif_preview_frames = []

        self._gif_preview_durations = []

        self._gif_preview_index = 0



    def release_preview_resources(self) -> None:

        log_event("asset_release_preview_resources")

        self._stop_gif_preview()

        self.asset_preview.clear()



    def import_asset_path(self, path: str | Path) -> None:

        log_event("asset_import_started", path=str(path))

        try:

            self.asset_library.import_file(Path(path))

            self.refresh_assets()

        except Exception as error:

            log_exception("asset_import_failed", error, path=str(path))

            self.asset_list_text.setPlainText(f"导入失败：{error}")



    def import_asset(self) -> None:

        log_event("asset_import_dialog_open")

        selected, _ = QFileDialog.getOpenFileName(

            self,

            "导入素材",

            "",

            "素材文件 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;所有文件 (*)",

        )

        if not selected:

            return



        log_event("asset_import_dialog_selected", path=selected)

        self.import_asset_path(selected)



    def closeEvent(self, event) -> None:  # noqa: ANN001

        self.release_preview_resources()

        super().closeEvent(event)
