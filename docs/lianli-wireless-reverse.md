# Lian Li Wireless Reverse Notes

## Current Findings

- Official L-Connect 3 v2.1.20 was downloaded from LIAN LI's CDN as
  `20260422-L-Connect 3-x64-v2.1.20-fde9a570.zip`.
- The ZIP contains a single NSIS installer executable:
  `20260422-L-Connect 3-x64-v2.1.20-fde9a570.exe`.
- Local artifact hashes:
  - ZIP SHA256:
    `ca62bcb06b599f5428deb11b944e419d9f0a37855dba02e3600fe9443ff2e0c9`
  - EXE SHA256:
    `92305cb805d3a9ddac5ff7d78d8426bc66ba2a3bc28be71be0e26a660579b60f`
- The official L-Connect 3 page now lists L3 v2.1.23. Local refresh downloaded
  `20260522-L-Connect 3-x64-v2.1.23-5b4679ee.zip`.
- The installer is signed/described as `L-Connect 3 Installer` by
  `LIAN LI INDUSTRIAL CO.,LTD`.
- Static extraction with stock 7-Zip exposes the NSIS PE and one large solid
  payload block, but not the final installed app tree yet.
- `analyze-artifact` and `analyze-artifact-tree` now scan installer/binary
  artifacts for repeatable static USB/protocol clues:
  - `python tools/lianli_wireless_probe.py analyze-artifact ".cache/lianli/extract/20260422-L-Connect 3-x64-v2.1.20-fde9a570.exe"`
  - `python tools/lianli_wireless_probe.py analyze-artifact-tree .cache/lianli/nsis --max-file-size 2000000000`
  - The installer PE has high-confidence UTF-16 `L-Connect 3 Installer` and
    LIAN LI company metadata, matching the PE version resource.
  - The large `[0]` block is now identified as an NSIS payload with
    `DEADBEEF NullsoftInst`, flags `112`, header size `102848`, data size
    `1318682558`, size delta `2`, and entropy `7.9999`, so it is effectively
    compressed/encrypted for naive string scanning. The standard NSIS flag mask
    only covers the low four bits; `[0]` has unsupported flags `0x70`, and a
    temporary copy with those flags cleared still does not open as NSIS in
    7-Zip.
  - Direct decompression probes at offsets `28`, `32`, `36`, `102848`, and
    `102876` tried zlib, raw deflate, bzip2, xz, LZMA-alone, and NSIS raw LZMA.
    None produced output, while a synthetic NSIS raw-LZMA payload is decoded by
    the same probe. Treat the payload as modified, encrypted, or otherwise not
    directly decompressible with stock NSIS/LZMA assumptions.
  - The NSIS tree scan covers 24 extracted PE/resource/payload files. Only
    three files contain static hits: `.rsrc/version.txt`, `CERTIFICATE`, and
    `[0]`.
  - `[0]` contains only isolated raw little-endian 4-byte VID/PID hits for
    `0416:7372`, `04fc:7393`, and `1cbe:0006`; the scanner flags these as
    medium-confidence and warns they may be accidental inside high-entropy
    compressed data.
  - No high-confidence `0416:8040`, `0416:8041`, `slv3tuzx`, `L-Wireless`, or
    full textual VID/PID strings were found in the currently extracted NSIS
    payload. Deeper extraction/decompression or a newer installer remains
    necessary before treating the official artifact as protocol evidence.

## Official L-Connect 3 v2.1.23 Refresh

The v2.1.23 ZIP is a useful update because LIAN LI split the huge asset bundle
out of the installer executable:

- ZIP SHA256:
  `e2f756ba9f30663705372c4acad9559b9e5d0eb4d854cb4449e6efeba70d108b`
- ZIP contents:
  - `20260522-L-Connect 3-x64-v2.1.23-5b4679ee.exe`
    - size `345832392`
    - SHA256 `20b7050c8b2977c6e7222ba039c1dc9f9ed307f7272ce6b0fbc0e91e709bccbb`
  - `Assets.zip`
    - size `1302351531`
    - SHA256 `bd4b4027a7b731ff4c6a0636841a993b64309f152b2985e6fd5b440892687e6d`
- Extracted `Assets.zip` has 755 files. Important top-level asset families:
  `animation`, `ga2v`, `hydroshift-ii-lcd-c`,
  `hydroshift-ii-lcd-s`, `lancool207`, `slv3`, `tl`,
  `tl-sensor`, `universal-screen-8.8-inch`, and `wireless-sensor`.

v2.1.23 installer / NSIS findings:

- The EXE scan finds only product/version metadata:
  `L-Connect 3` and `LIAN LI`.
- The extracted NSIS tree still has one `[0]` payload:
  - SHA256 `4d5a416ae2f52d8622d19a3346ccc2db994cd7591804d5671a33ef3576f15660`
  - NSIS signature `DEADBEEF NullsoftInst`
  - flags `112` / unsupported flags `0x70`
  - header size `50060`
  - data size `345462353`
  - size delta `7`
  - entropy `7.9999`
- Direct decompression probes at offsets `28`, `32`, `36`, `50060`, and
  `50088` again do not produce useful plaintext. One raw-deflate attempt at
  offset `28` reports success with zero output, so it is not actionable.
- No USB ID, `slv3tuzx`, `L-Wireless`, or protocol string was found inside
  v2.1.23 `[0]`.

v2.1.23 asset findings:

- Enhanced `analyze-artifact-tree` now scans both file contents and path names:
  - `file_count`: 755
  - content-matched files: 38
  - path-matched files: 179
  - summary categories:
    - `hid-code`: 357
    - `hid-command`: 272
    - `asset`: 202
    - `asset-model`: 102
    - `usb-id`: 91
- The strongest code clue is in 17 duplicated front-end animation bundles,
  for example
  `assets/animation/assets/index.326b5cf7.js`.
- Those bundles contain official HID control code for AL V2 and SL V2 fan
  controllers. This is not the L-Wireless RF protocol, but it is directly
  useful for future Linux support for wired LIAN LI controllers:
  - AL V2 loads HID devices with product ID `41220` (`0xa104`).
  - SL V2 loads HID devices with product IDs `41219` (`0xa103`) and
    `41221` (`0xa105`).
  - RPM polling writes `[[224,80,0,0,0,0,0,0]]` and then reads input report
    `(224, 65)`.
  - Version polling writes `[224,80,1,0,0,0,0,0]`.
  - Direct per-road fan RPM command builder returns
    `[224,32+road_id-1,0,percent_of_max_rpm]`.
  - Motherboard PWM sync writes four report fragments:
    `[224,16,98,17/16]`, `[224,16,98,34/32]`,
    `[224,16,98,68/64]`, `[224,16,98,136/128]`.
    The high bit pattern maps to four ports/channels; the lower value disables
    sync.
  - Motherboard lighting sync writes `[224,16,97,1/0,0,0]`.
  - LED locate and effect reset fragments include `[224,16,47,...]` and
    `[224,16,52,...]`.
- New structured extractor:
  `python tools/lianli_wireless_probe.py --save-json .cache/lianli/extract-hid-js-v2.1.23.json extract-hid-js .cache/lianli/assets-v2.1.23`
  now turns those JS bundles into a reusable HID command map:
  - JS files: 28 scanned, 17 matched.
  - Product IDs: `41220` / `0xa104` appears 17 times; `41219` / `0xa103`
    and `41221` / `0xa105` each appear 34 times.
  - Command template occurrences: 799 total.
  - Command categories: `sync` 170, `telemetry` 136, `lighting` 357,
    `control` 34, `config` 34, `diagnostic` 34, `discovery` 34.
  - Top templates include `motherboard-rpm-sync` 136, `fan-input-report` 68,
    `led-road-config` 68, `fan-rpm-poll` 34, `fan-rpm-set` 34,
    `fan-version-query` 34, and `motherboard-lighting-sync` 34.
- New wireless-oriented JS clue extractor:
  `python tools/lianli_wireless_probe.py --save-json .cache/lianli/extract-wireless-js-v2.1.23.json extract-wireless-js .cache/lianli/assets-v2.1.23`
  scans the same JS assets for RF USB IDs, product strings, wireless LCD keys,
  Electron IPC, settings pipe usage, and HID API contexts:
  - JS files: 28 scanned, 24 matched.
  - Clue occurrences: 841 total.
  - Categories: `ipc` 664, `hid-api` 102, `generic` 75.
  - High-confidence clues: only `hid-device-loader` 34.
  - No high-confidence `0416:8040`, `0416:8041`, `L-Wireless`, or
    `slv3tuzx` clue appears in the scanned JS.
  - Interpretation: the front-end assets expose the wired-controller HID path
    and Electron IPC/settings plumbing, but they still do not expose the RF
    sender/receiver control path as plaintext JS.
- New official changelog analyzer:
  `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-changelog-official.json analyze-changelog https://lian-li.com/zh-TW/l-connect3/l3-changelog/ --top 12`
  parses LIAN LI's current L-Connect 3 changelog and ranks versions by
  L-Wireless/RF/binding/fan-control relevance:
  - Parsed 54 L3 version entries; 17 contain wireless/RF/binding hard matches.
  - Latest listed L3 entries remain `2.1.23`, `2.1.20`, and `2.1.17`; neither
    `2.1.23` nor `2.1.20` contains wireless-scored changelog lines.
  - Top current versions by weighted wireless evidence: `2.0.33`, `2.0.32`,
    `2.0.23`, `2.1.17`, `2.0.22`, `2.0.29`, `2.0.20`, `2.0.34`,
    `2.0.30`, `2.0.21`, `2.1.11`, `2.0.24`.
  - Direct official download links were extracted for `2.0.33`, `2.0.32`,
    `2.1.17`, and `2.0.34` in that top set.
  - Highest-priority static-diff candidates are `2.0.33` / `2.0.32`, because
    their changelog mentions wireless fan color/settings resets, wireless
    controller dongle TX crashes, RF controller initialization failure, and
    random RPM spikes on SL-INF/CL wireless fans with the newer 4-pin receiver.
  - `2.0.34` is a targeted follow-up because it explicitly fixes wireless fan
    MB RPM sync. `2.1.17` is also important because it mentions RF products,
    rebinding, L-Wireless Utility fan settings, and quick sync behavior.
  - The analyzer intentionally treats RPM/lighting/firmware text as evidence
    only when a line also contains a wireless/RF/binding hard match, to avoid
    ranking old wired-controller changes as L-Wireless clues.
- v2.0.33 follow-up static scan:
  - Downloaded official
    `20250825-L-Connect+3-x64-v2.0.33-f7fc8097.exe` from the changelog link.
  - Size: `1257647400`; SHA256:
    `1dd6451215a4ee300293a7aa9d00e3fd439887c60b03a8f6f6c5bfbd7efac470`.
  - 7-Zip exposes the same outer PE/NSIS structure as later installers:
    24 files, including one `[0]` payload of size `1257277368`.
  - `[0]` SHA256:
    `9a652ab0e9cdb2a7e2e2d34c0c1cf8653529da64ec606d2fd23095d2933f1b4b`.
  - `analyze-artifact` on both the EXE and `[0]` finds no high-confidence
    `0416:8040`, `0416:8041`, `L-Wireless`, or RF protocol string.
  - The only wireless-adjacent payload hit is one medium-confidence raw
    little-endian `1cbe:0006` / TL LCD wireless VID/PID hit inside high-entropy
    data, so it should not be treated as protocol proof.
  - Direct zlib/deflate/bzip2/LZMA probes still fail at candidate NSIS offsets,
    and the NSIS firstheader again carries unsupported flags `0x70`. This means
    v2.0.33 is useful as a version target, but not yet as plaintext static
    protocol evidence without deeper NSIS extraction or a Windows USB trace.
- v2.0.32 high-priority wireless stability static scan:
  - Downloaded official
    `20250822-L-Connect+3-x64-v2.0.32-2342e974.exe` from the changelog link.
  - This version is high priority because the official changelog mentions
    wireless fan color/settings resets, wireless controller/dongle TX crashes,
    SL-INF/CL/new 4-pin receiver random RPM spikes, TL/SL wireless LCD
    brightness issues, and Hydroshift II wireless-mode RF controller
    initialization failure.
  - Size: `1257647472`; SHA256:
    `a3c70dd48d8ab2af604f7d73aa8580543def8f805d9e78aa7314dccd9f54dda8`.
  - 7-Zip reports FileVersion/ProductVersion `2.0.32.0` and the same
    outer PE/NSIS layout: 24 files, including one `[0]` payload of size
    `1257277440`.
  - `[0]` SHA256:
    `631c0a2bd19cd039970aa632ac249a119cae83fb56ce2b91bab6c9c3e0dd36de`.
  - `analyze-artifact` on the EXE and `[0]` again finds no high-confidence
    `0416:8040`, `0416:8041`, `L-Wireless`, or RF protocol string.
  - The only wireless-adjacent payload hit is one medium-confidence raw
    little-endian `1cbe:0006` / TL LCD wireless VID/PID hit inside high-entropy
    data, matching v2.0.33's weak static clue.
  - The NSIS header reports unsupported flags `0x70`, header size `96894`,
    data size `1257277434`, file/data size delta `6`, and no successful direct
    zlib/deflate/bzip2/LZMA probe.
  - `diff-artifacts` from v2.0.32 to v2.0.33 confirms the small size delta does
    not imply a localizable patch:
    - `[0]` size delta: `-72` bytes; common prefix: `24` bytes; common suffix:
      `1` byte.
    - Full EXE size delta: `-72` bytes; common prefix: `296` bytes; common
      suffix: `6` bytes.
    - 64KiB fixed-block reuse is `0.0` for `[0]` and `0.000208` for the outer
      EXE.
    - Static match delta is empty: v2.0.33 adds no new static protocol/product
      clue and removes none versus v2.0.32.
    - Magic carving over the changed payload again finds only implausible
      high-entropy MZ/ZIP/gzip/bzip2 signatures.
  - Interpretation: v2.0.32 is still a strong Windows USBPcap target because
    its changelog lines are protocol-relevant, but the official static payload
    is not yielding plaintext RF control code or a useful byte-level diff
    anchor.
- v2.0.34 targeted MB RPM sync follow-up static scan:
  - Downloaded official
    `20250919-L-Connect+3-x64-v2.0.34-988ad479.exe` from the changelog link.
  - Size: `1260695304`; SHA256:
    `b0e86525e65bcaa7473aa45e3b8dbb42f8aa58e839391e85df5aa80137d58459`.
  - 7-Zip reports FileVersion/ProductVersion `2.0.34.0` and the same
    outer PE/NSIS layout: 24 files, including one `[0]` payload of size
    `1260325272`.
  - `[0]` SHA256:
    `baa78afe80f3a0734c0879d3552a55cf3cdc0b4766cb040496e0e6ec92c0ea59`.
  - The `[0]` payload is `3047904` bytes larger than v2.0.33, so the wireless
    fan MB RPM sync fix likely lives inside changed compressed installer
    content, but the changed content is still opaque to the current static
    extractor.
  - `analyze-artifact` on both the EXE and `[0]` still finds no
    high-confidence `0416:8040`, `0416:8041`, `L-Wireless`, or RF protocol
    string.
  - Compared with v2.0.33, v2.0.34 adds one medium-confidence raw
    little-endian TL controller VID/PID hit in addition to the TL LCD hit, but
    both are inside high-entropy data and should not be treated as protocol
    proof.
  - `diff-artifacts` confirms this is not a small local patch in the static
    bytes:
    - `[0]` common prefix: `20` bytes; common suffix: `1` byte.
    - Full EXE common prefix: `296` bytes; common suffix: `6` bytes.
    - 64KiB fixed-block reuse is `0.0` for `[0]` and `0.000208` for the outer
      EXE, so almost the entire compressed payload was regenerated.
    - The only added static hits are medium-confidence raw TL controller and TL
      LCD little-endian VID/PID bytes; no new high-confidence protocol/product
      clue appears.
    - Magic carving over the changed payload finds random-looking MZ/gzip/bzip2
      signatures and one `PK\x03\x04` occurrence, but the sampled structural
      validators mark all of them implausible. The ZIP candidate has impossible
      version/method/name-length fields, so it is not a carveable inner ZIP.
  - `7z l .cache/lianli/nsis-v2.0.34/[0]` cannot open the inner payload as an
    archive, `file` reports only `data`, string extraction yields random-looking
    high-entropy fragments, direct zlib/deflate/bzip2/LZMA probes still fail,
    and the NSIS firstheader again carries unsupported flags `0x70`.
  - Interpretation: v2.0.34 is a strong capture target because the official
    changelog mentions wireless fan MB RPM sync, but it does not yet provide
    plaintext static protocol evidence. The next useful step is deeper NSIS
    extraction with a capable unpacker or a Windows USBPcap trace while toggling
    MB RPM sync in L-Connect 3 v2.0.34.
- v2.1.17 RF rebinding / L-Wireless Utility follow-up static scan:
  - Official changelog date: `2026-03-02`.
  - This version is high priority because the official changelog mentions wired
    SL-INF plus RF products, unbind/rebind behavior, fan speed display/control
    abnormalities after RF rebinding, and L-Wireless Utility fan `(SL/TL)`
    settings switching to quick sync after sort settings.
  - Downloaded installer:
    `20260213-L-Connect 3-x64-v2.1.17-2f7c3856.exe`.
  - Size: `1321815528`; SHA256:
    `e83c582dd1e95c59e3c3c63bb211ef42f4fe5a3a6268783699b857d93f0d4e15`.
  - 7-Zip reports FileVersion/ProductVersion `2.1.17.0` and the same outer
    PE/NSIS layout: 24 files, including one `[0]` payload of size
    `1321445496`.
  - `[0]` SHA256:
    `4331f95c7dd6785eaff554c1f16bbd59694fb949889efeacb4f451d46c9c05cf`.
  - The NSIS header reports unsupported flags `0x70`, header size `102782`,
    data size `1321445491`, file/data size delta `5`, entropy `7.9999`, and no
    successful direct zlib/deflate/bzip2/LZMA probe.
  - `analyze-artifact` on the EXE finds product metadata plus four
    medium-confidence raw little-endian VID/PID hits: RF sender, TL controller,
    TL LCD, and TL LCD wireless.
  - `analyze-artifact` on `[0]` finds the same four raw little-endian VID/PID
    hits, but no high-confidence `0416:8040`, `0416:8041`, `L-Wireless`, RF
    protocol string, or command template.
  - `analyze-artifact-tree` scans the same 24 extracted PE/resource/payload
    files and finds no additional plaintext protocol evidence beyond version
    resource/certificate metadata and the opaque `[0]` hits.
  - `7z l .cache/lianli/nsis-v2.1.17/[0]` cannot open the inner payload as an
    archive and `file` reports only `data`.
  - `diff-artifacts` from v2.0.34 to v2.1.17:
    - `[0]` size delta: `61120224` bytes; common prefix: `20` bytes; common
      suffix: `2` bytes.
    - Full EXE size delta: `61120224` bytes; common prefix: `296` bytes; common
      suffix: `6` bytes.
    - 64KiB fixed-block reuse is `0.0` for both `[0]` and the outer EXE, so the
      relevant compressed payload was regenerated rather than locally patched.
    - The only added static hits are medium-confidence raw RF sender and TL LCD
      wireless little-endian VID/PID bytes; no new high-confidence
      protocol/product clue appears.
    - Magic carving over the changed payload again yields only implausible
      high-entropy MZ/ZIP/gzip/bzip2-like signatures.
  - `diff-artifacts` from v2.1.17 to v2.1.23:
    - v2.1.23 shrinks the installer by `975983136` bytes because LIAN LI moved
      most assets into `Assets.zip`.
    - The v2.1.17 raw RF/TL VID/PID hits disappear from the v2.1.23 installer
      payload; the separately extracted v2.1.23 asset bundle still exposes
      wired AL V2 / SL V2 HID front-end code, not RF packet code.
  - Interpretation: v2.1.17 is now the best official Windows capture target for
    RF bind/unbind and L-Wireless Utility fan setting behavior, but its installer
    still does not expose enough plaintext to implement Linux RF control from
    static analysis alone. The next productive step is a USBPcap trace while
    toggling RF unbind/rebind, fan speed control, sort settings, and quick sync
    in L-Connect 3 v2.1.17.
- `assets/wireless-sensor/*.data` are .NET BinaryFormatter-like sensor video
  assets, not USB command code. They contain:
  - `slv3.models, Version=1.0.0.0`
  - `slv3.models.SensorVideoInfo`
  - fields such as `TotalFrame`, `FrameRate`, `styleType`, `SensorColor`, and
    `ImgLst`
  - `Dictionary<int, byte[]>` frame/image payloads.
- `assets/wireless-sensor/*.turtheme` use `lianli.ThemeEngine` /
  `ThemeEngine.Theme` serialized theme models. They include paths originally
  pointing into an `slv3` working tree.
- The asset scan still does not expose high-confidence textual
  `0416:8040` / `0416:8041` or `L-Wireless` RF protocol strings. Isolated
  little-endian VID/PID hits inside media remain medium-confidence and should
  not be treated as proof.

Practical interpretation:

- For L-Wireless receiver/fan control, `uwscli` plus USB captures remain the
  strongest protocol source.
- v2.1.23 confirms LIAN LI ships many SLV3/wireless LCD/sensor assets, but the
  L-Wireless RF control path is still not present as obvious plaintext in the
  installer/assets.
- v2.1.23 does add a concrete, official HID command map for AL V2 / SL V2
  wired fan controllers. If the project later wants broader LIAN LI fan support,
  this is the next best path after L-Wireless RF validation.
- Wine/Docker/VM assessment for the next capture phase:
  - This Linux host now has `tshark` installed for direct `.pcapng` decoding,
    but still has no Wine, Docker/Podman, QEMU, or VirtualBox installed.
  - Wine is useful only for installer smoke tests and installed-file extraction;
    it does not reproduce Windows kernel USBPcap/HID/WinUSB behavior reliably,
    so it cannot prove the RF protocol.
  - Docker is useful only as a Linux analyzer container after a capture has
    already been exported; it is not a Windows GUI + USBPcap environment.
  - A Windows VM with USB passthrough for `0416:8040` and `0416:8041` remains
    the best route to official L-Connect traffic.
  - New helper:
    `python tools/lianli_wireless_probe.py windows-capture-plan --version 2.1.17 --installer .cache/lianli/downloads/20260213-L-Connect-3-x64-v2.1.17-2f7c3856.exe --capture-base lianli-v2117`
    emits a machine-readable capture checklist for baseline, direct fan speed,
    motherboard PWM sync, RF unbind/rebind, sort/quick-sync, and lighting
    static/off plus generated-rainbow scenarios.
  - New sidecar note helper:
    `python tools/lianli_wireless_probe.py --save-json .cache/lianli/captures/lianli-v2117-01-direct-fan-speed.notes.json windows-capture-note direct-fan-speed --capture-base lianli-v2117 --receiver-mac <receiver-mac> --master-mac <master-mac> --channel <channel> --rx-type <rx-type> --device-type <device-type> --fan-count <fan-count> --led-count <led-count> --pwm-values <captured-or-expected-pwm-tuple> --mark-actions-done`
    creates the `<capture-stem>.notes.json` operator record consumed by
    `capture-set-report`, so the target MAC/channel/action context is kept next
    to each Windows USBPcap file. The sidecar can also carry scenario operation
    parameters such as direct PWM tuples, fallback PWM, decoded motherboard PWM,
    bind/unbind PWM tuples, static RGB color, rainbow frame count, interval, and
    effect index; when present, the planned no-write `compare-capture` commands
    are emitted with those placeholders already filled.
  - New batch triage helper:
    `python tools/lianli_wireless_probe.py summarize-captures <capture-dir>`
    recursively ranks `.pcapng`, `.pcap`, `.txt`, `.json`, `.tsv`, and `.hex`
    captures by RF frame, receiver snapshot, replay-hint, and operation
    evidence, then emits the next `analyze-capture`, `capture-replay-plan`, and
    `capture-protocol-report` commands for each promising file.
  - New planned-capture-set audit helper:
    `python tools/lianli_wireless_probe.py capture-set-report <capture-dir> --capture-base lianli-v2117`
    maps the same baseline, direct fan speed, motherboard PWM sync, RF rebind,
    sort/quick-sync, static/off lighting, and generated-rainbow lighting scenarios from
    `windows-capture-plan` onto the files in a directory. It marks each
    scenario as `evidence-found`, `partial-evidence`, `missing-capture`,
    `no-evidence`, or `analysis-error`, reports which expected official
    L-Connect behavior is still missing, and now aggregates per-scenario
    `linux_live_write_targets` so a complete capture set shows which operations
    have high-confidence Linux sender/endpoint evidence. The same report now
    includes `linux_validation_plan`, which turns that evidence into an ordered
    Linux checklist: `usb-capture-readiness`, `validate-readonly`, then guarded
    safe write experiments for the capture-derived receiver MACs. Passing
    `--experiment-dir <linux-log-dir>` attaches `summarize-experiments` output,
    mirrors its `hardware_validation` status at the capture-set top level, and
    changes `linux_validation_plan.status` when readonly and/or guarded-write
    Linux validation has already been observed. The same report now includes
    `linux_control_matrix`, a per-operation view for receiver snapshots, direct
    PWM, motherboard sync/mirror, bind/unbind, static RGB, and generated RGB
    animation. Each row shows Windows evidence status, Linux sender/endpoint
    confidence, attached experiment status, and operation-specific next
    commands instead of collapsing every target into a generic PWM experiment.
    The same report now emits `capture_note_context_summary`, which aggregates
    `<capture-stem>.notes.json` target context fields, flags MAC/channel/rx_type
    conflicts across sidecars, and exposes `common_target_args` for no-write
    `compare-capture` / dry-run commands. This is operator context only; it
    does not unlock guarded writes.
    It also emits `capture_note_operator_summary`, which tracks whether the
    Windows actions in each sidecar were marked complete.
    `capture-gap-report <capture-dir> --capture-base lianli-v2117` is the
    compact operator view of the same data: it sorts missing/partial scenarios
    by priority, names the next capture file to produce, lists proof gates such
    as baseline-before-write and lighting-before-pairing, and includes the
    exact post-capture analyzer commands.
    When given `--artifact-dir <artifact-report-dir>`, the gap report reads the
    target version's `artifact-evidence-matrix` changelog fields and annotates
    each affected scenario with `base_priority`, adjusted `priority`, and
    `changelog_focus`. Baseline still remains first, direct PWM stays the first
    write proof, and riskier RF bind/unbind captures are raised only after the
    lower-risk write scenarios.
    `linux_interface_contract` then turns the same evidence into implementation
    inputs: PyUSB sender/receiver VID/PID and endpoints, `LianLiWirelessBackend`
    builder/send method names, required runtime fields, dry-run/safe CLI names,
    validated operations, and operations ready for guarded experiments.
  - New standalone contract export:
    `python tools/lianli_wireless_probe.py linux-interface-contract <capture-dir> --capture-base lianli-v2117 --experiment-dir <linux-log-dir>`
    emits the same `lianli-linux-interface-contract/v1` payload without the
    full scenario audit, plus a compact source summary, hardware-validation
    status, per-operation control-matrix summary, and deduplicated recommended
    commands for GUI/backend integration.
  - New GUI/backend manifest export:
    `python tools/lianli_wireless_probe.py linux-control-manifest <capture-dir> --capture-base lianli-v2117 --experiment-dir <linux-log-dir>`
    wraps the interface contract as `lianli-linux-control-manifest/v1`, with
    per-operation capabilities, readiness/evidence, input field schema,
    safety gates, udev permission hints, target MACs, missing scenario details,
    and commands. Writes are explicitly disabled by default; guarded experiments still require the
    `WRITE-LIANLI` confirmation token.
  - New Linux preflight helper:
    `python tools/lianli_wireless_probe.py linux-control-preflight <capture-dir> --capture-base lianli-v2117 --experiment-dir <linux-log-dir>`
    combines the manifest with local sysfs USB visibility and
    `/dev/bus/usb` read/write permission checks. It emits per-operation
    `preflight_status` values such as `ready`, `needs-usb-permission`,
    `missing-hardware`, or `needs-capture-evidence`, so the GUI can refuse
    unsafe writes before opening PyUSB.
  - Capture-derived Linux targets now preserve runtime context from decoded RF
    frames and receiver snapshots: target MAC, channel, outer/payload `rx_type`,
    payload channel, master MAC, and a `runtime_contexts` list. When the capture
    set includes a baseline receiver snapshot, the context is enriched with
    `device_type`, `fan_count`, `pwm_values`, `fan_rpm`, `command_sequence`, and
    `raw_hex`. This lets the GUI/backend build `WirelessDeviceInfo`-compatible
    dry-run inputs from evidence instead of asking the user to re-enter
    channel/type values manually.
  - New Linux action-plan helper:
    `python tools/lianli_wireless_probe.py linux-control-action-plan <capture-dir> --capture-base lianli-v2117 --experiment-dir <linux-log-dir>`
    converts preflight output into ordered setup, readonly-validation,
    safe-experiment, and capture-evidence actions. Each action includes a
    primary command, readiness reason, required VID/PID, USB-write flag, and
    `WRITE-LIANLI` confirmation requirement when applicable. Ready live-write
    actions now also include `pre_write_validation_commands`: a no-write
    `linux-control-packet-preview` command plus one
    `linux-control-packet-compare` command for each official capture file that
    contributed to the operation. The safe experiment command remains the
    primary write command, but the intended workflow is preview/compare first.
    The same action now carries structured `pre_write_validation` policy:
    `minimum_required_match=exact-match`,
    `required_write_gate_status=pass`, and semantic-only matches require
    `live-list`/receiver-state refresh plus another compare before any guarded
    write. Saved `linux-control-packet-compare` JSON logs under
    `--experiment-dir` are summarized and attached as
    `pre_write_validation.observed_results`, letting a GUI show whether the
  gate is already `passed`, still `needs-run`, or blocked by stale/failed
  packet evidence. Compare logs can use either the action-plan absolute capture
  path or the same capture filename/path suffix; `observed_results` reports
  `observed_capture_match` as `exact` or `path-suffix` so GUI runs launched from
  a capture directory still satisfy the write gate. The gate now also reports
  `source_capture_coverage` and requires every expected official source capture
  to have its own exact/pass compare result; duplicate logs for one capture do
  not unlock a write when another required trace is still unverified. Saved
  compare logs must also carry the current
  `lianli-linux-control-packet-compare/v1` schema; older or hand-written logs
  are surfaced in `observed_results` but do not satisfy the write gate and are
  counted separately as `invalid_schema_count` in top-level
  `packet_compare_validation`. Actions blocked only by stale compare-log schema
  report `invalid-pre-write-validation-schema` and point back to the compare
  command instead of enabling a `WRITE-LIANLI` write. The action plan also
  includes top-level `guarded_write_readiness`, which separates preflight-ready
  actions from
    actions that are actually ready for guarded writes after exact packet
    comparison. Each action now also carries an `execution` block and the
    action-plan exposes `next_commands`: when pre-write validation has not
    passed, the next command is packet preview/compare instead of the
    `WRITE-LIANLI` safe experiment; after exact compare passes, the write
    command is enabled. If saved compare evidence is semantic-only, the action
    enters `refresh-live-snapshot` and `next_command` becomes
    `python tools/lianli_wireless_probe.py --save-json <experiment-dir>/live-list-refresh.json live-list`
    before re-running packet comparison. Saved `live-list` /
    `live-list-before` / `live-list-after` logs under `--experiment-dir` are
    now summarized as `live_snapshot_context`; once a refreshed snapshot for the
    target MAC exists, the action switches to `needs-recompare-after-refresh`
    and the next command becomes packet preview/compare using that refreshed
    command sequence and receiver state. Missing-evidence actions now carry structured `missing_scenarios`,
    `windows_capture_actions`, and `post_capture_commands`, so a GUI can show
    exactly which USBPcap file is still needed and which analyzer/compare
    commands to run after export.
  - New target registry helper:
    `python tools/lianli_wireless_probe.py linux-control-target-registry <capture-dir> --capture-base lianli-v2117 --experiment-dir <linux-log-dir>`
    turns action-plan runtime contexts into target entries such as
    `aa:bb:cc:dd:ee:ff@ch8/rx3`. Each entry includes packet-build readiness,
    operations that are ready, missing live snapshot fields, and a
    `WirelessDeviceInfo` JSON template for GUI/backend integration.
  - New packet preview helper:
    `python tools/lianli_wireless_probe.py linux-control-packet-preview <capture-dir> live-pwm --capture-base lianli-v2117 --experiment-dir <linux-log-dir> --pwm-values 77,88,99,111`
    selects a target from the registry and calls the existing
    `LianLiWirelessBackend` packet builders without writing USB. This verifies
    that capture-derived MAC/channel/rx_type/master MAC context can actually
    produce RF packet bytes before any guarded live experiment. For direct PWM,
    the preview now uses the PWM tuple decoded from the capture when
    `--pwm-values`/`--pwm` is omitted. It also emits every generated packet's
    full hex, SHA-256, RF chunk metadata, and decoded RF frame link so the bytes
    can be compared one-by-one with an official USBPcap trace.
  - New packet compare helper:
    `python tools/lianli_wireless_probe.py linux-control-packet-compare <capture-dir> live-pwm <official-capture> --capture-base lianli-v2117 --experiment-dir <linux-log-dir>`
    uses the same capture-derived target registry and packet preview, then runs
    `compare-capture` against an official USBPcap/tshark export. This removes
    the manual `--mac`/`--master-mac`/`--pwm-values` reconstruction step when
    checking whether Linux-built packets match L-Connect traffic. A semantic
    match can pass while exact match fails if the capture set lacks a receiver
    snapshot or the live receiver command sequence differs. With a baseline
    snapshot from the same Windows workflow, packet compare can now prove an
    exact byte match for direct PWM dry-runs; run `live-list` before real writes
    to refresh that state from hardware.
  - New capture timeline helper:
    `python tools/lianli_wireless_probe.py capture-timeline-report <capture>`
    renders receiver-list requests, master queries, receiver snapshots, and
    reassembled RF frames as chronological events. It also marks receiver
    snapshot field changes and carries USBPcap/tshark frame number, relative
    time/delta, VID/PID, endpoint, and source metadata when available, making
    official USBPcap traces easier to compare against L-Connect UI actions.
  - New artifact evidence matrix helper:
    `python tools/lianli_wireless_probe.py artifact-evidence-matrix <report-dir>`
    reads saved `analyze-artifact`, `analyze-artifact-tree`, `extract-hid-js`,
    `extract-wireless-js`, `diff-artifacts`, and `analyze-changelog` JSON
    reports, groups them by L-Connect version, and separates high-priority
    L-Wireless RF USB leads from low-confidence raw VID/PID hits, adjacent
    wireless assets, wired AL/SL V2 HID fan-controller evidence, and official
    changelog relevance. The matrix now emits `changelog_score`,
    `changelog_keywords`, per-version changelog evidence snippets, and
    `summary.recommended_capture_versions`, so versions like v2.1.17 can be
    prioritized when low-confidence RF static hits align with official
    RF/bind/fan-setting changelog lines.
  - New transport-layer capture helper:
    `python tools/lianli_wireless_probe.py capture-transport-report <capture>`
    keeps USBPcap/tshark metadata such as frame number, source field, endpoint
    direction, VID/PID, payload size, and first known L-Wireless
    classification before RF reassembly. It also reports `usb_device_counts`,
    `usb_endpoint_counts`, and `lianli_usb_targets.sender_seen/receiver_seen`
    so a trace can be rejected quickly if it missed `0416:8040` or
    `0416:8041`. Use it as the first check on a raw `.pcapng` or
    `tshark -T json` export to confirm whether the useful payloads are in
    `usb.capdata`, `usbhid.data`, or `data.data`.
  - New local USB readiness helper:
    `python tools/lianli_wireless_probe.py usb-capture-readiness`
    inspects `/sys/bus/usb/devices` for `0416:8040` and `0416:8041`, reports
    whether Linux live validation is possible, lists udev rules, usbmon/tshark
    status, and emits the next `live-list`, `live-master`,
    `validate-readonly`, Windows capture-plan, and capture-summary commands.
    Current host result is saved as
    `.cache/lianli/usb-capture-readiness-current.json`: no `0416:8040` or
    `0416:8041` devices are currently visible, `tshark` and `usbipd` are
    available, and `/sys/kernel/debug/usb/usbmon` is not accessible to the
    current unprivileged process.

## Public Protocol Lead

The strongest lead is the third-party Python package `uwscli==0.4.0`
(`Uni Wireless Sync CLI`). It is an unofficial Linux/POSIX-capable toolkit for
TL wireless fans, L-Wireless receivers, and TL LCD panels.

Relevant IDs and transports found in `uwscli`:

- RF sender: `VID:PID 0416:8040`
- RF receiver: `VID:PID 0416:8041`
- TL fan controller HID: `0416:7372`
- TL LCD HID/USB candidates: `04fc:7393`, `1cbe:0006`
- RF transport uses PyUSB/libusb endpoints `0x01` OUT and `0x81` IN.
- LCD wireless transport uses DES-CBC with key/IV `slv3tuzx`.

Supported operations already implemented upstream:

- List wireless receivers and parse metadata.
- Query master controller MAC.
- Bind/unbind wireless receivers.
- Set direct fan PWM values per receiver.
- Enable motherboard PWM sync mode by sending PWM `6`.
- Send static/rainbow/effect RGB payloads.
- Send JPEG frames and control brightness/rotation for wireless LCD panels.

## RF Fan Protocol Summary

The L-Wireless fan path appears to use two USB devices:

- `0416:8040` sender: accepts 64-byte RF packets.
- `0416:8041` receiver: returns snapshots of bound/unbound wireless receivers.

Read-only receiver snapshot:

- Write 64 bytes to `0416:8041`.
- Byte `0`: `0x10`
- Byte `1`: page count.
- Read `434 * page_count` bytes.
- Response byte `0`: `0x10`
- Response byte `1`: receiver count.
- Response bytes `2:4` can encode motherboard PWM input. Cross-checking the
  local `uwscli` source shows byte `2` as an indicator and byte `3` as the PWM
  value component. If the high bit of byte `2` is clear and
  `(byte2 & 0x7f) + byte3` is nonzero, effective PWM is
  `int(255 * byte3 / ((byte2 & 0x7f) + byte3))`; otherwise it is unavailable.
- Receiver records start at offset `4`, each 42 bytes.
- A valid record has byte `41 == 28`.
- Record fields observed in `uwscli`:
  - `0:6`: receiver MAC.
  - `6:12`: master MAC.
  - `12`: RF channel.
  - `13`: receiver type / slot.
  - `18`: device type.
  - `19`: fan count, with values over 9 stored as `value - 10`.
  - `28:36`: four big-endian RPM readings.
  - `36:40`: four PWM values.
  - `40`: command sequence.

Read-only master query:

- Write 64 bytes to RF sender `0416:8040`.
- Byte `0`: `0x11`.
- Byte `1`: RF channel, normally `8`.
- Read 64 bytes back from the same sender endpoint.
- Response byte `0`: `0x11`.
- Response bytes `1:7`: active master MAC, or all zero if unavailable.

PWM write payload:

- Build a 240-byte RF payload.
- Byte `0`: `0x12`
- Byte `1`: `0x10`
- Bytes `2:8`: receiver MAC.
- Bytes `8:14`: master MAC.
- Byte `14`: receiver type.
- Byte `15`: channel.
- Byte `16`: sequence index.
- Bytes `17:21`: four PWM values.
- Send through `0416:8040` as four 64-byte packets:
  - Byte `0`: `0x10`
  - Byte `1`: chunk sequence `0..3`
  - Byte `2`: channel
  - Byte `3`: receiver type
  - Bytes `4:64`: 60-byte payload chunk.

Bind payload:

- Binding uses the same 240-byte command family as PWM (`12 10`).
- It is sent to the unbound receiver through the RF sender with outer RF
  receiver type `0` when the receiver has no slot yet.
- Bytes `2:8`: receiver MAC.
- Bytes `8:14`: desired master MAC.
- Byte `14`: desired receiver slot/type, valid range `1..15`.
- Byte `15`: RF channel.
- Byte `16`: sequence/index `1`.
- Bytes `17:21`: current PWM tuple from the receiver snapshot.

Unbind payload:

- Unbinding also uses command `12 10`.
- Bytes `8:14` are zeroed master MAC bytes.
- Byte `14`: receiver slot/type `0`.
- Byte `16`: sequence/index `0`.
- The outer RF packet header still uses the currently bound receiver type.

Motherboard PWM passthrough:

- `uwscli` treats PWM value `6` as receiver-mode motherboard PWM sync.
- The same `uwscli` source also exposes a poll-and-mirror path: read
  `snapshot.motherboard_pwm` from receiver response bytes `2:4`, then write that
  decoded value as a normal direct PWM tuple `[pwm, pwm, pwm, pwm]` to each
  bound receiver. This is useful for a Linux daemon even if receiver-mode sync
  is not reliable.
- Direct PWM control is any normal value `0..255`; safety policy should avoid
  writing low duty values unless the user explicitly enables control.
- `dry-run-pwm-sync` now builds the same `06 06 06 06` PWM payload without
  writing USB. This is a useful preflight before enabling receiver-mode sync.
- `dry-run-pwm-mirror --snapshot-hex ...` decodes the motherboard PWM field from
  a receiver snapshot and builds the equivalent direct PWM RF packets. For
  snapshot bytes `10 00 0a 0a`, it decodes PWM `127` and produces
  `7f 7f 7f 7f` in the PWM payload field.
- `dry-run-pwm` and `compare-capture ... pwm` now also accept
  `--pwm-values 80,90,100,110` for non-uniform payloads observed in traces.
  `--current-pwm 80,90,100,110` can be supplied to bind/unbind comparisons so
  the current receiver PWM tuple in bytes `17:21` can be reproduced exactly.
- The capture analyzer now also recognizes the same poll-and-mirror pattern in
  official traces: if a receiver snapshot with a valid `motherboard_pwm` appears
  before a direct PWM RF frame whose four PWM bytes equal that decoded value,
  the frame is annotated as `live-pwm-mirror` with the snapshot packet index.

LED/RGB path:

- RGB uses RF payload byte `1 == 0x20`.
- Raw RGB frames are compressed with a TinyUZ-compatible encoder.
- Initial LED packet carries compressed length, frame count, LED count, and
  frame interval. Later packets carry compressed chunks.
- The capture analyzer now reassembles RGB packet groups and decodes TinyUZ
  literal streams, literal-line blocks, and dictionary back-references. In
  supported traces it
  records `rgb_payload.decode_status=decoded-literal` or `decoded-backref`,
  `static_color`, and `static_color_hex` such as `#000000` for lights off.
  Failed streams keep structured diagnostics such as `invalid-dict-pos`,
  `truncated-backref`, `stream-end-before-expected`, or `unknown-control`.
- The official `uwscli` LED transmit path repeats the first 240-byte LED
  payload four times. The local RGB sender mirrors that behavior by default.
  The analyzer keeps the raw evidence as `rf_frame_operations` while merging
  grouped RGB payload frames into one logical `rf_operations.live-rgb` sequence.
  For a 132-LED static color write this means 28 RF chunks, 7 RF frames, and 1
  logical RGB operation with `rgb_first_packet_retransmit_counts=[4]`.
- Inferred LED-count mapping from `uwscli`:
  - device type `1`: 116 LEDs
  - device type `2`: 132 LEDs
  - device type `3`: 174 LEDs
  - device type `4`: 88 LEDs
  - device type `65`: 96 LEDs
  - device type `10`: `24 + fan_count * 24`
  - fallback: `fan_count * 26`, or 60 LEDs if unknown
- The receiver record also carries an LED-count hint at raw byte `31`. The
  local `infer_led_count()` now adopts that hint when the type/fan-count
  heuristic is ambiguous, matching the behavior observed in `uwscli`.
- A static color frame can be generated locally now:
  - expand one RGB triple to `led_count * 3` bytes;
  - TinyUZ-compress the frame;
  - build one or more 240-byte LED payloads;
  - split each payload into four 64-byte RF packets.
- Turning lights off is the same static RGB path with color `(0, 0, 0)`.
  Static RGB dry-run/live/safe/compare commands now accept `--led-count` too,
  so captures from unknown or differently mapped receivers can be replayed with
  the exact decoded LED count.
- Multi-frame RGB payloads can be generated locally too. The generic builder
  accepts raw `led_count * frame_count * 3` RGB bytes, writes the official
  frame-count and interval fields, TinyUZ-compresses the full buffer, and uses
  the same first-payload retransmit behavior as static color. `dry-run-rainbow`
  generates the same HSV rainbow frame layout used by
  `uwscli.generate_rainbow_frames()`. `--led-count` can override the inferred
  LED count when replaying captures from an unknown or differently mapped
  receiver type.
- `capture-replay-plan` now recognizes locally reproducible generated rainbow
  RGB sequences by comparing the decoded RGB SHA256 against the local rainbow
  generator. For matching captures it emits copy-paste `dry-run-rainbow` and
  `compare-capture ... rainbow` commands with `--frame-count`, `--interval-ms`,
  `--led-count`, and `--effect-index`.
- The local TinyUZ encoder output was byte-for-byte compared against
  `uwscli.tinyuz.compress_led_payload()` for a 132-LED black frame.

LCD path:

- Wireless LCD transport uses USB endpoint `0x01` OUT and `0x81` IN.
- Header is DES-CBC encrypted with key and IV `slv3tuzx`.
- Plain header is 504 bytes, PKCS#7 padded to one 512-byte encrypted header.
- Header bytes observed in `uwscli`:
  - byte `0`: command id
  - bytes `2:4`: magic `1a 6d`
  - bytes `4:8`: timestamp in milliseconds, little-endian
  - bytes `8:12`: big-endian payload length for JPG/payload commands
  - byte `8`: single-byte value for brightness/rotation commands
- No-payload commands are transmitted as a 512-byte packet. Payload commands
  reserve a 102400-byte transfer buffer and place payload bytes after offset
  `512`.
- Commands observed:
  - `10`: get version
  - `11`: reboot
  - `13`: rotation
  - `14`: brightness
  - `101`: push JPG
  - `201`: get position / handshake
- A narrow read-only backend now exists for wireless LCD:
  - `GET_POS_INDEX` returns mode and frame index from bytes `8` and `9`.
  - `GET_VER` returns a UTF-8 firmware string from bytes `8:40`.
  - Live reads require encrypted headers and therefore the `lianli` extra /
    `pycryptodomex` dependency.
- Guarded live LCD writes currently support only brightness and rotation:
  - brightness is clamped to `0..100`;
  - rotation accepts `0`, `90`, `180`, or `270` degrees;
  - both require `--confirm WRITE-LIANLI`;
  - JPG upload remains dry-run only until real hardware validation.

## Initial Difficulty Assessment

Basic Linux support looks feasible, especially for TL wireless hardware:

- Device discovery and permissions are straightforward via libusb/hidapi.
- Core receiver commands are already documented in working Python code.
- PWM control is much easier than expected because `uwscli` has direct
  one-shot PWM commands.
- RGB is more complex, but static/effect paths already exist.
- LCD is the hardest path, but the transport framing and DES key are already
  known from `uwscli`.

Risks:

- `uwscli` currently says TL wireless/TL LCD are supported; other series such
  as SL-INF Wireless or CL Wireless may need device-type mapping and tests.
- We still need real hardware to verify VID/PID, receiver mode, bound state,
  fan count, LED count, and safety behavior.
- L-Connect may use firmware-specific behavior not covered by `uwscli`.

## Recommended Next Steps

1. If hardware is purchased, run:
   - `lsusb`
   - `python tools/lianli_wireless_probe.py`
   - `python tools/lianli_wireless_probe.py udev-rules`
   - `python -m pip install -e '.[lianli]'`
   - `python tools/lianli_wireless_probe.py validate-readonly --output-dir .cache/lianli/validation`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-list-before.json live-list`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-master.json live-master --channel 8`
   - `python tools/lianli_wireless_probe.py dry-run-master-query --channel 8`
   - `python tools/lianli_wireless_probe.py dry-run-pwm --pwm 120`
   - `python tools/lianli_wireless_probe.py dry-run-pwm-sync`
   - `python tools/lianli_wireless_probe.py dry-run-pwm-mirror --snapshot-hex "10 00 0a 0a"`
   - `python tools/lianli_wireless_probe.py dry-run-bind --master-mac 10:20:30:40:50:60 --rx-type 3`
   - `python tools/lianli_wireless_probe.py dry-run-unbind`
   - `python tools/lianli_wireless_probe.py dry-run-rgb --color 0,0,0 --led-count 132`
   - `python tools/lianli_wireless_probe.py dry-run-rainbow --frame-count 24 --interval-ms 50 --led-count 132`
   - `python tools/lianli_wireless_probe.py dry-run-lcd brightness --value 65 --timestamp-ms 16909060`
   - `python tools/lianli_wireless_probe.py dry-run-lcd push-jpg --payload-size 6 --timestamp-ms 1`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-lcd-info.json live-lcd-info`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-installer-exe.json analyze-artifact ".cache/lianli/extract/20260422-L-Connect 3-x64-v2.1.20-fde9a570.exe"`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-nsis-payload.json analyze-artifact .cache/lianli/nsis/[0]`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-changelog-official.json analyze-changelog https://lian-li.com/zh-TW/l-connect3/l3-changelog/ --top 12`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.0.34-exe.json analyze-artifact .cache/lianli/downloads/20250919-L-Connect-3-x64-v2.0.34-988ad479.exe`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.0.34-nsis-payload.json analyze-artifact .cache/lianli/nsis-v2.0.34/[0]`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.0.34-nsis-tree.json analyze-artifact-tree .cache/lianli/nsis-v2.0.34 --max-file-size 2000000000`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.0.32-exe.json analyze-artifact .cache/lianli/downloads/20250822-L-Connect-3-x64-v2.0.32-2342e974.exe`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.0.32-nsis-payload.json analyze-artifact .cache/lianli/nsis-v2.0.32/[0]`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.0.32-nsis-tree.json analyze-artifact-tree .cache/lianli/nsis-v2.0.32 --max-file-size 2000000000`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.0.32-v2.0.33-payload.json diff-artifacts .cache/lianli/nsis-v2.0.32/[0] .cache/lianli/nsis-v2.0.33/[0] --block-size 65536`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.0.32-v2.0.33-exe.json diff-artifacts .cache/lianli/downloads/20250822-L-Connect-3-x64-v2.0.32-2342e974.exe .cache/lianli/downloads/20250825-L-Connect-3-x64-v2.0.33-f7fc8097.exe --block-size 65536`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.0.33-v2.0.34-payload.json diff-artifacts .cache/lianli/nsis-v2.0.33/[0] .cache/lianli/nsis-v2.0.34/[0] --block-size 65536`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.0.33-v2.0.34-exe.json diff-artifacts .cache/lianli/downloads/20250825-L-Connect-3-x64-v2.0.33-f7fc8097.exe .cache/lianli/downloads/20250919-L-Connect-3-x64-v2.0.34-988ad479.exe --block-size 65536`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.1.17-exe.json analyze-artifact .cache/lianli/downloads/20260213-L-Connect-3-x64-v2.1.17-2f7c3856.exe`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.1.17-nsis-payload.json analyze-artifact .cache/lianli/nsis-v2.1.17/[0]`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.1.17-nsis-tree.json analyze-artifact-tree .cache/lianli/nsis-v2.1.17 --max-file-size 2000000000`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.0.34-v2.1.17-payload.json diff-artifacts .cache/lianli/nsis-v2.0.34/[0] .cache/lianli/nsis-v2.1.17/[0] --block-size 65536`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.0.34-v2.1.17-exe.json diff-artifacts .cache/lianli/downloads/20250919-L-Connect-3-x64-v2.0.34-988ad479.exe .cache/lianli/downloads/20260213-L-Connect-3-x64-v2.1.17-2f7c3856.exe --block-size 65536`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.1.17-v2.1.23-payload.json diff-artifacts .cache/lianli/nsis-v2.1.17/[0] .cache/lianli/nsis-v2.1.23/[0] --block-size 65536`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.1.17-v2.1.23-exe.json diff-artifacts .cache/lianli/downloads/20260213-L-Connect-3-x64-v2.1.17-2f7c3856.exe ".cache/lianli/extract-v2.1.23/20260522-L-Connect 3-x64-v2.1.23-5b4679ee.exe" --block-size 65536`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/extract-hid-js-v2.1.23.json extract-hid-js .cache/lianli/assets-v2.1.23`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/extract-wireless-js-v2.1.23.json extract-wireless-js .cache/lianli/assets-v2.1.23`
   - `python tools/lianli_wireless_probe.py usb-capture-readiness`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/protocol-signatures.json protocol-signatures --led-count 12 --rainbow-frames 3 --interval-ms 40`
   - `python -m uwscli fan list --output json`
   - `python -m uwscli fan list-masters --output json`
2. If a Windows L-Connect 3 USBPcap trace is available, decode it locally:
   - `python tools/lianli_wireless_probe.py windows-capture-plan --version 2.1.17 --installer .cache/lianli/downloads/20260213-L-Connect-3-x64-v2.1.17-2f7c3856.exe --capture-base lianli-v2117`
   - `python tools/lianli_wireless_probe.py summarize-captures ./lianli-v2117-captures`
   - `python tools/lianli_wireless_probe.py protocol-signatures --led-count 12 --rainbow-frames 3 --interval-ms 40`
   - `python tools/lianli_wireless_probe.py capture-triage-report l-connect-usb.pcapng --led-count 12 --rainbow-frames 3 --interval-ms 40`
   - `python tools/lianli_wireless_probe.py capture-transport-report l-connect-usb.pcapng`
   - `python tools/lianli_wireless_probe.py capture-signature-match l-connect-usb.pcapng --led-count 12 --rainbow-frames 3 --interval-ms 40`
   - `python tools/lianli_wireless_probe.py analyze-capture l-connect-usb.pcapng`
   - `python tools/lianli_wireless_probe.py capture-replay-plan l-connect-usb.pcapng`
   - `python tools/lianli_wireless_probe.py capture-protocol-report l-connect-usb.pcapng`
   - `python tools/lianli_wireless_probe.py compare-capture l-connect-usb.pcapng pwm-sync`
   - `python tools/lianli_wireless_probe.py capture-triage-report l-connect-usb-hex.txt --led-count 12 --rainbow-frames 3 --interval-ms 40`
   - `python tools/lianli_wireless_probe.py capture-transport-report l-connect-usb-hex.txt`
   - `python tools/lianli_wireless_probe.py capture-signature-match l-connect-usb-hex.txt --led-count 12 --rainbow-frames 3 --interval-ms 40`
   - `python tools/lianli_wireless_probe.py analyze-capture l-connect-usb-hex.txt`
   - `python tools/lianli_wireless_probe.py capture-replay-plan l-connect-usb-hex.txt`
   - `python tools/lianli_wireless_probe.py capture-protocol-report l-connect-usb-hex.txt`
   - `python tools/lianli_wireless_probe.py compare-capture l-connect-usb-hex.txt pwm-sync`
   - `python tools/lianli_wireless_probe.py compare-capture l-connect-usb-hex.txt pwm --mac aa:bb:cc:dd:ee:ff --master-mac 10:20:30:40:50:60 --pwm 120`
   - `python tools/lianli_wireless_probe.py compare-capture l-connect-usb-hex.txt pwm-mirror --snapshot-hex "10 00 0a 0a" --mac aa:bb:cc:dd:ee:ff --master-mac 10:20:30:40:50:60`
   - Raw `.pcapng`/`.pcap` input is supported when `tshark` is installed. The
     analyzer extracts `usb.capdata`, `usbhid.data`, and `data.data` fields and
     then reuses the same RF decoder.
   - Without `tshark`, export the payload fields manually:
     `tshark -r l-connect-usb.pcapng -T fields -E separator=\t -e usb.capdata -e usbhid.data -e data.data > l-connect-usb-hex.txt`
   - `compare-capture` builds the local expected RF packets and searches for
     matching frames in the official capture. It reports both exact payload
     equality and semantic equality. Semantic equality ignores the PWM-family
     sequence byte, because that byte normally increments per command and may
     differ between an official L-Connect run and a local dry-run.
   - `protocol-signatures` emits a local catalog of known request/RF payload
     fingerprints before a capture is decoded: receiver list request, master
     query, direct PWM, motherboard sync enable/disable, PWM mirror, bind,
     unbind, static RGB red/off, and generated rainbow. Each item includes USB
     target VID:PID, packet sizes, packet/RF payload SHA256 hashes, searchable
     hex prefixes, decoded operation summary, and copy-paste dry-run/compare
     commands using `<capture>` as the placeholder path.
   - `capture-signature-match` applies that catalog to one capture and reports
     which signatures are present. It combines exact contiguous packet-sequence
     matching for receiver-list/master-query requests with RF frame exact and
     semantic matching for PWM, sync, bind/unbind, RGB, and rainbow operations.
     The output is useful immediately after USBPcap export because it tells
     which compare commands are worth running in detail and which expected
     operations are absent.
     For real hardware captures, where receiver/master MACs differ from the
     catalog examples, it also reports `shape_match`: target-independent
     evidence based on decoded operation type and key parameters such as
     arbitrary direct PWM tuples, `[6,6,6,6]` motherboard PWM sync, bind/unbind
     operation shape, static RGB color, or generated rainbow parameters. When a
     matched RF frame has enough decoded context, `observed_commands` and
     top-level `matched_commands` prefer capture-derived commands using the
     real receiver/master MACs and decoded PWM/RGB parameters instead of the
     catalog placeholder target.
   - `capture-triage-report` is the one-command first pass for a single
     Windows USBPcap/export file. It runs transport extraction, local signature
     matching, replay hint generation, and protocol aggregation, then emits a
     compact JSON status such as `protocol-signature-match`,
     `rf-replay-hints`, or `no-known-l-wireless-payloads`. It intentionally
     keeps only matched signature items and concise transport/replay summaries
     so the first result is readable; its recommended commands include
     `capture-timeline-report` so protocol hits can be aligned back to USBPcap
     frame numbers and L-Connect UI actions. When protocol-level USB metadata
     proves a write operation came from the RF sender, the triage top level also
     includes `linux_live_write_targets`, a high-confidence next-step note, and
     guarded `validate-readonly` / `safe-pwm-experiment` commands for the
     observed receiver MAC.
   - `analyze-capture` emits `replay_hints` for each decoded logical operation.
     Each hint contains argv-style `dry_run` and `compare_capture` commands with inferred
     `--mac`, `--master-mac`, `--channel`, `--rx-type`, `--sequence`,
     `--pwm-values`, `--motherboard-pwm`, `--current-pwm`, RGB `--color`, or
     RGB `--led-count` where the capture contains enough context. For RGB
     frames it records
     decoded header fields; when the grouped TinyUZ payload decodes to a static
     color, the hint also emits copy-paste `dry-run-rgb` and
     `compare-capture ... rgb` commands.
   - `capture-replay-plan` is the concise version of those hints: it emits
     de-duplicated copy-paste `dry_run_commands` and
     `compare_capture_commands`, while keeping per-frame exact argv entries in
     `items`.
   - `capture-protocol-report` is the evidence table for a whole capture. It
     aggregates receiver MACs, master MACs, channels, receiver types,
     device/fan counts, operation counts, PWM tuples, motherboard PWM values,
     RGB header fields, RGB decode statuses/static colors, and frame
     indexes by device and by operation. When the input is a USBPcap/tshark
     export it also aggregates per-device/per-operation VID/PID counts, endpoint
     counts, USB frame numbers, and relative-time spans, which is the evidence
     needed to decide which Linux endpoint should be used for live writes. It
     now emits `linux_live_write_targets` when a logical operation is observed
     on a concrete USB target; high-confidence RF writes should point at
     `0416:8040` with `write_endpoint=0x01` and `read_endpoint=0x81`. For
     RGB, `operations` counts logical LED sequences, while `rf_frame_operations`
     preserves raw repeated RF frame counts for audit.
   - Accepted inputs include one 64/65-byte packet per line, Wireshark-style
     hexdump blocks, nested tshark JSON fields such as `usb.capdata` and
     `usbhid.data`, raw `.pcapng`/`.pcap` via optional `tshark`, and JSON arrays
     of byte integers.
   - The analyzer recognizes 64-byte RF chunks, receiver list requests,
     receiver snapshots, master queries, PWM writes, PWM sync writes, RGB
     payload headers, bind/unbind frames, and inferred PWM mirror writes based
     on a prior receiver snapshot.
3. For first live writes, use only one MAC at a time and keep a conservative
   fan duty:
   - `python tools/lianli_wireless_probe.py safe-pwm-experiment --mac aa:bb:cc:dd:ee:ff --pwm 120 --output-dir .cache/lianli/pwm-experiment --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py safe-sync-experiment --mac aa:bb:cc:dd:ee:ff --output-dir .cache/lianli/sync-experiment --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py safe-pwm-mirror-experiment --mac aa:bb:cc:dd:ee:ff --output-dir .cache/lianli/pwm-mirror-experiment --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py safe-rgb-experiment --mac aa:bb:cc:dd:ee:ff --color 0,0,0 --led-count 132 --output-dir .cache/lianli/rgb-experiment --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py safe-rainbow-experiment --mac aa:bb:cc:dd:ee:ff --frame-count 24 --interval-ms 50 --output-dir .cache/lianli/rainbow-experiment --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py safe-bind-experiment --mac aa:bb:cc:dd:ee:ff --rx-type 3 --output-dir .cache/lianli/bind-experiment --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py safe-unbind-experiment --mac aa:bb:cc:dd:ee:ff --output-dir .cache/lianli/unbind-experiment --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-pwm.json live-pwm --mac aa:bb:cc:dd:ee:ff --pwm 120 --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-pwm-sync.json live-pwm-sync --mac aa:bb:cc:dd:ee:ff --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-pwm-mirror.json live-pwm-mirror --mac aa:bb:cc:dd:ee:ff --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-rgb-off.json live-rgb --mac aa:bb:cc:dd:ee:ff --color 0,0,0 --led-count 132 --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-rainbow.json live-rainbow --mac aa:bb:cc:dd:ee:ff --frame-count 24 --interval-ms 50 --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-bind.json live-bind --mac aa:bb:cc:dd:ee:ff --rx-type 3 --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-unbind.json live-unbind --mac aa:bb:cc:dd:ee:ff --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-lcd-control.json live-lcd-control --brightness 60 --rotation 180 --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py analyze-log .cache/lianli/live-pwm.json`
   - `python tools/lianli_wireless_probe.py diff-snapshots .cache/lianli/live-list-before.json .cache/lianli/live-list-after.json`
   - `python tools/lianli_wireless_probe.py summarize-experiments .cache/lianli`
4. Run a read-only GUI probe that calls `backend.list_devices()` and displays
   receiver MAC, master MAC, fan count, RPM, PWM, channel, and device type.
5. Use the GUI `联力无线` page for local USB scans, read-only receiver
   snapshots, and read-only master MAC queries. The page is separate from
   motherboard `hwmon` fan control and OpenRGB lighting.
6. Only after the read-only probe is stable, enable guarded GUI writes:
   - require the GUI write checkbox and exact `WRITE-LIANLI` token;
   - refuse PWM values below a conservative floor;
   - write one receiver MAC at a time;
   - immediately re-read snapshot and verify RPM/PWM changed as expected.
7. Keep default behavior read-only and require explicit enable before PWM/RGB
   writes.

## Local Artifacts Added

- `usb9_lcd.lianli.wireless`: pure Python protocol constants, sysfs discovery,
  snapshot parsing, PWM RF packet construction, bind/unbind packet
  construction, and a transport-injected `LianLiWirelessBackend`. It does not
  open USB devices unless a future transport is supplied, and it does not write
  hardware by default.
- `PyUsbEndpointTransport` and `create_pyusb_backend()`: optional live USB
  bridge for future hardware testing. The module import stays dependency-free;
  `pyusb` is required only when a live transport is instantiated.
- `build_static_rgb_payloads()`, `build_rainbow_rgb_payloads()`,
  `build_rgb_frame_payloads()`, `generate_rainbow_rgb_frames()`,
  `LianLiWirelessBackend.build_static_rgb_packets()`,
  `LianLiWirelessBackend.build_rainbow_rgb_packets()`, and
  `tinyuz_compress_literal()`: static RGB, multi-frame rainbow/custom RGB, and
  turn-off packet generation without touching hardware.
- `usb9_lcd.lianli.capture`: L-Connect/USBPcap text/JSON hex capture analyzer.
  It reassembles four 64-byte RF chunks into a 240-byte payload and classifies
  PWM, motherboard PWM sync, RGB, bind, and unbind operations. It accepts plain
  hex lines, Wireshark hexdump text, nested tshark JSON capture fields,
  byte-array JSON records, and raw `.pcapng`/`.pcap` through optional
  `tshark`. It also compares official capture frames against locally built PWM,
  motherboard PWM sync, RGB, bind, or unbind packets with separate exact and
  semantic match reports. RGB analysis reassembles multi-packet static color
  payloads, merges official first-payload retransmits into one logical
  sequence, and decodes TinyUZ literal/literal-line/back-reference streams into
  explicit colors when possible.
  This is intended to compare official L-Connect 3 traffic against the Linux
  packet builders.
- `usb9_lcd.lianli.artifact`: static artifact scanner for installer/binary
  files and extracted directories. It reports file type, SHA256, entropy
  sample, NSIS header metadata when present, candidate decompression probes for
  NSIS payloads, high/medium confidence matches for known USB IDs, product
  strings, official HID control-code fragments, SLV3 sensor/theme asset models,
  extracted asset path clues, and the wireless LCD DES key. It also has
  `diff_artifact_files()` for comparing official versions by common
  prefix/suffix, fixed-block reuse, static clue deltas, changed-region matches,
  and basic magic-header validation, plus
  `extract_hid_js_commands()` for turning minified L-Connect JS assets into
  structured AL V2 / SL V2 product ID, function, and HID report-template maps,
  and `extract_wireless_js_clues()` for summarizing wireless/USB/IPC clue
  contexts in official JS bundles. The wireless JS extractor now also emits
  structured `ipc_events`, `settings_keys`, and `capture_hints`, so Windows-side
  USBPcap actions can be labeled from the official Electron message queue and
  settings pipe keys without treating those interface hints as confirmed RF
  protocol proof. `windows-capture-runbook` and `capture-gap-report` surface the
  matching hints as `interface_focus` per scenario; target-version hints are
  preferred, with a lower-confidence matrix-summary fallback when only another
  analyzed L-Connect asset version contains the JS entry points.
  High-entropy data still gets warnings where isolated 4-byte VID/PID hits may
  be accidental.
- `usb9_lcd.lianli.changelog`: official L-Connect 3 changelog analyzer. It
  accepts the live LIAN LI URL or saved HTML/text, extracts L3 versions, release
  dates, download links, wireless/RF/binding/fan-control evidence lines, and
  recommended version candidates for the next installer-download/static-diff
  pass.
- `tools/lianli_wireless_probe.py`: read-only sysfs probe for known LIAN LI
  wireless VID/PID pairs. It also supports `dry-run-pwm`, `dry-run-pwm-sync`,
  `dry-run-pwm-mirror`, `dry-run-master-query`, `dry-run-bind`,
  `dry-run-unbind`, `dry-run-rgb`, `dry-run-rainbow`, and `dry-run-lcd`
  packet/header summaries without touching USB devices. Static RGB and
  rainbow dry-runs both accept `--led-count` overrides for exact replay.
  The tool also supports artifact/capture/changelog analysis commands, including
  `diff-artifacts`, `extract-hid-js`, `extract-wireless-js`, and `analyze-changelog`, plus `udev-rules` for Linux
  permissions, `live-list` for a read-only PyUSB receiver snapshot, and
  `live-master` for a read-only master MAC query through the RF sender.
  `live-lcd-info` performs read-only wireless LCD handshake / firmware queries.
  Guarded live write commands (`live-pwm`, `live-pwm-sync`, `live-pwm-mirror`,
  `live-rgb`, `live-rainbow`, `live-bind`, `live-unbind`, and
  `live-lcd-control`) require the
  exact `--confirm WRITE-LIANLI` token. Receiver writes re-read the receiver
  snapshot after sending. The global `--save-json PATH` option writes the same
  JSON payload shown on stdout to disk for hardware validation logs.
  `analyze-log` inspects one saved live write JSON, reports receiver field
  changes, and adds a structured `expected_effect` check for PWM, motherboard
  PWM mirror, motherboard PWM sync, bind, and unbind logs so a hardware run is
  not treated as successful merely because some receiver field changed.
  `diff-snapshots` compares two saved receiver
  snapshots by MAC and reports added, removed, changed, and unchanged receivers.
  `summarize-experiments` recursively scans a directory of saved JSON logs and
  groups live write outcomes by operation, changed fields, receiver MACs, and
  validation errors so repeated hardware experiments can be compared quickly.
  It now also summarizes full `validate-readonly` runs and top-level
  `safe-*-experiment` JSON payloads into a `hardware_validation` status.
  `safe-pwm-experiment` is the preferred first write test: it performs one
  guarded single-MAC PWM write and saves before, write, after, analysis, and
  summary JSON files in one directory. `safe-sync-experiment` performs the same
  capture flow for motherboard PWM sync and records the expected magic PWM tuple
  `[6, 6, 6, 6]` when sync is enabled. `safe-pwm-mirror-experiment` reads the
  decoded motherboard PWM from the before snapshot, writes `[pwm, pwm, pwm, pwm]`
  as direct RF PWM, and saves the same before/write/after/analysis/summary logs.
  `safe-rgb-experiment` and `safe-rainbow-experiment` do the same for static
  RGB and generated rainbow writes and explicitly mark
  `visual_confirmation_required` when the receiver snapshot does not change,
  because RGB effects may only be observable on the LEDs. Static RGB safe/live
  writes also accept `--led-count` for receiver variants whose snapshot hint
  disagrees with the generic mapping. `safe-bind-experiment` performs the same
  capture flow for an unbound receiver, infers the master MAC when possible,
  and refuses receivers that are already bound. `safe-unbind-experiment`
  mirrors it for bound receivers and refuses receivers that are already unbound.
  `validate-readonly` runs scan, live-list, live-master, and optionally
  live-lcd-info, saving each step as separate JSON under an output directory.
  `receiver-validation-bundle` is now the preferred post-plug command for real
  L-Wireless receivers: it saves scan/readiness/live-list/live-master,
  nested readonly validation, preflight, and write-gate JSON logs into one
  evidence directory. The command also writes `receiver-validation-bundle.json`
  and `summary.json` itself, and mirrors `hardware_validation` plus
  `receiver_control_next_action` in stdout so the post-plug decision can be read
  without running a second command. `summarize-experiments` recognizes that bundle and reports
  `receiver_validation_bundles` plus `hardware_validation.status`, including
  `readonly-and-write-gate-ready` when the write-gate has passed but no guarded
  write experiment has been run yet. The same summary now emits
  `receiver_control_next_action`, which turns the bundle plus `live-list`
  snapshot into an explicit decision: do not write, collect more evidence, or
  run exactly one conservative `safe-pwm-experiment` command for a bound
  receiver MAC. That decision now also consumes `receiver_identity_consistency`;
  if top-level/nested readonly receiver snapshots or Master queries conflict,
  `summarize-experiments` reports `receiver-identity-conflict` and withholds the
  safe PWM command until the validation bundle is recaptured.
  `receiver-evidence-report <hardware-log-dir>` audits that same directory as a
  shareable evidence package: it checks required post-plug JSON files, records
  file sizes and SHA256 hashes, mirrors the hardware validation state, and keeps
  the next recommended command next to the manifest. It now emits
  `receiver_identity_consistency`, cross-checking `live-list.json`,
  `readonly/live-list.json`, and `live-master.json` for receiver MAC,
  master MAC, channel, rx_type, device_type, and fan_count mismatches. A
  mismatch is surfaced as `receiver-identity-conflict` before the log set can
  be treated as a safe-write candidate. The same audit now catches receiver MAC
  set changes between top-level and nested readonly snapshots, plus conflicting
  Master MACs between top-level and nested `live-master` logs. The report also
  audits the recommended or already-created safe PWM output directory, so the
  evidence checklist follows the MAC-specific `experiments/safe-pwm-<mac>` path
  emitted by `receiver_control_next_action`. `receiver-observation <safe-pwm-dir>` creates
  the matching manual `observation.json` record for visible/audible fan
  response. When machine logs are complete but no observation exists, the
  recommended observation command now carries the same target MAC and expected
  PWM tuple from `live-pwm.json`, reducing the chance that a manual observation
  is recorded against the wrong fan group. Evidence reports now distinguish
  internally consistent complete machine logs that still need observation
  (`write-evidence-needs-observation`) from confirmed control evidence
  (`write-evidence-confirmed`). Each safe fan write set now includes
  `machine_consistency`, checking that the live write JSON, before/after
  snapshots, and matching analysis JSON agree on the target and expected PWM
  effect. The evidence report now recognizes direct PWM, motherboard PWM sync,
  motherboard PWM mirror, static RGB, generated rainbow RGB, bind, and unbind
  experiment directories instead of assuming every write is `live-pwm.json`.
  RGB evidence is treated as machine-complete but visually unverified when the
  receiver snapshot is unchanged and `visual_confirmation_required` is set, so
  lighting tests are not incorrectly marked as failed before manual observation.
  Conflicting machine logs become
  `write-evidence-machine-conflict`, so a manual observation cannot promote a
  mixed log set to validated control.
  `receiver_control_next_action` now consumes those write evidence sets too:
  complete machine logs without `observation.json` return
  `write-validation-needs-observation`; a visually confirmed single-target PWM
  write returns `ready-for-safe-lighting-validation` and recommends exactly one
  guarded lighting experiment, starting with `safe-rgb-experiment --color
  0,0,0`. The same payload exposes deferred bind/unbind commands under
  `safe_expansion_candidate.deferred_pairing_commands`, but they are kept out of
  the primary recommendation because pairing changes receiver ownership state.
  Once both static RGB and generated rainbow writes are visually confirmed, the
  next-action status becomes `ready-for-pairing-risk-review`; the primary
  recommendation remains regenerating `receiver-evidence-report`, while the
  state-changing bind/unbind command stays in the deferred pairing list for
  explicit operator review. `receiver-pairing-risk-report <hardware-log-dir>`
  is the no-write audit for that final step: it reuses the evidence report and
  next-action payload, emits a checklist for identity consistency, completed
  PWM/RGB/rainbow observations, pending/conflicting write evidence, and only
  returns `ready-for-manual-pairing-review` when those blockers are clear.
  Negative, malformed, or ambiguous observations now produce explicit
  conflict/invalid/unclear statuses instead of being treated as merely missing
  observation. Confirmed observations also have to match the `live-pwm.json`
  target MAC, and recorded PWM values are compared against `pwm_values`;
  mismatches are treated as observation conflicts rather than validated control.
- `LianLiWirelessPage`: GUI page for safe LIAN LI wireless probing. It exposes
  sysfs scanning, live receiver snapshots, live master MAC queries, and a
  `只读验证` action that saves scan, live-list, live-master, and live-lcd-info
  JSON files under `.cache/lianli/gui-validation`. It also exposes guarded
  single-MAC PWM / motherboard PWM sync / motherboard PWM mirror / RGB-off
  writes, a guarded `安全 PWM 实验` action that saves
  before/write/after/analysis/summary JSON files under
  `.cache/lianli/gui-pwm-experiment`, a guarded `安全 Sync 实验` action that saves
  motherboard PWM sync before/write/after/analysis/summary JSON under
  `.cache/lianli/gui-sync-experiment` and records `[6, 6, 6, 6]` as the expected
  sync tuple, a guarded `安全 Mirror 实验` action that saves the decoded
  motherboard PWM mirror bundle under `.cache/lianli/gui-pwm-mirror-experiment`,
  a guarded `安全 RGB 实验` action that saves the same experiment bundle under
  `.cache/lianli/gui-rgb-experiment` and marks
  visual-confirmation-only cases, guarded `安全 Bind 实验` and
  `安全 Unbind 实验` actions that save the same experiment bundle under
  `.cache/lianli/gui-bind-experiment` and
  `.cache/lianli/gui-unbind-experiment`, wireless LCD info reads, and guarded
  wireless LCD brightness/rotation
  control. The page defaults to read-only and only enables write buttons after
  the user checks the write toggle and enters `WRITE-LIANLI`. Its `保存快照`
  button writes the current displayed JSON to a user-selected file. Its
  `分析日志` and `对比快照` buttons reuse the same CLI analysis code to inspect
  saved live-write logs or compare two saved snapshots directly inside the GUI.
  Its `汇总实验` button summarizes a selected log directory with the same
  aggregation logic as `summarize-experiments`; when the summary contains
  `receiver_control_next_action`, the page shows the Chinese next-step decision
  in the experiment guide and fills the target MAC field if exactly one ready
  receiver candidate is available.
- `tests/test_lianli_wireless.py`: protocol parser/builder tests.
- `tests/test_lianli_lcd.py`: wireless LCD command/header builder tests.
- `tests/test_lianli_probe_tool.py`: CLI probe/dry-run regression tests.
- `pyproject.toml` optional extra `lianli`: installs `pyusb>=1.2` and
  `pycryptodomex>=3.20` for live USB receiver tests and encrypted wireless LCD
  header generation without making them default GUI dependencies.

## Validation Log

- `pytest tests/test_lianli_wireless.py -q`: passed.
- `pytest tests/test_lianli_wireless.py tests/test_lianli_probe_tool.py -q`:
  passed; current focused suite is `128 passed`.
- `analyze-artifact` tests cover high-confidence ASCII/UTF-16 product strings,
  complete textual USB IDs, little-endian VID/PID bytes, SHA256/file-type
  reporting, NSIS header detection, NSIS raw-LZMA probe success on a synthetic
  payload, official HID command-code snippets, SLV3 sensor/theme model
  signatures, warning suppression for low-entropy meaningful data, directory
  tree aggregation, asset path clue aggregation, structured JS HID command
  extraction, wireless JS clue extraction, large-file skip reporting, and CLI
  JSON output.
- Capture analyzer tests cover plain RF chunk hex, receiver/master
  request/response records, Wireshark hexdump text, nested tshark JSON fields
  (`usb.capdata`, `usbhid.data`, and `data`), byte-array JSON records,
  `.pcapng` extraction through `tshark`, and clear `.pcapng` guidance when
  `tshark` is unavailable.
- `compare-capture` tests cover exact packet matches, semantic PWM matches
  with different sequence bytes, inferred PWM mirror traces from prior receiver
  snapshots, mismatch reporting, and CLI comparison against locally generated
  motherboard PWM sync, PWM mirror, and multi-frame rainbow packets. CLI tests
  also cover explicit `--pwm-values` tuples, bind comparison with
  `--current-pwm`, RGB/rainbow `--led-count` overrides, and `capture-replay-plan`
  command generation for PWM mirror and generated rainbow RGB captures.
  `capture-protocol-report` tests
  cover aggregation of device evidence, operation counts, PWM tuples, decoded
  motherboard PWM values, decoded literal/literal-line/back-reference RGB
  payloads, and the split between logical RGB sequence counts and raw repeated
  RF frame counts.
- `python tools/lianli_wireless_probe.py udev-rules`: prints rules for
  `0416:8040`, `0416:8041`, `0416:7372`, `04fc:7393`, and `1cbe:0006`.
- `python tools/lianli_wireless_probe.py dry-run-pwm-sync`: builds four RF
  packets; first packet contains `06060606` at the PWM field.
- `python tools/lianli_wireless_probe.py dry-run-pwm --pwm-values 80,90,100,110`:
  builds direct PWM packets with non-uniform PWM bytes `505a646e`.
- `python tools/lianli_wireless_probe.py dry-run-pwm-mirror --snapshot-hex "10 00 0a 0a"`:
  builds four RF packets from decoded motherboard PWM `127`; first packet
  contains `7f7f7f7f` at the PWM field.
- `python tools/lianli_wireless_probe.py compare-capture ... pwm-mirror --snapshot-hex "10 00 0a 0a"`:
  builds the expected direct PWM mirror packets, while observed official traces
  are annotated as `live-pwm-mirror` when the same decoded snapshot PWM appears
  immediately before the RF write sequence.
- `python tools/lianli_wireless_probe.py dry-run-master-query --channel 8`:
  builds the 64-byte `1108...` master MAC query request without writing USB.
- `python tools/lianli_wireless_probe.py dry-run-bind --master-mac 10:20:30:40:50:60 --rx-type 3`:
  builds four RF packets; first packet uses outer receiver type `00` for an
  unbound receiver and carries the desired master MAC / slot in the payload.
- `python tools/lianli_wireless_probe.py dry-run-unbind`: builds four RF
  packets; first packet zeroes the master MAC in the payload.
- `python tools/lianli_wireless_probe.py dry-run-lcd brightness --value 65 --timestamp-ms 16909060`:
  builds a 504-byte wireless LCD plaintext header; first bytes are
  `0e 00 1a 6d 04 03 02 01 41`.
- `python tools/lianli_wireless_probe.py dry-run-lcd push-jpg --payload-size 6 --timestamp-ms 1`:
  builds a wireless LCD JPG command summary with command `101`, packet length
  `102400`, and big-endian payload length `00000006`.
- `live-lcd-info` is covered by fake-backend tests for handshake and firmware
  parsing; real TL LCD wireless receiver verification is still pending hardware.
- `live-lcd-control --brightness 65 --rotation 180 --confirm WRITE-LIANLI` is
  covered by fake-backend tests for guarded brightness and rotation writes; real
  TL LCD wireless receiver verification is still pending hardware.
- `validate-readonly --output-dir ...` is covered by fake-backend tests and
  writes per-step JSON logs for scan, receiver snapshot, master query, and LCD
  info.
- `python tools/lianli_wireless_probe.py live-pwm --mac ... --pwm 120 --confirm WRITE-LIANLI`:
  guarded real USB write path is covered by fake-backend tests; real receiver
  verification is still pending hardware.
- `python tools/lianli_wireless_probe.py safe-pwm-experiment --mac ... --pwm 120 --confirm WRITE-LIANLI`:
  covered by fake-backend tests for before/write/after capture, analysis, and
  summary generation; real receiver verification is still pending hardware.
- `python tools/lianli_wireless_probe.py live-pwm-mirror --mac ... --confirm WRITE-LIANLI`:
  guarded real USB write path is covered by fake-backend tests; it refuses to
  write if the receiver snapshot has no valid motherboard PWM or if the decoded
  PWM is below `--min-pwm`.
- `python tools/lianli_wireless_probe.py safe-pwm-mirror-experiment --mac ... --confirm WRITE-LIANLI`:
  covered by fake-backend tests for motherboard PWM extraction, before/write/after
  capture, expected `[pwm, pwm, pwm, pwm]` analysis, and summary generation; real
  receiver verification is still pending hardware.
- `python tools/lianli_wireless_probe.py safe-sync-experiment --mac ... --confirm WRITE-LIANLI`:
  covered by fake-backend tests for before/write/after capture, `[6, 6, 6, 6]`
  PWM sync analysis, and summary generation; real motherboard PWM sync
  verification is still pending hardware.
- `python tools/lianli_wireless_probe.py safe-rgb-experiment --mac ... --color 0,0,0 --confirm WRITE-LIANLI`:
  covered by fake-backend tests for before/write/after capture, analysis,
  summary generation, and explicit visual-confirmation marking when receiver
  snapshot fields do not change; real LED verification is still pending
  hardware.
- `python tools/lianli_wireless_probe.py live-rainbow --mac ... --frame-count 24 --interval-ms 50 --confirm WRITE-LIANLI`:
  guarded live generated-rainbow RGB write path is covered by fake-backend
  tests; real LED verification is still pending hardware.
- `python tools/lianli_wireless_probe.py safe-rainbow-experiment --mac ... --frame-count 24 --interval-ms 50 --confirm WRITE-LIANLI`:
  covered by fake-backend tests for before/write/after capture, analysis,
  summary generation, LED-count reporting, and explicit visual-confirmation
  marking; real LED verification is still pending hardware.
- `python tools/lianli_wireless_probe.py safe-bind-experiment --mac ... --rx-type 3 --confirm WRITE-LIANLI`:
  covered by fake-backend tests for unbound receiver precondition, master MAC
  inference, before/write/after capture, bind-state analysis, and summary
  generation; real receiver pairing verification is still pending hardware.
- `python tools/lianli_wireless_probe.py safe-unbind-experiment --mac ... --confirm WRITE-LIANLI`:
  covered by fake-backend tests for bound receiver precondition,
  before/write/after capture, unbind-state analysis, and summary generation;
  real receiver unpairing verification is still pending hardware.
- GUI `联力无线` page fake-backend tests cover live snapshot rendering, write
  unlock gating, single-MAC PWM dispatch, LCD info reads, and guarded LCD
  brightness/rotation dispatch.
- GUI `安全 PWM 实验` test covers the guarded before/write/after capture,
  analysis, summary generation, and rendered result on the `联力无线` page.
- GUI `安全 Sync 实验` test covers guarded motherboard PWM sync capture,
  `[6, 6, 6, 6]` expected tuple reporting, analysis, summary generation, and
  rendered result on the `联力无线` page.
- GUI `镜像主板 PWM` and `安全 Mirror 实验` tests cover snapshot motherboard PWM
  extraction, guarded direct mirror write, before/write/after capture, expected
  `[pwm, pwm, pwm, pwm]` analysis, summary generation, and rendered result on the
  `联力无线` page.
- GUI `安全 RGB 实验` test covers guarded RGB-off capture, analysis, summary
  generation, visual-confirmation marking, and rendered result on the
  `联力无线` page.
- GUI `安全 Bind 实验` test covers guarded unbound-receiver pairing capture,
  master MAC inference, bind-state analysis, summary generation, and rendered
  result on the `联力无线` page.
- GUI `安全 Unbind 实验` test covers guarded bound-receiver unpairing capture,
  bind-state analysis, summary generation, and rendered result on the
  `联力无线` page.
- CLI `--save-json` test verifies that the saved file matches stdout JSON.
- CLI `analyze-log` and `diff-snapshots` tests verify PWM/RPM/bind-state style
  field changes are reported by MAC. `analyze-log` also verifies structured
  expected-effect matching and mismatch reporting for saved write logs.
- CLI `summarize-experiments` tests verify recursive JSON aggregation,
  per-operation changed/unchanged counts, field-change counts, validation
  errors, and invalid JSON reporting.
- GUI `保存快照`, `分析日志`, and `对比快照` tests verify valid JSON is written
  to disk and analysis results render in the `联力无线` page.
- GUI `汇总实验` test verifies a selected LIAN LI log directory is aggregated
  and rendered in the `联力无线` page.
- TinyUZ cross-check against `uwscli`: `452 452 True` for a 132-LED black frame.
- `python tools/lianli_wireless_probe.py dry-run-rgb --color 0,0,0`: builds
  28 RF chunks for a 132-LED static-off write; this includes the official
  `uwscli` behavior of sending the first LED payload four times.
- `python tools/lianli_wireless_probe.py dry-run-rgb --color 0,0,0 --led-count 12`:
  builds 20 RF chunks for a 12-LED static-off write and records
  `led_count=12` in the JSON summary.
- `python tools/lianli_wireless_probe.py dry-run-rainbow --frame-count 3 --interval-ms 40 --led-count 132`:
  builds 44 RF chunks for a 132-LED, 3-frame rainbow write; capture analysis
  decodes it as one logical `live-rgb` sequence with `frame_count=3`.
- `python tools/lianli_wireless_probe.py capture-replay-plan l-connect-rainbow-capture.txt`:
  emits `dry-run-rainbow` and `compare-capture ... rainbow` commands when the
  decoded RGB payload matches the local generated rainbow sequence.
- `python tools/lianli_wireless_probe.py windows-capture-plan --version 2.1.17 --installer .cache/lianli/downloads/20260213-L-Connect-3-x64-v2.1.17-2f7c3856.exe --capture-base lianli-v2117`:
  passed; saved `.cache/lianli/windows-capture-plan-v2.1.17.json`, reports
  that this host has `tshark` available but no Wine/Docker/QEMU/VirtualBox,
  recommends VM USB passthrough for protocol capture, records the v2.1.17
  installer SHA256, lists the LIAN LI USB IDs to pass through, and emits
  scenario-specific analyzer/compare commands for baseline, direct PWM,
  motherboard PWM sync, RF rebind, sort quick-sync, static/off lighting, and
  generated rainbow lighting.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/usb-capture-readiness-current.json usb-capture-readiness`:
  passed; reports `status=no-l-wireless-hardware`, missing `0416:8040` and
  `0416:8041`, `tshark=true`, `usbipd=true`, no Wine/Docker/QEMU/VirtualBox,
  and records a non-fatal usbmon permission error for
  `/sys/kernel/debug/usb/usbmon`.
- `python tools/lianli_wireless_probe.py capture-transport-report <capture>`:
  added and covered by tests for `tshark -T json` exports and raw `.pcapng`
  input through `tshark`; reports payload field counts, frame numbers,
  VID/PID-derived `usb_device_counts`, endpoint/direction summaries,
  `known_usb_devices`, `lianli_usb_targets`, first protocol-looking records,
  and next analyzer commands before RF reassembly, including
  `capture-timeline-report` when protocol-shaped records exist.
- `python tools/lianli_wireless_probe.py protocol-signatures --led-count 12 --rainbow-frames 3 --interval-ms 40`:
  added and covered by direct/CLI tests; emits a searchable local signature
  catalog for receiver list, master query, PWM, motherboard sync, PWM mirror,
  bind/unbind, static RGB, RGB off, and generated rainbow operations.
- `python tools/lianli_wireless_probe.py capture-signature-match <capture> --led-count 12 --rainbow-frames 3 --interval-ms 40`:
  added and covered by direct/CLI tests; matches an exported capture against
  the local signature catalog, including non-RF request packets and RF semantic
  matches, then emits the matched operations and capture-specific compare
  commands. It also reports MAC-independent `shape_match` evidence so a real
  receiver MAC does not hide known operation shapes, and emits
  `observed_commands` with capture-derived `--mac`, `--master-mac`, arbitrary
  direct PWM tuple, RGB color, or rainbow parameters when the frame decoder can
  infer them.
- `python tools/lianli_wireless_probe.py capture-triage-report <capture> --led-count 12 --rainbow-frames 3 --interval-ms 40`:
  added and covered by direct/CLI tests; combines transport, signature,
  replay, and protocol reports into one compact JSON first-pass verdict for a
  single official capture/export. The recommended command chain now includes
  `capture-timeline-report` so an interesting trace can immediately be checked
  against USBPcap frame order.
- `python tools/lianli_wireless_probe.py capture-protocol-report <capture>`:
  now preserves USBPcap/tshark metadata when reading a capture file directly.
  Device and operation reports include `usb_device_counts`,
  `usb_endpoint_counts`, `usb_target_counts`, `usb_frame_numbers`, and
  relative-time start/end/span fields when the export contains VID/PID,
  endpoint, frame, and time fields. Operation reports and the top-level report
  also include `linux_live_write_targets`, which turns same-packet VID/PID +
  endpoint evidence into a Linux/PyUSB target hint such as
  `PyUsbEndpointTransport(vid=0x0416, pid=0x8040, write_endpoint=0x01,
  read_endpoint=0x81)`.
- `capture-triage-report` now lifts those `linux_live_write_targets` into its
  top-level summary. If a high-confidence `0416:8040` sender write is present,
  `next_steps[0]` names the Linux write endpoint directly and
  `recommended_commands` includes `validate-readonly` plus the guarded safe PWM
  experiment for the capture-derived receiver MAC.
- `python tools/lianli_wireless_probe.py capture-set-report <capture-dir> --capture-base lianli-v2117`:
  added and covered by direct/CLI tests; audits a planned USBPcap directory
  against the Windows capture-plan scenario list, aggregates USB sender/receiver
  evidence, RF/signature operations, and high-confidence Linux live-write
  targets. It also emits `linux_validation_plan.commands` so the capture set
  can be turned into a Linux readiness/read-only/safe-write/log-summary
  checklist, while still listing missing scenarios needed to complete official
  evidence. Add `--experiment-dir <linux-log-dir>` after running
  `validate-readonly` and guarded `safe-*-experiment` commands to include the
  existing `hardware_validation` result in the same report. The report also
  emits `linux_control_matrix`, which evaluates each control surface separately
  and recommends matching guarded experiments such as `safe-sync-experiment`,
  `safe-pwm-mirror-experiment`, `safe-rgb-experiment`, or
  `safe-bind-experiment` when the corresponding Windows evidence and Linux
  sender endpoint are available. Static RGB/off and generated rainbow now have
  separate planned capture scenarios so a rainbow capture cannot accidentally
  satisfy the static RGB requirement. It also emits `linux_interface_contract`, a
  compact implementation contract for wiring the proven endpoints and packet
  builders into a Linux controller without re-reading the full protocol report.
  The report now includes `cross_scenario_deltas`, which indexes decoded RF
  operations, matched signatures, and protocol parameters by planned scenario.
  It highlights scenario-unique operations such as
  `direct-fan-speed -> live-pwm` and concrete values such as
  `live-pwm.pwm_values=77,88,99,111`,
  `live-rgb.rgb_static_colors=#000000`, or
  `live-rgb.rgb_rainbow_generated=132,3,40,2`. Each scenario delta now also
  emits `unique_parameter_labels` and folds those labels into `next_focus`, so
  a direct PWM trace points at both `live-pwm` and the exact PWM tuple to replay
  or compare. Use this after a Windows USBPcap capture batch to quickly spot
  which setting change introduced a protocol operation or parameter before
  diving into `capture-timeline-report`. The same focused delta summary is now
  propagated into `linux-interface-contract`, `linux-control-manifest`,
  `linux-control-preflight`, and `linux-control-action-plan`; each operation
  entry carries its relevant `protocol_deltas` so GUI/action-plan code can show
  the exact capture-derived parameter evidence next to the safe command.
- `python tools/lianli_wireless_probe.py capture-gap-report <capture-dir> --capture-base lianli-v2117`:
  added as a smaller companion to `capture-set-report`. It keeps the full
  capture-set report as the source of truth but returns only actionable gaps:
  missing or partial scenario captures, operation-level blockers, the next
  capture to run, and proof gates for baseline, PWM, lighting, and pairing.
  It now also carries `capture_note_context_summary`, so the compact report and
  the validation gate both see sidecar target conflicts without opening the
  full capture-set report. Scenario gaps also include
  `contextual_planned_linux_commands`: when a sidecar has a consistent target
  context, planned no-write `compare-capture` commands are emitted with
  receiver MAC, master MAC, channel, rx_type, device_type, and LED count filled.
  It also carries `capture_note_operator_summary`; unconfirmed sidecar actions
  remain visible in the compact gap report instead of being hidden in the full
  capture-set JSON.
  Passing `--artifact-dir <artifact-report-dir>` adds
  `artifact_capture_context`, `artifact_capture_changelog_score`, and per-scenario
  `changelog_focus`, allowing v2.1.17 RF bind/rebind and sort/quick-sync notes
  from the official changelog to move those captures earlier without putting
  pairing before lower-risk validation.
  With no captures it prioritizes `lianli-v2117-00-baseline.pcapng`; with
  baseline/direct PWM already present it moves on to motherboard PWM sync before
  lighting, sort/quick-sync, and RF rebind.
- `python tools/lianli_wireless_probe.py windows-capture-runbook <capture-dir> --capture-base lianli-v2117 --artifact-dir <artifact-report-dir>`:
  added as the operator-facing version of the capture plan. It combines the
  planned Windows USBPcap scenarios with the current `capture-set-report`
  audit, so each task carries its current status, priority, risk, capture path,
  exact Windows actions, expected evidence, acceptance checks, per-file Linux
  analyzer commands, a manual `tshark -T fields` export command, and a
  `lianli-windows-capture-note/v1` sidecar template. Use it before a Windows VM
  capture session so the next missing file, its target context note, and its
  post-capture verification commands are explicit. `capture-set-report` reads
  `<capture-stem>.notes.json` sidecars back into each scenario and ignores them
  as capture inputs, so target MAC, master MAC, channel, rx_type, device_type,
  fan count, LED count, expected operation parameters, and operator observations
  remain machine-readable. When those fields are present, the runbook keeps the
  normal triage/protocol commands and fills target plus parameter placeholders
  in the per-scenario `compare-capture` command.
- `python tools/lianli_wireless_probe.py lianli-validation-gate --capture-dir <capture-dir> --hardware-dir <hardware-log-dir> --artifact-dir <artifact-report-dir> --capture-base lianli-v2117`:
  added as the top-level no-write readiness report. It composes
  optional `artifact-evidence-matrix`, `capture-gap-report`, `receiver-evidence-report`, and
  `receiver-pairing-risk-report` into one checklist with blockers, warnings,
  stage status, and next commands. Use this after plugging in the receiver so
  the operator does not have to compare separate official-static, Windows
  capture, Linux hardware, and pairing-risk JSON files by hand. If
  `--artifact-dir` is omitted, the gate still works from capture and hardware
  evidence only. A sidecar target conflict is reported as
  `capture-note-target-context` and makes the gate status
  `needs-capture-note-context-fix`. Unconfirmed sidecar actions are reported as
  a `capture-note-operator-status` warning.
- `python tools/lianli_wireless_probe.py linux-interface-contract <capture-dir> --capture-base lianli-v2117 --experiment-dir <linux-log-dir>`:
  added and covered by direct/CLI tests; exports the stable
  `lianli-linux-interface-contract/v1` implementation contract directly. Use
  this when the GUI or a Linux controller only needs the PyUSB endpoints,
  packet builder/send method names, required runtime fields, validated/ready
  operation lists, and the next safe validation commands.
- `python tools/lianli_wireless_probe.py linux-control-manifest <capture-dir> --capture-base lianli-v2117 --experiment-dir <linux-log-dir>`:
  added and covered by direct/CLI tests; exports
  `lianli-linux-control-manifest/v1`, a GUI/backend-oriented manifest derived
  from the interface contract. Each operation carries capability, readiness,
  evidence, input schema, backend method names, safety requirements, and
  operation-specific commands. The manifest also includes udev permission
  hints and keeps all live writes disabled by default.
- `python tools/lianli_wireless_probe.py linux-control-preflight <capture-dir> --capture-base lianli-v2117 --experiment-dir <linux-log-dir>`:
  added and covered by direct/CLI tests; exports
  `lianli-linux-control-preflight/v1`, combining the control manifest with
  current Linux USB visibility and `/dev/bus/usb` access checks. It reports
  hardware status, permission status, per-operation required VID/PID,
  preflight status, ready operation names, blockers, and next commands. Use
  `--sys-root` and `--dev-root` for VM/container test fixtures.
- Capture-set, interface-contract, manifest, preflight, and action-plan outputs
  now carry runtime target context where the Windows capture proves it:
  `target_macs`, `channels`, `rx_types`, `master_macs`, and structured
  `runtime_contexts`. A complete context contains `mac`, `channel`, and
  `rx_type`; when a baseline receiver snapshot is present in the capture set,
  the same context also carries `device_type`, `fan_count`, `pwm_values`,
  `fan_rpm`, `command_sequence`, and `raw_hex` for deterministic packet dry-runs.
- `python tools/lianli_wireless_probe.py linux-control-action-plan <capture-dir> --capture-base lianli-v2117 --experiment-dir <linux-log-dir>`:
  added and covered by direct/CLI tests; exports
  `lianli-linux-control-action-plan/v1`, an ordered action list that a GUI can
  show without interpreting the full preflight payload. It separates udev
  setup, readonly validation, guarded safe experiments, and missing Windows
  capture evidence. Live-write actions keep the confirmation token and USB
  write flags in the action payload. Ready live-write actions now prepend
  no-write packet preview/compare commands to their command list and expose the
  same commands separately as `pre_write_validation_commands`; these commands
  use the capture-derived target id and official capture path so a GUI can make
  “compare official bytes before WRITE-LIANLI” a first-class step. The same
  live-write action now includes a structured `pre_write_validation` policy with
  `minimum_required_match=exact-match`, `required_write_gate_status=pass`, and
  per-compare expected results, so the GUI does not need to infer write safety
  from command strings. If `--experiment-dir` contains saved
  `linux-control-packet-compare` JSON logs, action-plan now attaches matching
  logs as `pre_write_validation.observed_results` and reports
  `validation_status` such as `needs-run`, `passed`,
  `refresh-live-snapshot`, or `failed`. Top-level
  `guarded_write_readiness` summarizes those per-action gates into
  `guarded-write-ready`, `needs-pre-write-validation`,
  `refresh-live-snapshot`, or failed/incomplete states for GUI control flow.
  The action-level `execution` block exposes `next_command`,
  `write_command`, `write_command_enabled`, and
  `blocked_by_pre_write_validation`, while top-level `next_commands` gives the
  GUI a ready-to-run ordered list. For `refresh-live-snapshot`, `next_command`
  is the save-json `live-list-refresh.json` command, followed by the packet
  preview/compare commands before any safe write command can be enabled. After the refreshed `live-list` JSON is
  saved under the same `--experiment-dir`, `live_snapshot_context` feeds the
  latest target state into the target registry, and action execution moves to
  `needs-recompare-after-refresh` so the GUI can run packet preview/compare
  immediately instead of asking for another snapshot.
  RGB validation commands generated by action-plan now also preserve the GUI /
  CLI lighting defaults: static RGB preview/compare commands include
  `--led-count` and `--effect-index`, while generated-rainbow commands also
  include `--frame-count` and `--interval-ms`. This avoids validating wireless
  lighting packets with the 12-LED test default when the actual fan chain needs
  a different LED count.
  Missing
  capture actions now also include structured Windows action steps, expected
  evidence, and post-capture analyzer/compare commands for the missing scenario
  file, including the separate generated-rainbow capture file.
- `python tools/lianli_wireless_probe.py linux-control-write-gate <capture-dir> --capture-base lianli-v2117 --experiment-dir <linux-log-dir>`:
  added as the GUI-facing write gate summary. It wraps action-plan and exports
  `lianli-linux-control-write-gate/v1` with a single top-level status such as
  `needs-packet-compare`, `refresh-live-snapshot`, `write-enabled`, or
  `blocked-by-preflight`. Each safe write action is reduced to the fields the
  GUI needs before enabling a button: `ready_for_guarded_write`,
  `write_command_enabled`, `next_command`, `required_before_write`,
  `confirmation_token`, exact-compare counts, source coverage, and an
  aggregated `target_state` summary. This keeps the real hardware workflow
  explicit: no `WRITE-LIANLI` command is surfaced as enabled until preflight,
  exact packet compare, and receiver-state gates agree.
- `python tools/lianli_wireless_probe.py linux-control-target-registry <capture-dir> --capture-base lianli-v2117 --experiment-dir <linux-log-dir>`:
  added and covered by direct/CLI tests; exports
  `lianli-linux-control-target-registry/v1`, a compact target registry derived
  from the action plan. It groups capture-derived runtime contexts by
  MAC/channel/rx_type, reports whether packet dry-runs are ready, lists ready
  operations, keeps missing live snapshot fields explicit, and emits a
  `WirelessDeviceInfo` kwargs template. If the capture set has baseline snapshot
  state, the template is filled with the captured raw receiver record and
  sequence, which is sufficient for deterministic no-write packet comparison.
  Real writes still require `live-list` to refresh command sequence/raw receiver
  state. When `--experiment-dir` contains saved live snapshots, matching MACs
  override the capture-derived `command_sequence`, `pwm_values`, `fan_rpm`, and
  `raw_hex` in the registry before packet preview/compare is built.
- `python tools/lianli_wireless_probe.py linux-control-packet-preview <capture-dir> live-pwm --capture-base lianli-v2117 --experiment-dir <linux-log-dir>`:
  added and covered by direct/CLI tests; exports
  `lianli-linux-control-packet-preview/v1`, a no-write packet preview built
  from the target registry. It reports packet count, first/last packet hex, full
  per-packet hex/SHA-256/RF chunk metadata, and decoded RF frame links so the
  capture-derived context can be checked against the existing packet builders
  before touching USB hardware. Direct PWM previews prefer capture-derived
  `observed_parameters.default_pwm_values`; explicit `--pwm-values` or `--pwm`
  still overrides that default. Static RGB previews now also prefer
  capture-derived `default_color`, `default_led_count`,
  `default_effect_index`, and `default_interval_ms`; explicit
  `--color`/`--led-count`/`--effect-index`/`--interval-ms` still override those
  defaults. With a matching baseline receiver snapshot plus the official static
  RGB capture, `linux-control-packet-compare ... live-rgb ...` can now prove an
  exact no-write packet match before any Linux USB write.
  Generated-rainbow captures now follow the same no-write loop: when a decoded
  RGB payload matches the local rainbow generator, `live-rainbow` observed
  parameters record the captured LED count, frame count, interval, and effect
  index; `linux-control-packet-preview ... live-rainbow ...` uses those values
  by default, and `linux-control-packet-compare ... live-rainbow ...` can prove
  an exact match against the official capture when the baseline receiver
  snapshot is present.
- `python tools/lianli_wireless_probe.py linux-control-packet-compare <capture-dir> live-pwm <official-capture> --capture-base lianli-v2117 --experiment-dir <linux-log-dir>`:
  added and covered by direct/CLI tests; exports
  `lianli-linux-control-packet-compare/v1`. It builds the preview packets from
  capture-derived target context and compares them against a USBPcap/tshark
  capture with the existing exact/semantic `compare-capture` engine, reporting
  `matched`, `exact_match`, `semantic_match`, target, parameters, expected packet
  count, and the underlying comparison payload. Use semantic match for protocol
  shape/parameter proof when only direct RF frames are present. Exact match is
  now expected when the capture set also includes a matching baseline receiver
  snapshot; live hardware writes still require fresh receiver sequence/raw state
  from `live-list`. Saved live snapshots are consumed by packet preview via the
  target registry, so a recompare after `live-list` uses the refreshed
  `command_sequence` and per-device `raw_hex` instead of stale capture state.
  Packet preview and packet compare now also expose `target_state`, including
  `missing_packet_fields`, `placeholder_fields`, snapshot metadata/state/raw
  availability, and `live_snapshot_refresh_required`. This makes the
  dry-run/write distinction explicit: if only direct RF write evidence is
  present, preview still helps inspect the generated packets, but the report
  marks missing receiver-state fields such as `device_type`, `fan_count`,
  `command_sequence`, or `raw` as placeholders. A `snapshot_source` marker alone
  now counts only as `snapshot_metadata_available`, not as usable
  `snapshot_state_available`, so the GUI can keep pointing back to `live-list`
  plus exact packet compare before enabling `WRITE-LIANLI`.
  Experiment summaries and action-plan pre-write validation now preserve those
  target-state fields as flattened `target_state_*` values, and
  `packet_compare_validation` reports status/placeholder/raw availability
  counts. This lets the GUI decide whether a passing compare was built from a
  capture-backed receiver snapshot or from dry-run placeholders without parsing
  the full packet-compare artifact.
  The report now also promotes the underlying
  `compare-capture` diagnostics to top-level `match_diagnostics`, so a GUI can
  distinguish `exact-match`, `semantic-match-exact-mismatch`, and
  `semantic-mismatch` without parsing the full comparison payload. Mismatches
  include a `closest_observed` / `nearest_differences` diagnostic on missing
  frames, listing the nearest decoded official frame and field-level differences
  such as `pwm_values`, `channel`, `rx_type`, `master_mac`, `sequence`, or RGB
  timing fields. The same output now includes a `write_gate` summary: only an
  `exact-match` passes guarded write preflight; a semantic-only match requires
  `live-list`/receiver-state refresh and another exact compare; a semantic
  mismatch blocks `WRITE-LIANLI`.
- `python tools/lianli_wireless_probe.py capture-timeline-report <capture>`:
  added and covered by direct/CLI tests; converts a capture into ordered
  receiver-list, master-query, receiver-snapshot, and RF-frame events, including
  per-snapshot device field changes, RF chunk packet spans, and USBPcap/tshark
  frame number/time/VID/PID/endpoint metadata when present. It also annotates
  event-level relative time, gap from the previous decoded event, RF chunk time
  span, and timeline summary span/max-gap values. Use it after
  `capture-triage-report` when an official trace needs to be matched back to
  exact L-Connect UI actions.
- `python tools/lianli_wireless_probe.py artifact-evidence-matrix .cache/lianli`:
  added and covered by direct/CLI tests; summarizes saved artifact/JS/diff JSON
  reports by L-Connect version, flags versions with high-priority RF sender or
  receiver VID/PID evidence for USBPcap capture, keeps high-entropy raw
  little-endian VID/PID hits in a lower-confidence bucket, and explicitly
  separates wired HID fan-controller leads from L-Wireless RF protocol proof.
- `tshark -v`: passed; reports TShark/Wireshark `4.2.2`, enabling direct raw
  `.pcapng` input for `analyze-capture` when a Windows USBPcap trace is
  available.
- `python tools/lianli_wireless_probe.py summarize-captures /tmp/lianli-capture-summary-demo`:
  passed on a synthetic mixed directory; ranks the direct PWM capture first,
  reports `live-pwm` evidence, and marks unrelated text as having no supported
  L-Wireless USB payloads.
- `python tools/lianli_wireless_probe.py`: safe read-only probe; currently finds
  no matching LIAN LI wireless devices on this machine.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-installer-exe.json analyze-artifact ".cache/lianli/extract/20260422-L-Connect 3-x64-v2.1.20-fde9a570.exe"`:
  passed; reports SHA256 `92305cb805d3a9ddac5ff7d78d8426bc66ba2a3bc28be71be0e26a660579b60f`,
  PE type, product metadata, and high-entropy raw VID/PID warning.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-nsis-payload.json analyze-artifact .cache/lianli/nsis/[0]`:
  passed; reports SHA256 `afa714b7805b0d5a970f4c7a4af710631166047e998bf731dbb39a443ca7d5de`,
  NSIS `DEADBEEF NullsoftInst` header metadata, unsupported firstheader flags
  `0x70`, entropy `7.9999`, no direct decompression hits, no high-confidence
  static pattern, and warnings about isolated raw VID/PID hits in compressed
  data.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-tree-nsis.json analyze-artifact-tree .cache/lianli/nsis --max-file-size 2000000000`:
  passed; scans 24 extracted NSIS PE/resource/payload files, finds static hits
  only in `.rsrc/version.txt`, `CERTIFICATE`, and `[0]`, and records `[0]` as
  the only NSIS payload.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.1.23-nsis-payload.json analyze-artifact .cache/lianli/nsis-v2.1.23/[0]`:
  passed; reports SHA256 `4d5a416ae2f52d8622d19a3346ccc2db994cd7591804d5671a33ef3576f15660`,
  unsupported NSIS flags `0x70`, entropy `7.9999`, no useful direct
  decompression hit, and no high-confidence protocol strings.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.1.23-assets-tree.json analyze-artifact-tree .cache/lianli/assets-v2.1.23 --max-file-size 80000000`:
  passed; scans 755 asset files, finds 38 content-matched files and 179
  path-matched files, including official AL V2 / SL V2 HID command code and
  SLV3 sensor/theme asset models.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/extract-hid-js-v2.1.23.json extract-hid-js .cache/lianli/assets-v2.1.23`:
  passed; scans 28 JS files, matches 17 duplicated animation bundles, and
  emits 799 structured AL V2 / SL V2 HID command-template hits.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/extract-wireless-js-v2.1.23.json extract-wireless-js .cache/lianli/assets-v2.1.23`:
  passed; scans 28 JS files, matches 24 files, emits 841 wireless/USB/IPC clue
  occurrences, and warns that no high-confidence L-Wireless RF sender/receiver
  USB ID appeared in scanned JS.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-changelog-official.json analyze-changelog https://lian-li.com/zh-TW/l-connect3/l3-changelog/ --top 12`:
  passed; parses 54 official L3 changelog entries, finds 17 wireless-related
  entries after hard-match filtering, and ranks `2.0.33`, `2.0.32`, `2.0.23`,
  `2.1.17`, `2.0.22`, `2.0.29`, `2.0.20`, `2.0.34`, `2.0.30`, `2.0.21`,
  `2.1.11`, and `2.0.24` as the top current candidates for further
  official-installer analysis. Latest listed L3 entries are still `2.1.23`,
  `2.1.20`, and `2.1.17`.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.0.33-exe.json analyze-artifact .cache/lianli/downloads/20250825-L-Connect-3-x64-v2.0.33-f7fc8097.exe`:
  passed; reports SHA256 `1dd6451215a4ee300293a7aa9d00e3fd439887c60b03a8f6f6c5bfbd7efac470`,
  PE type, L-Connect metadata, and one medium-confidence TL LCD wireless
  little-endian VID/PID hit in compressed data.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.0.33-nsis-payload.json analyze-artifact .cache/lianli/nsis-v2.0.33/[0]`:
  passed; reports SHA256 `9a652ab0e9cdb2a7e2e2d34c0c1cf8653529da64ec606d2fd23095d2933f1b4b`,
  NSIS high-entropy payload, unsupported flags `0x70`, no successful direct
  decompression probe, no high-confidence protocol/product string, and the same
  single medium-confidence TL LCD wireless VID/PID raw hit.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.0.33-nsis-tree.json analyze-artifact-tree .cache/lianli/nsis-v2.0.33 --max-file-size 2000000000`:
  passed; scans 24 extracted PE/resource/payload files and confirms `[0]` is
  the only NSIS payload.
- `sha256sum .cache/lianli/downloads/20250822-L-Connect-3-x64-v2.0.32-2342e974.exe`:
  passed; reports SHA256 `a3c70dd48d8ab2af604f7d73aa8580543def8f805d9e78aa7314dccd9f54dda8`.
- `7z l .cache/lianli/downloads/20250822-L-Connect-3-x64-v2.0.32-2342e974.exe`:
  passed; reports a 32-bit PE `L-Connect 3 Installer`, FileVersion/ProductVersion
  `2.0.32.0`, 24 files, and `[0]` payload size `1257277440`.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.0.32-exe.json analyze-artifact .cache/lianli/downloads/20250822-L-Connect-3-x64-v2.0.32-2342e974.exe`:
  passed; reports PE size `1257647472`, L-Connect metadata, SHA256
  `a3c70dd48d8ab2af604f7d73aa8580543def8f805d9e78aa7314dccd9f54dda8`,
  and one medium-confidence raw TL LCD wireless little-endian VID/PID hit.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.0.32-nsis-payload.json analyze-artifact .cache/lianli/nsis-v2.0.32/[0]`:
  passed; reports payload size `1257277440`, SHA256
  `631c0a2bd19cd039970aa632ac249a119cae83fb56ce2b91bab6c9c3e0dd36de`,
  NSIS high-entropy payload, unsupported flags `0x70`, no successful direct
  decompression probe, no high-confidence protocol/product string, and one
  medium-confidence TL LCD wireless raw VID/PID hit.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.0.32-nsis-tree.json analyze-artifact-tree .cache/lianli/nsis-v2.0.32 --max-file-size 2000000000`:
  passed; scans 24 extracted PE/resource/payload files and confirms `[0]` is
  the only NSIS payload.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.0.32-v2.0.33-payload.json diff-artifacts .cache/lianli/nsis-v2.0.32/[0] .cache/lianli/nsis-v2.0.33/[0] --block-size 65536`:
  passed; reports payload size delta `-72`, common prefix `24`, common suffix
  `1`, zero matching 64KiB blocks, no static match delta, and no new
  high-confidence clue.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.0.32-v2.0.33-exe.json diff-artifacts .cache/lianli/downloads/20250822-L-Connect-3-x64-v2.0.32-2342e974.exe .cache/lianli/downloads/20250825-L-Connect-3-x64-v2.0.33-f7fc8097.exe --block-size 65536`:
  passed; reports full EXE size delta `-72`, common prefix `296`, common suffix
  `6`, fixed-block reuse ratio `0.000208`, and the same lack of new
  high-confidence protocol evidence.
- `sha256sum .cache/lianli/downloads/20250919-L-Connect-3-x64-v2.0.34-988ad479.exe`:
  passed; reports SHA256 `b0e86525e65bcaa7473aa45e3b8dbb42f8aa58e839391e85df5aa80137d58459`.
- `7z l .cache/lianli/downloads/20250919-L-Connect-3-x64-v2.0.34-988ad479.exe`:
  passed; reports a 32-bit PE `L-Connect 3 Installer`, FileVersion/ProductVersion
  `2.0.34.0`, 24 files, and `[0]` payload size `1260325272`.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.0.34-exe.json analyze-artifact .cache/lianli/downloads/20250919-L-Connect-3-x64-v2.0.34-988ad479.exe`:
  passed; reports PE size `1260695304`, L-Connect metadata, SHA256
  `b0e86525e65bcaa7473aa45e3b8dbb42f8aa58e839391e85df5aa80137d58459`,
  and medium-confidence raw TL controller plus TL LCD little-endian VID/PID hits
  in high-entropy compressed data.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.0.34-nsis-payload.json analyze-artifact .cache/lianli/nsis-v2.0.34/[0]`:
  passed; reports payload size `1260325272`, SHA256
  `baa78afe80f3a0734c0879d3552a55cf3cdc0b4766cb040496e0e6ec92c0ea59`,
  NSIS high-entropy payload, unsupported flags `0x70`, no successful direct
  decompression probe, no high-confidence protocol/product string, and the same
  medium-confidence TL controller plus TL LCD raw VID/PID hits.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.0.34-nsis-tree.json analyze-artifact-tree .cache/lianli/nsis-v2.0.34 --max-file-size 2000000000`:
  passed; scans 24 extracted PE/resource/payload files, confirms `[0]` is the
  only NSIS payload, and finds no additional plaintext protocol evidence.
- `7z l .cache/lianli/nsis-v2.0.34/[0]` / `file .cache/lianli/nsis-v2.0.34/[0]` /
  `strings -a -n 8 .cache/lianli/nsis-v2.0.34/[0]`:
  confirms the inner payload is not openable by current 7-Zip, is identified
  only as `data`, and does not expose useful plaintext wireless/RF strings.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.0.33-v2.0.34-payload.json diff-artifacts .cache/lianli/nsis-v2.0.33/[0] .cache/lianli/nsis-v2.0.34/[0] --block-size 65536`:
  passed; reports payload size delta `3047904`, common prefix `20`, common
  suffix `1`, zero matching 64KiB blocks, added medium-confidence TL controller
  plus TL LCD raw VID/PID hits, removed TL LCD wireless raw VID/PID hit, and no
  new high-confidence clue.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.0.33-v2.0.34-exe.json diff-artifacts .cache/lianli/downloads/20250825-L-Connect-3-x64-v2.0.33-f7fc8097.exe .cache/lianli/downloads/20250919-L-Connect-3-x64-v2.0.34-988ad479.exe --block-size 65536`:
  passed; reports full EXE common prefix `296`, common suffix `6`, fixed-block
  reuse ratio `0.000208`, and the same lack of new high-confidence protocol
  evidence.
- `sha256sum .cache/lianli/downloads/20260213-L-Connect-3-x64-v2.1.17-2f7c3856.exe`:
  passed; reports SHA256 `e83c582dd1e95c59e3c3c63bb211ef42f4fe5a3a6268783699b857d93f0d4e15`.
- `7z l .cache/lianli/downloads/20260213-L-Connect-3-x64-v2.1.17-2f7c3856.exe`:
  passed; reports a 32-bit PE `L-Connect 3 Installer`, FileVersion/ProductVersion
  `2.1.17.0`, 24 files, and `[0]` payload size `1321445496`.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.1.17-exe.json analyze-artifact .cache/lianli/downloads/20260213-L-Connect-3-x64-v2.1.17-2f7c3856.exe`:
  passed; reports PE size `1321815528`, L-Connect metadata, SHA256
  `e83c582dd1e95c59e3c3c63bb211ef42f4fe5a3a6268783699b857d93f0d4e15`,
  and medium-confidence raw RF sender, TL controller, TL LCD, and TL LCD
  wireless little-endian VID/PID hits in high-entropy compressed data.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.1.17-nsis-payload.json analyze-artifact .cache/lianli/nsis-v2.1.17/[0]`:
  passed; reports payload size `1321445496`, SHA256
  `4331f95c7dd6785eaff554c1f16bbd59694fb949889efeacb4f451d46c9c05cf`,
  NSIS high-entropy payload, unsupported flags `0x70`, no successful direct
  decompression probe, no high-confidence protocol/product string, and the same
  four medium-confidence raw VID/PID hits.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/analyze-artifact-v2.1.17-nsis-tree.json analyze-artifact-tree .cache/lianli/nsis-v2.1.17 --max-file-size 2000000000`:
  passed; scans 24 extracted PE/resource/payload files and finds no additional
  plaintext RF protocol evidence.
- `7z l .cache/lianli/nsis-v2.1.17/[0]` / `file .cache/lianli/nsis-v2.1.17/[0]`:
  confirms the inner payload is not openable by current 7-Zip and is identified
  only as `data`.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.0.34-v2.1.17-payload.json diff-artifacts .cache/lianli/nsis-v2.0.34/[0] .cache/lianli/nsis-v2.1.17/[0] --block-size 65536`:
  passed; reports payload size delta `61120224`, common prefix `20`, common
  suffix `2`, zero matching 64KiB blocks, added medium-confidence RF sender and
  TL LCD wireless raw VID/PID hits, and no new high-confidence clue.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.0.34-v2.1.17-exe.json diff-artifacts .cache/lianli/downloads/20250919-L-Connect-3-x64-v2.0.34-988ad479.exe .cache/lianli/downloads/20260213-L-Connect-3-x64-v2.1.17-2f7c3856.exe --block-size 65536`:
  passed; reports full EXE size delta `61120224`, common prefix `296`, common
  suffix `6`, zero matching 64KiB blocks, and the same lack of new
  high-confidence protocol evidence.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.1.17-v2.1.23-payload.json diff-artifacts .cache/lianli/nsis-v2.1.17/[0] .cache/lianli/nsis-v2.1.23/[0] --block-size 65536`:
  passed; reports payload size delta `-975983136`, common prefix `20`, common
  suffix `5`, zero matching 64KiB blocks, removal of the v2.1.17 raw RF/TL
  VID/PID hits, and no new high-confidence clue.
- `python tools/lianli_wireless_probe.py --save-json .cache/lianli/diff-artifacts-v2.1.17-v2.1.23-exe.json diff-artifacts .cache/lianli/downloads/20260213-L-Connect-3-x64-v2.1.17-2f7c3856.exe ".cache/lianli/extract-v2.1.23/20260522-L-Connect 3-x64-v2.1.23-5b4679ee.exe" --block-size 65536`:
  passed; reports full EXE size delta `-975983136`, common prefix `296`, common
  suffix `6`, zero matching 64KiB blocks, and no new high-confidence protocol
  clue.
- `QT_QPA_PLATFORM=offscreen pytest -q tests/test_lianli_wireless.py tests/test_lianli_probe_tool.py`:
  `128 passed`.
- `QT_QPA_PLATFORM=offscreen pytest -q`: `400 passed`.
- `git diff --check`: passed.
