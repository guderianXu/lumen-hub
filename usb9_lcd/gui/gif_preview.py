from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageSequence

from usb9_lcd.gui.debug import log_event
from usb9_lcd.platforms import current_platform
from usb9_lcd.platforms.process import hidden_subprocess_kwargs


GIF_PREVIEW_WORKER_ARG = "--lumen-hub-gif-preview-worker"


@dataclass(frozen=True)
class GifPreviewFrame:
    path: Path
    width: int
    height: int
    duration_ms: int


def decode_gif_preview_frames(
    path: Path,
    cache_root: Path = current_platform().gif_preview_cache_dir(),
    *,
    max_frames: int = 90,
    width: int = 280,
    height: int = 220,
    timeout: float = 10.0,
) -> list[GifPreviewFrame]:
    source = Path(path)
    stat = source.stat()
    key = hashlib.sha256(
        f"v2:{source.resolve()}:{stat.st_mtime_ns}:{stat.st_size}:{max_frames}:{width}:{height}".encode()
    ).hexdigest()[:24]
    output_dir = cache_root / key
    manifest = output_dir / "manifest.json"
    cached = _read_manifest(manifest)
    if cached:
        log_event("gif_preview_cache_hit", path=str(source), frame_count=len(cached))
        return cached

    log_event("gif_preview_subprocess_starting", path=str(source), output_dir=str(output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    worker_args = [
        "--decode",
        str(source),
        str(output_dir),
        "--max-frames",
        str(max_frames),
        "--width",
        str(width),
        "--height",
        str(height),
    ]
    command = gif_preview_worker_command(worker_args)
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        **hidden_subprocess_kwargs(),
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "gif decode failed"
        log_event(
            "gif_preview_subprocess_failed",
            path=str(source),
            returncode=completed.returncode,
            stderr=completed.stderr.strip(),
        )
        raise RuntimeError(detail)

    frames = _read_manifest(manifest)
    if not frames:
        raise RuntimeError("gif decode produced no preview frames")
    log_event("gif_preview_subprocess_finished", path=str(source), frame_count=len(frames))
    return frames


def gif_preview_worker_command(worker_args: list[str]) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, GIF_PREVIEW_WORKER_ARG, *worker_args]
    return [sys.executable, "-m", "usb9_lcd.gui.gif_preview", *worker_args]


def _read_manifest(path: Path) -> list[GifPreviewFrame]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(payload, list):
        return []

    frames: list[GifPreviewFrame] = []
    for item in payload:
        if not isinstance(item, dict):
            return []
        frame_path = path.parent / str(item.get("file", ""))
        width = int(item.get("width", 0))
        height = int(item.get("height", 0))
        duration_ms = int(item.get("duration_ms", 120))
        if width <= 0 or height <= 0 or not frame_path.is_file():
            return []
        frames.append(GifPreviewFrame(frame_path, width, height, max(20, min(1000, duration_ms))))

    if not frames:
        return []
    return frames


def _decode_worker(
    source: Path,
    output_dir: Path,
    *,
    max_frames: int,
    width: int,
    height: int,
) -> None:
    frame_items: list[dict[str, int | str]] = []
    with Image.open(source) as image:
        for index, frame in enumerate(ImageSequence.Iterator(image)):
            if index >= max_frames:
                break
            preview = frame.convert("RGBA")
            duration_ms = _frame_duration_ms(frame)
            preview.thumbnail((width, height), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (width, height), (0, 0, 0, 255))
            x = (width - preview.width) // 2
            y = (height - preview.height) // 2
            canvas.alpha_composite(preview, (x, y))
            name = f"frame-{index:03d}.png"
            (output_dir / name).write_bytes(canvas.tobytes("raw", "RGBA"))
            frame_items.append({"file": name, "width": width, "height": height, "duration_ms": duration_ms})

    (output_dir / "manifest.json").write_text(
        json.dumps(frame_items, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _frame_duration_ms(frame: Image.Image) -> int:
    raw_duration = frame.info.get("duration", 120)
    try:
        duration_ms = int(raw_duration)
    except (TypeError, ValueError):
        duration_ms = 120
    return max(20, min(1000, duration_ms or 120))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decode", action="store_true")
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--max-frames", type=int, default=16)
    parser.add_argument("--width", type=int, default=280)
    parser.add_argument("--height", type=int, default=220)
    args = parser.parse_args(argv)

    if not args.decode:
        parser.error("--decode is required")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _decode_worker(
        args.source,
        args.output_dir,
        max_frames=args.max_frames,
        width=args.width,
        height=args.height,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
