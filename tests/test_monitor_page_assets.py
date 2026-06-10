from __future__ import annotations

import sys
from pathlib import Path


def test_monitor_page_builtin_backgrounds_use_bundled_asset_paths(tmp_path: Path, monkeypatch):
    bundle_root = tmp_path / "bundle"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    from usb9_lcd.gui import monitor_page

    backgrounds = monitor_page.builtin_monitor_backgrounds()

    assert backgrounds[0] == ("ROG 红色网格", bundle_root / "assets" / "monitor_backgrounds" / "rog_red_grid.png")
    assert all(path.is_absolute() for _, path in backgrounds)
