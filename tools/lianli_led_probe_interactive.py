#!/usr/bin/env python3
import json
from pathlib import Path
from usb9_lcd.gui.settings import DEFAULT_SETTINGS_PATH, load_settings
from usb9_lcd.lianli.wireless import create_pyusb_backend, WirelessDeviceInfo

PROBE_COLOR = (0, 0, 255)
PROBE_EFFECT_INDEX = 1      # 固定静态效果索引，避免动态效果干扰


def _target_from_settings_object(settings):
    lw = getattr(settings, "lianli_wireless", None)
    if lw is None:
        return None
    targets = getattr(lw, "targets", None)
    if not isinstance(targets, dict) or not targets:
        return None
    active = getattr(lw, "active_target_mac", "") or next(iter(targets))
    t = targets.get(active) or next(iter(targets.values()))
    return {
        "mac": str(getattr(t, "mac", "")),
        "master_mac": str(getattr(t, "master_mac", "")),
        "channel": int(getattr(t, "channel", 8)),
        "rx_type": int(getattr(t, "rx_type", 1)),
        "device_type": int(getattr(t, "device_type", 0)),
        "fan_count": int(getattr(t, "fan_count", 1)),
        "led_count": int(getattr(t, "led_count", 26)),
    }


def _target_from_raw_json(path: Path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    lw = payload.get("lianli_wireless")
    if isinstance(lw, dict):
        targets = lw.get("targets")
        active = str(lw.get("active_target_mac", ""))
        if isinstance(targets, dict) and targets:
            t = targets.get(active) if active in targets else next(iter(targets.values()))
            if isinstance(t, dict):
                return {
                    "mac": str(t.get("mac", active or "")),
                    "master_mac": str(t.get("master_mac", "")),
                    "channel": int(t.get("channel", 8)),
                    "rx_type": int(t.get("rx_type", 1)),
                    "device_type": int(t.get("device_type", 0)),
                    "fan_count": int(t.get("fan_count", 1)),
                    "led_count": int(t.get("led_count", 26)),
                }

    for key in ("lianli", "wireless", "lianli_target"):
        node = payload.get(key)
        if isinstance(node, dict) and node.get("mac"):
            return {
                "mac": str(node.get("mac", "")),
                "master_mac": str(node.get("master_mac", "")),
                "channel": int(node.get("channel", 8)),
                "rx_type": int(node.get("rx_type", 1)),
                "device_type": int(node.get("device_type", 0)),
                "fan_count": int(node.get("fan_count", 1)),
                "led_count": int(node.get("led_count", 26)),
            }
    return None


def build_target():
    settings = load_settings()
    target_dict = _target_from_settings_object(settings)
    if target_dict is None:
        target_dict = _target_from_raw_json(DEFAULT_SETTINGS_PATH)
    if target_dict is None or not target_dict.get("mac"):
        raise RuntimeError(
            f"未找到联力目标配置。请先在 GUI 里点一次重新识别。配置文件: {DEFAULT_SETTINGS_PATH}"
        )

    target = WirelessDeviceInfo(
        mac=target_dict["mac"],
        master_mac=target_dict["master_mac"],
        channel=int(target_dict["channel"]),
        rx_type=int(target_dict["rx_type"]),
        device_type=int(target_dict["device_type"]),
        fan_count=int(target_dict["fan_count"]),
        pwm_values=(0, 0, 0, 0),
        fan_rpm=(0, 0, 0, 0),
        command_sequence=0,
        raw=bytes(42),
    )
    default_led = max(1, int(target_dict.get("led_count", 26) or 26))
    return target, default_led


def prompt_int(msg, default):
    raw = input(f"{msg} [{default}]: ").strip()
    if not raw:
        return default
    return int(raw)


def send_probe(backend, target, led_total, lit_count):
    lit = max(0, min(lit_count, led_total))

    # 只发送一帧：前 N 颗亮，后续灭，减少风扇控制短暂停顿
    from usb9_lcd.lianli.wireless import build_rgb_frame_payloads, build_rf_chunks

    raw = bytearray()
    for i in range(led_total):
        raw.extend(PROBE_COLOR if i < lit else (0, 0, 0))

    payloads = build_rgb_frame_payloads(
        target,
        bytes(raw),
        led_count=led_total,
        frame_count=1,
        interval_ms=0,
        effect_index=PROBE_EFFECT_INDEX,
    )
    packets = 0
    for payload in payloads:
        for packet in build_rf_chunks(target.channel, target.rx_type, payload):
            n = backend.sender.write(packet)
            if n != len(packet):
                raise RuntimeError(f"short write {n}/{len(packet)}")
            packets += 1
    return packets


def main():
    target, default_led = build_target()
    print(f"target={target.mac} master={target.master_mac} ch={target.channel} rx={target.rx_type}")
    print("probe_color=RED")

    led_total = prompt_int("输入总灯数(LED Count)", default_led)

    backend = create_pyusb_backend(timeout_ms=1200)
    try:
        print("\n命令：")
        print("  +    前N颗 +1")
        print("  -    前N颗 -1")
        print("  数字 直接设 N")
        print("  o    全灭")
        print("  a    全亮")
        print("  l    修改总灯数")
        print("  q    退出")
        n = 0
        packets = send_probe(backend, target, led_total, n)
        print(f"已点亮前 {n}/{led_total} 颗, color=RED, packets={packets}")

        while True:
            cmd = input("> ").strip().lower()
            if cmd == "q":
                break
            if cmd == "+":
                n = min(led_total, n + 1)
            elif cmd == "-":
                n = max(0, n - 1)
            elif cmd == "o":
                n = 0
            elif cmd == "a":
                n = led_total
            elif cmd == "l":
                led_total = prompt_int("总灯数", led_total)
                n = min(n, led_total)
            elif cmd.isdigit():
                n = max(0, min(led_total, int(cmd)))
            else:
                print("未知命令")
                continue

            packets = send_probe(backend, target, led_total, n)
            print(f"已点亮前 {n}/{led_total} 颗, color=RED, packets={packets}")

    finally:
        try:
            backend.send_static_rgb(target, (0, 0, 0), led_count=led_total, effect_index=PROBE_EFFECT_INDEX)
        except Exception:
            pass
        try:
            backend.sender.close()
        except Exception:
            pass
        try:
            backend.receiver.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
