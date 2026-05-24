# USB9 LCD Desktop GUI Design

## Goal

Build a PySide6 desktop GUI for controlling USB-connected LCD screens. The first usable version focuses on the current ASUS TUF GAMING LC III 360 ARGB LCD static-image workflow, while keeping the GUI independent from any one device protocol.

The GUI must let the user:

- detect connected supported screens
- select the active device
- choose a static image
- preview the final frame before upload
- adjust fit, rotation, and background color
- send the frame to the selected device

GIF/video playback and CPU/GPU temperature monitoring remain visible as future modes, but are not implemented in the first GUI release.

## Confirmed Decisions

- Use PySide6 / Qt for the desktop app.
- Use a left-side mode navigation layout.
- Add a device-driver abstraction before wiring the GUI to hardware.
- Treat ASUS LC III as the first built-in driver, not as the whole app model.
- Do not assume the ASUS display is circular. The current verified output frame is 480 x 480, so its first preview profile is square unless later calibration proves otherwise.
- Make the preview component adaptive to the selected device profile.

## Main Window

The main window uses a three-area layout:

- left navigation: mode selection and current device summary
- center workspace: mode-specific preview and primary actions
- right settings panel: current mode settings and device status

Navigation entries:

- Image: enabled in the first release
- GIF / Video: disabled placeholder in the first release
- Monitor: disabled placeholder in the first release
- Device: enabled in the first release

The app should open directly into the Image page if a supported device is detected. If no device is detected, it should open to a clear device status view and keep the image controls disabled until a device appears.

## Device Driver Interface

The GUI talks to devices through a common internal interface instead of importing ASUS protocol details directly.

Each driver provides:

- stable driver id
- display name
- discovery logic
- active device path or connection handle details
- display width and height
- output pixel format
- preview profile
- capability flags
- frame upload method

Initial capability flags:

- `static_image`
- `animation`
- `sensor_monitor`

The first ASUS driver only advertises `static_image`.

The first implementation can keep drivers as built-in Python modules. A full external plugin loader is out of scope until more devices exist.

## Preview Profile

Each detected device exposes a preview profile. The GUI uses this profile to render the preview shape and scale, but the image pipeline still renders to the real device pixel dimensions.

Preview profile fields:

- `width`
- `height`
- `shape`: `square`, `rectangle`, `circle`, or `matrix`
- `pixel_style`: `continuous` or `matrix`
- `orientation`: normal output orientation before user rotation
- optional device label

ASUS LC III initial profile:

- `width`: 480
- `height`: 480
- `shape`: `square`
- `pixel_style`: `continuous`
- `orientation`: 0

If a future device is a circular display, only that device driver should declare `shape=circle`. If a future device is a low-resolution pixel matrix, its profile should use `pixel_style=matrix` so the GUI can show a grid-like preview.

## Image Page

The Image page contains:

- file picker for PNG/JPG/BMP and other formats Pillow can open
- adaptive preview of the final frame
- fit selector: `cover`, `contain`, `stretch`
- rotation selector: `0`, `90`, `180`, `270`
- background color input for `contain`
- dry-run/render validation feedback
- send button

The preview should update after image or setting changes. Upload uses the same frame generation path as the CLI so CLI and GUI output stay consistent.

## Device Page

The Device page contains:

- detected supported devices
- active driver name
- device paths or connection summary
- width, height, pixel format, and preview profile
- permission/read/write status when available
- refresh detection button
- calibration helper entry point

The calibration helper can initially be simple: send or preview a test image with border, corner labels, and orientation markers. Full calibration persistence can come later.

## Error Handling

The GUI should turn known failures into visible status messages:

- no supported device found
- device lacks write permission
- image file cannot be opened
- invalid background color
- frame preparation failed
- upload failed or device disconnected

Errors should not crash the window. Hardware upload failures should leave the UI usable and allow retry after refresh.

## Testing

Automated tests should cover non-visual logic:

- driver profile shape and capability data
- ASUS driver wrapping existing discovery and protocol APIs
- GUI-facing image settings converted into `FrameConfig`
- preview geometry calculations for square, rectangle, circle, and matrix profiles
- upload action calling the selected driver with rendered frame bytes

Manual verification for the first GUI release:

1. Launch the GUI.
2. Confirm ASUS LC III is detected.
3. Select an image and verify preview updates.
4. Change fit, rotation, and background.
5. Send to LCD and confirm the hardware changes.
6. Disconnect or block permissions and confirm the GUI shows a recoverable error.

## Out Of Scope For First GUI Release

- GIF/video playback loop
- CPU/GPU sensor rendering
- tray app or background daemon
- external third-party driver plugins
- packaged installers
- remote browser control

## Implementation Notes

Keep existing CLI behavior working. The GUI should reuse the same image conversion and ASUS protocol modules through the new driver interface. Existing tests should continue to pass while GUI-specific tests are added.
