"""Cloud Run Job entry point for the scheduled read-only assignment scan."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json

from .assignment_scan import run_assignment_scan
from .db import create_database
from .settings import settings
from .tronclass_collector import TRACKED_SEMESTER
from .telegram import TelegramClient
from . import assignment_access


def _excluded_course_ids(value: str) -> set[str]:
    result = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdecimal() or len(item) > 20:
            raise ValueError("invalid excluded course id")
        result.add(item)
    return result


async def _main() -> dict:
    if not settings.telegram_chat_id or not settings.telegram_bot_token:
        raise RuntimeError("Telegram delivery is not configured")
    db = create_database(settings)
    db.initialize()
    bot = TelegramClient(settings.telegram_bot_token)
    now = datetime.now(settings.tz)
    request = db.get_task_action_state(assignment_access.KEY) or {}
    interactive = request.get('status') in assignment_access.ACTIVE and request.get('expires_at', '') > now.isoformat()
    try:
        if not settings.tronclass_username or not settings.tronclass_password:
            raise RuntimeError('TronClass credentials are not configured')
        report = await run_assignment_scan(
            db, bot, settings.telegram_chat_id, now, settings.tronclass_username, settings.tronclass_password,
            excluded_course_ids=_excluded_course_ids(settings.tronclass_excluded_course_ids), interactive=interactive)
    except Exception:
        report = {'assignment_scan': 'runner_failed'}
        db.mutate_task_action_state(assignment_access.OUTCOME, lambda old: report)
        await assignment_access.finish(db, bot, now, report)
        raise
    await assignment_access.finish(db, bot, now, report)
    return report


def main() -> int:
    try:
        report = asyncio.run(_main())
    except Exception as error:
        # Credentials, HTTP details and page text must never enter Cloud logs.
        print(json.dumps({"assignment_scan": "runner_failed", "failure_type": type(error).__name__}, sort_keys=True))
        return 1
    print(json.dumps({**report, "release": settings.release_sha or "development", "semester": TRACKED_SEMESTER}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
