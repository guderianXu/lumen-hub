from usb9_lcd.lighting import LightingSettings, LightingTarget, OpenRgbLightingController
from usb9_lcd.lighting.effects import effect_uses_color
from usb9_lcd.lighting.engine import build_lighting_apply_plan
from usb9_lcd.lighting.software_effects import (
    SOFTWARE_LIGHTING_EFFECTS,
    render_software_effect_frame,
    software_effect_interval_seconds,
)


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
        self.color_frames = []
        self.zones = []
        self.custom_mode_set = False

    def set_mode(self, mode, save=False, force=False):
        self.set_modes.append((mode.name, save, force))

    def set_color(self, color, fast=False):
        self.colors.append((color, fast))

    def set_colors(self, colors, fast=False):
        self.color_frames.append((list(colors), fast))

    def set_custom_mode(self):
        self.custom_mode_set = True


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
        self.color_frames = []
        self.resized_to = None

    def resize(self, size):
        self.resized_to = size
        self.leds = [object()] * size

    def set_color(self, color, fast=False):
        self.colors.append((color, fast))

    def set_colors(self, colors, fast=False):
        self.color_frames.append((list(colors), fast))


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
    assert len(zone.colors) == 2


def test_openrgb_asus_static_profile_reapplies_all_zone_color():
    controller = OpenRgbLightingController()
    controller.client = FakeClient()
    controller.client.devices[0].name = "ASUS ROG STRIX B850-A GAMING WIFI S"
    zone = FakeZone()
    controller.client.devices[0].zones = [zone]
    controller.targets = [
        LightingTarget(
            id="device:0",
            name="ASUS ROG STRIX B850-A GAMING WIFI S / 全部",
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


def test_lighting_engine_builds_asus_static_stability_plan():
    plan = build_lighting_apply_plan(
        effect="static",
        device_name="ASUS ROG STRIX B850-A GAMING WIFI S",
        target_zone_index=None,
        requested_zone_size=None,
        default_static_reapply_count=2,
    )

    assert plan.zone_size == 30
    assert plan.stable_whole_device_color is True
    assert plan.color_reapply_count == 4
    assert plan.should_apply_explicit_color("mode-color") is True


def test_lighting_engine_prefers_clean_chase_alias_for_asus_profile():
    plan = build_lighting_apply_plan(
        effect="chase",
        device_name="ASUS ROG STRIX B850-A GAMING WIFI S",
        target_zone_index=None,
        requested_zone_size=30,
        default_static_reapply_count=2,
    )

    assert plan.mode_aliases[:2] == ("chase", "running lights")
    assert plan.uses_color is True


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


def test_software_effect_renderer_produces_distinct_frames_for_expanded_effects():
    base = (255, 32, 16)
    for effect in SOFTWARE_LIGHTING_EFFECTS:
        first = render_software_effect_frame(effect, led_count=30, frame_index=0, base_color=base)
        second = render_software_effect_frame(effect, led_count=30, frame_index=6, base_color=base)

        assert len(first) == 30
        assert len(second) == 30
        assert any(color != (0, 0, 0) for color in first + second)
        assert first != second


def test_expanded_software_effects_are_color_aware():
    for effect in SOFTWARE_LIGHTING_EFFECTS:
        assert effect_uses_color(effect) is True


def test_meteor_effect_has_no_long_black_gap():
    frames = [
        render_software_effect_frame("meteor", led_count=30, frame_index=index, base_color=(255, 0, 0))
        for index in range(90)
    ]

    assert all(any(color != (0, 0, 0) for color in frame) for frame in frames)
    assert software_effect_interval_seconds(50) <= 0.08


def test_openrgb_missing_expanded_effect_starts_software_animation_on_zone():
    controller = OpenRgbLightingController()
    controller.client = FakeClient()
    zone = FakeZone()
    controller.client.devices[0].modes = [FakeMode("Static"), FakeMode("Direct"), FakeMode("Off")]
    controller.client.devices[0].zones = [zone]
    controller.targets = [
        LightingTarget(
            id="device:0:zone:0",
            name="ARGB Controller / Zone 0",
            device_index=0,
            zone_index=0,
            modes=("Static", "Direct", "Off"),
        )
    ]
    controller.refresh = lambda: list(controller.targets)

    controller.apply(
        LightingSettings(
            target_id="device:0:zone:0",
            effect="meteor",
            color="#ff0000",
            brightness_percent=100,
            speed_percent=80,
            zone_size=12,
        )
    )

    try:
        assert controller.client.devices[0].set_modes == [("Direct", False, True)]
        assert zone.resized_to == 12
        assert zone.color_frames
        colors, fast = zone.color_frames[0]
        assert fast is True
        assert len(colors) == 12
        assert any(getattr(color, "red", 0) > 0 for color in colors)
    finally:
        controller._stop_software_effect()


def test_openrgb_native_expanded_effect_is_not_overwritten_by_static_color():
    controller = OpenRgbLightingController()
    controller.client = FakeClient()
    controller.client.devices[0].modes = [FakeMode("Matrix"), FakeMode("Off")]
    controller.targets = [
        LightingTarget(
            id="device:0",
            name="ARGB Controller / 全部",
            device_index=0,
            zone_index=None,
            modes=("Matrix", "Off"),
        )
    ]

    controller.apply(
        LightingSettings(
            target_id="device:0",
            effect="matrix",
            color="#00ff00",
            brightness_percent=100,
            speed_percent=50,
        )
    )

    assert controller.client.devices[0].set_modes == [("Matrix", False, True)]
    assert controller.client.devices[0].colors == []
    assert controller.client.devices[0].color_frames == []
