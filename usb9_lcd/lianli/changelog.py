from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from usb9_lcd.lianli.wireless import LianLiWirelessError


DEFAULT_CHANGELOG_URL = "https://lian-li.com/zh-TW/l-connect3/l3-changelog/"
DEFAULT_TOP_CHANGELOG_ENTRIES = 8
CHANGELOG_FETCH_TIMEOUT_SEC = 30

VERSION_RE = re.compile(
    r"\bL3\s+v(?P<version>\d+(?:\.\d+){1,3}(?:[-._a-zA-Z0-9]+)?)\b",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"(?:Released\s+on|發表於|发布于|發佈於|发布日期|發行日期)\s*[:：]\s*"
    r"(?P<date>\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4})",
    re.IGNORECASE,
)
DOWNLOAD_TEXT_RE = re.compile(r"download|下載|下载|軟體下載|软件下载", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s\"'<>]+")


@dataclass(frozen=True)
class _ChangelogToken:
    text: str
    href: str = ""


@dataclass(frozen=True)
class _ChangelogKeyword:
    name: str
    label: str
    category: str
    score: int
    regex: re.Pattern[str]


CHANGELOG_KEYWORDS = (
    _ChangelogKeyword(
        "l-wireless",
        "L-Wireless Utility / sync",
        "product",
        8,
        re.compile(
            r"\bL[- ]?Wireless\b|(?<![A-Za-z])L[- ]?(?:無線|无线)(?:\s*(?:Utility|Sync|控制器|同步))?",
            re.IGNORECASE,
        ),
    ),
    _ChangelogKeyword(
        "rf",
        "RF transport/controller",
        "transport",
        7,
        re.compile(r"\bRF\b|射頻|射频", re.IGNORECASE),
    ),
    _ChangelogKeyword(
        "wireless-controller",
        "wireless controller / dongle / TX-RX",
        "transport",
        7,
        re.compile(
            r"無線控制器|无线控制器|wireless controller|加密狗|dongle|\bTX\b|\bRX\b",
            re.IGNORECASE,
        ),
    ),
    _ChangelogKeyword(
        "wireless-fan",
        "wireless fan product",
        "fan",
        6,
        re.compile(
            r"無線風扇|无线风扇|wireless fans?|UNI FAN[^。.\n]{0,40}Wireless|"
            r"SL[- ]?Wireless|TL[- ]?Wireless|CL\s*無線|CL\s*无线|SL INF Wireless|"
            r"SL-INF\s*無線|SL-INF\s*无线",
            re.IGNORECASE,
        ),
    ),
    _ChangelogKeyword(
        "wireless-lcd",
        "wireless LCD product",
        "lcd",
        5,
        re.compile(
            r"無線\s*LCD|无线\s*LCD|Wireless LCD|TL[- ]?Wireless LCD|SL[- ]?Wireless LCD|"
            r"無線液晶|无线液晶",
            re.IGNORECASE,
        ),
    ),
    _ChangelogKeyword(
        "binding",
        "bind / unbind / pairing",
        "binding",
        6,
        re.compile(
            r"綁定|绑定|解除綁定|解除绑定|取消綁定|取消绑定|\bbind\b|\bunbind\b|配對|配对",
            re.IGNORECASE,
        ),
    ),
    _ChangelogKeyword(
        "device-identification",
        "device identification",
        "binding",
        6,
        re.compile(
            r"設備識別|设备识别|Identify RF Device|未知設備|未知设备|device identification",
            re.IGNORECASE,
        ),
    ),
    _ChangelogKeyword(
        "motherboard-sync",
        "motherboard PWM/RPM sync",
        "fan",
        8,
        re.compile(
            r"\bMB\s*(?:RPM|PWM)\b|主機板[^。.\n]{0,24}(?:RPM|PWM)|主板[^。.\n]{0,24}(?:RPM|PWM)",
            re.IGNORECASE,
        ),
    ),
    _ChangelogKeyword(
        "rpm-pwm",
        "fan speed / RPM / PWM",
        "fan",
        5,
        re.compile(
            r"\bMB RPM\b|主機板[^。.\n]{0,24}(?:RPM|PWM)|主板[^。.\n]{0,24}(?:RPM|PWM)|"
            r"4[- ]?pin PWM|風扇速度|风扇速度|fan speed|\bRPM\b|\bPWM\b",
            re.IGNORECASE,
        ),
    ),
    _ChangelogKeyword(
        "lighting",
        "lighting / RGB / Quick Sync W",
        "lighting",
        4,
        re.compile(
            r"燈光|灯光|照明|light(?:ing)?|static color|靜態色彩|静态颜色|彩虹|rainbow|快速同步|Quick Sync W",
            re.IGNORECASE,
        ),
    ),
    _ChangelogKeyword(
        "firmware",
        "firmware dependency",
        "firmware",
        3,
        re.compile(r"韌體|固件|firmware", re.IGNORECASE),
    ),
    _ChangelogKeyword(
        "settings-memory",
        "settings persistence",
        "state",
        3,
        re.compile(r"記憶|记忆|儲存|保存|save|遺失|丟失|丢失|reset", re.IGNORECASE),
    ),
)
HARD_CHANGELOG_KEYWORDS = {
    "l-wireless",
    "rf",
    "wireless-controller",
    "wireless-fan",
    "wireless-lcd",
    "binding",
    "device-identification",
}


class _ChangelogHtmlParser(HTMLParser):
    def __init__(self, *, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.tokens: list[_ChangelogToken] = []
        self._href_stack: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self._href_stack.append(urljoin(self.base_url, href) if href else "")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "a" and self._href_stack:
            self._href_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = _clean_text(data)
        if text:
            self.tokens.append(_ChangelogToken(text=text, href=self._href_stack[-1] if self._href_stack else ""))


def analyze_lconnect_changelog(
    source: str | Path = DEFAULT_CHANGELOG_URL,
    *,
    top: int = DEFAULT_TOP_CHANGELOG_ENTRIES,
) -> dict[str, Any]:
    text, resolved_source, source_type = load_lconnect_changelog_source(source)
    payload = analyze_lconnect_changelog_text(text, source=resolved_source, top=top)
    payload["source_type"] = source_type
    return payload


def load_lconnect_changelog_source(source: str | Path) -> tuple[str, str, str]:
    if isinstance(source, Path):
        path = source.expanduser()
        if not path.exists():
            raise LianLiWirelessError(f"changelog source path does not exist: {path}")
        return path.read_text(encoding="utf-8", errors="replace"), str(path), "file"

    source_text = str(source)
    if source_text.startswith(("http://", "https://")):
        request = Request(source_text, headers={"User-Agent": "usb9-lianli-probe/1.0"})
        with urlopen(request, timeout=CHANGELOG_FETCH_TIMEOUT_SEC) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace"), source_text, "url"

    path = Path(source_text).expanduser()
    if not path.exists():
        raise LianLiWirelessError(f"changelog source path does not exist: {path}")
    return path.read_text(encoding="utf-8", errors="replace"), str(path), "file"


def analyze_lconnect_changelog_text(
    text: str,
    *,
    source: str = "",
    top: int = DEFAULT_TOP_CHANGELOG_ENTRIES,
) -> dict[str, Any]:
    tokens = _changelog_tokens(text, base_url=source if source.startswith(("http://", "https://")) else "")
    entries = _parse_changelog_entries(tokens)
    wireless_entries = [entry for entry in entries if int(entry["wireless_score"]) > 0]
    sorted_wireless = sorted(
        wireless_entries,
        key=lambda entry: (
            -int(entry["wireless_score"]),
            -_date_sort_value(str(entry.get("release_date", ""))),
            str(entry.get("version", "")),
        ),
    )
    top_entries = sorted_wireless[: max(0, int(top))]
    return {
        "operation": "analyze-changelog",
        "source": source,
        "entry_count": len(entries),
        "wireless_entry_count": len(wireless_entries),
        "top_limit": top,
        "summary": {
            "keyword_counts": _keyword_counts(wireless_entries),
            "category_scores": _category_scores(wireless_entries),
            "top_versions": [entry["version"] for entry in top_entries],
            "recommended_download_versions": _recommended_download_versions(top_entries),
        },
        "top_entries": top_entries,
        "entries": entries,
        "warnings": _changelog_warnings(entries, wireless_entries),
    }


def _changelog_tokens(text: str, *, base_url: str = "") -> list[_ChangelogToken]:
    if "<" in text and re.search(r"</?(?:html|body|a|div|section|h[1-6]|p|li)\b", text, re.IGNORECASE):
        parser = _ChangelogHtmlParser(base_url=base_url)
        parser.feed(text)
        return parser.tokens
    return [_ChangelogToken(_clean_text(line)) for line in text.splitlines() if _clean_text(line)]


def _parse_changelog_entries(tokens: list[_ChangelogToken]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_lines: list[_ChangelogToken] = []

    for token in tokens:
        match = VERSION_RE.search(token.text)
        if match:
            if current is not None:
                entries.append(_finalize_entry(current, current_lines))
            current = {"version": match.group("version"), "raw_header": token.text}
            current_lines = []
            continue
        if current is not None:
            current_lines.append(token)

    if current is not None:
        entries.append(_finalize_entry(current, current_lines))
    return entries


def _finalize_entry(header: dict[str, Any], tokens: list[_ChangelogToken]) -> dict[str, Any]:
    release_date = ""
    download_urls: list[str] = []
    body_lines: list[str] = []
    for token in tokens:
        date_match = DATE_RE.search(token.text)
        if date_match:
            release_date = _normalize_date(date_match.group("date"))
            continue
        if token.href and _is_download_link(token):
            _append_unique(download_urls, token.href)
        for url in URL_RE.findall(token.text):
            if _is_download_url(url):
                _append_unique(download_urls, url)
        if DOWNLOAD_TEXT_RE.fullmatch(token.text):
            continue
        if VERSION_RE.search(token.text):
            continue
        body_lines.append(token.text)

    score, matched_keywords, matched_lines, category_scores = _score_changelog_lines(body_lines)
    return {
        "version": header["version"],
        "release_date": release_date,
        "download_urls": download_urls,
        "wireless_score": score,
        "matched_keywords": matched_keywords,
        "category_scores": category_scores,
        "matched_lines": matched_lines,
        "line_count": len(body_lines),
    }


def _score_changelog_lines(lines: list[str]) -> tuple[int, list[str], list[dict[str, Any]], dict[str, int]]:
    total_score = 0
    keywords: dict[str, str] = {}
    category_scores: dict[str, int] = {}
    matched_lines: list[dict[str, Any]] = []
    for line in lines:
        line_matches = []
        hard_match = False
        for keyword in CHANGELOG_KEYWORDS:
            if not keyword.regex.search(line):
                continue
            line_matches.append(keyword)
            hard_match = hard_match or keyword.name in HARD_CHANGELOG_KEYWORDS
        if not hard_match:
            continue
        line_keywords = []
        line_score = 0
        for keyword in line_matches:
            line_keywords.append(keyword.name)
            keywords[keyword.name] = keyword.label
            line_score += keyword.score
            category_scores[keyword.category] = category_scores.get(keyword.category, 0) + keyword.score
        if line_score:
            total_score += line_score
            matched_lines.append({"text": line, "keywords": line_keywords, "score": line_score})
    return total_score, sorted(keywords), matched_lines, dict(sorted(category_scores.items()))


def _normalize_date(value: str) -> str:
    parts = value.split("-")
    if len(parts) == 3 and len(parts[0]) == 4:
        return value
    if len(parts) == 3:
        month, day, year = parts
        return f"{year}-{month}-{day}"
    return value


def _date_sort_value(value: str) -> int:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return int(value.replace("-", ""))
    return 0


def _is_download_link(token: _ChangelogToken) -> bool:
    return bool(DOWNLOAD_TEXT_RE.search(token.text) or _is_download_url(token.href))


def _is_download_url(value: str) -> bool:
    href = value.lower()
    return bool("/l3_cx/" in href or href.endswith((".exe", ".zip", ".msi", ".7z")))


def _recommended_download_versions(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommended = []
    for entry in entries:
        recommended.append(
            {
                "version": entry["version"],
                "release_date": entry["release_date"],
                "wireless_score": entry["wireless_score"],
                "download_urls": entry["download_urls"],
                "evidence": [line["text"] for line in entry["matched_lines"][:3]],
            }
        )
    return recommended


def _keyword_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        for line in entry["matched_lines"]:
            for keyword in line["keywords"]:
                counts[keyword] = counts.get(keyword, 0) + 1
    return dict(sorted(counts.items()))


def _category_scores(entries: list[dict[str, Any]]) -> dict[str, int]:
    scores: dict[str, int] = {}
    for entry in entries:
        for category, score in entry["category_scores"].items():
            scores[category] = scores.get(category, 0) + int(score)
    return dict(sorted(scores.items()))


def _changelog_warnings(entries: list[dict[str, Any]], wireless_entries: list[dict[str, Any]]) -> list[str]:
    warnings = []
    if not entries:
        warnings.append("No L3 version entries were parsed from the changelog source.")
    if entries and not wireless_entries:
        warnings.append("No wireless-related changelog lines matched the current keyword set.")
    if wireless_entries:
        warnings.append(
            "Changelog matches prioritize official versions for download/static analysis; they do not prove the USB/RF packet protocol by themselves."
        )
    return warnings


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())
