from __future__ import annotations

import platform
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from usb9_lcd.drivers.base import DisplayDevice
from usb9_lcd.gui.fan_curve_model import (
    FAN_CURVE_CUSTOM_PRESET,
    fan_curve_preset_label,
    normalize_fan_curve_preset,
)
from usb9_lcd.monitoring.models import FanTelemetry, SystemTelemetry


class ControlCenterPage(QWidget):
    def __init__(
        self,
        navigate: Callable[[str], None],
        upload_monitor: Callable[[], None],
        load_fan_control: Callable[[], None],
        connect_lighting: Callable[[], None],
        sleep_all_off: Callable[[], None],
        apply_scene: Callable[[str], object] | None = None,
        apply_fan_preset: Callable[[str], object] | None = None,
    ) -> None:
        super().__init__()
        self._events: list[str] = []
        self._navigate = navigate
        self._apply_scene = apply_scene or (lambda _scene_key: None)
        self._apply_fan_preset = apply_fan_preset or (lambda _preset: None)
        self._fan_status_text = "未扫描"
        self._fan_rpm_text = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 30, 6)
        layout.setSpacing(14)
        layout.addWidget(self._hero_panel())

        dashboard = QGridLayout()
        dashboard.setContentsMargins(0, 0, 0, 0)
        dashboard.setHorizontalSpacing(34)
        dashboard.setVerticalSpacing(16)
        dashboard.addWidget(
            self._device_column(
                upload_monitor,
                load_fan_control,
                connect_lighting,
                sleep_all_off,
            ),
            0,
            0,
            2,
            1,
        )
        dashboard.addWidget(self._metric_grid(), 0, 1)
        dashboard.addWidget(self._fan_strategy_panel(), 1, 1)
        dashboard.setColumnMinimumWidth(0, 300)
        dashboard.setColumnStretch(0, 0)
        dashboard.setColumnStretch(1, 1)
        dashboard.setRowStretch(0, 1)
        layout.addLayout(dashboard, 1)
        self.add_event("控制中心已就绪")

    def _hero_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("HomeHeroPanel")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(12)

        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        title = QLabel("控制面板")
        title.setObjectName("PageTitle")
        self.telemetry_time_value = QLabel("等待硬件遥测")
        self.telemetry_time_value.setObjectName("PageSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(self.telemetry_time_value)
        layout.addLayout(title_box)
        layout.addStretch(1)

        scene_caption = QLabel("当前情境")
        scene_caption.setObjectName("HomeHeaderCaption")
        self.mode_value = QLabel("日常")
        self.mode_value.setObjectName("StatusPill")
        layout.addWidget(scene_caption)
        layout.addWidget(self.mode_value)

        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.scene_buttons: list[QPushButton] = []
        scenes = (
            ("daily", "日常", "屏幕监控、风扇自动、灯效默认"),
            ("gaming", "游戏", "性能优先，保留监控画面"),
            ("quiet", "静音", "低噪声策略，降低灯光亮度"),
            ("sleep", "睡眠", "黑屏并关闭所有灯光"),
            ("showcase", "展示", "亮灯展示整机状态"),
            ("temperature-warning", "温度警告", "红色警示灯效和高风扇曲线"),
        )
        for index, (key, label, description) in enumerate(scenes):
            button = QPushButton(label, panel)
            button.setCheckable(True)
            button.setToolTip(description)
            button.setProperty("modeAction", label)
            button.setProperty("sceneKey", key)
            button.setChecked(index == 0)
            button.clicked.connect(
                lambda _checked=False, name=label, scene_key=key: self._set_scene_mode(name, scene_key)
            )
            button.hide()
            self.scene_buttons.append(button)
            self.mode_group.addButton(button, index)
        return panel

    def _device_column(
        self,
        upload_monitor: Callable[[], None],
        load_fan_control: Callable[[], None],
        connect_lighting: Callable[[], None],
        sleep_all_off: Callable[[], None],
    ) -> QWidget:
        column = QWidget()
        column.setObjectName("HomeDeviceColumn")
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._device_overview_panel(), 1)
        layout.addWidget(
            self._command_panel(
                self._navigate,
                upload_monitor,
                load_fan_control,
                connect_lighting,
                sleep_all_off,
            )
        )
        layout.addWidget(self._event_panel())
        return column

    def _device_overview_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("HomeDeviceOverview")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 4, 10, 6)
        layout.setSpacing(5)

        image = QLabel()
        image.setObjectName("HomeHardwareImage")
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setFixedHeight(178)
        pixmap = QPixmap(str(self._dashboard_asset("monitor_backgrounds", "rog_red_grid.png")))
        if pixmap.isNull():
            image.setText("LCD")
        else:
            image.setPixmap(
                pixmap.scaled(
                    170,
                    170,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        layout.addWidget(image)

        product = QLabel("LUMEN HUB")
        product.setObjectName("HomeDeviceProduct")
        product.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(product)
        self.device_value = QLabel("未发现 LCD 设备")
        self.device_value.setObjectName("HomeDeviceIdentity")
        self.device_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.device_value.setWordWrap(True)
        layout.addWidget(self.device_value)

        platform_value = QLabel(f"{platform.system()}  /  {platform.machine()}")
        platform_value.setObjectName("HomePlatformIdentity")
        platform_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(platform_value)
        return panel

    def _metric_grid(self) -> QFrame:
        wrapper = QFrame()
        wrapper.setObjectName("HomeMetricGrid")
        overview = QGridLayout(wrapper)
        overview.setContentsMargins(0, 0, 0, 0)
        overview.setHorizontalSpacing(28)
        overview.setVerticalSpacing(22)

        monitor_label = QLabel("///  系统监控")
        monitor_label.setObjectName("HomeMonitorKicker")
        overview.addWidget(monitor_label, 0, 0, 1, 6)

        self.cpu_value = QLabel("温度 --\n负载 --\n功耗 --")
        self.cpu_value.hide()
        self.gpu_value = QLabel("温度 --\n负载 --\n功耗 --\n频率 --")
        self.gpu_value.hide()
        overview.addWidget(self.cpu_value, 0, 0)
        overview.addWidget(self.gpu_value, 0, 0)

        self.gpu_clock_display = self._metric_value("-- MHz")
        self.gpu_memory_display = self._metric_value("-- / -- MB")
        self.refresh_display = self._metric_value("等待遥测")
        frequency = self._monitor_section(
            "频率",
            (
                ("GPU Clock", self.gpu_clock_display, None),
                ("GPU Memory", self.gpu_memory_display, None),
                ("采样状态", self.refresh_display, None),
            ),
            "cpu",
        )
        overview.addWidget(frequency, 1, 0, 1, 3)

        self.cpu_temp_display = self._metric_value("-- °C")
        self.gpu_temp_display = self._metric_value("-- °C")
        self.lcd_status_display = self._metric_value("未发现")
        self.cpu_temp_bar = self._metric_bar("cpu")
        self.gpu_temp_bar = self._metric_bar("gpu")
        temperature = self._monitor_section(
            "温度",
            (
                ("CPU Package", self.cpu_temp_display, self.cpu_temp_bar),
                ("GPU", self.gpu_temp_display, self.gpu_temp_bar),
                ("LCD", self.lcd_status_display, None),
            ),
            "temperature",
        )
        overview.addWidget(temperature, 1, 3, 1, 3)

        self.cpu_load_display = self._metric_value("-- %")
        self.gpu_load_display = self._metric_value("-- %")
        self.vram_usage_display = self._metric_value("-- %")
        self.cpu_load_bar = self._metric_bar("cpu")
        self.gpu_load_bar = self._metric_bar("gpu")
        self.vram_usage_bar = self._metric_bar("vram")
        usage = self._monitor_section(
            "使用率",
            (
                ("CPU", self.cpu_load_display, self.cpu_load_bar),
                ("GPU", self.gpu_load_display, self.gpu_load_bar),
                ("VRAM", self.vram_usage_display, self.vram_usage_bar),
            ),
            "usage",
        )
        overview.addWidget(usage, 2, 0, 1, 2)

        self.fan_value = QLabel(self._fan_status_text)
        self.fan_value.setObjectName("HomeFanListValue")
        self.fan_value.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.fan_value.setWordWrap(True)
        fan_section = self._monitor_text_section("风扇", self.fan_value, "fan")
        overview.addWidget(fan_section, 2, 2, 1, 2)

        self.cpu_power_display = self._metric_value("-- W")
        self.gpu_power_display = self._metric_value("-- W")
        self.total_power_display = self._metric_value("-- W")
        power = self._monitor_section(
            "功耗",
            (
                ("CPU Package", self.cpu_power_display, None),
                ("GPU", self.gpu_power_display, None),
                ("合计", self.total_power_display, None),
            ),
            "power",
        )
        overview.addWidget(power, 2, 4, 1, 2)

        for column in range(6):
            overview.setColumnStretch(column, 1)
        overview.setRowStretch(1, 1)
        overview.setRowStretch(2, 1)
        return wrapper

    def _monitor_section(
        self,
        title: str,
        rows: tuple[tuple[str, QLabel, QProgressBar | None], ...],
        role: str,
    ) -> QFrame:
        section = QFrame()
        section.setObjectName("HomeMonitorSection")
        section.setProperty("monitorRole", role)
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        heading = QLabel(title)
        heading.setObjectName("HomeMonitorTitle")
        layout.addWidget(heading)
        for label, value, bar in rows:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            name = QLabel(label)
            name.setObjectName("HomeMetricName")
            row.addWidget(name)
            row.addStretch(1)
            row.addWidget(value)
            layout.addLayout(row)
            if bar is not None:
                layout.addWidget(bar)
        layout.addStretch(1)
        return section

    def _monitor_text_section(self, title: str, value: QLabel, role: str) -> QFrame:
        section = QFrame()
        section.setObjectName("HomeMonitorSection")
        section.setProperty("monitorRole", role)
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("HomeMonitorTitle")
        layout.addWidget(heading)
        layout.addWidget(value, 1)
        return section

    def _fan_strategy_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("HomeFanProfilePanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)

        title = QLabel("///  风扇模式")
        title.setObjectName("HomeMonitorKicker")
        self.fan_strategy_summary = QLabel("日常均衡自动曲线")
        self.fan_strategy_summary.setObjectName("FieldHint")
        self.fan_strategy_value = QLabel("标准")
        self.fan_strategy_value.setObjectName("HomeFanModeState")
        layout.addWidget(title, 0, 0)
        layout.addWidget(self.fan_strategy_summary, 0, 1, 1, 3)
        layout.addWidget(self.fan_strategy_value, 0, 5)

        self.fan_strategy_group = QButtonGroup(self)
        self.fan_strategy_group.setExclusive(True)
        self.fan_strategy_buttons: dict[str, QPushButton] = {}
        strategies = (
            ("quiet", "安静", "低噪声温控曲线"),
            ("normal", "标准", "日常均衡温控曲线"),
            ("high", "加速", "高负载散热曲线"),
            ("full", "全速", "固定 100% 输出"),
            (FAN_CURVE_CUSTOM_PRESET, "自定义", "进入风扇页编辑曲线"),
        )
        for index, (key, label, description) in enumerate(strategies):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("FanStrategyButton")
            button.setToolTip(description)
            button.setProperty("fanPreset", key)
            button.clicked.connect(lambda _checked=False, preset=key: self._activate_fan_strategy(preset))
            self.fan_strategy_group.addButton(button, index)
            self.fan_strategy_buttons[key] = button
            layout.addWidget(button, 1, index)
        details = QPushButton("曲线设置")
        details.setObjectName("HomeFanSettingsButton")
        details.setProperty("moduleAction", "打开风扇")
        details.setProperty("commandGroup", "fan")
        details.clicked.connect(lambda: self._navigate("fan"))
        layout.addWidget(details, 1, 5)
        for column in range(6):
            layout.setColumnStretch(column, 1)
        self.set_fan_strategy("normal", enabled=False)
        return panel

    def _command_panel(
        self,
        navigate: Callable[[str], None],
        upload_monitor: Callable[[], None],
        load_fan_control: Callable[[], None],
        connect_lighting: Callable[[], None],
        sleep_all_off: Callable[[], None],
    ) -> QFrame:
        panel = QFrame()
        panel.setObjectName("HomeCommandDock")
        layout = QGridLayout(panel)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)
        title = QLabel("///  设备")
        title.setObjectName("HomeMonitorKicker")
        layout.addWidget(title, 0, 0, 1, 2)

        self.device_tile_value = QLabel("未发现")
        self.lighting_value = QLabel("默认关闭")
        self.lianli_value = QLabel("未连接")
        self.fan_tile_value = QLabel("未扫描")
        actions: tuple[tuple[str, str, str, QLabel, Callable[[], None]], ...] = (
            ("screen", "LCD 屏幕", "打开屏幕", self.device_tile_value, lambda: navigate("screen")),
            ("fan", "风扇控制", "打开风扇", self.fan_tile_value, lambda: navigate("fan")),
            ("lighting", "灯效同步", "打开灯效", self.lighting_value, lambda: navigate("lighting")),
            ("lianli", "联力无线", "打开联力", self.lianli_value, lambda: navigate("lianli")),
        )
        for index, (group, label, module_action, status, action) in enumerate(actions):
            tile = self._device_tile(group, label, module_action, status, action)
            layout.addWidget(tile, 1 + index // 2, index % 2)

        sleep_button = QPushButton("睡眠全关")
        sleep_button.setObjectName("HomeSleepButton")
        sleep_button.setProperty("moduleAction", "睡眠全关")
        sleep_button.setProperty("commandGroup", "safety")
        sleep_button.clicked.connect(sleep_all_off)
        layout.addWidget(sleep_button, 3, 0)
        monitor_button = QPushButton("发送监控")
        monitor_button.setObjectName("HomeDeviceAction")
        monitor_button.setProperty("moduleAction", "发送监控")
        monitor_button.setProperty("commandGroup", "screen")
        monitor_button.clicked.connect(upload_monitor)
        layout.addWidget(monitor_button, 3, 1)

        scan_button = QPushButton("扫描风扇", panel)
        scan_button.setProperty("moduleAction", "扫描风扇")
        scan_button.setProperty("commandGroup", "fan")
        scan_button.clicked.connect(load_fan_control)
        scan_button.hide()
        connect_button = QPushButton("连接灯效", panel)
        connect_button.setProperty("moduleAction", "连接灯效")
        connect_button.setProperty("commandGroup", "lighting")
        connect_button.clicked.connect(connect_lighting)
        connect_button.hide()
        assets_button = QPushButton("素材库", panel)
        assets_button.setProperty("moduleAction", "素材库")
        assets_button.setProperty("commandGroup", "screen")
        assets_button.clicked.connect(lambda: navigate("assets"))
        assets_button.hide()
        return panel

    def _device_tile(
        self,
        group: str,
        title: str,
        module_action: str,
        value: QLabel,
        action: Callable[[], None],
    ) -> QFrame:
        tile = QFrame()
        tile.setObjectName("HomeDeviceTile")
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.setSpacing(1)
        button = QPushButton(title)
        button.setObjectName("HomeDeviceButton")
        button.setProperty("moduleAction", module_action)
        button.setProperty("commandGroup", group)
        button.clicked.connect(action)
        value.setObjectName("HomeDeviceTileStatus")
        value.setWordWrap(True)
        layout.addWidget(button)
        layout.addWidget(value)
        return tile

    def _event_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("HomeTimelinePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(3)
        header = QHBoxLayout()
        title = QLabel("最近状态")
        title.setObjectName("HomeMinorTitle")
        self.permission_value = QLabel("权限未检查")
        self.permission_value.setObjectName("HomeInlineStatus")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.permission_value)
        layout.addLayout(header)
        self.device_tree_value = QLabel("设备树未生成")
        self.device_tree_value.setObjectName("HomeInlineStatus")
        self.device_tree_value.setWordWrap(True)
        layout.addWidget(self.device_tree_value)
        self.event_labels: list[QLabel] = []
        for _index in range(3):
            label = QLabel("等待操作")
            label.setObjectName("TimelineItem")
            label.setWordWrap(True)
            self.event_labels.append(label)
            layout.addWidget(label)
        return panel

    def _set_scene_mode(self, mode: str, scene_key: str) -> None:
        self.set_mode_indicator(mode)
        self.add_event(f"切换到{mode}场景")
        self._apply_scene(scene_key)

    def set_mode_indicator(self, mode: str) -> None:
        self.mode_value.setText(mode)
        if not hasattr(self, "mode_group"):
            return
        for button in self.mode_group.buttons():
            button.setChecked(button.property("modeAction") == mode)

    def _activate_fan_strategy(self, preset: str) -> None:
        key = normalize_fan_curve_preset(preset)
        if key == FAN_CURVE_CUSTOM_PRESET:
            self.set_fan_strategy(key, enabled=True)
            self.add_event("打开自定义风扇曲线")
            self._navigate("fan")
            return
        try:
            applied = self._apply_fan_preset(key)
        except Exception as error:  # noqa: BLE001 - keep dashboard interaction responsive.
            self.add_event(f"风扇策略应用失败：{error}")
            return
        self.set_fan_strategy(key, enabled=True)
        label = fan_curve_preset_label(key)
        self.add_event(f"风扇策略已切换：{label}" if applied is not False else f"已选择{label}，等待可控风扇")

    def set_fan_strategy(self, preset: object, *, enabled: bool = True) -> None:
        key = normalize_fan_curve_preset(preset)
        label = fan_curve_preset_label(key)
        summaries = {
            "quiet": "低噪声自动曲线",
            "normal": "日常均衡自动曲线",
            "high": "高负载散热曲线",
            "full": "固定 100% 输出",
            FAN_CURVE_CUSTOM_PRESET: "自定义温控曲线",
        }
        self.fan_strategy_value.setText(label if enabled else f"{label} · 未启用")
        self.fan_strategy_summary.setText(summaries[key])
        for preset_key, button in self.fan_strategy_buttons.items():
            button.setChecked(preset_key == key)

    def add_event(self, message: str) -> None:
        self._events.insert(0, message)
        self._events = self._events[:6]
        if not hasattr(self, "event_labels"):
            return
        for index, label in enumerate(self.event_labels):
            label.setText(self._events[index] if index < len(self._events) else "等待操作")

    def update_device(self, device: DisplayDevice | None) -> None:
        if device is None:
            self.device_value.setText("未发现 LCD 设备")
            self.device_tile_value.setText("未发现")
            self.lcd_status_display.setText("未发现")
            return
        writable = "可写" if device.connection.writable else "只读"
        self.device_value.setText(f"{device.display_name}\n{device.width}x{device.height}  /  {writable}")
        self.device_tile_value.setText(f"{device.width}x{device.height}  {writable}")
        self.lcd_status_display.setText(writable)

    def update_telemetry(self, telemetry: SystemTelemetry | None) -> None:
        if telemetry is None:
            self.cpu_value.setText("温度 --\n负载 --\n功耗 --")
            self.gpu_value.setText("温度 --\n负载 --\n功耗 --\n频率 --")
            self.telemetry_time_value.setText("硬件遥测不可用")
            self.refresh_display.setText("不可用")
            for display in (
                self.cpu_temp_display,
                self.gpu_temp_display,
                self.cpu_load_display,
                self.gpu_load_display,
                self.vram_usage_display,
                self.cpu_power_display,
                self.gpu_power_display,
                self.total_power_display,
            ):
                display.setText("--")
            self.gpu_clock_display.setText("-- MHz")
            self.gpu_memory_display.setText("-- / -- MB")
            for bar in (
                self.cpu_temp_bar,
                self.gpu_temp_bar,
                self.cpu_load_bar,
                self.gpu_load_bar,
                self.vram_usage_bar,
            ):
                bar.setValue(0)
            self._fan_rpm_text = ""
            self._refresh_fan_value()
            return

        cpu_temp = self._display_value(telemetry.cpu.package_temperature_c, "°C", digits=0)
        cpu_load = self._display_value(telemetry.cpu.utilization_percent, "%", digits=0)
        cpu_power = self._display_value(telemetry.cpu.power_w, "W", digits=0)
        gpu_temp = self._display_value(telemetry.gpu.temperature_c, "°C", digits=0)
        gpu_load = self._display_value(telemetry.gpu.utilization_percent, "%", digits=0)
        gpu_power = self._display_value(telemetry.gpu.power_w, "W", digits=0)
        gpu_clock = "--" if telemetry.gpu.graphics_clock_mhz is None else str(telemetry.gpu.graphics_clock_mhz)
        gpu_fan = self._gpu_fan_summary(telemetry)

        self.cpu_value.setText(f"温度 {cpu_temp}\n负载 {cpu_load}\n功耗 {cpu_power}")
        self.gpu_value.setText(
            f"温度 {gpu_temp}\n负载 {gpu_load}\n功耗 {gpu_power}\n频率 {gpu_clock}MHz\n风扇 {gpu_fan}"
        )
        self.cpu_temp_display.setText(cpu_temp)
        self.gpu_temp_display.setText(gpu_temp)
        self.cpu_load_display.setText(cpu_load)
        self.gpu_load_display.setText(gpu_load)
        self.cpu_power_display.setText(cpu_power)
        self.gpu_power_display.setText(gpu_power)
        self.gpu_clock_display.setText(f"{gpu_clock} MHz")

        memory_used = telemetry.gpu.memory_used_mb
        memory_total = telemetry.gpu.memory_total_mb
        if memory_used is None or memory_total in {None, 0}:
            self.gpu_memory_display.setText("-- / -- MB")
            self.vram_usage_display.setText("-- %")
            self.vram_usage_bar.setValue(0)
        else:
            memory_percent = round(memory_used / memory_total * 100)
            self.gpu_memory_display.setText(f"{memory_used} / {memory_total} MB")
            self.vram_usage_display.setText(f"{memory_percent}%")
            self.vram_usage_bar.setValue(self._bar_value(memory_percent))

        known_power = [value for value in (telemetry.cpu.power_w, telemetry.gpu.power_w) if value is not None]
        self.total_power_display.setText(f"{sum(known_power):.0f} W" if known_power else "-- W")
        self.cpu_temp_bar.setValue(self._bar_value(telemetry.cpu.package_temperature_c))
        self.gpu_temp_bar.setValue(self._bar_value(telemetry.gpu.temperature_c))
        self.cpu_load_bar.setValue(self._bar_value(telemetry.cpu.utilization_percent))
        self.gpu_load_bar.setValue(self._bar_value(telemetry.gpu.utilization_percent))
        self.refresh_display.setText(telemetry.captured_at.strftime("%H:%M:%S"))
        self.telemetry_time_value.setText(f"实时硬件状态  /  更新于 {telemetry.captured_at:%H:%M:%S}")
        self._fan_rpm_text = self._fan_rpm_summary(telemetry)
        self._refresh_fan_value()

    def update_fan_status(self, text: str) -> None:
        self._fan_status_text = text or "未扫描"
        self._refresh_fan_value()

    def _refresh_fan_value(self) -> None:
        lines = [self._fan_status_text]
        if self._fan_rpm_text:
            lines.append(self._fan_rpm_text)
        value = "\n".join(line for line in lines if line)
        self.fan_value.setText(value)
        self.fan_tile_value.setText(self._fan_status_text.splitlines()[0] if self._fan_status_text else "未扫描")

    def _fan_rpm_summary(self, telemetry: SystemTelemetry) -> str:
        fans = [fan for fan in telemetry.fans if fan.available and fan.rpm is not None]
        if not fans:
            return "转速 --"
        parts: list[str] = []
        for fan in fans[:5]:
            percent = "" if fan.percent is None else f" · {fan.percent:.0f}%"
            parts.append(f"{fan.name} {fan.rpm} RPM{percent}")
        if len(fans) > 5:
            parts.append(f"另有 {len(fans) - 5} 个风扇")
        return "\n".join(parts)

    def _gpu_fan_summary(self, telemetry: SystemTelemetry) -> str:
        gpu_fan = next((fan for fan in telemetry.fans if self._has_gpu_fan_speed(fan)), None)
        if gpu_fan is not None:
            if gpu_fan.rpm is not None:
                return f"{gpu_fan.rpm} RPM"
            if gpu_fan.percent is not None:
                return f"{gpu_fan.percent:.0f}%"
        if telemetry.gpu.fan_speed_percent is not None:
            return f"{telemetry.gpu.fan_speed_percent:.0f}%"
        return "--"

    def _is_gpu_fan_name(self, name: str) -> bool:
        text = name.lower()
        return any(marker in text for marker in ("gpu", "nvidia", "geforce", "rtx", "radeon", "arc"))

    def _has_gpu_fan_speed(self, fan: FanTelemetry) -> bool:
        return fan.available and self._is_gpu_fan_name(fan.name) and (fan.rpm is not None or fan.percent is not None)

    def update_lighting_status(self, text: str) -> None:
        self.lighting_value.setText(text or "默认关闭")

    def update_lianli_status(self, text: str) -> None:
        self.lianli_value.setText(text or "未连接")

    def update_permission_status(self, text: str) -> None:
        self.permission_value.setText(text or "权限未检查")

    def update_device_tree_status(self, text: str) -> None:
        self.device_tree_value.setText(text or "设备树未生成")

    def recent_events(self) -> list[str]:
        return list(self._events)

    def _metric_value(self, text: str) -> QLabel:
        value = QLabel(text)
        value.setObjectName("HomeMetricValue")
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return value

    def _metric_bar(self, role: str) -> QProgressBar:
        bar = QProgressBar()
        bar.setObjectName("HomeMetricBar")
        bar.setProperty("metricRole", role)
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(3)
        return bar

    def _bar_value(self, value: float | int | None) -> int:
        if value is None:
            return 0
        return max(0, min(100, round(float(value))))

    def _display_value(self, value: float | int | None, suffix: str, *, digits: int) -> str:
        if value is None:
            return f"-- {suffix}" if suffix else "--"
        return f"{float(value):.{digits}f}{suffix}"

    def _dashboard_asset(self, *parts: str) -> Path:
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        return root.joinpath("assets", *parts)
