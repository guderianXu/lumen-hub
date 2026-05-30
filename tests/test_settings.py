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


def test_openrgb_profile_matches_asus_b850a_behavior():
    profile = profile_for_device_name("ASUS ROG STRIX B850-A GAMING WIFI S")

    assert profile.key == "asus-rog-strix-b850-a"
    assert profile.static_strategy == "whole-device-static-all-zones-same-color"
    assert profile.static_zone_size == 30
    assert profile.effect_aliases["chase"][0] == "chase"
