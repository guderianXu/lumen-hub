# Lian Li 官方抓包逆向闭环（v2.1.17）

> 目标：
> 1) 在 Windows 下抓取官方 L-Connect 3 的 USB 报文（7 个场景）
> 2) 把抓包交给 Linux 工具链做缺口报告与签名比对
> 3) 输出可直接驱动你当前 Linux 风扇控制软件的数据证据

## 一、Windows 准备（命令行为主）

- 安装 Wireshark + USBPcap
- 插入联力无线主机与接收器 USB，优先识别 VID/PID：`0416:8040`, `0416:8041`
- 先运行 Windows preflight，确认 Wireshark/Npcap/USBPcap、联力 USB 设备、runbook 生成和可选 live-list 状态：

```powershell
cd E:\风扇控制\lumen-hub
.\scripts\lianli-windows-preflight.ps1
```

preflight 报告会写入 `.cache\lianli\windows-preflight\windows-preflight.json`。如果 `usbpcap.status` 是 `installed-needs-reboot-or-replug`，说明除 USBPcap 抓包外的 Windows 准备可继续，但正式 USB 抓包需要先重启 Windows 或重新枚举 USB 控制器。

- 可选：先确认可见设备
  
```powershell
cd E:\风扇控制\lumen-hub
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\lianli-wireless-probe.ps1 scan
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\lianli-wireless-probe.ps1 live-list
```

- 生成 Windows 抓包执行清单与 note：

```powershell
.\scripts\lianli-reverse-operator-plan.ps1 -Run
```

脚本会在 `.cache\lianli\windows-captures-v2.1.17\reverse-operator-commands.txt` 生成按序命令。

常用参数：

```powershell
.\scripts\lianli-reverse-operator-plan.ps1 -Run
.\scripts\lianli-reverse-operator-plan.ps1 -Run -SkipLive -TargetId "<你的 target id>"
.\scripts\lianli-reverse-operator-plan.ps1 -Run -DryRun
.\scripts\lianli-reverse-operator-plan.ps1 -Run -SkipLinux
```

说明：

- 首次 `-Run` 时如果不带 `-TargetId`，脚本会优先从 `linux-control-target-registry` 的产物读取第一个 `target id`；如果仍显示 `<target-id>`，说明还需要先完成 `windows-capture-ingest` / `linux-control-target-registry` 产物，或手工补充 `-TargetId`。

## 二、抓包场景（请按顺序记录 pcap 文件）

文件名约定（以实际文件名命名）：

- `l-connect-v2.1.17-00-baseline.pcapng`
- `l-connect-v2.1.17-01-direct-fan-speed.pcapng`
- `l-connect-v2.1.17-02-mb-pwm-sync.pcapng`
- `l-connect-v2.1.17-03-rf-rebind.pcapng`
- `l-connect-v2.1.17-04-sort-quick-sync.pcapng`
- `l-connect-v2.1.17-05-lighting-static-off.pcapng`
- `l-connect-v2.1.17-06-lighting-generated-rainbow.pcapng`

每个场景抓完后直接保存到上述文件名。

## 三、抓包完成后转回 Linux 做比对

```bash
cd /e/风扇控制/lumen-hub
bash scripts/lianli-wireless-probe.sh windows-capture-ingest .cache/lianli/windows-captures-v2.1.17 --capture-base l-connect-v2.1.17 --target-context-from .cache/lianli/hardware
bash scripts/lianli-wireless-probe.sh capture-set-report .cache/lianli/windows-captures-v2.1.17 --capture-base l-connect-v2.1.17
bash scripts/lianli-wireless-probe.sh capture-gap-report .cache/lianli/windows-captures-v2.1.17 --capture-base l-connect-v2.1.17
bash scripts/lianli-wireless-probe.sh capture-triage-report .cache/lianli/windows-captures-v2.1.17 --capture-base l-connect-v2.1.17
bash scripts/lianli-wireless-probe.sh capture-signature-match .cache/lianli/windows-captures-v2.1.17 --version 2.1.17
bash scripts/lianli-wireless-probe.sh lianli-validation-gate --capture-dir .cache/lianli/windows-captures-v2.1.17 --hardware-dir .cache/lianli/hardware --capture-base l-connect-v2.1.17 --artifact-dir .cache/lianli
bash scripts/lianli-wireless-probe.sh linux-control-target-registry .cache/lianli/windows-captures-v2.1.17 --capture-base l-connect-v2.1.17
```

### 指定场景做包级对齐（已有对应 pcap 才会命中）

```bash
bash scripts/lianli-wireless-probe.sh linux-control-packet-compare .cache/lianli/windows-captures-v2.1.17 .cache/lianli/windows-captures-v2.1.17/l-connect-v2.1.17-01-direct-fan-speed.pcapng --capture-base l-connect-v2.1.17 live-pwm --target-id <target-id> --pwm-values 66,55,44,33
```

## 四、故障排查

- 仍抓不到 8040/8041：先确认 USBPcap 正在抓的是对应 USB 总线；Linux 环境可先在 Windows 侧确认扫描到设备再抓。
- 比对报错：检查 pcap 文件名是否按上述约定命名、是否放在 `windows-captures-v2.1.17`。
- 若比对报告未显示 evidence：优先补抓 `baseline` 与 `direct-fan-speed`。

## 五、我在本地已做的修改

- 已修复 `usb9_lcd/lianli/wireless.py` 的 Windows libusb 后端自动发现与权限拒绝提示。
- 已增加 `scripts/lianli-reverse-operator-plan.ps1` 的闭环执行能力（现为双模式：预览/执行）。

