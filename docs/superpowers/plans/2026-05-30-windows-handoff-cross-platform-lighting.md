# Windows 切换交接：跨平台灯效与平台适配

> 记录时间：2026-05-30  
> 当前分支：`main`  
> 当前已推送提交：`092b586 Keep software lighting effects continuous`  
> 目的：切换到 Windows 前，把已完成内容、关键文件、验证命令和下一步任务写入文件，便于在 Windows 继续接上。

## 当前状态

- [x] 本地工作区已清理，最新代码已推送到 GitHub `main`。
- [x] OpenRGB 静态模式、设备画像、灯效计划、测试窗口、硬件操作队列、Linux/Windows 平台路径、软件灯效兜底都已完成基础实现。
- [x] 最近一次重点修复：新增的软件灯效不再“亮一下就灭”，现在流星/彗星/扫描/遮罩/星空/矩阵都有持续底光或多段运动。
- [ ] Windows 端还没有实机跑 GUI 验证，需要切到 Windows 后继续测试 OpenRGB、路径检测、软件灯效连续性和联力无线。

## 已完成内容

### OpenRGB 设备画像与配置迁移

- [x] 新增 OpenRGB 设备画像，用于针对不同主板/控制器调整写入策略。
- [x] 已为 ASUS ROG STRIX B850-A 类设备增加静态稳定策略：整设备 + 所有 zone 重复写入。
- [x] 配置版本已迁移到 `CONFIG_VERSION = 2`，并新增 `lighting.device_profiles` 缓存。
- [x] 旧配置加载时自动补齐 `device_profiles`。

关键文件：

- `usb9_lcd/lighting/profiles.py`
- `usb9_lcd/gui/settings.py`
- `tests/test_settings.py`

### 灯效应用引擎拆分

- [x] 新增 `LightingApplyPlan`，把 OpenRGB 设备策略从 GUI/驱动直接逻辑里拆出来。
- [x] 支持设备 profile 影响 mode alias、静态 zone size、重复写入次数。
- [x] 追逐模式已优先选择干净的 `Chase`，避免错误落到 `Chase Fade`。

关键文件：

- `usb9_lcd/lighting/engine.py`
- `usb9_lcd/lighting/openrgb.py`
- `tests/test_lighting.py`

### OpenRGB 测试窗口

- [x] GUI 灯效页增加 `测试窗口`。
- [x] 测试窗口支持刷新画像、稳定静态红、只亮选中目标、全部关闭。
- [x] 测试窗口显示设备 profile 信息，便于确认当前设备匹配到了什么策略。

关键文件：

- `usb9_lcd/gui/lighting_page.py`
- `tests/test_gui_import.py`

### 硬件操作串行队列

- [x] GUI OpenRGB 操作从“忙时拒绝”改成“忙时排队”。
- [x] 连续点击应用、测试、关闭时会按顺序执行，避免多线程抢写 OpenRGB。
- [x] 队列状态会显示等待数量。

关键文件：

- `usb9_lcd/gui/operation_queue.py`
- `usb9_lcd/gui/lighting_page.py`
- `tests/test_gui_import.py`

### Linux / Windows 平台适配层

- [x] 新增 `usb9_lcd/platforms/`。
- [x] Linux 使用 XDG 配置、缓存、日志目录，并检测 hidraw、OpenRGB udev、hwmon、nvidia-smi。
- [x] Windows 使用 `APPDATA` / `LOCALAPPDATA`，并检测 OpenRGB 常见安装路径、管理员状态和驱动提示。
- [x] GUI 顶栏新增 `平台诊断`。
- [x] 配置、日志、GIF 缓存、keepalive、OpenRGB server log 已改为平台路径。
- [x] 保留旧 Linux 配置兼容：如果存在 `~/.config/usb9-lcd/settings.json` 且新配置不存在，会继续读取旧配置。

关键文件：

- `usb9_lcd/platforms/base.py`
- `usb9_lcd/platforms/linux.py`
- `usb9_lcd/platforms/windows.py`
- `usb9_lcd/gui/platform_diagnostics.py`
- `usb9_lcd/gui/main_window.py`
- `usb9_lcd/gui/debug.py`
- `usb9_lcd/gui/gif_preview.py`
- `usb9_lcd/keepalive.py`
- `usb9_lcd/lighting/server.py`
- `tests/test_platforms.py`

### 新增软件渲染灯效

- [x] 对 OpenRGB 没有原生模式的效果，新增软件逐灯帧兜底。
- [x] 支持：`星空`、`流星`、`彗星`、`扫描`、`遮罩`、`矩阵`、`渐变`。
- [x] 如果设备有原生 OpenRGB 模式，优先使用原生模式。
- [x] 如果没有原生模式，自动切 `Direct/custom`，并由软件线程持续写帧。
- [x] 切换其他灯效或断开时，会停止旧的软件灯效线程。
- [x] 最新修复：新增灯效改为连续型，避免单个分区/风扇“亮一会就熄灭”。

关键文件：

- `usb9_lcd/lighting/software_effects.py`
- `usb9_lcd/lighting/openrgb.py`
- `usb9_lcd/lighting/effects.py`
- `usb9_lcd/lighting/engine.py`
- `tests/test_lighting.py`

## 最近提交记录

- `092b586 Keep software lighting effects continuous`
- `7e9dd90 Reduce meteor effect blackout gap`
- `57ca7db Add software-rendered OpenRGB effects`
- `130301d Add Linux and Windows platform diagnostics`
- `79e8092 Serialize OpenRGB GUI hardware operations`
- `d50776d Add OpenRGB lighting test window`
- `e0ad9b4 Refactor OpenRGB lighting strategy planning`
- `3b036fd Add OpenRGB device profiles and settings migration`

## Windows 端接手步骤

### 1. 拉取代码

```powershell
git clone https://github.com/guderianXu/lumen-hub.git
cd lumen-hub
# 如果已经 clone 过：
git pull origin main
```

### 2. 安装依赖

```powershell
python -m pip install -e ".[dev]"
```

如需联力无线相关实验工具：

```powershell
python -m pip install -e ".[dev,lianli]"
```

### 3. 跑基础测试

```powershell
python -m pytest tests/test_lighting.py -q
python -m pytest tests/test_platforms.py tests/test_settings.py -q
```

GUI import 级别回归可跑：

```powershell
$env:QT_QPA_PLATFORM="offscreen"
python -m pytest tests/test_gui_import.py::test_lighting_page_exposes_expanded_openrgb_effects tests/test_gui_import.py::test_main_window_opens_platform_diagnostics_window -q
Remove-Item Env:QT_QPA_PLATFORM
```

### 4. 启动 GUI

```powershell
python -m usb9_lcd.gui.app
```

或安装 entrypoint 后：

```powershell
lumen-hub-gui
```

### 5. Windows GUI 重点验证

- [ ] 打开 GUI 顶栏 `平台诊断`。
- [ ] 确认配置路径在 `%APPDATA%\LumenHub\settings.json`。
- [ ] 确认日志路径在 `%LOCALAPPDATA%\LumenHub\Logs`。
- [ ] 确认 OpenRGB 路径能自动找到，或在设置页手动填入 `OpenRGB.exe`。
- [ ] 启动 OpenRGB，开启 SDK Server，默认端口 `127.0.0.1:6742`。
- [ ] GUI 灯效页点击连接 OpenRGB，确认能发现主板、内存、风扇、ARGB 区域。
- [ ] 用 `测试窗口` 先跑 `稳定静态红`，确认 CPU 风扇/ARGB 是否稳定亮。
- [ ] 测试静态、彩虹、追逐、星空、流星、彗星、扫描、遮罩、矩阵、渐变。
- [ ] 重点观察新增软件灯效是否仍有“局部风扇长时间变黑”。如果有，记录具体设备、zone、效果、速度、亮度。

## Windows 端仍要做的事

### OpenRGB / 灯效

- [ ] 在 Windows 实机确认 OpenRGB Python SDK 的 `set_colors(..., fast=True)` 对主板和风扇是否稳定。
- [ ] 如果 Windows 下软件灯效写帧太快导致 OpenRGB 卡顿，调高 `software_effect_interval_seconds()` 的最小间隔。
- [ ] 如果仍感觉亮度不连续，给 GUI 增加 `底光强度` / `连续度` 滑块，而不是继续硬编码。
- [ ] 检查 ASUS 主板在 Windows 下的 zone 数和 Linux 是否一致。
- [ ] 检查 CPU 风扇在 Windows 下是否仍需要 whole-device static 策略。

### 平台适配

- [ ] 验证 `WindowsPlatformAdapter.openrgb_candidate_paths()` 是否能覆盖你的 OpenRGB 实际安装位置。
- [ ] 验证 `平台诊断` 对管理员状态、OpenRGB SDK、路径显示是否准确。
- [ ] 后续可以增加 Windows 一键打开日志目录/配置目录按钮。

### 联力无线

- [ ] Windows 端继续验证联力无线直写路径。
- [ ] 写入前停止 L-Connect 相关服务/进程，避免官方软件抢占设备。
- [ ] 已有 Windows 抓包和联力文档可参考：
  - `docs/lianli-wireless-windows-capture-playbook.md`
  - `docs/lianli/wireless-static-rgb-notes.md`
  - `scripts/lianli-reverse-operator-plan.ps1`
- [ ] Windows 接收器读取仍可能不可靠，当前更可信的是 sender direct write 路径。

### 打包

- [ ] Windows PyInstaller 打包尚未做。
- [ ] Linux AppImage / deb 打包尚未做。
- [ ] 目前先验证功能，不建议先做安装包。

## 已验证命令

最近 Linux 端已跑过：

```bash
python -m py_compile usb9_lcd/lighting/software_effects.py
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_lighting.py -q
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_lighting.py \
  tests/test_gui_import.py::test_lighting_page_exposes_expanded_openrgb_effects \
  tests/test_gui_import.py::test_lighting_page_connects_to_openrgb_and_applies_settings \
  tests/test_gui_import.py::test_lighting_page_static_default_syncs_all_openrgb_devices \
  tests/test_gui_import.py::test_lighting_page_openrgb_test_window_runs_static_probe -q
```

最新结果：

- `tests/test_lighting.py`: `18 passed`
- OpenRGB/GUI 相关回归：`22 passed`

## 注意事项

- 不要把 GitHub token 写入 remote、配置文件或文档。
- 当前 remote 是普通 HTTPS URL，不包含 token。
- 如果要在 Windows 推送，建议使用 Git Credential Manager 或重新生成 token 后只放在凭据管理器里。
- 切换 Windows 后，优先做实机验证，不要先大改架构。
