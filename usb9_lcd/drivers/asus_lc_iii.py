from __future__ import annotations

from types import TracebackType

from usb9_lcd.device import choose_interfaces, discover_lcd_interfaces
from usb9_lcd.drivers.base import (
    Capability,
    DeviceConnection,
    DisplayDevice,
    PixelFormat,
    PixelStyle,
    PreviewProfile,
    PreviewShape,
)
from usb9_lcd.protocol import LcdProtocol
from usb9_lcd.transport import HidApiTransport, HidrawTransport


class AsusLcIiiDriver:
    driver_id = "asus.lc_iii"
    display_name = "ASUS TUF Gaming LC III LCD"
    width = 320
    height = 320

    def discover(self) -> list[DisplayDevice]:
        try:
            control, data = choose_interfaces(discover_lcd_interfaces())
        except ValueError:
            return []

        return [
            DisplayDevice(
                connection=DeviceConnection(
                    driver_id=self.driver_id,
                    display_name=self.display_name,
                    paths=(control.path, data.path),
                    writable=control.can_write and data.can_write,
                    readable=control.can_read and data.can_read,
                    details=f"control={control.path} data={data.path}",
                ),
                width=self.width,
                height=self.height,
                pixel_format=PixelFormat.JPEG,
                preview=PreviewProfile(
                    width=self.width,
                    height=self.height,
                    shape=PreviewShape.SQUARE,
                    pixel_style=PixelStyle.CONTINUOUS,
                    orientation=180,
                    label=self.display_name,
                ),
                capabilities=frozenset({Capability.STATIC_IMAGE, Capability.ANIMATION, Capability.SENSOR_MONITOR}),
            )
        ]

    def upload_static_frame(self, device: DisplayDevice, frame: bytes) -> None:
        with self.open_upload_session(device) as session:
            session.upload_static_frame(frame)

    def set_display_brightness(self, device: DisplayDevice, level: int) -> None:
        with self.open_upload_session(device) as session:
            if session.protocol is None:
                raise RuntimeError("upload session is not open")
            session.protocol.set_display_brightness(level)

    def set_display_power(self, device: DisplayDevice, enabled: bool) -> None:
        with self.open_upload_session(device) as session:
            if session.protocol is None:
                raise RuntimeError("upload session is not open")
            session.protocol.set_display_power(enabled)

    def open_upload_session(self, device: DisplayDevice) -> "AsusLcIiiUploadSession":
        return AsusLcIiiUploadSession(device)


class AsusLcIiiUploadSession:
    def __init__(self, device: DisplayDevice) -> None:
        control_path, data_path = device.connection.paths
        self.control_transport = _transport_for_path(control_path)
        self.data_transport = _transport_for_path(data_path)
        self.protocol: LcdProtocol | None = None

    def __enter__(self) -> "AsusLcIiiUploadSession":
        control = self.control_transport.__enter__()
        data = self.data_transport.__enter__()
        self.protocol = LcdProtocol(control, data)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self.data_transport.__exit__(exc_type, exc_value, traceback)
        finally:
            self.control_transport.__exit__(exc_type, exc_value, traceback)
            self.protocol = None

    def upload_static_frame(self, frame: bytes) -> None:
        if self.protocol is None:
            raise RuntimeError("upload session is not open")
        self.protocol.upload_frame(frame)


def _transport_for_path(path):  # noqa: ANN001
    path_text = str(path)
    if path_text.startswith("\\\\?\\HID#"):
        return HidApiTransport(path_text)
    return HidrawTransport(path)
