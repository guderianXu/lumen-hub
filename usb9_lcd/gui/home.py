from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
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
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(self._hero_panel())
        layout.addWidget(self._metric_grid())
        layout.addWidget(self._fan_strategy_panel())
        layout.addWidget(self._subsystem_strip())

        lower = QGridLayout()
        lower.setContentsMargins(0, 0, 0, 0)
        lower.setHorizontalSpacing(12)
        lower.addWidget(
            self._command_panel(
                navigate,
                upload_monitor,
                load_fan_control,
                connect_lighting,
                sleep_all_off,
            ),
            0,
            0,
            1,
            2,
        )
        lower.addWidget(self._event_panel(), 0, 2)
        lower.setColumnStretch(0, 1)
        lower.setColumnStretch(1, 1)
        lower.setColumnStretch(2, 1)
        layout.addLayout(lower)
        layout.addStretch(1)
        self.add_event("控制中心已就绪")

    def _hero_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("HomeHeroPanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(9)

        title = QLabel("系统控制面板")
        title.setObjectName("PageTitle")
        self.telemetry_time_value = QLabel("等待硬件遥测")
        self.telemetry_time_value.setObjectName("PageSubtitle")
        self.mode_value = QLabel("日常")
        self.mode_value.setObjectName("StatusPill")

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_box.addWidget(title)
        title_box.addWidget(self.telemetry_time_value)
        layout.addLayout(title_box, 0, 0, 1, 5)
        layout.addWidget(self.mode_value, 0, 5)

        scene_label = QLabel("全局场景")
        scene_label.setObjectName("SectionLabel")
        layout.addWidget(scene_label, 1, 0)

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
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("SegmentButton")
            button.setToolTip(description)
            button.setProperty("modeAction", label)
            button.setProperty("sceneKey", key)
            if index == 0:
                button.setChecked(True)
            button.clicked.connect(
                lambda _checked=False, name=label, scene_key=key: self._set_scene_mode(name, scene_key)
            )
            self.scene_buttons.append(button)
            self.mode_group.addButton(button, index)
            layout.addWidget(button, 1, index + 1)
        layout.setColumnStretch(0, 0)
        for column in range(1, 7):
            layout.setColumnStretch(column, 1)
        return panel

    def _metric_grid(self) -> QFrame:
        wrapper = QFrame()
        wrapper.setObjectName("HomeMetricGrid")
        overview = QGridLayout(wrapper)
        overview.setContentsMargins(0, 0, 0, 0)
        overview.setSpacing(10)

        self.cpu_value = QLabel("温度 --\n负载 --\n功耗 --")
        self.gpu_value = QLabel("温度 --\n负载 --\n功耗 --\n频率 --")
        self.fan_value = QLabel(self._fan_status_text)
        self.device_value = QLabel("未发现设备")

        self.cpu_temp_bar = self._metric_bar("temperature")
        self.cpu_load_bar = self._metric_bar("load")
        self.gpu_temp_bar = self._metric_bar("temperature")
        self.gpu_load_bar = self._metric_bar("load")

        overview.addWidget(
            self._telemetry_card("CPU", self.cpu_value, "cpu", (self.cpu_temp_bar, self.cpu_load_bar)), 0, 0
        )
        overview.addWidget(
            self._telemetry_card("GPU", self.gpu_value, "gpu", (self.gpu_temp_bar, self.gpu_load_bar)), 0, 1
        )
        overview.addWidget(self._telemetry_card("风扇转速", self.fan_value, "fan"), 0, 2)
        overview.addWidget(self._telemetry_card("显示设备", self.device_value, "screen"), 0, 3)
        for column in range(4):
            overview.setColumnStretch(column, 1)
        return wrapper

    def _fan_strategy_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("HomeFanProfilePanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)

        title = QLabel("风扇策略")
        title.setObjectName("SectionLabel")
        self.fan_strategy_summary = QLabel("标准自动曲线")
        self.fan_strategy_summary.setObjectName("FieldHint")
        self.fan_strategy_value = QLabel("标准")
        self.fan_strategy_value.setObjectName("StatusPill")
        layout.addWidget(title, 0, 0)
        layout.addWidget(self.fan_strategy_summary, 0, 1, 1, 4)
        layout.addWidget(self.fan_strategy_value, 0, 5)

        self.fan_strategy_group = QButtonGroup(self)
        self.fan_strategy_group.setExclusive(True)
        self.fan_strategy_buttons: dict[str, QPushButton] = {}
        strategies = (
            ("quiet", "安静", "低噪声温控曲线"),
            ("normal", "标准", "日常均衡温控曲线"),
            ("high", "高速", "高负载散热曲线"),
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
        details.setObjectName("SecondaryButton")
        details.setProperty("moduleAction", "打开风扇")
        details.setProperty("commandGroup", "fan")
        details.clicked.connect(lambda: self._navigate("fan"))
        layout.addWidget(details, 1, 5)
        for column in range(6):
            layout.setColumnStretch(column, 1)
        self.set_fan_strategy("normal", enabled=False)
        return panel

    def _subsystem_strip(self) -> QFrame:
        wrapper = QFrame()
        wrapper.setObjectName("HomeSubsystemStrip")
        layout = QGridLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.lighting_value = QLabel("默认关闭")
        self.lianli_value = QLabel("未连接")
        self.permission_value = QLabel("权限未检查")
        self.device_tree_value = QLabel("设备树未生成")
        cards = (
            ("灯效", self.lighting_value, "lighting"),
            ("联力无线", self.lianli_value, "lianli"),
            ("设备状态", self.device_tree_value, "device-tree"),
            ("权限", self.permission_value, "permission"),
        )
        for index, (title, value, role) in enumerate(cards):
            layout.addWidget(self._compact_status_card(title, value, role), 0, index)
            layout.setColumnStretch(index, 1)
        return wrapper

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
        except Exception as error:  # noqa: BLE001 - surface backend failures without breaking the dashboard.
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
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setHorizontalSpacing(7)
        layout.setVerticalSpacing(7)
        title = QLabel("快捷操作")
        title.setObjectName("SectionLabel")
        columns = 5
        layout.addWidget(title, 0, 0, 1, columns)
        actions: tuple[tuple[str, str, Callable[[], None], bool], ...] = (
            ("safety", "睡眠全关", sleep_all_off, False),
            ("screen", "发送监控", upload_monitor, True),
            ("screen", "打开屏幕", lambda: navigate("screen"), True),
            ("screen", "素材库", lambda: navigate("assets"), False),
            ("fan", "扫描风扇", load_fan_control, False),
            ("fan", "打开风扇", lambda: navigate("fan"), True),
            ("lighting", "打开灯效", lambda: navigate("lighting"), True),
            ("lighting", "连接灯效", connect_lighting, False),
            ("lianli", "打开联力", lambda: navigate("lianli"), True),
            ("lianli", "读取联力状态", lambda: navigate("lianli"), False),
        )
        for index, (group, label, action, primary) in enumerate(actions):
            button = QPushButton(label)
            button.setProperty("moduleAction", label)
            button.setProperty("commandGroup", group)
            if label == "睡眠全关":
                button.setObjectName("DangerButton")
            elif primary:
                button.setObjectName("PrimaryButton")
            else:
                button.setObjectName("SecondaryButton")
            button.clicked.connect(action)
            layout.addWidget(button, 1 + index // columns, index % columns)
        return panel

    def _event_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("HomeTimelinePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(4)
        title = QLabel("最近事件")
        title.setObjectName("SectionLabel")
        layout.addWidget(title)
        self.event_labels = []
        for _index in range(4):
            label = QLabel("等待操作")
            label.setObjectName("TimelineItem")
            label.setWordWrap(True)
            self.event_labels.append(label)
            layout.addWidget(label)
        return panel

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
            return
        writable = "可写" if device.connection.writable else "只读"
        self.device_value.setText(f"{device.display_name}\n{device.width}x{device.height} · {writable}")

    def update_telemetry(self, telemetry: SystemTelemetry | None) -> None:
        if telemetry is None:
            self.cpu_value.setText("温度 --\n负载 --\n功耗 --")
            self.gpu_value.setText("温度 --\n负载 --\n功耗 --\n频率 --")
            self.telemetry_time_value.setText("硬件遥测不可用")
            for bar in (self.cpu_temp_bar, self.cpu_load_bar, self.gpu_temp_bar, self.gpu_load_bar):
                bar.setValue(0)
            self._fan_rpm_text = ""
            self._refresh_fan_value()
            return
        cpu = "--" if telemetry.cpu.package_temperature_c is None else f"{telemetry.cpu.package_temperature_c:.0f}°C"
        cpu_load = "--" if telemetry.cpu.utilization_percent is None else f"{telemetry.cpu.utilization_percent:.0f}%"
        cpu_power = "--" if telemetry.cpu.power_w is None else f"{telemetry.cpu.power_w:.0f}W"
        gpu = "--" if telemetry.gpu.temperature_c is None else f"{telemetry.gpu.temperature_c:.0f}°C"
        gpu_load = "--" if telemetry.gpu.utilization_percent is None else f"{telemetry.gpu.utilization_percent:.0f}%"
        gpu_power = "--" if telemetry.gpu.power_w is None else f"{telemetry.gpu.power_w:.0f}W"
        gpu_clock = "--" if telemetry.gpu.graphics_clock_mhz is None else f"{telemetry.gpu.graphics_clock_mhz}MHz"
        gpu_fan = self._gpu_fan_summary(telemetry)
        self.cpu_value.setText(f"温度 {cpu}\n负载 {cpu_load}\n功耗 {cpu_power}")
        self.gpu_value.setText(f"温度 {gpu}\n负载 {gpu_load}\n功耗 {gpu_power}\n频率 {gpu_clock}\n风扇 {gpu_fan}")
        self.cpu_temp_bar.setValue(self._bar_value(telemetry.cpu.package_temperature_c))
        self.cpu_load_bar.setValue(self._bar_value(telemetry.cpu.utilization_percent))
        self.gpu_temp_bar.setValue(self._bar_value(telemetry.gpu.temperature_c))
        self.gpu_load_bar.setValue(self._bar_value(telemetry.gpu.utilization_percent))
        self.telemetry_time_value.setText(f"实时硬件状态 · 更新于 {telemetry.captured_at:%H:%M:%S}")
        self._fan_rpm_text = self._fan_rpm_summary(telemetry)
        self._refresh_fan_value()

    def update_fan_status(self, text: str) -> None:
        self._fan_status_text = text or "未扫描"
        self._refresh_fan_value()

    def _refresh_fan_value(self) -> None:
        lines = [self._fan_status_text]
        if self._fan_rpm_text:
            lines.append(self._fan_rpm_text)
        self.fan_value.setText("\n".join(line for line in lines if line))

    def _fan_rpm_summary(self, telemetry: SystemTelemetry) -> str:
        fans = [fan for fan in telemetry.fans if fan.available and fan.rpm is not None]
        if not fans:
            return "转速 --"
        parts: list[str] = []
        for fan in fans[:4]:
            percent = "" if fan.percent is None else f" · {fan.percent:.0f}%"
            parts.append(f"{fan.name} {fan.rpm} RPM{percent}")
        if len(fans) > 4:
            parts.append(f"另有 {len(fans) - 4} 个风扇")
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

    def _telemetry_card(
        self,
        title: str,
        value: QLabel,
        role: str,
        bars: tuple[QProgressBar, ...] = (),
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("HomeStatusCard")
        card.setProperty("statusRole", role)
        card.setMinimumHeight(154)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(6)
        title_label = QLabel(title)
        title_label.setObjectName("HomeMetricTitle")
        value.setObjectName("HomeMetricValue")
        value.setWordWrap(True)
        card_layout.addWidget(title_label)
        card_layout.addWidget(value, 1)
        for bar in bars:
            card_layout.addWidget(bar)
        return card

    def _compact_status_card(self, title: str, value: QLabel, role: str) -> QFrame:
        card = QFrame()
        card.setObjectName("HomeStatusCard")
        card.setProperty("statusRole", role)
        card.setMinimumHeight(74)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("SectionLabel")
        value.setObjectName("HomeCompactValue")
        value.setWordWrap(True)
        card_layout.addWidget(title_label)
        card_layout.addWidget(value, 1)
        return card

    def _metric_bar(self, role: str) -> QProgressBar:
        bar = QProgressBar()
        bar.setObjectName("HomeMetricBar")
        bar.setProperty("metricRole", role)
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(5)
        return bar

    def _bar_value(self, value: float | int | None) -> int:
        if value is None:
            return 0
        return max(0, min(100, round(float(value))))
