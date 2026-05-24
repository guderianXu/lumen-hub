# 光枢

Linux 下的屏幕、灯效、风扇与硬件联动控制中心。

当前项目包含：

- ASUS TUF GAMING LC III 360 ARGB LCD 小屏内容上传、监控画面和动画播放。
- OpenRGB 主板、内存、风扇、ARGB 灯带控制。
- PWM 风扇监控、权限请求、曲线策略和压力测试入口。
- 联力 L-Wireless 设备读取、实验性风扇/灯光/小屏控制。
- 睡眠前一键关闭 LCD 屏幕与整机灯光。

底层 Python 包名仍为 `usb9_lcd`，命令行入口也保持兼容。

## 目标硬件

Detected target:

- USB ID: `0b05:1c7b`
- Product: `ASUSTek Computer, Inc. TUF GAMING LC III 360 ARGB LCD`

## 快速开始

```bash
python -m pip install -e '.[dev]'
python -m usb9_lcd detect
python -m usb9_lcd show ./image.png
python -m usb9_lcd.gui.app
```

## Static Image

Prepare a frame without touching the hardware:

```bash
python -m usb9_lcd show ./image.png --dry-run
```

Transfer a static image frame to the LCD:

```bash
python -m usb9_lcd show ./image.png
```

Common image options:

```bash
python -m usb9_lcd show ./image.png --fit contain --background '#000000'
python -m usb9_lcd show ./image.png --fit stretch
python -m usb9_lcd show ./image.png --rotate 90
```

Fit modes:

- `cover`: fill the screen and crop overflow.
- `contain`: preserve aspect ratio and fill empty space with `--background`.
- `stretch`: fill the screen without preserving aspect ratio.

## Desktop GUI

Install GUI dependencies:

```bash
python -m pip install -e '.[dev]'
```

Launch the desktop GUI:

```bash
python -m usb9_lcd.gui.app
```

The desktop GUI supports a dark monitoring dashboard, local asset library, static image upload, and animated asset playback for the detected ASUS LCD. The display model and preview geometry are device-aware, so future screens can provide different sizes, shapes, pixel styles, and protocols through separate drivers.

The `灯效` page controls motherboard, RAM, fan, and ARGB lighting through the OpenRGB SDK Server. Start OpenRGB with its SDK server enabled before connecting from the GUI. The default endpoint is `127.0.0.1:6742`.

The `监控` page reads NVIDIA GPU telemetry through `nvidia-smi` and CPU temperature from Linux hwmon sensors. Timed telemetry refresh runs in the background so slow sensor commands do not block the GUI timer path; manual monitoring upload can still wait for telemetry collection or device writes to finish. If a sensor is unavailable, the GUI shows `不可用` and keeps running.

The `素材库` page indexes local files under `assets/user/`, generated presets under `assets/presets/`, and source links from `assets/links.json`.

Animated assets can be played to the LCD through low-rate frame refresh. This mode decodes GIF/WebP frames and sends them through the same static image protocol, so it is intentionally conservative and is not the native ASUS animation protocol.

If Linux denies access to `/dev/hidraw*`, add a udev rule similar to:

```udev
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0b05", ATTRS{idProduct}=="1c7b", MODE="0660", GROUP="plugdev"
```

On this machine the installed rule is:

```udev
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0b05", ATTRS{idProduct}=="1c7b", MODE="0660", GROUP="plugdev", TAG+="uaccess"
```

## Hardware Notes

- `python -m usb9_lcd detect` found the ASUS LCD at `/dev/hidraw10` and `/dev/hidraw11`.
- `python -m usb9_lcd show ./sample.png --dry-run` prepared a 460800-byte RGB565 frame.
- After installing the udev rule, the LCD re-enumerated as `/dev/hidraw0` and `/dev/hidraw1` with read/write access.
- The original placeholder packet sequence reached the device but timed out/reset it.
- Static analysis of ASUS InfoHub replaced the placeholder sequence with the observed HID2 frame packet format.
- `python -m usb9_lcd show ./sample.png` switches the LCD to custom image mode and completes a 460800-byte transfer without disconnecting the device.
