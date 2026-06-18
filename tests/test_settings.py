from __future__ import annotations

import json

from usb9_lcd.gui.settings import CONFIG_VERSION, GuiSettings, load_settings, save_settings
from usb9_lcd.lighting.profiles import openrgb_device_profile_payload, profile_for_device_name


def test_settings_migrates_lighting_device_profiles(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "lighting": {
                    "target_id": "device:0",
                    "target_profiles": {"device:0": {"effect": "static"}},
                }
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(settings_path)

    assert settings.config_version == CONFIG_VERSION
    assert settings.lighting.target_id == "device:0"
    assert settings.lighting.device_profiles == {}


def test_settings_saves_config_version_and_device_profiles(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings = GuiSettings()
    settings.lighting.device_profiles["device:0"] = openrgb_device_profile_payload(
        "ASUS ROG STRIX B850-A GAMING WIFI S",
        "device:0",
    )

    save_settings(settings, settings_path)

    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload["config_version"] == CONFIG_VERSION
    assert payload["lighting"]["device_profiles"]["device:0"]["key"] == "asus-rog-strix-b850-a"


def test_settings_does_not_restore_lianli_write_enable(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings = GuiSettings()
    settings.lianli_wireless.write_enabled = True

    save_settings(settings, settings_path)
    loaded = load_settings(settings_path)

    assert loaded.lianli_wireless.write_enabled is False


def test_settings_migrates_removed_lianli_wireless_effect_to_off(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"lianli_wireless": {"effect": "staggered"}}),
        encoding="utf-8",
    )

    loaded = load_settings(settings_path)

    assert loaded.lianli_wireless.effect == "off"


def test_settings_round_trips_lianli_auto_curve_enable(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings = GuiSettings()
    settings.lianli_wireless.auto_curve_enabled = True

    save_settings(settings, settings_path)
    loaded = load_settings(settings_path)

    assert loaded.lianli_wireless.auto_curve_enabled is True


def test_settings_round_trips_lianli_fan_curve_profiles(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings = GuiSettings()
    settings.lianli_wireless.fan_curve_profiles["quiet"] = [[30, 320], [60, 760], [90, 1200]]
    settings.lianli_wireless.fan_curve_profiles["normal"] = [[30, 520], [60, 1040], [90, 1600]]

    save_settings(settings, settings_path)
    loaded = load_settings(settings_path)

    assert loaded.lianli_wireless.fan_curve_profiles["quiet"] == [[30, 320], [60, 760], [90, 1200]]
    assert loaded.lianli_wireless.fan_curve_profiles["normal"] == [[30, 520], [60, 1040], [90, 1600]]
    assert {"quiet", "normal", "high", "full", "custom"} <= set(loaded.lianli_wireless.fan_curve_profiles)


def test_settings_round_trips_global_scenes_without_losing_openrgb_scenes(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings = GuiSettings()
    settings.active_scene = "Gaming"
    settings.scenes["Gaming"] = {
        "name": "Gaming",
        "screen": {"mode": "monitor_profile", "profile": "Gaming"},
        "openrgb": {"mode": "scene", "scene_name": "ARGB"},
        "lianli_lighting": {"mode": "effect", "effect": "runway", "color": "#ff2d55"},
        "host_fan": {"mode": "preset", "preset": "normal"},
        "lianli_fan": {"mode": "preset", "preset": "normal"},
        "safety": {"dry_run": False, "allow_lianli_write": False},
    }
    settings.lighting.scenes["ARGB"] = {
        "targets": {"device:0": {"effect": "static", "color": "#ffffff"}}
    }

    save_settings(settings, settings_path)
    loaded = load_settings(settings_path)

    assert loaded.active_scene == "Gaming"
    assert loaded.scenes["Gaming"]["openrgb"]["scene_name"] == "ARGB"
    assert loaded.lighting.scenes["ARGB"]["targets"]["device:0"]["effect"] == "static"


def test_settings_defaults_global_scene_storage_to_empty():
    settings = GuiSettings()

    assert settings.active_scene == ""
    assert settings.scenes == {}


def test_openrgb_profile_matches_asus_b850a_behavior():
    profile = profile_for_device_name("ASUS ROG STRIX B850-A GAMING WIFI S")

    assert profile.key == "asus-rog-strix-b850-a"
    assert profile.static_strategy == "whole-device-static-all-zones-same-color"
    assert profile.static_zone_size == 30
    assert profile.effect_aliases["chase"][0] == "chase"
