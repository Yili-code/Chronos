"""Read-only CLI for the shared official calendar parser."""
import argparse
import json
import httpx
from chronos.academic_calendar import (
    OFFICIAL_CALENDAR_URL, CalendarEvent, TableParser, classify_event,
    parse_calendar, summarize, create_tls_context,
)

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
