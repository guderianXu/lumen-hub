from __future__ import annotations

import colorsys
from collections.abc import Callable
from dataclasses import dataclass
import sys
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from usb9_lcd.gui.debug import log_event, log_exception
from usb9_lcd.gui.operation_queue import HardwareOperation, HardwareOperationQueue
from usb9_lcd.gui.settings import (
    DEFAULT_SETTINGS_PATH,
    GuiSettings,
    LightingUiSettings,
    save_settings as _save_settings_impl,
)
from usb9_lcd.lighting import LightingSettings, LightingTarget, OpenRgbLightingController, OpenRgbServerManager
from usb9_lcd.lighting.effects import LIGHTING_EFFECTS, LIGHTING_EFFECT_MAP, effect_label
from usb9_lcd.lighting.layout import LightingPhysicalLayout
from usb9_lcd.lighting.profiles import openrgb_device_profile_payload


LIGHTING_PALETTES: dict[str, tuple[str, tuple[str, ...]]] = {

    "neon": ("Neon", ("#ff2d55", "#00e5ff", "#7cff6b", "#ffd60a", "#ffffff")),

    "cool": ("Cool tech", ("#0088ff", "#00e5ff", "#6ee7ff", "#a78bfa", "#ffffff")),

    "warm": ("Warm sunset", ("#ff2d55", "#ff7a18", "#ffd60a", "#ffb86b", "#ffffff")),

    "aurora": ("Aurora", ("#22c55e", "#00e5ff", "#8b5cf6", "#f472b6", "#ffffff")),

    "mono": ("Mono contrast", ("#ffffff", "#cbd5e1", "#94a3b8", "#64748b", "#000000")),

}


@dataclass(frozen=True)
class _OpenRgbRuntimeSnapshot:
    host: str
    port: int
    app_path: str
    auto_start_server: bool


@dataclass(frozen=True)
class _LightingUiSnapshot:
    target_id: str
    effect: str
    color: str
    brightness_percent: int
    speed: int
    argb_zone_size: int
    save_mode: bool
    sync_mode: int
    temperature_limit: int
    palette: str

    def profile(self) -> dict[str, object]:
        return {
            "effect": self.effect,
            "color": self.color,
            "brightness_percent": self.brightness_percent,
            "speed": self.speed,
            "argb_zone_size": self.argb_zone_size,
            "save_mode": self.save_mode,
            "sync_mode": self.sync_mode,
            "temperature_limit": self.temperature_limit,
            "palette": self.palette,
        }


@dataclass(frozen=True)
class _LightingApplyCommit:
    target_ids: tuple[str, ...]
    selected_target_id: str
    snapshot: _LightingUiSnapshot


@dataclass(frozen=True)
class _LightingOperationResult:
    message: str
    targets: tuple[LightingTarget, ...] | None = None
    update_target_controls: bool = False
    apply_commit: _LightingApplyCommit | None = None
    active_scene: str | None = None
    argb_next_index: int | None = None


def save_settings(settings: GuiSettings) -> None:
    pages_module = sys.modules.get("usb9_lcd.gui.pages")
    override = getattr(pages_module, "save_settings", None) if pages_module is not None else None
    if override is not None and override is not save_settings:
        override(settings)
        return
    _save_settings_impl(settings)


def render_lighting_target_partition_summary(targets: list[LightingTarget]) -> str:
    if not targets:
        return "分区统计：未发现 OpenRGB 目标"
    device_indexes = sorted({target.device_index for target in targets})
    whole_count = sum(1 for target in targets if target.zone_index is None)
    zone_count = sum(1 for target in targets if target.zone_index is not None)
    lines = [
        "分区统计：",
        f"设备 {len(device_indexes)} 个 / 全设备目标 {whole_count} 个 / 区域 {zone_count} 个",
    ]
    for device_index in device_indexes:
        device_targets = [target for target in targets if target.device_index == device_index]
        whole_targets = [target for target in device_targets if target.zone_index is None]
        zone_targets = [target for target in device_targets if target.zone_index is not None]
        label = _target_device_label(device_targets[0])
        lines.append(f"- {label}: 全设备 {len(whole_targets)} / 分区 {len(zone_targets)}")
        for zone in sorted(zone_targets, key=lambda target: target.zone_index or 0)[:6]:
            lines.append(f"  {zone.zone_index}: {_target_zone_label(zone)}")
        if len(zone_targets) > 6:
            lines.append(f"  ... 还有 {len(zone_targets) - 6} 个分区")
    return "\n".join(lines)


def _target_device_label(target: LightingTarget) -> str:
    return target.name.split(" / ", 1)[0] or f"device:{target.device_index}"


def _target_zone_label(target: LightingTarget) -> str:
    if " / " in target.name:
        return target.name.split(" / ", 1)[1]
    return target.name


class OpenRgbTestDialog(QDialog):
    def __init__(self, page: "LightingPage") -> None:
        super().__init__(page)
        self.page = page
        self.setWindowTitle("OpenRGB 测试窗口")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(620, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("OpenRGB 测试窗口")
        title.setObjectName("SectionLabel")
        layout.addWidget(title)

        hint = QLabel("用于验证主板、风扇和 ARGB 区域的真实写入策略；不会保存到设备固件。")
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.target_combo = QComboBox()
        layout.addWidget(QLabel("测试目标"))
        layout.addWidget(self.target_combo)

        button_row = QHBoxLayout()
        self.refresh_button = QPushButton("刷新画像")
        self.refresh_button.clicked.connect(self.refresh_targets)
        self.static_red_button = QPushButton("稳定静态红")
        self.static_red_button.setObjectName("PrimaryButton")
        self.static_red_button.clicked.connect(self.apply_stable_static_red)
        self.single_target_button = QPushButton("只亮选中目标")
        self.single_target_button.clicked.connect(self.apply_selected_target_red)
        self.off_button = QPushButton("全部关闭")
        self.off_button.clicked.connect(self.turn_off_all)
        for button in (
            self.refresh_button,
            self.static_red_button,
            self.single_target_button,
            self.off_button,
        ):
            button_row.addWidget(button)
        layout.addLayout(button_row)

        self.profile_text = QTextEdit()
        self.profile_text.setReadOnly(True)
        layout.addWidget(self.profile_text, 1)
        self.refresh_targets()

    def refresh_targets(self) -> None:
        self.target_combo.clear()
        for target in self.page.targets:
            self.target_combo.addItem(self.page._target_display_name(target), target.id)
        self._show_profiles()

    def apply_stable_static_red(self) -> None:
        runtime = self.page._openrgb_runtime_snapshot()
        zone_size = self.page.argb_zone_size.value()
        self.page._run_lighting_operation(
            "OpenRGB 测试：稳定静态红...",
            "openrgb_test_static_failed",
            lambda: self.page._openrgb_test_static_red_from_worker(runtime, zone_size),
        )

    def apply_selected_target_red(self) -> None:
        target_id = str(self.target_combo.currentData() or "")
        if not target_id:
            self.profile_text.setPlainText("请先连接 OpenRGB 并选择测试目标。")
            return
        runtime = self.page._openrgb_runtime_snapshot()
        zone_size = self.page.argb_zone_size.value()
        self.page._run_lighting_operation(
            "OpenRGB 测试：只亮选中目标...",
            "openrgb_test_target_failed",
            lambda: self.page._openrgb_test_single_target_red_from_worker(target_id, runtime, zone_size),
        )

    def turn_off_all(self) -> None:
        runtime = self.page._openrgb_runtime_snapshot()
        zone_size = self.page.argb_zone_size.value()
        self.page._run_lighting_operation(
            "OpenRGB 测试：全部关闭...",
            "openrgb_test_off_failed",
            lambda: self.page._turn_off_all_lighting_from_worker(runtime, zone_size),
        )

    def _show_profiles(self) -> None:
        lines: list[str] = []
        for target in self.page.targets:
            if target.zone_index is not None:
                continue
            payload = self.page.settings.lighting.device_profiles.get(target.id, {})
            lines.append(f"{target.id} | {target.name}")
            if isinstance(payload, dict) and payload:
                lines.append(f"  profile={payload.get('key', 'default')}")
                lines.append(f"  static={payload.get('static_strategy', 'standard')}")
                lines.append(f"  zone_size={payload.get('static_zone_size', '--')}")
                notes = payload.get("notes", [])
                if isinstance(notes, list):
                    for note in notes[:3]:
                        lines.append(f"  note={note}")
            else:
                lines.append("  profile=尚未连接或未保存")
        self.profile_text.setPlainText("\n".join(lines) or "尚未发现 OpenRGB 目标。")


class LightingPage(QWidget):

    operation_finished = Signal(object, object)

    status_changed = Signal(str)



    def __init__(

        self,

        controller: OpenRgbLightingController | None = None,

        auto_connect: bool = False,

        settings: GuiSettings | None = None,

        sync_color_provider: Callable[[int], str | None] | None = None,

    ) -> None:

        super().__init__()

        self.controller = controller or OpenRgbLightingController()

        self.settings = settings or GuiSettings()

        self.sync_color_provider = sync_color_provider

        self.controller.host = self.settings.openrgb.host

        self.controller.port = self.settings.openrgb.port

        self.server_manager = OpenRgbServerManager(

            self.settings.openrgb.app_path,

            host=self.settings.openrgb.host,

            port=self.settings.openrgb.port,

        )

        self.targets: list[LightingTarget] = []

        self.effect_map = dict(LIGHTING_EFFECT_MAP)

        self.selected_color = self.settings.lighting.color

        self._lighting_operation_active = False

        self._lighting_closed = False

        self._lighting_threads: list[threading.Thread] = []
        self._lighting_operation_queue = HardwareOperationQueue()

        self._selected_scene_name = self.settings.lighting.active_scene

        self._argb_wizard_targets: list[LightingTarget] = []

        self._argb_wizard_restore: list[LightingSettings] = []

        self._argb_wizard_index = -1
        self._openrgb_test_dialog: OpenRgbTestDialog | None = None

        self.operation_finished.connect(self._lighting_operation_finished)

        layout = QVBoxLayout(self)

        layout.setContentsMargins(24, 22, 24, 24)

        layout.setSpacing(16)



        header = QLabel("灯效控制")

        header.setObjectName("PageTitle")

        subtitle = QLabel("通过 OpenRGB SDK Server 控制主板、内存、风扇和 ARGB 灯带")

        subtitle.setObjectName("PageSubtitle")

        layout.addWidget(header)

        layout.addWidget(subtitle)



        layout.addWidget(self._connection_panel())

        self.lighting_workspace_tabs = self._lighting_workspace()

        layout.addWidget(self.lighting_workspace_tabs)

        self._restore_lighting_settings(self.settings.lighting)
        self._update_lighting_apply_preview()

        self.status_changed.emit(self.home_status_text())



        if auto_connect:

            QTimer.singleShot(0, self.connect_openrgb)



    def _connection_panel(self) -> QFrame:

        panel = QFrame()

        panel.setObjectName("MetricCard")

        layout = QGridLayout(panel)

        layout.setContentsMargins(14, 12, 14, 12)

        layout.setHorizontalSpacing(10)

        layout.setVerticalSpacing(8)

        title = QLabel("OpenRGB")

        title.setObjectName("SectionLabel")

        self.openrgb_host_input = QLineEdit(self.controller.host)

        self.openrgb_host_input.setMaximumWidth(160)

        self.openrgb_port_input = QSpinBox()

        self.openrgb_port_input.setRange(1, 65535)

        self.openrgb_port_input.setValue(self.controller.port)

        self.openrgb_port_input.setMaximumWidth(100)

        self.connect_lighting_button = QPushButton("连接")

        self.connect_lighting_button.setObjectName("PrimaryButton")

        self.connect_lighting_button.clicked.connect(self.connect_openrgb)

        self.refresh_lighting_button = QPushButton("刷新")

        self.refresh_lighting_button.clicked.connect(self.refresh_openrgb_targets)

        self.identify_lighting_button = QPushButton("闪烁识别")

        self.identify_lighting_button.clicked.connect(self.identify_selected_target)
        self.openrgb_test_button = QPushButton("测试窗口")

        self.openrgb_test_button.clicked.connect(self.open_openrgb_test_window)

        self.openrgb_status_label = QLabel("未连接")

        self.openrgb_status_label.setObjectName("FieldHint")

        self.openrgb_status_label.setWordWrap(True)
        self.lighting_save_status_label = QLabel(f"保存状态：配置文件 {DEFAULT_SETTINGS_PATH}")
        self.lighting_save_status_label.setObjectName("FieldHint")
        self.lighting_save_status_label.setWordWrap(True)

        layout.addWidget(title, 0, 0)

        layout.addWidget(QLabel("地址"), 0, 1)

        layout.addWidget(self.openrgb_host_input, 0, 2)

        layout.addWidget(QLabel("端口"), 0, 3)

        layout.addWidget(self.openrgb_port_input, 0, 4)

        layout.addWidget(self.connect_lighting_button, 0, 5)

        layout.addWidget(self.refresh_lighting_button, 0, 6)

        layout.addWidget(self.identify_lighting_button, 0, 7)
        layout.addWidget(self.openrgb_test_button, 0, 8)

        layout.addWidget(self.openrgb_status_label, 1, 0, 1, 9)
        layout.addWidget(self.lighting_save_status_label, 2, 0, 1, 9)

        return panel



    def _lighting_workspace(self) -> QTabWidget:

        tabs = QTabWidget()

        tabs.setObjectName("LightingWorkspaceTabs")

        tabs.addTab(self._quick_lighting_tab(), "快速应用")

        tabs.addTab(self._zone_panel(), "区域")

        tabs.addTab(self._scene_panel(), "场景")

        tabs.addTab(self._lighting_sync_panel(), "联动")

        return tabs



    def _quick_lighting_tab(self) -> QWidget:

        tab = QWidget()

        layout = QGridLayout(tab)

        layout.setContentsMargins(12, 12, 12, 12)

        layout.setHorizontalSpacing(12)

        layout.setVerticalSpacing(12)

        effect_panel = self._effect_panel()

        color_panel = self._color_panel()

        preset_panel = self._preset_panel()

        action_panel = self._lighting_action_panel()

        effect_panel.setMaximumHeight(220)

        preset_panel.setMaximumHeight(118)

        action_panel.setMaximumHeight(170)

        color_panel.setMaximumHeight(360)

        layout.addWidget(effect_panel, 0, 0)

        layout.addWidget(color_panel, 0, 1, 2, 1)

        layout.addWidget(preset_panel, 1, 0)

        layout.addWidget(action_panel, 2, 0, 1, 2)

        layout.setColumnStretch(0, 1)

        layout.setColumnStretch(1, 1)

        layout.setRowStretch(3, 1)

        return tab



    def _effect_panel(self) -> QFrame:

        panel = QFrame()

        panel.setObjectName("MetricCard")

        layout = QVBoxLayout(panel)

        layout.setSpacing(8)

        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("灯效模式")

        title.setObjectName("SectionLabel")

        layout.addWidget(title)



        self.effect_group = QButtonGroup(self)

        effect_grid = QGridLayout()

        effect_grid.setHorizontalSpacing(8)

        effect_grid.setVerticalSpacing(8)

        for index, effect in enumerate(LIGHTING_EFFECTS):

            name = effect.label

            button = QPushButton(name)

            button.setCheckable(True)

            button.setObjectName("SegmentButton")

            if name == "关闭":

                button.setChecked(True)

            self.effect_group.addButton(button, index)

            effect_grid.addWidget(button, index // 6, index % 6)

        self.effect_group.buttonClicked.connect(lambda _button: self._update_lighting_apply_preview())

        layout.addLayout(effect_grid)

        return panel



    def _color_panel(self) -> QFrame:

        panel = QFrame()

        panel.setObjectName("MetricCard")

        layout = QVBoxLayout(panel)

        title = QLabel("颜色与亮度")

        title.setObjectName("SectionLabel")

        layout.addWidget(title)



        self.lighting_palette_combo = QComboBox()

        for key, (label, _colors) in LIGHTING_PALETTES.items():

            self.lighting_palette_combo.addItem(label, key)

        self.lighting_palette_combo.setCurrentIndex(

            max(0, self.lighting_palette_combo.findData(self.settings.lighting.palette))

        )

        self.lighting_palette_combo.currentIndexChanged.connect(self._lighting_palette_changed)

        layout.addWidget(QLabel("调色板"))

        layout.addWidget(self.lighting_palette_combo)



        self.lighting_swatch_buttons: list[QPushButton] = []

        swatch_row = QHBoxLayout()

        for index in range(5):

            swatch = QPushButton()

            swatch.setObjectName("ColorSwatch")

            swatch.clicked.connect(

                lambda _checked=False, button_index=index: self.set_selected_color(

                    self._lighting_palette_colors()[button_index]

                )

            )

            self.lighting_swatch_buttons.append(swatch)

            swatch_row.addWidget(swatch)

        layout.addLayout(swatch_row)

        self._update_lighting_swatches()



        self.selected_color_input = QLineEdit(self.selected_color)

        self.selected_color_input.textChanged.connect(self._selected_color_text_changed)

        self.selected_color_preview = QLabel()

        self.selected_color_preview.setObjectName("LightingColorPreview")

        self.selected_color_preview.setFixedHeight(34)

        custom_color_button = QPushButton("自定义颜色")

        custom_color_button.clicked.connect(self.choose_lighting_color)

        layout.addWidget(QLabel("颜色"))

        color_row = QGridLayout()

        color_row.setHorizontalSpacing(8)

        color_row.addWidget(self.selected_color_input, 0, 0)

        color_row.addWidget(self.selected_color_preview, 0, 1)

        color_row.setColumnStretch(0, 1)

        layout.addLayout(color_row)

        layout.addWidget(custom_color_button)

        self.brightness_slider = QSlider(Qt.Orientation.Horizontal)

        self.brightness_slider.setRange(0, 100)

        self.brightness_slider.setValue(80)

        self.speed_slider = QSlider(Qt.Orientation.Horizontal)

        self.speed_slider.setRange(1, 10)

        self.speed_slider.setValue(5)

        self.brightness_value_label = QLabel()

        self.brightness_value_label.setObjectName("FieldHint")

        self.speed_value_label = QLabel()

        self.speed_value_label.setObjectName("FieldHint")

        self.brightness_slider.valueChanged.connect(self._update_lighting_slider_labels)

        self.speed_slider.valueChanged.connect(self._update_lighting_slider_labels)

        slider_grid = QGridLayout()

        slider_grid.setHorizontalSpacing(8)

        slider_grid.setVerticalSpacing(6)

        slider_grid.addWidget(QLabel("亮度"), 0, 0)

        slider_grid.addWidget(self.brightness_slider, 0, 1)

        slider_grid.addWidget(self.brightness_value_label, 0, 2)

        slider_grid.addWidget(QLabel("速度"), 1, 0)

        slider_grid.addWidget(self.speed_slider, 1, 1)

        slider_grid.addWidget(self.speed_value_label, 1, 2)

        slider_grid.setColumnStretch(1, 1)

        layout.addLayout(slider_grid)

        self._update_lighting_slider_labels()

        self._update_lighting_color_preview()

        return panel



    def _zone_panel(self) -> QFrame:

        panel = QFrame()

        panel.setObjectName("LightingTargetPanel")

        layout = QGridLayout(panel)

        layout.setContentsMargins(14, 12, 14, 12)

        layout.setHorizontalSpacing(10)

        layout.setVerticalSpacing(8)

        title = QLabel("区域")

        title.setObjectName("SectionLabel")

        self.lighting_target_combo = QComboBox()

        self.lighting_target_combo.addItem("请先连接 OpenRGB", "")

        self.lighting_target_combo.currentIndexChanged.connect(self._target_changed)

        self.target_alias_input = QLineEdit()

        self.target_alias_input.setPlaceholderText("区域备注，例如：顶部风扇")

        self.target_alias_input.editingFinished.connect(self._save_current_target_alias)

        self.layout_order_spin = QSpinBox()
        self.layout_order_spin.setRange(0, 255)
        self.layout_order_spin.setToolTip("物理串联顺序；0 表示使用 OpenRGB 原始顺序。")

        self.layout_led_count_spin = QSpinBox()
        self.layout_led_count_spin.setRange(0, 500)
        self.layout_led_count_spin.setSuffix(" 灯")
        self.layout_led_count_spin.setToolTip("0 表示使用 OpenRGB 回报的 LED 数或默认灯珠数量。")

        self.layout_direction_combo = QComboBox()
        self.layout_direction_combo.addItem("正向", "forward")
        self.layout_direction_combo.addItem("反向", "reverse")

        self.layout_port_input = QLineEdit()
        self.layout_port_input.setPlaceholderText("端口/位置，例如：顶部、底部、后置")

        self.save_lighting_layout_button = QPushButton("保存物理布局")
        self.save_lighting_layout_button.clicked.connect(self.save_lighting_layout)

        target_button_row = QHBoxLayout()

        self.save_target_profile_button = QPushButton("保存区域配置")

        self.save_target_profile_button.clicked.connect(self.save_current_target_profile)

        self.load_target_profile_button = QPushButton("加载区域配置")

        self.load_target_profile_button.clicked.connect(self.load_current_target_profile)

        self.identify_argb_button = QPushButton("识别所有 ARGB")

        self.identify_argb_button.clicked.connect(self.identify_argb_targets)

        self.save_next_argb_button = QPushButton("保存并下一步")

        self.save_next_argb_button.clicked.connect(self.save_argb_alias_and_continue)

        self.save_next_argb_button.setVisible(False)

        target_button_row.addWidget(self.save_target_profile_button)

        target_button_row.addWidget(self.load_target_profile_button)

        target_button_row.addWidget(self.identify_argb_button)

        target_button_row.addWidget(self.save_next_argb_button)

        self.openrgb_modes_text = QTextEdit()

        self.openrgb_modes_text.setReadOnly(True)

        self.openrgb_modes_text.setFixedHeight(82)

        layout.addWidget(title, 0, 0)

        layout.addWidget(QLabel("目标"), 0, 1)

        layout.addWidget(self.lighting_target_combo, 0, 2, 1, 3)

        layout.addWidget(QLabel("备注"), 1, 1)

        layout.addWidget(self.target_alias_input, 1, 2, 1, 3)

        layout.addWidget(QLabel("顺序"), 2, 0)

        layout.addWidget(self.layout_order_spin, 2, 1)

        layout.addWidget(QLabel("灯数"), 2, 2)

        layout.addWidget(self.layout_led_count_spin, 2, 3)

        layout.addWidget(QLabel("方向"), 2, 4)

        layout.addWidget(self.layout_direction_combo, 2, 5)

        layout.addWidget(QLabel("端口/位置"), 3, 0)

        layout.addWidget(self.layout_port_input, 3, 1, 1, 3)

        layout.addWidget(self.save_lighting_layout_button, 3, 4, 1, 2)

        layout.addLayout(target_button_row, 4, 0, 1, 5)

        layout.addWidget(self.openrgb_modes_text, 0, 6, 5, 1)

        layout.setColumnStretch(2, 2)

        layout.setColumnStretch(6, 1)

        return panel



    def _preset_panel(self) -> QFrame:

        panel = QFrame()

        panel.setObjectName("LightingPresetPanel")

        layout = QGridLayout(panel)

        layout.setContentsMargins(14, 12, 14, 12)

        layout.setHorizontalSpacing(8)

        layout.setVerticalSpacing(8)

        title = QLabel("快捷预设")

        title.setObjectName("SectionLabel")

        layout.addWidget(title, 0, 0, 1, 3)

        presets = (

            ("全部关闭", "off"),

            ("白光", "white"),

            ("彩虹同步", "rainbow"),

            ("温度联动", "temperature"),

            ("素材主色", "asset"),

        )

        for index, (label, preset) in enumerate(presets):

            button = QPushButton(label)

            button.clicked.connect(lambda _checked=False, name=preset: self.apply_scene_preset(name))

            layout.addWidget(button, 1 + index // 3, index % 3)

        return panel



    def _lighting_action_panel(self) -> QFrame:

        panel = QFrame()

        panel.setObjectName("LightingActionPanel")

        layout = QVBoxLayout(panel)

        layout.setContentsMargins(14, 12, 14, 12)

        layout.setSpacing(10)

        title = QLabel("应用")

        title.setObjectName("SectionLabel")

        self.apply_lighting_button = QPushButton("应用到灯光")

        self.apply_lighting_button.setObjectName("PrimaryButton")

        self.apply_lighting_button.clicked.connect(self.apply_lighting)

        self.apply_all_lighting_checkbox = QCheckBox("同步到所有 OpenRGB 设备")

        self.apply_all_lighting_checkbox.setChecked(True)

        self.apply_all_lighting_checkbox.setToolTip("开启后会把当前灯效写入每个 OpenRGB 设备；关闭后只写入上方选中的目标。")
        self.apply_all_lighting_checkbox.stateChanged.connect(self._update_lighting_apply_preview)

        self.lighting_apply_preview_text = QTextEdit()

        self.lighting_apply_preview_text.setReadOnly(True)

        self.lighting_apply_preview_text.setFixedHeight(82)

        layout.addWidget(title)

        layout.addWidget(self.apply_all_lighting_checkbox)

        layout.addWidget(self.lighting_apply_preview_text)

        layout.addWidget(self.apply_lighting_button)

        return panel



    def _lighting_sync_panel(self) -> QFrame:

        panel = QFrame()

        panel.setObjectName("LightingSyncPanel")

        layout = QVBoxLayout(panel)

        layout.setContentsMargins(14, 12, 14, 12)

        layout.setSpacing(8)

        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("灯光联动")

        title.setObjectName("SectionLabel")

        layout.addWidget(title)

        self.sync_mode_combo = QComboBox()

        self.sync_mode_combo.addItems(

            [

                "关闭",

                "根据 CPU 温度变色",

                "根据 GPU 温度变色",

                "根据 CPU 利用率变色",

                "根据 GPU 利用率变色",

                "跟随当前素材主色",

            ]

        )
        self.sync_mode_combo.currentIndexChanged.connect(self._update_lighting_apply_preview)

        layout.addWidget(self.sync_mode_combo)

        settings_grid = QGridLayout()

        settings_grid.setHorizontalSpacing(8)

        settings_grid.setVerticalSpacing(6)

        self.temperature_limit = QSpinBox()

        self.temperature_limit.setRange(40, 100)

        self.temperature_limit.setValue(75)

        self.temperature_limit.setSuffix(" °C")

        self.argb_zone_size = QSpinBox()

        self.argb_zone_size.setRange(1, 500)

        self.argb_zone_size.setValue(self.settings.lighting.argb_zone_size)

        self.argb_zone_size.setSuffix(" 灯")
        self.argb_zone_size.valueChanged.connect(self._update_lighting_apply_preview)

        settings_grid.addWidget(QLabel("高温阈值"), 0, 0)

        settings_grid.addWidget(self.temperature_limit, 0, 1)

        settings_grid.addWidget(QLabel("灯珠数量"), 1, 0)

        settings_grid.addWidget(self.argb_zone_size, 1, 1)

        layout.addLayout(settings_grid)

        self.save_mode_checkbox = QCheckBox("保存到 OpenRGB 设备配置")

        self.save_mode_checkbox.setChecked(self.settings.lighting.save_mode)
        self.save_mode_checkbox.stateChanged.connect(self._update_lighting_apply_preview)

        layout.addWidget(self.save_mode_checkbox)

        self.apply_sync_lighting_button = QPushButton("应用联动")

        self.apply_sync_lighting_button.setObjectName("PrimaryButton")

        self.apply_sync_lighting_button.clicked.connect(self.apply_lighting)

        layout.addWidget(self.apply_sync_lighting_button)

        layout.addStretch(1)

        return panel



    def _scene_panel(self) -> QFrame:

        panel = QFrame()

        panel.setObjectName("LightingScenePanel")

        layout = QVBoxLayout(panel)

        layout.setContentsMargins(14, 12, 14, 12)

        layout.setSpacing(8)

        title = QLabel("多区域场景")

        title.setObjectName("SectionLabel")

        layout.addWidget(title)

        self.scene_combo = QComboBox()

        self.scene_combo.setEditable(True)

        self.scene_combo.addItems(sorted(self.settings.lighting.scenes))

        if self.settings.lighting.active_scene:

            self.scene_combo.setCurrentText(self.settings.lighting.active_scene)

        self.scene_combo.currentIndexChanged.connect(self._scene_selection_changed)

        scene_grid = QGridLayout()

        scene_grid.setHorizontalSpacing(6)

        scene_grid.setVerticalSpacing(6)

        self.save_scene_button = QPushButton("保存场景")

        self.save_scene_button.clicked.connect(self.save_lighting_scene)

        self.apply_scene_button = QPushButton("应用场景")

        self.apply_scene_button.clicked.connect(self.apply_saved_lighting_scene)

        self.rename_scene_button = QPushButton("重命名")

        self.rename_scene_button.clicked.connect(self.rename_lighting_scene)

        self.delete_scene_button = QPushButton("删除")

        self.delete_scene_button.clicked.connect(self.delete_lighting_scene)

        scene_grid.addWidget(self.save_scene_button, 0, 0)

        scene_grid.addWidget(self.apply_scene_button, 0, 1)

        scene_grid.addWidget(self.rename_scene_button, 0, 2)

        scene_grid.addWidget(self.delete_scene_button, 0, 3)

        self.scene_summary_text = QTextEdit()

        self.scene_summary_text.setReadOnly(True)

        self.scene_summary_text.setMinimumHeight(96)

        self.scene_summary_text.setMaximumHeight(140)

        layout.addWidget(self.scene_combo)

        layout.addLayout(scene_grid)

        layout.addWidget(self.scene_summary_text)

        self._update_scene_summary()

        return panel



    def replace_settings(self, settings: GuiSettings) -> None:
        self.settings = settings
        self.controller.host = settings.openrgb.host
        self.controller.port = settings.openrgb.port
        self.server_manager = OpenRgbServerManager(
            settings.openrgb.app_path,
            host=settings.openrgb.host,
            port=settings.openrgb.port,
        )
        self._selected_scene_name = settings.lighting.active_scene
        widgets = (
            self.openrgb_host_input,
            self.openrgb_port_input,
            self.lighting_palette_combo,
            self.selected_color_input,
            self.brightness_slider,
            self.speed_slider,
            self.argb_zone_size,
            self.save_mode_checkbox,
            self.sync_mode_combo,
            self.temperature_limit,
            self.scene_combo,
            self.effect_group,
        )
        previous = [widget.blockSignals(True) for widget in widgets]
        try:
            self.openrgb_host_input.setText(settings.openrgb.host)
            self.openrgb_port_input.setValue(settings.openrgb.port)
            self._restore_lighting_settings(settings.lighting)
            self.scene_combo.clear()
            self.scene_combo.addItems(sorted(settings.lighting.scenes))
            if settings.lighting.active_scene:
                self.scene_combo.setCurrentText(settings.lighting.active_scene)
        finally:
            for widget, blocked in zip(widgets, previous, strict=False):
                widget.blockSignals(blocked)
        self.selected_color = self.selected_color_input.text().strip()
        self._update_lighting_swatches()
        self._update_lighting_slider_labels()
        self._update_scene_summary()
        self._update_lighting_apply_preview()


    def _openrgb_runtime_snapshot(self) -> _OpenRgbRuntimeSnapshot:
        return _OpenRgbRuntimeSnapshot(
            host=self.controller.host,
            port=self.controller.port,
            app_path=self.settings.openrgb.app_path,
            auto_start_server=self.settings.openrgb.auto_start_server,
        )


    def _current_lighting_ui_snapshot(self, target_id: str) -> _LightingUiSnapshot:
        return _LightingUiSnapshot(
            target_id=target_id,
            effect=self._selected_effect(),
            color=self.selected_color_input.text().strip(),
            brightness_percent=self.brightness_slider.value(),
            speed=self.speed_slider.value(),
            argb_zone_size=self.argb_zone_size.value(),
            save_mode=self.save_mode_checkbox.isChecked(),
            sync_mode=self.sync_mode_combo.currentIndex(),
            temperature_limit=self.temperature_limit.value(),
            palette=self._lighting_palette_key(),
        )


    def connect_openrgb(self) -> None:

        self.controller.host = self.openrgb_host_input.text().strip() or "127.0.0.1"

        self.controller.port = self.openrgb_port_input.value()

        self.settings.openrgb.host = self.controller.host

        self.settings.openrgb.port = self.controller.port

        self.server_manager = OpenRgbServerManager(
            self.settings.openrgb.app_path,
            host=self.controller.host,
            port=self.controller.port,
        )

        save_settings(self.settings)
        self._show_lighting_saved_feedback("OpenRGB 连接设置已保存")
        runtime = self._openrgb_runtime_snapshot()

        self._run_lighting_operation(

            "正在连接 OpenRGB...",

            "openrgb_connect_failed",

            lambda: self._connect_from_worker(runtime),

        )


    def open_openrgb_test_window(self) -> None:

        if self._openrgb_test_dialog is not None and self._openrgb_test_dialog.isVisible():

            self._openrgb_test_dialog.raise_()

            self._openrgb_test_dialog.activateWindow()

            return

        self._openrgb_test_dialog = OpenRgbTestDialog(self)

        self._openrgb_test_dialog.show()



    def apply_scene_preset(self, preset: str) -> None:

        if preset == "off":

            self._select_whole_device_target()

            self._set_effect("off")

            self.sync_mode_combo.setCurrentIndex(0)

            self.set_selected_color("#000000")

            self.brightness_slider.setValue(0)

        elif preset == "white":

            self._set_effect("static")

            self.sync_mode_combo.setCurrentIndex(0)

            self.set_selected_color(self._lighting_palette_colors()[-1])

            self.brightness_slider.setValue(85)

        elif preset == "rainbow":

            self._set_effect("rainbow")

            self.sync_mode_combo.setCurrentIndex(0)

            self.brightness_slider.setValue(80)

            self.speed_slider.setValue(6)

        elif preset == "temperature":

            self._set_effect("static")

            self.sync_mode_combo.setCurrentIndex(2)

            self.brightness_slider.setValue(80)

        elif preset == "asset":

            self._set_effect("static")

            self.sync_mode_combo.setCurrentIndex(5)

            self.brightness_slider.setValue(80)

        self.apply_lighting()



    def save_current_target_profile(self) -> None:

        target_id = str(self.lighting_target_combo.currentData() or "")

        if not target_id:

            self.openrgb_status_label.setText("请先选择灯光目标")

            return

        self._remember_lighting_settings(target_id)

        self.openrgb_status_label.setText("区域配置已保存")
        self._show_lighting_saved_feedback("区域配置已保存")



    def load_current_target_profile(self) -> None:

        target_id = str(self.lighting_target_combo.currentData() or "")

        if not target_id:

            self.openrgb_status_label.setText("请先选择灯光目标")

            return

        if not self._restore_target_profile(target_id):

            self.openrgb_status_label.setText("区域配置已保存")

            return

        self.openrgb_status_label.setText("区域配置已加载")



    def save_lighting_scene(self) -> None:

        name = self.scene_combo.currentText().strip() or "默认场景"

        current_target_id = str(self.lighting_target_combo.currentData() or "")

        if current_target_id:

            self._remember_lighting_settings(current_target_id)

        target_ids = [target.id for target in self.targets]

        profiles = {

            target_id: dict(profile)

            for target_id, profile in self.settings.lighting.target_profiles.items()

            if target_id in target_ids and isinstance(profile, dict)

        }

        if not profiles:

            self.openrgb_status_label.setText("区域配置已保存")

            return

        self.settings.lighting.scenes[name] = {"targets": profiles}

        self.settings.lighting.active_scene = name

        self._selected_scene_name = name

        save_settings(self.settings)
        self._show_lighting_saved_feedback(f"场景已保存：{name}")

        self._refresh_scene_names(name)

        self._update_scene_summary()

        self.openrgb_status_label.setText(f"场景已保存：{name}")



    def apply_saved_lighting_scene(self) -> None:

        name = self.scene_combo.currentText().strip()

        scene = self.settings.lighting.scenes.get(name)

        if not isinstance(scene, dict):

            self.openrgb_status_label.setText("请选择已保存的场景")

            return

        runtime = self._openrgb_runtime_snapshot()
        known_targets = tuple(self.targets)
        scene_settings = self._scene_lighting_settings(scene)
        self._run_lighting_operation(

            f"正在应用场景：{name}",

            "openrgb_scene_apply_failed",

            lambda: self._apply_scene_from_worker(name, scene_settings, runtime, known_targets),

        )



    def rename_lighting_scene(self) -> None:

        old_name = self._selected_scene_name or self.settings.lighting.active_scene

        new_name = self.scene_combo.currentText().strip()

        if not old_name or old_name not in self.settings.lighting.scenes:

            self.openrgb_status_label.setText("请选择要重命名的场景")

            return

        if not new_name:

            self.openrgb_status_label.setText("请输入新的场景名")

            return

        if new_name != old_name and new_name in self.settings.lighting.scenes:

            self.openrgb_status_label.setText("场景名称已存在")

            return

        self.settings.lighting.scenes[new_name] = self.settings.lighting.scenes.pop(old_name)

        if self.settings.lighting.active_scene == old_name:

            self.settings.lighting.active_scene = new_name

        self._selected_scene_name = new_name

        save_settings(self.settings)
        self._show_lighting_saved_feedback(f"场景已重命名：{new_name}")

        self._refresh_scene_names(new_name)

        self._update_scene_summary()

        self.openrgb_status_label.setText(f"场景已重命名：{new_name}")



    def delete_lighting_scene(self) -> None:

        name = self._selected_scene_name or self.scene_combo.currentText().strip()

        if not name or name not in self.settings.lighting.scenes:

            self.openrgb_status_label.setText("请选择要删除的场景")

            return

        self.settings.lighting.scenes.pop(name, None)

        if self.settings.lighting.active_scene == name:

            self.settings.lighting.active_scene = ""

        next_name = sorted(self.settings.lighting.scenes)[0] if self.settings.lighting.scenes else ""

        self._selected_scene_name = next_name

        save_settings(self.settings)
        self._show_lighting_saved_feedback(f"场景已删除：{name}")

        self._refresh_scene_names(next_name)

        self._update_scene_summary()

        self.openrgb_status_label.setText(f"场景已删除：{name}")



    def identify_argb_targets(self) -> None:

        targets = self._argb_targets()

        if not targets:

            self.openrgb_status_label.setText("未发现 ARGB 区域")

            return

        self._argb_wizard_targets = targets

        self._argb_wizard_restore = [self._settings_from_saved_profile(target.id) for target in targets]

        self._argb_wizard_index = 0

        self.save_next_argb_button.setVisible(True)

        self._select_target_by_id(targets[0].id)
        target = targets[0]
        restore = self._argb_wizard_restore[0]

        self._run_lighting_operation(

            f"正在识别 ARGB 1/{len(targets)}...",

            "openrgb_identify_argb_failed",

            lambda: self._flash_argb_wizard_target_from_worker(target, restore, 0, len(targets)),

        )



    def save_argb_alias_and_continue(self) -> None:

        if not self._argb_wizard_targets or self._argb_wizard_index < 0:

            self.openrgb_status_label.setText("请先启动 ARGB 识别")

            return

        self._save_current_target_alias()

        index = self._argb_wizard_index
        targets = tuple(self._argb_wizard_targets)
        restore = tuple(self._argb_wizard_restore)

        self._run_lighting_operation(

            "正在保存并进入下一个 ARGB 区域...",

            "openrgb_identify_argb_failed",

            lambda: self._advance_argb_wizard_from_worker(index, targets, restore),

        )



    def refresh_openrgb_targets(self) -> None:
        runtime = self._openrgb_runtime_snapshot()

        self._run_lighting_operation(

            "正在刷新 OpenRGB 设备...",

            "openrgb_refresh_failed",

            lambda: self._refresh_targets_from_worker(runtime),

        )



    def identify_selected_target(self) -> None:

        target_id = str(self.lighting_target_combo.currentData() or "")

        if not target_id:

            self.openrgb_status_label.setText("请先选择灯光目标")

            return

        restore_settings = self._current_lighting_settings(target_id)

        self._run_lighting_operation(

            "正在闪烁识别区域...",

            "openrgb_identify_failed",

            lambda: self._identify_from_worker(restore_settings),

        )



    def apply_lighting(self) -> None:

        self._prepare_visible_lighting_inputs()

        target_id = str(self.lighting_target_combo.currentData() or "")

        apply_all = self._apply_all_lighting_enabled()

        try:

            settings = self._current_lighting_settings(target_id)
            snapshot = self._current_lighting_ui_snapshot(target_id)

        except Exception as error:

            self.openrgb_status_label.setText(f"参数错误：{error}")

            return

        if not getattr(self.controller, "connected", False):

            runtime = self._openrgb_runtime_snapshot()

            self._run_lighting_operation(

                "正在连接 OpenRGB 并应用灯效...",

                "openrgb_connect_apply_failed",

                lambda: self._connect_and_apply_from_worker(settings, apply_all, snapshot, runtime),

            )

            return

        if not target_id:

            self.openrgb_status_label.setText("请先选择灯光目标")

            return

        runtime = self._openrgb_runtime_snapshot()
        self._run_lighting_operation(

            "正在应用灯效...",

            "openrgb_apply_failed",

            lambda: self._apply_from_worker(settings, apply_all, snapshot, runtime),

        )



    def turn_off_all_lighting(self) -> None:

        self._set_effect("off")

        self.set_selected_color("#000000")

        self.brightness_slider.setValue(0)

        self.sync_mode_combo.setCurrentIndex(0)
        runtime = self._openrgb_runtime_snapshot()
        zone_size = self.argb_zone_size.value()

        self._run_lighting_operation(

            "正在关闭所有灯光...",

            "openrgb_turn_off_all_failed",

            lambda: self._turn_off_all_lighting_from_worker(runtime, zone_size),

        )



    def _run_lighting_operation(self, busy_message: str, error_event: str, operation: Callable[[], str]) -> None:

        hardware_operation = HardwareOperation(busy_message, error_event, operation)
        pending_count = self._lighting_operation_queue.submit(hardware_operation)
        if pending_count:
            self.openrgb_status_label.setText(f"已加入队列：{pending_count} 个等待")
            self.status_changed.emit(self.home_status_text())
            return

        self._start_lighting_operation(hardware_operation)


    def _start_lighting_operation(self, hardware_operation: HardwareOperation) -> None:

        self._set_lighting_busy(True, self._queued_busy_message(hardware_operation.busy_message))



        def worker() -> None:

            try:

                message = hardware_operation.operation()

            except Exception as error:  # pragma: no cover - delivered through signal

                log_exception(hardware_operation.error_event, error)

                if not self._lighting_closed:

                    self.operation_finished.emit("", error)

                return

            if not self._lighting_closed:

                self.operation_finished.emit(message, None)



        thread = threading.Thread(target=worker, name="usb9-lcd-openrgb", daemon=True)

        self._lighting_threads.append(thread)

        thread.start()



    def _ensure_openrgb_server_from_worker(self, runtime: _OpenRgbRuntimeSnapshot) -> None:
        if runtime.auto_start_server:
            OpenRgbServerManager(
                runtime.app_path,
                host=runtime.host,
                port=runtime.port,
            ).ensure_running()


    def _connect_from_worker(self, runtime: _OpenRgbRuntimeSnapshot) -> _LightingOperationResult:

        self._ensure_openrgb_server_from_worker(runtime)

        targets = self.controller.connect()

        return _LightingOperationResult(
            f"已连接，发现 {len(targets)} 个目标，默认保持关闭",
            targets=tuple(targets),
            update_target_controls=True,
        )


    def _refresh_targets_from_worker(self, runtime: _OpenRgbRuntimeSnapshot) -> _LightingOperationResult:

        targets = self._refresh_or_connect_openrgb_from_worker(runtime)

        return _LightingOperationResult(f"已刷新，发现 {len(targets)} 个目标", targets=tuple(targets), update_target_controls=True)



    def _set_targets_from_worker(self, targets: list[LightingTarget], prefix: str) -> _LightingOperationResult:

        return _LightingOperationResult(f"{prefix}，发现 {len(targets)} 个目标", targets=tuple(targets), update_target_controls=True)



    def _refresh_or_connect_openrgb_from_worker(self, runtime: _OpenRgbRuntimeSnapshot) -> list[LightingTarget]:

        if not getattr(self.controller, "connected", False):

            self._ensure_openrgb_server_from_worker(runtime)

            targets = self.controller.connect()

        else:

            try:

                targets = self.controller.refresh()

            except Exception:

                disconnect = getattr(self.controller, "disconnect", None)

                if callable(disconnect):

                    disconnect()

                self._ensure_openrgb_server_from_worker(runtime)

                targets = self.controller.connect()

        return targets



    def _settings_with_target(self, settings: LightingSettings, target_id: str) -> LightingSettings:

        return LightingSettings(

            target_id=target_id,

            effect=settings.effect,

            color=settings.color,

            brightness_percent=settings.brightness_percent,

            speed_percent=settings.speed_percent,

            zone_size=settings.zone_size,

            save=settings.save,

            physical_layout=settings.physical_layout,

        )



    def _resolve_lighting_target_id(

        self,

        target_id: str,

        effect: str,

        targets: list[LightingTarget],

    ) -> str:

        if target_id and any(target.id == target_id for target in targets):

            return target_id

        preferred = self._preferred_lighting_target(targets, effect)

        if preferred is None:

            raise ValueError("OpenRGB 未发现可用灯光目标")

        return preferred.id



    def _resolve_lighting_apply_target_ids(

        self,

        settings: LightingSettings,

        targets: list[LightingTarget],

        apply_all: bool,

    ) -> list[str]:

        if not apply_all:

            return [self._resolve_lighting_target_id(settings.target_id, settings.effect, targets)]

        return [target.id for target in self._lighting_broadcast_targets(targets, settings.effect)]



    def _lighting_broadcast_targets(self, targets: list[LightingTarget], effect: str) -> list[LightingTarget]:

        if not targets:

            raise ValueError("OpenRGB 未发现可用灯光目标")

        if effect == "off":

            return [target for target in targets if target.zone_index is None] or list(targets)

        whole_targets = [target for target in targets if target.zone_index is None]

        if whole_targets:

            return whole_targets

        return sorted(

            [target for target in targets if target.zone_index is not None],

            key=lambda target: (target.device_index, target.zone_index or 0),

        )



    def _apply_lighting_settings_to_targets(

        self,

        settings: LightingSettings,

        target_ids: list[str],

    ) -> tuple[list[str], list[str]]:

        applied: list[str] = []

        errors: list[str] = []

        for target_id in target_ids:

            try:

                self.controller.apply(self._settings_with_target(settings, target_id))

            except Exception as error:

                errors.append(f"{target_id}: {error}")

                continue

            applied.append(target_id)

        return applied, errors



    def _remember_lighting_settings_for_targets(
        self,
        target_ids: tuple[str, ...],
        selected_target_id: str,
        snapshot: _LightingUiSnapshot,
    ) -> None:

        if not target_ids:

            return

        self.settings.lighting.target_id = selected_target_id if selected_target_id in target_ids else target_ids[0]

        self.settings.lighting.effect = snapshot.effect

        self.settings.lighting.color = snapshot.color

        self.settings.lighting.brightness_percent = snapshot.brightness_percent

        self.settings.lighting.speed = snapshot.speed

        self.settings.lighting.argb_zone_size = snapshot.argb_zone_size

        self.settings.lighting.save_mode = snapshot.save_mode

        self.settings.lighting.sync_mode = snapshot.sync_mode

        self.settings.lighting.temperature_limit = snapshot.temperature_limit

        self.settings.lighting.palette = snapshot.palette

        profile = snapshot.profile()

        for target_id in target_ids:

            self.settings.lighting.target_profiles[target_id] = dict(profile)

        save_settings(self.settings)



    def _lighting_apply_message(self, applied: list[str], errors: list[str], *, connected: bool = False) -> str:

        prefix = "已连接并应用灯效" if connected else "灯效已应用"

        if errors:

            return f"{prefix}：{len(applied)} 个目标，失败 {len(errors)} 个"

        if len(applied) <= 1:

            return prefix

        return f"{prefix}：{len(applied)} 个目标"



    def _apply_from_worker(
        self,
        settings: LightingSettings,
        apply_all: bool,
        snapshot: _LightingUiSnapshot,
        runtime: _OpenRgbRuntimeSnapshot,
    ) -> _LightingOperationResult:

        targets = self._refresh_or_connect_openrgb_from_worker(runtime)

        target_ids = self._resolve_lighting_apply_target_ids(settings, targets, apply_all)

        applied, errors = self._apply_lighting_settings_to_targets(settings, target_ids)

        if not applied:

            raise RuntimeError("; ".join(errors) or "OpenRGB 没有成功应用任何目标")

        return _LightingOperationResult(
            self._lighting_apply_message(applied, errors),
            targets=tuple(targets),
            apply_commit=_LightingApplyCommit(tuple(applied), settings.target_id, snapshot),
        )



    def _turn_off_all_lighting_from_worker(
        self,
        runtime: _OpenRgbRuntimeSnapshot,
        zone_size: int,
    ) -> _LightingOperationResult:

        self._ensure_openrgb_server_from_worker(runtime)

        targets = self.controller.refresh() if self.controller.connected else self.controller.connect()

        if not targets:

            return _LightingOperationResult("OpenRGB 未发现灯光设备", targets=tuple(), update_target_controls=True)

        targets_to_disable = [target for target in targets if target.zone_index is None] or targets

        for target in targets_to_disable:

            self.controller.apply(

                LightingSettings(

                    target_id=target.id,

                    effect="off",

                    color="#000000",

                    brightness_percent=0,

                    speed_percent=0,

                    zone_size=zone_size,

                    save=False,

                )

            )

        return _LightingOperationResult(
            f"所有灯光已关闭：{len(targets_to_disable)} 个目标",
            targets=tuple(targets),
            update_target_controls=True,
        )


    def _openrgb_test_static_red_from_worker(
        self,
        runtime: _OpenRgbRuntimeSnapshot,
        zone_size: int,
    ) -> _LightingOperationResult:

        targets = self._refresh_or_connect_openrgb_from_worker(runtime)

        whole_targets = [target for target in targets if target.zone_index is None] or targets

        for target in whole_targets:

            self.controller.apply(

                LightingSettings(

                    target_id=target.id,

                    effect="static",

                    color="#ff0000",

                    brightness_percent=100,

                    speed_percent=50,

                    zone_size=zone_size,

                    save=False,

                )

            )

        return _LightingOperationResult(f"OpenRGB 测试完成：稳定静态红 {len(whole_targets)} 个目标", targets=tuple(targets))


    def _openrgb_test_single_target_red_from_worker(
        self,
        target_id: str,
        runtime: _OpenRgbRuntimeSnapshot,
        zone_size: int,
    ) -> _LightingOperationResult:

        targets = self._refresh_or_connect_openrgb_from_worker(runtime)

        if not any(target.id == target_id for target in targets):

            raise ValueError("测试目标不存在，请先刷新 OpenRGB")

        self.controller.apply(

            LightingSettings(

                target_id=target_id,

                effect="static",

                color="#ff0000",

                brightness_percent=100,

                speed_percent=50,

                zone_size=zone_size,

                save=False,

            )

        )

        return _LightingOperationResult(f"OpenRGB 测试完成：{target_id} 静态红", targets=tuple(targets))



    def _connect_and_apply_from_worker(
        self,
        settings: LightingSettings,
        apply_all: bool,
        snapshot: _LightingUiSnapshot,
        runtime: _OpenRgbRuntimeSnapshot,
    ) -> _LightingOperationResult:

        targets = self._refresh_or_connect_openrgb_from_worker(runtime)

        if not targets:

            return _LightingOperationResult("OpenRGB 未发现灯光设备", targets=tuple(), update_target_controls=True)

        target_ids = self._resolve_lighting_apply_target_ids(settings, targets, apply_all)

        applied, errors = self._apply_lighting_settings_to_targets(settings, target_ids)

        if not applied:

            raise RuntimeError("; ".join(errors) or "OpenRGB 没有成功应用任何目标")

        message = self._lighting_apply_message(applied, errors, connected=True)

        return _LightingOperationResult(
            f"{message}，发现 {len(targets)} 个目标",
            targets=tuple(targets),
            update_target_controls=True,
            apply_commit=_LightingApplyCommit(tuple(applied), settings.target_id, snapshot),
        )



    def _restore_last_lighting_from_worker(self, targets: list[LightingTarget]) -> bool:

        remembered = self.settings.lighting.target_id

        if not remembered or all(target.id != remembered for target in targets):

            return False

        settings = self._settings_from_saved_profile(remembered)

        self.controller.apply(settings)

        return True



    def _restore_active_scene_from_worker(self, targets: list[LightingTarget]) -> bool:

        name = self.settings.lighting.active_scene

        scene = self.settings.lighting.scenes.get(name)

        if not isinstance(scene, dict):

            return False

        return self._apply_scene_payload(self._scene_lighting_settings(scene), targets) > 0



    def _scene_lighting_settings(self, scene: dict) -> tuple[LightingSettings, ...]:
        profiles = scene.get("targets")
        if not isinstance(profiles, dict):
            return ()
        return tuple(
            self._settings_from_profile(str(target_id), profile)
            for target_id, profile in profiles.items()
            if isinstance(profile, dict)
        )


    def _apply_scene_from_worker(
        self,
        name: str,
        scene_settings: tuple[LightingSettings, ...],
        runtime: _OpenRgbRuntimeSnapshot,
        known_targets: tuple[LightingTarget, ...],
    ) -> _LightingOperationResult:

        if not self.controller.connected:

            self._ensure_openrgb_server_from_worker(runtime)

            targets = self.controller.connect()

        else:

            targets = list(known_targets) or self.controller.refresh()

        applied_count = self._apply_scene_payload(scene_settings, targets)

        if not applied_count:

            return _LightingOperationResult(f"场景没有匹配当前 OpenRGB 目标：{name}", targets=tuple(targets))

        return _LightingOperationResult(
            f"场景已应用：{name}，{applied_count} 个区域",
            targets=tuple(targets),
            active_scene=name,
        )



    def _apply_scene_payload(self, scene_settings: tuple[LightingSettings, ...], targets: list[LightingTarget]) -> int:

        target_ids = {target.id for target in targets}

        applied_count = 0

        for settings in scene_settings:

            if settings.target_id not in target_ids:

                continue

            self.controller.apply(settings)

            applied_count += 1

        return applied_count



    def _identify_from_worker(self, restore_settings: LightingSettings) -> str:

        target_id = restore_settings.target_id

        zone_size = restore_settings.zone_size

        self.controller.apply(

            LightingSettings(target_id=target_id, effect="static", color="#ff0000", brightness_percent=100, speed_percent=50, zone_size=zone_size)

        )

        time.sleep(0.35)

        self.controller.apply(

            LightingSettings(target_id=target_id, effect="static", color="#00ff00", brightness_percent=100, speed_percent=50, zone_size=zone_size)

        )

        time.sleep(0.35)

        self.controller.apply(

            LightingSettings(target_id=target_id, effect="static", color="#0000ff", brightness_percent=100, speed_percent=50, zone_size=zone_size)

        )

        time.sleep(0.35)

        self.controller.apply(restore_settings)

        return "区域识别完成"



    def _flash_argb_wizard_target_from_worker(
        self,
        target: LightingTarget,
        restore: LightingSettings,
        index: int,
        total: int,
    ) -> _LightingOperationResult:


        self.controller.apply(

            LightingSettings(

                target_id=target.id,

                effect="static",

                color="#ff0000",

                brightness_percent=100,

                speed_percent=50,

                zone_size=restore.zone_size,

            )

        )

        return _LightingOperationResult(f"ARGB 向导：当前 {index + 1}/{total}，输入名称后点保存并下一步")



    def _advance_argb_wizard_from_worker(
        self,
        index: int,
        targets: tuple[LightingTarget, ...],
        restore_settings: tuple[LightingSettings, ...],
    ) -> _LightingOperationResult:

        if index >= len(restore_settings):

            return _LightingOperationResult("ARGB 命名完成", argb_next_index=-1)

        self.controller.apply(restore_settings[index])

        next_index = index + 1

        if next_index >= len(targets):

            return _LightingOperationResult("ARGB 命名完成", argb_next_index=-1)

        target = targets[next_index]

        restore = restore_settings[next_index]

        self.controller.apply(

            LightingSettings(

                target_id=target.id,

                effect="static",

                color="#ff0000",

                brightness_percent=100,

                speed_percent=50,

                zone_size=restore.zone_size,

            )

        )

        return _LightingOperationResult(
            f"ARGB 向导：当前 {next_index + 1}/{len(targets)}，输入名称后点保存并下一步",
            argb_next_index=next_index,
        )



    def _lighting_operation_finished(self, result: object, error: object) -> None:

        next_operation = self._lighting_operation_queue.complete_current()

        if error is not None:

            self.openrgb_status_label.setText(f"操作失败：{error}")

            self.status_changed.emit(self.home_status_text())
            if next_operation is None:

                self._set_lighting_busy(False, "")

            else:

                self._start_lighting_operation(next_operation)

            return

        operation_result = result if isinstance(result, _LightingOperationResult) else _LightingOperationResult(str(result))
        message = operation_result.message

        if operation_result.targets is not None:

            self.targets = list(operation_result.targets)

        if operation_result.update_target_controls or message.startswith(("已连接", "已刷新", "场景已应用", "所有灯光已关闭")):

            self._set_targets(list(operation_result.targets or self.targets))

        if operation_result.apply_commit is not None:

            commit = operation_result.apply_commit

            self._remember_lighting_settings_for_targets(commit.target_ids, commit.selected_target_id, commit.snapshot)

        if operation_result.active_scene is not None:

            self.settings.lighting.active_scene = operation_result.active_scene

            self._selected_scene_name = operation_result.active_scene

            save_settings(self.settings)

        if operation_result.argb_next_index is not None:

            self._argb_wizard_index = operation_result.argb_next_index

        if message.startswith(("灯效已应用", "已连接并应用灯效")):

            self._show_lighting_saved_feedback("灯效设置已保存")

        if self._openrgb_test_dialog is not None and self._openrgb_test_dialog.isVisible():

            self._openrgb_test_dialog.refresh_targets()

        if message.startswith("ARGB 向导："):

            self._sync_argb_wizard_ui()

        if message == "ARGB 命名完成":

            self.save_next_argb_button.setVisible(False)

            self._argb_wizard_targets = []

            self._argb_wizard_restore = []

        if next_operation is None:

            self._set_lighting_busy(False, "")

            self.openrgb_status_label.setText(message)

        else:

            self.openrgb_status_label.setText(message)

            self._start_lighting_operation(next_operation)

        self.status_changed.emit(self.home_status_text())


    def _show_lighting_saved_feedback(self, message: str) -> None:

        if hasattr(self, "lighting_save_status_label"):

            self.lighting_save_status_label.setText(f"{message}（{time.strftime('%H:%M:%S')}）")


    def _queued_busy_message(self, message: str) -> str:

        pending_count = self._lighting_operation_queue.pending_count

        if pending_count <= 0:

            return message

        return f"{message}（队列剩余 {pending_count} 个）"



    def _set_lighting_busy(self, active: bool, message: str) -> None:

        self._lighting_operation_active = active

        for widget in (

            self.connect_lighting_button,

            self.refresh_lighting_button,

            self.identify_lighting_button,

            self.openrgb_test_button,

            self.apply_lighting_button,

            self.save_target_profile_button,

            self.load_target_profile_button,

            self.identify_argb_button,

            self.save_next_argb_button,

            self.save_scene_button,

            self.apply_scene_button,

            self.rename_scene_button,

            self.delete_scene_button,

            self.apply_sync_lighting_button,

            self.apply_all_lighting_checkbox,

            self.lighting_target_combo,

            self.scene_combo,

            self.openrgb_host_input,

            self.openrgb_port_input,

        ):

            widget.setEnabled(not active)

        if active:

            self.openrgb_status_label.setText(message)

        self.status_changed.emit(self.home_status_text())



    def home_status_text(self) -> str:

        if self._lighting_operation_active:

            return "执行中"

        connected = bool(getattr(self.controller, "connected", False))

        if not connected and not self.targets:

            return "默认关闭"

        target_count = len(self.targets)

        effect = self._selected_effect()

        brightness = self.brightness_slider.value() if hasattr(self, "brightness_slider") else 0

        if effect == "off" or brightness <= 0:

            state = "关闭"

        else:

            state = f"{self._effect_label(effect)} {brightness}%"

        prefix = f"已连接 {target_count}" if connected else f"未连接 {target_count}"

        return f"{prefix}\n{state}"



    def _effect_label(self, effect: str) -> str:
        return effect_label(effect)



    def _apply_lighting_sync_for_tests(self, settings: LightingSettings) -> None:

        try:

            self.controller.apply(settings)

        except Exception as error:

            log_exception("openrgb_apply_failed", error)

            self.openrgb_status_label.setText(f"应用失败：{error}")

            return

        self.openrgb_status_label.setText("灯效已应用")



    def _prepare_visible_lighting_inputs(self) -> None:

        effect = self._selected_effect()

        if effect == "off":

            return

        if self.brightness_slider.value() <= 0:

            self.brightness_slider.setValue(80)

        color = self.selected_color_input.text().strip()

        if not self._lighting_color_has_output(color):

            self.set_selected_color(self._lighting_palette_colors()[-1])



    def _apply_all_lighting_enabled(self) -> bool:

        return bool(

            hasattr(self, "apply_all_lighting_checkbox")

            and self.apply_all_lighting_checkbox.isChecked()

        )


    def _update_lighting_apply_preview(self, *args) -> None:  # noqa: ANN002

        if not hasattr(self, "lighting_apply_preview_text"):

            return

        self.lighting_apply_preview_text.setPlainText(self._lighting_apply_preview_text())


    def _lighting_apply_preview_text(self) -> str:

        target_id = str(self.lighting_target_combo.currentData() or "") if hasattr(self, "lighting_target_combo") else ""

        settings = self._current_lighting_settings(target_id)

        apply_all = self._apply_all_lighting_enabled()

        try:

            target_ids = self._resolve_lighting_apply_target_ids(settings, self.targets, apply_all) if self.targets else []

        except ValueError:

            target_ids = []

        target_id_set = set(target_ids)

        target_names = [

            self._target_display_name(target)

            for target in self.targets

            if target.id in target_id_set

        ]

        if not self.targets:

            scope = "连接后自动扫描"

            target_line = "目标：连接后自动选择"

        elif apply_all:

            scope = "同步到所有 OpenRGB 设备"

            target_line = f"目标：{len(target_ids)} 个"

        else:

            scope = "只写入当前目标"

            target_line = f"目标：{target_names[0] if target_names else target_id or '未选择'}"

        if target_names and apply_all:

            preview_names = "，".join(target_names[:3])

            if len(target_names) > 3:

                preview_names += f" 等 {len(target_names)} 个"

            target_line += f"（{preview_names}）"

        effect = settings.effect

        lines = [

            "应用预览：",

            f"范围：{scope}",

            target_line,

            f"灯效：{self._effect_label(effect)} / {self._lighting_effect_preview_note(effect, settings.brightness_percent)}",

            f"颜色：{settings.color} / 亮度 {settings.brightness_percent}% / 速度 {settings.speed_percent}%",

            f"灯珠数量：{settings.zone_size or '--'} / 保存到设备：{'是' if settings.save else '否'}",

        ]

        return "\n".join(lines)


    def _lighting_effect_preview_note(self, effect: str, brightness_percent: int) -> str:

        if effect == "off" or brightness_percent <= 0:

            return "关闭/黑色写入"

        if effect == "static":

            return "静态单色稳定写入"

        if effect in {"star", "meteor", "comet", "scan", "visor", "matrix", "gradient", "chase"}:

            return "软件帧循环"

        return "OpenRGB 模式优先"



    @staticmethod

    def _lighting_color_has_output(color: str) -> bool:

        qcolor = QColor(color)

        if not qcolor.isValid():

            return False

        return qcolor.red() > 0 or qcolor.green() > 0 or qcolor.blue() > 0



    def _current_lighting_settings(self, target_id: str) -> LightingSettings:

        color = self.selected_color_input.text().strip()

        if self.sync_mode_combo.currentIndex() and self.sync_color_provider is not None:

            color = self.sync_color_provider(self.sync_mode_combo.currentIndex()) or color

        return LightingSettings(

            target_id=target_id,

            effect=self._selected_effect(),

            color=color,

            brightness_percent=self.brightness_slider.value(),

            speed_percent=self.speed_slider.value() * 10,

            zone_size=self.argb_zone_size.value(),

            save=self.save_mode_checkbox.isChecked(),

            physical_layout=self._current_physical_layout(),

        )



    def _lighting_palette_key(self) -> str:

        key = str(self.lighting_palette_combo.currentData() or self.settings.lighting.palette or "neon")

        return key if key in LIGHTING_PALETTES else "neon"



    def _lighting_palette_colors(self) -> tuple[str, ...]:

        return LIGHTING_PALETTES[self._lighting_palette_key()][1]



    def _lighting_palette_changed(self) -> None:

        self.settings.lighting.palette = self._lighting_palette_key()

        self._update_lighting_swatches()

        save_settings(self.settings)
        self._show_lighting_saved_feedback(f"灯效调色板已保存：{self.settings.lighting.palette}")



    def _update_lighting_swatches(self) -> None:

        current_color = self.selected_color

        if hasattr(self, "selected_color_input"):

            current_color = self.selected_color_input.text().strip()

        current_qcolor = QColor(current_color)

        for button, color in zip(self.lighting_swatch_buttons, self._lighting_palette_colors(), strict=False):

            selected = current_qcolor.isValid() and QColor(color).name().lower() == current_qcolor.name().lower()

            border = "#f2f3f0" if selected else "#62686c"

            button.setStyleSheet(f"background: {color}; border: 2px solid {border};")

            button.setToolTip(color)

        self._update_lighting_color_preview()



    def _update_lighting_color_preview(self) -> None:

        if not hasattr(self, "selected_color_preview"):

            return

        color = self.selected_color_input.text().strip()

        qcolor = QColor(color)

        if not qcolor.isValid():

            color = "#000000"

        self.selected_color_preview.setStyleSheet(

            f"background: {color}; border: 1px solid #555b5f; border-radius: 6px;"

        )

        self.selected_color_preview.setToolTip(color)
        self._update_lighting_apply_preview()



    def _update_lighting_slider_labels(self) -> None:

        if hasattr(self, "brightness_value_label"):

            self.brightness_value_label.setText(f"{self.brightness_slider.value()}%")

        if hasattr(self, "speed_value_label"):

            self.speed_value_label.setText(f"{self.speed_slider.value() * 10}%")
        self._update_lighting_apply_preview()



    def choose_lighting_color(self) -> None:

        initial = QColor(self.selected_color_input.text().strip())

        if not initial.isValid():

            initial = QColor(self.selected_color)

        color = QColorDialog.getColor(initial, self, "选择灯效颜色")

        if color.isValid():

            self.set_selected_color(color.name())



    def _settings_from_saved_profile(self, target_id: str) -> LightingSettings:

        profile = self.settings.lighting.target_profiles.get(target_id, {})

        if not isinstance(profile, dict):

            profile = {}

        return self._settings_from_profile(target_id, profile)



    def _settings_from_profile(self, target_id: str, profile: dict) -> LightingSettings:

        color = str(profile.get("color", self.settings.lighting.color))

        sync_mode = int(profile.get("sync_mode", self.settings.lighting.sync_mode))

        if sync_mode and self.sync_color_provider is not None:

            color = self.sync_color_provider(sync_mode) or color

        return LightingSettings(

            target_id=target_id,

            effect=str(profile.get("effect", self.settings.lighting.effect)),

            color=color,

            brightness_percent=int(profile.get("brightness_percent", self.settings.lighting.brightness_percent)),

            speed_percent=int(profile.get("speed", self.settings.lighting.speed)) * 10,

            zone_size=int(profile.get("argb_zone_size", self.settings.lighting.argb_zone_size)),

            save=bool(profile.get("save_mode", self.settings.lighting.save_mode)),

            physical_layout=self._current_physical_layout(),

        )



    def set_selected_color(self, color: str) -> None:

        self.selected_color = color

        self.selected_color_input.setText(color)

        self._update_lighting_swatches()

        self._update_lighting_color_preview()



    def _selected_color_text_changed(self, color: str) -> None:

        if QColor(color).isValid():

            self.selected_color = color

            self._update_lighting_swatches()

            self._update_lighting_color_preview()



    def _selected_effect(self) -> str:

        checked = self.effect_group.checkedButton()

        if checked is None:

            return "static"

        return self.effect_map.get(checked.text(), "static")



    def _set_effect(self, effect: str) -> None:

        for button in self.effect_group.buttons():

            if self.effect_map.get(button.text()) == effect:

                button.setChecked(True)
                self._update_lighting_apply_preview()

                return



    def _select_whole_device_target(self) -> None:

        for index in range(self.lighting_target_combo.count()):

            target_id = str(self.lighting_target_combo.itemData(index) or "")

            target = next((item for item in self.targets if item.id == target_id), None)

            if target is not None and target.zone_index is None:

                self.lighting_target_combo.setCurrentIndex(index)

                return



    def _select_target_by_id(self, target_id: str) -> None:

        for index in range(self.lighting_target_combo.count()):

            if self.lighting_target_combo.itemData(index) == target_id:

                self.lighting_target_combo.setCurrentIndex(index)

                return



    def _argb_targets(self) -> list[LightingTarget]:

        candidates = [

            target

            for target in self.targets

            if target.zone_index is not None and self._lighting_target_score(target) > 10

        ]

        if candidates:

            return sorted(candidates, key=self._lighting_target_score, reverse=True)

        return sorted(

            [target for target in self.targets if target.zone_index is not None],

            key=self._lighting_target_score,

            reverse=True,

        )



    def _preferred_lighting_target(self, targets: list[LightingTarget], effect: str = "") -> LightingTarget | None:

        if not targets:

            return None

        whole_targets = [target for target in targets if target.zone_index is None]

        if effect == "off":

            return whole_targets[0] if whole_targets else targets[0]

        zone_targets = [target for target in targets if target.zone_index is not None]

        if zone_targets:

            return max(zone_targets, key=self._lighting_target_score)

        return whole_targets[0] if whole_targets else targets[0]



    @staticmethod

    def _lighting_target_score(target: LightingTarget) -> int:

        if target.zone_index is None:

            return -100

        name = target.name.lower()

        score = 10

        for keyword, weight in (

            ("argb", 50),

            ("addressable", 50),

            ("jrainbow", 45),

            ("d_led", 45),

            ("d-led", 45),

            ("led_c", 45),

            ("rgb header", 40),

            ("header", 25),

            ("fan", 20),

            ("strip", 15),

            ("motherboard", 8),

            ("mainboard", 8),

            ("aura", 8),

        ):

            if keyword in name:

                score += weight

        return score



    def _refresh_scene_names(self, active: str = "") -> None:

        current = active or self.scene_combo.currentText().strip()

        self.scene_combo.blockSignals(True)

        self.scene_combo.clear()

        self.scene_combo.addItems(sorted(self.settings.lighting.scenes))

        if current:

            self.scene_combo.setCurrentText(current)

        self.scene_combo.blockSignals(False)

        self._selected_scene_name = current



    def _scene_selection_changed(self, index: int) -> None:

        if index >= 0:

            self._selected_scene_name = self.scene_combo.itemText(index)

        self._update_scene_summary()



    def _update_scene_summary(self) -> None:

        name = self._selected_scene_name or self.scene_combo.currentText().strip()

        scene = self.settings.lighting.scenes.get(name)

        if not isinstance(scene, dict):

            self.scene_summary_text.setPlainText("未选择场景")

            return

        profiles = scene.get("targets")

        if not isinstance(profiles, dict) or not profiles:

            self.scene_summary_text.setPlainText("场景为空")

            return

        lines = []

        for target_id in profiles:

            target = next((item for item in self.targets if item.id == target_id), None)

            label = self.settings.lighting.target_aliases.get(str(target_id))

            if not label and target is not None:

                label = target.name

            lines.append(label or str(target_id))

        self.scene_summary_text.setPlainText("包含区域：\n" + "\n".join(lines))



    def _sync_argb_wizard_ui(self) -> None:

        if self._argb_wizard_index < 0 or self._argb_wizard_index >= len(self._argb_wizard_targets):

            return

        target = self._argb_wizard_targets[self._argb_wizard_index]

        self._select_target_by_id(target.id)



    def _set_targets(self, targets: list[LightingTarget]) -> None:

        self.targets = targets
        if self._remember_openrgb_device_profiles(targets):
            save_settings(self.settings)

        self.lighting_target_combo.clear()

        if not targets:

            self.lighting_target_combo.addItem("OpenRGB 未发现灯光设备", "")

            self.openrgb_modes_text.setPlainText(render_lighting_target_partition_summary([]))

            self._update_scene_summary()
            self._update_lighting_apply_preview()

            return

        for target in targets:

            self.lighting_target_combo.addItem(self._target_display_name(target), target.id)

        remembered = self.settings.lighting.target_id
        target_ids = {target.id for target in targets}
        preferred = remembered if remembered in target_ids else ""
        if not preferred:
            default_target = self._preferred_lighting_target(targets)
            preferred = default_target.id if default_target is not None else ""

        if preferred:

            for index in range(self.lighting_target_combo.count()):

                if self.lighting_target_combo.itemData(index) == preferred:

                    self.lighting_target_combo.setCurrentIndex(index)

                    break

        self._target_changed(self.lighting_target_combo.currentIndex())

        self._update_scene_summary()
        self._update_lighting_apply_preview()


    def _remember_openrgb_device_profiles(self, targets: list[LightingTarget]) -> bool:

        changed = False

        for target in targets:

            if target.zone_index is not None:

                continue

            device_name = target.name.split(" / ", 1)[0]

            payload = openrgb_device_profile_payload(device_name, target.id)

            if self.settings.lighting.device_profiles.get(target.id) != payload:

                self.settings.lighting.device_profiles[target.id] = payload

                changed = True

        return changed



    def _target_changed(self, index: int) -> None:

        target_id = str(self.lighting_target_combo.itemData(index) or "")

        target = next((item for item in self.targets if item.id == target_id), None)

        if target is None:

            self.openrgb_modes_text.setPlainText(render_lighting_target_partition_summary(self.targets))

            self.target_alias_input.setText("")

            self._restore_layout_controls("")
            self._update_lighting_apply_preview()

            return

        self.target_alias_input.setText(self.settings.lighting.target_aliases.get(target.id, ""))

        self._restore_layout_controls(target.id)

        profile = self.settings.lighting.device_profiles.get(f"device:{target.device_index}")

        profile_lines = []

        if isinstance(profile, dict):

            profile_lines = [

                "",

                f"设备画像：{profile.get('label', profile.get('key', 'default'))}",

                f"静态策略：{profile.get('static_strategy', 'standard')}",

            ]

        lines = [
            "当前目标：",
            f"{self._target_display_name(target)} ({target.id})",
            "",
            "可用模式：",
            *target.modes,
            *profile_lines,
            "",
            render_lighting_target_partition_summary(self.targets),
        ]
        self.openrgb_modes_text.setPlainText("\n".join(lines))
        self._update_lighting_apply_preview()


    def _current_physical_layout(self) -> LightingPhysicalLayout:

        return LightingPhysicalLayout.from_mapping(self.settings.lighting.physical_layout)


    def _restore_layout_controls(self, target_id: str) -> None:

        payload = self.settings.lighting.physical_layout.get(target_id, {}) if target_id else {}

        if not isinstance(payload, dict):

            payload = {}

        self.layout_order_spin.setValue(int(payload.get("order", 0) or 0))

        self.layout_led_count_spin.setValue(int(payload.get("led_count", 0) or 0))

        direction = str(payload.get("direction", "forward"))

        self.layout_direction_combo.setCurrentIndex(max(0, self.layout_direction_combo.findData(direction)))

        self.layout_port_input.setText(str(payload.get("port_label", "")))


    def save_lighting_layout(self) -> None:

        target_id = str(self.lighting_target_combo.currentData() or "")

        if not target_id:

            self.openrgb_status_label.setText("请先选择灯光目标")

            return

        self.settings.lighting.physical_layout[target_id] = {
            "order": self.layout_order_spin.value(),
            "led_count": self.layout_led_count_spin.value(),
            "direction": str(self.layout_direction_combo.currentData() or "forward"),
            "port_label": self.layout_port_input.text().strip(),
        }

        save_settings(self.settings)

        self.lighting_save_status_label.setText(f"物理布局已保存：{target_id}")

        self.openrgb_status_label.setText("物理布局已保存")

        self._update_lighting_apply_preview()



    def _restore_lighting_settings(self, settings: LightingUiSettings) -> None:

        self.lighting_palette_combo.setCurrentIndex(max(0, self.lighting_palette_combo.findData(settings.palette)))

        self._update_lighting_swatches()

        self.selected_color_input.setText("#000000")

        self.brightness_slider.setValue(0)

        self.speed_slider.setValue(settings.speed)

        self.argb_zone_size.setValue(settings.argb_zone_size)

        self.save_mode_checkbox.setChecked(settings.save_mode)

        self.sync_mode_combo.setCurrentIndex(0)

        self.temperature_limit.setValue(settings.temperature_limit)

        self._set_effect("off")



    def _remember_lighting_settings(self, target_id: str) -> None:

        self.settings.lighting.target_id = target_id

        self.settings.lighting.effect = self._selected_effect()

        self.settings.lighting.color = self.selected_color_input.text().strip()

        self.settings.lighting.brightness_percent = self.brightness_slider.value()

        self.settings.lighting.speed = self.speed_slider.value()

        self.settings.lighting.argb_zone_size = self.argb_zone_size.value()

        self.settings.lighting.save_mode = self.save_mode_checkbox.isChecked()

        self.settings.lighting.sync_mode = self.sync_mode_combo.currentIndex()

        self.settings.lighting.temperature_limit = self.temperature_limit.value()

        self.settings.lighting.palette = self._lighting_palette_key()

        self.settings.lighting.target_profiles[target_id] = {

            "effect": self.settings.lighting.effect,

            "color": self.settings.lighting.color,

            "brightness_percent": self.settings.lighting.brightness_percent,

            "speed": self.settings.lighting.speed,

            "argb_zone_size": self.settings.lighting.argb_zone_size,

            "save_mode": self.settings.lighting.save_mode,

            "sync_mode": self.settings.lighting.sync_mode,

            "temperature_limit": self.settings.lighting.temperature_limit,

            "palette": self.settings.lighting.palette,

        }

        save_settings(self.settings)



    def _restore_target_profile(self, target_id: str) -> bool:

        profile = self.settings.lighting.target_profiles.get(target_id)

        if not isinstance(profile, dict):

            return False

        self.selected_color_input.setText(str(profile.get("color", self.settings.lighting.color)))

        self.brightness_slider.setValue(int(profile.get("brightness_percent", self.settings.lighting.brightness_percent)))

        self.speed_slider.setValue(int(profile.get("speed", self.settings.lighting.speed)))

        self.argb_zone_size.setValue(int(profile.get("argb_zone_size", self.settings.lighting.argb_zone_size)))

        self.save_mode_checkbox.setChecked(bool(profile.get("save_mode", self.settings.lighting.save_mode)))

        self.sync_mode_combo.setCurrentIndex(int(profile.get("sync_mode", self.settings.lighting.sync_mode)))

        self.temperature_limit.setValue(int(profile.get("temperature_limit", self.settings.lighting.temperature_limit)))

        palette = str(profile.get("palette", self.settings.lighting.palette))

        self.lighting_palette_combo.setCurrentIndex(max(0, self.lighting_palette_combo.findData(palette)))

        self._update_lighting_swatches()

        self._set_effect(str(profile.get("effect", self.settings.lighting.effect)))

        return True



    def _save_current_target_alias(self) -> None:

        target_id = str(self.lighting_target_combo.currentData() or "")

        if not target_id:

            return

        alias = self.target_alias_input.text().strip()

        if alias:

            self.settings.lighting.target_aliases[target_id] = alias

        else:

            self.settings.lighting.target_aliases.pop(target_id, None)

        save_settings(self.settings)
        self._show_lighting_saved_feedback("区域备注已保存")

        current = self.lighting_target_combo.currentIndex()

        self._set_targets(self.targets)

        self.lighting_target_combo.setCurrentIndex(current)

        self._update_scene_summary()



    def _target_display_name(self, target: LightingTarget) -> str:

        alias = self.settings.lighting.target_aliases.get(target.id, "")

        return f"{alias} 路 {target.name}" if alias else target.name



    def closeEvent(self, event) -> None:  # noqa: ANN001

        self.release_lighting_resources()

        super().closeEvent(event)



    def release_lighting_resources(self) -> None:

        self._lighting_closed = True

        self._lighting_operation_queue.clear()

        for thread in list(self._lighting_threads):

            if thread.is_alive():

                thread.join(timeout=1.0)
