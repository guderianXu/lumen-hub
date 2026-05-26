# 联力 L-Wireless 接收器实机验证计划

> **For agentic workers:** 这份文件是接收器到货后的执行记录入口。先做只读证据采集，再根据 write-gate 结果决定是否进入单 MAC 安全写入实验。

## 当前状态

- 已完成 L-Connect 3 多版本静态分析、官方 changelog 排名、JS 资源 HID/无线线索提取。
- 已有 CLI 逆向工具：`tools/lianli_wireless_probe.py`。
- 已有只读 PyUSB 路径：`scan`、`live-list`、`live-master`、`live-lcd-info`、`validate-readonly`。
- 已有接收器插上后的整包验证入口：`receiver-validation-bundle`。
- `receiver-validation-bundle` 会保存自身 JSON、`summary.json`，并把 `hardware_validation` / `receiver_control_next_action` 提升到 stdout 顶层。
- 已有 `receiver-evidence-report`，用于审计实机日志目录、列出必需证据文件、每个 JSON 的 SHA256，以及推荐/已存在的安全 PWM 实验目录。
- `receiver-evidence-report` 会交叉检查 `live-list.json`、`readonly/live-list.json`、`live-master.json` 的 receiver MAC、Master MAC、channel、rx_type、device_type、fan_count，避免混用不同接收器或旧日志。
- `receiver-evidence-report` 也会检查安全写入实验内部的 `live-*.json`、before/after 快照和 `analyze-live-*.json` 是否指向同一个目标并匹配预期效果；目前覆盖直写 PWM、主板 PWM sync、主板 PWM mirror、静态 RGB、彩虹 RGB、绑定和解绑。
- 已有 `receiver-observation`，用于把安全 PWM 后肉眼/听感确认的风扇变化，或 RGB/绑定实验后的实际变化，保存成 `observation.json`。
- `summarize-experiments` 已能识别 `receiver-validation-bundle`，并汇总 write-gate 是否已准备好。
- `summarize-experiments` 已能输出 `receiver_control_next_action`，直接给出是否允许单目标安全 PWM、候选 MAC 和保守命令；如果 receiver 身份证据冲突或不完整，会先要求重新采集整包验证日志。
- `receiver_control_next_action` 会在安全 PWM 机器日志完整但缺少观察时要求先补 `receiver-observation`；只有 PWM 已经视觉/听感确认后，才会推荐下一步安全 RGB / rainbow 灯光实验。PWM 和两类灯光都确认后，状态会进入 bind/unbind 风险复核，而不会把配对命令作为首选推荐。
- 已有 `receiver-pairing-risk-report`，用于在不写入 USB 的情况下审计 bind/unbind 前置证据、阻塞项、延后命令和人工复核状态。
- 已有 `capture-gap-report`，用于把 Windows USBPcap 抓包目录压缩成“下一份该抓什么”的缺口报告，避免直接从完整 `capture-set-report` 里人工筛选。
- GUI 的“汇总实验”会显示同一个下一步结论，并且只在 `receiver_control_next_action` 真正允许安全 PWM 时自动填入唯一候选 MAC。
- 已有抓包驱动的安全写入门禁：`linux-control-write-gate`。
- GUI 联力页已接入写入门禁；真实主窗口默认要求 write-gate 通过后才解锁写入。
- 目前仍没有真实 L-Wireless 接收器/风扇硬件验证结果，因此不能把 Linux 写入控制判定为完成。

## 已完成

- [x] 官方软件静态线索提取和多版本对比。
- [x] Linux 只读枚举、Master 查询、LCD 信息读取入口。
- [x] Windows USBPcap 抓包分析、packet preview/compare、写入门禁。
- [x] GUI 联力页接入只读验证和写入门禁。
- [x] 新增 `receiver-validation-bundle`，用于插上接收器后一次性保存只读、preflight、write-gate 证据。
- [x] `receiver-validation-bundle` 会在同一目录写出 `receiver-validation-bundle.json` 和 `summary.json`。
- [x] 新增 `receiver-evidence-report`，用于把实机验证目录整理成可分享的证据清单。
- [x] `receiver-evidence-report` 会跟随下一步推荐命令里的安全 PWM 输出目录，不再只检查固定 `safe-pwm-001`。
- [x] `receiver-evidence-report` 会输出 `receiver_identity_consistency`，当两份只读快照的 receiver 集合/身份字段不一致，或两份 Master 查询互相矛盾时标记 `receiver-identity-conflict`。
- [x] `receiver-evidence-report` 会输出每个写入证据的 `machine_consistency`，机器日志内部冲突时标记 `write-evidence-machine-conflict`。
- [x] `receiver-evidence-report` 已能识别 `safe-pwm-experiment`、`safe-sync-experiment`、`safe-pwm-mirror-experiment`、`safe-rgb-experiment`、`safe-rainbow-experiment`、`safe-bind-experiment` 和 `safe-unbind-experiment` 的不同写入/分析文件。
- [x] 新增 `receiver-observation`，用于把实际风扇变化记录进证据目录；`receiver-evidence-report` 会区分只收集机器日志和已经肉眼确认。
- [x] 新增 bundle 复盘摘要，`summarize-experiments` 会显示 `receiver_validation_bundles` 和 `hardware_validation.status`。
- [x] 新增接收器控制下一步摘要，`summarize-experiments` 会显示 `receiver_control_next_action`。
- [x] `receiver_control_next_action` 会在已确认 PWM 后给出下一阶段安全灯光实验建议；RGB 和 rainbow 都确认后进入 `ready-for-pairing-risk-review`，并把 bind/unbind 保留为延后验证命令。
- [x] 新增 `receiver-pairing-risk-report`，用于在执行任何 bind/unbind 前输出可分享的风险复核 JSON。
- [x] 新增 `capture-gap-report`，用于把缺失/部分 Windows USBPcap 场景按 baseline、PWM、灯光、sort/quick-sync、RF rebind 顺序排序，并给出下一份 pcap 的后处理命令。
- [x] GUI 汇总实验会显示 `receiver_control_next_action` 的中文结论，并只在身份一致、写入门禁通过时自动填入唯一可用 MAC。

## 待完成

- [ ] 插上真实 L-Wireless 接收器后保存整包验证日志。
- [ ] 确认真实 receiver MAC、Master MAC、channel、rx_type、fan_count。
- [ ] 根据真实 `write-gate.json` 判断是否允许安全写入。
- [ ] 只对一个明确 MAC 做最小 PWM 写入实验。
- [ ] 对比写入前后 snapshot、写入日志和肉眼观察到的风扇反馈。
- [ ] 在有足够证据后再扩展 RGB / rainbow / bind / unbind。

## 装上接收器后先做什么

1. 只插接收器，先不要直接做写入实验。
2. 运行 GUI：
   ```bash
   python -m usb9_lcd.gui.app
   ```
3. 在 GUI 的“联力无线”页依次点：
   - 扫描 USB
   - 读取接收器
   - 读取 Master
   - 只读验证
   - 写入门禁
4. 同时保留 CLI 证据，优先运行整包验证命令：
   ```bash
   python tools/lianli_wireless_probe.py \
     --save-json .cache/lianli/hardware/receiver-validation-bundle.json \
     receiver-validation-bundle \
     --output-dir .cache/lianli/hardware \
     --capture-dir .cache/lianli
   ```
5. 立刻复盘整包结果：
   ```bash
   python tools/lianli_wireless_probe.py summarize-experiments .cache/lianli/hardware
   ```
   如果只看上一步 `receiver-validation-bundle` 的 stdout，也可以直接看同名字段。
   重点看：
   - `receiver_validation_bundles[0].status`
   - `hardware_validation.status`
   - `hardware_validation.write_gate_ready_count`
   - `receiver_control_next_action.status`
   - `receiver_control_next_action.candidates`
   - `receiver_control_next_action.recommended_commands`
   - `receiver_identity_consistency.status`
6. 生成一份可回传分析的证据清单：
   ```bash
   python tools/lianli_wireless_probe.py \
     --save-json .cache/lianli/hardware/receiver-evidence-report.json \
     receiver-evidence-report .cache/lianli/hardware
   ```
7. 也可以在 GUI 里点“汇总实验”，选择 `.cache/lianli/hardware`，看实验流程里的“下一步”提示；如果只有一个可用 MAC，GUI 会自动填入目标 MAC。
8. 如果需要分步排查，再运行底层命令：
   ```bash
   mkdir -p .cache/lianli/hardware
   python tools/lianli_wireless_probe.py --save-json .cache/lianli/hardware/scan.json scan
   python tools/lianli_wireless_probe.py --save-json .cache/lianli/hardware/readiness.json usb-capture-readiness
   python tools/lianli_wireless_probe.py --save-json .cache/lianli/hardware/live-list.json live-list
   python tools/lianli_wireless_probe.py --save-json .cache/lianli/hardware/live-master.json live-master
   python tools/lianli_wireless_probe.py --save-json .cache/lianli/hardware/validate-readonly.json validate-readonly --output-dir .cache/lianli/hardware/readonly
   ```
9. 如果权限不足，先运行：
   ```bash
   python tools/lianli_wireless_probe.py udev-rules
   ```
   按输出安装规则后重新插拔接收器，再重复只读验证。

## 写入实验前必须满足

- `live-list` 能稳定读到接收器和目标风扇 receiver。
- 目标 MAC 已明确，不能对未知设备写入。
- `receiver_identity_consistency.status` 必须是 `consistent`；如果出现 `receiver-identity-conflict`，先重新采集整包验证日志。
- `linux-control-write-gate` 必须显示 `status = write-enabled`。
- GUI 的“写入门禁”状态必须显示已通过。
- `WRITE-LIANLI` token 只能用于单 MAC 的安全实验，不用于批量写入。

门禁检查命令：

```bash
python tools/lianli_wireless_probe.py --save-json .cache/lianli/hardware/write-gate.json \
  linux-control-write-gate .cache/lianli \
  --experiment-dir .cache/lianli/hardware/experiments
```

如果门禁不是 `write-enabled`，下一步通常是补官方 Windows USBPcap 抓包，或重新运行 packet preview/compare。

补官方抓包时先生成计划，再用缺口报告确定下一份 pcap：

```bash
python tools/lianli_wireless_probe.py windows-capture-plan \
  --version 2.1.17 \
  --capture-base lianli-v2117

python tools/lianli_wireless_probe.py \
  --save-json .cache/lianli/capture-gap-report.json \
  capture-gap-report .cache/lianli/captures \
  --capture-base lianli-v2117
```

`capture-gap-report.next_capture` 是下一份优先抓的文件；没有任何抓包时会先要求
`lianli-v2117-00-baseline.pcapng`。baseline 和 direct PWM 都有证据后，才继续
motherboard PWM sync、静态/彩虹灯光、sort/quick-sync，最后才是 RF rebind。

## 第一轮允许尝试的写入

只在 write-gate 通过后执行最小 PWM 实验。优先复制
`receiver_control_next_action.recommended_commands[0]`，它会用真实 MAC 生成
`experiments/safe-pwm-aa-bb-cc-dd-ee-ff` 这类输出目录，避免多个目标互相覆盖。
安全 PWM 机器日志齐全后，`receiver-evidence-report` 推荐的
`receiver-observation` 命令会自动带上同一个目标 MAC 和写入 PWM 值，
优先复制它来记录观察结果，避免手工把观察记到错误目标上。
手工执行时可以使用同样格式：

```bash
python tools/lianli_wireless_probe.py safe-pwm-experiment \
  --mac aa:bb:cc:dd:ee:ff \
  --pwm 120 \
  --output-dir .cache/lianli/hardware/experiments/safe-pwm-aa-bb-cc-dd-ee-ff \
  --confirm WRITE-LIANLI
```

把 `aa:bb:cc:dd:ee:ff` 换成 `live-list` 里读到的目标 receiver MAC。
如果手工改目录，后续 `receiver-evidence-report` 仍会自动发现
`experiments/` 下含有安全写入证据文件的目录；PWM、主板同步、主板镜像、
RGB、彩虹 RGB、绑定和解绑实验都会进入同一份审计报告。

实验后必须保存：

- before snapshot
- write log
- after snapshot
- analyze-live-pwm.json
- summary.json
- observation.json，记录观察到的实际风扇变化

记录观察结果：

```bash
python tools/lianli_wireless_probe.py \
  --save-json .cache/lianli/hardware/experiments/safe-pwm-aa-bb-cc-dd-ee-ff/observation.json \
  receiver-observation .cache/lianli/hardware/experiments/safe-pwm-aa-bb-cc-dd-ee-ff \
  --effect changed \
  --target aa:bb:cc:dd:ee:ff \
  --observed-pwm 120 \
  --note "fan speed visibly changed after guarded PWM write"
```

如果风扇没有变化，`--effect unchanged`；如果没看清，`--effect unclear`。
`receiver-evidence-report` 会把完整机器日志但缺观察记录的状态标成
`write-evidence-needs-observation`，只有观察记录确认变化后才会变成
`write-evidence-confirmed`。如果观察记录写的是 `unchanged`，报告会标成
`write-evidence-observation-conflict`；如果机器日志本身的 target、
before/after 快照或 `analyze-live-*.json` 互相矛盾，会标成
`write-evidence-machine-conflict`；如果观察文件格式不对或结果不明确，
会分别标成 `write-evidence-invalid-observation` 或
`write-evidence-unclear-observation`。即使 `--effect changed`，如果
`observation.json` 里的目标 MAC 或 `--observed-pwm` 与 `live-pwm.json`
不一致，也会标成 `write-evidence-observation-conflict`。这些状态都不能作为 Linux PWM 控制已验证的证据。

RGB/彩虹实验的 receiver 快照可能不会改变；如果分析日志带有
`visual_confirmation_required`，报告会把机器日志视为完整但仍需要人工观察，
不会因为 `likely_effective=false` 直接判定机器冲突。此时按报告推荐的
`receiver-observation` 命令记录灯光实际变化即可。

安全 PWM 已经有 `observation.json` 且 `receiver-evidence-report` 显示
`write-evidence-confirmed` 后，再运行：

```bash
python tools/lianli_wireless_probe.py summarize-experiments .cache/lianli/hardware
```

如果 `receiver_control_next_action.status` 变为
`ready-for-safe-lighting-validation`，只复制
`receiver_control_next_action.recommended_commands[0]` 运行一个灯光实验。
该命令会优先使用 `safe-rgb-experiment --color 0,0,0`；完成后仍然先记录
对应 `observation.json`，再考虑 rainbow。RGB 和 rainbow 都确认后，
`receiver_control_next_action.status` 会变为 `ready-for-pairing-risk-review`。
此时首选推荐仍然是重新生成 `receiver-evidence-report`；`safe_expansion_candidate`
里会列出 `deferred_pairing_commands`，但 bind/unbind 会改变接收器绑定状态，
必须等 PWM 和灯光证据都归档后再单独评估，不能作为普通下一步自动执行。

进入 `ready-for-pairing-risk-review` 后，先运行只读风险报告：

```bash
python tools/lianli_wireless_probe.py \
  --save-json .cache/lianli/hardware/receiver-pairing-risk-report.json \
  receiver-pairing-risk-report .cache/lianli/hardware
```

只有当该报告的 `status` 是 `ready-for-manual-pairing-review`，并且
`blockers` 为空时，才允许人工打开 `deferred_pairing_commands` 逐条评估。
这一步仍然不是自动执行许可；它只是证明 PWM、RGB、rainbow、身份一致性和
write evidence 都已满足配对风险复核的最低门槛。

## 还没有做

- 没有真实 L-Wireless 接收器枚举证据。
- 没有真实 receiver MAC、Master MAC、channel、rx_type、fan_count 的 Linux 读数。
- 没有真实硬件的 `live-list` 前后变化验证。
- 没有真实安全 PWM 写入成功/失败结果。
- 没有真实无线灯光 RGB / rainbow 写入验证。
- 没有真实 bind / unbind 验证。
- 没有 Lian Li 四代无线风扇接收器的完整协议覆盖。
- 没有把 Windows 官方 L-Connect 写入抓包覆盖到所有目标动作。

## 暂时不要做

- 不要跳过 write-gate 直接运行 `live-pwm`、`live-rgb`、`live-bind`、`live-unbind`。
- 不要对多个 MAC 同时写入。
- 不要在不确定 receiver 是否绑定的情况下做 bind/unbind。
- 不要在 `ready-for-pairing-risk-review` 前做 bind/unbind；即使进入该状态，也要先归档 PWM、RGB、rainbow 的 evidence report。
- 不要把静态 JS 里的 wired-controller HID 命令当作 L-Wireless RF 协议。
- 不要把 GUI 显示的“可点击”当作协议已验证；最终证据必须来自实机读写日志和官方抓包对比。

## 相关文件

- `tools/lianli_wireless_probe.py`：CLI 入口。
- `usb9_lcd/lianli/wireless.py`：Linux/PyUSB L-Wireless backend。
- `usb9_lcd/lianli/capture.py`：抓包分析、packet preview/compare、write-gate。
- `usb9_lcd/lianli/analysis.py`：实验日志分析。
- `usb9_lcd/gui/pages.py`：GUI 联力页和写入门禁 UI。
- `docs/lianli-wireless-reverse.md`：长期逆向记录。
- `.cache/lianli/hardware/`：接收器实机验证日志建议目录。

## 下一步完成标准

接收器装上后，本阶段至少需要拿到：

- `receiver-validation-bundle.json`
- `summary.json`
- `receiver-evidence-report.json`
- `receiver-pairing-risk-report.json`，仅当进入 `ready-for-pairing-risk-review` 后需要
- `scan.json`
- `readiness.json`
- `live-list.json`
- `live-master.json`
- `validate-readonly.json`
- `readonly/scan.json`
- `readonly/live-list.json`
- `readonly/live-master.json`
- `preflight.json`
- `write-gate.json`
- `summarize-experiments` 输出里 `receiver_validation_bundles[0].status`
- `summarize-experiments` 输出里 `hardware_validation.status`
- `summarize-experiments` 输出里 `receiver_control_next_action.status`

如果 write-gate 通过，再追加：

- `experiments/safe-pwm-<mac>/live-list-before.json`
- `experiments/safe-pwm-<mac>/live-pwm.json`
- `experiments/safe-pwm-<mac>/live-list-after.json`
- `experiments/safe-pwm-<mac>/analyze-live-pwm.json`
- `experiments/safe-pwm-<mac>/summary.json`
- `experiments/safe-pwm-<mac>/observation.json`
- 如果 `receiver_control_next_action.status` 进入
  `ready-for-safe-lighting-validation`，再追加
  `experiments/safe-rgb-<mac>/live-rgb.json` /
  `analyze-live-rgb.json` 和对应 `observation.json`；rainbow 同理保存
  `experiments/safe-rainbow-<mac>/live-rainbow.json` /
  `analyze-live-rainbow.json` 和对应 `observation.json`。
- 如果测试主板 PWM 联动，则同样保存
  `experiments/safe-sync-<mac>/live-pwm-sync.json` /
  `analyze-live-pwm-sync.json`，或
  `experiments/safe-pwm-mirror-<mac>/live-pwm-mirror.json` /
  `analyze-live-pwm-mirror.json`，并记录对应 `observation.json`。

只有这些证据显示目标 MAC、packet compare、写入前后状态和实际风扇反馈一致，才能把 PWM 控制从“候选可行”升级为“实机验证可行”。
