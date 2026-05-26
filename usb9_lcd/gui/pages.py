from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from usb9_lcd.assets import AssetLibrary, MediaAsset
from usb9_lcd.gui.debug import log_event, log_exception
from usb9_lcd.gui.gif_preview import decode_gif_preview_frames
from usb9_lcd.gui.settings import GuiSettings, LightingUiSettings, save_settings
from usb9_lcd.lianli.analysis import analyze_live_log, diff_snapshot_files, summarize_experiment_dir
from usb9_lcd.lianli.capture import linux_control_write_gate_report
from usb9_lcd.lianli.lcd import LianLiWirelessLcdBackend, create_pyusb_lcd_backend
from usb9_lcd.lianli.wireless import (
    LianLiWirelessBackend,
    WirelessDeviceInfo,
    create_pyusb_backend,
    infer_led_count,
    scan_known_usb_devices,
)
from usb9_lcd.lighting import LightingSettings, LightingTarget, OpenRgbLightingController, OpenRgbServerManager
from usb9_lcd.monitoring.models import CpuTelemetry, GpuTelemetry, SystemTelemetry
from usb9_lcd.monitoring.render import MonitorLayout, MonitorRenderSettings


LIGHTING_PALETTES: dict[str, tuple[str, tuple[str, ...]]] = {
    "neon": ("霓虹蓝粉", ("#ff2d55", "#00e5ff", "#7cff6b", "#ffd60a", "#ffffff")),
    "cool": ("冷色科技", ("#0088ff", "#00e5ff", "#6ee7ff", "#a78bfa", "#ffffff")),
    "warm": ("暖色日落", ("#ff2d55", "#ff7a18", "#ffd60a", "#ffb86b", "#ffffff")),
    "aurora": ("极光渐变", ("#22c55e", "#00e5ff", "#8b5cf6", "#f472b6", "#ffffff")),
    "mono": ("黑白高对比", ("#ffffff", "#cbd5e1", "#94a3b8", "#64748b", "#000000")),
}

LIANLI_WRITE_CONFIRM_TOKEN = "WRITE-LIANLI"
GIF_PREVIEW_MIN_FRAME_MS = 66


def format_cpu_temperature(cpu: CpuTelemetry) -> str:
    if cpu.available and cpu.package_temperature_c is not None:
        return f"CPU {cpu.package_temperature_c:.0f}°C"
    return "CPU 不可用"


def format_gpu_temperature(gpu: GpuTelemetry) -> str:
    if gpu.available and gpu.temperature_c is not None:
        return f"GPU {gpu.temperature_c}°C"
    return "GPU 不可用"


def format_gpu_detail(gpu: GpuTelemetry) -> str:
    if not gpu.available:
        return "NVIDIA GPU 不可用"

    details = [gpu.name]
    if gpu.utilization_percent is not None:
        details.append(f"{gpu.utilization_percent}%")
    if gpu.power_w is not None:
        details.append(f"{gpu.power_w:.0f}W")
    if gpu.memory_used_mb is not None and gpu.memory_total_mb is not None:
        details.append(f"{gpu.memory_used_mb}/{gpu.memory_total_mb}MB")
    if gpu.graphics_clock_mhz is not None:
        details.append(f"{gpu.graphics_clock_mhz}MHz")
    return " | ".join(details)


def _wireless_device_payload(device: WirelessDeviceInfo) -> dict[str, object]:
    return {
        "mac": device.mac,
        "master_mac": device.master_mac,
        "is_bound": device.is_bound,
        "channel": device.channel,
        "rx_type": device.rx_type,
        "device_type": device.device_type,
        "fan_count": device.fan_count,
        "pwm_values": list(device.pwm_values),
        "fan_rpm": list(device.fan_rpm),
        "command_sequence": device.command_sequence,
    }


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
            self.upload_monitor_button = QPushButton("上传一次")
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
        self.cpu_temp_value = QLabel("CPU 不可用")
        self.gpu_temp_value = QLabel("GPU 不可用")
        self.gpu_detail_value = QLabel("NVIDIA GPU 不可用")

        grid.addWidget(self._metric_card("CPU 温度", self.cpu_temp_value), 0, 0)
        grid.addWidget(self._metric_card("GPU 温度", self.gpu_temp_value), 0, 1)
        grid.addWidget(self._metric_card("GPU 详情", self.gpu_detail_value), 1, 0, 1, 2)
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
        self.gpu_detail_value.setText("NVIDIA GPU 不可用")
        self.preview_body.clear()
        self.preview_body.setText("监控不可用")

    def _update_preview_text(self, telemetry: SystemTelemetry) -> None:
        cpu = "--" if telemetry.cpu.package_temperature_c is None else f"{telemetry.cpu.package_temperature_c:.0f}"
        gpu = "--" if telemetry.gpu.temperature_c is None else f"{telemetry.gpu.temperature_c:.0f}"
        gpu_load = "--" if telemetry.gpu.utilization_percent is None else str(telemetry.gpu.utilization_percent)
        self.preview_body.setText(f"CPU {cpu}°C\nGPU {gpu}°C\nLOAD {gpu_load}%")

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
        for label, path in (
            ("ROG 红色网格", Path("assets/monitor_backgrounds/rog_red_grid.png")),
            ("蓝色核心", Path("assets/monitor_backgrounds/blue_core.png")),
            ("霓虹仪表", Path("assets/monitor_backgrounds/neon_meter.png")),
        ):
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


class LianLiWirelessPage(QWidget):
    operation_finished = Signal(str, object)
    status_changed = Signal(str)

    def __init__(
        self,
        backend_factory: Callable[[], LianLiWirelessBackend] = create_pyusb_backend,
        lcd_backend_factory: Callable[[], LianLiWirelessLcdBackend] = create_pyusb_lcd_backend,
        validation_output_dir: Path = Path(".cache/lianli/gui-validation"),
        experiment_output_dir: Path = Path(".cache/lianli/gui-pwm-experiment"),
        sync_experiment_output_dir: Path = Path(".cache/lianli/gui-sync-experiment"),
        mirror_experiment_output_dir: Path = Path(".cache/lianli/gui-pwm-mirror-experiment"),
        rgb_experiment_output_dir: Path = Path(".cache/lianli/gui-rgb-experiment"),
        rainbow_experiment_output_dir: Path = Path(".cache/lianli/gui-rainbow-experiment"),
        bind_experiment_output_dir: Path = Path(".cache/lianli/gui-bind-experiment"),
        unbind_experiment_output_dir: Path = Path(".cache/lianli/gui-unbind-experiment"),
        write_gate_capture_dir: Path = Path(".cache/lianli"),
        write_gate_experiment_dir: Path | None = None,
        require_write_gate: bool = False,
        write_gate_report_factory: Callable[..., dict[str, object]] = linux_control_write_gate_report,
    ) -> None:
        super().__init__()
        self.backend_factory = backend_factory
        self.lcd_backend_factory = lcd_backend_factory
        self.validation_output_dir = validation_output_dir
        self.experiment_output_dir = experiment_output_dir
        self.sync_experiment_output_dir = sync_experiment_output_dir
        self.mirror_experiment_output_dir = mirror_experiment_output_dir
        self.rgb_experiment_output_dir = rgb_experiment_output_dir
        self.rainbow_experiment_output_dir = rainbow_experiment_output_dir
        self.bind_experiment_output_dir = bind_experiment_output_dir
        self.unbind_experiment_output_dir = unbind_experiment_output_dir
        self.write_gate_capture_dir = write_gate_capture_dir
        self.write_gate_experiment_dir = write_gate_experiment_dir or experiment_output_dir
        self.require_write_gate = require_write_gate
        self.write_gate_report_factory = write_gate_report_factory
        self._lianli_write_gate_payload: dict[str, object] | None = None
        self._operation_active = False
        self._closed = False
        self._threads: list[threading.Thread] = []
        self.operation_finished.connect(self._operation_finished)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(16)

        header = QLabel("联力无线")
        header.setObjectName("PageTitle")
        subtitle = QLabel("L-Wireless 接收器、风扇、灯光和配对状态的 Linux 探测")
        subtitle.setObjectName("PageSubtitle")
        layout.addWidget(header)
        layout.addWidget(subtitle)

        layout.addWidget(self._read_panel())
        layout.addWidget(self._experiment_guide_panel())

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self._snapshot_panel(), 3)
        body.addWidget(self._write_panel(), 2)
        layout.addLayout(body, 1)

        self.scan_local_devices()

    def _read_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("MetricCard")
        layout = QGridLayout(panel)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        title = QLabel("L-Wireless")
        title.setObjectName("SectionLabel")
        self.lianli_status_label = QLabel("默认只读")
        self.lianli_status_label.setObjectName("FieldHint")
        self.lianli_status_label.setWordWrap(True)
        self.scan_lianli_button = QPushButton("扫描 USB")
        self.scan_lianli_button.clicked.connect(self.scan_local_devices)
        self.live_lianli_button = QPushButton("读取接收器")
        self.live_lianli_button.setObjectName("PrimaryButton")
        self.live_lianli_button.clicked.connect(self.refresh_live_devices)
        self.master_lianli_button = QPushButton("读取 Master")
        self.master_lianli_button.clicked.connect(self.query_live_master)
        self.lcd_info_button = QPushButton("读取 LCD")
        self.lcd_info_button.clicked.connect(self.query_live_lcd_info)
        self.validate_lianli_button = QPushButton("只读验证")
        self.validate_lianli_button.clicked.connect(self.run_readonly_validation)
        self.save_lianli_snapshot_button = QPushButton("保存快照")
        self.save_lianli_snapshot_button.clicked.connect(self.save_lianli_snapshot)
        self.analyze_lianli_log_button = QPushButton("分析日志")
        self.analyze_lianli_log_button.clicked.connect(self.analyze_lianli_log)
        self.diff_lianli_snapshots_button = QPushButton("对比快照")
        self.diff_lianli_snapshots_button.clicked.connect(self.diff_lianli_snapshots)
        self.summarize_lianli_experiments_button = QPushButton("汇总实验")
        self.summarize_lianli_experiments_button.clicked.connect(self.summarize_lianli_experiments)
        self.lianli_write_gate_button = QPushButton("写入门禁")
        self.lianli_write_gate_button.clicked.connect(self.run_lianli_write_gate)
        layout.addWidget(title, 0, 0)
        layout.addWidget(self.lianli_status_label, 0, 1, 1, 4)
        for index, button in enumerate(
            (
                self.scan_lianli_button,
                self.live_lianli_button,
                self.master_lianli_button,
                self.lcd_info_button,
                self.validate_lianli_button,
                self.save_lianli_snapshot_button,
                self.analyze_lianli_log_button,
                self.diff_lianli_snapshots_button,
                self.summarize_lianli_experiments_button,
                self.lianli_write_gate_button,
            )
        ):
            layout.addWidget(button, 1 + index // 5, index % 5)
        return panel

    def _experiment_guide_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("MetricCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        title = QLabel("实验流程")
        title.setObjectName("SectionLabel")
        guide = QLabel(
            "1. 扫描 USB 并保存只读快照；2. 读取接收器和 Master；3. 只对单个 MAC 做安全实验；"
            "4. 对比写入前后快照，确认有效后再长期使用。"
        )
        guide.setObjectName("FieldHint")
        guide.setWordWrap(True)
        self.lianli_next_action_label = QLabel("下一步：先完成只读验证、写入门禁和实验汇总")
        self.lianli_next_action_label.setObjectName("FieldHint")
        self.lianli_next_action_label.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(guide)
        layout.addWidget(self.lianli_next_action_label)
        return panel

    def _snapshot_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("MetricCard")
        layout = QVBoxLayout(panel)
        title = QLabel("接收器快照")
        title.setObjectName("SectionLabel")
        self.lianli_snapshot_text = QTextEdit()
        self.lianli_snapshot_text.setReadOnly(True)
        self.lianli_snapshot_text.setMinimumHeight(320)
        layout.addWidget(title)
        layout.addWidget(self.lianli_snapshot_text, 1)
        return panel

    def _write_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("MetricCard")
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)
        title = QLabel("受保护写入")
        title.setObjectName("SectionLabel")
        layout.addWidget(title)
        hint = QLabel("写入会影响真实风扇/灯光，只对指定 MAC 单设备发送")
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.lianli_write_gate_label = QLabel("")
        self.lianli_write_gate_label.setObjectName("FieldHint")
        self.lianli_write_gate_label.setWordWrap(True)
        layout.addWidget(self.lianli_write_gate_label)

        self.lianli_write_enable = QCheckBox("启用联力写入")
        self.lianli_write_enable.toggled.connect(self._update_write_controls)
        self.lianli_confirm_input = QLineEdit()
        self.lianli_confirm_input.setPlaceholderText(LIANLI_WRITE_CONFIRM_TOKEN)
        self.lianli_confirm_input.textChanged.connect(self._update_write_controls)
        self.lianli_mac_input = QLineEdit()
        self.lianli_mac_input.setPlaceholderText("aa:bb:cc:dd:ee:ff")
        self.lianli_master_mac_input = QLineEdit()
        self.lianli_master_mac_input.setPlaceholderText("可留空自动读取 Master")
        self.lianli_rx_type_value = QSpinBox()
        self.lianli_rx_type_value.setRange(1, 15)
        self.lianli_rx_type_value.setValue(3)
        self.lianli_pwm_value = QSpinBox()
        self.lianli_pwm_value.setRange(40, 255)
        self.lianli_pwm_value.setValue(120)
        self.lianli_pwm_value.setSuffix(" PWM")
        self.lianli_lcd_brightness = QSpinBox()
        self.lianli_lcd_brightness.setRange(0, 100)
        self.lianli_lcd_brightness.setValue(60)
        self.lianli_lcd_brightness.setSuffix(" %")
        self.lianli_lcd_rotation = QComboBox()
        for degrees in (0, 90, 180, 270):
            self.lianli_lcd_rotation.addItem(f"{degrees}°", degrees)
        self.lianli_rainbow_frame_count = QSpinBox()
        self.lianli_rainbow_frame_count.setRange(1, 65535)
        self.lianli_rainbow_frame_count.setValue(24)
        self.lianli_rainbow_frame_count.setSuffix(" 帧")
        self.lianli_rainbow_interval = QSpinBox()
        self.lianli_rainbow_interval.setRange(0, 65535)
        self.lianli_rainbow_interval.setValue(50)
        self.lianli_rainbow_interval.setSuffix(" ms")
        layout.addWidget(self.lianli_write_enable)
        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.addWidget(QLabel("确认令牌"), 0, 0)
        form.addWidget(self.lianli_confirm_input, 0, 1, 1, 3)
        form.addWidget(QLabel("目标 MAC"), 1, 0)
        form.addWidget(self.lianli_mac_input, 1, 1, 1, 3)
        form.addWidget(QLabel("Master MAC"), 2, 0)
        form.addWidget(self.lianli_master_mac_input, 2, 1, 1, 3)
        form.addWidget(QLabel("RX Type"), 3, 0)
        form.addWidget(self.lianli_rx_type_value, 3, 1)
        form.addWidget(QLabel("PWM"), 3, 2)
        form.addWidget(self.lianli_pwm_value, 3, 3)
        form.addWidget(QLabel("LCD 亮度"), 4, 0)
        form.addWidget(self.lianli_lcd_brightness, 4, 1)
        form.addWidget(QLabel("LCD 旋转"), 4, 2)
        form.addWidget(self.lianli_lcd_rotation, 4, 3)
        form.addWidget(QLabel("彩虹帧数"), 5, 0)
        form.addWidget(self.lianli_rainbow_frame_count, 5, 1)
        form.addWidget(QLabel("彩虹间隔"), 5, 2)
        form.addWidget(self.lianli_rainbow_interval, 5, 3)
        layout.addLayout(form)

        self.lianli_pwm_button = QPushButton("发送 PWM")
        self.lianli_pwm_button.clicked.connect(self.send_live_pwm)
        self.lianli_safe_pwm_button = QPushButton("安全 PWM 实验")
        self.lianli_safe_pwm_button.clicked.connect(self.run_safe_pwm_experiment)
        self.lianli_pwm_sync_button = QPushButton("主板 PWM 同步")
        self.lianli_pwm_sync_button.clicked.connect(self.send_live_pwm_sync)
        self.lianli_safe_sync_button = QPushButton("安全 Sync 实验")
        self.lianli_safe_sync_button.clicked.connect(self.run_safe_sync_experiment)
        self.lianli_pwm_mirror_button = QPushButton("镜像主板 PWM")
        self.lianli_pwm_mirror_button.clicked.connect(self.send_live_pwm_mirror)
        self.lianli_safe_mirror_button = QPushButton("安全 Mirror 实验")
        self.lianli_safe_mirror_button.clicked.connect(self.run_safe_pwm_mirror_experiment)
        self.lianli_rgb_off_button = QPushButton("关闭无线灯光")
        self.lianli_rgb_off_button.clicked.connect(self.send_live_rgb_off)
        self.lianli_safe_rgb_button = QPushButton("安全 RGB 实验")
        self.lianli_safe_rgb_button.clicked.connect(self.run_safe_rgb_experiment)
        self.lianli_rainbow_button = QPushButton("彩虹灯效")
        self.lianli_rainbow_button.clicked.connect(self.send_live_rainbow)
        self.lianli_safe_rainbow_button = QPushButton("安全 Rainbow 实验")
        self.lianli_safe_rainbow_button.clicked.connect(self.run_safe_rainbow_experiment)
        self.lianli_safe_bind_button = QPushButton("安全 Bind 实验")
        self.lianli_safe_bind_button.clicked.connect(self.run_safe_bind_experiment)
        self.lianli_safe_unbind_button = QPushButton("安全 Unbind 实验")
        self.lianli_safe_unbind_button.clicked.connect(self.run_safe_unbind_experiment)
        self.lianli_lcd_info_button = QPushButton("读取 LCD 信息")
        self.lianli_lcd_info_button.clicked.connect(self.query_live_lcd_info)
        self.lianli_lcd_control_button = QPushButton("应用 LCD")
        self.lianli_lcd_control_button.clicked.connect(self.send_live_lcd_control)
        action_grid = QGridLayout()
        action_grid.setHorizontalSpacing(8)
        action_grid.setVerticalSpacing(8)
        for index, button in enumerate((
            self.lianli_pwm_button,
            self.lianli_safe_pwm_button,
            self.lianli_pwm_sync_button,
            self.lianli_safe_sync_button,
            self.lianli_pwm_mirror_button,
            self.lianli_safe_mirror_button,
            self.lianli_rgb_off_button,
            self.lianli_safe_rgb_button,
            self.lianli_rainbow_button,
            self.lianli_safe_rainbow_button,
            self.lianli_safe_bind_button,
            self.lianli_safe_unbind_button,
            self.lianli_lcd_info_button,
            self.lianli_lcd_control_button,
        )):
            action_grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(action_grid)
        layout.addStretch(1)
        self._update_write_controls()
        return panel

    def scan_local_devices(self) -> None:
        payload = self._scan_usb_payload()
        self.lianli_snapshot_text.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))
        self._set_lianli_status(f"USB 扫描完成：{payload['device_count']} 个已知设备")

    def refresh_live_devices(self) -> None:
        self._run_lianli_operation("正在读取接收器...", self._live_devices_payload)

    def query_live_master(self) -> None:
        self._run_lianli_operation("正在读取 Master...", self._live_master_payload)

    def query_live_lcd_info(self) -> None:
        self._run_lianli_operation("正在读取 LCD...", self._live_lcd_info_payload)

    def run_readonly_validation(self) -> None:
        self._run_lianli_operation("正在执行只读验证...", self._readonly_validation_payload)

    def run_lianli_write_gate(self) -> None:
        self._run_lianli_operation("正在检查写入门禁...", self._write_gate_payload)

    def save_lianli_snapshot(self) -> None:
        text = self.lianli_snapshot_text.toPlainText().strip()
        if not text:
            self._set_lianli_status("没有可保存的联力快照")
            return
        default_path = str(Path(".cache/lianli/gui-snapshot.json"))
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "保存联力快照",
            default_path,
            "JSON (*.json);;All Files (*)",
        )
        if not path:
            return
        destination = Path(path)
        try:
            json.loads(text)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text + "\n", encoding="utf-8")
        except Exception as error:  # noqa: BLE001
            self._set_lianli_status(f"保存失败：{error}")
            return
        self._set_lianli_status(f"已保存：{destination}")

    def analyze_lianli_log(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "选择联力 live 写入日志",
            str(Path(".cache/lianli")),
            "JSON (*.json);;All Files (*)",
        )
        if not path:
            return
        self._run_lianli_operation(
            "正在分析日志...",
            lambda: analyze_live_log(Path(path)),
        )

    def diff_lianli_snapshots(self) -> None:
        before_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "选择写入前快照",
            str(Path(".cache/lianli")),
            "JSON (*.json);;All Files (*)",
        )
        if not before_path:
            return
        after_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "选择写入后快照",
            str(Path(before_path).parent),
            "JSON (*.json);;All Files (*)",
        )
        if not after_path:
            return
        self._run_lianli_operation(
            "正在对比快照...",
            lambda: diff_snapshot_files(Path(before_path), Path(after_path)),
        )

    def summarize_lianli_experiments(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "选择联力实验日志目录",
            str(Path(".cache/lianli")),
        )
        if not path:
            return
        self._run_lianli_operation(
            "正在汇总实验...",
            lambda: summarize_experiment_dir(Path(path)),
        )

    def send_live_pwm(self) -> None:
        self._run_guarded_write(
            "正在发送 PWM...",
            lambda backend, target: backend.send_pwm(target, [self.lianli_pwm_value.value()]),
        )

    def run_safe_pwm_experiment(self) -> None:
        if not self._write_unlocked():
            self.lianli_status_label.setText(self._write_blocked_text())
            return
        mac = self.lianli_mac_input.text().strip()
        if not mac:
            self.lianli_status_label.setText("请填写目标 MAC")
            return
        pwm = self.lianli_pwm_value.value()
        self._run_safe_receiver_experiment(
            busy_message="正在执行安全 PWM 实验...",
            mac=mac,
            output_dir=self.experiment_output_dir,
            result_operation="gui-safe-pwm-experiment",
            write_operation="live-pwm",
            write_filename="live-pwm.json",
            analysis_filename="analyze-live-pwm.json",
            write_fields={"pwm_values": [pwm, pwm, pwm, pwm]},
            result_fields={"pwm_values": [pwm, pwm, pwm, pwm]},
            writer=lambda backend, target: backend.send_pwm(target, [pwm]),
        )

    def send_live_pwm_sync(self) -> None:
        self._run_guarded_write(
            "正在启用主板 PWM 同步...",
            lambda backend, target: backend.send_motherboard_pwm_sync(target),
        )

    def run_safe_sync_experiment(self) -> None:
        if not self._write_unlocked():
            self.lianli_status_label.setText(self._write_blocked_text())
            return
        mac = self.lianli_mac_input.text().strip()
        if not mac:
            self.lianli_status_label.setText("请填写目标 MAC")
            return
        expected_pwm_values = [6, 6, 6, 6]
        fields = {
            "enabled": True,
            "fallback_pwm": 100,
            "expected_pwm_values": expected_pwm_values,
        }
        self._run_safe_receiver_experiment(
            busy_message="正在执行安全 Sync 实验...",
            mac=mac,
            output_dir=self.sync_experiment_output_dir,
            result_operation="gui-safe-sync-experiment",
            write_operation="live-pwm-sync",
            write_filename="live-pwm-sync.json",
            analysis_filename="analyze-live-pwm-sync.json",
            write_fields=fields,
            result_fields=fields,
            writer=lambda backend, target: backend.send_motherboard_pwm_sync(target),
        )

    def send_live_pwm_mirror(self) -> None:
        if not self._write_unlocked():
            self.lianli_status_label.setText(self._write_blocked_text())
            return
        mac = self.lianli_mac_input.text().strip()
        if not mac:
            self.lianli_status_label.setText("请填写目标 MAC")
            return

        def operation() -> dict[str, object]:
            backend = self.backend_factory()
            before = backend.list_devices()
            motherboard_pwm = self._safe_snapshot_motherboard_pwm(before.motherboard_pwm)
            target = next((device for device in before.devices if device.mac.lower() == mac.lower()), None)
            if target is None:
                raise ValueError(f"未找到接收器 MAC：{mac}")
            packets_written = backend.send_motherboard_pwm_mirror(target, motherboard_pwm)
            after = backend.list_devices()
            return {
                "operation": "live-pwm-mirror",
                "target": target.mac,
                "motherboard_pwm": motherboard_pwm,
                "pwm_values": [motherboard_pwm] * 4,
                "packets_written": packets_written,
                "before": _wireless_device_payload(target),
                "after": {
                    "device_count": after.device_count,
                    "motherboard_pwm": after.motherboard_pwm,
                    "devices": [_wireless_device_payload(device) for device in after.devices],
                },
            }

        self._run_lianli_operation("正在镜像主板 PWM...", operation)

    def run_safe_pwm_mirror_experiment(self) -> None:
        if not self._write_unlocked():
            self.lianli_status_label.setText(self._write_blocked_text())
            return
        mac = self.lianli_mac_input.text().strip()
        if not mac:
            self.lianli_status_label.setText("请填写目标 MAC")
            return

        def operation() -> dict[str, object]:
            backend = self.backend_factory()
            output_dir = self.mirror_experiment_output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            before = backend.list_devices()
            motherboard_pwm = self._safe_snapshot_motherboard_pwm(before.motherboard_pwm)
            pwm_values = [motherboard_pwm] * 4
            before_payload = {
                "operation": "live-list-before",
                "device_count": before.device_count,
                "motherboard_pwm": before.motherboard_pwm,
                "devices": [_wireless_device_payload(device) for device in before.devices],
            }
            before_path = output_dir / "live-list-before.json"
            before_path.write_text(json.dumps(before_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            target = next((device for device in before.devices if device.mac.lower() == mac.lower()), None)
            if target is None:
                raise ValueError(f"未找到接收器 MAC：{mac}")
            packets_written = backend.send_motherboard_pwm_mirror(target, motherboard_pwm)

            after = backend.list_devices()
            after_payload = {
                "operation": "live-list-after",
                "device_count": after.device_count,
                "motherboard_pwm": after.motherboard_pwm,
                "devices": [_wireless_device_payload(device) for device in after.devices],
            }
            after_path = output_dir / "live-list-after.json"
            after_path.write_text(json.dumps(after_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            write_payload = {
                "operation": "live-pwm-mirror",
                "target": target.mac,
                "motherboard_pwm": motherboard_pwm,
                "pwm_values": pwm_values,
                "packets_written": packets_written,
                "before": _wireless_device_payload(target),
                "after": after_payload,
            }
            write_path = output_dir / "live-pwm-mirror.json"
            write_path.write_text(json.dumps(write_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            analysis_payload = analyze_live_log(write_path)
            analysis_path = output_dir / "analyze-live-pwm-mirror.json"
            analysis_path.write_text(json.dumps(analysis_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            summary_payload = summarize_experiment_dir(output_dir)
            summary_path = output_dir / "summary.json"
            summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return {
                "operation": "gui-safe-pwm-mirror-experiment",
                "target": target.mac,
                "motherboard_pwm": motherboard_pwm,
                "pwm_values": pwm_values,
                "output_dir": str(output_dir),
                "packets_written": packets_written,
                "likely_effective": analysis_payload["likely_effective"],
                "steps": [
                    {"name": "before", "path": str(before_path)},
                    {"name": "write", "path": str(write_path)},
                    {"name": "after", "path": str(after_path)},
                    {"name": "analysis", "path": str(analysis_path)},
                    {"name": "summary", "path": str(summary_path)},
                ],
                "analysis": analysis_payload,
                "summary": summary_payload,
            }

        self._run_lianli_operation("正在执行安全 Mirror 实验...", operation)

    def send_live_rgb_off(self) -> None:
        self._run_guarded_write(
            "正在关闭无线灯光...",
            lambda backend, target: backend.send_static_rgb(target, (0, 0, 0)),
        )

    def send_live_rainbow(self) -> None:
        frame_count = self.lianli_rainbow_frame_count.value()
        interval_ms = self.lianli_rainbow_interval.value()
        self._run_guarded_write(
            "正在发送彩虹灯效...",
            lambda backend, target: backend.send_rainbow_rgb(
                target,
                frame_count=frame_count,
                interval_ms=interval_ms,
            ),
        )

    def run_safe_rgb_experiment(self) -> None:
        if not self._write_unlocked():
            self.lianli_status_label.setText(self._write_blocked_text())
            return
        mac = self.lianli_mac_input.text().strip()
        if not mac:
            self.lianli_status_label.setText("请填写目标 MAC")
            return
        color = (0, 0, 0)

        def mark_visual_confirmation(analysis_payload: dict[str, object]) -> dict[str, object]:
            required = not bool(analysis_payload["snapshot_changed"])
            analysis_payload["visual_confirmation_required"] = required
            return {"visual_confirmation_required": required}

        self._run_safe_receiver_experiment(
            busy_message="正在执行安全 RGB 实验...",
            mac=mac,
            output_dir=self.rgb_experiment_output_dir,
            result_operation="gui-safe-rgb-experiment",
            write_operation="live-rgb",
            write_filename="live-rgb.json",
            analysis_filename="analyze-live-rgb.json",
            write_fields={"color": list(color), "effect_index": 1},
            result_fields={"color": list(color), "effect_index": 1},
            writer=lambda backend, target: backend.send_static_rgb(target, color),
            analysis_enricher=mark_visual_confirmation,
        )

    def run_safe_rainbow_experiment(self) -> None:
        if not self._write_unlocked():
            self.lianli_status_label.setText(self._write_blocked_text())
            return
        mac = self.lianli_mac_input.text().strip()
        if not mac:
            self.lianli_status_label.setText("请填写目标 MAC")
            return
        frame_count = self.lianli_rainbow_frame_count.value()
        interval_ms = self.lianli_rainbow_interval.value()

        def mark_visual_confirmation(analysis_payload: dict[str, object]) -> dict[str, object]:
            required = not bool(analysis_payload["snapshot_changed"])
            analysis_payload["visual_confirmation_required"] = required
            return {"visual_confirmation_required": required}

        def send_rainbow(
            backend: LianLiWirelessBackend,
            target: WirelessDeviceInfo,
        ) -> tuple[int, dict[str, object], dict[str, object]]:
            led_count = infer_led_count(target)
            packets_written = backend.send_rainbow_rgb(
                target,
                frame_count=frame_count,
                interval_ms=interval_ms,
            )
            dynamic_fields = {"led_count": led_count}
            return packets_written, dynamic_fields, dynamic_fields

        self._run_safe_receiver_experiment(
            busy_message="正在执行安全 Rainbow 实验...",
            mac=mac,
            output_dir=self.rainbow_experiment_output_dir,
            result_operation="gui-safe-rainbow-experiment",
            write_operation="live-rainbow",
            write_filename="live-rainbow.json",
            analysis_filename="analyze-live-rainbow.json",
            write_fields={
                "frame_count": frame_count,
                "interval_ms": interval_ms,
                "effect_index": 1,
            },
            result_fields={
                "frame_count": frame_count,
                "interval_ms": interval_ms,
                "effect_index": 1,
            },
            writer=send_rainbow,
            analysis_enricher=mark_visual_confirmation,
        )

    def run_safe_bind_experiment(self) -> None:
        if not self._write_unlocked():
            self.lianli_status_label.setText(self._write_blocked_text())
            return
        mac = self.lianli_mac_input.text().strip()
        if not mac:
            self.lianli_status_label.setText("请填写目标 MAC")
            return
        requested_master_mac = self.lianli_master_mac_input.text().strip()
        rx_type = self.lianli_rx_type_value.value()

        def bind_writer(
            backend: LianLiWirelessBackend,
            target: WirelessDeviceInfo,
        ) -> tuple[int, dict[str, object], dict[str, object]]:
            if target.is_bound:
                raise ValueError(f"接收器已绑定：{target.mac}")
            master_query_channel = target.channel or 8
            master_mac = requested_master_mac
            if not master_mac:
                master_query = backend.query_master_mac(channel=master_query_channel)
                if master_query is None:
                    raise ValueError("无法自动读取 Master MAC，请手动填写")
                master_mac = master_query[0]
            packets_written = backend.send_bind(
                target,
                master_mac=master_mac,
                rx_type=rx_type,
                channel=None,
            )
            fields = {
                "master_mac": master_mac,
                "rx_type": rx_type,
                "channel": None,
                "master_query_channel": master_query_channel,
            }
            return packets_written, fields, fields

        self._run_safe_receiver_experiment(
            busy_message="正在执行安全 Bind 实验...",
            mac=mac,
            output_dir=self.bind_experiment_output_dir,
            result_operation="gui-safe-bind-experiment",
            write_operation="live-bind",
            write_filename="live-bind.json",
            analysis_filename="analyze-live-bind.json",
            write_fields={},
            result_fields={},
            writer=bind_writer,
        )

    def run_safe_unbind_experiment(self) -> None:
        if not self._write_unlocked():
            self.lianli_status_label.setText(self._write_blocked_text())
            return
        mac = self.lianli_mac_input.text().strip()
        if not mac:
            self.lianli_status_label.setText("请填写目标 MAC")
            return

        def unbind_writer(
            backend: LianLiWirelessBackend,
            target: WirelessDeviceInfo,
        ) -> tuple[int, dict[str, object], dict[str, object]]:
            if not target.is_bound:
                raise ValueError(f"接收器已解绑：{target.mac}")
            packets_written = backend.send_unbind(target, channel=None)
            fields = {"channel": None}
            return packets_written, fields, fields

        self._run_safe_receiver_experiment(
            busy_message="正在执行安全 Unbind 实验...",
            mac=mac,
            output_dir=self.unbind_experiment_output_dir,
            result_operation="gui-safe-unbind-experiment",
            write_operation="live-unbind",
            write_filename="live-unbind.json",
            analysis_filename="analyze-live-unbind.json",
            write_fields={},
            result_fields={},
            writer=unbind_writer,
        )

    def send_live_lcd_control(self) -> None:
        if not self._write_unlocked():
            self.lianli_status_label.setText(self._write_blocked_text())
            return
        brightness = self.lianli_lcd_brightness.value()
        rotation = int(self.lianli_lcd_rotation.currentData())

        def operation() -> dict[str, object]:
            backend = self.lcd_backend_factory()
            return {
                "operation": "live-lcd-control",
                "applied": {
                    "brightness": {
                        "value": brightness,
                        "bytes_written": backend.set_brightness(brightness),
                    },
                    "rotation": {
                        "degrees": rotation,
                        "bytes_written": backend.set_rotation(rotation),
                    },
                },
            }

        self._run_lianli_operation("正在应用 LCD...", operation)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self._closed = True
        super().closeEvent(event)

    def _live_devices_payload(self) -> dict[str, object]:
        backend = self.backend_factory()
        snapshot = backend.list_devices()
        return {
            "operation": "live-list",
            "device_count": snapshot.device_count,
            "motherboard_pwm": snapshot.motherboard_pwm,
            "devices": [_wireless_device_payload(device) for device in snapshot.devices],
        }

    def _live_master_payload(self) -> dict[str, object]:
        backend = self.backend_factory()
        result = backend.query_master_mac(channel=8)
        return {
            "operation": "live-master",
            "detected": result is not None,
            "master_mac": result[0] if result else None,
            "channel": result[1] if result else 8,
        }

    def _live_lcd_info_payload(self) -> dict[str, object]:
        backend = self.lcd_backend_factory()
        return {
            "operation": "live-lcd-info",
            "mode": "both",
            "handshake": backend.handshake(),
            "firmware": backend.firmware_version(),
        }

    def _scan_usb_payload(self) -> dict[str, object]:
        devices = scan_known_usb_devices()
        return {
            "operation": "scan-usb",
            "device_count": len(devices),
            "devices": [
                {
                    "vid_pid": device.vid_pid,
                    "label": device.label,
                    "manufacturer": device.manufacturer,
                    "product": device.product,
                    "serial": device.serial,
                    "sysfs_path": device.sysfs_path,
                }
                for device in devices
            ],
        }

    def _readonly_validation_payload(self) -> dict[str, object]:
        output_dir = self.validation_output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        steps: list[dict[str, object]] = []
        operations: tuple[tuple[str, Callable[[], dict[str, object]]], ...] = (
            ("scan-usb", self._scan_usb_payload),
            ("live-list", self._live_devices_payload),
            ("live-master", self._live_master_payload),
            ("live-lcd-info", self._live_lcd_info_payload),
        )
        for name, operation in operations:
            path = output_dir / f"{name}.json"
            try:
                payload = operation()
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                steps.append({"name": name, "status": "ok", "path": str(path)})
            except Exception as error:  # noqa: BLE001
                payload = {
                    "operation": name,
                    "status": "error",
                    "error": str(error),
                }
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                steps.append({"name": name, "status": "error", "path": str(path), "error": str(error)})
        return {
            "operation": "gui-validate-readonly",
            "output_dir": str(output_dir),
            "step_count": len(steps),
            "ok_count": sum(1 for step in steps if step["status"] == "ok"),
            "error_count": sum(1 for step in steps if step["status"] == "error"),
            "steps": steps,
        }

    def _write_gate_payload(self) -> dict[str, object]:
        return self.write_gate_report_factory(
            self.write_gate_capture_dir,
            experiment_dir=self.write_gate_experiment_dir,
        )

    def _run_safe_receiver_experiment(
        self,
        *,
        busy_message: str,
        mac: str,
        output_dir: Path,
        result_operation: str,
        write_operation: str,
        write_filename: str,
        analysis_filename: str,
        write_fields: dict[str, object],
        result_fields: dict[str, object],
        writer: Callable[
            [LianLiWirelessBackend, WirelessDeviceInfo],
            int | tuple[int, dict[str, object], dict[str, object]],
        ],
        analysis_enricher: Callable[[dict[str, object]], dict[str, object]] | None = None,
    ) -> None:
        def operation() -> dict[str, object]:
            backend = self.backend_factory()
            output_dir.mkdir(parents=True, exist_ok=True)
            before = backend.list_devices()
            before_payload = {
                "operation": "live-list-before",
                "device_count": before.device_count,
                "motherboard_pwm": before.motherboard_pwm,
                "devices": [_wireless_device_payload(device) for device in before.devices],
            }
            before_path = output_dir / "live-list-before.json"
            before_path.write_text(json.dumps(before_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            target = next((device for device in before.devices if device.mac.lower() == mac.lower()), None)
            if target is None:
                raise ValueError(f"未找到接收器 MAC：{mac}")
            write_result = writer(backend, target)
            dynamic_write_fields: dict[str, object] = {}
            dynamic_result_fields: dict[str, object] = {}
            if isinstance(write_result, tuple):
                packets_written, dynamic_write_fields, dynamic_result_fields = write_result
            else:
                packets_written = write_result
            after = backend.list_devices()
            after_payload = {
                "operation": "live-list-after",
                "device_count": after.device_count,
                "motherboard_pwm": after.motherboard_pwm,
                "devices": [_wireless_device_payload(device) for device in after.devices],
            }
            after_path = output_dir / "live-list-after.json"
            after_path.write_text(json.dumps(after_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            write_payload = {
                "operation": write_operation,
                "target": target.mac,
                **write_fields,
                **dynamic_write_fields,
                "packets_written": packets_written,
                "before": _wireless_device_payload(target),
                "after": {
                    "device_count": after.device_count,
                    "motherboard_pwm": after.motherboard_pwm,
                    "devices": [_wireless_device_payload(device) for device in after.devices],
                },
            }
            write_path = output_dir / write_filename
            write_path.write_text(json.dumps(write_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            analysis_payload = analyze_live_log(write_path)
            enriched_result_fields = {**result_fields, **dynamic_result_fields}
            if analysis_enricher is not None:
                enriched_result_fields.update(analysis_enricher(analysis_payload))
            analysis_path = output_dir / analysis_filename
            analysis_path.write_text(json.dumps(analysis_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            summary_payload = summarize_experiment_dir(output_dir)
            summary_path = output_dir / "summary.json"
            summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return {
                "operation": result_operation,
                "target": target.mac,
                **enriched_result_fields,
                "output_dir": str(output_dir),
                "packets_written": packets_written,
                "likely_effective": analysis_payload["likely_effective"],
                "steps": [
                    {"name": "before", "path": str(before_path)},
                    {"name": "write", "path": str(write_path)},
                    {"name": "after", "path": str(after_path)},
                    {"name": "analysis", "path": str(analysis_path)},
                    {"name": "summary", "path": str(summary_path)},
                ],
                "analysis": analysis_payload,
                "summary": summary_payload,
            }

        self._run_lianli_operation(busy_message, operation)

    def _run_guarded_write(
        self,
        busy_message: str,
        writer: Callable[[LianLiWirelessBackend, WirelessDeviceInfo], int],
    ) -> None:
        if not self._write_unlocked():
            self.lianli_status_label.setText(self._write_blocked_text())
            return
        mac = self.lianli_mac_input.text().strip()
        if not mac:
            self.lianli_status_label.setText("请填写目标 MAC")
            return

        def operation() -> dict[str, object]:
            backend = self.backend_factory()
            before = backend.list_devices()
            target = next((device for device in before.devices if device.mac.lower() == mac.lower()), None)
            if target is None:
                raise ValueError(f"未找到接收器 MAC：{mac}")
            packets_written = writer(backend, target)
            after = backend.list_devices()
            return {
                "operation": "live-write",
                "target": target.mac,
                "packets_written": packets_written,
                "before": _wireless_device_payload(target),
                "after": {
                    "device_count": after.device_count,
                    "motherboard_pwm": after.motherboard_pwm,
                    "devices": [_wireless_device_payload(device) for device in after.devices],
                },
            }

        self._run_lianli_operation(busy_message, operation)

    def _safe_snapshot_motherboard_pwm(self, value: int | None) -> int:
        if value is None:
            raise ValueError("接收器快照没有有效的主板 PWM 值")
        minimum = self.lianli_pwm_value.minimum()
        if value < minimum:
            raise ValueError(f"主板 PWM {value} 低于安全下限 {minimum}")
        return max(0, min(255, int(value)))

    def _run_lianli_operation(self, busy_message: str, operation: Callable[[], dict[str, object]]) -> None:
        if self._operation_active:
            self._set_lianli_status("联力操作仍在进行")
            return
        self._set_lianli_busy(True, busy_message)

        def worker() -> None:
            try:
                result = operation()
                if not self._closed:
                    self.operation_finished.emit("完成", result)
            except Exception as error:  # noqa: BLE001
                if not self._closed:
                    self.operation_finished.emit("失败", error)

        thread = threading.Thread(target=worker, daemon=True)
        self._threads.append(thread)
        thread.start()

    def _operation_finished(self, message: str, result: object) -> None:
        self._set_lianli_busy(False, "")
        if isinstance(result, Exception):
            self._set_lianli_status(f"{message}：{result}")
            return
        if isinstance(result, dict) and result.get("operation") == "linux-control-write-gate":
            self.apply_lianli_write_gate(result)
            message = self._write_gate_status_text(result)
        if isinstance(result, dict) and result.get("operation") == "summarize-experiments":
            message = self._apply_receiver_next_action(result)
        self.lianli_status_label.setText(message)
        self.lianli_snapshot_text.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        self.status_changed.emit(self.home_status_text())

    def _apply_receiver_next_action(self, payload: dict[str, object]) -> str:
        action = payload.get("receiver_control_next_action")
        if not isinstance(action, dict):
            text = "下一步：汇总完成，但没有接收器控制建议"
            self.lianli_next_action_label.setText(text)
            return "实验汇总完成"
        text = self._receiver_next_action_text(action)
        self.lianli_next_action_label.setText(text)
        ready_candidates = [
            candidate
            for candidate in action.get("candidates", [])
            if isinstance(candidate, dict) and candidate.get("status") == "ready"
        ] if isinstance(action.get("candidates"), list) else []
        action_ready = (
            str(action.get("status") or "") == "ready-for-single-target-safe-pwm"
            and bool(action.get("can_run_safe_pwm"))
        )
        if action_ready and len(ready_candidates) == 1 and not self.lianli_mac_input.text().strip():
            mac = str(ready_candidates[0].get("mac") or "").strip()
            if mac:
                self.lianli_mac_input.setText(mac)
        return text

    def _receiver_next_action_text(self, action: dict[str, object]) -> str:
        status = str(action.get("status") or "unknown")
        candidates = action.get("candidates")
        ready_macs = [
            str(candidate.get("mac") or "")
            for candidate in candidates
            if isinstance(candidate, dict) and candidate.get("status") == "ready" and candidate.get("mac")
        ] if isinstance(candidates, list) else []
        messages = {
            "ready-for-single-target-safe-pwm": "下一步：写入门禁已通过，可对一个已绑定 MAC 运行安全 PWM",
            "ready-for-safe-lighting-validation": "下一步：PWM 已确认，可一次运行一个安全灯光实验",
            "ready-for-pairing-risk-review": "下一步：PWM 和灯光已确认，配对/解绑只进入风险复核",
            "write-validation-needs-observation": "下一步：已有写入机器日志，先补观察记录",
            "write-validation-conflict": "下一步：写入证据存在冲突，先复盘 receiver-evidence-report",
            "write-validation-incomplete": "下一步：写入证据不完整，先补齐 before/write/after/analysis",
            "write-validation-already-observed": "下一步：已经有安全写入实验记录，先复盘结果再继续扩展",
            "validation-errors": "下一步：验证日志有错误，先查看 validation_errors",
            "needs-bound-target": "下一步：门禁已通过，但 live-list 没有可写的已绑定 MAC",
            "needs-receiver-validation-bundle": "下一步：先运行 receiver-validation-bundle",
            "needs-live-list": "下一步：先刷新 live-list 接收器快照",
            "needs-write-gate": "下一步：写入门禁未通过，继续补抓包/packet compare",
            "receiver-identity-conflict": "下一步：接收器身份日志互相矛盾，先重新采集 receiver-validation-bundle",
            "needs-receiver-identity-validation": "下一步：接收器身份日志不完整，先重新采集 receiver-validation-bundle",
        }
        text = messages.get(status, f"下一步：{status}")
        if ready_macs:
            text += "：" + ", ".join(ready_macs[:3])
        return text

    def _set_lianli_busy(self, active: bool, message: str) -> None:
        self._operation_active = active
        for button in (
            self.scan_lianli_button,
            self.live_lianli_button,
            self.master_lianli_button,
            self.lcd_info_button,
            self.validate_lianli_button,
            self.save_lianli_snapshot_button,
            self.analyze_lianli_log_button,
            self.diff_lianli_snapshots_button,
            self.summarize_lianli_experiments_button,
            self.lianli_write_gate_button,
        ):
            button.setEnabled(not active)
        self._update_write_controls()
        if message:
            self._set_lianli_status(message)

    def home_status_text(self) -> str:
        text = self.lianli_status_label.text().strip() or "未连接"
        snapshot = self.lianli_snapshot_text.toPlainText().strip()
        if snapshot:
            try:
                payload = json.loads(snapshot)
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                count = payload.get("device_count")
                operation = payload.get("operation")
                if isinstance(count, int):
                    if operation == "scan-usb":
                        return "未连接" if count == 0 else f"USB {count} 设备"
                    if operation == "live-list":
                        return f"接收器 {count} 个"
        if text.startswith("USB 扫描完成"):
            return text.removeprefix("USB 扫描完成：")
        if text == "默认只读":
            return "未连接"
        return text

    def _set_lianli_status(self, text: str) -> None:
        self.lianli_status_label.setText(text)
        self.status_changed.emit(self.home_status_text())

    def apply_lianli_write_gate(self, payload: dict[str, object]) -> None:
        self._lianli_write_gate_payload = dict(payload)
        self._update_write_controls()

    def _write_unlocked(self) -> bool:
        return (
            self.lianli_write_enable.isChecked()
            and self.lianli_confirm_input.text().strip() == LIANLI_WRITE_CONFIRM_TOKEN
            and self._write_gate_unlocked()
        )

    def _write_gate_unlocked(self) -> bool:
        if not self.require_write_gate:
            return True
        return bool(
            self._lianli_write_gate_payload
            and self._lianli_write_gate_payload.get("allows_any_guarded_write")
        )

    def _write_blocked_text(self) -> str:
        if not self.lianli_write_enable.isChecked() or self.lianli_confirm_input.text().strip() != LIANLI_WRITE_CONFIRM_TOKEN:
            return "写入未启用或确认令牌不正确"
        if self.require_write_gate and not self._write_gate_unlocked():
            return "写入门禁未通过：请先点击“写入门禁”，完成官方抓包对比后再写入"
        return "写入未启用或确认令牌不正确"

    def _update_write_controls(self) -> None:
        enabled = (not self._operation_active) and self._write_unlocked()
        for button in (
            self.lianli_pwm_button,
            self.lianli_safe_pwm_button,
            self.lianli_pwm_sync_button,
            self.lianli_safe_sync_button,
            self.lianli_pwm_mirror_button,
            self.lianli_safe_mirror_button,
            self.lianli_rgb_off_button,
            self.lianli_safe_rgb_button,
            self.lianli_rainbow_button,
            self.lianli_safe_rainbow_button,
            self.lianli_safe_bind_button,
            self.lianli_safe_unbind_button,
            self.lianli_lcd_control_button,
        ):
            button.setEnabled(enabled)
        if hasattr(self, "lianli_lcd_info_button"):
            self.lianli_lcd_info_button.setEnabled(not self._operation_active)
        if hasattr(self, "lianli_write_gate_label"):
            self.lianli_write_gate_label.setText(self._write_gate_label_text())

    def _write_gate_label_text(self) -> str:
        if not self.require_write_gate:
            return "写入门禁：当前实例未强制；真实 GUI 会要求门禁通过后再写入。"
        payload = self._lianli_write_gate_payload
        if not payload:
            return (
                "写入门禁：未检查。需要先完成官方抓包、packet preview/compare，"
                "再点击“写入门禁”；未通过前写入按钮保持锁定。"
            )
        return self._write_gate_status_text(payload)

    def _write_gate_status_text(self, payload: dict[str, object]) -> str:
        status = str(payload.get("status") or "unknown")
        ready = int(payload.get("ready_action_count") or 0)
        blocked = int(payload.get("blocked_action_count") or 0)
        next_command = str(payload.get("next_command") or "")
        status_text = {
            "write-enabled": f"写入门禁通过 [{status}]：{ready} 个安全实验已允许写入",
            "needs-packet-compare": f"写入门禁未通过 [{status}]：需要先运行 packet preview/compare",
            "refresh-live-snapshot": f"写入门禁未通过 [{status}]：需要刷新 live-list 快照后重新对比",
            "needs-recompare-after-refresh": f"写入门禁未通过 [{status}]：需要用刷新后的快照重新 packet compare",
            "incomplete-packet-compare": f"写入门禁未通过 [{status}]：仍有抓包对比未通过",
            "packet-compare-failed": f"写入门禁未通过 [{status}]：packet compare 失败",
            "invalid-packet-compare-schema": f"写入门禁未通过 [{status}]：对比产物 schema 已过期",
            "blocked-by-preflight": f"写入门禁阻塞 [{status}]：硬件、权限或抓包证据不足",
            "needs-capture-evidence": f"写入门禁未通过 [{status}]：需要先补官方 L-Connect 抓包证据",
        }.get(status, f"写入门禁状态 [{status}]：ready {ready} / blocked {blocked}")
        if blocked and status == "write-enabled":
            status_text += f"，另有 {blocked} 个动作仍阻塞"
        if next_command and status != "write-enabled":
            status_text += f"；下一步：{next_command}"
        return status_text



class LightingPage(QWidget):
    operation_finished = Signal(str, object)
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
        self.effect_map = {
            "关闭": "off",
            "静态": "static",
            "呼吸": "breathing",
            "彩虹": "rainbow",
            "光谱": "spectrum",
            "追逐": "chase",
            "星空": "star",
            "Direct": "direct",
        }
        self.selected_color = self.settings.lighting.color
        self._lighting_operation_active = False
        self._lighting_closed = False
        self._lighting_threads: list[threading.Thread] = []
        self._selected_scene_name = self.settings.lighting.active_scene
        self._argb_wizard_targets: list[LightingTarget] = []
        self._argb_wizard_restore: list[LightingSettings] = []
        self._argb_wizard_index = -1
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
        self.openrgb_status_label = QLabel("未连接")
        self.openrgb_status_label.setObjectName("FieldHint")
        self.openrgb_status_label.setWordWrap(True)
        layout.addWidget(title, 0, 0)
        layout.addWidget(QLabel("地址"), 0, 1)
        layout.addWidget(self.openrgb_host_input, 0, 2)
        layout.addWidget(QLabel("端口"), 0, 3)
        layout.addWidget(self.openrgb_port_input, 0, 4)
        layout.addWidget(self.connect_lighting_button, 0, 5)
        layout.addWidget(self.refresh_lighting_button, 0, 6)
        layout.addWidget(self.identify_lighting_button, 0, 7)
        layout.addWidget(self.openrgb_status_label, 1, 0, 1, 8)
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
        effect_panel.setMaximumHeight(128)
        preset_panel.setMaximumHeight(118)
        action_panel.setMaximumHeight(96)
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
        for index, name in enumerate(("关闭", "静态", "呼吸", "彩虹", "光谱", "追逐", "Direct")):
            button = QPushButton(name)
            button.setCheckable(True)
            button.setObjectName("SegmentButton")
            if name == "关闭":
                button.setChecked(True)
            self.effect_group.addButton(button, index)
            effect_grid.addWidget(button, index // 4, index % 4)
        layout.addLayout(effect_grid)
        return panel

    def _color_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("MetricCard")
        layout = QVBoxLayout(panel)
        title = QLabel("颜色与速度")
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
        target_button_row = QHBoxLayout()
        self.save_target_profile_button = QPushButton("保存区域配置")
        self.save_target_profile_button.clicked.connect(self.save_current_target_profile)
        self.load_target_profile_button = QPushButton("加载区域配置")
        self.load_target_profile_button.clicked.connect(self.load_current_target_profile)
        self.identify_argb_button = QPushButton("识别所有 ARGB")
        self.identify_argb_button.clicked.connect(self.identify_argb_targets)
        self.save_next_argb_button = QPushButton("保存并下一个")
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
        layout.addLayout(target_button_row, 2, 0, 1, 5)
        layout.addWidget(self.openrgb_modes_text, 0, 5, 3, 1)
        layout.setColumnStretch(2, 2)
        layout.setColumnStretch(5, 1)
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
        layout.addWidget(title)
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
        settings_grid.addWidget(QLabel("高温阈值"), 0, 0)
        settings_grid.addWidget(self.temperature_limit, 0, 1)
        settings_grid.addWidget(QLabel("灯珠数量"), 1, 0)
        settings_grid.addWidget(self.argb_zone_size, 1, 1)
        layout.addLayout(settings_grid)
        self.save_mode_checkbox = QCheckBox("保存到 OpenRGB 设备配置")
        self.save_mode_checkbox.setChecked(self.settings.lighting.save_mode)
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

    def connect_openrgb(self) -> None:
        self.controller.host = self.openrgb_host_input.text().strip() or "127.0.0.1"
        self.controller.port = self.openrgb_port_input.value()
        self.settings.openrgb.host = self.controller.host
        self.settings.openrgb.port = self.controller.port
        save_settings(self.settings)
        self._run_lighting_operation(
            "正在连接 OpenRGB...",
            "openrgb_connect_failed",
            lambda: self._connect_from_worker(),
        )

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

    def load_current_target_profile(self) -> None:
        target_id = str(self.lighting_target_combo.currentData() or "")
        if not target_id:
            self.openrgb_status_label.setText("请先选择灯光目标")
            return
        if not self._restore_target_profile(target_id):
            self.openrgb_status_label.setText("这个区域还没有保存配置")
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
            self.openrgb_status_label.setText("没有可保存的区域配置")
            return
        self.settings.lighting.scenes[name] = {"targets": profiles}
        self.settings.lighting.active_scene = name
        self._selected_scene_name = name
        save_settings(self.settings)
        self._refresh_scene_names(name)
        self._update_scene_summary()
        self.openrgb_status_label.setText(f"场景已保存：{name}")

    def apply_saved_lighting_scene(self) -> None:
        name = self.scene_combo.currentText().strip()
        scene = self.settings.lighting.scenes.get(name)
        if not isinstance(scene, dict):
            self.openrgb_status_label.setText("请选择已保存的场景")
            return
        self._run_lighting_operation(
            f"正在应用场景：{name}",
            "openrgb_scene_apply_failed",
            lambda: self._apply_scene_from_worker(name, scene),
        )

    def rename_lighting_scene(self) -> None:
        old_name = self._selected_scene_name or self.settings.lighting.active_scene
        new_name = self.scene_combo.currentText().strip()
        if not old_name or old_name not in self.settings.lighting.scenes:
            self.openrgb_status_label.setText("请选择要重命名的场景")
            return
        if not new_name:
            self.openrgb_status_label.setText("请输入新的场景名称")
            return
        if new_name != old_name and new_name in self.settings.lighting.scenes:
            self.openrgb_status_label.setText("场景名称已存在")
            return
        self.settings.lighting.scenes[new_name] = self.settings.lighting.scenes.pop(old_name)
        if self.settings.lighting.active_scene == old_name:
            self.settings.lighting.active_scene = new_name
        self._selected_scene_name = new_name
        save_settings(self.settings)
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
        self._run_lighting_operation(
            f"正在识别 ARGB 1/{len(targets)}...",
            "openrgb_identify_argb_failed",
            lambda: self._flash_argb_wizard_target_from_worker(targets[0]),
        )

    def save_argb_alias_and_continue(self) -> None:
        if not self._argb_wizard_targets or self._argb_wizard_index < 0:
            self.openrgb_status_label.setText("请先开始 ARGB 识别")
            return
        self._save_current_target_alias()
        index = self._argb_wizard_index
        self._run_lighting_operation(
            "正在保存并进入下一个 ARGB 区域...",
            "openrgb_identify_argb_failed",
            lambda: self._advance_argb_wizard_from_worker(index),
        )

    def refresh_openrgb_targets(self) -> None:
        self._run_lighting_operation(
            "正在刷新 OpenRGB 设备...",
            "openrgb_refresh_failed",
            lambda: self._set_targets_from_worker(self.controller.refresh(), "已刷新"),
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
        target_id = str(self.lighting_target_combo.currentData() or "")
        if not target_id and not self.controller.connected:
            settings = self._current_lighting_settings("")
            self._run_lighting_operation(
                "正在连接 OpenRGB 并应用灯效...",
                "openrgb_connect_apply_failed",
                lambda: self._connect_and_apply_from_worker(settings),
            )
            return
        if not target_id:
            self.openrgb_status_label.setText("请先选择灯光目标")
            return
        try:
            settings = self._current_lighting_settings(target_id)
        except Exception as error:
            self.openrgb_status_label.setText(f"参数错误：{error}")
            return

        self._run_lighting_operation(
            "正在应用灯效...",
            "openrgb_apply_failed",
            lambda: self._apply_from_worker(settings),
        )

    def turn_off_all_lighting(self) -> None:
        self._set_effect("off")
        self.set_selected_color("#000000")
        self.brightness_slider.setValue(0)
        self.sync_mode_combo.setCurrentIndex(0)
        self._run_lighting_operation(
            "正在关闭所有灯光...",
            "openrgb_turn_off_all_failed",
            lambda: self._turn_off_all_lighting_from_worker(),
        )

    def _run_lighting_operation(self, busy_message: str, error_event: str, operation: Callable[[], str]) -> None:
        if self._lighting_operation_active:
            self.openrgb_status_label.setText("灯效操作正在执行...")
            return

        self._set_lighting_busy(True, busy_message)

        def worker() -> None:
            try:
                message = operation()
            except Exception as error:  # pragma: no cover - delivered through signal
                log_exception(error_event, error)
                if not self._lighting_closed:
                    self.operation_finished.emit("", error)
                return
            if not self._lighting_closed:
                self.operation_finished.emit(message, None)

        thread = threading.Thread(target=worker, name="usb9-lcd-openrgb", daemon=True)
        self._lighting_threads.append(thread)
        thread.start()

    def _connect_from_worker(self) -> str:
        if self.settings.openrgb.auto_start_server:
            self.server_manager = OpenRgbServerManager(
                self.settings.openrgb.app_path,
                host=self.controller.host,
                port=self.controller.port,
            )
            self.server_manager.ensure_running()
        targets = self.controller.connect()
        self._set_targets_from_worker(targets, "已连接")
        return f"已连接，发现 {len(targets)} 个目标，默认保持关闭"

    def _set_targets_from_worker(self, targets: list[LightingTarget], prefix: str) -> str:
        self.targets = targets
        return f"{prefix}，发现 {len(targets)} 个目标"

    def _apply_from_worker(self, settings: LightingSettings) -> str:
        self.controller.apply(settings)
        self._remember_lighting_settings(settings.target_id)
        return "灯效已应用"

    def _turn_off_all_lighting_from_worker(self) -> str:
        if self.settings.openrgb.auto_start_server:
            self.server_manager.ensure_running()
        targets = self.controller.refresh() if self.controller.connected else self.controller.connect()
        self.targets = targets
        if not targets:
            return "OpenRGB 未发现灯光设备"
        targets_to_disable = [target for target in targets if target.zone_index is None] or targets
        for target in targets_to_disable:
            self.controller.apply(
                LightingSettings(
                    target_id=target.id,
                    effect="off",
                    color="#000000",
                    brightness_percent=0,
                    speed_percent=0,
                    zone_size=self.argb_zone_size.value(),
                    save=False,
                )
            )
        return f"所有灯光已关闭：{len(targets_to_disable)} 个目标"

    def _connect_and_apply_from_worker(self, settings: LightingSettings) -> str:
        if self.settings.openrgb.auto_start_server:
            self.server_manager.ensure_running()
        targets = self.controller.connect()
        if not targets:
            self.targets = []
            return "OpenRGB 未发现灯光设备"
        target_id = settings.target_id or targets[0].id
        resolved = LightingSettings(
            target_id=target_id,
            effect=settings.effect,
            color=settings.color,
            brightness_percent=settings.brightness_percent,
            speed_percent=settings.speed_percent,
            zone_size=settings.zone_size,
            save=settings.save,
        )
        self.targets = targets
        self.controller.apply(resolved)
        self._remember_lighting_settings(target_id)
        return f"已连接并应用灯效，发现 {len(targets)} 个目标"

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
        return self._apply_scene_payload(scene, targets) > 0

    def _apply_scene_from_worker(self, name: str, scene: dict) -> str:
        if not self.controller.connected:
            if self.settings.openrgb.auto_start_server:
                self.server_manager.ensure_running()
            targets = self.controller.connect()
            self.targets = targets
        else:
            targets = self.targets or self.controller.refresh()
        applied_count = self._apply_scene_payload(scene, targets)
        if not applied_count:
            return f"场景没有匹配当前 OpenRGB 目标：{name}"
        self.settings.lighting.active_scene = name
        self._selected_scene_name = name
        save_settings(self.settings)
        return f"场景已应用：{name}，{applied_count} 个区域"

    def _apply_scene_payload(self, scene: dict, targets: list[LightingTarget]) -> int:
        target_ids = {target.id for target in targets}
        profiles = scene.get("targets")
        if not isinstance(profiles, dict):
            return 0
        applied_count = 0
        for target_id, profile in profiles.items():
            if target_id not in target_ids or not isinstance(profile, dict):
                continue
            self.controller.apply(self._settings_from_profile(str(target_id), profile))
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

    def _flash_argb_wizard_target_from_worker(self, target: LightingTarget) -> str:
        restore = self._argb_wizard_restore[self._argb_wizard_index]
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
        return f"ARGB 向导：当前 {self._argb_wizard_index + 1}/{len(self._argb_wizard_targets)}，输入名称后点保存并下一个"

    def _advance_argb_wizard_from_worker(self, index: int) -> str:
        if index >= len(self._argb_wizard_restore):
            return "ARGB 命名完成"
        self.controller.apply(self._argb_wizard_restore[index])
        next_index = index + 1
        if next_index >= len(self._argb_wizard_targets):
            self._argb_wizard_index = -1
            return "ARGB 命名完成"
        self._argb_wizard_index = next_index
        target = self._argb_wizard_targets[next_index]
        restore = self._argb_wizard_restore[next_index]
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
        return f"ARGB 向导：当前 {next_index + 1}/{len(self._argb_wizard_targets)}，输入名称后点保存并下一个"

    def _lighting_operation_finished(self, message: str, error: object) -> None:
        self._set_lighting_busy(False, "")
        if error is not None:
            self.openrgb_status_label.setText(f"操作失败：{error}")
            self.status_changed.emit(self.home_status_text())
            return
        if message.startswith(("已连接", "已刷新", "场景已应用", "所有灯光已关闭")):
            self._set_targets(self.targets)
        if message.startswith("ARGB 向导："):
            self._sync_argb_wizard_ui()
        if message == "ARGB 命名完成":
            self.save_next_argb_button.setVisible(False)
            self._argb_wizard_targets = []
            self._argb_wizard_restore = []
        self.openrgb_status_label.setText(message)
        self.status_changed.emit(self.home_status_text())

    def _set_lighting_busy(self, active: bool, message: str) -> None:
        self._lighting_operation_active = active
        for widget in (
            self.connect_lighting_button,
            self.refresh_lighting_button,
            self.identify_lighting_button,
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
        for label, value in self.effect_map.items():
            if value == effect:
                return label
        return effect

    def _apply_lighting_sync_for_tests(self, settings: LightingSettings) -> None:
        try:
            self.controller.apply(settings)
        except Exception as error:
            log_exception("openrgb_apply_failed", error)
            self.openrgb_status_label.setText(f"应用失败：{error}")
            return
        self.openrgb_status_label.setText("灯效已应用")

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

    def _update_lighting_slider_labels(self) -> None:
        if hasattr(self, "brightness_value_label"):
            self.brightness_value_label.setText(f"{self.brightness_slider.value()}%")
        if hasattr(self, "speed_value_label"):
            self.speed_value_label.setText(f"{self.speed_slider.value() * 10}%")

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
            if target.zone_index is not None and ("argb" in target.name.lower() or "addressable" in target.name.lower())
        ]
        if candidates:
            return candidates
        return [target for target in self.targets if target.zone_index is not None]

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
        self.lighting_target_combo.clear()
        if not targets:
            self.lighting_target_combo.addItem("OpenRGB 未发现灯光设备", "")
            self.openrgb_modes_text.setPlainText("")
            self._update_scene_summary()
            return
        for target in targets:
            self.lighting_target_combo.addItem(self._target_display_name(target), target.id)
        remembered = self.settings.lighting.target_id
        if remembered:
            for index in range(self.lighting_target_combo.count()):
                if self.lighting_target_combo.itemData(index) == remembered:
                    self.lighting_target_combo.setCurrentIndex(index)
                    break
        self._target_changed(self.lighting_target_combo.currentIndex())
        self._update_scene_summary()

    def _target_changed(self, index: int) -> None:
        target_id = str(self.lighting_target_combo.itemData(index) or "")
        target = next((item for item in self.targets if item.id == target_id), None)
        if target is None:
            self.openrgb_modes_text.setPlainText("")
            self.target_alias_input.setText("")
            return
        self.target_alias_input.setText(self.settings.lighting.target_aliases.get(target.id, ""))
        self.openrgb_modes_text.setPlainText("可用模式：\n" + "\n".join(target.modes))

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
        current = self.lighting_target_combo.currentIndex()
        self._set_targets(self.targets)
        self.lighting_target_combo.setCurrentIndex(current)
        self._update_scene_summary()

    def _target_display_name(self, target: LightingTarget) -> str:
        alias = self.settings.lighting.target_aliases.get(target.id, "")
        return f"{alias} · {target.name}" if alias else target.name

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self.release_lighting_resources()
        super().closeEvent(event)

    def release_lighting_resources(self) -> None:
        self._lighting_closed = True
        for thread in list(self._lighting_threads):
            if thread.is_alive():
                thread.join(timeout=1.0)


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
        subtitle = QLabel("管理本地图片和 GIF 素材，预览后可直接发送到小屏幕")
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
            self._media_assets = self.asset_library.list_media()
            media_lines = [
                f"{asset.path.name} | {asset.width}x{asset.height} | {asset.kind} | "
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
                f"{asset.path.name}\n{asset.width}x{asset.height} | "
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
