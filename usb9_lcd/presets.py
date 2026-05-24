from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PresetSpec:
    name: str


DEFAULT_PRESET_SPECS: tuple[PresetSpec, ...] = ()


def generate_default_presets(output_dir: Path = Path("assets/presets")) -> list[Path]:
    _ = output_dir
    return []
