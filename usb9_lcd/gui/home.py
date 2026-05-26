from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from usb9_lcd.drivers.base import DisplayDevice
from usb9_lcd.monitoring.models import SystemTelemetry


class ControlCenterPage(QWidget):
    def __init__(
        self,
        navigate: Callable[[str], None],
        upload_monitor: Callable[[], None],
        load_fan_control: Callable[[], None],
        connect_lighting: Callable[[], None],
        sleep_all_off: Callable[[], None],
    ) -> None:
        super().__init__()
        self._events: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(16)

        header_row = QHBoxLayout()
        title_box = QVBoxLayout()
        header = QLabel("控制中心")
        header.setObjectName("PageTitle")
        subtitle = QLabel("整机状态、运行模式和睡前一键关闭集中在这里")
        subtitle.setObjectName("PageSubtitle")
        title_box.addWidget(header)
        title_box.addWidget(subtitle)
        header_row.addLayout(title_box, 1)
        self.mode_value = QLabel("日常")
        self.mode_value.setObjectName("DeviceBadge")
        header_row.addWidget(self.mode_value)
        layout.addLayout(header_row)

        layout.addWidget(self._mode_panel(sleep_all_off))

        overview = QGridLayout()
        overview.setSpacing(12)
        self.device_value = QLabel("未发现设备")
        self.cpu_value = QLabel("CPU --")
        self.gpu_value = QLabel("GPU --")
        self.fan_value = QLabel("未加载")
        self.lighting_value = QLabel("默认关闭")
        self.lianli_value = QLabel("未连接")
        overview.addWidget(self._status_card("LCD 设备", self.device_value), 0, 0)
        overview.addWidget(self._status_card("CPU", self.cpu_value), 0, 1)
        overview.addWidget(self._status_card("GPU", self.gpu_value), 0, 2)
        overview.addWidget(self._status_card("风扇", self.fan_value), 1, 0)
        overview.addWidget(self._status_card("灯效", self.lighting_value), 1, 1)
        overview.addWidget(self._status_card("联力无线", self.lianli_value), 1, 2)
        layout.addLayout(overview)
        layout.addWidget(
            self._command_panel(
                navigate,
                upload_monitor,
                load_fan_control,
                connect_lighting,
                sleep_all_off,
            )
        )
        layout.addWidget(self._event_panel())
        layout.addStretch(1)
        self.add_event("控制中心已就绪")

    def _mode_panel(self, sleep_all_off: Callable[[], None]) -> QFrame:
        panel = QFrame()
        panel.setObjectName("MetricCard")
        layout = QGridLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        title = QLabel("运行模式")
        title.setObjectName("SectionLabel")
        hint = QLabel("选择当前使用场景，睡眠模式会立即关闭屏幕和灯光")
        hint.setObjectName("FieldHint")
        self.mode_group = QButtonGroup(self)
        modes = (
            ("日常", "屏幕监控、风扇自动、灯效默认关闭", None),
            ("游戏", "GPU 联动、性能优先、保留监控画面", None),
            ("静音", "低噪声策略、灯光低亮度或关闭", None),
            ("睡眠", "黑屏并关闭所有灯光", sleep_all_off),
        )
        layout.addWidget(title, 0, 0)
        layout.addWidget(hint, 0, 1, 1, len(modes) - 1)
        for index, (label, description, action) in enumerate(modes):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("SegmentButton")
            button.setToolTip(description)
            button.setProperty("modeAction", label)
            if index == 0:
                button.setChecked(True)
            button.clicked.connect(
                lambda _checked=False, name=label, callback=action: self._set_mode(name, callback)
            )
            self.mode_group.addButton(button, index)
            layout.addWidget(button, 1, index)
        return panel

    def _set_mode(self, mode: str, action: Callable[[], None] | None = None) -> None:
        self.set_mode_indicator(mode)
        self.add_event(f"切换到{mode}模式")
        if action is not None:
            action()

    def set_mode_indicator(self, mode: str) -> None:
        self.mode_value.setText(mode)
        if not hasattr(self, "mode_group"):
            return
        for button in self.mode_group.buttons():
            button.setChecked(button.property("modeAction") == mode)

    def _command_panel(
        self,
        navigate: Callable[[str], None],
        upload_monitor: Callable[[], None],
        load_fan_control: Callable[[], None],
        connect_lighting: Callable[[], None],
        sleep_all_off: Callable[[], None],
    ) -> QFrame:
        panel = QFrame()
        panel.setObjectName("MetricCard")
        layout = QGridLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        title = QLabel("快捷操作")
        title.setObjectName("SectionLabel")
        columns = 4
        layout.addWidget(title, 0, 0, 1, columns)
        actions: tuple[tuple[str, Callable[[], None], bool], ...] = (
            ("睡眠全关", sleep_all_off, False),
            ("上传一次", upload_monitor, True),
            ("打开监控", lambda: navigate("monitor"), True),
            ("打开风扇", lambda: navigate("fan"), True),
            ("扫描风扇", load_fan_control, False),
            ("打开灯效", lambda: navigate("lighting"), True),
            ("连接灯效", connect_lighting, False),
            ("打开上传", lambda: navigate("upload"), True),
            ("素材库", lambda: navigate("assets"), False),
            ("打开联力", lambda: navigate("lianli"), True),
            ("读取状态", lambda: navigate("lianli"), False),
        )
        for index, (label, action, primary) in enumerate(actions):
            button = QPushButton(label)
            button.setProperty("moduleAction", label)
            if label == "睡眠全关":
                button.setObjectName("DangerButton")
            elif primary:
                button.setObjectName("PrimaryButton")
            button.clicked.connect(action)
            layout.addWidget(button, 1 + index // columns, index % columns)
        return panel

    def _event_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("MetricCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        title = QLabel("最近事件")
        title.setObjectName("SectionLabel")
        layout.addWidget(title)
        self.event_labels = []
        for _index in range(4):
            label = QLabel("等待操作")
            label.setObjectName("ChecklistItem")
            label.setWordWrap(True)
            self.event_labels.append(label)
            layout.addWidget(label)
        return panel

    def add_event(self, message: str) -> None:
        self._events.insert(0, message)
        self._events = self._events[:4]
        if not hasattr(self, "event_labels"):
            return
        for index, label in enumerate(self.event_labels):
            label.setText(self._events[index] if index < len(self._events) else "等待操作")

    def update_device(self, device: DisplayDevice | None) -> None:
        if device is None:
            self.device_value.setText("未发现设备")
            return
        writable = "可写" if device.connection.writable else "只读"
        self.device_value.setText(f"{device.display_name}\n{device.width}x{device.height} · {writable}")

    def update_telemetry(self, telemetry: SystemTelemetry | None) -> None:
        if telemetry is None:
            self.cpu_value.setText("CPU 不可用")
            self.gpu_value.setText("GPU 不可用")
            return
        cpu = "--" if telemetry.cpu.package_temperature_c is None else f"{telemetry.cpu.package_temperature_c:.0f}°C"
        cpu_load = "--" if telemetry.cpu.utilization_percent is None else f"{telemetry.cpu.utilization_percent}%"
        gpu = "--" if telemetry.gpu.temperature_c is None else f"{telemetry.gpu.temperature_c:.0f}°C"
        gpu_load = "--" if telemetry.gpu.utilization_percent is None else f"{telemetry.gpu.utilization_percent}%"
        self.cpu_value.setText(f"{cpu}\n{cpu_load}")
        self.gpu_value.setText(f"{gpu}\n{gpu_load}")

    def update_fan_status(self, text: str) -> None:
        self.fan_value.setText(text or "未加载")

    def update_lighting_status(self, text: str) -> None:
        self.lighting_value.setText(text or "默认关闭")

    def update_lianli_status(self, text: str) -> None:
        self.lianli_value.setText(text or "未连接")

    def _status_card(self, title: str, value: QLabel) -> QFrame:
        card = QFrame()
        card.setObjectName("MetricCard")
        card.setMinimumHeight(104)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(6)
        title_label = QLabel(title)
        title_label.setObjectName("SectionLabel")
        value.setObjectName("HomeMetricValue")
        value.setWordWrap(True)
        card_layout.addWidget(title_label)
        card_layout.addWidget(value, 1)
        return card
