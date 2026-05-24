# USB9 LCD Monitoring Dashboard And Asset Library Design

## Goal

Upgrade the current desktop GUI from a basic image uploader into a dark hardware dashboard for the ASUS USB9 LCD. The first screen should focus on CPU/GPU monitoring, with a device-adaptive preview and a local asset library for static images, GIFs, and collected ROG-style animation links.

The user has an NVIDIA GPU, so GPU telemetry should use `nvidia-smi` first. The project should keep the existing static image upload path working and use it as the first output mechanism for monitoring frames.

## Scope

In scope:

- Dark dashboard visual redesign for the PySide6 GUI.
- Monitoring page with CPU and NVIDIA GPU telemetry.
- Device preview that uses the existing `DisplayDevice.preview` profile and does not assume the ASUS screen is circular.
- Local asset library for imported images/GIFs and collected online links.
- Built-in original preset assets with a red/blue cyber-eye style.
- Monitor-frame renderer that produces a 480x480 RGB image/frame and can be uploaded through the existing static image protocol.
- Graceful handling when telemetry commands or sensors are unavailable.

Out of scope for this phase:

- Reverse engineering a native hardware GIF/video protocol.
- Bundling third-party ROG trademark assets as project-owned preset files.
- Uploading copyrighted online GIFs automatically without the user importing or downloading them.
- Real-time high-FPS animation guarantees. GIF playback may be previewed locally, but hardware output starts as static or low-rate frame refresh.

## User Experience

The main window opens on a `监控` page. The layout uses a dark, dense hardware-control style:

- Left navigation: `监控`, `素材库`, `上传`, `设备`, `设置`.
- Center dashboard: CPU/GPU temperature cards, load, power, memory, clocks, and update status.
- Right panel: adaptive display preview and selected output mode.
- Status bar: last refresh time, selected device, and upload errors.

The `素材库` page provides:

- Local imported assets from `assets/user/`.
- Preset generated assets from `assets/presets/`.
- Collected links stored in `assets/links.json`.
- Preview for PNG/JPG/WEBP/GIF.
- Actions: import file, open source link, select for upload, and send first frame to display.

The existing static image upload controls move to an `上传` page and keep their current fit/rotate/background behavior.

## Monitoring Data Model

Add a non-GUI monitoring layer so tests can validate behavior without Qt widgets.

Suggested dataclasses:

- `SensorReading`: label, value, unit, available, detail.
- `GpuTelemetry`: name, temperature_c, utilization_percent, power_w, memory_used_mb, memory_total_mb, graphics_clock_mhz, available, error.
- `CpuTelemetry`: package_temperature_c, utilization_percent, available, error.
- `SystemTelemetry`: cpu, gpu, captured_at.

Unavailable fields should be `None` and accompanied by a short error string. GUI consumers must render missing data as `不可用`, not crash.

## NVIDIA GPU Collection

Use `nvidia-smi` as the primary NVIDIA collector:

```bash
nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,power.draw,memory.used,memory.total,clocks.current.graphics --format=csv,noheader,nounits
```

Parsing rules:

- Support one or more GPUs but display the first GPU by default in this phase.
- Strip whitespace and parse numeric fields defensively.
- If `nvidia-smi` is missing, times out, or returns non-zero, return unavailable telemetry with the error.
- Use a short timeout, around 2 seconds, to avoid freezing the GUI.

## CPU Collection

Use Linux sensor sources in this order:

1. `/sys/class/hwmon` for temperature.
2. `sensors` command as fallback if needed.

For the first implementation, package temperature is enough. CPU utilization can be added by sampling `/proc/stat`; if not implemented in the first pass, the GUI should show CPU load as unavailable while still showing temperature.

## Dashboard Rendering For LCD

Add a renderer that can draw a monitoring dashboard to a PIL image sized to the selected device. The ASUS driver provides 480x480 RGB565 output through the existing render/upload path.

Renderer requirements:

- Input: `SystemTelemetry`, `DisplayDevice`, visual theme.
- Output: PIL `Image` or RGB/RGB565 frame via the existing render pipeline.
- Use readable large temperature values.
- Keep visual elements inside the device dimensions.
- Support at least square displays now; use preview/device dimensions so later rectangle or matrix displays can get different layouts.

Hardware output should initially refresh at a conservative rate, for example every 2-5 seconds, because the current protocol uploads full frames over HID.

## Asset Library

Default project directories:

```text
assets/
  presets/
  user/
  links.json
```

`assets/links.json` stores collected online sources:

```json
[
  {
    "title": "ROG official GIPHY",
    "url": "https://giphy.com/GlobalROG",
    "kind": "collection",
    "tags": ["rog", "gif"]
  }
]
```

Supported imported formats:

- Static: PNG, JPG, JPEG, WEBP, BMP.
- Animated preview: GIF, WEBP where Pillow supports frames.

ROG-style online GIFs should be treated as user-managed links/imports. The app may help open the source URL and import local downloads, but it should not hard-code online downloading as a required workflow.

## Preset Assets

Create original presets that match the desired mood without relying on external files:

- Red mechanical eye with scan-line effect.
- Red breathing cyber eye.
- Blue HUD eye.
- Glitch-style standby eye.

Implementation can start with generated PIL frames saved as GIF/PNG in `assets/presets/`. Later these can be replaced by richer hand-made assets.

## Error Handling

- Telemetry unavailable: show `不可用` and keep the previous UI responsive.
- Device not found: monitoring and asset preview still work; upload actions are disabled or show a clear message.
- Upload error: show the exception in the status bar and keep the app open.
- Invalid imported file: reject with a user-readable message.
- GIF too large: allow preview but warn before high-frequency output.

## Testing

Add focused tests for:

- `nvidia-smi` parser with normal output.
- NVIDIA collector unavailable path.
- CPU hwmon parser using temporary directories.
- Asset link loading and default directory creation.
- Dashboard renderer produces the selected device size.
- GUI can construct the dark dashboard with injected fake telemetry and fake driver.
- Upload still calls the existing driver/render path.

Manual verification:

- `pytest -q`
- `python -m usb9_lcd detect`
- `python -m usb9_lcd show ./sample.png --dry-run`
- Launch GUI and confirm the `监控` page opens first.
- Confirm NVIDIA GPU temperature appears on this machine.

## Open Decisions

- The first hardware monitoring output refresh interval should default to 3 seconds.
- GIF output to hardware should be implemented later as low-rate frame refresh first, before spending more time on native animation protocol reverse engineering.
- The default GUI style is the confirmed dark hardware dashboard direction.
