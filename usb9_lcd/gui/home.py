from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from usb9_lcd.drivers.base import DisplayDevice
from usb9_lcd.monitoring.models import FanTelemetry, SystemTelemetry


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

        layout.addWidget(self._hero_panel(sleep_all_off))
        layout.addWidget(self._metric_grid())
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

    def _hero_panel(self, sleep_all_off: Callable[[], None]) -> QFrame:
        panel = QFrame()
        panel.setObjectName("HomeHeroPanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(10)

        title = QLabel("Lumen Hub 控制中心")
        title.setObjectName("PageTitle")
        subtitle = QLabel("屏幕、风扇、灯效与联力无线的实时硬件指挥台")
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        self.mode_value = QLabel("日常")
        self.mode_value.setObjectName("StatusPill")

        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box, 0, 0, 1, 3)
        layout.addWidget(self.mode_value, 0, 3)

        self.mode_group = QButtonGroup(self)
        modes = (
            ("日常", "屏幕监控、风扇自动、灯效默认", None),
            ("游戏", "性能优先，保留监控画面", None),
            ("静音", "低噪声策略，降低灯光亮度", None),
            ("睡眠", "黑屏并关闭所有灯光", sleep_all_off),
        )
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

    def _metric_grid(self) -> QFrame:
        wrapper = QFrame()
        wrapper.setObjectName("HomeMetricGrid")
        overview = QGridLayout(wrapper)
        overview.setContentsMargins(0, 0, 0, 0)
        overview.setSpacing(12)

        self.device_value = QLabel("未发现设备")
        self.cpu_value = QLabel("CPU --")
        self.gpu_value = QLabel("GPU --")
        self._fan_status_text = "未加载"
        self._fan_rpm_text = ""
        self.fan_value = QLabel(self._fan_status_text)
        self.lighting_value = QLabel("默认关闭")
        self.lianli_value = QLabel("未连接")
        self.permission_value = QLabel("权限未检查")
        self.device_tree_value = QLabel("设备树未生成")

        overview.addWidget(self._status_card("LCD", self.device_value, "screen"), 0, 0, 1, 2)
        overview.addWidget(self._status_card("CPU", self.cpu_value, "cpu"), 0, 2)
        overview.addWidget(self._status_card("GPU", self.gpu_value, "gpu"), 0, 3)
        overview.addWidget(self._status_card("风扇", self.fan_value, "fan"), 1, 0, 1, 2)
        overview.addWidget(self._status_card("灯效", self.lighting_value, "lighting"), 1, 2)
        overview.addWidget(self._status_card("联力无线", self.lianli_value, "lianli"), 1, 3)
        overview.addWidget(self._status_card("设备树", self.device_tree_value, "device-tree"), 2, 0, 1, 2)
        overview.addWidget(self._status_card("权限", self.permission_value, "permission"), 2, 2, 1, 2)
        return wrapper

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
        panel.setObjectName("HomeCommandDock")
        layout = QGridLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        title = QLabel("快捷操作")
        title.setObjectName("SectionLabel")
        columns = 5
        layout.addWidget(title, 0, 0, 1, columns)
        actions: tuple[tuple[str, str, Callable[[], None], bool], ...] = (
            ("safety", "睡眠全关", sleep_all_off, False),
            ("screen", "发送监控", upload_monitor, True),
            ("screen", "打开屏幕", lambda: navigate("screen"), True),
            ("screen", "素材库", lambda: navigate("assets"), False),
            ("fan", "打开风扇", lambda: navigate("fan"), True),
            ("fan", "扫描风扇", load_fan_control, False),
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
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        title = QLabel("最近事件")
        title.setObjectName("SectionLabel")
        layout.addWidget(title)
        self.event_labels = []
        for _index in range(6):
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
            self.device_value.setText("未发现设备")
            return
        writable = "可写" if device.connection.writable else "只读"
        self.device_value.setText(f"{device.display_name}\n{device.width}x{device.height} | {writable}")

    def update_telemetry(self, telemetry: SystemTelemetry | None) -> None:
        if telemetry is None:
            self.cpu_value.setText("CPU 不可用")
            self.gpu_value.setText("GPU 不可用")
            self._fan_rpm_text = ""
            self._refresh_fan_value()
            return
        cpu = "--" if telemetry.cpu.package_temperature_c is None else f"{telemetry.cpu.package_temperature_c:.0f}°C"
        cpu_load = "--" if telemetry.cpu.utilization_percent is None else f"{telemetry.cpu.utilization_percent:.0f}%"
        cpu_power = "--" if telemetry.cpu.power_w is None else f"{telemetry.cpu.power_w:.0f}W"
        gpu = "--" if telemetry.gpu.temperature_c is None else f"{telemetry.gpu.temperature_c:.0f}°C"
        gpu_load = "--" if telemetry.gpu.utilization_percent is None else f"{telemetry.gpu.utilization_percent:.0f}%"
        gpu_power = "--" if telemetry.gpu.power_w is None else f"{telemetry.gpu.power_w:.0f}W"
        gpu_fan = self._gpu_fan_summary(telemetry)
        self.cpu_value.setText(f"温度 {cpu}\n负载 {cpu_load}\n功耗 {cpu_power}")
        self.gpu_value.setText(f"温度 {gpu}\n负载 {gpu_load}\n功耗 {gpu_power}\n风扇 {gpu_fan}")
        self._fan_rpm_text = self._fan_rpm_summary(telemetry)
        self._refresh_fan_value()

    def update_fan_status(self, text: str) -> None:
        self._fan_status_text = text or "未加载"
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
            percent = "" if fan.percent is None else f" / {fan.percent:.0f}%"
            parts.append(f"{fan.name} {fan.rpm} RPM{percent}")
        if len(fans) > 4:
            parts.append(f"+{len(fans) - 4} 个")
        return "转速 " + " · ".join(parts)

    def _gpu_fan_summary(self, telemetry: SystemTelemetry) -> str:
        gpu_fan = next(
            (
                fan
                for fan in telemetry.fans
                if self._has_gpu_fan_speed(fan)
            ),
            None,
        )
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
        return (
            fan.available
            and self._is_gpu_fan_name(fan.name)
            and (fan.rpm is not None or fan.percent is not None)
        )

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

    def _status_card(self, title: str, value: QLabel, role: str = "") -> QFrame:
        card = QFrame()
        card.setObjectName("HomeStatusCard")
        if role:
            card.setProperty("statusRole", role)
        card.setMinimumHeight(96)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(15, 13, 15, 13)
        card_layout.setSpacing(7)
        title_label = QLabel(title)
        title_label.setObjectName("SectionLabel")
        value.setObjectName("HomeMetricValue")
        value.setWordWrap(True)
        card_layout.addWidget(title_label)
        card_layout.addWidget(value, 1)
        return card
