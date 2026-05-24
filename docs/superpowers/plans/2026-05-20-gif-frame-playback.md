# USB9 LCD GIF Frame Playback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Play animated GIF/WebP assets on the ASUS LCD by decoding frames and sending conservative low-FPS RGB565 frames through the existing static upload protocol.

**Architecture:** Add a non-GUI animation frame renderer first, then wire it into the existing PySide6 asset page and `DisplayDriver.upload_static_frame()`. Playback uses `QTimer` for low-rate scheduling and prevents overlapping uploads; it does not attempt native ASUS animation commands.

**Tech Stack:** Python 3.10+, Pillow, PySide6, pytest, existing USB9 LCD driver/render stack.

---

## Task 1: Add Animated Frame Renderer

**Files:**
- Create: `usb9_lcd/animation.py`
- Create: `tests/test_animation.py`

- [ ] **Step 1: Write failing animation tests**

Create `tests/test_animation.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from usb9_lcd.animation import AnimationRenderSettings, iter_animated_frames
from usb9_lcd.drivers.base import Capability, DeviceConnection, DisplayDevice, PixelFormat, PixelStyle, PreviewProfile, PreviewShape


def _device(width: int = 2, height: int = 1, *, capabilities=frozenset({Capability.STATIC_IMAGE}), pixel_format=PixelFormat.RGB565) -> DisplayDevice:
    return DisplayDevice(
        connection=DeviceConnection("test.driver", "Test Display", (Path("/dev/null"),), True, True),
        width=width,
        height=height,
        pixel_format=pixel_format,
        preview=PreviewProfile(width, height, PreviewShape.RECTANGLE, PixelStyle.CONTINUOUS),
        capabilities=capabilities,
    )


def _gif(path: Path) -> None:
    Image.new("RGB", (1, 1), (255, 0, 0)).save(
        path,
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), (0, 255, 0))],
        duration=100,
        loop=0,
    )


def test_iter_animated_frames_returns_rgb565_frames(tmp_path: Path):
    gif_path = tmp_path / "blink.gif"
    _gif(gif_path)

    frames = list(iter_animated_frames(gif_path, _device(), AnimationRenderSettings(fit="stretch", fps=2)))

    assert [frame.index for frame in frames] == [0, 1]
    assert [len(frame.frame) for frame in frames] == [4, 4]
    assert frames[0].duration_ms == 500


def test_iter_animated_frames_honors_max_frames(tmp_path: Path):
    gif_path = tmp_path / "blink.gif"
    _gif(gif_path)

    frames = list(iter_animated_frames(gif_path, _device(), AnimationRenderSettings(max_frames=1)))

    assert len(frames) == 1


def test_iter_animated_frames_rejects_static_images(tmp_path: Path):
    image_path = tmp_path / "static.png"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(image_path)

    with pytest.raises(ValueError, match="selected asset is not animated"):
        list(iter_animated_frames(image_path, _device()))


def test_iter_animated_frames_rejects_unsupported_device(tmp_path: Path):
    gif_path = tmp_path / "blink.gif"
    _gif(gif_path)

    with pytest.raises(ValueError, match="selected device does not support static images"):
        list(iter_animated_frames(gif_path, _device(capabilities=frozenset())))
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_animation.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'usb9_lcd.animation'`.

- [ ] **Step 3: Implement animation renderer**

Create `usb9_lcd/animation.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from PIL import Image, ImageSequence, UnidentifiedImageError

from usb9_lcd.drivers.base import Capability, DisplayDevice, PixelFormat
from usb9_lcd.image import FitMode, FrameConfig, Rotation, image_to_rgb565_bytes


@dataclass(frozen=True)
class AnimationRenderSettings:
    fit: FitMode = "contain"
    rotate: Rotation = 0
    background: str = "#000000"
    max_frames: int = 120
    fps: int = 2

    def __post_init__(self) -> None:
        if self.max_frames <= 0:
            raise ValueError("max_frames must be positive")
        if self.fps <= 0:
            raise ValueError("fps must be positive")


@dataclass(frozen=True)
class AnimatedFrame:
    frame: bytes
    index: int
    duration_ms: int


def _validate_device(device: DisplayDevice) -> None:
    if not device.supports(Capability.STATIC_IMAGE):
        raise ValueError("selected device does not support static images")
    if device.pixel_format is not PixelFormat.RGB565:
        raise ValueError(f"selected device pixel format must be rgb565, got {device.pixel_format}")


def iter_animated_frames(
    path: str | Path,
    device: DisplayDevice,
    settings: AnimationRenderSettings = AnimationRenderSettings(),
) -> Iterator[AnimatedFrame]:
    _validate_device(device)
    try:
        image = Image.open(path)
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"failed to open animated asset: {error}") from error

    with image:
        frame_count = int(getattr(image, "n_frames", 1))
        if frame_count <= 1:
            raise ValueError("selected asset is not animated")

        duration_ms = max(1, int(1000 / settings.fps))
        config = FrameConfig(
            width=device.width,
            height=device.height,
            fit=settings.fit,
            rotate=settings.rotate,
            background=settings.background,
        )
        for index, frame in enumerate(ImageSequence.Iterator(image)):
            if index >= settings.max_frames:
                break
            rgb = frame.convert("RGB")
            yield AnimatedFrame(
                frame=image_to_rgb565_bytes(rgb, config),
                index=index,
                duration_ms=duration_ms,
            )
```

- [ ] **Step 4: Run animation tests**

Run:

```bash
pytest tests/test_animation.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add usb9_lcd/animation.py tests/test_animation.py
git commit -m "feat: render animated assets as frames"
```

## Task 2: Add GUI Animated Asset Selection

**Files:**
- Modify: `usb9_lcd/gui/pages.py`
- Modify: `usb9_lcd/gui/main_window.py`
- Modify: `tests/test_gui_import.py`

- [ ] **Step 1: Add failing GUI selection test**

Append to `tests/test_gui_import.py`:

```python
def test_asset_page_selects_animated_asset_for_playback(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    library = AssetLibrary(tmp_path / "assets")
    gif_path = tmp_path / "blink.gif"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(
        gif_path,
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), (0, 255, 0))],
        duration=100,
        loop=0,
    )
    imported = library.import_file(gif_path)

    window = MainWindow(driver=FakeDriver(), telemetry_provider=lambda: _fake_telemetry(), asset_library=library, auto_refresh=False)
    window.select_asset_for_playback(imported)

    assert window.selected_animation_path == imported
    assert imported.name in window.statusBar().currentMessage()

    window.close()
    app.quit()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_gui_import.py::test_asset_page_selects_animated_asset_for_playback -q
```

Expected: FAIL because `select_asset_for_playback` does not exist.

- [ ] **Step 3: Implement selection plumbing**

In `AssetLibraryPage`, add a `select_asset_for_playback: Callable[[Path], None] | None` constructor argument and render each media line with the path. For this first implementation, expose a method:

```python
def selected_media_paths(self) -> list[Path]:
    return [asset.path for asset in self.asset_library.list_media() if asset.animated]
```

In `MainWindow.__init__`, add:

```python
self.selected_animation_path: Path | None = None
```

Add method:

```python
def select_asset_for_playback(self, path: str | Path) -> None:
    self.selected_animation_path = Path(path)
    self.statusBar().showMessage(f"已选择动画素材：{self.selected_animation_path.name}")
```

Add buttons to asset page if practical:

- `选择第一个动图`
- It calls the first path from `selected_media_paths()` and then `select_asset_for_playback`.

- [ ] **Step 4: Run GUI tests**

Run:

```bash
pytest tests/test_gui_import.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add usb9_lcd/gui/pages.py usb9_lcd/gui/main_window.py tests/test_gui_import.py
git commit -m "feat: select animated assets for playback"
```

## Task 3: Add GUI Playback Controller

**Files:**
- Modify: `usb9_lcd/gui/main_window.py`
- Modify: `usb9_lcd/gui/pages.py`
- Modify: `tests/test_gui_import.py`

- [x] **Step 1: Add failing playback tests**

Append to `tests/test_gui_import.py`:

```python
def test_main_window_play_animation_uploads_one_frame(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    driver = FakeDriver()
    library = AssetLibrary(tmp_path / "assets")
    gif_path = tmp_path / "blink.gif"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(
        gif_path,
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), (0, 255, 0))],
        duration=100,
        loop=0,
    )
    imported = library.import_file(gif_path)
    window = MainWindow(driver=driver, telemetry_provider=lambda: _fake_telemetry(), asset_library=library, auto_refresh=False)
    window.refresh_devices()
    window.select_asset_for_playback(imported)

    window.play_animation()
    window.play_next_animation_frame()

    assert len(driver.uploads) == 1
    assert len(driver.uploads[0][1]) == driver.device.width * driver.device.height * 2
    assert "动画播放中" in window.statusBar().currentMessage()

    window.stop_animation()
    window.close()
    app.quit()
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_gui_import.py::test_main_window_play_animation_uploads_one_frame -q
```

Expected: FAIL because `play_animation` does not exist.

- [x] **Step 3: Implement playback state**

In `MainWindow.__init__`, add:

```python
from usb9_lcd.animation import AnimationRenderSettings, iter_animated_frames

self.animation_timer = QTimer(self)
self.animation_timer.setInterval(500)
self.animation_timer.timeout.connect(self.play_next_animation_frame)
self._animation_frames = None
self._animation_uploading = False
```

Implement:

```python
def play_animation(self) -> None:
    device = self._selected_device()
    if device is None:
        self.statusBar().showMessage("请先选择设备")
        return
    if self.selected_animation_path is None:
        self.statusBar().showMessage("请先选择动图素材")
        return
    try:
        self._animation_frames = iter(iter_animated_frames(self.selected_animation_path, device, AnimationRenderSettings()))
    except Exception as error:
        self.statusBar().showMessage(f"动画播放失败：{error}")
        return
    self.animation_timer.start()
    self.statusBar().showMessage("动画播放中")


def play_next_animation_frame(self) -> None:
    device = self._selected_device()
    if device is None or self._animation_frames is None or self._animation_uploading:
        return
    self._animation_uploading = True
    try:
        animated_frame = next(self._animation_frames)
        self.driver.upload_static_frame(device, animated_frame.frame)
    except StopIteration:
        self.stop_animation()
    except Exception as error:
        self.stop_animation(message=f"动画播放失败：{error}")
    finally:
        self._animation_uploading = False


def stop_animation(self, message: str = "动画播放已停止") -> None:
    self.animation_timer.stop()
    self._animation_frames = None
    self._animation_uploading = False
    self.statusBar().showMessage(message)
```

Add asset/monitor buttons:

- In asset page: `播放到屏幕`, `停止播放`.
- MainWindow can expose `play_animation` and `stop_animation`.

- [x] **Step 4: Run GUI tests**

Run:

```bash
pytest tests/test_gui_import.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add usb9_lcd/gui/main_window.py usb9_lcd/gui/pages.py tests/test_gui_import.py
git commit -m "feat: play animated assets on display"
```

## Task 4: Final Verification

**Files:**
- Modify: `README.md`

- [x] **Step 1: Update README**

Add under Desktop GUI:

```markdown
Animated assets can be played to the LCD through low-rate frame refresh. This mode decodes GIF/WebP frames and sends them through the same static image protocol, so it is intentionally conservative and is not the native ASUS animation protocol.
```

- [x] **Step 2: Run tests**

Run:

```bash
pytest -q
```

Expected: PASS.

- [x] **Step 3: Verify GIF frame generation manually**

Run:

```bash
python - <<'PY'
from pathlib import Path
from usb9_lcd.animation import iter_animated_frames
from usb9_lcd.drivers.asus_lc_iii import AsusLcIiiDriver

device = AsusLcIiiDriver().discover()[0]
frames = iter_animated_frames(Path("assets/user/giphy-rog-logo.gif"), device)
first = next(frames)
print(len(first.frame), first.duration_ms)
PY
```

Expected:

```text
460800 500
```

- [x] **Step 4: Launch GUI headless**

Run:

```bash
QT_QPA_PLATFORM=offscreen timeout 3s python -m usb9_lcd.gui.app
```

Expected: timeout exit 124, no Python traceback.

- [ ] **Step 5: Commit README**

```bash
git add README.md
git commit -m "docs: document animated asset playback"
```
