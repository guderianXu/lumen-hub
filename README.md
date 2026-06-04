# 光枢 / Lumen Hub

Linux 下的屏幕、灯效、风扇与硬件联动控制中心。

当前项目包含：

- ASUS TUF GAMING LC III 360 ARGB LCD 小屏内容上传、监控画面和动画播放。
- OpenRGB 主板、内存、风扇、ARGB 灯带控制。
- PWM 风扇监控、权限请求、曲线策略和压力测试入口。
- 联力 L-Wireless 设备读取、实验性风扇/灯光/小屏控制。
- 睡眠前一键关闭 LCD 屏幕与整机灯光。

英文项目名为 `Lumen Hub`，仓库/包名使用 `lumen-hub`；底层 Python 包名仍为 `usb9_lcd`，命令行入口也保持兼容。

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

安装后也可以直接运行：

```bash
lumen-hub
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

或使用安装后的脚本入口：

```bash
lumen-hub-gui
```

The desktop GUI supports a dark monitoring dashboard, local asset library, static image upload, animated asset playback for the detected ASUS LCD, OpenRGB lighting, PWM fan control, and an experimental LIAN LI wireless page. The display model and preview geometry are device-aware, so future screens can provide different sizes, shapes, pixel styles, and protocols through separate drivers.

## 安装与自启动

Linux 桌面入口和权限样例放在 `packaging/linux/`：

```bash
sudo install -Dm644 packaging/linux/lumen-hub.desktop /usr/share/applications/lumen-hub.desktop
mkdir -p ~/.config/autostart
cp packaging/linux/lumen-hub.desktop ~/.config/autostart/lumen-hub.desktop

sudo install -Dm644 packaging/linux/lumen-hub-udev.rules /etc/udev/rules.d/60-lumen-hub.rules
sudo udevadm control --reload-rules
sudo udevadm trigger

sudo install -Dm644 packaging/linux/lumen-hub-tmpfiles.conf /etc/tmpfiles.d/lumen-hub-pwm.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/lumen-hub-pwm.conf
```

`packaging/linux/lumen-hub.desktop` 调用安装后的 `lumen-hub-gui` 入口；`packaging/linux/lumen-hub-udev.rules` 覆盖 ASUS LCD、OpenRGB i2c 访问和联力无线 USB 权限；`packaging/linux/lumen-hub-tmpfiles.conf` 用于让普通用户组写入 Linux hwmon `pwm*` 风扇控制节点。修改 udev/tmpfiles 后建议重新插拔相关 USB 设备并重新打开软件。

Windows 自启动脚本放在 `packaging/windows/lumen-hub-autostart.ps1`：

```powershell
powershell -ExecutionPolicy Bypass -File packaging/windows/lumen-hub-autostart.ps1
powershell -ExecutionPolicy Bypass -File packaging/windows/lumen-hub-autostart.ps1 -Mode ScheduledTask
powershell -ExecutionPolicy Bypass -File packaging/windows/lumen-hub-autostart.ps1 -Uninstall
```

默认模式会在当前用户 Startup 文件夹创建 `Lumen Hub.lnk`，计划任务模式会注册登录时启动的 `Lumen Hub` 任务。两种模式默认执行 `lumen-hub-gui`。

The `灯效` page controls motherboard, RAM, fan, and ARGB lighting through the OpenRGB SDK Server. Start OpenRGB with its SDK server enabled before connecting from the GUI. The default endpoint is `127.0.0.1:6742`.

The `监控` page reads NVIDIA GPU telemetry through `nvidia-smi` and CPU temperature from Linux hwmon sensors. Timed telemetry refresh runs in the background so slow sensor commands do not block the GUI timer path; manual monitoring upload can still wait for telemetry collection or device writes to finish. If a sensor is unavailable, the GUI shows `不可用` and keeps running.

The `素材库` page indexes local files under `assets/user/`, generated presets under `assets/presets/`, and source links from `assets/links.json`.

Animated assets can be played to the LCD through low-rate frame refresh. This mode decodes GIF/WebP frames and sends them through the same static image protocol, so it is intentionally conservative and is not the native ASUS animation protocol.

If Linux denies access to `/dev/hidraw*`, add a udev rule similar to:

```udev
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0b05", ATTRS{idProduct}=="1c7b", MODE="0660", GROUP="plugdev"
```

PWM 风扇写入权限可以在 GUI 的 `风扇` 页诊断。Linux 下软件启动后会自动触发一次普通风扇扫描；如果系统还没有暴露 `fan*_input` 或 `pwm*`，会请求系统授权并尝试加载本机已有的主板 hwmon 驱动，包括 `nct6683 force=1`、`nct6775`、`asus_ec_sensors` 和 `it87`。如果已经发现 `pwm*` 但当前用户不可写，软件会在启动/刷新时请求系统授权执行一次临时 `chown/chmod`；也可以手动点击“授权 PWM 权限”。普通风扇页支持手动 PWM，也支持和联力页一致的 CPU 温度曲线控制：内置安静、标准、高速、全速和自定义预设；拖动点位调整温度到 PWM 百分比，启用后按设定间隔自动写入可控风扇。长期建议配置 tmpfiles/udev 权限，让 `/sys/class/hwmon/.../pwm*` 对当前用户组可写，然后执行：

```bash
sudo systemd-tmpfiles --create /etc/tmpfiles.d/lumen-hub-pwm.conf
```

OpenRGB 灯效页默认保持关闭；连接 OpenRGB SDK Server 后才会应用灯效。联力无线页目前仍是实验性功能，默认只读，写入必须启用复选框并输入确认令牌。

## 开发验证

```bash
QT_QPA_PLATFORM=offscreen pytest -q
```

联力无线的 Windows USBPcap 抓包执行手册见：`docs/lianli-wireless-windows-capture-playbook.md`，可直接用于 Windows 端按场景抓 baseline / PWM / sync / 灯光 / 重绑的 pcap 文件。

GitHub Actions 配置在 `.github/workflows/tests.yml`，会在 push 和 pull request 时安装依赖并运行完整测试。

On this machine the installed rule is:

```udev
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0b05", ATTRS{idProduct}=="1c7b", MODE="0660", GROUP="plugdev", TAG+="uaccess"
```

## LIAN LI reverse workflow (cross-platform probe entrypoint)

If you are working on LIAN LI reverse packets and Linux-side matching, use the project-local probe wrapper to avoid `usb9_lcd` import/path issues:

```powershell
# PowerShell / Windows
cd .\scripts
.\lianli-wireless-probe.ps1 scan
.\lianli-wireless-probe.ps1 usb-capture-readiness
.\lianli-wireless-probe.ps1 validate-readonly --output-dir .cache\lianli\validation-live
```

```bash
# Linux / bash
cd ./scripts
./lianli-wireless-probe.sh scan
./lianli-wireless-probe.sh usb-capture-readiness
./lianli-wireless-probe.sh validate-readonly --output-dir .cache/lianli/validation-live
```

For Windows USBPcap capture generation, continue with:

```powershell
cd ..\lumen-hub
.\scripts\lianli-reverse-operator-plan.ps1 -Run
```

## Hardware Notes

- `python -m usb9_lcd detect` found the ASUS LCD at `/dev/hidraw10` and `/dev/hidraw11`.
- `python -m usb9_lcd show ./sample.png --dry-run` prepared a 460800-byte RGB565 frame.
- After installing the udev rule, the LCD re-enumerated as `/dev/hidraw0` and `/dev/hidraw1` with read/write access.
- The original placeholder packet sequence reached the device but timed out/reset it.
- Static analysis of ASUS InfoHub replaced the placeholder sequence with the observed HID2 frame packet format.
- `python -m usb9_lcd show ./sample.png` switches the LCD to custom image mode and completes a 460800-byte transfer without disconnecting the device.
