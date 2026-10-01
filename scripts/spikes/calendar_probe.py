"""Read-only feasibility probe for the official NTOU academic calendar.

This module deliberately lives outside the Chronos runtime.  It fetches one
public page, extracts dated events, classifies only explicit calendar policy,
and emits a sanitized summary.  It performs no authenticated or write request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
from dataclasses import asdict, dataclass
from datetime import date
from html.parser import HTMLParser

import httpx


OFFICIAL_CALENDAR_URL = "https://academic.ntou.edu.tw/p/405-1005-123146,c834.php?Lang=zh-tw"
MONTHS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}


@dataclass(frozen=True)
class CalendarEvent:
    start_date: str
    end_date: str
    classification: str
    text: str


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table" and self._table is None:
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            text = "".join(self._cell)
            lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
            self._row.append("\n".join(line for line in lines if line))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def classify_event(text: str) -> str:
    if "正常上班上課" in text:
        return "normal_instruction"
    if "教師自行擇期補課" in text or "教職員彈性休假" in text:
        return "needs_confirmation"
    if "期中考試" in text or "學期考試" in text:
        return "exam_period"
    if any(marker in text for marker in ("停止上課", "放假", "補假")):
        return "no_class"
    return "other"


def _parse_range(token: str, gregorian_year: int, month: int) -> tuple[date, date]:
    cleaned = token.replace(" ", "")
    if "/" in cleaned:
        match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:-(\d{1,2})/(\d{1,2}))?", cleaned)
        if not match:
            raise ValueError(f"unsupported explicit date token: {token}")
        start_month, start_day = int(match.group(1)), int(match.group(2))
        end_month = int(match.group(3) or start_month)
        end_day = int(match.group(4) or start_day)
        end_year = gregorian_year + (1 if end_month < start_month else 0)
        return date(gregorian_year, start_month, start_day), date(end_year, end_month, end_day)
    match = re.fullmatch(r"(\d{1,2})(?:-(\d{1,2}))?", cleaned)
    if not match:
        raise ValueError(f"unsupported date token: {token}")
    start_day = int(match.group(1))
    end_day = int(match.group(2) or start_day)
    return date(gregorian_year, month, start_day), date(gregorian_year, month, end_day)


def parse_calendar(html: str) -> list[CalendarEvent]:
    parser = TableParser()
    parser.feed(html)
    calendar = next(
        (table for table in parser.tables if any("辦 理 事" in cell for row in table for cell in row)),
        None,
    )
    if calendar is None:
        raise ValueError("official calendar table not found")

    roc_year: int | None = None
    month: int | None = None
    events: list[CalendarEvent] = []
    for row in calendar:
        for cell in row:
            compact = re.sub(r"\s+", "", cell)
            year_match = re.fullmatch(r"(\d{3})年", compact)
            if year_match:
                roc_year = int(year_match.group(1))
            month_match = re.fullmatch(r"([一二三四五六七八九十]{1,2})月", compact)
            if month_match:
                month = MONTHS[month_match.group(1)]

        if roc_year is None or month is None:
            continue
        for cell in row:
            for line in cell.splitlines():
                match = re.match(r"^[（(]([^）)]+)[）)]\s*(.+)$", line.strip())
                if not match or not re.search(r"\d", match.group(1)):
                    continue
                token, text = match.groups()
                try:
                    start, end = _parse_range(token, roc_year + 1911, month)
                except ValueError:
                    continue
                events.append(
                    CalendarEvent(
                        start_date=start.isoformat(),
                        end_date=end.isoformat(),
                        classification=classify_event(text),
                        text=text,
                    )
                )
    return events


def summarize(html: str, source_url: str) -> dict:
    events = parse_calendar(html)
    counts: dict[str, int] = {}
    for event in events:
        counts[event.classification] = counts.get(event.classification, 0) + 1
    evidence = [event for event in events if event.classification != "other"]
    return {
        "source_url": source_url,
        "content_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        "event_count": len(events),
        "classification_counts": counts,
        "evidence": [asdict(event) for event in evidence],
    }


def create_tls_context() -> ssl.SSLContext:
    """Keep chain/hostname checks while tolerating NTOU's legacy CA extensions.

    Python 3.13+ enables OpenSSL strict X.509 extension checks by default.  The
    NTOU chain currently omits Subject Key Identifier, while browsers and the
    Python 3.12 Cloud Run image accept the otherwise valid chain.
    """
    context = ssl.create_default_context()
    strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if strict:
        context.verify_flags &= ~strict
    return context


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=OFFICIAL_CALENDAR_URL)
    parser.add_argument(
        "--include-events",
        action="store_true",
        help="include normalized event text; the default report contains only counts and a fingerprint",
    )
    args = parser.parse_args()
    with httpx.Client(follow_redirects=True, timeout=20, verify=create_tls_context()) as client:
        response = client.get(args.url, headers={"User-Agent": "Chronos-Phase0-Calendar-Probe/1.0"})
        response.raise_for_status()
    report = summarize(response.text, str(response.url))
    required = {"no_class", "normal_instruction", "needs_confirmation", "exam_period"}
    missing = sorted(required - report["classification_counts"].keys())
    report["required_classifications_present"] = not missing
    report["missing_classifications"] = missing
    if not args.include_events:
        report["classified_event_count"] = len(report.pop("evidence"))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
