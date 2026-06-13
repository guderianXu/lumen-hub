from pathlib import Path

import pytest

from usb9_lcd.device import HidInterface, choose_interfaces, discover_from_hidapi, discover_from_sysfs


def make_hidraw(root: Path, name: str, hid_id: str, report: bytes, mode: int = 0o660) -> Path:
    hidraw = root / "class" / "hidraw" / name
    device = hidraw / "device"
    device.mkdir(parents=True)
    (device / "uevent").write_text(
        f"HID_ID={hid_id}\nHID_NAME=ASUS TUF GAMING LC III 360 ARGB LCD\nHID_UNIQ=A247392SS000000\n",
        encoding="utf-8",
    )
    (device / "report_descriptor").write_bytes(report)
    dev = root / "dev" / name
    dev.parent.mkdir(parents=True, exist_ok=True)
    dev.write_bytes(b"")
    dev.chmod(mode)
    return dev


def test_discover_from_sysfs_matches_asus_lcd(tmp_path):
    make_hidraw(tmp_path, "hidraw10", "0003:00000B05:00001C7B", bytes.fromhex("0606ff0901a101150026ff00750896b8010901810296b80109019102c0"))
    make_hidraw(tmp_path, "hidraw11", "0003:00000B05:00001C7B", bytes.fromhex("0606ff0901a101150026ff0075089610000901810296000409019102c0"))

    interfaces = discover_from_sysfs(sys_root=tmp_path, dev_root=tmp_path / "dev")

    assert interfaces == [
        HidInterface(path=tmp_path / "dev" / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
        HidInterface(path=tmp_path / "dev" / "hidraw11", name="hidraw11", report_size=1024, can_read=True, can_write=True),
    ]


def test_discover_from_sysfs_ignores_other_asus_devices(tmp_path):
    make_hidraw(tmp_path, "hidraw9", "0003:00000B05:000019AF", b"")

    assert discover_from_sysfs(sys_root=tmp_path, dev_root=tmp_path / "dev") == []


def test_discover_from_sysfs_ignores_malformed_hid_id(tmp_path):
    make_hidraw(tmp_path, "hidraw9", "0003:nothex:00001C7B", bytes.fromhex("96b801"))

    assert discover_from_sysfs(sys_root=tmp_path, dev_root=tmp_path / "dev") == []


def test_discover_from_sysfs_skips_unknown_report_size(tmp_path):
    make_hidraw(tmp_path, "hidraw9", "0003:00000B05:00001C7B", b"unknown")
    make_hidraw(tmp_path, "hidraw10", "0003:00000B05:00001C7B", bytes.fromhex("96b801"))

    assert discover_from_sysfs(sys_root=tmp_path, dev_root=tmp_path / "dev") == [
        HidInterface(path=tmp_path / "dev" / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
    ]


def test_discover_from_sysfs_skips_missing_report_descriptor(tmp_path):
    bad = make_hidraw(tmp_path, "hidraw9", "0003:00000B05:00001C7B", bytes.fromhex("96b801"))
    (bad.parent.parent / "class" / "hidraw" / "hidraw9" / "device" / "report_descriptor").unlink()
    make_hidraw(tmp_path, "hidraw10", "0003:00000B05:00001C7B", bytes.fromhex("96b801"))

    assert discover_from_sysfs(sys_root=tmp_path, dev_root=tmp_path / "dev") == [
        HidInterface(path=tmp_path / "dev" / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
    ]


def test_choose_interfaces_requires_at_least_two_interfaces(tmp_path):
    interface = HidInterface(path=tmp_path / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True)

    with pytest.raises(ValueError, match="expected HID report sizes 440 and 1024 for ASUS LCD; found 440"):
        choose_interfaces([interface])


def test_choose_interfaces_selects_440_control_and_1024_data(tmp_path):
    control = HidInterface(path=tmp_path / "hidraw-control", name="hidraw-control", report_size=440, can_read=True, can_write=True)
    data = HidInterface(path=tmp_path / "hidraw-data", name="hidraw-data", report_size=1024, can_read=True, can_write=True)

    assert choose_interfaces([data, control]) == (control, data)


def test_discover_from_hidapi_matches_windows_asus_interfaces():
    def enumerate_devices(vendor_id, product_id):  # noqa: ANN001
        assert vendor_id == 0x0B05
        assert product_id == 0x1C7B
        return [
            {
                "path": b"\\\\?\\HID#VID_0B05&PID_1C7B&MI_01#a&data#{guid}",
                "interface_number": 1,
                "product_string": "TUF GAMING LC III 360 ARGB LCD",
            },
            {
                "path": b"\\\\?\\HID#VID_0B05&PID_1C7B&MI_00#a&control#{guid}",
                "interface_number": 0,
                "product_string": "TUF GAMING LC III 360 ARGB LCD",
            },
        ]

    interfaces = discover_from_hidapi(enumerate_devices=enumerate_devices)

    assert interfaces == [
        HidInterface(
            path=Path("\\\\?\\HID#VID_0B05&PID_1C7B&MI_00#a&control#{guid}"),
            name="TUF GAMING LC III 360 ARGB LCD MI_00",
            report_size=440,
            can_read=True,
            can_write=True,
        ),
        HidInterface(
            path=Path("\\\\?\\HID#VID_0B05&PID_1C7B&MI_01#a&data#{guid}"),
            name="TUF GAMING LC III 360 ARGB LCD MI_01",
            report_size=1024,
            can_read=True,
            can_write=True,
        ),
    ]


def test_discover_from_hidapi_parses_interface_from_path_when_number_is_missing():
    def enumerate_devices(vendor_id, product_id):  # noqa: ANN001
        return [
            {"path": "\\\\?\\HID#VID_0B05&PID_1C7B&MI_00#a&control#{guid}", "product_string": ""},
            {"path": "\\\\?\\HID#VID_0B05&PID_1C7B&MI_01#a&data#{guid}", "product_string": ""},
        ]

    interfaces = discover_from_hidapi(enumerate_devices=enumerate_devices)

    assert [interface.report_size for interface in interfaces] == [440, 1024]


@pytest.mark.parametrize("report_sizes", [(16, 440), (440, 440), (1024, 1024)])
def test_choose_interfaces_requires_control_and_data_report_sizes(tmp_path, report_sizes):
    interfaces = [
        HidInterface(path=tmp_path / f"hidraw{index}", name=f"hidraw{index}", report_size=report_size, can_read=True, can_write=True)
        for index, report_size in enumerate(report_sizes)
    ]

    with pytest.raises(ValueError, match=r"expected HID report sizes 440 and 1024 for ASUS LCD; found .+"):
        choose_interfaces(interfaces)
