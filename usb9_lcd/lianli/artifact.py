from __future__ import annotations

import bz2
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import lzma
import math
from pathlib import Path
import re
from typing import Any
import zlib

from usb9_lcd.lianli.wireless import LianLiWirelessError


CHUNK_SIZE = 1024 * 1024
MAX_OFFSETS_PER_PATTERN = 16
CONTEXT_BYTES = 48
DEFAULT_TREE_MAX_FILE_SIZE = 512 * 1024 * 1024
HID_JS_MAX_FILE_SIZE = 64 * 1024 * 1024
NSIS_SIGNATURE = b"\xef\xbe\xad\xdeNullsoftInst"
NSIS_FIRSTHEADER_SIZE = 28
NSIS_STANDARD_FLAGS_MASK = 0x0F
NSIS_PROBE_SAMPLE_SIZE = 1024 * 1024
DIFF_BLOCK_SIZE = 64 * 1024
DIFF_MAX_RANGES = 16
ARTIFACT_REPORT_OPERATIONS = {
    "analyze-artifact",
    "analyze-artifact-tree",
    "analyze-changelog",
    "extract-hid-js",
    "extract-wireless-js",
    "diff-artifacts",
}
ARTIFACT_VERSION_RE = re.compile(r"v?\d+\.\d+\.\d+")
RF_HIGH_CONFIDENCE_STATIC_LABELS = {
    "RF sender VID:PID text",
    "RF receiver VID:PID text",
    "RF sender VID:PID text UTF-16LE",
    "RF receiver VID:PID text UTF-16LE",
}
RF_MEDIUM_CONFIDENCE_STATIC_LABELS = {
    "RF sender VID/PID little-endian",
    "RF receiver VID/PID little-endian",
}
RF_STATIC_LABELS = RF_HIGH_CONFIDENCE_STATIC_LABELS | RF_MEDIUM_CONFIDENCE_STATIC_LABELS
WIRELESS_STATIC_LABEL_KEYWORDS = (
    "L-Wireless",
    "Uni Wireless",
    "wireless LCD",
    "wireless sensor",
)
RF_JS_CLUE_NAMES = {"rf-sender-usb-id", "rf-receiver-usb-id"}


@dataclass(frozen=True)
class MagicPattern:
    label: str
    needle: bytes
    note: str = ""


@dataclass(frozen=True)
class StaticPattern:
    label: str
    needle: bytes
    category: str
    confidence: str
    encoding: str = "raw"
    note: str = ""


@dataclass(frozen=True)
class HidJsCommandPattern:
    name: str
    label: str
    category: str
    regex: re.Pattern[str]
    reports: tuple[dict[str, Any], ...]
    source_functions: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class JsCluePattern:
    name: str
    label: str
    category: str
    regex: re.Pattern[str]
    confidence: str
    note: str = ""


STATIC_PATTERNS = (
    StaticPattern("RF sender VID:PID text", b"0416:8040", "usb-id", "high"),
    StaticPattern("RF receiver VID:PID text", b"0416:8041", "usb-id", "high"),
    StaticPattern("TL controller VID:PID text", b"0416:7372", "usb-id", "high"),
    StaticPattern("TL LCD VID:PID text", b"04fc:7393", "usb-id", "high"),
    StaticPattern("TL LCD wireless VID:PID text", b"1cbe:0006", "usb-id", "high"),
    StaticPattern("RF sender VID/PID little-endian", bytes.fromhex("16044080"), "usb-id", "medium"),
    StaticPattern("RF receiver VID/PID little-endian", bytes.fromhex("16044180"), "usb-id", "medium"),
    StaticPattern("TL controller VID/PID little-endian", bytes.fromhex("16047273"), "usb-id", "medium"),
    StaticPattern("TL LCD VID/PID little-endian", bytes.fromhex("fc049373"), "usb-id", "medium"),
    StaticPattern("TL LCD wireless VID/PID little-endian", bytes.fromhex("be1c0600"), "usb-id", "medium"),
    StaticPattern("SL V2 fan HID PID decimal", b"41219", "usb-id", "medium"),
    StaticPattern("AL V2 fan HID PID decimal", b"41220", "usb-id", "medium"),
    StaticPattern("SL V2 A105 fan HID PID decimal", b"41221", "usb-id", "medium"),
    StaticPattern("wireless LCD DES key", b"slv3tuzx", "crypto", "high"),
    StaticPattern("L-Connect product name", b"L-Connect 3", "product", "high"),
    StaticPattern("LIAN LI company name", b"LIAN LI", "product", "medium"),
    StaticPattern("L-Wireless text", b"L-Wireless", "product", "high"),
    StaticPattern("Uni Wireless text", b"Uni Wireless", "product", "medium"),
    StaticPattern("OpenRGB text", b"OpenRGB", "integration", "medium"),
    StaticPattern("HID device loader call", b"loadHidDevices(", "hid-code", "high"),
    StaticPattern("AL V2 fan motherboard PWM sync action", b"syncALV2FanRpm2MotherBoard", "hid-code", "high"),
    StaticPattern("SL V2 fan motherboard PWM sync action", b"syncSLV2FanRpm2MotherBoard", "hid-code", "high"),
    StaticPattern("fan RPM command builder", b"getFanRpmCmdData", "hid-code", "high"),
    StaticPattern("fan controller command builder", b"getFanControllerCmd", "hid-code", "high"),
    StaticPattern("fan controller effect command builder", b"getFanControllerEffectCmd", "hid-code", "high"),
    StaticPattern("fan frame reader", b"readFanFrame", "hid-code", "medium"),
    StaticPattern("HID RPM poll report bytes", b"224,80,0,0,0,0,0,0", "hid-command", "high"),
    StaticPattern("HID motherboard lighting sync bytes", b"224,16,97", "hid-command", "high"),
    StaticPattern("HID motherboard RPM sync bytes", b"224,16,98", "hid-command", "high"),
    StaticPattern("HID fan locate bytes", b"224,16,47", "hid-command", "medium"),
    StaticPattern("HID fan effect reset bytes", b"224,16,52", "hid-command", "medium"),
    StaticPattern("SL V3 sensor video model", b"slv3.models.SensorVideoInfo", "asset-model", "high"),
    StaticPattern("SL V3 sensor model assembly", b"slv3.models, Version=", "asset-model", "high"),
    StaticPattern("LIAN LI theme engine model", b"lianli.ThemeEngine", "asset-model", "medium"),
    StaticPattern("ThemeEngine theme type", b"ThemeEngine.Theme", "asset-model", "medium"),
)

PATH_PATTERNS = (
    StaticPattern("SL V3 asset path", b"assets/slv3/", "asset", "medium", "path"),
    StaticPattern("TL animation asset path", b"assets/tl/", "asset", "medium", "path"),
    StaticPattern("TL sensor web asset path", b"assets/tl-sensor/", "asset", "medium", "path"),
    StaticPattern("wireless sensor asset path", b"assets/wireless-sensor/", "asset", "medium", "path"),
    StaticPattern("wireless LCD template path", b"wireless-template/", "asset", "medium", "path"),
    StaticPattern("wireless sensor data filename", b"sensor_", "asset", "medium", "path"),
    StaticPattern("wireless sensor theme filename", b".turtheme", "asset", "medium", "path"),
)

MAGIC_PATTERNS = (
    MagicPattern("PE MZ header", b"MZ", "May be a false positive in high-entropy compressed data."),
    MagicPattern("ZIP local file header", b"PK\x03\x04"),
    MagicPattern("7z archive header", b"7z\xbc\xaf\x27\x1c"),
    MagicPattern("RAR archive header", b"Rar!\x1a\x07"),
    MagicPattern("gzip header", b"\x1f\x8b", "May be a false positive in high-entropy compressed data."),
    MagicPattern("xz header", b"\xfd7zXZ\x00"),
    MagicPattern("bzip2 header", b"BZh"),
    MagicPattern("SQLite header", b"SQLite format 3\x00"),
    MagicPattern("NSIS signature", NSIS_SIGNATURE),
)

HID_JS_PRODUCT_IDS = {
    41219: {
        "hex": "0xa103",
        "vid_pid": "0416:a103",
        "label": "SL V2 fan controller",
    },
    41220: {
        "hex": "0xa104",
        "vid_pid": "0416:a104",
        "label": "AL V2 fan controller",
    },
    41221: {
        "hex": "0xa105",
        "vid_pid": "0416:a105",
        "label": "SL V2 A105 fan controller",
    },
}

HID_JS_FUNCTION_NAMES = (
    "loadALV2FanHidDevices",
    "loadSLV2FanHidDevices",
    "syncALV2FanController2MotherBoard",
    "syncSLV2FanController2MotherBoard",
    "syncALV2FanRpm2MotherBoard",
    "syncSLV2FanRpm2MotherBoard",
    "checkingALV2FanControllerVersion",
    "checkingSLV2FanControllerVersion",
    "checkingALV2FanControllerRpm",
    "checkingSLV2FanControllerRpm",
    "getFanRpmCmdData",
    "getFanControllerCmd",
    "getFanControllerEffectCmd",
    "readFanFrame",
    "loadHidDevices",
    "getInputReport",
)

HID_JS_COMMAND_PATTERNS = (
    HidJsCommandPattern(
        name="hid-device-loader",
        label="HID device loader",
        category="discovery",
        regex=re.compile(r"loadHidDevices\("),
        reports=(),
        source_functions=("loadALV2FanHidDevices", "loadSLV2FanHidDevices"),
        note="Loads node-hid/WebHID devices by product ID.",
    ),
    HidJsCommandPattern(
        name="motherboard-lighting-sync",
        label="Motherboard lighting sync",
        category="sync",
        regex=re.compile(r"\[224,16,97,[^\[\]]{1,48}\]"),
        reports=({"bytes": [224, 16, 97, "enable?1:0", 0, 0], "direction": "out"},),
        source_functions=("syncALV2FanController2MotherBoard", "syncSLV2FanController2MotherBoard"),
    ),
    HidJsCommandPattern(
        name="motherboard-rpm-sync",
        label="Motherboard RPM sync",
        category="sync",
        regex=re.compile(r"\[224,16,98,[^\[\]]{1,48}\]"),
        reports=(
            {"bytes": [224, 16, 98, "enable?17:16"], "direction": "out", "port": 1},
            {"bytes": [224, 16, 98, "enable?34:32"], "direction": "out", "port": 2},
            {"bytes": [224, 16, 98, "enable?68:64"], "direction": "out", "port": 3},
            {"bytes": [224, 16, 98, "enable?136:128"], "direction": "out", "port": 4},
        ),
        source_functions=("syncALV2FanRpm2MotherBoard", "syncSLV2FanRpm2MotherBoard"),
    ),
    HidJsCommandPattern(
        name="fan-rpm-poll",
        label="Fan RPM poll request",
        category="telemetry",
        regex=re.compile(r"\[\[224,80,0,0,0,0,0,0\]\]"),
        reports=({"bytes": [224, 80, 0, 0, 0, 0, 0, 0], "direction": "out"},),
        source_functions=("checkingALV2FanControllerRpm", "checkingSLV2FanControllerRpm"),
    ),
    HidJsCommandPattern(
        name="fan-version-query",
        label="Firmware version query",
        category="telemetry",
        regex=re.compile(r"\.write\(\[224,80,1,0,0,0,0,0\]\)"),
        reports=({"bytes": [224, 80, 1, 0, 0, 0, 0, 0], "direction": "out"},),
        source_functions=("checkingALV2FanControllerVersion", "checkingSLV2FanControllerVersion"),
    ),
    HidJsCommandPattern(
        name="fan-input-report",
        label="Fan input report read",
        category="telemetry",
        regex=re.compile(r"getInputReport\(224,65\)"),
        reports=({"report_id": 224, "length": 65, "direction": "in"},),
        source_functions=("checkingALV2FanControllerVersion", "checkingSLV2FanControllerVersion", "checkingALV2FanControllerRpm", "checkingSLV2FanControllerRpm"),
    ),
    HidJsCommandPattern(
        name="fan-rpm-set",
        label="Direct fan RPM/PWM command",
        category="control",
        regex=re.compile(r"\[224,32\+this\.roads\[r\]\.id-1,0,Math\.floor\(t\*100/n\)\]"),
        reports=({"bytes": [224, "32+road_id-1", 0, "floor(target_rpm*100/max_rpm)"], "direction": "out"},),
        source_functions=("getFanRpmCmdData",),
    ),
    HidJsCommandPattern(
        name="road-sort",
        label="Road order command",
        category="config",
        regex=re.compile(r"\[224,16,99\]"),
        reports=({"bytes": [224, 16, 99, "road0", "road1", "road2", "road3", 8], "direction": "out"},),
    ),
    HidJsCommandPattern(
        name="led-road-config",
        label="LED road count/config command",
        category="lighting",
        regex=re.compile(r"\[224,16,96,this\.roads\[[^\]]+\]\.id,this\.roads\[[^\]]+\]\.lightNum,0,0\]"),
        reports=({"bytes": [224, 16, 96, "road_id", "led_count", 0, 0], "direction": "out"},),
    ),
    HidJsCommandPattern(
        name="led-effect",
        label="LED effect command",
        category="lighting",
        regex=re.compile(r"\[224,16\+2\*s\+\(t\?0:1\),[^\[\]]{1,160}\]"),
        reports=({"bytes": [224, "16+2*road+(inner?0:1)", "mode_index", "speed", "direction", "brightness"], "direction": "out"},),
    ),
    HidJsCommandPattern(
        name="led-static-color",
        label="LED static color frame command",
        category="lighting",
        regex=re.compile(r"\[224,48\+"),
        reports=({"bytes": [224, "48+road/channel", "rgb frame bytes..."], "direction": "out"},),
    ),
    HidJsCommandPattern(
        name="fan-locate-slv2",
        label="SL V2 locate LED command",
        category="diagnostic",
        regex=re.compile(r"\[224,16,47,[^\[\]]{1,48}\]"),
        reports=({"bytes": [224, 16, 47, "road_id-1", 0, 0], "direction": "out"},),
        source_functions=("locateSLV2FanControllerLed",),
    ),
    HidJsCommandPattern(
        name="fan-locate-alv2",
        label="AL V2 locate LED command",
        category="diagnostic",
        regex=re.compile(r"\[224,16\+\(r\.id-1\)\*2,54,0,0,2\]"),
        reports=({"bytes": [224, "16+(road_id-1)*2", 54, 0, 0, 2], "direction": "out"},),
        source_functions=("locateALV2FanControllerLed",),
    ),
    HidJsCommandPattern(
        name="led-effect-reset",
        label="LED effect reset command",
        category="lighting",
        regex=re.compile(r"\[224,16,52,0,0,0(?:,0)?\]"),
        reports=({"bytes": [224, 16, 52, 0, 0, 0], "direction": "out"},),
    ),
)

WIRELESS_JS_CLUE_PATTERNS = (
    JsCluePattern(
        name="rf-sender-usb-id",
        label="L-Wireless RF sender USB ID",
        category="usb-id",
        regex=re.compile(r"0416[:/_-]?8040|0x8040|\b32832\b", re.IGNORECASE),
        confidence="high",
        note="Expected RF sender/transmitter endpoint.",
    ),
    JsCluePattern(
        name="rf-receiver-usb-id",
        label="L-Wireless RF receiver USB ID",
        category="usb-id",
        regex=re.compile(r"0416[:/_-]?8041|0x8041|\b32833\b", re.IGNORECASE),
        confidence="high",
        note="Expected RF receiver snapshot endpoint.",
    ),
    JsCluePattern(
        name="tl-wireless-lcd-usb-id",
        label="TL wireless LCD USB ID",
        category="usb-id",
        regex=re.compile(r"1cbe[:/_-]?0006", re.IGNORECASE),
        confidence="medium",
        note="Known TL LCD wireless receiver VID/PID.",
    ),
    JsCluePattern(
        name="wireless-lcd-des-key",
        label="wireless LCD DES key",
        category="crypto",
        regex=re.compile(r"slv3tuzx", re.IGNORECASE),
        confidence="high",
        note="Wireless LCD header DES-CBC key/IV observed in public implementations.",
    ),
    JsCluePattern(
        name="l-wireless-product",
        label="L-Wireless product string",
        category="product",
        regex=re.compile(r"L-?Wireless", re.IGNORECASE),
        confidence="high",
    ),
    JsCluePattern(
        name="uni-wireless-product",
        label="Uni Wireless product string",
        category="product",
        regex=re.compile(r"Uni\s+Wireless", re.IGNORECASE),
        confidence="medium",
    ),
    JsCluePattern(
        name="wireless-token",
        label="wireless token",
        category="generic",
        regex=re.compile(r"\bwireless\b", re.IGNORECASE),
        confidence="low",
    ),
    JsCluePattern(
        name="receiver-token",
        label="receiver token",
        category="generic",
        regex=re.compile(r"\breceiver\b", re.IGNORECASE),
        confidence="low",
    ),
    JsCluePattern(
        name="sender-token",
        label="sender token",
        category="generic",
        regex=re.compile(r"\bsender\b", re.IGNORECASE),
        confidence="low",
    ),
    JsCluePattern(
        name="ipc-renderer",
        label="Electron ipcRenderer",
        category="ipc",
        regex=re.compile(r"\bipcRenderer\b"),
        confidence="medium",
        note="Likely front-end to native-process bridge.",
    ),
    JsCluePattern(
        name="message-queue-ipc",
        label="message-queue IPC channel",
        category="ipc",
        regex=re.compile(r"message-queue"),
        confidence="medium",
    ),
    JsCluePattern(
        name="hid-device-loader",
        label="HID device loader",
        category="hid-api",
        regex=re.compile(r"\bloadHidDevices\s*\("),
        confidence="high",
        note="Official JS path for wired HID controllers.",
    ),
    JsCluePattern(
        name="input-report-api",
        label="HID input report API",
        category="hid-api",
        regex=re.compile(r"\bgetInputReport\s*\("),
        confidence="medium",
    ),
    JsCluePattern(
        name="settings-pipe",
        label="settings pipe API",
        category="ipc",
        regex=re.compile(r"\b(?:readSettingsLike|writeSettings)\s*\("),
        confidence="medium",
    ),
)


def analyze_artifact_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise LianLiWirelessError(f"artifact path is not a file: {path}")
    patterns = list(STATIC_PATTERNS) + _utf16_patterns(STATIC_PATTERNS)
    matches = _scan_patterns(path, patterns)
    entropy = _entropy_sample(path)
    nsis_header = _nsis_header(path)
    nsis_probe = _probe_nsis_payload(path, nsis_header)
    return {
        "operation": "analyze-artifact",
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": _sha256_file(path),
        "file_type": _file_type(path),
        "nsis_header": nsis_header,
        "nsis_probe": nsis_probe,
        "entropy_sample": entropy,
        "match_count": sum(match["count"] for match in matches),
        "matched_pattern_count": len(matches),
        "matches": matches,
        "summary": _artifact_summary(matches),
        "warnings": _artifact_warnings(matches, entropy, nsis_header, nsis_probe),
    }


def diff_artifact_files(
    before: Path,
    after: Path,
    *,
    block_size: int = DIFF_BLOCK_SIZE,
) -> dict[str, Any]:
    if not before.is_file():
        raise LianLiWirelessError(f"before artifact path is not a file: {before}")
    if not after.is_file():
        raise LianLiWirelessError(f"after artifact path is not a file: {after}")
    if block_size <= 0:
        raise LianLiWirelessError("block_size must be positive")

    before_size = before.stat().st_size
    after_size = after.stat().st_size
    common_prefix = _common_prefix_size(before, after)
    common_suffix = _common_suffix_size(before, after, common_prefix)
    before_changed = _changed_range(before_size, common_prefix, common_suffix)
    after_changed = _changed_range(after_size, common_prefix, common_suffix)
    before_analysis = analyze_artifact_file(before)
    after_analysis = analyze_artifact_file(after)
    block_similarity = _block_similarity(before, after, block_size=block_size)
    after_changed_ranges = _bounded_ranges([after_changed])
    before_changed_ranges = _bounded_ranges([before_changed])
    return {
        "operation": "diff-artifacts",
        "before": _diff_file_payload(before, before_analysis),
        "after": _diff_file_payload(after, after_analysis),
        "size_delta": after_size - before_size,
        "common_prefix_bytes": common_prefix,
        "common_suffix_bytes": common_suffix,
        "before_changed_range": before_changed,
        "after_changed_range": after_changed,
        "before_changed_size": max(0, before_changed["end"] - before_changed["start"]),
        "after_changed_size": max(0, after_changed["end"] - after_changed["start"]),
        "block_similarity": block_similarity,
        "static_match_delta": _static_match_delta(before_analysis["matches"], after_analysis["matches"]),
        "after_changed_static_matches": _matches_in_ranges(after_analysis["matches"], after_changed_ranges),
        "before_changed_static_matches": _matches_in_ranges(before_analysis["matches"], before_changed_ranges),
        "after_changed_magic": _scan_magic_offsets(after, after_changed_ranges),
        "before_changed_magic": _scan_magic_offsets(before, before_changed_ranges),
        "warnings": _diff_warnings(before_analysis, after_analysis, block_similarity, after_changed),
    }


def artifact_evidence_matrix(path: Path) -> dict[str, Any]:
    root = path.expanduser()
    report_files = [root] if root.is_file() else sorted(root.rglob("*.json")) if root.is_dir() else []
    if not report_files:
        raise LianLiWirelessError(f"no JSON artifact reports found: {root}")

    versions: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    ignored_count = 0
    for report_path in report_files:
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            errors.append({"path": str(report_path), "error": str(error)})
            continue
        if not isinstance(payload, dict) or payload.get("operation") not in ARTIFACT_REPORT_OPERATIONS:
            ignored_count += 1
            continue
        if payload.get("operation") == "analyze-changelog":
            if _merge_changelog_report(versions, report_path, payload) == 0:
                ignored_count += 1
            continue
        version = _artifact_report_version(report_path, payload)
        version_state = versions.setdefault(version, _empty_artifact_version_state(version))
        _merge_artifact_report(version_state, report_path, payload)

    version_items = [_artifact_version_payload(state) for state in versions.values()]
    version_items.sort(key=_artifact_version_sort_key)
    return {
        "operation": "artifact-evidence-matrix",
        "path": str(root),
        "report_file_count": len(report_files),
        "used_report_count": sum(int(item["report_count"]) for item in version_items),
        "ignored_report_count": ignored_count,
        "error_count": len(errors),
        "version_count": len(version_items),
        "summary": _artifact_matrix_summary(version_items),
        "versions": version_items,
        "errors": errors,
    }


def _empty_artifact_version_state(version: str) -> dict[str, Any]:
    return {
        "version": version,
        "reports": [],
        "operations": Counter(),
        "categories": Counter(),
        "confidence": Counter(),
        "static_high_confidence": Counter(),
        "rf_static_labels": Counter(),
        "rf_high_confidence_static_labels": Counter(),
        "rf_low_confidence_static_labels": Counter(),
        "wireless_static_labels": Counter(),
        "wireless_js_clues": Counter(),
        "wireless_js_high_confidence": Counter(),
        "wireless_js_ipc_events": Counter(),
        "wireless_js_settings_keys": Counter(),
        "hid_product_ids": Counter(),
        "hid_command_categories": Counter(),
        "nsis_file_count": 0,
        "high_entropy_nsis_count": 0,
        "changelog_score": 0,
        "changelog_release_date": "",
        "changelog_download_urls": [],
        "changelog_keywords": Counter(),
        "changelog_category_scores": Counter(),
        "changelog_evidence": [],
        "warnings": [],
    }


def _merge_changelog_report(
    versions: dict[str, dict[str, Any]],
    path: Path,
    payload: dict[str, Any],
) -> int:
    source = str(payload.get("source") or "")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        entries = payload.get("top_entries")
    if not isinstance(entries, list):
        return 0
    merged = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        version_text = str(entry.get("version") or "").strip()
        if not version_text:
            continue
        score = _safe_int(entry.get("wireless_score"))
        if score <= 0:
            continue
        version = version_text if version_text.startswith("v") else f"v{version_text}"
        state = versions.setdefault(version, _empty_artifact_version_state(version))
        state["operations"]["analyze-changelog"] += 1
        state["reports"].append(
            {
                "path": str(path),
                "operation": "analyze-changelog",
                "source": source,
            }
        )
        state["changelog_score"] = max(int(state.get("changelog_score") or 0), score)
        release_date = str(entry.get("release_date") or "").strip()
        if release_date:
            state["changelog_release_date"] = release_date
        for url in _strings_from_any_list(entry.get("download_urls")):
            if url not in state["changelog_download_urls"]:
                state["changelog_download_urls"].append(url)
        for keyword in _strings_from_any_list(entry.get("matched_keywords")):
            state["changelog_keywords"][keyword] += 1
        state["changelog_category_scores"].update(_int_mapping(entry.get("category_scores")))
        _merge_changelog_evidence(state, entry.get("matched_lines"))
        merged += 1
    return merged


def _merge_changelog_evidence(state: dict[str, Any], matched_lines: Any) -> None:
    if not isinstance(matched_lines, list):
        return
    existing = {
        str(item.get("text") or "")
        for item in state["changelog_evidence"]
        if isinstance(item, dict)
    }
    for line in matched_lines:
        if not isinstance(line, dict):
            continue
        text = str(line.get("text") or "").strip()
        if not text or text in existing:
            continue
        state["changelog_evidence"].append(
            {
                "text": text,
                "keywords": _strings_from_any_list(line.get("keywords")),
                "score": _safe_int(line.get("score")),
            }
        )
        existing.add(text)
        if len(state["changelog_evidence"]) >= 8:
            break


def _artifact_report_version(path: Path, payload: dict[str, Any]) -> str:
    candidates = [
        path.name,
        str(payload.get("path", "")),
        str(payload.get("root", "")),
        str(payload.get("before", {}).get("path", "") if isinstance(payload.get("before"), dict) else ""),
        str(payload.get("after", {}).get("path", "") if isinstance(payload.get("after"), dict) else ""),
    ]
    for text in candidates:
        match = ARTIFACT_VERSION_RE.search(text)
        if match:
            version = match.group(0)
            return version if version.startswith("v") else f"v{version}"
    return "unknown"


def _merge_artifact_report(state: dict[str, Any], path: Path, payload: dict[str, Any]) -> None:
    operation = str(payload.get("operation"))
    state["operations"][operation] += 1
    state["reports"].append(
        {
            "path": str(path),
            "operation": operation,
            "source": str(payload.get("path") or payload.get("root") or ""),
        }
    )
    for warning in payload.get("warnings", []) if isinstance(payload.get("warnings"), list) else []:
        if isinstance(warning, str) and warning not in state["warnings"]:
            state["warnings"].append(warning)

    if operation in {"analyze-artifact", "analyze-artifact-tree"}:
        _merge_static_artifact_report(state, payload)
    elif operation == "extract-wireless-js":
        _merge_wireless_js_report(state, payload)
    elif operation == "extract-hid-js":
        _merge_hid_js_report(state, payload)
    elif operation == "diff-artifacts":
        _merge_diff_artifact_report(state, payload)


def _merge_static_artifact_report(state: dict[str, Any], payload: dict[str, Any]) -> None:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    state["categories"].update(_int_mapping(summary.get("categories")))
    state["confidence"].update(_int_mapping(summary.get("confidence")))
    for label in _strings_from_any_list(summary.get("high_confidence_patterns")):
        state["static_high_confidence"][label] += 1
        if label in RF_STATIC_LABELS:
            state["rf_static_labels"][label] += 1
        if label in RF_HIGH_CONFIDENCE_STATIC_LABELS:
            state["rf_high_confidence_static_labels"][label] += 1
        if label in RF_MEDIUM_CONFIDENCE_STATIC_LABELS:
            state["rf_low_confidence_static_labels"][label] += 1
        if _is_wireless_static_label(label):
            state["wireless_static_labels"][label] += 1

    for match in _artifact_report_matches(payload):
        label = str(match.get("label") or "")
        count = int(match.get("count", 1) or 1)
        confidence = str(match.get("confidence") or "")
        if confidence == "high":
            state["static_high_confidence"][label] += count
        if label in RF_STATIC_LABELS:
            state["rf_static_labels"][label] += count
        if label in RF_HIGH_CONFIDENCE_STATIC_LABELS:
            state["rf_high_confidence_static_labels"][label] += count
        if label in RF_MEDIUM_CONFIDENCE_STATIC_LABELS:
            state["rf_low_confidence_static_labels"][label] += count
        if _is_wireless_static_label(label):
            state["wireless_static_labels"][label] += count

    nsis_header = payload.get("nsis_header")
    if isinstance(nsis_header, dict):
        state["nsis_file_count"] += 1
        if float(payload.get("entropy_sample", 0.0) or 0.0) >= 7.5:
            state["high_entropy_nsis_count"] += 1
    state["nsis_file_count"] += int(summary.get("nsis_file_count", 0) or 0)


def _merge_wireless_js_report(state: dict[str, Any], payload: dict[str, Any]) -> None:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    for clue in payload.get("clues", []) if isinstance(payload.get("clues"), list) else []:
        if not isinstance(clue, dict):
            continue
        name = str(clue.get("name") or "")
        count = int(clue.get("count", 1) or 1)
        if name:
            state["wireless_js_clues"][name] += count
    for clue_name in _strings_from_any_list(summary.get("high_confidence_clues")):
        state["wireless_js_high_confidence"][clue_name] += 1
    for item in payload.get("ipc_events", []) if isinstance(payload.get("ipc_events"), list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("key") or "")
        if name:
            state["wireless_js_ipc_events"][name] += int(item.get("count", 1) or 1)
    for item in payload.get("settings_keys", []) if isinstance(payload.get("settings_keys"), list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("key") or "")
        if name:
            state["wireless_js_settings_keys"][name] += int(item.get("count", 1) or 1)


def _merge_hid_js_report(state: dict[str, Any], payload: dict[str, Any]) -> None:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    state["hid_command_categories"].update(_int_mapping(summary.get("command_categories")))
    for item in payload.get("product_ids", []) if isinstance(payload.get("product_ids"), list) else []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("vid_pid") or item.get("decimal") or item.get("key") or "")
        if key:
            state["hid_product_ids"][key] += int(item.get("count", 1) or 1)


def _merge_diff_artifact_report(state: dict[str, Any], payload: dict[str, Any]) -> None:
    delta = payload.get("static_match_delta", {}) if isinstance(payload.get("static_match_delta"), dict) else {}
    for item in delta.get("added", []) if isinstance(delta.get("added"), list) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "")
        count = max(1, int(item.get("after_count", 1) or 1))
        if label in RF_STATIC_LABELS:
            state["rf_static_labels"][label] += count
        if label in RF_HIGH_CONFIDENCE_STATIC_LABELS:
            state["rf_high_confidence_static_labels"][label] += count
        if label in RF_MEDIUM_CONFIDENCE_STATIC_LABELS:
            state["rf_low_confidence_static_labels"][label] += count
        if _is_wireless_static_label(label):
            state["wireless_static_labels"][label] += count


def _artifact_report_matches(payload: dict[str, Any]) -> list[dict[str, Any]]:
    matches = payload.get("matches")
    if isinstance(matches, list):
        return [item for item in matches if isinstance(item, dict)]
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    top_matches = summary.get("top_matches")
    if isinstance(top_matches, list):
        return [item for item in top_matches if isinstance(item, dict)]
    return []


def _artifact_version_payload(state: dict[str, Any]) -> dict[str, Any]:
    rf_static = dict(sorted(state["rf_static_labels"].items()))
    rf_high_static = dict(sorted(state["rf_high_confidence_static_labels"].items()))
    rf_low_static = dict(sorted(state["rf_low_confidence_static_labels"].items()))
    wireless_static = dict(sorted(state["wireless_static_labels"].items()))
    wireless_js = dict(sorted(state["wireless_js_clues"].items()))
    wireless_js_high = dict(sorted(state["wireless_js_high_confidence"].items()))
    wireless_js_ipc_events = dict(sorted(state["wireless_js_ipc_events"].items()))
    wireless_js_settings_keys = dict(sorted(state["wireless_js_settings_keys"].items()))
    hid_categories = dict(sorted(state["hid_command_categories"].items()))
    changelog_score = int(state.get("changelog_score") or 0)
    changelog_keywords = dict(sorted(state["changelog_keywords"].items()))
    assessment = _artifact_version_assessment(
        rf_static=rf_static,
        rf_high_static=rf_high_static,
        rf_low_static=rf_low_static,
        wireless_static=wireless_static,
        wireless_js=wireless_js,
        wireless_js_high=wireless_js_high,
        wireless_js_ipc_events=wireless_js_ipc_events,
        wireless_js_settings_keys=wireless_js_settings_keys,
        hid_categories=hid_categories,
    )
    return {
        "version": state["version"],
        "assessment": assessment,
        "capture_priority": _artifact_capture_priority(assessment),
        "report_count": len(state["reports"]),
        "operations": dict(sorted(state["operations"].items())),
        "static_categories": dict(sorted(state["categories"].items())),
        "static_confidence": dict(sorted(state["confidence"].items())),
        "static_high_confidence": dict(sorted(state["static_high_confidence"].items())),
        "rf_static_labels": rf_static,
        "rf_high_confidence_static_labels": rf_high_static,
        "rf_low_confidence_static_labels": rf_low_static,
        "wireless_static_labels": wireless_static,
        "wireless_js_clues": wireless_js,
        "wireless_js_high_confidence": wireless_js_high,
        "wireless_js_ipc_events": wireless_js_ipc_events,
        "wireless_js_settings_keys": wireless_js_settings_keys,
        "hid_product_ids": dict(sorted(state["hid_product_ids"].items())),
        "hid_command_categories": hid_categories,
        "nsis_file_count": state["nsis_file_count"],
        "high_entropy_nsis_count": state["high_entropy_nsis_count"],
        "changelog_score": changelog_score,
        "changelog_release_date": str(state.get("changelog_release_date") or ""),
        "changelog_download_urls": list(state.get("changelog_download_urls") or []),
        "changelog_keywords": changelog_keywords,
        "changelog_category_scores": dict(sorted(state["changelog_category_scores"].items())),
        "changelog_evidence": list(state.get("changelog_evidence") or []),
        "capture_recommendation_score": _artifact_capture_recommendation_score(
            assessment,
            changelog_score,
        ),
        "recommended_next_steps": _artifact_version_next_steps(
            assessment,
            rf_static=rf_static,
            rf_high_static=rf_high_static,
            rf_low_static=rf_low_static,
            wireless_js=wireless_js,
            wireless_js_ipc_events=wireless_js_ipc_events,
            wireless_js_settings_keys=wireless_js_settings_keys,
            hid_categories=hid_categories,
            nsis_file_count=state["nsis_file_count"],
            high_entropy_nsis_count=state["high_entropy_nsis_count"],
            changelog_score=changelog_score,
            changelog_keywords=changelog_keywords,
        ),
        "warnings": list(state["warnings"]),
        "reports": list(state["reports"]),
    }


def _artifact_version_assessment(
    *,
    rf_static: dict[str, int],
    rf_high_static: dict[str, int],
    rf_low_static: dict[str, int],
    wireless_static: dict[str, int],
    wireless_js: dict[str, int],
    wireless_js_high: dict[str, int],
    wireless_js_ipc_events: dict[str, int],
    wireless_js_settings_keys: dict[str, int],
    hid_categories: dict[str, int],
) -> str:
    if rf_high_static or (RF_JS_CLUE_NAMES & set(wireless_js)):
        return "rf-usb-protocol-lead"
    if rf_low_static:
        return "rf-usb-low-confidence-lead"
    if wireless_static or wireless_js_high:
        return "wireless-adjacent-lead"
    if hid_categories:
        return "wired-hid-fan-lead"
    return "no-actionable-wireless-evidence"


def _artifact_capture_priority(assessment: str) -> str:
    return {
        "rf-usb-protocol-lead": "high",
        "rf-usb-low-confidence-lead": "medium",
        "wireless-adjacent-lead": "medium",
        "wired-hid-fan-lead": "low",
    }.get(assessment, "low")


def _artifact_capture_recommendation_score(assessment: str, changelog_score: int) -> int:
    static_score = {
        "rf-usb-protocol-lead": 1000,
        "rf-usb-low-confidence-lead": 500,
        "wireless-adjacent-lead": 300,
        "wired-hid-fan-lead": 80,
    }.get(assessment, 0)
    return static_score + max(0, int(changelog_score))


def _artifact_version_next_steps(
    assessment: str,
    *,
    rf_static: dict[str, int],
    rf_high_static: dict[str, int],
    rf_low_static: dict[str, int],
    wireless_js: dict[str, int],
    wireless_js_ipc_events: dict[str, int],
    wireless_js_settings_keys: dict[str, int],
    hid_categories: dict[str, int],
    nsis_file_count: int,
    high_entropy_nsis_count: int,
    changelog_score: int = 0,
    changelog_keywords: dict[str, int] | None = None,
) -> list[str]:
    steps: list[str] = []
    changelog_keywords = changelog_keywords or {}
    if assessment == "rf-usb-protocol-lead":
        steps.append("Prioritize this version for Windows VM USBPcap capture of 0416:8040/0416:8041 traffic.")
        if rf_high_static:
            steps.append("Inspect the matched artifact contexts for RF sender/receiver VID:PID references and nearby IPC/native bridge code.")
        if RF_JS_CLUE_NAMES & set(wireless_js):
            steps.append("Inspect the matched JS files for the official L-Wireless sender/receiver code path.")
    elif assessment == "rf-usb-low-confidence-lead":
        steps.append("Treat raw little-endian RF VID/PID hits as low-confidence until confirmed in decompressed installed files or USBPcap traffic.")
        if rf_low_static:
            steps.append("Inspect surrounding bytes and entropy before using this version to prioritize a capture.")
    elif assessment == "wireless-adjacent-lead":
        steps.append("Treat this as adjacent wireless evidence; inspect JS/native bridge code before assuming RF control semantics.")
        if wireless_js_ipc_events:
            steps.append("Use extracted Electron message-queue events to drive Windows-side action tracing and USBPcap capture labels.")
        if wireless_js_settings_keys:
            steps.append("Use extracted settings keys to correlate L-Connect UI state with native bridge writes and persisted fan/light profiles.")
    elif assessment == "wired-hid-fan-lead":
        steps.append("Use these official HID command templates for AL/SL V2 wired fan support, but do not treat them as L-Wireless RF proof.")
    else:
        steps.append("No actionable static L-Wireless evidence was found; rely on USBPcap captures or deeper installer extraction.")
    if changelog_score > 0:
        keywords = ", ".join(sorted(changelog_keywords)[:6])
        suffix = f" ({keywords})" if keywords else ""
        steps.append(f"Official changelog wireless score is {changelog_score}; use it to prioritize scenario coverage{suffix}.")
    if hid_categories and assessment != "wired-hid-fan-lead":
        steps.append("Separate wired HID fan-controller leads from L-Wireless RF protocol evidence.")
    if nsis_file_count and high_entropy_nsis_count:
        steps.append("High-entropy NSIS payloads remain a static-analysis blocker; scan the installed app tree or capture runtime USB traffic.")
    return steps


def _artifact_matrix_summary(versions: list[dict[str, Any]]) -> dict[str, Any]:
    recommended = [
        item
        for item in sorted(
            versions,
            key=lambda value: (
                -int(value.get("capture_recommendation_score") or 0),
                _artifact_version_sort_key(value),
            ),
        )
        if int(item.get("capture_recommendation_score") or 0) > 0
    ]
    return {
        "rf_protocol_versions": [
            item["version"] for item in versions if item["assessment"] == "rf-usb-protocol-lead"
        ],
        "rf_low_confidence_versions": [
            item["version"] for item in versions if item["assessment"] == "rf-usb-low-confidence-lead"
        ],
        "wireless_adjacent_versions": [
            item["version"] for item in versions if item["assessment"] == "wireless-adjacent-lead"
        ],
        "wired_hid_fan_versions": [
            item["version"] for item in versions if item["assessment"] == "wired-hid-fan-lead"
        ],
        "wireless_js_interface_versions": [
            item["version"]
            for item in versions
            if item.get("wireless_js_ipc_events") or item.get("wireless_js_settings_keys")
        ],
        "high_priority_capture_versions": [
            item["version"] for item in versions if item["capture_priority"] == "high"
        ],
        "nsis_static_blocked_versions": [
            item["version"] for item in versions if int(item["high_entropy_nsis_count"]) > 0
        ],
        "changelog_top_versions": [
            item["version"]
            for item in sorted(versions, key=lambda value: -int(value.get("changelog_score") or 0))
            if int(item.get("changelog_score") or 0) > 0
        ][:12],
        "recommended_capture_versions": [
            {
                "version": item["version"],
                "score": int(item.get("capture_recommendation_score") or 0),
                "assessment": str(item.get("assessment") or ""),
                "changelog_score": int(item.get("changelog_score") or 0),
                "release_date": str(item.get("changelog_release_date") or ""),
            }
            for item in recommended[:12]
        ],
    }


def _artifact_version_sort_key(item: dict[str, Any]) -> tuple[int, tuple[int, ...] | str]:
    version = str(item.get("version", "unknown"))
    if version == "unknown":
        return (1, version)
    return (0, tuple(int(part) for part in version.removeprefix("v").split(".")))


def _int_mapping(value: Any) -> Counter[str]:
    counter: Counter[str] = Counter()
    if not isinstance(value, dict):
        return counter
    for key, count in value.items():
        try:
            counter[str(key)] += int(count)
        except (TypeError, ValueError):
            continue
    return counter


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _strings_from_any_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _is_wireless_static_label(label: str) -> bool:
    return any(keyword in label for keyword in WIRELESS_STATIC_LABEL_KEYWORDS)


def analyze_artifact_tree(
    path: Path,
    *,
    max_file_size: int = DEFAULT_TREE_MAX_FILE_SIZE,
) -> dict[str, Any]:
    if path.is_file():
        analysis = analyze_artifact_file(path)
        entry = _tree_entry_from_analysis(path.parent, path, analysis)
        return {
            "operation": "analyze-artifact-tree",
            "root": str(path),
            "root_type": "file",
            "max_file_size": max_file_size,
            "file_count": 1,
            "scanned_file_count": 1,
            "skipped_file_count": 0,
            "matched_file_count": 1 if analysis["match_count"] else 0,
            "path_matched_file_count": 1 if entry.get("path_match_count", 0) else 0,
            "files": [entry],
            "summary": _tree_summary([analysis], [], [entry]),
            "warnings": _tree_warnings([analysis], [], [entry]),
        }
    if not path.is_dir():
        raise LianLiWirelessError(f"artifact tree path is not a file or directory: {path}")

    analyses: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
        size = file_path.stat().st_size
        if size > max_file_size:
            entry = _skipped_tree_entry(path, file_path, max_file_size)
            files.append(entry)
            skipped.append(entry)
            continue
        analysis = analyze_artifact_file(file_path)
        analyses.append(analysis)
        files.append(_tree_entry_from_analysis(path, file_path, analysis))

    return {
        "operation": "analyze-artifact-tree",
        "root": str(path),
        "root_type": "directory",
        "max_file_size": max_file_size,
        "file_count": len(files),
        "scanned_file_count": len(analyses),
            "skipped_file_count": len(skipped),
            "matched_file_count": sum(1 for analysis in analyses if analysis["match_count"]),
            "path_matched_file_count": sum(1 for entry in files if entry.get("path_match_count", 0)),
            "files": files,
            "summary": _tree_summary(analyses, skipped, files),
            "warnings": _tree_warnings(analyses, skipped, files),
        }


def extract_hid_js_commands(
    path: Path,
    *,
    max_file_size: int = HID_JS_MAX_FILE_SIZE,
) -> dict[str, Any]:
    root, root_type, candidates = _hid_js_candidates(path)
    product_ids: dict[int, dict[str, Any]] = {}
    functions: dict[str, dict[str, Any]] = {}
    commands: dict[str, dict[str, Any]] = {}
    files: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for file_path in candidates:
        size = file_path.stat().st_size
        if size > max_file_size:
            skipped.append(
                {
                    "path": str(file_path),
                    "relative_path": _relative_path(root, file_path),
                    "size": size,
                    "skip_reason": f"file exceeds max_file_size ({max_file_size} bytes)",
                }
            )
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            skipped.append(
                {
                    "path": str(file_path),
                    "relative_path": _relative_path(root, file_path),
                    "size": size,
                    "skip_reason": f"unable to read file: {error}",
                }
            )
            continue
        file_entry = _extract_hid_js_file(root, file_path, text)
        if file_entry["match_count"]:
            files.append(file_entry)
            _merge_hid_js_aggregates(product_ids, file_entry["product_ids"])
            _merge_hid_js_aggregates(functions, file_entry["functions"])
            _merge_hid_js_aggregates(commands, file_entry["command_templates"])

    product_id_list = _sorted_hid_js_aggregates(product_ids)
    function_list = _sorted_hid_js_aggregates(functions)
    command_list = _sorted_hid_js_aggregates(commands)
    return {
        "operation": "extract-hid-js",
        "root": str(path),
        "root_type": root_type,
        "max_file_size": max_file_size,
        "js_file_count": len(candidates),
        "scanned_file_count": len(candidates) - len(skipped),
        "skipped_file_count": len(skipped),
        "matched_file_count": len(files),
        "product_ids": product_id_list,
        "functions": function_list,
        "command_templates": command_list,
        "summary": {
            "product_id_occurrences": sum(int(item["count"]) for item in product_id_list),
            "function_occurrences": sum(int(item["count"]) for item in function_list),
            "command_template_occurrences": sum(int(item["count"]) for item in command_list),
            "command_categories": _hid_js_command_categories(command_list),
        },
        "files": files,
        "skipped": skipped,
        "warnings": _hid_js_warnings(files, skipped, command_list),
    }


def extract_wireless_js_clues(
    path: Path,
    *,
    max_file_size: int = HID_JS_MAX_FILE_SIZE,
) -> dict[str, Any]:
    root, root_type, candidates = _hid_js_candidates(path)
    clues: dict[str, dict[str, Any]] = {}
    ipc_events: dict[str, dict[str, Any]] = {}
    settings_keys: dict[str, dict[str, Any]] = {}
    files: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for file_path in candidates:
        size = file_path.stat().st_size
        if size > max_file_size:
            skipped.append(
                {
                    "path": str(file_path),
                    "relative_path": _relative_path(root, file_path),
                    "size": size,
                    "skip_reason": f"file exceeds max_file_size ({max_file_size} bytes)",
                }
            )
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            skipped.append(
                {
                    "path": str(file_path),
                    "relative_path": _relative_path(root, file_path),
                    "size": size,
                    "skip_reason": f"unable to read file: {error}",
                }
            )
            continue

        file_entry = _extract_wireless_js_file(root, file_path, text)
        if file_entry["match_count"]:
            files.append(file_entry)
            _merge_js_clue_aggregates(clues, file_entry["clues"])
            _merge_js_clue_aggregates(ipc_events, file_entry["ipc_events"])
            _merge_js_clue_aggregates(settings_keys, file_entry["settings_keys"])

    clue_list = _sorted_hid_js_aggregates(clues)
    ipc_event_list = _sorted_hid_js_aggregates(ipc_events)
    settings_key_list = _sorted_hid_js_aggregates(settings_keys)
    return {
        "operation": "extract-wireless-js",
        "root": str(path),
        "root_type": root_type,
        "max_file_size": max_file_size,
        "js_file_count": len(candidates),
        "scanned_file_count": len(candidates) - len(skipped),
        "skipped_file_count": len(skipped),
        "matched_file_count": len(files),
        "clues": clue_list,
        "ipc_events": ipc_event_list,
        "settings_keys": settings_key_list,
        "summary": {
            "clue_occurrences": sum(int(item["count"]) for item in clue_list),
            "ipc_event_occurrences": sum(int(item["count"]) for item in ipc_event_list),
            "settings_key_occurrences": sum(int(item["count"]) for item in settings_key_list),
            "categories": _js_clue_categories(clue_list),
            "confidence": _js_clue_confidence(clue_list),
            "high_confidence_clues": [
                item["name"] for item in clue_list if item.get("confidence") == "high"
            ],
            "top_ipc_events": [item["name"] for item in ipc_event_list[:12]],
            "top_settings_keys": [item["name"] for item in settings_key_list[:12]],
        },
        "files": files,
        "skipped": skipped,
        "warnings": _wireless_js_warnings(files, skipped, clue_list),
    }


def _hid_js_candidates(path: Path) -> tuple[Path, str, list[Path]]:
    if path.is_file():
        return path.parent, "file", [path]
    if path.is_dir():
        return path, "directory", sorted(item for item in path.rglob("*.js") if item.is_file())
    raise LianLiWirelessError(f"HID JS path is not a file or directory: {path}")


def _extract_hid_js_file(root: Path, path: Path, text: str) -> dict[str, Any]:
    product_ids = []
    functions = []
    command_templates = []
    for decimal, metadata in HID_JS_PRODUCT_IDS.items():
        count = text.count(str(decimal))
        if not count:
            continue
        product_ids.append(
            {
                "key": str(decimal),
                "decimal": decimal,
                **metadata,
                "count": count,
                "file_count": 1,
                "files": [_relative_path(root, path)],
            }
        )
    for name in HID_JS_FUNCTION_NAMES:
        count = text.count(name)
        if not count:
            continue
        functions.append(
            {
                "key": name,
                "name": name,
                "count": count,
                "file_count": 1,
                "files": [_relative_path(root, path)],
            }
        )
    for pattern in HID_JS_COMMAND_PATTERNS:
        matches = list(pattern.regex.finditer(text))
        if not matches:
            continue
        command_templates.append(
            {
                "key": pattern.name,
                "name": pattern.name,
                "label": pattern.label,
                "category": pattern.category,
                "count": len(matches),
                "file_count": 1,
                "files": [_relative_path(root, path)],
                "reports": list(pattern.reports),
                "source_functions": list(pattern.source_functions),
                "contexts": [_hid_js_context(text, match.start(), match.end()) for match in matches[:4]],
                "note": pattern.note,
            }
        )
    return {
        "path": str(path),
        "relative_path": _relative_path(root, path),
        "size": path.stat().st_size,
        "match_count": sum(item["count"] for item in product_ids)
        + sum(item["count"] for item in functions)
        + sum(item["count"] for item in command_templates),
        "product_ids": product_ids,
        "functions": functions,
        "command_templates": command_templates,
    }


def _extract_wireless_js_file(root: Path, path: Path, text: str) -> dict[str, Any]:
    clues = []
    for pattern in WIRELESS_JS_CLUE_PATTERNS:
        matches = list(pattern.regex.finditer(text))
        if not matches:
            continue
        clues.append(
            {
                "key": pattern.name,
                "name": pattern.name,
                "label": pattern.label,
                "category": pattern.category,
                "confidence": pattern.confidence,
                "count": len(matches),
                "file_count": 1,
                "files": [_relative_path(root, path)],
                "contexts": [_hid_js_context(text, match.start(), match.end()) for match in matches[:4]],
                "note": pattern.note,
            }
        )
    ipc_events = _extract_js_ipc_events(root, path, text)
    settings_keys = _extract_js_settings_keys(root, path, text)
    return {
        "path": str(path),
        "relative_path": _relative_path(root, path),
        "size": path.stat().st_size,
        "match_count": sum(item["count"] for item in clues)
        + sum(item["count"] for item in ipc_events)
        + sum(item["count"] for item in settings_keys),
        "clues": clues,
        "ipc_events": ipc_events,
        "settings_keys": settings_keys,
    }


def _extract_js_ipc_events(root: Path, path: Path, text: str) -> list[dict[str, Any]]:
    events: dict[str, list[re.Match[str]]] = {}
    patterns = (
        re.compile(r"\bevent\s*:\s*['\"]([^'\"]+)['\"]"),
        re.compile(r"\bipcRenderer\.send\(\s*['\"]message-queue['\"]\s*,\s*['\"]([^'\"]+)['\"]"),
    )
    for pattern in patterns:
        for match in pattern.finditer(text):
            name = str(match.group(1)).strip()
            if name:
                events.setdefault(name, []).append(match)
    return _js_named_match_items(
        root,
        path,
        text,
        events,
        category="ipc-event",
        note="Electron message-queue event observed in official front-end JS.",
    )


def _extract_js_settings_keys(root: Path, path: Path, text: str) -> list[dict[str, Any]]:
    keys: dict[str, dict[str, Any]] = {}
    pattern = re.compile(r"\bpipe\.(readSettingsLike|writeSettings)\(\s*['\"]([^'\"]+)['\"]")
    for match in pattern.finditer(text):
        operation = str(match.group(1)).strip()
        settings_key = str(match.group(2)).strip()
        if not operation or not settings_key:
            continue
        name = f"{operation}:{settings_key}"
        entry = keys.setdefault(
            name,
            {
                "operation": operation,
                "settings_key": settings_key,
                "matches": [],
            },
        )
        entry["matches"].append(match)

    relative = _relative_path(root, path)
    return [
        {
            "key": name,
            "name": name,
            "category": "settings-key",
            "operation": str(entry["operation"]),
            "settings_key": str(entry["settings_key"]),
            "count": len(entry["matches"]),
            "file_count": 1,
            "files": [relative],
            "contexts": [
                _hid_js_context(text, match.start(), match.end())
                for match in entry["matches"][:4]
            ],
            "note": "L-Connect settings pipe key observed in official front-end JS.",
        }
        for name, entry in sorted(keys.items())
    ]


def _js_named_match_items(
    root: Path,
    path: Path,
    text: str,
    matches_by_name: dict[str, list[re.Match[str]]],
    *,
    category: str,
    note: str,
) -> list[dict[str, Any]]:
    relative = _relative_path(root, path)
    return [
        {
            "key": name,
            "name": name,
            "category": category,
            "count": len(matches),
            "file_count": 1,
            "files": [relative],
            "contexts": [_hid_js_context(text, match.start(), match.end()) for match in matches[:4]],
            "note": note,
        }
        for name, matches in sorted(matches_by_name.items())
    ]


def _merge_hid_js_aggregates(target: dict[Any, dict[str, Any]], items: list[dict[str, Any]]) -> None:
    for item in items:
        key = item["key"]
        if key not in target:
            merged = dict(item)
            merged["files"] = list(item.get("files", []))[:8]
            merged.pop("contexts", None)
            target[key] = merged
            continue
        existing = target[key]
        existing["count"] = int(existing["count"]) + int(item["count"])
        existing["file_count"] = int(existing["file_count"]) + 1
        for file_name in item.get("files", []):
            if len(existing["files"]) >= 8:
                break
            if file_name not in existing["files"]:
                existing["files"].append(file_name)


def _merge_js_clue_aggregates(target: dict[str, dict[str, Any]], items: list[dict[str, Any]]) -> None:
    _merge_hid_js_aggregates(target, items)


def _sorted_hid_js_aggregates(items: dict[Any, dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (dict(item) for item in items.values()),
        key=lambda item: (-int(item["count"]), str(item.get("name") or item.get("decimal") or item["key"])),
    )


def _hid_js_command_categories(command_templates: list[dict[str, Any]]) -> dict[str, int]:
    categories: dict[str, int] = {}
    for command in command_templates:
        category = str(command["category"])
        categories[category] = categories.get(category, 0) + int(command["count"])
    return dict(sorted(categories.items()))


def _js_clue_categories(clues: list[dict[str, Any]]) -> dict[str, int]:
    categories: dict[str, int] = {}
    for clue in clues:
        category = str(clue["category"])
        categories[category] = categories.get(category, 0) + int(clue["count"])
    return dict(sorted(categories.items()))


def _js_clue_confidence(clues: list[dict[str, Any]]) -> dict[str, int]:
    confidence: dict[str, int] = {}
    for clue in clues:
        level = str(clue["confidence"])
        confidence[level] = confidence.get(level, 0) + int(clue["count"])
    return dict(sorted(confidence.items()))


def _hid_js_warnings(
    files: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    command_templates: list[dict[str, Any]],
) -> list[str]:
    warnings = []
    if skipped:
        warnings.append(f"{len(skipped)} JS file(s) were skipped; rerun with --max-file-size to include them.")
    if not files:
        warnings.append("No official HID JS command patterns were found.")
    if command_templates:
        warnings.append(
            "Extracted commands are for official AL V2 / SL V2 wired HID fan controllers; they are not L-Wireless RF packets."
        )
    return warnings


def _wireless_js_warnings(
    files: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    clues: list[dict[str, Any]],
) -> list[str]:
    warnings = []
    if skipped:
        warnings.append(f"{len(skipped)} JS file(s) were skipped; rerun with --max-file-size to include them.")
    if not files:
        warnings.append("No wireless-related JS clues were found.")
        return warnings
    high_names = {str(item["name"]) for item in clues if item.get("confidence") == "high"}
    if not {"rf-sender-usb-id", "rf-receiver-usb-id"} & high_names:
        warnings.append("No high-confidence L-Wireless RF sender/receiver USB ID appeared in scanned JS.")
    if any(item.get("category") == "generic" for item in clues):
        warnings.append("Generic wireless/receiver/sender terms may come from UI or library code; prioritize high-confidence USB/product/API clues.")
    return warnings


def _hid_js_context(text: str, start: int, end: int) -> str:
    left = max(0, start - 80)
    right = min(len(text), end + 80)
    return _clean_text(text[left:right])


def _utf16_patterns(patterns: list[StaticPattern] | tuple[StaticPattern, ...]) -> list[StaticPattern]:
    utf16: list[StaticPattern] = []
    for pattern in patterns:
        if not _is_ascii_text(pattern.needle):
            continue
        utf16.append(
            StaticPattern(
                label=f"{pattern.label} UTF-16LE",
                needle=pattern.needle.decode("ascii").encode("utf-16le"),
                category=pattern.category,
                confidence=pattern.confidence,
                encoding="utf-16le",
                note=pattern.note,
            )
        )
    return utf16


def _scan_patterns(path: Path, patterns: list[StaticPattern]) -> list[dict[str, Any]]:
    states = {
        index: {
            "pattern": pattern,
            "count": 0,
            "offsets": [],
            "contexts": [],
        }
        for index, pattern in enumerate(patterns)
    }
    max_pattern_len = max(len(pattern.needle) for pattern in patterns)
    file_offset = 0
    tail = b""
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            data = tail + chunk
            data_offset = file_offset - len(tail)
            for index, pattern in enumerate(patterns):
                start = 0
                while True:
                    found = data.find(pattern.needle, start)
                    if found < 0:
                        break
                    absolute = data_offset + found
                    if absolute >= file_offset:
                        state = states[index]
                        state["count"] += 1
                        if len(state["offsets"]) < MAX_OFFSETS_PER_PATTERN:
                            state["offsets"].append(absolute)
                            state["contexts"].append(_context_preview(data, found, pattern))
                    start = found + 1
            if max_pattern_len > 1:
                tail = data[-(max_pattern_len - 1) :]
            file_offset += len(chunk)
    matches: list[dict[str, Any]] = []
    for state in states.values():
        if not state["count"]:
            continue
        pattern = state["pattern"]
        matches.append(
            {
                "label": pattern.label,
                "category": pattern.category,
                "confidence": pattern.confidence,
                "encoding": pattern.encoding,
                "count": state["count"],
                "offsets": state["offsets"],
                "contexts": state["contexts"],
                "note": pattern.note,
            }
        )
    return matches


def _context_preview(data: bytes, found: int, pattern: StaticPattern) -> str:
    start = max(0, found - CONTEXT_BYTES)
    end = min(len(data), found + len(pattern.needle) + CONTEXT_BYTES)
    raw = data[start:end]
    if pattern.encoding == "utf-16le":
        try:
            return _clean_text(raw.decode("utf-16le", errors="ignore"))
        except UnicodeDecodeError:
            pass
    if _is_mostly_printable(raw):
        return _clean_text(raw.decode("utf-8", errors="replace"))
    return raw.hex()


def _artifact_summary(matches: list[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, int] = {}
    confidence: dict[str, int] = {}
    high_confidence = []
    for match in matches:
        categories[str(match["category"])] = categories.get(str(match["category"]), 0) + int(match["count"])
        confidence[str(match["confidence"])] = confidence.get(str(match["confidence"]), 0) + int(match["count"])
        if match["confidence"] == "high":
            high_confidence.append(match["label"])
    return {
        "categories": dict(sorted(categories.items())),
        "confidence": dict(sorted(confidence.items())),
        "high_confidence_patterns": high_confidence,
    }


def _artifact_warnings(
    matches: list[dict[str, Any]],
    entropy: float,
    nsis_header: dict[str, Any] | None = None,
    nsis_probe: dict[str, Any] | None = None,
) -> list[str]:
    warnings: list[str] = []
    high_count = sum(int(match["count"]) for match in matches if match["confidence"] == "high")
    raw_medium = [
        match
        for match in matches
        if match["confidence"] == "medium"
        and match["encoding"] == "raw"
        and match["category"] == "usb-id"
    ]
    if entropy >= 7.5 and raw_medium:
        warnings.append(
            "High entropy input with raw medium-confidence hits; isolated 4-byte VID/PID matches may be accidental in compressed data."
        )
    if matches and high_count == 0:
        warnings.append("No high-confidence static protocol/product pattern was found.")
    if nsis_header and entropy >= 7.5:
        warnings.append(
            "NSIS payload is high entropy; scan the decompressed install tree or captured USB traffic for stronger evidence."
        )
    if nsis_header and nsis_header.get("unsupported_flags"):
        warnings.append(
            f"NSIS firstheader has non-standard flags 0x{int(nsis_header['unsupported_flags']):x}; stock 7-Zip may refuse NSIS extraction."
        )
    if nsis_probe and not nsis_probe["direct_decompression_hits"]:
        warnings.append("No direct zlib/deflate/bzip2/LZMA decompression probe succeeded at candidate NSIS offsets.")
    return warnings


def _diff_file_payload(path: Path, analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(path),
        "size": analysis["size"],
        "sha256": analysis["sha256"],
        "file_type": analysis["file_type"],
        "entropy_sample": analysis["entropy_sample"],
        "summary": analysis["summary"],
        "nsis_header": analysis["nsis_header"],
    }


def _common_prefix_size(before: Path, after: Path) -> int:
    offset = 0
    with before.open("rb") as left, after.open("rb") as right:
        while True:
            left_chunk = left.read(CHUNK_SIZE)
            right_chunk = right.read(CHUNK_SIZE)
            if not left_chunk or not right_chunk:
                return offset + _common_prefix_in_chunks(left_chunk, right_chunk)
            if left_chunk == right_chunk:
                offset += len(left_chunk)
                continue
            return offset + _common_prefix_in_chunks(left_chunk, right_chunk)


def _common_prefix_in_chunks(left: bytes, right: bytes) -> int:
    limit = min(len(left), len(right))
    for index in range(limit):
        if left[index] != right[index]:
            return index
    return limit


def _common_suffix_size(before: Path, after: Path, common_prefix: int) -> int:
    before_size = before.stat().st_size
    after_size = after.stat().st_size
    suffix = 0
    with before.open("rb") as left, after.open("rb") as right:
        while before_size - suffix > common_prefix and after_size - suffix > common_prefix:
            read_size = min(
                CHUNK_SIZE,
                before_size - suffix - common_prefix,
                after_size - suffix - common_prefix,
            )
            if read_size <= 0:
                break
            left.seek(before_size - suffix - read_size)
            right.seek(after_size - suffix - read_size)
            left_chunk = left.read(read_size)
            right_chunk = right.read(read_size)
            if left_chunk == right_chunk:
                suffix += read_size
                continue
            return suffix + _common_suffix_in_chunks(left_chunk, right_chunk)
    return suffix


def _common_suffix_in_chunks(left: bytes, right: bytes) -> int:
    limit = min(len(left), len(right))
    for index in range(1, limit + 1):
        if left[-index] != right[-index]:
            return index - 1
    return limit


def _changed_range(size: int, common_prefix: int, common_suffix: int) -> dict[str, int]:
    end = max(common_prefix, size - common_suffix)
    return {"start": common_prefix, "end": end}


def _bounded_ranges(ranges: list[dict[str, int]]) -> list[tuple[int, int]]:
    bounded = []
    for item in ranges[:DIFF_MAX_RANGES]:
        start = max(0, int(item["start"]))
        end = max(start, int(item["end"]))
        if end > start:
            bounded.append((start, end))
    return bounded


def _block_similarity(before: Path, after: Path, *, block_size: int) -> dict[str, Any]:
    before_hashes = _block_hashes(before, block_size)
    after_hashes = _block_hashes(after, block_size)
    compared = min(len(before_hashes), len(after_hashes))
    same_offset = sum(1 for index in range(compared) if before_hashes[index] == after_hashes[index])
    before_set = set(before_hashes)
    anywhere = sum(1 for digest in after_hashes if digest in before_set)
    longest_same_run = _longest_same_offset_run(before_hashes, after_hashes, compared)
    return {
        "block_size": block_size,
        "before_block_count": len(before_hashes),
        "after_block_count": len(after_hashes),
        "compared_block_count": compared,
        "same_offset_block_count": same_offset,
        "same_offset_ratio": round(same_offset / compared, 6) if compared else 1.0,
        "after_blocks_matching_any_before_count": anywhere,
        "after_blocks_matching_any_before_ratio": round(anywhere / len(after_hashes), 6) if after_hashes else 1.0,
        "longest_same_offset_run_blocks": longest_same_run,
    }


def _block_hashes(path: Path, block_size: int) -> list[bytes]:
    hashes: list[bytes] = []
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(block_size), b""):
            hashes.append(hashlib.sha256(chunk).digest())
    return hashes


def _longest_same_offset_run(left: list[bytes], right: list[bytes], compared: int) -> int:
    longest = 0
    current = 0
    for index in range(compared):
        if left[index] == right[index]:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _static_match_delta(
    before_matches: list[dict[str, Any]],
    after_matches: list[dict[str, Any]],
) -> dict[str, Any]:
    before_by_label = {str(match["label"]): match for match in before_matches}
    after_by_label = {str(match["label"]): match for match in after_matches}
    labels = sorted(set(before_by_label) | set(after_by_label))
    added = []
    removed = []
    changed = []
    for label in labels:
        before_count = int(before_by_label.get(label, {}).get("count", 0))
        after_count = int(after_by_label.get(label, {}).get("count", 0))
        if before_count == after_count:
            continue
        payload = {
            "label": label,
            "before_count": before_count,
            "after_count": after_count,
            "delta": after_count - before_count,
        }
        if before_count == 0:
            added.append(payload)
        elif after_count == 0:
            removed.append(payload)
        else:
            changed.append(payload)
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def _matches_in_ranges(
    matches: list[dict[str, Any]],
    ranges: list[tuple[int, int]],
) -> list[dict[str, Any]]:
    if not ranges:
        return []
    selected = []
    for match in matches:
        offsets = [
            int(offset)
            for offset in match.get("offsets", [])
            if _offset_in_ranges(int(offset), ranges)
        ]
        if not offsets:
            continue
        selected.append(
            {
                "label": match["label"],
                "category": match["category"],
                "confidence": match["confidence"],
                "encoding": match["encoding"],
                "count_in_sampled_offsets": len(offsets),
                "offsets": offsets,
            }
        )
    return selected


def _offset_in_ranges(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)


def _scan_magic_offsets(path: Path, ranges: list[tuple[int, int]]) -> list[dict[str, Any]]:
    if not ranges:
        return []
    states = {
        index: {
            "pattern": pattern,
            "count": 0,
            "offsets": [],
        }
        for index, pattern in enumerate(MAGIC_PATTERNS)
    }
    max_pattern_len = max(len(pattern.needle) for pattern in MAGIC_PATTERNS)
    with path.open("rb") as handle:
        for start, end in ranges:
            _scan_magic_range(handle, start, end, states, max_pattern_len)
    result = []
    with path.open("rb") as handle:
        for state in states.values():
            if not state["count"]:
                continue
            pattern = state["pattern"]
            sampled_offsets = [
                {
                    "offset": int(offset),
                    "validation": _validate_magic_at(handle, pattern, int(offset)),
                }
                for offset in state["offsets"]
            ]
            plausible_count = sum(
                1 for item in sampled_offsets if item["validation"].get("plausible") is True
            )
            result.append(
                {
                    "label": pattern.label,
                    "count": state["count"],
                    "offsets": state["offsets"],
                    "plausible_sampled_offset_count": plausible_count,
                    "sampled_offsets": sampled_offsets,
                    "note": pattern.note,
                }
            )
    return result

def _scan_magic_range(
    handle,  # noqa: ANN001
    start: int,
    end: int,
    states: dict[int, dict[str, Any]],
    max_pattern_len: int,
) -> None:
    handle.seek(start)
    file_offset = start
    tail = b""
    remaining = end - start
    while remaining > 0:
        chunk = handle.read(min(CHUNK_SIZE, remaining))
        if not chunk:
            break
        data = tail + chunk
        data_offset = file_offset - len(tail)
        for state in states.values():
            pattern = state["pattern"]
            search = 0
            while True:
                found = data.find(pattern.needle, search)
                if found < 0:
                    break
                absolute = data_offset + found
                if file_offset <= absolute < end:
                    state["count"] += 1
                    if len(state["offsets"]) < MAX_OFFSETS_PER_PATTERN:
                        state["offsets"].append(absolute)
                search = found + 1
        if max_pattern_len > 1:
            tail = data[-(max_pattern_len - 1) :]
        file_offset += len(chunk)
        remaining -= len(chunk)


def _validate_magic_at(handle, pattern: MagicPattern, offset: int) -> dict[str, Any]:  # noqa: ANN001
    handle.seek(offset)
    sample = handle.read(512)
    label = pattern.label
    if label == "ZIP local file header":
        return _validate_zip_local_header(sample)
    if label == "PE MZ header":
        return _validate_pe_header(sample)
    if label == "gzip header":
        return {
            "plausible": len(sample) >= 3 and sample[:2] == b"\x1f\x8b" and sample[2] == 8,
            "method": sample[2] if len(sample) >= 3 else None,
        }
    if label == "bzip2 header":
        return {
            "plausible": len(sample) >= 4 and sample[:3] == b"BZh" and 49 <= sample[3] <= 57,
            "level": chr(sample[3]) if len(sample) >= 4 and 32 <= sample[3] <= 126 else None,
        }
    return {"plausible": sample.startswith(pattern.needle)}


def _validate_zip_local_header(sample: bytes) -> dict[str, Any]:
    if len(sample) < 30 or not sample.startswith(b"PK\x03\x04"):
        return {"plausible": False}
    version_needed = int.from_bytes(sample[4:6], "little")
    flags = int.from_bytes(sample[6:8], "little")
    method = int.from_bytes(sample[8:10], "little")
    compressed_size = int.from_bytes(sample[18:22], "little")
    uncompressed_size = int.from_bytes(sample[22:26], "little")
    name_length = int.from_bytes(sample[26:28], "little")
    extra_length = int.from_bytes(sample[28:30], "little")
    name = sample[30 : 30 + min(name_length, 120)]
    plausible_name = 0 < name_length < 512 and all(byte >= 32 and byte != 127 for byte in name)
    plausible = (
        10 <= version_needed <= 63
        and method in {0, 8, 9, 12, 14, 93, 95, 98}
        and name_length > 0
        and name_length < 512
        and extra_length < 4096
        and plausible_name
    )
    return {
        "plausible": plausible,
        "version_needed": version_needed,
        "flags": flags,
        "method": method,
        "compressed_size": compressed_size,
        "uncompressed_size": uncompressed_size,
        "name_length": name_length,
        "extra_length": extra_length,
        "name_preview": _clean_text(name.decode("utf-8", errors="replace")),
    }


def _validate_pe_header(sample: bytes) -> dict[str, Any]:
    if len(sample) < 64 or not sample.startswith(b"MZ"):
        return {"plausible": False}
    pe_offset = int.from_bytes(sample[60:64], "little")
    plausible = 64 <= pe_offset <= len(sample) - 4 and sample[pe_offset : pe_offset + 4] == b"PE\x00\x00"
    return {
        "plausible": plausible,
        "pe_offset": pe_offset,
    }


def _diff_warnings(
    before_analysis: dict[str, Any],
    after_analysis: dict[str, Any],
    block_similarity: dict[str, Any],
    after_changed: dict[str, int],
) -> list[str]:
    warnings: list[str] = []
    if before_analysis["entropy_sample"] >= 7.5 and after_analysis["entropy_sample"] >= 7.5:
        warnings.append("Both artifacts are high entropy; binary differences may reflect compressed payload churn rather than direct protocol changes.")
    if block_similarity["after_blocks_matching_any_before_ratio"] < 0.05:
        warnings.append("Very few fixed-size blocks are shared; prioritize decompression or USB capture over byte-level diffing.")
    if after_changed["end"] > after_changed["start"] and not _high_confidence_added(before_analysis, after_analysis):
        warnings.append("No new high-confidence static protocol/product clue was added in the newer artifact.")
    return warnings


def _high_confidence_added(before_analysis: dict[str, Any], after_analysis: dict[str, Any]) -> bool:
    before_high = {
        str(match["label"])
        for match in before_analysis["matches"]
        if match["confidence"] == "high"
    }
    after_high = {
        str(match["label"])
        for match in after_analysis["matches"]
        if match["confidence"] == "high"
    }
    return bool(after_high - before_high)


def _file_type(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            magic = handle.read(32)
    except OSError as error:
        raise LianLiWirelessError(f"unable to read artifact {path}: {error}") from error
    if _looks_like_nsis(magic):
        return "nsis"
    if magic.startswith(b"PK\x03\x04"):
        return "zip"
    if magic.startswith(b"MZ"):
        return "pe"
    if magic.startswith(b"\x7fELF"):
        return "elf"
    return "data"


def _nsis_header(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as handle:
            raw = handle.read(32)
    except OSError as error:
        raise LianLiWirelessError(f"unable to read artifact {path}: {error}") from error
    if not _looks_like_nsis(raw):
        return None
    size = path.stat().st_size
    flags = int.from_bytes(raw[0:4], "little")
    header_size = int.from_bytes(raw[20:24], "little")
    data_size = int.from_bytes(raw[24:28], "little")
    payload_offset = NSIS_FIRSTHEADER_SIZE
    return {
        "signature": "DEADBEEF NullsoftInst",
        "flags": flags,
        "standard_flags_mask": NSIS_STANDARD_FLAGS_MASK,
        "standard_flags": _nsis_standard_flags(flags),
        "unsupported_flags": flags & ~NSIS_STANDARD_FLAGS_MASK,
        "header_size": header_size,
        "data_size": data_size,
        "payload_offset": payload_offset,
        "file_size": size,
        "size_delta": size - data_size,
    }


def _looks_like_nsis(raw: bytes) -> bool:
    return len(raw) >= 20 and raw[4:20] == NSIS_SIGNATURE


def _nsis_standard_flags(flags: int) -> list[str]:
    names = []
    if flags & 0x01:
        names.append("uninstall")
    if flags & 0x02:
        names.append("silent")
    if flags & 0x04:
        names.append("no-crc")
    if flags & 0x08:
        names.append("force-crc")
    return names


def _probe_nsis_payload(path: Path, nsis_header: dict[str, Any] | None) -> dict[str, Any] | None:
    if not nsis_header:
        return None
    offsets = _nsis_probe_offsets(path, nsis_header)
    attempts = []
    for offset in offsets:
        sample = _read_sample(path, offset, NSIS_PROBE_SAMPLE_SIZE)
        attempts.append(
            {
                "offset": offset,
                "head_hex": sample[:16].hex(),
                "methods": _probe_compression_methods(sample),
            }
        )
    direct_hits = [
        {
            "offset": attempt["offset"],
            "method": method["method"],
            "output_size": method["output_size"],
            "output_prefix_hex": method["output_prefix_hex"],
            "output_prefix_text": method["output_prefix_text"],
        }
        for attempt in attempts
        for method in attempt["methods"]
        if method["ok"]
    ]
    return {
        "standard_flags_supported": nsis_header["unsupported_flags"] == 0,
        "unsupported_flags": nsis_header["unsupported_flags"],
        "arc_size_delta": nsis_header["size_delta"],
        "probe_sample_size": NSIS_PROBE_SAMPLE_SIZE,
        "attempts": attempts,
        "direct_decompression_hits": direct_hits,
    }


def _nsis_probe_offsets(path: Path, nsis_header: dict[str, Any]) -> list[int]:
    size = path.stat().st_size
    payload_offset = int(nsis_header["payload_offset"])
    header_size = int(nsis_header["header_size"])
    candidates = [
        payload_offset,
        payload_offset + 4,
        payload_offset + 8,
        header_size,
        header_size + payload_offset,
    ]
    return [
        offset
        for offset in dict.fromkeys(candidates)
        if 0 <= offset < size
    ]


def _read_sample(path: Path, offset: int, size: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(offset)
        return handle.read(size)


def _probe_compression_methods(sample: bytes) -> list[dict[str, Any]]:
    methods = [
        ("zlib", lambda data: zlib.decompress(data)),
        ("raw-deflate", lambda data: zlib.decompress(data, -15)),
        ("bzip2", lambda data: bz2.decompress(data)),
        ("xz", lambda data: lzma.decompress(data, format=lzma.FORMAT_XZ)),
        ("lzma-alone", lambda data: lzma.decompress(data, format=lzma.FORMAT_ALONE)),
        ("nsis-lzma-raw", _decompress_nsis_lzma_raw),
    ]
    results = []
    for name, decoder in methods:
        try:
            output = decoder(sample)
        except Exception as error:  # noqa: BLE001 - decoder diagnostics are part of the output
            results.append(
                {
                    "method": name,
                    "ok": False,
                    "error": _short_error(error),
                }
            )
            continue
        results.append(
            {
                "method": name,
                "ok": True,
                "output_size": len(output),
                "output_prefix_hex": output[:48].hex(),
                "output_prefix_text": _clean_text(output[:160].decode("utf-8", errors="replace")),
            }
        )
    return results


def _decompress_nsis_lzma_raw(sample: bytes) -> bytes:
    if len(sample) < 6:
        raise LianLiWirelessError("sample too small for NSIS raw LZMA")
    prop = sample[0]
    lc = prop % 9
    rest = prop // 9
    lp = rest % 5
    pb = rest // 5
    dictionary_size = int.from_bytes(sample[1:5], "little")
    if pb > 4 or dictionary_size <= 0:
        raise LianLiWirelessError(
            f"invalid raw LZMA properties lc={lc} lp={lp} pb={pb} dict={dictionary_size}"
        )
    decoder = lzma.LZMADecompressor(
        format=lzma.FORMAT_RAW,
        filters=[
            {
                "id": lzma.FILTER_LZMA1,
                "dict_size": dictionary_size,
                "lc": lc,
                "lp": lp,
                "pb": pb,
            }
        ],
    )
    return decoder.decompress(sample[5:])


def _short_error(error: Exception) -> str:
    text = str(error).splitlines()[0] if str(error) else error.__class__.__name__
    return text[:160]


def _tree_entry_from_analysis(root: Path, path: Path, analysis: dict[str, Any]) -> dict[str, Any]:
    path_matches = _path_matches(root, path)
    return {
        "path": str(path),
        "relative_path": _relative_path(root, path),
        "size": analysis["size"],
        "sha256": analysis["sha256"],
        "file_type": analysis["file_type"],
        "nsis_header": analysis["nsis_header"],
        "nsis_probe": analysis["nsis_probe"],
        "match_count": analysis["match_count"],
        "matched_pattern_count": analysis["matched_pattern_count"],
        "matches": analysis["matches"],
        "path_match_count": sum(match["count"] for match in path_matches),
        "path_matches": path_matches,
        "warnings": analysis["warnings"],
    }


def _skipped_tree_entry(root: Path, path: Path, max_file_size: int) -> dict[str, Any]:
    path_matches = _path_matches(root, path)
    return {
        "path": str(path),
        "relative_path": _relative_path(root, path),
        "size": path.stat().st_size,
        "file_type": _file_type(path),
        "nsis_header": _nsis_header(path),
        "nsis_probe": None,
        "path_match_count": sum(match["count"] for match in path_matches),
        "path_matches": path_matches,
        "skipped": True,
        "skip_reason": f"file exceeds max_file_size ({max_file_size} bytes)",
    }


def _relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def _path_matches(root: Path, path: Path) -> list[dict[str, Any]]:
    relative_path = _relative_path(root, path).replace("\\", "/")
    searchable = relative_path.lower()
    matches: list[dict[str, Any]] = []
    for pattern in PATH_PATTERNS:
        needle = pattern.needle.decode("ascii").lower()
        offsets: list[int] = []
        start = 0
        while True:
            found = searchable.find(needle, start)
            if found < 0:
                break
            if len(offsets) < MAX_OFFSETS_PER_PATTERN:
                offsets.append(found)
            start = found + 1
        if not offsets:
            continue
        matches.append(
            {
                "label": pattern.label,
                "category": pattern.category,
                "confidence": pattern.confidence,
                "encoding": pattern.encoding,
                "count": searchable.count(needle),
                "offsets": offsets,
                "contexts": [relative_path],
                "note": pattern.note,
            }
        )
    return matches


def _tree_summary(
    analyses: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    categories: dict[str, int] = {}
    confidence: dict[str, int] = {}
    labels: dict[str, dict[str, Any]] = {}
    nsis_files = []
    entries = files or []
    for analysis in analyses:
        if analysis["nsis_header"]:
            nsis_files.append(str(analysis["path"]))
        for match in analysis["matches"]:
            _add_tree_match_summary(categories, confidence, labels, match, str(analysis["path"]))
    for entry in entries:
        for match in entry.get("path_matches", []):
            _add_tree_match_summary(categories, confidence, labels, match, str(entry["path"]))
    for entry in skipped:
        if entry.get("nsis_header"):
            nsis_files.append(str(entry["path"]))
    top_matches = sorted(
        labels.values(),
        key=lambda item: (-int(item["count"]), str(item["label"])),
    )[:20]
    return {
        "categories": dict(sorted(categories.items())),
        "confidence": dict(sorted(confidence.items())),
        "top_matches": top_matches,
        "nsis_file_count": len(nsis_files),
        "nsis_files": nsis_files[:16],
    }


def _add_tree_match_summary(
    categories: dict[str, int],
    confidence: dict[str, int],
    labels: dict[str, dict[str, Any]],
    match: dict[str, Any],
    path: str,
) -> None:
    category = str(match["category"])
    level = str(match["confidence"])
    count = int(match["count"])
    label = str(match["label"])
    categories[category] = categories.get(category, 0) + count
    confidence[level] = confidence.get(level, 0) + count
    label_entry = labels.setdefault(
        label,
        {
            "label": label,
            "category": category,
            "confidence": level,
            "count": 0,
            "file_count": 0,
            "files": [],
        },
    )
    label_entry["count"] += count
    label_entry["file_count"] += 1
    if len(label_entry["files"]) < 8:
        label_entry["files"].append(path)


def _tree_warnings(
    analyses: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    files: list[dict[str, Any]] | None = None,
) -> list[str]:
    warnings: list[str] = []
    entries = files or []
    if skipped:
        warnings.append(
            f"{len(skipped)} file(s) were skipped by size; rerun with --max-file-size to include large installer payloads."
        )
    file_warning_count = sum(1 for analysis in analyses if analysis["warnings"])
    if file_warning_count:
        warnings.append(f"{file_warning_count} scanned file(s) reported warnings; inspect files[].warnings.")
    high_count = sum(
        int(match["count"])
        for analysis in analyses
        for match in analysis["matches"]
        if match["confidence"] == "high"
    )
    high_count += sum(
        int(match["count"])
        for entry in entries
        for match in entry.get("path_matches", [])
        if match["confidence"] == "high"
    )
    if analyses and high_count == 0:
        warnings.append("No high-confidence static protocol/product pattern was found in scanned files.")
    if any(entry.get("nsis_header") for entry in skipped):
        warnings.append("A skipped NSIS payload may contain compressed installer contents.")
    return warnings


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entropy_sample(path: Path, *, sample_size: int = CHUNK_SIZE) -> float:
    counts = [0] * 256
    total = 0
    size = path.stat().st_size
    offsets = [0]
    if size > sample_size * 2:
        offsets.append(max(0, size // 2 - sample_size // 2))
        offsets.append(max(0, size - sample_size))
    with path.open("rb") as handle:
        for offset in dict.fromkeys(offsets):
            handle.seek(offset)
            sample = handle.read(min(sample_size, max(0, size - offset)))
            for byte in sample:
                counts[byte] += 1
                total += 1
    if not total:
        return 0.0
    entropy = 0.0
    for count in counts:
        if not count:
            continue
        probability = count / total
        entropy -= probability * math.log2(probability)
    return round(entropy, 4)


def _is_ascii_text(raw: bytes) -> bool:
    return all(32 <= byte <= 126 for byte in raw)


def _is_mostly_printable(raw: bytes) -> bool:
    if not raw:
        return False
    printable = sum(1 for byte in raw if byte in (9, 10, 13) or 32 <= byte <= 126)
    return printable / len(raw) >= 0.75


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\x00", " ").split())[:220]
