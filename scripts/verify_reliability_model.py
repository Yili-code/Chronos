"""Opt-in real model and local execution verification; no Calendar or Telegram writes.

Only fixed synthetic cases are sent. Disposable databases are cleaned on exit.
The output distinguishes provider interpretation from actual execution acceptance.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import asyncio, json, logging, subprocess, tempfile
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from chronos.ai import ExternalAI
from chronos.settings import Settings
from chronos.db import Database
from chronos.learning_calendar import LearningCalendar
from chronos.task_actions import task_fields

logging.disable(logging.CRITICAL)
NOW = datetime.fromisoformat("2026-10-25T10:00:00+08:00")
CASES = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "tests/fixtures/reliability_time_cases.json"
    ).read_text(encoding="utf-8")
)


async def verify(args):
    ai = ExternalAI(Settings(_env_file=args.env_file))
    cfg = Settings(
        _env_file=None,
        enable_learning_calendar=True,
        learning_auto_accept=False,
        calendar_account="owner@example.com",
        learning_start_hour=6,
        learning_daily_minutes=600,
        telegram_chat_id=123,
    )
    results = []
    for case in CASES:
        db = Database(args.database_dir / (case["id"] + ".db"))
        db.initialize()
        task = db.create_task(
            "Database report", NOW + timedelta(days=20), "database", NOW, {}
        )
        state = {
            "status": "scheduled",
            "token": "synthetic",
            "event_id": "synthetic-event",
            "start": "2026-10-26T10:00:00+08:00",
            "end": "2026-10-26T11:00:00+08:00",
            "minutes": 60,
            "snapshot": task_fields(task),
        }
        event = {
            "id": "synthetic-event",
            "etag": "v1",
            "_calendar_id": "primary",
            "start": {"dateTime": state["start"]},
            "end": {"dateTime": state["end"]},
            "extendedProperties": {
                "private": {"chronosTask": str(task["id"]), "chronosToken": "synthetic"}
            },
        }
        calendar = AsyncMock()
        calendar.configured = lambda: True
        calendar.event.side_effect = lambda _: deepcopy(event)
        calendar.agenda.side_effect = lambda *args: [deepcopy(event)]

        async def move(i, body, etag):
            assert i == event["id"] and etag == event["etag"]
            event.update(deepcopy(body))
            event["etag"] = "v2"
            return deepcopy(event)

        calendar.move_event.side_effect = move
        planner = LearningCalendar(db, calendar, AsyncMock(), cfg, ai=ai)
        planner.update(task["id"], lambda old: deepcopy(state))
        db.mutate_task_action_state(
            "learning:day:2026-10-26", lambda old: {"synthetic": 60}
        )
        db.mutate_task_action_state(
            "learning:course-calendar-policy",
            lambda old: {
                "first_day": "2020-01-01",
                "last_regular_day": "2020-01-02",
                "excluded_dates": [],
            },
        )
        result = {"id": case["id"], "input": case["text"], "expected": case["expected"]}
        try:
            parsed = await ai.parse_schedule_edit(case["text"], task, state, NOW)
            result["parsed"] = parsed.model_dump(mode="json")
            # Reuse the actual live result once through the real booking mutation path.
            planner.ai = AsyncMock()
            planner.ai.parse_schedule_edit.return_value = parsed
            reply = await planner.revise(task["id"], "synthetic", case["text"], NOW)
            saved = planner.get(task["id"])
            if case["expected"] == "clarify":
                passed = (
                    calendar.move_event.await_count == 0
                    and saved == state
                    and (not parsed.actionable or "不一致" in reply)
                )
            elif case["expected"] == "blocked-meal":
                passed = (
                    parsed.actionable
                    and datetime.fromisoformat(parsed.start)
                    .astimezone(cfg.tz)
                    .isoformat()
                    == case["parsed_expected"]
                    and calendar.move_event.await_count == 0
                    and saved == state
                )
            else:
                passed = (
                    saved["start"] == case["expected"]
                    and saved["minutes"] == case["minutes"]
                    and calendar.move_event.await_count == 1
                    and "updated" in reply.lower()
                )
            result.update(
                passed=passed,
                reply=reply,
                writes=calendar.move_event.await_count,
                actual_start=saved["start"],
            )
        except Exception as e:
            result.update(passed=False, error_type=type(e).__name__)
        results.append(result)
        args.output.write_text(
            json.dumps(
                {
                    "scope": "Live model; disposable SQLite; mocked Calendar/Telegram; no service writes",
                    "source_commit": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], text=True
                    ).strip(),
                    "reference_now": NOW.isoformat(),
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "id": case["id"],
                    "passed": result["passed"],
                    "writes": result.get("writes"),
                    "error": result.get("error_type"),
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
        if result.get("error_type"):
            break
        if case is not CASES[-1]:
            await asyncio.sleep(args.interval)
    return len(results) == len(CASES) and all(r["passed"] for r in results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly authorize live model calls using synthetic input only",
    )
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=10)
    args = parser.parse_args()
    if not args.live:
        parser.error("Live model calls require --live")
    if args.interval < 10:
        parser.error("Use an interval of at least ten seconds")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    base = Path(".pytest-tmp").resolve()
    base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reliability-live-", dir=base) as directory:
        args.database_dir = Path(directory).resolve()
        if not args.database_dir.is_relative_to(base):
            raise RuntimeError("Unexpected temporary directory")
        return 0 if asyncio.run(verify(args)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
