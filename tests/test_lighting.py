from usb9_lcd.lighting import LightingSettings, LightingTarget, OpenRgbLightingController


class FakeMode:
    def __init__(self, name):
        self.name = name


class FakeDevice:
    def __init__(self):
        self.modes = [FakeMode("Static"), FakeMode("Off")]
        self.set_modes = []
        self.colors = []
        self.zones = []

    def set_mode(self, mode, save=False, force=False):
        self.set_modes.append((mode.name, save, force))

    def set_color(self, color, fast=False):
        self.colors.append((color, fast))


class FakeClient:
    def __init__(self):
        self.devices = [FakeDevice()]


def test_openrgb_off_uses_native_off_mode_before_black_color():
    controller = OpenRgbLightingController()
    controller.client = FakeClient()
    controller.targets = [
        LightingTarget(
            id="device:0",
            name="ASUS Motherboard / 全部",
            device_index=0,
            zone_index=None,
            modes=("Static", "Off"),
        )
    ]

    controller.apply(
        LightingSettings(
            target_id="device:0",
            effect="off",
            color="#ff0000",
            brightness_percent=80,
            speed_percent=50,
            save=True,
        )
    )

    device = controller.client.devices[0]
    assert device.set_modes == [("Off", True, True)]
    assert device.colors == []


class FakeZone:
    def __init__(self):
        self.leds = []
        self.colors = []
        self.resized_to = None

    def resize(self, size):
        self.resized_to = size
        self.leds = [object()] * size

    def set_color(self, color, fast=False):
        self.colors.append((color, fast))


def test_openrgb_resizes_empty_addressable_zone_before_setting_color():
    controller = OpenRgbLightingController()
    controller.client = FakeClient()
    zone = FakeZone()
    controller.client.devices[0].zones = [zone]
    controller.targets = [
        LightingTarget(
            id="device:0:zone:0",
            name="ASUS Motherboard / Aura Addressable 1",
            device_index=0,
            zone_index=0,
            modes=("Static", "Off"),
        )
    ]
    controller.refresh = lambda: list(controller.targets)

    controller.apply(
        LightingSettings(
            target_id="device:0:zone:0",
            effect="static",
            color="#ff0000",
            brightness_percent=100,
            speed_percent=50,
            zone_size=30,
        )
    )

    assert zone.resized_to == 30
    assert len(zone.colors) == 1
