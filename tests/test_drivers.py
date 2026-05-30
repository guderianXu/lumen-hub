from pathlib import Path

from usb9_lcd.device import HidInterface
from usb9_lcd.drivers.asus_lc_iii import AsusLcIiiDriver
from usb9_lcd.drivers.base import (
    Capability,
    DeviceConnection,
    DisplayDevice,
    PixelFormat,
    PixelStyle,
    PreviewProfile,
    PreviewShape,
)


def test_preview_profile_describes_square_asus_screen_without_circle_assumption():
    profile = PreviewProfile(
        width=320,
        height=320,
        shape=PreviewShape.SQUARE,
        pixel_style=PixelStyle.CONTINUOUS,
        orientation=180,
        label="ASUS LC III",
    )

    assert profile.width == 320
    assert profile.height == 320
    assert profile.shape is PreviewShape.SQUARE
    assert profile.pixel_style is PixelStyle.CONTINUOUS
    assert profile.label == "ASUS LC III"


def test_display_device_exposes_driver_metadata_and_capabilities():
    profile = PreviewProfile(
        width=64,
        height=32,
        shape=PreviewShape.RECTANGLE,
        pixel_style=PixelStyle.MATRIX,
    )
    connection = DeviceConnection(
        driver_id="test.driver",
        display_name="Test Matrix",
        paths=(Path("/dev/hidraw-test"),),
        writable=True,
        readable=True,
        details="test device",
    )
    device = DisplayDevice(
        connection=connection,
        width=64,
        height=32,
        pixel_format=PixelFormat.RGB565,
        preview=profile,
        capabilities=frozenset({Capability.STATIC_IMAGE}),
    )

    assert device.driver_id == "test.driver"
    assert device.display_name == "Test Matrix"
    assert device.supports(Capability.STATIC_IMAGE) is True
    assert device.supports(Capability.ANIMATION) is False


def test_asus_driver_discovers_square_static_image_device(monkeypatch, tmp_path):
    interfaces = [
        HidInterface(path=tmp_path / "hidraw0", name="hidraw0", report_size=440, can_read=True, can_write=True),
        HidInterface(path=tmp_path / "hidraw1", name="hidraw1", report_size=1024, can_read=False, can_write=True),
    ]
    monkeypatch.setattr("usb9_lcd.drivers.asus_lc_iii.discover_from_sysfs", lambda: interfaces)

    devices = AsusLcIiiDriver().discover()

    assert len(devices) == 1
    device = devices[0]
    assert device.driver_id == "asus.lc_iii"
    assert device.display_name == "ASUS TUF Gaming LC III LCD"
    assert device.width == 320
    assert device.height == 320
    assert device.pixel_format is PixelFormat.JPEG
    assert device.preview.shape is PreviewShape.SQUARE
    assert device.preview.pixel_style is PixelStyle.CONTINUOUS
    assert device.preview.orientation == 180
    assert device.capabilities == frozenset({Capability.STATIC_IMAGE, Capability.ANIMATION, Capability.SENSOR_MONITOR})
    assert device.connection.paths == (tmp_path / "hidraw0", tmp_path / "hidraw1")
    assert device.connection.writable is True


def test_asus_driver_returns_no_devices_when_interfaces_are_incomplete(monkeypatch):
    monkeypatch.setattr("usb9_lcd.drivers.asus_lc_iii.discover_from_sysfs", lambda: [])

    assert AsusLcIiiDriver().discover() == []


def test_asus_upload_session_reuses_hidraw_transports(monkeypatch, tmp_path):
    device = DisplayDevice(
        connection=DeviceConnection(
            driver_id="asus.lc_iii",
            display_name="ASUS Test LCD",
            paths=(tmp_path / "hidraw0", tmp_path / "hidraw1"),
            writable=True,
            readable=True,
        ),
        width=320,
        height=320,
        pixel_format=PixelFormat.JPEG,
        preview=PreviewProfile(
            width=320,
            height=320,
            shape=PreviewShape.SQUARE,
            pixel_style=PixelStyle.CONTINUOUS,
        ),
        capabilities=frozenset({Capability.STATIC_IMAGE}),
    )
    opened_paths = []
    closed_paths = []
    uploaded_frames = []

    class FakeTransport:
        def __init__(self, path):
            self.path = Path(path)

        def __enter__(self):
            opened_paths.append(self.path)
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            closed_paths.append(self.path)

    class FakeProtocol:
        def __init__(self, control, data):
            self.control = control
            self.data = data

        def upload_frame(self, frame):
            uploaded_frames.append((self.control.path, self.data.path, frame))

    monkeypatch.setattr("usb9_lcd.drivers.asus_lc_iii.HidrawTransport", FakeTransport)
    monkeypatch.setattr("usb9_lcd.drivers.asus_lc_iii.LcdProtocol", FakeProtocol)

    with AsusLcIiiDriver().open_upload_session(device) as session:
        session.upload_static_frame(b"one")
        session.upload_static_frame(b"two")

    assert opened_paths == [tmp_path / "hidraw0", tmp_path / "hidraw1"]
    assert closed_paths == [tmp_path / "hidraw1", tmp_path / "hidraw0"]
    assert uploaded_frames == [
        (tmp_path / "hidraw0", tmp_path / "hidraw1", b"one"),
        (tmp_path / "hidraw0", tmp_path / "hidraw1", b"two"),
    ]
