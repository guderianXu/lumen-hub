from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from usb9_lcd.assets import bundled_asset_path
from usb9_lcd.monitoring.models import CpuTelemetry, FanTelemetry, GpuTelemetry, SystemTelemetry
from usb9_lcd.monitoring.render import MonitorRenderSettings


def builtin_monitor_backgrounds() -> tuple[tuple[str, Path], ...]:
    return (
        ("ROG 红色网格", bundled_asset_path("monitor_backgrounds/rog_red_grid.png")),
        ("蓝色核心", bundled_asset_path("monitor_backgrounds/blue_core.png")),
        ("霓虹仪表", bundled_asset_path("monitor_backgrounds/neon_meter.png")),
    )


def format_cpu_temperature(cpu: CpuTelemetry) -> str:

    parts: list[str] = []

    if cpu.package_temperature_c is not None:

        parts.append(f"CPU {cpu.package_temperature_c:.0f}°C")

    if cpu.utilization_percent is not None:

        parts.append(f"Load {cpu.utilization_percent:.0f}%")

    if cpu.power_w is not None:

        parts.append(f"Power {cpu.power_w:.0f}W")

    if parts:

        return "\n".join(parts)

    return "CPU 不可用"





def format_gpu_temperature(gpu: GpuTelemetry) -> str:

    if gpu.available and gpu.temperature_c is not None:

        return f"GPU {gpu.temperature_c}°C"

    return "GPU 不可用"





def format_gpu_detail(gpu: GpuTelemetry) -> str:

    if not gpu.available:

        return gpu.error or "NVIDIA GPU unavailable"



    details = [gpu.name]

    if gpu.utilization_percent is not None:

        details.append(f"{gpu.utilization_percent}%")

    if gpu.power_w is not None:

        details.append(f"{gpu.power_w:.0f}W")

    if gpu.memory_used_mb is not None and gpu.memory_total_mb is not None:

        details.append(f"{gpu.memory_used_mb}/{gpu.memory_total_mb}MB")

    if gpu.graphics_clock_mhz is not None:

        details.append(f"{gpu.graphics_clock_mhz}MHz")

    return " / ".join(details)





def format_fan_detail(fans: list[FanTelemetry]) -> str:

    if not fans:

        return "Fan sensors unavailable"



    available = [fan for fan in fans if fan.available]

    if not available:

        return fans[0].error or "Fan sensors unavailable"



    lines: list[str] = []

    for fan in available[:4]:

        values: list[str] = []

        if fan.rpm is not None:

            values.append(f"{fan.rpm} RPM")

        if fan.percent is not None:

            values.append(f"{fan.percent:.0f}%")

        lines.append(f"{fan.name}: {' / '.join(values) if values else 'active'}")

    if len(available) > 4:

        lines.append(f"+{len(available) - 4} more fan sensors")

    return "\n".join(lines)





class MonitorPage(QWidget):

    def __init__(

        self,

        refresh_telemetry: Callable[[], None],

        upload_monitoring_frame: Callable[[], None] | None = None,

        toggle_live_monitoring: Callable[[], None] | None = None,

    ) -> None:

        super().__init__()

        self._latest_preview: QPixmap | None = None

        self.custom_background_path: Path | None = None

        layout = QVBoxLayout(self)

        layout.setContentsMargins(24, 22, 24, 24)

        layout.setSpacing(16)



        header_row = QHBoxLayout()

        title_box = QVBoxLayout()

        header = QLabel("硬件监控")

        header.setObjectName("PageTitle")

        subtitle = QLabel("实时读取 CPU / NVIDIA GPU 状态，并渲染成适合小屏幕的监控画面")

        subtitle.setObjectName("PageSubtitle")

        title_box.addWidget(header)

        title_box.addWidget(subtitle)

        header_row.addLayout(title_box, 1)

        refresh_button = QPushButton("刷新")

        refresh_button.clicked.connect(refresh_telemetry)

        header_row.addWidget(refresh_button)

        if upload_monitoring_frame is not None:

            self.upload_monitor_button = QPushButton("发送当前监控")

            self.upload_monitor_button.clicked.connect(upload_monitoring_frame)

            header_row.addWidget(self.upload_monitor_button)

        self.monitor_interval_combo = QComboBox()

        for seconds in (1, 2, 5, 10):

            self.monitor_interval_combo.addItem(f"{seconds}s", seconds)

        header_row.addWidget(self.monitor_interval_combo)

        self.live_monitor_button = QPushButton("开始实时监控")

        self.live_monitor_button.setObjectName("PrimaryButton")

        if toggle_live_monitoring is not None:

            self.live_monitor_button.clicked.connect(toggle_live_monitoring)

        header_row.addWidget(self.live_monitor_button)

        layout.addLayout(header_row)



        grid = QGridLayout()

        grid.setSpacing(12)

        self.cpu_temp_value = QLabel("CPU 数据不可用")

        self.gpu_temp_value = QLabel("GPU 数据不可用")

        self.gpu_detail_value = QLabel("NVIDIA GPU 数据不可用")

        self.fan_detail_value = QLabel("风扇传感器不可用")



        grid.addWidget(self._metric_card("CPU", self.cpu_temp_value), 0, 0)

        grid.addWidget(self._metric_card("GPU", self.gpu_temp_value), 0, 1)

        grid.addWidget(self._metric_card("GPU 详情", self.gpu_detail_value), 1, 0, 1, 2)

        grid.addWidget(self._metric_card("风扇传感器", self.fan_detail_value), 2, 0, 1, 2)

        layout.addLayout(grid)



        preview_row = QHBoxLayout()

        preview_row.setSpacing(16)

        self.monitor_preview = QFrame()

        self.monitor_preview.setObjectName("ScreenPreviewCard")

        self.monitor_preview.setMinimumSize(320, 300)

        preview_layout = QVBoxLayout(self.monitor_preview)

        preview_title = QLabel("屏幕预览")

        preview_title.setObjectName("SectionLabel")

        self.preview_body = QLabel("等待监控数据")

        self.preview_body.setObjectName("LcdPreview")

        self.preview_body.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.preview_body.setMinimumSize(240, 240)

        preview_layout.addWidget(preview_title)

        preview_layout.addWidget(self.preview_body, 1)

        preview_row.addWidget(self.monitor_preview, 1)



        profile = QFrame()

        profile.setObjectName("MetricCard")

        profile_layout = QVBoxLayout(profile)

        profile_title = QLabel("画面配置")

        profile_title.setObjectName("SectionLabel")

        profile_layout.addWidget(profile_title)



        self.monitor_background_combo = QComboBox()

        self._add_monitor_backgrounds()

        self.monitor_layout_combo = QComboBox()

        self._add_monitor_layouts()

        self.monitor_palette_combo = QComboBox()

        self._add_monitor_palettes()

        self.monitor_profile_combo = QComboBox()

        self.monitor_profile_combo.setEditable(True)

        self.save_monitor_profile_button = QPushButton("保存配置")

        self.load_monitor_profile_button = QPushButton("加载配置")

        profile_layout.addWidget(QLabel("布局"))

        profile_layout.addWidget(self.monitor_layout_combo)

        profile_layout.addWidget(QLabel("调色板"))

        profile_layout.addWidget(self.monitor_palette_combo)

        profile_layout.addWidget(QLabel("底图"))

        profile_layout.addWidget(self.monitor_background_combo)

        profile_layout.addWidget(QLabel("配置"))

        profile_layout.addWidget(self.monitor_profile_combo)

        profile_button_row = QHBoxLayout()

        profile_button_row.addWidget(self.save_monitor_profile_button)

        profile_button_row.addWidget(self.load_monitor_profile_button)

        profile_layout.addLayout(profile_button_row)

        choose_background = QPushButton("选择自定义底图")

        choose_background.clicked.connect(self.choose_monitor_background)

        profile_layout.addWidget(choose_background)



        self.show_cpu_temp_checkbox = QCheckBox("CPU 温度")

        self.show_cpu_temp_checkbox.setChecked(True)

        self.show_gpu_temp_checkbox = QCheckBox("GPU 温度")

        self.show_gpu_temp_checkbox.setChecked(True)

        self.show_gpu_load_checkbox = QCheckBox("GPU 负载")

        self.show_gpu_load_checkbox.setChecked(True)

        self.show_gpu_power_checkbox = QCheckBox("GPU 功耗")

        self.show_gpu_power_checkbox.setChecked(True)

        self.show_vram_checkbox = QCheckBox("显存")

        self.show_vram_checkbox.setChecked(True)

        self.show_gpu_clock_checkbox = QCheckBox("GPU 频率")

        self.show_gpu_clock_checkbox.setChecked(False)

        self.show_cpu_load_checkbox = QCheckBox("CPU 负载")

        self.show_cpu_load_checkbox.setChecked(True)

        self.show_time_checkbox = QCheckBox("时间")

        self.show_time_checkbox.setChecked(True)



        for checkbox in (

            self.show_cpu_temp_checkbox,

            self.show_gpu_temp_checkbox,

            self.show_gpu_load_checkbox,

            self.show_gpu_power_checkbox,

            self.show_vram_checkbox,

            self.show_gpu_clock_checkbox,

            self.show_cpu_load_checkbox,

            self.show_time_checkbox,

        ):

            profile_layout.addWidget(checkbox)



        profile_layout.addStretch(1)

        preview_row.addWidget(profile, 1)

        layout.addLayout(preview_row, 1)



    def update_telemetry(self, telemetry: SystemTelemetry) -> None:

        self.cpu_temp_value.setText(format_cpu_temperature(telemetry.cpu))

        self.gpu_temp_value.setText(format_gpu_temperature(telemetry.gpu))

        self.gpu_detail_value.setText(format_gpu_detail(telemetry.gpu))

        self.fan_detail_value.setText(format_fan_detail(telemetry.fans))

        self._update_preview_text(telemetry)



    def update_preview_image(self, image) -> None:  # noqa: ANN001

        rgb = image.convert("RGB")

        data = rgb.tobytes()

        qimage = QImage(

            data,

            rgb.width,

            rgb.height,

            rgb.width * 3,

            QImage.Format.Format_RGB888,

        ).copy()

        pixmap = QPixmap.fromImage(qimage).scaled(

            240,

            240,

            Qt.AspectRatioMode.KeepAspectRatio,

            Qt.TransformationMode.SmoothTransformation,

        )

        self.preview_body.clear()

        self.preview_body.setPixmap(pixmap)



    def show_unavailable(self) -> None:

        self.cpu_temp_value.setText("CPU 不可用")

        self.gpu_temp_value.setText("GPU 不可用")

        self.gpu_detail_value.setText("NVIDIA GPU unavailable")

        self.fan_detail_value.setText("Fan sensors unavailable")

        self.preview_body.clear()

        self.preview_body.setText("Monitoring unavailable")



    def _update_preview_text(self, telemetry: SystemTelemetry) -> None:

        cpu = "--" if telemetry.cpu.package_temperature_c is None else f"{telemetry.cpu.package_temperature_c:.0f}"

        cpu_load = "--" if telemetry.cpu.utilization_percent is None else f"{telemetry.cpu.utilization_percent:.0f}"

        gpu = "--" if telemetry.gpu.temperature_c is None else f"{telemetry.gpu.temperature_c:.0f}"

        gpu_load = "--" if telemetry.gpu.utilization_percent is None else str(telemetry.gpu.utilization_percent)

        first_fan = next((fan for fan in telemetry.fans if fan.available and fan.rpm is not None), None)

        fan = "--" if first_fan is None else f"{first_fan.rpm}RPM"

        self.preview_body.setText(f"CPU {cpu}C / {cpu_load}%\nGPU {gpu}C\nLOAD {gpu_load}%\nFAN {fan}")



    def _metric_card(self, title: str, value: QLabel) -> QFrame:

        card = QFrame()

        card.setObjectName("MetricCard")

        layout = QVBoxLayout(card)

        title_label = QLabel(title)

        title_label.setStyleSheet("color: #a3a7aa;")

        value.setObjectName("MetricValue")

        value.setWordWrap(True)

        layout.addWidget(title_label)

        layout.addWidget(value)

        return card



    def _add_monitor_backgrounds(self) -> None:

        self.monitor_background_combo.addItem("默认暗色", "")

        for label, path in builtin_monitor_backgrounds():

            self.monitor_background_combo.addItem(label, str(path))



    def _add_monitor_layouts(self) -> None:

        for label, layout in (

            ("均衡双温度卡", "balanced"),

            ("GPU 大卡优先", "gpu_focus"),

            ("极简大数字", "minimal"),

            ("详细列表", "details"),

            ("底图叠字", "overlay"),

        ):

            self.monitor_layout_combo.addItem(label, layout)



    def _add_monitor_palettes(self) -> None:

        for label, palette in (

            ("霓虹蓝绿", "neon"),

            ("琥珀橙红", "amber"),

            ("冰蓝冷光", "ice"),

            ("终端绿色", "terminal"),

            ("高对比", "contrast"),

        ):

            self.monitor_palette_combo.addItem(label, palette)



    def choose_monitor_background(self) -> None:

        selected, _ = QFileDialog.getOpenFileName(

            self,

            "选择监控底图",

            "",

            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*)",

        )

        if not selected:

            return

        self.custom_background_path = Path(selected)

        self.monitor_background_combo.addItem(f"自定义：{self.custom_background_path.name}", str(self.custom_background_path))

        self.monitor_background_combo.setCurrentIndex(self.monitor_background_combo.count() - 1)



    def render_settings(self) -> MonitorRenderSettings:

        data = self.monitor_background_combo.currentData()

        layout = self.monitor_layout_combo.currentData() or "balanced"

        palette = self.monitor_palette_combo.currentData() or "neon"

        background_path = Path(str(data)) if data else None

        return MonitorRenderSettings(

            layout=layout,

            palette=palette,

            background_path=background_path,

            show_cpu_temp=self.show_cpu_temp_checkbox.isChecked(),

            show_gpu_temp=self.show_gpu_temp_checkbox.isChecked(),

            show_gpu_load=self.show_gpu_load_checkbox.isChecked(),

            show_gpu_power=self.show_gpu_power_checkbox.isChecked(),

            show_vram=self.show_vram_checkbox.isChecked(),

            show_gpu_clock=self.show_gpu_clock_checkbox.isChecked(),

            show_cpu_load=self.show_cpu_load_checkbox.isChecked(),

            show_time=self.show_time_checkbox.isChecked(),

        )



    def set_profile_names(self, names: list[str], active: str = "") -> None:

        self.monitor_profile_combo.clear()

        self.monitor_profile_combo.addItems(names)

        if active and active in names:

            self.monitor_profile_combo.setCurrentText(active)



    def set_live_monitoring_active(self, active: bool) -> None:

        self.live_monitor_button.setText("停止实时监控" if active else "开始实时监控")

        self.live_monitor_button.setChecked(active)
