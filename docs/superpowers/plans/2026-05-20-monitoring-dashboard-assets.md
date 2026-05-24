# USB9 LCD Monitoring Dashboard And Asset Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the desktop GUI into a dark hardware monitoring dashboard with NVIDIA GPU telemetry, CPU temperature, a local image/GIF asset library, and monitor-frame output through the existing static upload protocol.

**Architecture:** Add testable non-GUI services first: telemetry models/collectors, asset-library indexing, and monitor-frame rendering. Then refactor the PySide6 window into a dark multi-page tool that consumes those services through injectable dependencies. Hardware output reuses the existing `DisplayDriver.upload_static_frame()` and RGB565 render path.

**Tech Stack:** Python 3.10+, PySide6, Pillow, pytest, Linux `/sys/class/hwmon`, NVIDIA `nvidia-smi`.

---

## File Structure

- Execution note: Tasks 1-5 are mostly independent service layers and can be implemented by separate workers. Tasks 6-9 all modify `usb9_lcd/gui/main_window.py` and `tests/test_gui_import.py`, so run those sequentially and review after each task.
- Create `usb9_lcd/monitoring/__init__.py`: public monitoring exports.
- Create `usb9_lcd/monitoring/models.py`: telemetry dataclasses.
- Create `usb9_lcd/monitoring/nvidia.py`: `nvidia-smi` parser and collector.
- Create `usb9_lcd/monitoring/cpu.py`: Linux hwmon CPU temperature collector.
- Create `usb9_lcd/monitoring/service.py`: aggregate telemetry provider.
- Create `usb9_lcd/monitoring/render.py`: PIL renderer for LCD monitoring frames.
- Create `usb9_lcd/assets.py`: asset directory, link, and media indexing services.
- Create `assets/links.json`: starter collection links for ROG/GIF sources.
- Create generated preset assets under `assets/presets/`.
- Modify `usb9_lcd/gui/main_window.py`: dark dashboard, new pages, telemetry refresh, asset-library UI.
- Modify `tests/test_gui_import.py`: GUI dark dashboard and fake telemetry coverage.
- Create `tests/test_monitoring.py`: telemetry parser/collector tests.
- Create `tests/test_assets.py`: asset-library tests.
- Create `tests/test_monitor_render.py`: LCD monitoring renderer tests.
- Modify `README.md`: document dashboard monitoring and asset library.

## Task 1: Add Monitoring Models And NVIDIA Collector

**Files:**
- Create: `usb9_lcd/monitoring/__init__.py`
- Create: `usb9_lcd/monitoring/models.py`
- Create: `usb9_lcd/monitoring/nvidia.py`
- Test: `tests/test_monitoring.py`

- [ ] **Step 1: Write failing NVIDIA parser and collector tests**

Create `tests/test_monitoring.py`:

```python
from __future__ import annotations

import subprocess

from usb9_lcd.monitoring.nvidia import collect_nvidia_gpu, parse_nvidia_smi_csv


def test_parse_nvidia_smi_csv_parses_first_gpu():
    output = "NVIDIA GeForce RTX 4090, 61, 42, 216.50, 8123, 24564, 2745\n"

    telemetry = parse_nvidia_smi_csv(output)

    assert telemetry.available is True
    assert telemetry.name == "NVIDIA GeForce RTX 4090"
    assert telemetry.temperature_c == 61
    assert telemetry.utilization_percent == 42
    assert telemetry.power_w == 216.50
    assert telemetry.memory_used_mb == 8123
    assert telemetry.memory_total_mb == 24564
    assert telemetry.graphics_clock_mhz == 2745
    assert telemetry.error == ""


def test_parse_nvidia_smi_csv_handles_not_available_fields():
    output = "NVIDIA GPU, N/A, [Not Supported], N/A, 100, 200, N/A\n"

    telemetry = parse_nvidia_smi_csv(output)

    assert telemetry.available is True
    assert telemetry.temperature_c is None
    assert telemetry.utilization_percent is None
    assert telemetry.power_w is None
    assert telemetry.memory_used_mb == 100
    assert telemetry.memory_total_mb == 200
    assert telemetry.graphics_clock_mhz is None


def test_collect_nvidia_gpu_returns_unavailable_when_command_missing():
    def run_missing(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    telemetry = collect_nvidia_gpu(run=run_missing)

    assert telemetry.available is False
    assert telemetry.name == "NVIDIA GPU"
    assert "nvidia-smi" in telemetry.error


def test_collect_nvidia_gpu_returns_unavailable_on_command_failure():
    def run_failed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], returncode=1, stdout="", stderr="driver not loaded")

    telemetry = collect_nvidia_gpu(run=run_failed)

    assert telemetry.available is False
    assert telemetry.error == "driver not loaded"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_monitoring.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'usb9_lcd.monitoring'`.

- [ ] **Step 3: Implement monitoring models**

Create `usb9_lcd/monitoring/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class GpuTelemetry:
    name: str = "NVIDIA GPU"
    temperature_c: int | None = None
    utilization_percent: int | None = None
    power_w: float | None = None
    memory_used_mb: int | None = None
    memory_total_mb: int | None = None
    graphics_clock_mhz: int | None = None
    available: bool = False
    error: str = ""


@dataclass(frozen=True)
class CpuTelemetry:
    package_temperature_c: float | None = None
    utilization_percent: float | None = None
    available: bool = False
    error: str = ""


@dataclass(frozen=True)
class SystemTelemetry:
    cpu: CpuTelemetry
    gpu: GpuTelemetry
    captured_at: datetime
```

Create `usb9_lcd/monitoring/__init__.py`:

```python
from .models import CpuTelemetry, GpuTelemetry, SystemTelemetry
from .nvidia import collect_nvidia_gpu, parse_nvidia_smi_csv

__all__ = [
    "CpuTelemetry",
    "GpuTelemetry",
    "SystemTelemetry",
    "collect_nvidia_gpu",
    "parse_nvidia_smi_csv",
]
```

- [ ] **Step 4: Implement NVIDIA collector**

Create `usb9_lcd/monitoring/nvidia.py`:

```python
from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence

from .models import GpuTelemetry

NVIDIA_SMI_QUERY = (
    "name,temperature.gpu,utilization.gpu,power.draw,"
    "memory.used,memory.total,clocks.current.graphics"
)


def _parse_int(value: str) -> int | None:
    value = value.strip()
    if not value or value.upper() == "N/A" or "not supported" in value.lower():
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _parse_float(value: str) -> float | None:
    value = value.strip()
    if not value or value.upper() == "N/A" or "not supported" in value.lower():
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_nvidia_smi_csv(output: str) -> GpuTelemetry:
    first_line = next((line for line in output.splitlines() if line.strip()), "")
    if not first_line:
        return GpuTelemetry(error="nvidia-smi returned no GPU rows")

    parts = [part.strip() for part in first_line.split(",")]
    if len(parts) < 7:
        return GpuTelemetry(error=f"unexpected nvidia-smi output: {first_line}")

    return GpuTelemetry(
        name=parts[0] or "NVIDIA GPU",
        temperature_c=_parse_int(parts[1]),
        utilization_percent=_parse_int(parts[2]),
        power_w=_parse_float(parts[3]),
        memory_used_mb=_parse_int(parts[4]),
        memory_total_mb=_parse_int(parts[5]),
        graphics_clock_mhz=_parse_int(parts[6]),
        available=True,
    )


def collect_nvidia_gpu(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: float = 2.0,
) -> GpuTelemetry:
    command: Sequence[str] = (
        "nvidia-smi",
        f"--query-gpu={NVIDIA_SMI_QUERY}",
        "--format=csv,noheader,nounits",
    )
    try:
        result = run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as error:
        return GpuTelemetry(error=str(error))
    except subprocess.TimeoutExpired:
        return GpuTelemetry(error="nvidia-smi timed out")
    except OSError as error:
        return GpuTelemetry(error=str(error))

    if result.returncode != 0:
        return GpuTelemetry(error=(result.stderr or result.stdout or "nvidia-smi failed").strip())

    return parse_nvidia_smi_csv(result.stdout)
```

- [ ] **Step 5: Run monitoring tests**

Run:

```bash
pytest tests/test_monitoring.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add usb9_lcd/monitoring tests/test_monitoring.py
git commit -m "feat: add nvidia telemetry collector"
```

## Task 2: Add CPU hwmon Collector And Aggregate Service

**Files:**
- Modify: `usb9_lcd/monitoring/__init__.py`
- Create: `usb9_lcd/monitoring/cpu.py`
- Create: `usb9_lcd/monitoring/service.py`
- Test: `tests/test_monitoring.py`

- [ ] **Step 1: Append failing CPU and service tests**

Append to `tests/test_monitoring.py`:

```python
from datetime import datetime
from pathlib import Path

from usb9_lcd.monitoring.cpu import collect_cpu_temperature_from_hwmon
from usb9_lcd.monitoring.models import CpuTelemetry, GpuTelemetry
from usb9_lcd.monitoring.service import collect_system_telemetry


def test_collect_cpu_temperature_from_hwmon_prefers_package_label(tmp_path: Path):
    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("coretemp\n", encoding="utf-8")
    (hwmon / "temp1_label").write_text("Package id 0\n", encoding="utf-8")
    (hwmon / "temp1_input").write_text("54875\n", encoding="utf-8")
    (hwmon / "temp2_label").write_text("Core 0\n", encoding="utf-8")
    (hwmon / "temp2_input").write_text("50000\n", encoding="utf-8")

    telemetry = collect_cpu_temperature_from_hwmon(tmp_path)

    assert telemetry.available is True
    assert telemetry.package_temperature_c == 54.875
    assert telemetry.error == ""


def test_collect_cpu_temperature_from_hwmon_returns_unavailable_when_missing(tmp_path: Path):
    telemetry = collect_cpu_temperature_from_hwmon(tmp_path)

    assert telemetry.available is False
    assert telemetry.package_temperature_c is None
    assert "temperature" in telemetry.error


def test_collect_system_telemetry_combines_cpu_gpu_and_timestamp():
    now = datetime(2026, 5, 20, 12, 0, 0)

    telemetry = collect_system_telemetry(
        cpu_collector=lambda: CpuTelemetry(package_temperature_c=50.0, available=True),
        gpu_collector=lambda: GpuTelemetry(name="RTX", temperature_c=61, available=True),
        clock=lambda: now,
    )

    assert telemetry.cpu.package_temperature_c == 50.0
    assert telemetry.gpu.temperature_c == 61
    assert telemetry.captured_at == now
```

- [ ] **Step 2: Run new tests to verify they fail**

Run:

```bash
pytest tests/test_monitoring.py -q
```

Expected: FAIL with missing `usb9_lcd.monitoring.cpu`.

- [ ] **Step 3: Implement CPU collector**

Create `usb9_lcd/monitoring/cpu.py`:

```python
from __future__ import annotations

from pathlib import Path

from .models import CpuTelemetry

PACKAGE_LABEL_MARKERS = ("package", "tdie", "tctl", "cpu")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _temperature_from_millidegrees(value: str) -> float | None:
    try:
        return int(value.strip()) / 1000.0
    except ValueError:
        return None


def collect_cpu_temperature_from_hwmon(hwmon_root: Path = Path("/sys/class/hwmon")) -> CpuTelemetry:
    readings: list[tuple[int, str, float]] = []

    for hwmon in sorted(hwmon_root.glob("hwmon*")):
        for input_path in sorted(hwmon.glob("temp*_input")):
            index = input_path.stem.removeprefix("temp").removesuffix("_input")
            label = _read_text(hwmon / f"temp{index}_label")
            temperature = _temperature_from_millidegrees(_read_text(input_path))
            if temperature is None:
                continue

            priority = 1
            lowered = label.lower()
            if any(marker in lowered for marker in PACKAGE_LABEL_MARKERS):
                priority = 0
            readings.append((priority, label, temperature))

    if not readings:
        return CpuTelemetry(error="no CPU temperature sensor found")

    readings.sort(key=lambda item: item[0])
    return CpuTelemetry(package_temperature_c=readings[0][2], available=True)
```

- [ ] **Step 4: Implement aggregate service and exports**

Create `usb9_lcd/monitoring/service.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from .cpu import collect_cpu_temperature_from_hwmon
from .models import CpuTelemetry, GpuTelemetry, SystemTelemetry
from .nvidia import collect_nvidia_gpu


def collect_system_telemetry(
    cpu_collector: Callable[[], CpuTelemetry] = collect_cpu_temperature_from_hwmon,
    gpu_collector: Callable[[], GpuTelemetry] = collect_nvidia_gpu,
    clock: Callable[[], datetime] = datetime.now,
) -> SystemTelemetry:
    return SystemTelemetry(
        cpu=cpu_collector(),
        gpu=gpu_collector(),
        captured_at=clock(),
    )
```

Update `usb9_lcd/monitoring/__init__.py`:

```python
from .cpu import collect_cpu_temperature_from_hwmon
from .models import CpuTelemetry, GpuTelemetry, SystemTelemetry
from .nvidia import collect_nvidia_gpu, parse_nvidia_smi_csv
from .service import collect_system_telemetry

__all__ = [
    "CpuTelemetry",
    "GpuTelemetry",
    "SystemTelemetry",
    "collect_cpu_temperature_from_hwmon",
    "collect_nvidia_gpu",
    "collect_system_telemetry",
    "parse_nvidia_smi_csv",
]
```

- [ ] **Step 5: Run monitoring tests**

Run:

```bash
pytest tests/test_monitoring.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add usb9_lcd/monitoring tests/test_monitoring.py
git commit -m "feat: add cpu telemetry collector"
```

## Task 3: Add Asset Library Service And Starter Links

**Files:**
- Create: `usb9_lcd/assets.py`
- Create: `assets/links.json`
- Test: `tests/test_assets.py`

- [ ] **Step 1: Write failing asset-library tests**

Create `tests/test_assets.py`:

```python
from __future__ import annotations

from pathlib import Path

from PIL import Image

from usb9_lcd.assets import AssetLibrary, AssetLink


def test_asset_library_creates_directories_and_default_links(tmp_path: Path):
    library = AssetLibrary(tmp_path)

    links = library.load_links()

    assert (tmp_path / "presets").is_dir()
    assert (tmp_path / "user").is_dir()
    assert (tmp_path / "links.json").is_file()
    assert any(link.title == "ROG official GIPHY" for link in links)


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


def test_asset_library_saves_links(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    library.save_links([AssetLink(title="Example", url="https://example.com", kind="gif", tags=("eye",))])

    links = library.load_links()

    assert links == [AssetLink(title="Example", url="https://example.com", kind="gif", tags=("eye",))]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_assets.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'usb9_lcd.assets'`.

- [ ] **Step 3: Implement asset library service**

Create `usb9_lcd/assets.py`:

```python
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
    def __init__(self, root: Path = Path("assets")) -> None:
        self.root = root
        self.presets_dir = root / "presets"
        self.user_dir = root / "user"
        self.links_path = root / "links.json"
        self.ensure()

    def ensure(self) -> None:
        self.presets_dir.mkdir(parents=True, exist_ok=True)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        if not self.links_path.exists():
            self.save_links(
                [
                    AssetLink(
                        title=item["title"],
                        url=item["url"],
                        kind=item["kind"],
                        tags=tuple(item["tags"]),
                    )
                    for item in DEFAULT_LINKS
                ]
            )

    def load_links(self) -> list[AssetLink]:
        self.ensure()
        try:
            raw = json.loads(self.links_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [
            AssetLink(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                kind=str(item.get("kind", "link")),
                tags=tuple(str(tag) for tag in item.get("tags", [])),
            )
            for item in raw
            if item.get("title") and item.get("url")
        ]

    def save_links(self, links: list[AssetLink]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = [
            {"title": link.title, "url": link.url, "kind": link.kind, "tags": list(link.tags)}
            for link in links
        ]
        self.links_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def import_file(self, source: Path) -> Path:
        self.ensure()
        if source.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
            raise ValueError(f"unsupported asset type: {source.suffix}")
        destination = self.user_dir / source.name
        copy2(source, destination)
        return destination

    def list_media(self) -> list[MediaAsset]:
        self.ensure()
        assets: list[MediaAsset] = []
        for directory in (self.presets_dir, self.user_dir):
            for path in sorted(directory.iterdir()):
                if path.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS or not path.is_file():
                    continue
                try:
                    with Image.open(path) as image:
                        frame_count = getattr(image, "n_frames", 1)
                        assets.append(
                            MediaAsset(
                                path=path,
                                kind=path.suffix.lower().removeprefix("."),
                                width=image.width,
                                height=image.height,
                                frame_count=frame_count,
                                animated=frame_count > 1,
                            )
                        )
                except (OSError, UnidentifiedImageError):
                    continue
        return assets
```

- [ ] **Step 4: Add starter `assets/links.json`**

Create `assets/links.json`:

```json
[
  {
    "title": "ROG official GIPHY",
    "url": "https://giphy.com/GlobalROG",
    "kind": "collection",
    "tags": ["rog", "gif"]
  },
  {
    "title": "Gif Abyss ROG animated emblem",
    "url": "https://gifs.alphacoders.com/gifs/view/202278",
    "kind": "gif",
    "tags": ["rog", "logo", "animation"]
  },
  {
    "title": "Pixabay cyber eye GIF search",
    "url": "https://pixabay.com/gifs/search/cyber%20eye/",
    "kind": "collection",
    "tags": ["eye", "cyber", "gif"]
  }
]
```

- [ ] **Step 5: Run asset tests**

Run:

```bash
pytest tests/test_assets.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add usb9_lcd/assets.py assets/links.json tests/test_assets.py
git commit -m "feat: add local asset library"
```

## Task 4: Generate Original Preset Eye Assets

**Files:**
- Create: `usb9_lcd/presets.py`
- Create generated files: `assets/presets/*.gif`, `assets/presets/*.png`
- Test: `tests/test_assets.py`

- [ ] **Step 1: Append failing preset generation test**

Append to `tests/test_assets.py`:

```python
from usb9_lcd.presets import generate_default_presets


def test_generate_default_presets_creates_eye_assets(tmp_path: Path):
    generated = generate_default_presets(tmp_path)

    assert {path.name for path in generated} == {
        "red_mechanical_eye.gif",
        "red_scan_eye.gif",
        "blue_hud_eye.gif",
        "glitch_standby_eye.gif",
    }
    for path in generated:
        assert path.exists()
        with Image.open(path) as image:
            assert image.size == (480, 480)
            assert getattr(image, "n_frames", 1) >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_assets.py::test_generate_default_presets_creates_eye_assets -q
```

Expected: FAIL with missing `usb9_lcd.presets`.

- [ ] **Step 3: Implement preset generation**

Create `usb9_lcd/presets.py`:

```python
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

PRESET_NAMES = (
    "red_mechanical_eye.gif",
    "red_scan_eye.gif",
    "blue_hud_eye.gif",
    "glitch_standby_eye.gif",
)


def _eye_frame(size: int, color: tuple[int, int, int], phase: float, glitch: bool = False) -> Image.Image:
    image = Image.new("RGB", (size, size), "#05070a")
    draw = ImageDraw.Draw(image)
    center = size // 2
    glow = int(30 + 30 * math.sin(phase))
    outer = (max(color[0] - 80, 0), max(color[1] - 80, 0), max(color[2] - 80, 0))

    for radius, alpha in ((190, 20), (150, 35), (112, 70)):
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        layer_draw = ImageDraw.Draw(layer)
        layer_draw.ellipse(
            (center - radius, center - radius, center + radius, center + radius),
            outline=(*color, alpha + glow),
            width=8,
        )
        layer = layer.filter(ImageFilter.GaussianBlur(6))
        image = Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")
        draw = ImageDraw.Draw(image)

    draw.ellipse((78, 122, 402, 358), fill=outer, outline=color, width=6)
    draw.ellipse((132, 154, 348, 326), fill="#090b10", outline=color, width=4)
    pupil_width = int(54 + 18 * math.sin(phase))
    draw.ellipse(
        (center - pupil_width, 178, center + pupil_width, 302),
        fill=color,
        outline=(255, 220, 230),
        width=2,
    )
    scan_y = int(140 + (math.sin(phase) + 1) * 92)
    draw.rectangle((92, scan_y, 388, scan_y + 5), fill=(255, 255, 255))
    for y in range(0, size, 18):
        draw.line((60, y, size - 60, y), fill=(20, 24, 30))

    if glitch:
        offset = int(8 * math.sin(phase * 2))
        draw.rectangle((90 + offset, 236, 390 + offset, 246), fill=color)
        draw.rectangle((120 - offset, 174, 330 - offset, 180), fill=(255, 255, 255))

    return image


def _save_gif(path: Path, frames: list[Image.Image]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=80, loop=0)


def generate_default_presets(output_dir: Path = Path("assets/presets")) -> list[Path]:
    specs = [
        ("red_mechanical_eye.gif", (255, 55, 78), False),
        ("red_scan_eye.gif", (255, 34, 68), False),
        ("blue_hud_eye.gif", (70, 190, 255), False),
        ("glitch_standby_eye.gif", (255, 70, 110), True),
    ]
    generated: list[Path] = []
    for name, color, glitch in specs:
        frames = [
            _eye_frame(480, color, phase=(math.tau * index / 18), glitch=glitch)
            for index in range(18)
        ]
        path = output_dir / name
        _save_gif(path, frames)
        generated.append(path)
    return generated
```

- [ ] **Step 4: Generate project preset files**

Run:

```bash
python -c "from usb9_lcd.presets import generate_default_presets; generate_default_presets()"
```

Expected: `assets/presets/red_mechanical_eye.gif`, `red_scan_eye.gif`, `blue_hud_eye.gif`, and `glitch_standby_eye.gif` exist.

- [ ] **Step 5: Run asset tests**

Run:

```bash
pytest tests/test_assets.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add usb9_lcd/presets.py assets/presets tests/test_assets.py
git commit -m "feat: add default eye preset assets"
```

## Task 5: Add Monitoring Dashboard Renderer

**Files:**
- Create: `usb9_lcd/monitoring/render.py`
- Test: `tests/test_monitor_render.py`

- [ ] **Step 1: Write failing renderer tests**

Create `tests/test_monitor_render.py`:

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image

from usb9_lcd.drivers.base import (
    Capability,
    DeviceConnection,
    DisplayDevice,
    PixelFormat,
    PixelStyle,
    PreviewProfile,
    PreviewShape,
)
from usb9_lcd.monitoring.models import CpuTelemetry, GpuTelemetry, SystemTelemetry
from usb9_lcd.monitoring.render import render_monitoring_image


def _device(width: int = 480, height: int = 480) -> DisplayDevice:
    return DisplayDevice(
        connection=DeviceConnection(
            driver_id="test.driver",
            display_name="Test Display",
            paths=(Path("/dev/null"),),
            writable=True,
            readable=True,
        ),
        width=width,
        height=height,
        pixel_format=PixelFormat.RGB565,
        preview=PreviewProfile(width=width, height=height, shape=PreviewShape.SQUARE, pixel_style=PixelStyle.CONTINUOUS),
        capabilities=frozenset({Capability.STATIC_IMAGE}),
    )


def test_render_monitoring_image_uses_device_size():
    telemetry = SystemTelemetry(
        cpu=CpuTelemetry(package_temperature_c=54.5, utilization_percent=18, available=True),
        gpu=GpuTelemetry(name="RTX", temperature_c=61, utilization_percent=42, power_w=216.5, memory_used_mb=8000, memory_total_mb=24000, graphics_clock_mhz=2700, available=True),
        captured_at=datetime(2026, 5, 20, 12, 0, 0),
    )

    image = render_monitoring_image(telemetry, _device())

    assert isinstance(image, Image.Image)
    assert image.mode == "RGB"
    assert image.size == (480, 480)
    assert image.getpixel((10, 10)) != (0, 0, 0)


def test_render_monitoring_image_handles_unavailable_values():
    telemetry = SystemTelemetry(
        cpu=CpuTelemetry(error="no sensor"),
        gpu=GpuTelemetry(error="nvidia-smi missing"),
        captured_at=datetime(2026, 5, 20, 12, 0, 0),
    )

    image = render_monitoring_image(telemetry, _device(320, 240))

    assert image.size == (320, 240)
```

- [ ] **Step 2: Run renderer tests to verify they fail**

Run:

```bash
pytest tests/test_monitor_render.py -q
```

Expected: FAIL with missing `usb9_lcd.monitoring.render`.

- [ ] **Step 3: Implement dashboard renderer**

Create `usb9_lcd/monitoring/render.py`:

```python
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from usb9_lcd.drivers.base import DisplayDevice

from .models import SystemTelemetry


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _value(value: object, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.0f}{suffix}"
    return f"{value}{suffix}"


def _card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], title: str, value: str, accent: tuple[int, int, int]) -> None:
    draw.rounded_rectangle(box, radius=14, fill=(22, 27, 35), outline=(45, 54, 68), width=2)
    x1, y1, x2, y2 = box
    draw.text((x1 + 18, y1 + 14), title, fill=(150, 164, 184), font=_font(20))
    draw.text((x1 + 18, y1 + 46), value, fill=accent, font=_font(46))
    draw.line((x1 + 18, y2 - 18, x2 - 18, y2 - 18), fill=accent, width=4)


def render_monitoring_image(telemetry: SystemTelemetry, device: DisplayDevice) -> Image.Image:
    width = device.width
    height = device.height
    image = Image.new("RGB", (width, height), (8, 10, 14))
    draw = ImageDraw.Draw(image)

    margin = max(12, width // 24)
    draw.rounded_rectangle((margin, margin, width - margin, height - margin), radius=18, fill=(14, 17, 23), outline=(48, 58, 74), width=2)
    draw.text((margin + 18, margin + 14), "USB9 LCD MONITOR", fill=(230, 238, 248), font=_font(max(18, width // 18)))

    card_top = margin + 62
    card_height = max(112, height // 4)
    gap = max(10, width // 48)
    card_width = (width - margin * 2 - gap * 3) // 2
    cpu_box = (margin + gap, card_top, margin + gap + card_width, card_top + card_height)
    gpu_box = (margin + gap * 2 + card_width, card_top, margin + gap * 2 + card_width * 2, card_top + card_height)

    _card(draw, cpu_box, "CPU", _value(telemetry.cpu.package_temperature_c, "C"), (63, 217, 138))
    _card(draw, gpu_box, "GPU", _value(telemetry.gpu.temperature_c, "C"), (106, 183, 255))

    info_y = card_top + card_height + 24
    info_lines = [
        f"GPU: {telemetry.gpu.name if telemetry.gpu.available else 'N/A'}",
        f"LOAD: {_value(telemetry.gpu.utilization_percent, '%')}  POWER: {_value(telemetry.gpu.power_w, 'W')}",
        f"VRAM: {_value(telemetry.gpu.memory_used_mb, 'MB')} / {_value(telemetry.gpu.memory_total_mb, 'MB')}",
        f"CLOCK: {_value(telemetry.gpu.graphics_clock_mhz, 'MHz')}",
        telemetry.captured_at.strftime("%H:%M:%S"),
    ]
    for index, line in enumerate(info_lines):
        draw.text((margin + 24, info_y + index * 34), line, fill=(176, 188, 204), font=_font(max(16, width // 26)))

    return image
```

- [ ] **Step 4: Run renderer tests**

Run:

```bash
pytest tests/test_monitor_render.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add usb9_lcd/monitoring/render.py tests/test_monitor_render.py
git commit -m "feat: render monitoring dashboard frames"
```

## Task 6: Redesign GUI As Dark Dashboard

**Files:**
- Modify: `usb9_lcd/gui/main_window.py`
- Modify: `tests/test_gui_import.py`

- [ ] **Step 1: Replace GUI smoke tests with dashboard expectations**

Update the two existing GUI tests in `tests/test_gui_import.py` so they expect the new navigation and first page:

```python
def test_main_window_constructs_with_dark_dashboard_pages():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    window = MainWindow(driver=FakeDriver(), telemetry_provider=lambda: _fake_telemetry())

    assert window.windowTitle() == "usb9-lcd"
    assert [window.navigation.item(index).text() for index in range(window.navigation.count())] == [
        "监控",
        "素材库",
        "上传",
        "设备",
        "设置",
    ]
    assert window.navigation.currentRow() == 0
    assert window.pages.count() == 5
    assert "CPU" in window.cpu_temp_value.text()
    assert "GPU" in window.gpu_temp_value.text()

    window.close()
    app.quit()
```

Add helper near `FakeDriver`:

```python
from datetime import datetime

from usb9_lcd.monitoring.models import CpuTelemetry, GpuTelemetry, SystemTelemetry


def _fake_telemetry() -> SystemTelemetry:
    return SystemTelemetry(
        cpu=CpuTelemetry(package_temperature_c=54.0, utilization_percent=18, available=True),
        gpu=GpuTelemetry(name="RTX", temperature_c=61, utilization_percent=42, power_w=216.0, memory_used_mb=8000, memory_total_mb=24000, graphics_clock_mhz=2700, available=True),
        captured_at=datetime(2026, 5, 20, 12, 0, 0),
    )
```

- [ ] **Step 2: Run GUI tests to verify they fail**

Run:

```bash
pytest tests/test_gui_import.py -q
```

Expected: FAIL because current navigation is still `图片`, `设备`.

- [ ] **Step 3: Implement dark dashboard pages**

In `usb9_lcd/gui/main_window.py`, refactor `MainWindow.__init__` to accept:

```python
from collections.abc import Callable
from usb9_lcd.monitoring.models import SystemTelemetry
from usb9_lcd.monitoring.service import collect_system_telemetry

def __init__(
    self,
    driver: DisplayDriver | None = None,
    telemetry_provider: Callable[[], SystemTelemetry] = collect_system_telemetry,
) -> None:
```

Implement:

- `self.telemetry_provider = telemetry_provider`
- Navigation labels: `("监控", "素材库", "上传", "设备", "设置")`
- `self.pages.addWidget(self._monitor_page())`
- `self.pages.addWidget(self._asset_page())`
- `self.pages.addWidget(self._upload_page())`
- `self.pages.addWidget(self._device_page())`
- `self.pages.addWidget(self._settings_page())`
- Rename the current `_image_page()` to `_upload_page()` and keep existing upload controls there.
- Add `_monitor_page()` with dark cards and labels named `cpu_temp_value`, `gpu_temp_value`, `gpu_detail_value`, `monitor_preview`.
- Add `refresh_telemetry()` that calls `self.telemetry_provider()` and updates labels.
- Apply a dark stylesheet in `_apply_theme()`.

Use this stylesheet:

```python
self.setStyleSheet(
    """
    QMainWindow, QWidget { background: #0f1115; color: #e8eef7; }
    QListWidget { background: #171b22; border: 0; padding: 8px; }
    QListWidget::item { padding: 10px; border-radius: 6px; color: #aab4c3; }
    QListWidget::item:selected { background: #263140; color: #7ee787; }
    QLabel#MetricValue { font-size: 34px; font-weight: 700; }
    QFrame#MetricCard { background: #191f28; border: 1px solid #2d3746; border-radius: 8px; }
    QPushButton { background: #263140; border: 1px solid #3a4656; border-radius: 6px; padding: 8px 12px; }
    QPushButton:hover { background: #304055; }
    QComboBox, QLineEdit, QTextEdit { background: #11161d; border: 1px solid #303a46; border-radius: 6px; padding: 6px; color: #e8eef7; }
    """
)
```

- [ ] **Step 4: Run GUI tests**

Run:

```bash
pytest tests/test_gui_import.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add usb9_lcd/gui/main_window.py tests/test_gui_import.py
git commit -m "feat: redesign gui as monitoring dashboard"
```

## Task 7: Add Asset Library GUI Page

**Files:**
- Modify: `usb9_lcd/gui/main_window.py`
- Modify: `tests/test_gui_import.py`

- [ ] **Step 1: Add failing asset page GUI test**

Append to `tests/test_gui_import.py`:

```python
def test_main_window_asset_page_lists_links_and_media(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.assets import AssetLibrary
    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    library = AssetLibrary(tmp_path)
    Image.new("RGB", (2, 2), (255, 0, 0)).save(tmp_path / "user" / "red.png")

    window = MainWindow(driver=FakeDriver(), telemetry_provider=lambda: _fake_telemetry(), asset_library=library)
    window.refresh_assets()

    assert "red.png" in window.asset_list_text.toPlainText()
    assert "ROG official GIPHY" in window.asset_links_text.toPlainText()

    window.close()
    app.quit()
```

- [ ] **Step 2: Run GUI tests to verify failure**

Run:

```bash
pytest tests/test_gui_import.py::test_main_window_asset_page_lists_links_and_media -q
```

Expected: FAIL because `MainWindow` does not accept `asset_library`.

- [ ] **Step 3: Implement asset-library page**

Update `MainWindow.__init__` signature:

```python
from usb9_lcd.assets import AssetLibrary

def __init__(
    self,
    driver: DisplayDriver | None = None,
    telemetry_provider: Callable[[], SystemTelemetry] = collect_system_telemetry,
    asset_library: AssetLibrary | None = None,
) -> None:
    self.asset_library = asset_library or AssetLibrary()
```

Implement `_asset_page()` with:

- `self.asset_list_text = QTextEdit(readOnly=True)`
- `self.asset_links_text = QTextEdit(readOnly=True)`
- `refresh_assets_button = QPushButton("刷新素材")`
- `import_asset_button = QPushButton("导入素材")`
- `refresh_assets()` method:

```python
def refresh_assets(self) -> None:
    media_lines = [
        f"{asset.path.name} | {asset.width}x{asset.height} | {asset.kind} | {'动图' if asset.animated else '静态'} | {asset.frame_count} 帧"
        for asset in self.asset_library.list_media()
    ]
    link_lines = [
        f"{link.title} | {link.url} | {', '.join(link.tags)}"
        for link in self.asset_library.load_links()
    ]
    self.asset_list_text.setPlainText("\n".join(media_lines) or "暂无本地素材")
    self.asset_links_text.setPlainText("\n".join(link_lines) or "暂无链接")
```

Implement `import_asset()` using `QFileDialog.getOpenFileName`, calling `self.asset_library.import_file(Path(selected))`, then `refresh_assets()`.

- [ ] **Step 4: Run GUI tests**

Run:

```bash
pytest tests/test_gui_import.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add usb9_lcd/gui/main_window.py tests/test_gui_import.py
git commit -m "feat: add asset library gui page"
```

## Task 8: Add Monitoring Frame Upload Controls

**Files:**
- Modify: `usb9_lcd/gui/main_window.py`
- Modify: `tests/test_gui_import.py`

- [ ] **Step 1: Add failing monitor upload test**

Append to `tests/test_gui_import.py`:

```python
def test_main_window_uploads_monitoring_frame_to_selected_device():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    driver = FakeDriver()
    window = MainWindow(driver=driver, telemetry_provider=lambda: _fake_telemetry())

    window.upload_monitoring_frame()

    assert len(driver.uploads) == 1
    uploaded_device, uploaded_frame = driver.uploads[0]
    assert uploaded_device is driver.device
    assert len(uploaded_frame) == driver.device.width * driver.device.height * 2
    assert "监控画面已上传" in window.statusBar().currentMessage()

    window.close()
    app.quit()
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
pytest tests/test_gui_import.py::test_main_window_uploads_monitoring_frame_to_selected_device -q
```

Expected: FAIL because `upload_monitoring_frame` does not exist.

- [ ] **Step 3: Implement monitor upload control**

In `usb9_lcd/gui/main_window.py`:

- Add a button on monitor page: `上传监控画面`
- Store latest telemetry: `self.latest_telemetry: SystemTelemetry | None = None`
- In `refresh_telemetry()`, assign `self.latest_telemetry = telemetry`
- Implement:

```python
from usb9_lcd.image import image_to_rgb565_bytes
from usb9_lcd.image import FrameConfig
from usb9_lcd.monitoring.render import render_monitoring_image

def upload_monitoring_frame(self) -> None:
    device = self._selected_device()
    if device is None:
        self.statusBar().showMessage("请先选择设备")
        return
    telemetry = self.latest_telemetry or self.telemetry_provider()
    try:
        image = render_monitoring_image(telemetry, device)
        frame = image_to_rgb565_bytes(
            image,
            FrameConfig(width=device.width, height=device.height, fit="stretch"),
        )
        self.driver.upload_static_frame(device, frame)
    except Exception as error:
        self.statusBar().showMessage(f"监控画面上传失败：{error}")
        return
    self.statusBar().showMessage("监控画面已上传")
```

- [ ] **Step 4: Run GUI tests**

Run:

```bash
pytest tests/test_gui_import.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add usb9_lcd/gui/main_window.py tests/test_gui_import.py
git commit -m "feat: upload monitoring frame from gui"
```

## Task 9: Add Telemetry Timer And Documentation

**Files:**
- Modify: `usb9_lcd/gui/main_window.py`
- Modify: `README.md`
- Test: `tests/test_gui_import.py`

- [ ] **Step 1: Add failing timer configuration test**

Append to `tests/test_gui_import.py`:

```python
def test_main_window_configures_telemetry_timer():
    from PySide6.QtWidgets import QApplication

    from usb9_lcd.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(driver=FakeDriver(), telemetry_provider=lambda: _fake_telemetry())

    assert window.telemetry_timer.interval() == 2000
    assert window.telemetry_timer.isActive() is True

    window.close()
    app.quit()
```

- [ ] **Step 2: Run timer test to verify failure**

Run:

```bash
pytest tests/test_gui_import.py::test_main_window_configures_telemetry_timer -q
```

Expected: FAIL because `telemetry_timer` does not exist.

- [ ] **Step 3: Implement telemetry timer**

In `MainWindow.__init__`, after UI setup:

```python
from PySide6.QtCore import QTimer

self.telemetry_timer = QTimer(self)
self.telemetry_timer.setInterval(2000)
self.telemetry_timer.timeout.connect(self.refresh_telemetry)
self.telemetry_timer.start()
self.refresh_telemetry()
```

Ensure `closeEvent()` stops the timer:

```python
def closeEvent(self, event) -> None:  # noqa: ANN001
    self.telemetry_timer.stop()
    super().closeEvent(event)
```

- [ ] **Step 4: Update README**

Add under `Desktop GUI`:

```markdown
The `监控` page reads NVIDIA GPU telemetry through `nvidia-smi` and CPU temperature from Linux hwmon sensors. If a sensor is unavailable, the GUI shows `不可用` and keeps running.

The `素材库` page indexes local files under `assets/user/`, generated presets under `assets/presets/`, and source links from `assets/links.json`.
```

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/test_gui_import.py -q
pytest -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add usb9_lcd/gui/main_window.py tests/test_gui_import.py README.md
git commit -m "feat: refresh dashboard telemetry"
```

## Task 10: Final Verification

**Files:**
- No code files unless verification finds a defect.

- [ ] **Step 1: Run full automated tests**

Run:

```bash
pytest -q
```

Expected: PASS.

- [ ] **Step 2: Verify hardware detection**

Run:

```bash
python -m usb9_lcd detect
```

Expected: control and data hidraw paths print with `write=True`.

- [ ] **Step 3: Verify existing static upload dry-run**

Run:

```bash
python -m usb9_lcd show ./sample.png --dry-run --rotate 90 --fit contain --background '#101820'
```

Expected:

```text
prepared frame: 460800 bytes
```

- [ ] **Step 4: Verify NVIDIA telemetry manually**

Run:

```bash
python - <<'PY'
from usb9_lcd.monitoring import collect_nvidia_gpu
print(collect_nvidia_gpu())
PY
```

Expected: available telemetry on this NVIDIA machine, or a clear error if the driver command is unavailable.

- [ ] **Step 5: Verify GUI launch in headless mode**

Run:

```bash
QT_QPA_PLATFORM=offscreen timeout 3s python -m usb9_lcd.gui.app
```

Expected: Qt app enters event loop and exits by timeout with code 124. No Python traceback.

- [ ] **Step 6: Verify git status**

Run:

```bash
git status --short
```

Expected: clean worktree.
