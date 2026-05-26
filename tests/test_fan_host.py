from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_fan_host_display_mapping_keeps_backend_fields_and_classifies_roles(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6798", encoding="utf-8")
    labels = {
        1: "CPU_OPT",
        2: "AIO Pump",
        3: "CHA_FAN1",
        4: "",
    }
    for index, label in labels.items():
        (hwmon / f"pwm{index}").write_text("128", encoding="utf-8")
        (hwmon / f"fan{index}_input").write_text(str(1000 + index), encoding="utf-8")
        if label:
            (hwmon / f"fan{index}_label").write_text(label, encoding="utf-8")

    raw_fans = [
        SimpleNamespace(
            name=f"主板 PWM{index}",
            pwm_path=str(hwmon / f"pwm{index}"),
            rpm_input=str(hwmon / f"fan{index}_input"),
            min_pwm=20,
            max_pwm=240,
        )
        for index in labels
    ]

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}

    display_fans = page._display_fans_from_monitor(raw_fans, [])

    assert [fan.name for fan in display_fans] == [
        "CPU_OPT · PWM1/FAN1",
        "AIO_PUMP · PWM2/FAN2",
        "CHA_FAN1 · PWM3/FAN3",
        "未识别通道 · PWM4/FAN4",
    ]
    assert [fan.header_label for fan in display_fans] == ["CPU_OPT", "AIO_PUMP", "CHA_FAN1", ""]
    assert [fan.type_label for fan in display_fans] == ["CPU 风扇", "水泵/AIO", "机箱风扇", "未识别通道"]
    for raw_fan, display_fan in zip(raw_fans, display_fans, strict=True):
        assert display_fan.original_name == raw_fan.name
        assert display_fan.pwm_path == raw_fan.pwm_path
        assert display_fan.rpm_input == raw_fan.rpm_input
        assert display_fan.min_pwm == raw_fan.min_pwm
        assert display_fan.max_pwm == raw_fan.max_pwm
        assert page._display_fan_name(raw_fan.name) == display_fan.name
        assert page._original_fan_name(display_fan.name) == raw_fan.name
        assert raw_fan.pwm_path in display_fan.detail_text
        assert raw_fan.rpm_input in display_fan.detail_text

    page.close()
    app.quit()


def test_fan_host_layout_separates_dense_sections():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)

    assert [page.workspace_tabs.tabText(index) for index in range(page.workspace_tabs.count())] == [
        "仪表盘",
        "曲线",
        "调速",
        "策略",
        "标定",
        "权限",
        "压测",
        "历史",
    ]
    assert page.charts_tab.parentWidget() is not None
    assert page.channel_detail_tabs.count() == 3
    assert [page.channel_detail_tabs.tabText(index) for index in range(page.channel_detail_tabs.count())] == [
        "标定",
        "全部通道",
        "实时卡片",
    ]
    assert page.channel_selector_section.objectName() == "FanChannelEditorSection"
    assert page.channel_properties_section.objectName() == "FanChannelEditorSection"
    assert page.channel_actions_section.objectName() == "FanChannelEditorSection"
    assert page.channel_evidence_section.objectName() == "FanChannelEvidenceSection"
    assert page.permission_info_tabs.count() == 2
    assert [page.permission_info_tabs.tabText(index) for index in range(page.permission_info_tabs.count())] == [
        "权限明细",
        "诊断建议",
    ]
    assert page.permission_status_panel.objectName() == "FanPermissionPanel"
    assert page.permission_detail_panel.objectName() == "FanPermissionDetailPanel"
    assert page.fan_count_value.objectName() == "FanSummaryValue"
    assert page.overview_rpm_chart.parentWidget().objectName() == "FanOverviewCharts"
    assert page.overview_temperature_chart.parentWidget().objectName() == "FanOverviewCharts"
    assert page.fan_overview_headline_label.text()
    assert page.fan_role_summary_label.isHidden()
    assert page.fan_role_speed_label.isHidden()
    assert page.fan_identity_overview_label.isHidden()
    assert page.fan_identity_table.parentWidget() is not page.overview_tab
    assert page.workspace_tabs.minimumHeight() >= 520
    assert page.workspace_tabs.maximumHeight() == 16777215

    page.close()
    app.quit()


def test_fan_host_control_rows_keep_identity_control_and_binding_separate():
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout

    from usb9_lcd.gui.fan_host import DisplayFanChannel, EmbeddedFanControlPanel

    class DummySlider(QFrame):
        pwm_changed = Signal(str, int)
        auto_toggled = Signal(str, bool)

        def __init__(self, fan_name: str) -> None:
            super().__init__()
            layout = QVBoxLayout(self)
            self._name_label = QLabel(fan_name)
            layout.addWidget(self._name_label)

        def set_control_enabled(self, enabled: bool) -> None:
            self._enabled = enabled

    app = QApplication.instance() or QApplication([])
    panel = EmbeddedFanControlPanel(DummySlider)
    panel.populate_fans(
        [
            DisplayFanChannel(
                name="CPU_FAN · PWM1/FAN1",
                pwm_path="/sys/class/hwmon/hwmon0/pwm1",
                rpm_input="/sys/class/hwmon/hwmon0/fan1_input",
                type_label="CPU 风扇",
                channel_label="PWM1/FAN1",
                header_label="CPU_FAN",
                header_confirmed=True,
                detail_text="底层路径应放进 tooltip，而不是挤在默认行内。",
            )
        ],
        [],
    )

    assert [panel._mode_tabs.tabText(index) for index in range(panel._mode_tabs.count())] == [
        "手动调速",
        "温度绑定",
    ]
    assert panel.findChildren(QFrame, "FanControlRow")
    assert panel.findChildren(QFrame, "FanControlToolbar")
    assert not panel.findChildren(QFrame, "FanControlIdentityBlock")
    assert not panel.findChildren(QFrame, "FanControlSliderBlock")
    assert panel.findChildren(QFrame, "FanControlRow")[0].findChildren(QLabel, "FanControlChannelTitle")
    bind_blocks = panel.findChildren(QFrame, "FanControlBindBlock")
    assert bind_blocks
    assert bind_blocks[0].parentWidget().objectName() == "FanControlGroup"
    path_labels = panel.findChildren(QLabel, "FanControlPathLabel")
    assert [label.text() for label in path_labels].count("PWM1/FAN1") == 2
    assert panel._sliders["CPU_FAN · PWM1/FAN1"]._name_label.text() == "CPU_FAN · CPU"

    panel.close()
    app.quit()


def test_embedded_profile_editor_splits_profile_state_from_curve_canvas(tmp_path: Path):
    from PySide6.QtWidgets import QApplication, QFrame

    from usb9_lcd.gui.fan_host import EmbeddedProfileEditor

    class DummyCurve:
        def __init__(self, points=None) -> None:
            self.points = points or []

    class DummyProfile:
        def __init__(self, name: str) -> None:
            self.name = name
            self.curves = {
                "CPU": [(30, 25), (60, 70)],
                "GPU": [(35, 30), (75, 85)],
            }

    class DummyProfileManager:
        config_dir = tmp_path

        def __init__(self) -> None:
            self.profile = DummyProfile("quiet")

        def list_names(self) -> list[str]:
            return ["quiet"]

        def load(self, _name: str) -> DummyProfile:
            return self.profile

        def get_active(self) -> DummyProfile:
            return self.profile

    class DummyCurveEditor(QFrame):
        def __init__(self) -> None:
            super().__init__()
            self.curve = DummyCurve()

        def set_curve(self, curve: DummyCurve) -> None:
            self.curve = curve

        def get_curve(self) -> DummyCurve:
            return self.curve

    app = QApplication.instance() or QApplication([])
    editor = EmbeddedProfileEditor(DummyProfileManager(), DummyCurveEditor, DummyCurve, DummyProfile)

    assert editor.findChild(QFrame, "FanProfileSidebar") is not None
    assert editor.findChild(QFrame, "FanCurveEditorPanel") is not None
    assert editor._curve_tabs.objectName() == "FanProfileCurveTabs"
    assert editor._curve_tabs.minimumHeight() >= 540

    editor.close()
    app.quit()


def test_fan_host_channel_override_changes_role_and_display_name(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6798", encoding="utf-8")
    (hwmon / "pwm4").write_text("128", encoding="utf-8")
    (hwmon / "fan4_input").write_text("980", encoding="utf-8")
    raw_fan = SimpleNamespace(
        name="主板 PWM4",
        pwm_path=str(hwmon / "pwm4"),
        rpm_input=str(hwmon / "fan4_input"),
        min_pwm=20,
        max_pwm=240,
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}

    display_fan = page._display_fans_from_monitor([raw_fan], [])[0]
    assert display_fan.name == "未识别通道 · PWM4/FAN4"

    page._fan_label_overrides[display_fan.identity_key] = {"role": "水泵/AIO", "alias": "水泵", "header": "AIO_PUMP"}
    remapped = page._display_fans_from_monitor([raw_fan], [])[0]
    page._fans = [remapped]

    assert remapped.name == "水泵 · PWM4/FAN4"
    assert remapped.type_label == "水泵/AIO"
    assert remapped.header_label == "AIO_PUMP"
    assert page._fan_channel_display_label(remapped) == "AIO_PUMP · PWM4/FAN4"
    assert page._fan_count_summary_text() == "1 通道\n水泵 1"

    page.close()
    app.quit()


def test_fan_host_sorts_channels_by_physical_role_and_summarizes_counts(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6798", encoding="utf-8")
    labels = {
        4: "CHA_FAN2",
        7: "AIO_PUMP",
        1: "CPU_FAN",
    }
    for index, label in labels.items():
        (hwmon / f"pwm{index}").write_text("128", encoding="utf-8")
        (hwmon / f"fan{index}_input").write_text(str(900 + index), encoding="utf-8")
        (hwmon / f"fan{index}_label").write_text(label, encoding="utf-8")

    raw_fans = [
        SimpleNamespace(
            name=f"主板 PWM{index}",
            pwm_path=str(hwmon / f"pwm{index}"),
            rpm_input=str(hwmon / f"fan{index}_input"),
            min_pwm=20,
            max_pwm=240,
        )
        for index in labels
    ]

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}

    page._fans = page._display_fans_from_monitor(raw_fans, [])
    page._loaded = True
    page._refresh_summary()

    assert [fan.name for fan in page._fans] == [
        "CPU_FAN · PWM1/FAN1",
        "AIO_PUMP · PWM7/FAN7",
        "CHA_FAN2 · PWM4/FAN4",
    ]
    assert page.fan_count_value.text() == "3 通道\nCPU 风扇 1 · 水泵 1 · 机箱风扇 1"

    page.close()
    app.quit()


def test_fan_host_role_summary_keeps_all_channels_grouped_by_physical_role(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6798", encoding="utf-8")
    labels = {
        3: "CHA_FAN1",
        7: "AIO_PUMP",
        1: "CPU_FAN",
    }
    for index, label in labels.items():
        (hwmon / f"pwm{index}").write_text("128", encoding="utf-8")
        rpm = 0 if label == "AIO_PUMP" else 1000 + index
        (hwmon / f"fan{index}_input").write_text(str(rpm), encoding="utf-8")
        (hwmon / f"fan{index}_label").write_text(label, encoding="utf-8")

    raw_fans = [
        SimpleNamespace(
            name=f"主板 PWM{index}",
            pwm_path=str(hwmon / f"pwm{index}"),
            rpm_input=str(hwmon / f"fan{index}_input"),
            min_pwm=20,
            max_pwm=240,
        )
        for index in labels
    ]

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}
    page._fans = page._display_fans_from_monitor(raw_fans, [])
    page._loaded = True
    page._latest_rpm = {
        "CPU_FAN · PWM1/FAN1": 1350,
        "CHA_FAN1 · PWM3/FAN3": 920,
    }

    assert page._overview_fan_names() == [
        "CPU_FAN · PWM1/FAN1",
        "AIO_PUMP · PWM7/FAN7",
        "CHA_FAN1 · PWM3/FAN3",
    ]
    summary = page._fan_role_summary_text()
    assert "CPU：1/1 有转速（CPU_FAN · PWM1/FAN1）" in summary
    assert "水泵：1 个待确认（AIO_PUMP · PWM7/FAN7）" in summary
    assert "机箱：1/1 有转速（CHA_FAN1 · PWM3/FAN3）" in summary

    page.close()
    app.quit()


def test_fan_host_speed_summary_names_cpu_pump_and_case_fans(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6799", encoding="utf-8")
    labels = {
        1: ("CPU_FAN", 1320),
        7: ("AIO_PUMP", 0),
        3: ("CHA_FAN1", 920),
    }
    for index, (label, rpm) in labels.items():
        (hwmon / f"pwm{index}").write_text("128", encoding="utf-8")
        (hwmon / f"fan{index}_input").write_text(str(rpm), encoding="utf-8")
        (hwmon / f"fan{index}_label").write_text(label, encoding="utf-8")

    raw_fans = [
        SimpleNamespace(
            name=f"主板 PWM{index}",
            pwm_path=str(hwmon / f"pwm{index}"),
            rpm_input=str(hwmon / f"fan{index}_input"),
            min_pwm=20,
            max_pwm=240,
        )
        for index in labels
    ]

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}
    page._fans = page._display_fans_from_monitor(raw_fans, [])
    page._loaded = True
    page._refresh_fan_visuals()

    summary = page._fan_role_speed_summary_text()
    assert "CPU：CPU_FAN 1320 RPM" in summary
    assert "水泵：AIO_PUMP 无转速" in summary
    assert "机箱：CHA_FAN1 920 RPM" in summary
    assert page.fan_role_speed_label.text() == summary

    page.close()
    app.quit()


def test_fan_host_role_metric_cards_show_cpu_pump_and_case_speeds(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6799", encoding="utf-8")
    labels = {
        1: ("CPU_FAN", 1320),
        7: ("AIO_PUMP", 0),
        3: ("CHA_FAN1", 920),
        4: ("CHA_FAN2", 780),
    }
    for index, (label, rpm) in labels.items():
        (hwmon / f"pwm{index}").write_text("128", encoding="utf-8")
        (hwmon / f"fan{index}_input").write_text(str(rpm), encoding="utf-8")
        (hwmon / f"fan{index}_label").write_text(label, encoding="utf-8")

    raw_fans = [
        SimpleNamespace(
            name=f"主板 PWM{index}",
            pwm_path=str(hwmon / f"pwm{index}"),
            rpm_input=str(hwmon / f"fan{index}_input"),
            min_pwm=20,
            max_pwm=240,
        )
        for index in labels
    ]

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}
    page._fans = page._display_fans_from_monitor(raw_fans, [])
    page._loaded = True
    page._refresh_fan_visuals()

    assert page.fan_role_metric_cards["CPU 风扇"].speed_label.text() == "CPU_FAN 1320 RPM"
    assert page.fan_role_metric_cards["水泵/AIO"].speed_label.text() == "无转速"
    assert page.fan_role_metric_cards["机箱风扇"].speed_label.text() == "2/2 有转速 · 平均 850 RPM"
    assert "CHA_FAN1" in page.fan_role_metric_cards["机箱风扇"].detail_label.text()

    page.close()
    app.quit()


def test_fan_host_seeds_runtime_rpm_and_identity_overview_from_hwmon(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6799", encoding="utf-8")
    labels = {
        1: ("CPU_FAN", 0),
        2: ("AIO_PUMP", 1460),
        3: ("CHA_FAN1", 890),
    }
    for index, (label, rpm) in labels.items():
        (hwmon / f"pwm{index}").write_text("128", encoding="utf-8")
        (hwmon / f"fan{index}_input").write_text(str(rpm), encoding="utf-8")
        (hwmon / f"fan{index}_label").write_text(label, encoding="utf-8")

    raw_fans = [
        SimpleNamespace(
            name=f"主板 PWM{index}",
            pwm_path=str(hwmon / f"pwm{index}"),
            rpm_input=str(hwmon / f"fan{index}_input"),
            min_pwm=20,
            max_pwm=240,
        )
        for index in labels
    ]

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page.monitor = SimpleNamespace(control_enabled=False)
    page._fan_label_overrides = {}

    page._fans = page._display_fans_from_monitor(raw_fans, [])
    page._loaded = True
    page._refresh_summary()
    page._refresh_fan_table()

    assert page._latest_rpm["CPU_FAN · PWM1/FAN1"] == 0
    assert page._latest_rpm["AIO_PUMP · PWM2/FAN2"] == 1460
    assert page._latest_rpm["CHA_FAN1 · PWM3/FAN3"] == 890
    assert page.fan_table.item(0, 4).text() == "0 RPM"
    assert page.fan_table.item(1, 4).text() == "1460 RPM"
    assert page.fan_table.item(2, 4).text() == "890 RPM"
    assert "2/3 个通道有转速" in page.visual_status_label.text()

    overview = page.fan_identity_overview_label.text()
    assert "物理接口识别：3 路已命名" in overview
    assert "底层 PWM/FAN 路径放在明细提示里" in overview
    assert page.fan_identity_table.rowCount() == 3
    assert page.fan_identity_table.item(0, 0).text() == "CPU_FAN"
    assert page.fan_identity_table.item(0, 1).text() == "CPU"
    assert page.fan_identity_table.item(0, 2).text() == "无转速"
    assert page.fan_identity_table.item(0, 3).text() == "已确认"
    assert page.fan_identity_table.item(1, 0).text() == "AIO_PUMP"
    assert page.fan_identity_table.item(1, 1).text() == "水泵"
    assert page.fan_identity_table.item(1, 2).text() == "1460 RPM"
    assert page.fan_identity_table.item(1, 3).text() == "已确认"
    assert page.fan_identity_table.item(2, 0).text() == "CHA_FAN1"
    assert page.fan_identity_table.item(2, 1).text() == "机箱"
    assert page.fan_identity_table.item(2, 2).text() == "890 RPM"
    assert page.fan_identity_table.item(2, 3).text() == "已确认"

    page.close()
    app.quit()


def test_fan_host_quick_channel_label_marks_selected_unknown_channel(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    saved: dict[str, dict[str, dict[str, str]]] = {}

    def fake_save(overrides):
        saved["channels"] = dict(overrides)

    monkeypatch.setattr(fan_host, "_save_fan_channel_overrides", fake_save)

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6798", encoding="utf-8")
    (hwmon / "pwm4").write_text("128", encoding="utf-8")
    (hwmon / "fan4_input").write_text("960", encoding="utf-8")
    raw_fan = SimpleNamespace(
        name="主板 PWM4",
        pwm_path=str(hwmon / "pwm4"),
        rpm_input=str(hwmon / "fan4_input"),
        min_pwm=20,
        max_pwm=240,
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}
    page._fans = page._display_fans_from_monitor([raw_fan], [])
    page._refresh_channel_label_editor()
    refreshed = []
    page._reload_after_channel_label_change = lambda: refreshed.append(True)

    page.apply_quick_channel_label(role="水泵/AIO", header="AIO_PUMP", alias="水泵")

    key = page._fans[0].identity_key
    assert page._fan_label_overrides[key] == {"role": "水泵/AIO", "header": "AIO_PUMP", "alias": "水泵"}
    assert saved["channels"][key] == {"role": "水泵/AIO", "header": "AIO_PUMP", "alias": "水泵"}
    assert refreshed == [True]

    page.close()
    app.quit()


def test_fan_host_confirm_detected_board_candidate_locks_header(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    saved: dict[str, dict[str, dict[str, str]]] = {}

    def fake_save(overrides):
        saved["channels"] = dict(overrides)

    monkeypatch.setattr(fan_host, "_save_fan_channel_overrides", fake_save)
    monkeypatch.setattr(fan_host, "_is_real_sysfs_hwmon_path", lambda _path: True)

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6799", encoding="utf-8")
    (hwmon / "pwm7").write_text("128", encoding="utf-8")
    (hwmon / "fan7_input").write_text("1180", encoding="utf-8")
    raw_fan = SimpleNamespace(
        name="主板 PWM7",
        pwm_path=str(hwmon / "pwm7"),
        rpm_input=str(hwmon / "fan7_input"),
        min_pwm=20,
        max_pwm=240,
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}
    page._system_board_identity = lambda: ("ASUSTeK COMPUTER INC.", "ROG STRIX B850-A GAMING WIFI S")
    page._fans = page._display_fans_from_monitor([raw_fan], [])
    page._refresh_channel_label_editor()
    refreshed = []
    page._reload_after_channel_label_change = lambda: refreshed.append(True)

    assert page._fans[0].name == "AIO_PUMP? · PWM7/FAN7"

    page.confirm_selected_channel_detection()

    key = page._fans[0].identity_key
    assert page._fan_label_overrides[key] == {"role": "水泵/AIO", "header": "AIO_PUMP"}
    assert saved["channels"][key] == {"role": "水泵/AIO", "header": "AIO_PUMP"}
    assert refreshed == [True]

    remapped = page._display_fans_from_monitor([raw_fan], [])[0]
    assert remapped.name == "AIO_PUMP · PWM7/FAN7"
    assert remapped.header_confirmed is True

    page.close()
    app.quit()


def test_fan_host_profile_status_name_does_not_mangle_display_pwm_labels():
    from usb9_lcd.gui.fan_host import _profile_fan_status_name

    assert _profile_fan_status_name("水泵/AIO · PWM2/FAN2") == "水泵/AIO · PWM2/FAN2"
    assert _profile_fan_status_name("主板 PWM3") == "PWM3/FAN3"


def test_fan_host_driver_probe_treats_pwm_as_control_file():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)

    assert page._is_pwm_control_file("pwm1") is True
    assert page._is_pwm_control_file("pwm7") is True
    assert page._is_pwm_control_file("fan1_input") is False
    assert page._is_fan_control_file("fan1_input") is False
    assert "has_pwm_control" in page._fan_hwmon_probe_shell(include_forced_probes=True)

    page.close()
    app.quit()


def test_fan_host_role_detection_covers_common_board_and_gpu_labels():
    from usb9_lcd.gui.fan_host import _fan_header_role, _fan_role_detection, _known_fan_header_label

    cases = [
        ("CPU_FAN", "CPU 风扇", "CPU_FAN"),
        ("CPU Fan 1", "CPU 风扇", "CPU_FAN"),
        ("CPU FanIn", "CPU 风扇", "CPU_FAN"),
        ("AIO Pump", "水泵/AIO", "AIO_PUMP"),
        ("AIO_PUMP1", "水泵/AIO", "AIO_PUMP"),
        ("PumpIn", "水泵/AIO", "W_PUMP"),
        ("Pump", "水泵/AIO", "W_PUMP"),
        ("Pump Fan 1", "水泵/AIO", "W_PUMP"),
        ("CHA Fan", "机箱风扇", "CHA_FAN"),
        ("CHA1_FAN", "机箱风扇", "CHA_FAN1"),
        ("SYS2 Fan", "机箱风扇", "SYS_FAN2"),
        ("Chassis3 FanIn", "机箱风扇", "CHA_FAN3"),
        ("H_AMP Fan", "机箱风扇", "H_AMP"),
        ("EXT_FAN2", "机箱风扇", "EXT_FAN"),
        ("SYS_FAN", "机箱风扇", "SYS_FAN"),
        ("GPU Fan", "GPU 风扇", ""),
        ("PWM4", "未识别通道", ""),
    ]

    for label, role, header in cases:
        assert _fan_role_detection([("label", label)], has_pwm=True)[0] == role
        assert _known_fan_header_label(label) == header
        if header:
            assert _fan_header_role(header) == role


def test_fan_host_infers_unconfirmed_header_from_legacy_backend_name(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6798", encoding="utf-8")
    (hwmon / "pwm2").write_text("128", encoding="utf-8")
    (hwmon / "fan2_input").write_text("1160", encoding="utf-8")
    raw_fan = SimpleNamespace(
        name="主板 AIO Pump",
        pwm_path=str(hwmon / "pwm2"),
        rpm_input=str(hwmon / "fan2_input"),
        min_pwm=20,
        max_pwm=240,
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}

    display_fan = page._display_fans_from_monitor([raw_fan], [])[0]

    assert display_fan.name == "AIO_PUMP? · PWM2/FAN2"
    assert display_fan.type_label == "水泵/AIO"
    assert display_fan.header_label == "AIO_PUMP?"
    assert display_fan.header_confirmed is False
    assert "旧风扇后端名称候选" in display_fan.header_basis
    assert "原始名称: 主板 AIO Pump" in display_fan.role_basis

    page.close()
    app.quit()


def test_fan_host_channel_editor_prefills_detected_role_header_and_live_speed(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6798", encoding="utf-8")
    (hwmon / "pwm1").write_text("128", encoding="utf-8")
    (hwmon / "fan1_input").write_text("930", encoding="utf-8")
    (hwmon / "fan1_label").write_text("CPU_FAN", encoding="utf-8")
    raw_fan = SimpleNamespace(
        name="主板 PWM1",
        pwm_path=str(hwmon / "pwm1"),
        rpm_input=str(hwmon / "fan1_input"),
        min_pwm=20,
        max_pwm=240,
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}
    page._fans = page._display_fans_from_monitor([raw_fan], [])
    page._latest_rpm[page._fans[0].name] = 930

    page._refresh_channel_label_editor()

    assert page.channel_role_combo.currentData() == "CPU 风扇"
    assert page.channel_header_combo.currentData() == "CPU_FAN"
    assert "CPU_FAN" in page.channel_label_combo.currentText()
    assert "有转速 930 RPM" in page.channel_label_combo.currentText()
    assert "当前识别：CPU 风扇" in page.channel_evidence_label.text()
    assert "有转速 930 RPM" in page.channel_evidence_label.text()

    page.close()
    app.quit()


def test_fan_host_readonly_rpm_channel_uses_fan_role_not_pwm(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6798", encoding="utf-8")
    (hwmon / "fan1_input").write_text("1220", encoding="utf-8")
    (hwmon / "fan1_label").write_text("CPU_FAN", encoding="utf-8")
    sensor = SimpleNamespace(
        name="主板 CPU_FAN",
        unit="RPM",
        source="hwmon",
        internal_id=str(hwmon / "fan1_input"),
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}

    display_fan = page._display_fans_from_monitor([], [sensor])[0]

    assert display_fan.name == "CPU_FAN · FAN1"
    assert display_fan.type_label == "CPU 风扇"
    assert display_fan.header_label == "CPU_FAN"
    assert display_fan.read_only is True
    assert "PWM" not in display_fan.type_label

    page.close()
    app.quit()


def test_fan_host_gpu_readonly_channel_keeps_gpu_model_name():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}

    display_fan = page._display_fan_channel(
        SimpleNamespace(
            name="GPU0 GeForce RTX 4090 风扇",
            pwm_path="readonly:nvidia-smi:0:fan",
            rpm_input="nvidia-smi:0:fan",
            read_only=True,
            rpm_unit="%",
        ),
        existing=set(),
    )

    assert display_fan.name == "GPU0 GeForce RTX 4090 风扇 · GPU0"
    assert display_fan.type_label == "GPU 风扇"
    assert display_fan.rpm_unit == "%"

    page.close()
    app.quit()


def test_fan_host_identity_key_separates_same_chip_channel_on_different_hwmons(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    raw_fans = []
    for hwmon_name in ("hwmon0", "hwmon1"):
        hwmon = tmp_path / hwmon_name
        hwmon.mkdir()
        (hwmon / "name").write_text("nct6798", encoding="utf-8")
        (hwmon / "pwm1").write_text("128", encoding="utf-8")
        (hwmon / "fan1_input").write_text("900", encoding="utf-8")
        raw_fans.append(
            SimpleNamespace(
                name="主板 PWM1",
                pwm_path=str(hwmon / "pwm1"),
                rpm_input=str(hwmon / "fan1_input"),
                min_pwm=20,
                max_pwm=240,
            )
        )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}

    display_fans = page._display_fans_from_monitor(raw_fans, [])

    assert display_fans[0].identity_key != display_fans[1].identity_key
    assert display_fans[0].name == "未识别通道 · PWM1/FAN1"
    assert display_fans[1].name == "未识别通道 · PWM1/FAN1 #2"

    page.close()
    app.quit()


def test_fan_host_identity_key_survives_hwmon_number_changes(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    device_root = tmp_path / "devices" / "platform" / "nct6799.656" / "hwmon"
    class_a = tmp_path / "class-a" / "hwmon"
    class_b = tmp_path / "class-b" / "hwmon"
    class_a.mkdir(parents=True)
    class_b.mkdir(parents=True)

    raw_fans = []
    for class_root, hwmon_name in ((class_a, "hwmon0"), (class_b, "hwmon7")):
        real_hwmon = device_root / hwmon_name
        real_hwmon.mkdir(parents=True)
        (real_hwmon / "name").write_text("nct6799", encoding="utf-8")
        (real_hwmon / "pwm7").write_text("128", encoding="utf-8")
        (real_hwmon / "fan7_input").write_text("1200", encoding="utf-8")
        (class_root / hwmon_name).symlink_to(real_hwmon, target_is_directory=True)
        raw_fans.append(
            SimpleNamespace(
                name="主板 PWM7",
                pwm_path=str(class_root / hwmon_name / "pwm7"),
                rpm_input=str(class_root / hwmon_name / "fan7_input"),
                min_pwm=20,
                max_pwm=240,
            )
        )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}

    first = page._display_fans_from_monitor([raw_fans[0]], [])[0]
    page._fan_label_overrides[first.identity_key] = {"role": "水泵/AIO", "header": "AIO_PUMP", "alias": "水泵"}
    second = page._display_fans_from_monitor([raw_fans[1]], [])[0]

    assert first.identity_key == second.identity_key
    assert second.name == "水泵 · PWM7/FAN7"
    assert second.type_label == "水泵/AIO"
    assert second.header_label == "AIO_PUMP"

    page.close()
    app.quit()


def test_fan_host_marks_asus_board_header_candidates_as_unconfirmed(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    monkeypatch.setattr(fan_host, "_is_real_sysfs_hwmon_path", lambda _path: True)

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6799", encoding="utf-8")
    (hwmon / "pwm7").write_text("128", encoding="utf-8")
    (hwmon / "fan7_input").write_text("1400", encoding="utf-8")
    raw_fan = SimpleNamespace(
        name="主板 PWM7",
        pwm_path=str(hwmon / "pwm7"),
        rpm_input=str(hwmon / "fan7_input"),
        min_pwm=20,
        max_pwm=240,
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}
    page._system_board_identity = lambda: ("ASUSTeK COMPUTER INC.", "ROG STRIX B850-A GAMING WIFI S")

    display_fan = page._display_fans_from_monitor([raw_fan], [])[0]

    assert display_fan.name == "AIO_PUMP? · PWM7/FAN7"
    assert display_fan.header_label == "AIO_PUMP?"
    assert display_fan.header_confirmed is False
    assert display_fan.type_label == "水泵/AIO"
    assert "需要用识别脉冲" in display_fan.header_basis

    page.close()
    app.quit()


def test_fan_host_asus_b850_unlabeled_channels_show_candidates_and_confirmation_guidance(
    tmp_path: Path,
    monkeypatch,
):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    monkeypatch.setattr(fan_host, "_is_real_sysfs_hwmon_path", lambda _path: True)

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6799", encoding="utf-8")
    for index in range(1, 8):
        (hwmon / f"pwm{index}").write_text("128", encoding="utf-8")
        (hwmon / f"fan{index}_input").write_text(str(1000 + index), encoding="utf-8")

    raw_fans = [
        SimpleNamespace(
            name=f"主板 PWM{index}",
            pwm_path=str(hwmon / f"pwm{index}"),
            rpm_input=str(hwmon / f"fan{index}_input"),
            min_pwm=20,
            max_pwm=240,
        )
        for index in range(1, 8)
    ]

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}
    page._system_board_identity = lambda: ("ASUSTeK COMPUTER INC.", "ROG STRIX B850-A GAMING WIFI S")

    page._fans = page._display_fans_from_monitor(raw_fans, [])
    page._loaded = True
    page._refresh_summary()
    page._refresh_fan_table()

    assert [fan.header_label for fan in page._fans] == [
        "CPU_FAN?",
        "CPU_OPT?",
        "AIO_PUMP?",
        "CHA_FAN1?",
        "CHA_FAN2?",
        "CHA_FAN3?",
        "CHA_FAN4?",
    ]
    assert [fan.type_label for fan in page._fans] == [
        "CPU 风扇",
        "CPU 风扇",
        "水泵/AIO",
        "机箱风扇",
        "机箱风扇",
        "机箱风扇",
        "机箱风扇",
    ]
    assert all(fan.header_confirmed is False for fan in page._fans)
    aio = next(fan for fan in page._fans if fan.header_label == "AIO_PUMP?")
    assert "ROG STRIX B850-A GAMING WIFI S" in aio.header_basis
    assert "无 hwmon label" in aio.header_basis
    assert "ASUS B850-A 7 路接口映射" in aio.header_basis
    assert "PWM7/FAN7 -> AIO_PUMP" in aio.header_basis
    assert "候选接口 CPU_FAN?" in page.channel_label_combo.itemText(0)

    page.channel_label_combo.setCurrentIndex(page.channel_label_combo.findData(aio.identity_key))
    evidence = page.channel_evidence_label.text()
    assert "当前识别：水泵/AIO" in evidence
    assert "物理接口：AIO_PUMP?" in evidence
    assert "确认当前识别" in evidence
    assert "识别选中通道" in evidence

    page.close()
    app.quit()


def test_fan_host_can_confirm_all_board_header_candidates(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    saved: dict[str, dict[str, dict[str, str]]] = {}

    def fake_save(overrides):
        saved["channels"] = dict(overrides)

    monkeypatch.setattr(fan_host, "_save_fan_channel_overrides", fake_save)
    monkeypatch.setattr(fan_host, "_is_real_sysfs_hwmon_path", lambda _path: True)

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6799", encoding="utf-8")
    for index in range(1, 8):
        (hwmon / f"pwm{index}").write_text("128", encoding="utf-8")
        (hwmon / f"fan{index}_input").write_text(str(1000 + index), encoding="utf-8")

    raw_fans = [
        SimpleNamespace(
            name=f"主板 PWM{index}",
            pwm_path=str(hwmon / f"pwm{index}"),
            rpm_input=str(hwmon / f"fan{index}_input"),
            min_pwm=20,
            max_pwm=240,
        )
        for index in range(1, 8)
    ]

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}
    page._system_board_identity = lambda: ("ASUSTeK COMPUTER INC.", "ROG STRIX B850-A GAMING WIFI S")
    refreshed = []
    page._reload_after_channel_label_change = lambda: refreshed.append(True)

    page._fans = page._display_fans_from_monitor(raw_fans, [])
    page._loaded = True
    page._refresh_fan_visuals()

    assert page.confirm_all_candidates_button.isEnabled() is True
    notice = page.fan_identity_notice_label.text()
    assert "检测到主板候选接口" in notice
    assert "CPU_FAN?" in notice
    assert "AIO_PUMP?" in notice

    page.confirm_all_candidate_channel_detections()

    assert refreshed == [True]
    assert set(saved["channels"]) == {fan.identity_key for fan in page._fans}
    assert {value["header"] for value in saved["channels"].values()} == {
        "CPU_FAN",
        "CPU_OPT",
        "AIO_PUMP",
        "CHA_FAN1",
        "CHA_FAN2",
        "CHA_FAN3",
        "CHA_FAN4",
    }
    assert {value["role"] for value in saved["channels"].values()} == {"CPU 风扇", "水泵/AIO", "机箱风扇"}

    page.close()
    app.quit()


def test_fan_channel_override_json_roundtrip_cleans_empty_fields(tmp_path: Path, monkeypatch):
    import usb9_lcd.gui.fan_host as fan_host

    path = tmp_path / "fan-channels.json"
    monkeypatch.setattr(fan_host, "FAN_CHANNEL_LABELS_PATH", path)

    fan_host._save_fan_channel_overrides(
        {
            "hwmon:board:PWM1/FAN1": {"role": "CPU 风扇", "header": "CPU_FAN", "alias": ""},
            "empty": {"role": "", "header": "", "alias": ""},
        }
    )

    assert fan_host._load_fan_channel_overrides() == {
        "hwmon:board:PWM1/FAN1": {"role": "CPU 风扇", "header": "CPU_FAN", "alias": ""}
    }


def test_fan_host_uses_pwm_label_basis_and_can_identify_selected_channel(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    class FakeMonitor:
        def __init__(self):
            self.control_enabled = False
            self.manual_calls = []
            self.auto_calls = []
            self.control_states = []

        def set_control_enabled(self, enabled):
            self.control_enabled = enabled
            self.control_states.append(enabled)

        def set_fan_manual(self, name, pwm):
            self.manual_calls.append((name, pwm))

        def set_fan_auto(self, name):
            self.auto_calls.append(name)

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6798", encoding="utf-8")
    (hwmon / "pwm1").write_text("128", encoding="utf-8")
    (hwmon / "fan1_input").write_text("1350", encoding="utf-8")
    (hwmon / "pwm1_label").write_text("W_PUMP", encoding="utf-8")
    raw_fan = SimpleNamespace(
        name="主板 PWM1",
        pwm_path=str(hwmon / "pwm1"),
        rpm_input=str(hwmon / "fan1_input"),
        min_pwm=20,
        max_pwm=240,
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}
    page.monitor = FakeMonitor()
    page._loaded = True
    page._fans = page._display_fans_from_monitor([raw_fan], [])

    page._refresh_summary()
    page._refresh_fan_table()
    display_name = page._fans[0].name

    assert display_name == "W_PUMP · PWM1/FAN1"
    assert page.fan_table.horizontalHeaderItem(3).text() == "关联传感器"
    assert page.fan_table.horizontalHeaderItem(5).text() == "输出"
    assert page.fan_table.item(0, 1).text() == "水泵/AIO"
    assert page.fan_table.item(0, 2).text() == "W_PUMP · PWM1/FAN1"
    assert page.fan_table.item(0, 3).text() == "--"
    assert page.fan_table.item(0, 5).toolTip() == "水泵输出"
    assert "hwmon label: W_PUMP" in page.fan_table.item(0, 2).toolTip()
    assert "pwm: " in page.fan_table.item(0, 0).toolTip()
    assert "物理接口：W_PUMP" in page.channel_evidence_label.text()
    assert "PWM1/FAN1" in page.channel_evidence_label.text()
    assert str(hwmon / "pwm1") in page.channel_evidence_label.toolTip()

    page.identify_selected_fan_channel()

    assert page.monitor.control_states == [True]
    assert page.monitor.manual_calls == [("主板 PWM1", 230)]
    assert page._latest_pwm[display_name] == 230
    assert page.fan_table.item(0, 5).text() == "90% (230)"
    assert page.fan_table.item(0, 5).toolTip() == "水泵输出"

    page._finish_fan_channel_identify("主板 PWM1", display_name, was_enabled=False)

    assert page.monitor.auto_calls == ["主板 PWM1"]
    assert page.monitor.control_states == [True, False]

    page.close()
    app.quit()


def test_fan_host_pump_zero_rpm_status_calls_out_pump(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6798", encoding="utf-8")
    (hwmon / "pwm2").write_text("128", encoding="utf-8")
    (hwmon / "fan2_input").write_text("0", encoding="utf-8")
    (hwmon / "fan2_label").write_text("AIO_PUMP", encoding="utf-8")
    raw_fan = SimpleNamespace(
        name="主板 PWM2",
        pwm_path=str(hwmon / "pwm2"),
        rpm_input=str(hwmon / "fan2_input"),
        min_pwm=20,
        max_pwm=240,
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page.monitor = SimpleNamespace(control_enabled=False)
    page._fan_label_overrides = {}
    page._fans = page._display_fans_from_monitor([raw_fan], [])
    page._loaded = True
    page._refresh_fan_table()

    assert page.fan_table.item(0, 7).text() == "水泵无转速，只读"

    page.close()
    app.quit()


def test_fan_host_shows_candidate_channel_identity_evidence(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    monkeypatch.setattr(fan_host, "_is_real_sysfs_hwmon_path", lambda _path: True)

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6799", encoding="utf-8")
    (hwmon / "pwm1").write_text("72", encoding="utf-8")
    (hwmon / "fan1_input").write_text("0", encoding="utf-8")
    (hwmon / "pwm1_temp_sel").write_text("8", encoding="utf-8")
    (hwmon / "temp8_label").write_text("PECI/TSI Agent 0 Calibration", encoding="utf-8")
    (hwmon / "temp8_input").write_text("42000", encoding="utf-8")
    raw_fan = SimpleNamespace(
        name="主板 PWM1",
        pwm_path=str(hwmon / "pwm1"),
        rpm_input=str(hwmon / "fan1_input"),
        min_pwm=20,
        max_pwm=240,
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}
    page._system_board_identity = lambda: ("ASUSTeK COMPUTER INC.", "ROG STRIX B850-A GAMING WIFI S")

    display_fan = page._display_fans_from_monitor([raw_fan], [])[0]
    page._fans = [display_fan]
    page._loaded = True
    page._refresh_summary()
    page._refresh_fan_table()

    assert display_fan.name == "CPU_FAN? · PWM1/FAN1"
    assert display_fan.header_confirmed is False
    assert display_fan.chip_label == "主板 nct6799"
    assert display_fan.hwmon_path == str(hwmon)
    assert "芯片 主板 nct6799" in display_fan.evidence_text
    assert "PWM pwm1 / 转速 fan1_input" in display_fan.evidence_text
    assert "主板候选" in display_fan.evidence_text
    assert page.fan_table.columnCount() == 8
    assert page.fan_identity_table.item(0, 0).text() == "CPU_FAN?"
    assert page.fan_identity_table.item(0, 1).text() == "CPU"
    assert page.fan_identity_table.item(0, 3).text() == "候选"
    assert "确认当前识别" in page.fan_identity_table.item(0, 4).text()
    assert "CPU_FAN?" in page.channel_label_combo.currentText()
    assert "芯片 主板 nct6799" in page.channel_label_combo.currentText()
    assert "芯片 主板 nct6799" in page.fan_table.item(0, 6).toolTip()
    assert "hwmon:" in page.channel_evidence_label.toolTip()
    assert "pwm1" in page.channel_evidence_label.text()

    page.close()
    app.quit()


def test_fan_host_keeps_unlabeled_channel_unknown_but_visible(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.fan_host import FanControlHostPage

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6799", encoding="utf-8")
    (hwmon / "pwm4").write_text("72", encoding="utf-8")
    (hwmon / "fan4_input").write_text("0", encoding="utf-8")
    raw_fan = SimpleNamespace(
        name="主板 PWM4",
        pwm_path=str(hwmon / "pwm4"),
        rpm_input=str(hwmon / "fan4_input"),
        min_pwm=20,
        max_pwm=240,
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}
    page._system_board_identity = lambda: ("Other Vendor", "Unknown Board")

    display_fan = page._display_fans_from_monitor([raw_fan], [])[0]
    page._fans = [display_fan]
    page._loaded = True
    page._refresh_summary()

    assert display_fan.name == "未识别通道 · PWM4/FAN4"
    assert display_fan.type_label == "未识别通道"
    assert display_fan.header_label == ""
    assert "接口未确认" in display_fan.evidence_text
    assert "芯片 主板 nct6799" in display_fan.evidence_text
    assert "PWM pwm4 / 转速 fan4_input" in display_fan.evidence_text
    assert page.fan_identity_table.item(0, 0).text() == "待标定通道 1"
    assert page.fan_identity_table.item(0, 1).text() == "未识别"
    assert page.fan_identity_table.item(0, 3).text() == "待标定"
    assert "识别选中通道" in page.fan_identity_table.item(0, 4).text()

    page.close()
    app.quit()


def test_fan_host_uses_fancontrol_temperature_binding_for_cpu_candidate(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    board_hwmon = tmp_path / "hwmon0"
    cpu_hwmon = tmp_path / "hwmon1"
    board_hwmon.mkdir()
    cpu_hwmon.mkdir()
    (board_hwmon / "name").write_text("nct6798", encoding="utf-8")
    (board_hwmon / "pwm1").write_text("128", encoding="utf-8")
    (board_hwmon / "fan1_input").write_text("1280", encoding="utf-8")
    (cpu_hwmon / "name").write_text("k10temp", encoding="utf-8")
    (cpu_hwmon / "temp1_input").write_text("43000", encoding="utf-8")
    (cpu_hwmon / "temp1_label").write_text("Tctl", encoding="utf-8")
    fancontrol_path = tmp_path / "fancontrol"
    fancontrol_path.write_text(
        "\n".join(
            (
                "DEVNAME=hwmon0=nct6798 hwmon1=k10temp",
                "FCTEMPS=hwmon0/pwm1=hwmon1/temp1_input",
                "FCFANS=hwmon0/pwm1=hwmon0/fan1_input",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fan_host, "FANCONTROL_CONFIG_PATH", fancontrol_path)

    raw_fan = SimpleNamespace(
        name="主板 PWM1",
        pwm_path=str(board_hwmon / "pwm1"),
        rpm_input=str(board_hwmon / "fan1_input"),
        min_pwm=20,
        max_pwm=240,
    )
    cpu_sensor = SimpleNamespace(
        name="CPU Tctl",
        unit="°C",
        source="hwmon",
        internal_id=str(cpu_hwmon / "temp1_input"),
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}

    display_fan = page._display_fans_from_monitor([raw_fan], [cpu_sensor])[0]
    page._fans = [display_fan]
    page._loaded = True
    page._refresh_summary()
    page._refresh_fan_table()

    assert display_fan.name == "CPU_FAN? · PWM1/FAN1"
    assert display_fan.type_label == "CPU 风扇"
    assert display_fan.header_label == "CPU_FAN?"
    assert display_fan.header_confirmed is False
    assert display_fan.sensor_label == "CPU Tctl"
    assert "fancontrol FCTEMPS" in display_fan.sensor_basis
    assert "fancontrol 转速: hwmon0/fan1_input" in display_fan.detail_text
    assert page.fan_table.item(0, 3).text() == "CPU Tctl"
    assert "fancontrol FCTEMPS" in page.fan_table.item(0, 3).toolTip()
    assert "关联传感器：CPU Tctl" in page.channel_evidence_label.text()

    page.close()
    app.quit()


def test_fan_host_keeps_non_primary_cpu_bound_pwm_as_unknown_but_shows_sensor(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import usb9_lcd.gui.fan_host as fan_host
    from usb9_lcd.gui.fan_host import FanControlHostPage

    board_hwmon = tmp_path / "hwmon0"
    cpu_hwmon = tmp_path / "hwmon1"
    board_hwmon.mkdir()
    cpu_hwmon.mkdir()
    (board_hwmon / "name").write_text("nct6798", encoding="utf-8")
    (board_hwmon / "pwm3").write_text("128", encoding="utf-8")
    (board_hwmon / "fan3_input").write_text("880", encoding="utf-8")
    (cpu_hwmon / "name").write_text("k10temp", encoding="utf-8")
    (cpu_hwmon / "temp1_input").write_text("43000", encoding="utf-8")
    fancontrol_path = tmp_path / "fancontrol"
    fancontrol_path.write_text(
        "FCTEMPS=hwmon0/pwm3=hwmon1/temp1_input\nFCFANS=hwmon0/pwm3=hwmon0/fan3_input\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(fan_host, "FANCONTROL_CONFIG_PATH", fancontrol_path)

    raw_fan = SimpleNamespace(
        name="主板 PWM3",
        pwm_path=str(board_hwmon / "pwm3"),
        rpm_input=str(board_hwmon / "fan3_input"),
        min_pwm=20,
        max_pwm=240,
    )
    cpu_sensor = SimpleNamespace(
        name="CPU Tctl",
        unit="°C",
        source="hwmon",
        internal_id=str(cpu_hwmon / "temp1_input"),
    )

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}

    display_fan = page._display_fans_from_monitor([raw_fan], [cpu_sensor])[0]

    assert display_fan.name == "未识别通道 · PWM3/FAN3"
    assert display_fan.type_label == "未识别通道"
    assert display_fan.header_label == ""
    assert display_fan.sensor_label == "CPU Tctl"
    assert "fancontrol FCTEMPS" in display_fan.sensor_basis

    page.close()
    app.quit()


def test_embedded_fan_control_groups_roles_and_maps_slider_back_to_original_name(tmp_path: Path):
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout

    from usb9_lcd.gui.fan_host import EmbeddedFanControlPanel, FanControlHostPage

    class FakeSlider(QFrame):
        auto_toggled = Signal(str, bool)
        pwm_changed = Signal(str, int)

        def __init__(self, fan_name: str):
            super().__init__()
            self.fan_name = fan_name
            self.control_enabled = False
            layout = QVBoxLayout(self)
            self._name_label = QLabel(fan_name)
            layout.addWidget(self._name_label)

        def set_control_enabled(self, enabled: bool):
            self.control_enabled = bool(enabled)

        def update_rpm(self, rpm: int):
            self.rpm = rpm

    class FakeMonitor:
        control_enabled = False

        def __init__(self):
            self.manual_calls = []
            self.auto_calls = []

        def set_fan_manual(self, name, pwm):
            self.manual_calls.append((name, pwm))

        def set_fan_auto(self, name):
            self.auto_calls.append(name)

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("nct6799", encoding="utf-8")
    labels = {1: "CPU_FAN", 2: "AIO_PUMP", 3: "CHA_FAN1"}
    for index, label in labels.items():
        (hwmon / f"pwm{index}").write_text("128", encoding="utf-8")
        (hwmon / f"fan{index}_input").write_text(str(900 + index), encoding="utf-8")
        (hwmon / f"fan{index}_label").write_text(label, encoding="utf-8")

    raw_fans = [
        SimpleNamespace(
            name=f"主板 PWM{index}",
            pwm_path=str(hwmon / f"pwm{index}"),
            rpm_input=str(hwmon / f"fan{index}_input"),
            min_pwm=20,
            max_pwm=240,
        )
        for index in labels
    ]

    app = QApplication.instance() or QApplication([])
    page = FanControlHostPage(auto_grant_pwm_permissions=False, auto_probe_hwmon_drivers=False)
    page._fan_label_overrides = {}
    page._fans = page._display_fans_from_monitor(raw_fans, [])

    panel = EmbeddedFanControlPanel(FakeSlider)
    panel.populate_fans(page._controllable_display_fans(), [])

    assert set(panel._section_widgets) == {"CPU 风扇", "水泵/AIO", "机箱风扇"}
    assert panel._fan_roles["AIO_PUMP · PWM2/FAN2"] == "水泵/AIO"
    assert panel._sliders["AIO_PUMP · PWM2/FAN2"]._name_label.text() == "AIO_PUMP · 水泵"
    assert panel._role_filter_combo.count() == 4

    monitor = FakeMonitor()
    page._connect_display_fan_control(panel, monitor)
    panel._sliders["AIO_PUMP · PWM2/FAN2"].pwm_changed.emit("AIO_PUMP · PWM2/FAN2", 201)
    panel._sliders["AIO_PUMP · PWM2/FAN2"].auto_toggled.emit("AIO_PUMP · PWM2/FAN2", True)

    assert monitor.manual_calls == [("主板 PWM2", 201)]
    assert monitor.auto_calls == ["主板 PWM2"]

    panel.close()
    page.close()
    app.quit()
