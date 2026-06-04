from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Rgb = tuple[int, int, int]


@dataclass(frozen=True)
class LightingTargetLayout:
    target_id: str
    order: int = 0
    led_count: int = 0
    direction: str = "forward"
    port_label: str = ""

    @property
    def reverse(self) -> bool:
        return normalize_layout_direction(self.direction) == "reverse"


@dataclass(frozen=True)
class LightingPhysicalLayout:
    targets: tuple[LightingTargetLayout, ...] = ()

    @classmethod
    def from_mapping(cls, value: Any) -> "LightingPhysicalLayout":
        if not isinstance(value, dict):
            return cls()
        entries: list[LightingTargetLayout] = []
        for target_id, payload in value.items():
            if not isinstance(payload, dict):
                continue
            entries.append(
                LightingTargetLayout(
                    target_id=str(payload.get("target_id", target_id)),
                    order=_int_or_default(payload.get("order"), 0),
                    led_count=max(0, _int_or_default(payload.get("led_count"), 0)),
                    direction=normalize_layout_direction(str(payload.get("direction", "forward"))),
                    port_label=str(payload.get("port_label", "")),
                )
            )
        return cls(tuple(entries))

    def to_mapping(self) -> dict[str, dict[str, object]]:
        return {
            entry.target_id: {
                "order": int(entry.order),
                "led_count": int(entry.led_count),
                "direction": normalize_layout_direction(entry.direction),
                "port_label": entry.port_label,
            }
            for entry in self.targets
            if entry.target_id
        }


def normalize_layout_direction(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"reverse", "reversed", "backward", "backwards", "right-to-left", "rtl", "反向"}:
        return "reverse"
    return "forward"


def layout_entry_for_target(layout: LightingPhysicalLayout | None, target_id: str) -> LightingTargetLayout:
    if layout is not None:
        for entry in layout.targets:
            if entry.target_id == target_id:
                return LightingTargetLayout(
                    target_id=entry.target_id,
                    order=int(entry.order),
                    led_count=max(0, int(entry.led_count)),
                    direction=normalize_layout_direction(entry.direction),
                    port_label=entry.port_label,
                )
    return LightingTargetLayout(target_id=target_id)


def ordered_layout_entries(layout: LightingPhysicalLayout | None, target_ids: list[str] | tuple[str, ...]) -> list[LightingTargetLayout]:
    raw_positions = {target_id: index for index, target_id in enumerate(target_ids)}
    entries = [layout_entry_for_target(layout, target_id) for target_id in target_ids]
    return sorted(entries, key=lambda entry: (_order_key(entry, raw_positions), raw_positions.get(entry.target_id, 0)))


def layout_led_count(layout: LightingPhysicalLayout | None, target_id: str, *, fallback: int) -> int:
    entry = layout_entry_for_target(layout, target_id)
    return entry.led_count if entry.led_count > 0 else max(0, int(fallback))


def apply_layout_direction(colors: list[Rgb], entry: LightingTargetLayout) -> list[Rgb]:
    return list(reversed(colors)) if entry.reverse else list(colors)


def _order_key(entry: LightingTargetLayout, raw_positions: dict[str, int]) -> int:
    if entry.order > 0:
        return entry.order
    return raw_positions.get(entry.target_id, 0) + 10_000


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
