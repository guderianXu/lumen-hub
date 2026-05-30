from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class OpenRgbDeviceProfile:
    key: str
    label: str
    name_patterns: tuple[str, ...]
    static_strategy: str = "standard"
    static_zone_size: int = 30
    static_reapply_count: int = 2
    effect_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def matches(self, device_name: str) -> bool:
        normalized = device_name.lower()
        return all(part.lower() in normalized for part in self.name_patterns)

    def payload(self, device_name: str, target_id: str = "") -> dict[str, Any]:
        data = asdict(self)
        data["device_name"] = device_name
        if target_id:
            data["target_id"] = target_id
        return data


DEFAULT_OPENRGB_PROFILE = OpenRgbDeviceProfile(
    key="default",
    label="Default OpenRGB device",
    name_patterns=(),
)


ASUS_ROG_STRIX_B850_A_PROFILE = OpenRgbDeviceProfile(
    key="asus-rog-strix-b850-a",
    label="ASUS ROG STRIX B850-A GAMING WIFI S",
    name_patterns=("asus", "rog strix b850-a"),
    static_strategy="whole-device-static-all-zones-same-color",
    static_zone_size=30,
    static_reapply_count=4,
    effect_aliases={
        "chase": ("chase", "running lights", "chase fade", "rainbow chase"),
        "static": ("static",),
        "direct": ("direct", "custom"),
    },
    notes=(
        "CPU fan lighting only stayed on when Static was applied to the whole device, "
        "then device color and every Aura Addressable zone were written with the same color.",
        "Aura Addressable 2/3 may report zero LEDs until resized to 30.",
        "Prefer native Chase over Chase Fade for a cleaner chase effect.",
    ),
)


KNOWN_OPENRGB_DEVICE_PROFILES: tuple[OpenRgbDeviceProfile, ...] = (
    ASUS_ROG_STRIX_B850_A_PROFILE,
)


def profile_for_device_name(device_name: str) -> OpenRgbDeviceProfile:
    for profile in KNOWN_OPENRGB_DEVICE_PROFILES:
        if profile.matches(device_name):
            return profile
    return DEFAULT_OPENRGB_PROFILE


def mode_aliases_for_device(effect: str, device_name: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    profile = profile_for_device_name(device_name)
    return profile.effect_aliases.get(effect, fallback)


def openrgb_device_profile_payload(device_name: str, target_id: str = "") -> dict[str, Any]:
    return profile_for_device_name(device_name).payload(device_name, target_id)
