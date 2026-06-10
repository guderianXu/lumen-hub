from __future__ import annotations

from pathlib import Path

from usb9_lcd.gui import gif_preview


def test_frozen_gif_preview_uses_executable_worker_marker(tmp_path: Path, monkeypatch):
    source = tmp_path / "preview.gif"
    source.write_bytes(b"placeholder")
    cache_root = tmp_path / "cache"
    captured_command: list[str] = []
    calls = 0

    def fake_read_manifest(path: Path) -> list[gif_preview.GifPreviewFrame]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return []
        return [gif_preview.GifPreviewFrame(path.parent / "frame-000.png", 280, 220, 120)]

    def fake_run(command, **kwargs):  # noqa: ANN001
        captured_command.extend(command)

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return Completed()

    monkeypatch.setattr(gif_preview.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gif_preview.sys, "executable", "/opt/LumenHub/LumenHub")
    monkeypatch.setattr(gif_preview, "_read_manifest", fake_read_manifest)
    monkeypatch.setattr(gif_preview.subprocess, "run", fake_run)

    frames = gif_preview.decode_gif_preview_frames(source, cache_root=cache_root)

    assert frames[0].path.name == "frame-000.png"
    assert captured_command[:2] == ["/opt/LumenHub/LumenHub", "--lumen-hub-gif-preview-worker"]
    assert "-m" not in captured_command
