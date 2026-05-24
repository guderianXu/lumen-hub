from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from shutil import copy2

from PIL import Image, UnidentifiedImageError

SUPPORTED_MEDIA_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

DEFAULT_LINKS = [
    {
        "title": "ROG official GIPHY",
        "url": "https://giphy.com/GlobalROG",
        "kind": "collection",
        "tags": ["rog", "gif"],
    },
    {
        "title": "Gif Abyss ROG animated emblem",
        "url": "https://gifs.alphacoders.com/gifs/view/202278",
        "kind": "gif",
        "tags": ["rog", "logo", "animation"],
    },
    {
        "title": "Pixabay cyber eye GIF search",
        "url": "https://pixabay.com/gifs/search/cyber%20eye/",
        "kind": "collection",
        "tags": ["eye", "cyber", "gif"],
    },
]


@dataclass(frozen=True)
class AssetLink:
    title: str
    url: str
    kind: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class MediaAsset:
    path: Path
    kind: str
    width: int
    height: int
    frame_count: int
    animated: bool


class AssetLibrary:
    def __init__(self, root: Path | str = Path("assets")) -> None:
        self.root = Path(root)
        self.presets_dir = self.root / "presets"
        self.user_dir = self.root / "user"
        self.links_path = self.root / "links.json"
        self.ensure()

    def ensure(self) -> None:
        self.presets_dir.mkdir(parents=True, exist_ok=True)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        from usb9_lcd.presets import DEFAULT_PRESET_SPECS, generate_default_presets

        if any(not (self.presets_dir / spec.name).exists() for spec in DEFAULT_PRESET_SPECS):
            generate_default_presets(self.presets_dir)
        if not self.links_path.exists():
            self.save_links(_default_asset_links())

    def load_links(self) -> list[AssetLink]:
        self.ensure()
        try:
            raw = json.loads(self.links_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

        if not isinstance(raw, list):
            return []

        links: list[AssetLink] = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("title") or not item.get("url"):
                continue
            raw_tags = item.get("tags", [])
            tags = raw_tags if isinstance(raw_tags, (list, tuple)) else []
            links.append(
                AssetLink(
                    title=str(item["title"]),
                    url=str(item["url"]),
                    kind=str(item.get("kind", "link")),
                    tags=tuple(str(tag) for tag in tags),
                )
            )
        return links

    def save_links(self, links: list[AssetLink]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = [
            {"title": link.title, "url": link.url, "kind": link.kind, "tags": list(link.tags)}
            for link in links
        ]
        self.links_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def list_media(self) -> list[MediaAsset]:
        self.ensure()
        assets: list[MediaAsset] = []
        for directory in (self.presets_dir, self.user_dir):
            for path in sorted(directory.iterdir()):
                if not path.is_file() or path.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
                    continue
                asset = _read_media_asset(path)
                if asset is not None:
                    assets.append(asset)
        return assets

    def import_file(self, source: Path | str) -> Path:
        self.ensure()
        source_path = Path(source)
        if source_path.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
            raise ValueError(f"unsupported asset type: {source_path.suffix}")
        try:
            with Image.open(source_path) as image:
                image.verify()
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError(f"invalid asset file: {source_path}") from exc

        destination = _available_destination(self.user_dir, source_path.name)
        copy2(source_path, destination)
        return destination


def _default_asset_links() -> list[AssetLink]:
    return [
        AssetLink(
            title=item["title"],
            url=item["url"],
            kind=item["kind"],
            tags=tuple(item["tags"]),
        )
        for item in DEFAULT_LINKS
    ]


def _read_media_asset(path: Path) -> MediaAsset | None:
    try:
        with Image.open(path) as image:
            frame_count = int(getattr(image, "n_frames", 1))
            return MediaAsset(
                path=path,
                kind=path.suffix.lower().removeprefix("."),
                width=image.width,
                height=image.height,
                frame_count=frame_count,
                animated=frame_count > 1,
            )
    except (OSError, UnidentifiedImageError):
        return None


def _available_destination(directory: Path, filename: str) -> Path:
    destination = directory / filename
    if not destination.exists():
        return destination

    stem = destination.stem
    suffix = destination.suffix
    index = 1
    while True:
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1
