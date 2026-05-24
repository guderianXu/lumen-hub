# Lian Li Wireless Reverse Notes

## Current Findings

- Official L-Connect 3 v2.1.20 was downloaded from LIAN LI's CDN as
  `20260422-L-Connect 3-x64-v2.1.20-fde9a570.zip`.
- The ZIP contains a single NSIS installer executable:
  `20260422-L-Connect 3-x64-v2.1.20-fde9a570.exe`.
- The installer is signed/described as `L-Connect 3 Installer` by
  `LIAN LI INDUSTRIAL CO.,LTD`.
- Static extraction with stock 7-Zip exposes the NSIS PE and one large solid
  payload block, but not the final installed app tree yet.

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
- Direct PWM control is any normal value `0..255`; safety policy should avoid
  writing low duty values unless the user explicitly enables control.
- `dry-run-pwm-sync` now builds the same `06 06 06 06` PWM payload without
  writing USB. This is a useful preflight before enabling receiver-mode sync.

LED/RGB path:

- RGB uses RF payload byte `1 == 0x20`.
- Raw RGB frames are compressed with a TinyUZ-compatible literal encoder.
- Initial LED packet carries compressed length, frame count, LED count, and
  frame interval. Later packets carry compressed chunks.
- Inferred LED-count mapping from `uwscli`:
  - device type `1`: 116 LEDs
  - device type `2`: 132 LEDs
  - device type `3`: 174 LEDs
  - device type `4`: 88 LEDs
  - device type `65`: 96 LEDs
  - device type `10`: `24 + fan_count * 24`
  - fallback: `fan_count * 26`, or 60 LEDs if unknown
- A static color frame can be generated locally now:
  - expand one RGB triple to `led_count * 3` bytes;
  - TinyUZ-compress the frame;
  - build one or more 240-byte LED payloads;
  - split each payload into four 64-byte RF packets.
- Turning lights off is the same static RGB path with color `(0, 0, 0)`.
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
   - `python tools/lianli_wireless_probe.py dry-run-bind --master-mac 10:20:30:40:50:60 --rx-type 3`
   - `python tools/lianli_wireless_probe.py dry-run-unbind`
   - `python tools/lianli_wireless_probe.py dry-run-rgb --color 0,0,0`
   - `python tools/lianli_wireless_probe.py dry-run-lcd brightness --value 65 --timestamp-ms 16909060`
   - `python tools/lianli_wireless_probe.py dry-run-lcd push-jpg --payload-size 6 --timestamp-ms 1`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-lcd-info.json live-lcd-info`
   - `python -m uwscli fan list --output json`
   - `python -m uwscli fan list-masters --output json`
2. For first live writes, use only one MAC at a time and keep a conservative
   fan duty:
   - `python tools/lianli_wireless_probe.py safe-pwm-experiment --mac aa:bb:cc:dd:ee:ff --pwm 120 --output-dir .cache/lianli/pwm-experiment --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py safe-sync-experiment --mac aa:bb:cc:dd:ee:ff --output-dir .cache/lianli/sync-experiment --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py safe-rgb-experiment --mac aa:bb:cc:dd:ee:ff --color 0,0,0 --output-dir .cache/lianli/rgb-experiment --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py safe-bind-experiment --mac aa:bb:cc:dd:ee:ff --rx-type 3 --output-dir .cache/lianli/bind-experiment --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py safe-unbind-experiment --mac aa:bb:cc:dd:ee:ff --output-dir .cache/lianli/unbind-experiment --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-pwm.json live-pwm --mac aa:bb:cc:dd:ee:ff --pwm 120 --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-pwm-sync.json live-pwm-sync --mac aa:bb:cc:dd:ee:ff --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-rgb-off.json live-rgb --mac aa:bb:cc:dd:ee:ff --color 0,0,0 --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-bind.json live-bind --mac aa:bb:cc:dd:ee:ff --rx-type 3 --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-unbind.json live-unbind --mac aa:bb:cc:dd:ee:ff --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py --save-json .cache/lianli/live-lcd-control.json live-lcd-control --brightness 60 --rotation 180 --confirm WRITE-LIANLI`
   - `python tools/lianli_wireless_probe.py analyze-log .cache/lianli/live-pwm.json`
   - `python tools/lianli_wireless_probe.py diff-snapshots .cache/lianli/live-list-before.json .cache/lianli/live-list-after.json`
   - `python tools/lianli_wireless_probe.py summarize-experiments .cache/lianli`
3. Run a read-only GUI probe that calls `backend.list_devices()` and displays
   receiver MAC, master MAC, fan count, RPM, PWM, channel, and device type.
4. Use the GUI `联力无线` page for local USB scans, read-only receiver
   snapshots, and read-only master MAC queries. The page is separate from
   motherboard `hwmon` fan control and OpenRGB lighting.
5. Only after the read-only probe is stable, enable guarded GUI writes:
   - require the GUI write checkbox and exact `WRITE-LIANLI` token;
   - refuse PWM values below a conservative floor;
   - write one receiver MAC at a time;
   - immediately re-read snapshot and verify RPM/PWM changed as expected.
6. Keep default behavior read-only and require explicit enable before PWM/RGB
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
- `build_static_rgb_payloads()`, `LianLiWirelessBackend.build_static_rgb_packets()`,
  and `tinyuz_compress_literal()`: static RGB/turn-off packet generation without
  touching hardware.
- `tools/lianli_wireless_probe.py`: read-only sysfs probe for known LIAN LI
  wireless VID/PID pairs. It also supports `dry-run-pwm`, `dry-run-pwm-sync`,
  `dry-run-master-query`, `dry-run-bind`, `dry-run-unbind`, `dry-run-rgb`, and
  `dry-run-lcd` packet/header summaries without touching USB devices,
  `udev-rules` for Linux
  permissions, `live-list` for a read-only PyUSB receiver snapshot, and
  `live-master` for a read-only master MAC query through the RF sender.
  `live-lcd-info` performs read-only wireless LCD handshake / firmware queries.
  Guarded live write commands (`live-pwm`, `live-pwm-sync`, `live-rgb`,
  `live-bind`, `live-unbind`, and `live-lcd-control`) require the exact
  `--confirm WRITE-LIANLI` token. Receiver writes re-read the receiver snapshot
  after sending. The global `--save-json PATH` option writes the same JSON
  payload shown on stdout to disk for hardware validation logs. `analyze-log`
  inspects one saved live write JSON, reports receiver field changes, and adds
  a structured `expected_effect` check for PWM, motherboard PWM sync, bind, and
  unbind logs so a hardware run is not treated as successful merely because
  some receiver field changed. `diff-snapshots` compares two saved receiver
  snapshots by MAC and reports added, removed, changed, and unchanged receivers.
  `summarize-experiments` recursively scans a directory of saved JSON logs and
  groups live write outcomes by operation, changed fields, receiver MACs, and
  validation errors so repeated hardware experiments can be compared quickly.
  `safe-pwm-experiment` is the preferred first write test: it performs one
  guarded single-MAC PWM write and saves before, write, after, analysis, and
  summary JSON files in one directory. `safe-sync-experiment` performs the same
  capture flow for motherboard PWM sync and records the expected magic PWM tuple
  `[6, 6, 6, 6]` when sync is enabled. `safe-rgb-experiment` does the same for
  static RGB writes and explicitly marks `visual_confirmation_required` when the
  receiver snapshot does not change, because RGB effects may only be observable
  on the LEDs. `safe-bind-experiment` performs the same capture flow for an
  unbound receiver, infers the master MAC when possible, and refuses receivers
  that are already bound. `safe-unbind-experiment` mirrors it for bound
  receivers and refuses receivers that are already unbound.
  `validate-readonly` runs scan, live-list, live-master, and optionally
  live-lcd-info, saving each step as separate JSON under an output directory.
- `LianLiWirelessPage`: GUI page for safe LIAN LI wireless probing. It exposes
  sysfs scanning, live receiver snapshots, live master MAC queries, and a
  `只读验证` action that saves scan, live-list, live-master, and live-lcd-info
  JSON files under `.cache/lianli/gui-validation`. It also exposes guarded
  single-MAC PWM / motherboard PWM sync / RGB-off writes, a guarded
  `安全 PWM 实验` action that saves before/write/after/analysis/summary JSON
  files under `.cache/lianli/gui-pwm-experiment`, a guarded `安全 Sync 实验`
  action that saves motherboard PWM sync before/write/after/analysis/summary
  JSON under `.cache/lianli/gui-sync-experiment` and records `[6, 6, 6, 6]`
  as the expected sync tuple, a guarded `安全 RGB 实验` action that saves the
  same experiment bundle under `.cache/lianli/gui-rgb-experiment` and marks
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
  aggregation logic as `summarize-experiments`.
- `tests/test_lianli_wireless.py`: protocol parser/builder tests.
- `tests/test_lianli_lcd.py`: wireless LCD command/header builder tests.
- `tests/test_lianli_probe_tool.py`: CLI probe/dry-run regression tests.
- `pyproject.toml` optional extra `lianli`: installs `pyusb>=1.2` and
  `pycryptodomex>=3.20` for live USB receiver tests and encrypted wireless LCD
  header generation without making them default GUI dependencies.

## Validation Log

- `pytest tests/test_lianli_wireless.py -q`: passed.
- `pytest tests/test_lianli_wireless.py tests/test_lianli_probe_tool.py -q`:
  passed.
- `python tools/lianli_wireless_probe.py udev-rules`: prints rules for
  `0416:8040`, `0416:8041`, `0416:7372`, `04fc:7393`, and `1cbe:0006`.
- `python tools/lianli_wireless_probe.py dry-run-pwm-sync`: builds four RF
  packets; first packet contains `06060606` at the PWM field.
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
- `python tools/lianli_wireless_probe.py safe-sync-experiment --mac ... --confirm WRITE-LIANLI`:
  covered by fake-backend tests for before/write/after capture, `[6, 6, 6, 6]`
  PWM sync analysis, and summary generation; real motherboard PWM sync
  verification is still pending hardware.
- `python tools/lianli_wireless_probe.py safe-rgb-experiment --mac ... --color 0,0,0 --confirm WRITE-LIANLI`:
  covered by fake-backend tests for before/write/after capture, analysis,
  summary generation, and explicit visual-confirmation marking when receiver
  snapshot fields do not change; real LED verification is still pending
  hardware.
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
- `python tools/lianli_wireless_probe.py`: safe read-only probe; currently finds
  no matching LIAN LI wireless devices on this machine.
- `pytest -q`: `270 passed`.
