# USB9 LCD Static Image Display Design

## Goal

Build a Python command line program that can display one static PNG or JPG image on the ASUS TUF GAMING LC III 360 ARGB LCD connected through the motherboard USB 9-pin header.

The detected device is:

- USB ID: `0b05:1c7b`
- Product: `ASUSTek Computer, Inc. TUF GAMING LC III 360 ARGB LCD`
- HID control/status interface: `/dev/hidraw10`, USB interface 0, 440-byte reports
- HID frame/data interface: `/dev/hidraw11`, USB interface 1, 1024-byte output reports and 16-byte input reports

The first release focuses only on a static image. GIF/video playback and CPU/GPU temperature monitoring will reuse the same frame rendering and transfer pipeline later.

The implementation should also leave room for a more convenient user-facing tool. The first version can be command line based, but command names, configuration, and module boundaries should support adding presets, a local UI, or a background service later without rewriting the hardware code.

## User Commands

The program exposes a small CLI:

```bash
python -m usb9_lcd detect
python -m usb9_lcd show ./image.png
```

`detect` prints the matched USB device, discovered hidraw nodes, report sizes, and permission status.

`show` loads an image, converts it to the target frame format, and sends it to the LCD.

Future convenience commands can build on the same structure, for example:

```bash
python -m usb9_lcd preset image ./image.png
python -m usb9_lcd preset monitor
python -m usb9_lcd daemon
```

## Architecture

### Device Layer

The device layer finds the target device by USB vendor/product ID instead of hard-coding hidraw numbers. The current machine maps the LCD to `/dev/hidraw10` and `/dev/hidraw11`, but hidraw numbering can change after reboot or reconnect.

Discovery reads udev/sysfs metadata and selects HID interfaces whose parent path contains `0003:0B05:1C7B`. It records:

- hidraw device path
- USB interface number
- report descriptor size
- readable/writable permission status

If the user lacks permissions, the CLI reports a clear error and suggests a udev rule instead of failing with a raw `PermissionError`.

### Image Layer

The image layer uses Pillow to:

- open PNG/JPG input
- convert to RGB
- resize and center-crop to the configured LCD resolution
- convert pixels to the protocol encoder input

The LCD is a 2.8 inch full-color IPS LCD. The first implementation uses 480x480 as the default frame size, while keeping width and height as configuration values so hardware testing can correct them without changing the image or protocol APIs.

### Protocol Layer

The protocol layer owns all ASUS HID packet details:

- command packets sent to the 440-byte interface
- frame/data packets sent to the 1024-byte interface
- chunking and sequence numbers
- optional acknowledgement reads from the 16-byte input endpoint

Because ASUS does not publish the LCD protocol, the first implementation treats the protocol as experimentally verified code. It will start with non-destructive detection and explicit debug logging, then add image transfer once the expected packet framing is identified.

The protocol layer exposes a narrow interface:

```python
class LcdProtocol:
    def upload_frame(self, frame: bytes) -> None:
        ...
```

Higher layers do not know about report IDs, packet headers, or hidraw paths.

### CLI Layer

The CLI is intentionally small. It parses commands, calls the device layer, runs image conversion, and delegates transfer to the protocol layer.

The CLI should avoid exposing protocol details to the user. Common choices such as image fit mode, rotation, and screen resolution should be options or configuration values with sensible defaults.

### Usability Layer

After the hardware path works, a usability layer can be added above the CLI and protocol modules. It should focus on daily use:

- named presets for static image, animated media, and sensor dashboard modes
- a config file for default image path, fit mode, rotation, and refresh interval
- a background daemon for temperature monitoring and GIF/video playback
- a local web UI or desktop tray UI if the CLI becomes inconvenient

This layer must call the same public APIs as the CLI, so the hardware protocol code remains isolated and testable.

## Error Handling

The program handles:

- target USB device not found
- hidraw node not found
- insufficient hidraw permissions
- unsupported or unreadable image file
- image conversion failure
- short writes or HID write failures
- missing protocol acknowledgement when acknowledgements are expected

Errors should include the command that helps the user diagnose the issue, such as `python -m usb9_lcd detect`.

## Testing

Automated tests cover logic that does not require the physical LCD:

- device matching from sample udev/sysfs data
- image resize and crop dimensions
- frame byte length for a configured resolution
- packet chunking boundaries
- CLI argument parsing

Hardware verification is manual for the first release:

1. Run `python -m usb9_lcd detect`.
2. Confirm it finds USB ID `0b05:1c7b`.
3. Run `python -m usb9_lcd show ./sample.png`.
4. Confirm the LCD updates and the command exits cleanly.

## Future Work

After static image display works:

- GIF/video support can decode frames with Pillow or imageio and call `upload_frame` repeatedly with frame timing.
- CPU/GPU temperature monitoring can render a dashboard image periodically and call `upload_frame`.
- A small daemon can keep the display updated without repeatedly starting Python.
