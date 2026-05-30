from usb9_lcd.lighting import LightingSettings, LightingTarget, OpenRgbLightingController


class FakeMode:
    def __init__(self, name):
        self.name = name


class FakeModeSpecificColorMode:
    value = 2


class FakeModeSpecificMode(FakeMode):
    def __init__(self, name):
        super().__init__(name)
        self.color_mode = FakeModeSpecificColorMode()
        self.colors_min = 1
        self.colors_max = 3
        self.colors = []


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


def test_openrgb_whole_device_static_also_updates_child_zones():
    controller = OpenRgbLightingController()
    controller.client = FakeClient()
    zone = FakeZone()
    controller.client.devices[0].zones = [zone]
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
            effect="static",
            color="#ff0000",
            brightness_percent=100,
            speed_percent=50,
        )
    )

    assert len(controller.client.devices[0].colors) == 1
    assert len(zone.colors) == 1


def test_openrgb_whole_device_static_resizes_empty_child_zones_before_color():
    controller = OpenRgbLightingController()
    controller.client = FakeClient()
    zone = FakeZone()
    controller.client.devices[0].zones = [zone]
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
            effect="static",
            color="#ff0000",
            brightness_percent=100,
            speed_percent=50,
            zone_size=26,
        )
    )

    assert zone.resized_to == 26
    assert len(zone.leds) == 26
    assert len(zone.colors) == 4


def test_openrgb_static_mode_specific_color_also_updates_explicit_color_targets():
    controller = OpenRgbLightingController()
    controller.client = FakeClient()
    mode = FakeModeSpecificMode("Static")
    controller.client.devices[0].modes = [mode, FakeMode("Off")]
    zone = FakeZone()
    controller.client.devices[0].zones = [zone]
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
            effect="static",
            color="#ff0000",
            brightness_percent=100,
            speed_percent=50,
        )
    )

    assert controller.client.devices[0].set_modes == [("Static", False, True)]
    assert len(mode.colors) == 3
    assert len(controller.client.devices[0].colors) == 1
    assert len(zone.colors) == 1


def test_openrgb_whole_device_static_falls_back_to_child_zones():
    class ZoneOnlyDevice(FakeDevice):
        def set_color(self, color, fast=False):
            raise RuntimeError("device color write failed")

    controller = OpenRgbLightingController()
    controller.client = FakeClient()
    controller.client.devices[0] = ZoneOnlyDevice()
    zone = FakeZone()
    controller.client.devices[0].zones = [zone]
    controller.targets = [
        LightingTarget(
            id="device:0",
            name="ARGB Controller / 全部",
            device_index=0,
            zone_index=None,
            modes=("Static", "Off"),
        )
    ]

    controller.apply(
        LightingSettings(
            target_id="device:0",
            effect="static",
            color="#ff0000",
            brightness_percent=100,
            speed_percent=50,
        )
    )

    assert len(zone.colors) == 1


def test_openrgb_dynamic_modes_do_not_overwrite_with_static_color():
    controller = OpenRgbLightingController()
    controller.client = FakeClient()
    controller.client.devices[0].modes.append(FakeMode("Rainbow"))
    controller.targets = [
        LightingTarget(
            id="device:0",
            name="ASUS Motherboard / 全部",
            device_index=0,
            zone_index=None,
            modes=("Static", "Rainbow", "Off"),
        )
    ]

    controller.apply(
        LightingSettings(
            target_id="device:0",
            effect="rainbow",
            color="#000000",
            brightness_percent=0,
            speed_percent=50,
        )
    )

    assert controller.client.devices[0].set_modes == [("Rainbow", False, True)]
    assert controller.client.devices[0].colors == []


def test_openrgb_supports_extra_dynamic_effect_aliases():
    controller = OpenRgbLightingController()
    controller.client = FakeClient()
    controller.client.devices[0].modes = [FakeMode("Wave"), FakeMode("Off")]
    controller.targets = [
        LightingTarget(
            id="device:0",
            name="ARGB Controller / 全部",
            device_index=0,
            zone_index=None,
            modes=("Wave", "Off"),
        )
    ]

    controller.apply(
        LightingSettings(
            target_id="device:0",
            effect="wave",
            color="#ff0000",
            brightness_percent=100,
            speed_percent=50,
        )
    )

    assert controller.client.devices[0].set_modes == [("Wave", False, True)]
    assert controller.client.devices[0].colors == []


def test_openrgb_chase_prefers_clean_chase_mode_and_applies_selected_color():
    controller = OpenRgbLightingController()
    controller.client = FakeClient()
    chase_fade = FakeModeSpecificMode("Chase Fade")
    chase = FakeModeSpecificMode("Chase")
    controller.client.devices[0].modes = [chase_fade, chase, FakeMode("Off")]
    controller.targets = [
        LightingTarget(
            id="device:0",
            name="ASUS Motherboard / 全部",
            device_index=0,
            zone_index=None,
            modes=("Chase Fade", "Chase", "Off"),
        )
    ]

    controller.apply(
        LightingSettings(
            target_id="device:0",
            effect="chase",
            color="#00e5ff",
            brightness_percent=80,
            speed_percent=50,
        )
    )

    assert controller.client.devices[0].set_modes == [("Chase", False, True)]
    assert chase.colors
    assert chase_fade.colors == []


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
    assert len(zone.colors) == 2
