# 联力无线 Windows 抓包执行手册（L-Connect 3 v2.1.17）

本手册适用于你现在的流程：先在 Windows 抓官方 USBPcap，再回 Linux 进行 compare 与写入门禁解锁。

## 一次性准备（在 Linux）

你已接上接收器后，先在 Linux 先把抓包目录和 sidecar 搞好，减少 Windows 误差：

```bash
python tools/lianli_wireless_probe.py \
  --save-json .cache/lianli/windows-capture-runbook-v2.1.17.json \
  windows-capture-runbook .cache/lianli/windows-captures-v2.1.17 \
  --capture-base l-connect-v2.1.17 \
  --artifact-dir .cache/lianli

python tools/lianli_wireless_probe.py \
  windows-capture-note-batch .cache/lianli/windows-captures-v2.1.17 \
  --version 2.1.17 \
  --capture-base l-connect-v2.1.17 \
  --artifact-dir .cache/lianli \
  --target-context-from .cache/lianli/hardware \
  --write-files

python tools/lianli_wireless_probe.py \
  --save-json .cache/lianli/windows-capture-checklist-v2.1.17.json \
  windows-capture-checklist .cache/lianli/windows-captures-v2.1.17 \
  --capture-base l-connect-v2.1.17 \
  --artifact-dir .cache/lianli \
  --target-context-from .cache/lianli/hardware \
  --max-tasks 8

python tools/lianli_wireless_probe.py \
  --save-json .cache/lianli/windows-capture-queue-v2.1.17.json \
  windows-capture-queue .cache/lianli/windows-captures-v2.1.17 \
  --capture-base l-connect-v2.1.17 \
  --artifact-dir .cache/lianli \
  --target-context-from .cache/lianli/hardware
```

你要抓包时只需照下面“场景文件名顺序”操作，别把多个动作放进同一个 pcap。

```bash
python tools/lianli_wireless_probe.py windows-capture-package .cache/lianli/windows-captures-v2.1.17 \
  --output-dir .cache/lianli/windows-capture-handoff-v2.1.17 \
  --zip-path .cache/lianli/windows-capture-handoff-v2.1.17.zip \
  --capture-base l-connect-v2.1.17 \
  --artifact-dir .cache/lianli \
  --target-context-from .cache/lianli/hardware
```

然后把 handoff 目录或 zip 拿到 Windows。

---

## Windows 端抓包前置

- 安装并启动 Wireshark（带 USBPcap）
- USB 透传到 Windows VM（建议整机只透传接收器相关设备）
- 透传设备：
  - `0416:8040`（L-Wireless Sender，主写）
  - `0416:8041`（L-Wireless Receiver）
  - 观察到的情况下可再透传 `0416:7372` / `04fc:7393` / `1cbe:0006`
- 在抓每一段前，先开始 capture；动作完成后立即停止
- 每个 scenario 保存为独立文件：`l-connect-v2.1.17-XX-<scenario>.pcapng`

如果你用的是我生成的 handoff/README，也可直接运行：

```powershell
.\capture-assistant.ps1
# 看默认待抓任务 + 需要点的动作
.\capture-assistant.ps1 -Scenario baseline
.\capture-assistant.ps1 -Pack -RequireReady
```

---

## 需要抓的场景与文件

按顺序抓（建议）

| 场景ID | 文件名 | 风险 | 抓什么 | 必须做的 L-Connect 操作 |
|---|---|---|---|---|
| `baseline` | `l-connect-v2.1.17-00-baseline.pcapng` | 只读 | 首次发现/状态枚举 | 打开 L-Connect，进入 L-Wireless，等待设备列表刷新，记录 receiver/master/channel/type/fan_count/LED_count |
| `direct-fan-speed` | `l-connect-v2.1.17-01-direct-fan-speed.pcapng` | 写入（低） | 直写 PWM 包 | 手动风扇速度（固定档），例如 55%→75%，不动同步/灯光 |
| `mb-pwm-sync` | `l-connect-v2.1.17-02-mb-pwm-sync.pcapng` | 写入（低） | 主板同步路径 | 打开同步开关，关->开，记录 UI 的同步状态变化 |
| `rf-rebind` | `l-connect-v2.1.17-03-rf-rebind.pcapng` | 写入（高） | 解绑/重绑 | 先解绑目标接收器，等待列表刷新，再绑定回去，之后做一次手动转速 |
| `sort-quick-sync` | `l-connect-v2.1.17-04-sort-quick-sync.pcapng` | 写入（中） | 排序触发 rewrite/quick-sync | 改变风扇顺序/排序后观察是否出现 sync 或设置重写 |
| `lighting-static-off` | `l-connect-v2.1.17-05-lighting-static-off.pcapng` | 写入（中） | 静态+熄灭 | 先设明显静态色（建议红），再设关闭/黑色 |
| `lighting-generated-rainbow` | `l-connect-v2.1.17-06-lighting-generated-rainbow.pcapng` | 写入（中） | 彩虹动画 | 选择生成彩虹/色彩流效果，确认播放一次后停止 |

> `mb-pwm-sync`、`lighting-*`、`sort-quick-sync` 及 `rf-rebind` 对设备状态有影响；建议尽量不要跨场景混合操作。

---

## Windows 端每个文件通用步骤

1. 开始 USBPcap 抓包
2. 在 L-Connect 做该场景动作（只做该场景定义的最小操作）
3. 动作生效 10~20 秒后停止抓包
4. 立即保存到对应文件名
5. 回填 sidecar：

```bash
python tools/lianli_wireless_probe.py windows-capture-note-update \
  .cache/lianli/windows-captures-v2.1.17/<对应文件名>.notes.json \
  --captured-at <时间戳> \
  --operator <你的名字> \
  --observation "<看到的UI结果>" \
  --mark-actions-done
```

场景额外参数（可选）：

- `--pwm-values 77,88,99,111`
- `--fallback-pwm 90,90,90,90`
- `--motherboard-pwm 120`
- `--color 255,0,0` / `--color 0,0,0`
- `--pre-unbind-pwm ...` `--post-bind-pwm ...`
- `--frame-count 24` `--interval-ms 50` `--effect-index 1`

---

## 抓完回 Linux 后验收

```bash
python tools/lianli_wireless_probe.py windows-capture-ingest .cache/lianli/windows-captures-v2.1.17 \
  --capture-base l-connect-v2.1.17 \
  --artifact-dir .cache/lianli \
  --target-context-from .cache/lianli/hardware

python tools/lianli_wireless_probe.py capture-gap-report .cache/lianli/windows-captures-v2.1.17 \
  --capture-base l-connect-v2.1.17 \
  --artifact-dir .cache/lianli

python tools/lianli_wireless_probe.py lianli-validation-gate \
  --capture-dir .cache/lianli/windows-captures-v2.1.17 \
  --hardware-dir .cache/lianli/hardware \
  --capture-base l-connect-v2.1.17 \
  --artifact-dir .cache/lianli
```

重点看每个场景是否在 `capture-gap-report` 中变成 `evidence-found`。

---

## 常见问题

- 抓不到 0416:8040/8041：说明没透传到 VM 或没选到 USBPcap 设备。
- 一个 pcap 混了多个动作：后续会被视为不清晰，后续场景会卡。
- sidecar 还没填 `mark-actions-done`：下一个阶段比较命令不会自动接上。
- `failed to create` 这类报错，多半是 sidecar 已有同名文件但 Windows 端没写权限或文件被占用，删掉重跑同名场景文件即可。

---

## 备注

- 当前版本计划默认抓包基线是 `l-connect-v2.1.17`。
- 每个抓包文件名必须保持固定前缀，不要改。
