# LIAN LI Wireless Static RGB Reverse Notes

Date: 2026-05-27
Project: lumen-hub
Hardware: LIAN LI L-Wireless RF sender/receiver

## Devices

- RF sender: `0416:8040`
- RF receiver: `0416:8041`
- Windows capture/control device path:
  - USBPcap executable: `D:\tools\USBPcapCMD.exe`
  - USBPcap root hub for LIAN LI devices: `\\.\USBPcap2`
  - receiver USB address on that hub: `2`
  - sender USB address on that hub: `3`
- Recommended USBPcap capture command shape:

```powershell
& 'D:\tools\USBPcapCMD.exe' -d '\\.\USBPcap2' --devices 2,3 --inject-descriptors -o '<output>.pcapng'
```

## Confirmed Wireless Target Context

- receiver MAC / target MAC: `14:55:f9:62:32:e1`
- master MAC: `24:69:dd:62:32:dc`
- channel: `8`
- rx_type: `1`
- static RGB led_count: `26`
- static RGB interval_ms: `60`

## Static RGB Interface Conclusion

- Static lighting uses `live-rgb`.
- Official static RGB packet structure was reproduced exactly for captured green and blue examples.
- Real Windows write to `0416:8040` succeeded after stopping L-Connect services/processes and writing directly to the sender with the known target context.
- Receiver snapshot reads on Windows are currently unreliable in our CLI path; the receiver returned a snapshot beginning with `0x00` in one live run. Direct sender write is therefore the validated Windows control path for now.

## Official Packet Structure Findings

- RF packet size: `64` bytes.
- RGB command generated `20` USB writes for one static color in the current implementation.
- Logical RGB payload sequence uses two payloads:
  - payload index `0`: metadata only.
  - payload index `1`: TinyUZ RGB data starts at offset `20`.
- First logical payload is repeated four times in RF chunks, matching official behavior.
- Static one-color TinyUZ compression uses a small back-reference stream.
- TinyUZ dictionary size for repeated RGB triplet is `3`, not the default `4096`.
- Confirmed compressed length for one-color 26-LED static payload: `12` bytes.

## Exact-Matched Captured Static Colors

### Green

- RGB: `(0, 254, 0)`
- effect_index: `70089055`
- Result: exact-match against official capture and real Windows live write changed the fan lighting green.

### Blue

- RGB: `(0, 0, 254)`
- effect_index: `70095480`
- Result: exact-match against official capture. Real Windows color sweep also successfully wrote blue.

## Additional Real Windows Color Sweep Results

The following arbitrary static colors were successfully sent through direct sender writes. These confirm RGB channel order and arbitrary static RGB support beyond official green/blue captures.

- red: `(254, 0, 0)`
- blue: `(0, 0, 254)`
- white: `(254, 254, 254)`
- off: `(0, 0, 0)`
- green: `(0, 254, 0)`
- yellow: `(254, 254, 0)`
- cyan: `(0, 254, 254)`
- magenta: `(254, 0, 254)`
- orange: `(254, 80, 0)`
- purple: `(128, 0, 254)`
- pink: `(254, 40, 120)`
- dim_red: `(64, 0, 0)`
- dim_white: `(48, 48, 48)`

## Windows Runtime Notes

- L-Connect services/processes can hold the USB device and cause `libusb_open` access denied.
- Stop these before direct Windows writes:
  - `LConnectServiceWatcher`
  - `LConnectService`
  - `L-Connect 3`
  - `L-Connect-Service`
  - `L-Connect-Service-Watcher`
  - L-Connect `CefSharp.BrowserSubprocess` processes
- Devices were bound to Microsoft WinUSB when direct writes succeeded:
  - Driver INF: `winusb.inf`
  - Service: `WINUSB`

## Working Direct Sender Write Pattern

Use known target context and open only the sender. Do not require a receiver snapshot before writing.

```python
from usb9_lcd.lianli.wireless import (
    LianLiWirelessBackend,
    PyUsbEndpointTransport,
    RF_SENDER_VID,
    RF_SENDER_PID,
    WirelessDeviceInfo,
)

target = WirelessDeviceInfo(
    mac="14:55:f9:62:32:e1",
    master_mac="24:69:dd:62:32:dc",
    channel=8,
    rx_type=1,
    device_type=0,
    fan_count=4,
    pwm_values=(0, 0, 0, 0),
    fan_rpm=(0, 0, 0, 0),
    command_sequence=0,
    raw=bytes(42),
)

sender = PyUsbEndpointTransport(RF_SENDER_VID, RF_SENDER_PID, timeout_ms=1000)
try:
    backend = LianLiWirelessBackend(sender=sender)
    backend.send_static_rgb(target, (0, 254, 0), led_count=26, effect_index=70089055)
finally:
    sender.close()
```

## Files Changed For Static RGB Correctness

- `usb9_lcd/lianli/wireless.py`
  - default static RGB interval changed to `60` ms.
  - static RGB now uses official two-payload layout.
  - first payload is metadata-only.
  - compressed RGB data starts in later payloads.
  - repeated static RGB data uses TinyUZ small-dictionary backref compression.
  - PyUSB bulk endpoint type detection now falls back to USB standard constants when PyUSB lacks `TRANSFER_TYPE_MASK`.
- `tools/lianli_wireless_probe.py`
  - compare/dry-run defaults aligned with 60 ms RGB interval.

## Next Reverse Target

Official dynamic lighting effects, starting with rainbow. For rainbow, capture separate official packets for:

- speed 75%, brightness 100%, direction left.
- speed 25%, brightness 100%, direction left.
- speed 75%, brightness 50%, direction left.
- speed 75%, brightness 100%, direction right.

This is needed to map official effect opcode and parameters for speed, brightness, and direction.
