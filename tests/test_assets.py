from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from usb9_lcd.assets import ASSET_CATEGORIES, AssetLibrary, AssetLink
from usb9_lcd.presets import generate_default_presets


def test_asset_library_creates_directories_and_default_links(tmp_path: Path):
    library = AssetLibrary(tmp_path)

    links = library.load_links()

    assert (tmp_path / "presets").is_dir()
    assert (tmp_path / "user").is_dir()
    assert (tmp_path / "links.json").is_file()
    assert any(link.title == "ROG official GIPHY" for link in links)
    assert any(link.title == "Gif Abyss ROG animated emblem" for link in links)
    assert any(link.title == "Pixabay cyber eye GIF search" for link in links)


def test_asset_library_indexes_static_and_animated_files(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    static_path = tmp_path / "user" / "red.png"
    animated_path = tmp_path / "user" / "blink.gif"
    Image.new("RGB", (2, 2), (255, 0, 0)).save(static_path)
    Image.new("RGB", (2, 2), (0, 0, 0)).save(
        animated_path,
        save_all=True,
        append_images=[Image.new("RGB", (2, 2), (255, 255, 255))],
        duration=100,
        loop=0,
    )

    assets = library.list_media()

    by_name = {asset.path.name: asset for asset in assets}
    assert by_name["red.png"].animated is False
    assert by_name["red.png"].frame_count == 1
    assert by_name["blink.gif"].animated is True
    assert by_name["blink.gif"].frame_count == 2


def test_asset_library_indexes_template_categories_and_filters(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    monitor_dir = tmp_path / "monitor_backgrounds"
    test_pattern_dir = tmp_path / "test_patterns"
    monitor_dir.mkdir(exist_ok=True)
    test_pattern_dir.mkdir(exist_ok=True)
    Image.new("RGB", (4, 4), (0, 0, 255)).save(monitor_dir / "neon_meter.png")
    Image.new("RGB", (4, 4), (255, 255, 255)).save(test_pattern_dir / "lcd_test.png")
    Image.new("RGB", (2, 2), (255, 0, 0)).save(tmp_path / "user" / "red.png")
    Image.new("RGB", (2, 2), (0, 0, 0)).save(
        tmp_path / "user" / "blink.gif",
        save_all=True,
        append_images=[Image.new("RGB", (2, 2), (255, 255, 255))],
        duration=100,
        loop=0,
    )

    assets = library.list_media()

    by_name = {asset.path.name: asset for asset in assets}
    assert by_name["neon_meter.png"].category == "monitoring"
    assert by_name["neon_meter.png"].category_label == "监控仪表盘"
    assert by_name["neon_meter.png"].template is True
    assert by_name["lcd_test.png"].category == "test-pattern"
    assert by_name["lcd_test.png"].template is True
    assert by_name["red.png"].category == "static"
    assert by_name["red.png"].template is False
    assert by_name["blink.gif"].category == "animation"
    assert by_name["blink.gif"].template is False
    assert [asset.path.name for asset in library.list_media(category="monitoring")] == ["neon_meter.png"]
    assert library.category_counts() == {
        "animation": 1,
        "monitoring": 1,
        "static": 1,
        "test-pattern": 1,
    }


def test_asset_library_exposes_product_template_categories():
    assert ASSET_CATEGORIES["monitoring"] == "监控仪表盘"
    assert ASSET_CATEGORIES["cpu-theme"] == "CPU 主题"
    assert ASSET_CATEGORIES["gpu-theme"] == "GPU 主题"
    assert ASSET_CATEGORIES["lianli-status"] == "联力状态"


def test_asset_library_does_not_generate_builtin_presets(tmp_path: Path):
    library = AssetLibrary(tmp_path)

    assets = library.list_media()

    preset_assets = [asset for asset in assets if asset.path.parent == tmp_path / "presets"]
    assert preset_assets == []


def test_default_preset_gifs_stay_within_size_budget(tmp_path: Path):
    generated = generate_default_presets(tmp_path)

    assert sum(path.stat().st_size for path in generated) < 2 * 1024 * 1024
    assert all(path.stat().st_size < 800 * 1024 for path in generated)


def test_asset_library_saves_links(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    library.save_links([AssetLink(title="Example", url="https://example.com", kind="gif", tags=("eye",))])

    links = library.load_links()

    assert links == [AssetLink(title="Example", url="https://example.com", kind="gif", tags=("eye",))]


def test_asset_library_returns_empty_links_for_non_list_json(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    library.links_path.write_text("1", encoding="utf-8")

    assert library.load_links() == []


def test_asset_library_normalizes_bad_link_tags_to_empty_tuple(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    library.links_path.write_text(
        """[
  {"title": "Null tags", "url": "https://example.com/null", "tags": null},
  {"title": "Number tags", "url": "https://example.com/number", "tags": 7}
]""",
        encoding="utf-8",
    )

    links = library.load_links()

    assert links == [
        AssetLink(title="Null tags", url="https://example.com/null", kind="link", tags=()),
        AssetLink(title="Number tags", url="https://example.com/number", kind="link", tags=()),
    ]


def test_asset_library_imports_supported_file_to_user_directory(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    source = tmp_path / "source.webp"
    Image.new("RGB", (2, 2), (0, 255, 0)).save(source)

    imported = library.import_file(source)

    assert imported == tmp_path / "user" / "source.webp"
    assert imported.is_file()


def test_asset_library_imports_duplicate_names_without_overwriting(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first_source = first_dir / "source.png"
    second_source = second_dir / "source.png"
    Image.new("RGB", (2, 2), (255, 0, 0)).save(first_source)
    Image.new("RGB", (2, 2), (0, 255, 0)).save(second_source)

    first_imported = library.import_file(first_source)
    second_imported = library.import_file(second_source)

    assert first_imported == tmp_path / "user" / "source.png"
    assert second_imported == tmp_path / "user" / "source-1.png"
    assert first_imported != second_imported
    with Image.open(first_imported) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)
    with Image.open(second_imported) as image:
        assert image.getpixel((0, 0)) == (0, 255, 0)


def test_asset_library_rejects_unsupported_imports(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    source = tmp_path / "notes.txt"
    source.write_text("not media", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported asset type"):
        library.import_file(source)


def test_asset_library_rejects_invalid_supported_imports(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    source = tmp_path / "fake.png"
    source.write_text("not media", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid asset file"):
        library.import_file(source)


def test_asset_library_skips_corrupted_media_files(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    broken = tmp_path / "user" / "broken.gif"
    valid = tmp_path / "user" / "valid.bmp"
    broken.write_bytes(b"not a real image")
    Image.new("RGB", (2, 2), (0, 0, 255)).save(valid)

    assets = library.list_media()
    user_assets = [asset for asset in assets if asset.path.parent == tmp_path / "user"]

    assert [asset.path.name for asset in user_assets] == ["valid.bmp"]


def test_generate_default_presets_is_disabled(tmp_path: Path):
    generated = generate_default_presets(tmp_path)

    assert generated == []
    assert list(tmp_path.iterdir()) == []
