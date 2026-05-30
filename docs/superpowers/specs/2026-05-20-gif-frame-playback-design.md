# USB9 LCD GIF Frame Playback Design

## Goal

Add practical GIF playback for the ASUS USB9 LCD by decoding animated image frames and sending them through the existing static RGB565 upload protocol at a conservative frame rate.

This is not a native device animation protocol. It is a first usable playback mode that works with the protocol already proven on the screen.

## Scope

In scope:

- Decode GIF/WebP animated image frames with Pillow.
- Resize/crop/contain frames using the existing fit/background/rotation behavior.
- Convert frames to RGB565 for a selected `DisplayDevice`.
- Add a playback controller that uploads frames sequentially through a `DisplayDriver`.
- Add GUI controls on the asset or upload path to play/stop a selected animated asset.
- Keep playback conservative by default, around 2 FPS.
- Prevent overlapping uploads and allow stopping after the current frame finishes.

Out of scope:

- Native ASUS animation protocol reverse engineering.
- High-FPS playback guarantees.
- Audio/video playback.
- Background playback after the GUI closes.

## Playback Behavior

The first version uses low-rate frame refresh:

1. User selects a GIF from the asset library.
2. The app decodes frames lazily or with a bounded frame count.
3. Each frame is rendered to the selected device dimensions.
4. The frame is uploaded via `driver.upload_static_frame(device, frame)`.
5. A timer schedules the next frame only after the current upload completes or returns.

Default settings:

- Playback FPS: 2.
- Maximum decoded frames per loop: 120.
- Fit: contain.
- Background: `#000000`.
- Rotation: 0.

If a GIF has frame durations, the service may use them later, but the first version uses a fixed FPS to keep behavior predictable.

## Non-GUI Services

Add an animation rendering layer:

- `AnimatedFrame`: frame bytes, index, duration_ms.
- `AnimationRenderSettings`: fit, rotate, background, max_frames, fps.
- `iter_animated_frames(path, device, settings) -> Iterator[AnimatedFrame]`

The iterator should:

- Reject unsupported devices using the same checks as static rendering.
- Reject non-animated or unreadable files with clear `ValueError`.
- Stop after `max_frames`.
- Use the existing image conversion path so dimensions and RGB565 byte order stay consistent.

## GUI Integration

Add controls for selected animated assets:

- Select an asset in the asset page.
- `播放到屏幕` starts playback if the selected asset is animated.
- `停止播放` stops after the current upload finishes.
- Status bar shows playing, stopped, and error messages.

Playback must not run long loops on the GUI thread. Use a `QTimer` to schedule frame steps and avoid starting a new frame while an upload is in progress. A fully threaded uploader can be added later if the low-rate first version still feels too blocking.

## Error Handling

- No selected device: show `请先选择设备`.
- No selected animated asset: show `请先选择动图素材`.
- Bad file: show `动画播放失败：...`.
- Upload failure: stop playback and show `动画播放失败：...`.
- User stop: stop timer and show `动画播放已停止`.

## Tests

Add tests for:

- GIF frame iterator returns RGB565 frames with device byte length.
- Iterator rejects static images.
- Iterator honors `max_frames`.
- GUI can select an animated asset and start playback with fake driver.
- Playback stop prevents additional frame uploads.
- Bad upload stops playback and reports failure.

## Manual Verification

- `pytest -q`
- Launch GUI.
- Open `素材库`.
- Select a default eye GIF.
- Select the ASUS device.
- Click `播放到屏幕`.
- Confirm the screen changes frames at a conservative rate.
- Click `停止播放`.
