"""Opt-in live classification audit; synthetic records, no application/database writes.

Run from the repository root. Credentials come from normal Settings/env loading.
Results establish sampled classification only, not end-to-end execution correctness.
"""
import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chronos.ai import ExternalAI
from chronos.settings import Settings


async def audit(args):
    settings = Settings()
    ai = ExternalAI(settings)
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    now = datetime.now(settings.tz)
    source_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    tasks = [{"id": 701, "position": 1, "title": "Complete database report", "project": "database",
              "due_at": None, "timing": {"content": "Write three pages"}}]
    context = {"bound_task_id": None, "clarification": None,
               "preferences": {"name": "YiLi", "language": "繁體中文", "emoji": "不用表情符號"}}
    output = Path(args.output)
    results = json.loads(output.read_text(encoding="utf-8"))["results"] if output.exists() else []
    # Sequential calls avoid a burst against provider quotas.
    for case in cases:
        if not args.start <= case["id"] <= args.end:
            continue
        if args.ids and case["id"] not in args.ids:
            continue
        previous = next((item for item in results if item["id"] == case["id"]), None)
        if previous and "intent" in previous and not args.repeat:
            continue
        try:
            intent = await ai.classify_intent(case["request"], tasks, now, context=context)
            expected = case.get("allowed", [case["expected"]])
            passed = intent.action in expected
            if intent.action in {"task_edit", "task_complete", "plan_request"}:
                passed = passed and intent.task_ids == [701] and not intent.question
            if intent.action == "preference":
                passed = passed and intent.preference_operation is not None
                if intent.preference_operation != "list":
                    passed = passed and bool(intent.preference_key)
                if intent.preference_operation == "set":
                    passed = passed and bool(intent.preference_value)
            if intent.action in {"agenda", "event_create"}:
                passed = passed and intent.start is not None and intent.start.utcoffset() is not None
            if intent.action == "event_create":
                passed = passed and bool(intent.title and intent.event_category) and bool(intent.end or intent.minutes)
                expected_event = case.get("expected_event")
                if expected_event and intent.start:
                    local = intent.start.astimezone(settings.tz)
                    day_offset = expected_event.get("days_ahead")
                    if day_offset is None:
                        day_offset = 7 - now.weekday() + expected_event["next_weekday"]
                    expected_day = now.date() + timedelta(days=day_offset)
                    minutes = (intent.end - intent.start).total_seconds() / 60 if intent.end else intent.minutes
                    passed = passed and (local.date(), local.hour, local.minute, minutes) == (
                        expected_day, expected_event["hour"], 0, expected_event["minutes"])
            result = {"id": case["id"], "passed": passed, "source_sha": source_sha,
                      "checked_at": datetime.now(settings.tz).isoformat(), "intent": intent.model_dump(mode="json")}
        except Exception as error:
            result = {"id": case["id"], "passed": False, "error_type": type(error).__name__}
        results = [item for item in results if item["id"] != case["id"]] + [result]
        results.sort(key=lambda item: item["id"])
        Path(args.output).write_text(json.dumps({"checked_at": now.isoformat(), "model": settings.gemini_model,
            "scope": "live classifier; synthetic context; no task/calendar/Telegram writes", "results": results},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"id": case["id"], "passed": result["passed"],
                          "action": result.get("intent", {}).get("action"), "error": result.get("error_type")}), flush=True)
        if "error_type" in result:
            print("Provider unavailable; remaining cases stay unverified.", flush=True)
            break
        await asyncio.sleep(args.interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="docs/audits/natural-language-100.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=100)
    parser.add_argument("--interval", type=float, default=8)
    parser.add_argument("--repeat", action="store_true")
    parser.add_argument("--ids", type=lambda value: {int(part) for part in value.split(",")})
    args = parser.parse_args()
    if args.interval < 0 or not 1 <= args.start <= args.end <= 100:
        parser.error("Use a nonnegative interval and a range within 1..100")
    asyncio.run(audit(args))
