from pathlib import Path

from PIL import Image

from usb9_lcd.cli import build_parser
from usb9_lcd.cli import main
from usb9_lcd.device import HidInterface


def test_parser_accepts_detect_command():
    parser = build_parser()
    args = parser.parse_args(["detect"])

    assert args.command == "detect"


def test_parser_accepts_show_command():
    parser = build_parser()
    args = parser.parse_args(
        [
            "show",
            "sample.png",
            "--dry-run",
            "--fit",
            "stretch",
            "--rotate",
            "90",
            "--background",
            "#123456",
        ]
    )

    assert args.command == "show"
    assert args.image == "sample.png"
    assert args.dry_run is True
    assert args.fit == "stretch"
    assert args.rotate == 90
    assert args.background == "#123456"


def test_parser_accepts_control_default_command():
    parser = build_parser()
    args = parser.parse_args(["control", "default"])

    assert args.command == "control"
    assert args.control_action == "default"


def test_parser_accepts_control_brightness_command():
    parser = build_parser()
    args = parser.parse_args(["control", "brightness", "80"])

    assert args.command == "control"
    assert args.control_action == "brightness"
    assert args.value == 80


def test_parser_accepts_control_power_command():
    parser = build_parser()
    args = parser.parse_args(["control", "power", "on"])

    assert args.command == "control"
    assert args.control_action == "power"
    assert args.state == "on"


def test_parser_accepts_control_screen_type_command():
    parser = build_parser()
    args = parser.parse_args(["control", "screen-type", "2"])

    assert args.command == "control"
    assert args.control_action == "screen-type"
    assert args.value == 2


def test_show_dry_run_converts_image(tmp_path: Path, capsys):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (20, 20), "blue").save(image_path)

    exit_code = main(["show", str(image_path), "--dry-run", "--width", "10", "--height", "10"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.startswith("prepared frame: ")
    assert captured.out.endswith(" bytes\n")
    assert "200 bytes" not in captured.out


def test_show_defaults_to_tuf_lc_iii_frame_size(tmp_path: Path, capsys):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (20, 20), "blue").save(image_path)

    exit_code = main(["show", str(image_path), "--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.startswith("prepared frame: ")
    assert captured.out.endswith(" bytes\n")


def test_show_missing_image_dry_run_returns_one_without_traceback(tmp_path: Path, capsys):
    exit_code = main(["show", str(tmp_path / "missing.png"), "--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out.startswith("failed to prepare image:")
    assert "Traceback" not in captured.out
    assert captured.err == ""


def test_show_invalid_image_dry_run_returns_one_without_traceback(tmp_path: Path, capsys):
    image_path = tmp_path / "invalid.png"
    image_path.write_text("not an image", encoding="utf-8")

    exit_code = main(["show", str(image_path), "--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out.startswith("failed to prepare image:")
    assert "Traceback" not in captured.out
    assert captured.err == ""


def test_show_invalid_dimensions_return_one_without_traceback(tmp_path: Path, capsys):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (20, 20), "blue").save(image_path)

    exit_code = main(["show", str(image_path), "--dry-run", "--width", "0"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == "failed to prepare image: width and height must be positive\n"
    assert "Traceback" not in captured.out
    assert captured.err == ""


def test_show_returns_one_when_no_device_after_valid_image(monkeypatch, tmp_path: Path, capsys):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (20, 20), "blue").save(image_path)
    monkeypatch.setattr("usb9_lcd.cli.discover_from_sysfs", lambda: [])

    assert main(["show", str(image_path), "--width", "10", "--height", "10"]) == 1

    captured = capsys.readouterr()
    assert captured.out == "ASUS LCD 0b05:1c7b not found\n"


def test_show_returns_one_when_interfaces_are_not_writable(monkeypatch, tmp_path: Path, capsys):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (20, 20), "blue").save(image_path)
    interfaces = [
        HidInterface(path=tmp_path / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=False),
        HidInterface(path=tmp_path / "hidraw11", name="hidraw11", report_size=1024, can_read=True, can_write=True),
    ]
    monkeypatch.setattr("usb9_lcd.cli.discover_from_sysfs", lambda: interfaces)

    assert main(["show", str(image_path), "--width", "10", "--height", "10"]) == 1

    captured = capsys.readouterr()
    assert "LCD hidraw nodes are not writable." in captured.out
    assert "plugdev" in captured.out
    assert 'SUBSYSTEM=="hidraw"' in captured.out


def test_show_uploads_frame_with_mocked_hardware(monkeypatch, tmp_path: Path, capsys):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (20, 20), "blue").save(image_path)
    interfaces = [
        HidInterface(path=tmp_path / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
        HidInterface(path=tmp_path / "hidraw11", name="hidraw11", report_size=1024, can_read=True, can_write=True),
    ]
    opened_paths = []
    uploads = []

    class FakeTransport:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            opened_paths.append(self.path)
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    class FakeProtocol:
        def __init__(self, control, data, data_report_size):
            self.control = control
            self.data = data
            self.data_report_size = data_report_size

        def upload_frame(self, frame):
            uploads.append((self.control.path, self.data.path, self.data_report_size, frame))

    monkeypatch.setattr("usb9_lcd.cli.discover_from_sysfs", lambda: interfaces)
    monkeypatch.setattr("usb9_lcd.cli.HidrawTransport", FakeTransport)
    monkeypatch.setattr("usb9_lcd.cli.LcdProtocol", FakeProtocol)

    assert main(["show", str(image_path), "--width", "10", "--height", "10"]) == 0

    captured = capsys.readouterr()
    assert captured.out.startswith("frame transfer completed: ")
    assert opened_paths == [tmp_path / "hidraw10", tmp_path / "hidraw11"]
    assert uploads[0][:3] == (tmp_path / "hidraw10", tmp_path / "hidraw11", 1024)
    assert uploads[0][3].startswith(b"\xff\xd8")


def test_show_upload_error_returns_one_with_hardware_message(monkeypatch, tmp_path: Path, capsys):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (20, 20), "blue").save(image_path)
    interfaces = [
        HidInterface(path=tmp_path / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
        HidInterface(path=tmp_path / "hidraw11", name="hidraw11", report_size=1024, can_read=True, can_write=True),
    ]

    class FakeTransport:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            if self.path == tmp_path / "hidraw11":
                raise OSError("device disconnected")
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    monkeypatch.setattr("usb9_lcd.cli.discover_from_sysfs", lambda: interfaces)
    monkeypatch.setattr("usb9_lcd.cli.HidrawTransport", FakeTransport)

    assert main(["show", str(image_path), "--width", "10", "--height", "10"]) == 1

    captured = capsys.readouterr()
    assert captured.out == "failed to upload frame: device disconnected\n"
    assert "Traceback" not in captured.out


def test_show_permission_error_prints_udev_guidance(monkeypatch, tmp_path: Path, capsys):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (20, 20), "blue").save(image_path)
    interfaces = [
        HidInterface(path=tmp_path / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
        HidInterface(path=tmp_path / "hidraw11", name="hidraw11", report_size=1024, can_read=True, can_write=True),
    ]

    class FakeTransport:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            raise PermissionError("permission denied")

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    monkeypatch.setattr("usb9_lcd.cli.discover_from_sysfs", lambda: interfaces)
    monkeypatch.setattr("usb9_lcd.cli.HidrawTransport", FakeTransport)

    assert main(["show", str(image_path), "--width", "10", "--height", "10"]) == 1

    captured = capsys.readouterr()
    assert "failed to upload frame: permission denied" in captured.out
    assert "LCD hidraw nodes are not writable." in captured.out
    assert "plugdev" in captured.out
    assert 'SUBSYSTEM=="hidraw"' in captured.out
    assert "Traceback" not in captured.out


def test_show_protocol_error_returns_one_with_hardware_message(monkeypatch, tmp_path: Path, capsys):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (20, 20), "blue").save(image_path)
    interfaces = [
        HidInterface(path=tmp_path / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
        HidInterface(path=tmp_path / "hidraw11", name="hidraw11", report_size=1024, can_read=True, can_write=True),
    ]

    class FakeTransport:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    class FakeProtocol:
        def __init__(self, control, data, data_report_size):
            pass

        def upload_frame(self, frame):
            raise ValueError("sequence does not fit in 2 bytes")

    monkeypatch.setattr("usb9_lcd.cli.discover_from_sysfs", lambda: interfaces)
    monkeypatch.setattr("usb9_lcd.cli.HidrawTransport", FakeTransport)
    monkeypatch.setattr("usb9_lcd.cli.LcdProtocol", FakeProtocol)

    assert main(["show", str(image_path), "--width", "10", "--height", "10"]) == 1

    captured = capsys.readouterr()
    assert captured.out == "failed to upload frame: sequence does not fit in 2 bytes\n"
    assert "Traceback" not in captured.out


def test_show_protocol_os_error_returns_one_with_hardware_message(monkeypatch, tmp_path: Path, capsys):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (20, 20), "blue").save(image_path)
    interfaces = [
        HidInterface(path=tmp_path / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
        HidInterface(path=tmp_path / "hidraw11", name="hidraw11", report_size=1024, can_read=True, can_write=True),
    ]

    class FakeTransport:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    class FakeProtocol:
        def __init__(self, control, data, data_report_size):
            pass

        def upload_frame(self, frame):
            raise OSError("short HID write")

    monkeypatch.setattr("usb9_lcd.cli.discover_from_sysfs", lambda: interfaces)
    monkeypatch.setattr("usb9_lcd.cli.HidrawTransport", FakeTransport)
    monkeypatch.setattr("usb9_lcd.cli.LcdProtocol", FakeProtocol)

    assert main(["show", str(image_path), "--width", "10", "--height", "10"]) == 1

    captured = capsys.readouterr()
    assert "failed to upload frame: short HID write" in captured.out
    assert "Traceback" not in captured.out


def test_control_default_sends_screen_type_one(monkeypatch, tmp_path: Path, capsys):
    interfaces = [
        HidInterface(path=tmp_path / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
        HidInterface(path=tmp_path / "hidraw11", name="hidraw11", report_size=1024, can_read=True, can_write=True),
    ]
    calls = []

    class FakeTransport:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    class FakeProtocol:
        def __init__(self, control, data, data_report_size):
            self.control = control
            self.data = data
            self.data_report_size = data_report_size

        def set_screen_type(self, value):
            calls.append(("screen_type", value, self.control.path, self.data.path, self.data_report_size))

        def set_display_power(self, enabled):
            calls.append(("power", enabled))

        def set_display_brightness(self, level):
            calls.append(("brightness", level))

    monkeypatch.setattr("usb9_lcd.cli.discover_from_sysfs", lambda: interfaces)
    monkeypatch.setattr("usb9_lcd.cli.HidrawTransport", FakeTransport)
    monkeypatch.setattr("usb9_lcd.cli.LcdProtocol", FakeProtocol)

    assert main(["control", "default"]) == 0

    captured = capsys.readouterr()
    assert captured.out == "control command completed: default\n"
    assert calls == [
        ("power", True),
        ("brightness", 100),
        ("screen_type", 1, tmp_path / "hidraw10", tmp_path / "hidraw11", 1024),
    ]


def test_control_power_sends_state(monkeypatch, tmp_path: Path, capsys):
    interfaces = [
        HidInterface(path=tmp_path / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
        HidInterface(path=tmp_path / "hidraw11", name="hidraw11", report_size=1024, can_read=True, can_write=True),
    ]
    calls = []

    class FakeTransport:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    class FakeProtocol:
        def __init__(self, control, data, data_report_size):
            pass

        def set_display_power(self, enabled):
            calls.append(("power", enabled))

    monkeypatch.setattr("usb9_lcd.cli.discover_from_sysfs", lambda: interfaces)
    monkeypatch.setattr("usb9_lcd.cli.HidrawTransport", FakeTransport)
    monkeypatch.setattr("usb9_lcd.cli.LcdProtocol", FakeProtocol)

    assert main(["control", "power", "on"]) == 0

    captured = capsys.readouterr()
    assert captured.out == "control command completed: power on\n"
    assert calls == [("power", True)]


def test_control_brightness_sends_level(monkeypatch, tmp_path: Path, capsys):
    interfaces = [
        HidInterface(path=tmp_path / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
        HidInterface(path=tmp_path / "hidraw11", name="hidraw11", report_size=1024, can_read=True, can_write=True),
    ]
    calls = []

    class FakeTransport:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    class FakeProtocol:
        def __init__(self, control, data, data_report_size):
            pass

        def set_display_brightness(self, level):
            calls.append(("brightness", level))

    monkeypatch.setattr("usb9_lcd.cli.discover_from_sysfs", lambda: interfaces)
    monkeypatch.setattr("usb9_lcd.cli.HidrawTransport", FakeTransport)
    monkeypatch.setattr("usb9_lcd.cli.LcdProtocol", FakeProtocol)

    assert main(["control", "brightness", "80"]) == 0

    captured = capsys.readouterr()
    assert captured.out == "control command completed: brightness 80\n"
    assert calls == [("brightness", 80)]


def test_control_screen_type_sends_safe_value(monkeypatch, tmp_path: Path, capsys):
    interfaces = [
        HidInterface(path=tmp_path / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
        HidInterface(path=tmp_path / "hidraw11", name="hidraw11", report_size=1024, can_read=True, can_write=True),
    ]
    calls = []

    class FakeTransport:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    class FakeProtocol:
        def __init__(self, control, data, data_report_size):
            pass

        def set_screen_type(self, value):
            calls.append(("screen_type", value))

    monkeypatch.setattr("usb9_lcd.cli.discover_from_sysfs", lambda: interfaces)
    monkeypatch.setattr("usb9_lcd.cli.HidrawTransport", FakeTransport)
    monkeypatch.setattr("usb9_lcd.cli.LcdProtocol", FakeProtocol)

    assert main(["control", "screen-type", "2"]) == 0

    captured = capsys.readouterr()
    assert captured.out == "control command completed: screen-type 2\n"
    assert calls == [("screen_type", 2)]


def test_control_rejects_unsafe_screen_type(capsys):
    assert main(["control", "screen-type", "4"]) == 1

    captured = capsys.readouterr()
    assert captured.out == "failed to run control command: screen-type must be 1 or 2\n"


def test_control_rejects_invalid_brightness(capsys):
    assert main(["control", "brightness", "300"]) == 1

    captured = capsys.readouterr()
    assert captured.out == "failed to run control command: brightness must be between 0 and 255\n"


def test_detect_returns_one_when_asus_lcd_not_found(monkeypatch, capsys):
    monkeypatch.setattr("usb9_lcd.cli.discover_from_sysfs", lambda: [])

    assert main(["detect"]) == 1

    captured = capsys.readouterr()
    assert captured.out == "ASUS LCD 0b05:1c7b not found\n"


def test_detect_returns_one_when_only_one_interface_is_found(monkeypatch, capsys, tmp_path):
    interfaces = [
        HidInterface(path=tmp_path / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
    ]
    monkeypatch.setattr("usb9_lcd.cli.discover_from_sysfs", lambda: interfaces)

    assert main(["detect"]) == 1

    captured = capsys.readouterr()
    assert captured.out == "expected HID report sizes 440 and 1024 for ASUS LCD; found 440\n"


def test_detect_returns_one_when_data_interface_is_missing(monkeypatch, capsys, tmp_path):
    interfaces = [
        HidInterface(path=tmp_path / "hidraw9", name="hidraw9", report_size=16, can_read=True, can_write=True),
        HidInterface(path=tmp_path / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
    ]
    monkeypatch.setattr("usb9_lcd.cli.discover_from_sysfs", lambda: interfaces)

    assert main(["detect"]) == 1

    captured = capsys.readouterr()
    assert captured.out == "expected HID report sizes 440 and 1024 for ASUS LCD; found 16, 440\n"


def test_detect_prints_control_and_data_interfaces(monkeypatch, capsys, tmp_path):
    interfaces = [
        HidInterface(path=tmp_path / "hidraw10", name="hidraw10", report_size=440, can_read=True, can_write=True),
        HidInterface(path=tmp_path / "hidraw11", name="hidraw11", report_size=1024, can_read=True, can_write=True),
    ]
    monkeypatch.setattr("usb9_lcd.cli.discover_from_sysfs", lambda: interfaces)

    assert main(["detect"]) == 0

    captured = capsys.readouterr()
    assert captured.out == (
        f"control: path={tmp_path / 'hidraw10'} report_size=440 read=True write=True\n"
        f"data: path={tmp_path / 'hidraw11'} report_size=1024 read=True write=True\n"
    )
