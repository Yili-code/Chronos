import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chronos.academic_calendar import OFFICIAL_CALENDAR_URL
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from chronos.study_scheduler import tick_study

NOW = datetime(2026, 10, 5, 12, 10, tzinfo=TAIPEI)


def save(db, kind, fetched=NOW):
    db.save_calendar_snapshot({"source_url": OFFICIAL_CALENDAR_URL,
        "fetched_at": fetched.isoformat(), "content_sha256": "a" * 64,
        "events": [{"start_date": "2026-10-05", "end_date": "2026-10-05",
                    "classification": kind, "text": "Official event"}]})


@pytest.mark.parametrize('kind,suppressed', [('no_class', True), ('exam_period', True),
    ('normal_instruction', False), ('needs_confirmation', False), ('other', False)])
def test_official_policy_controls_prompts(tmp_path, kind, suppressed):
    db = Database(tmp_path / 'db.sqlite3')
    db.initialize()
    save(db, kind)
    bot = AsyncMock()
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
    result = asyncio.run(tick_study(db, bot, 123, NOW))
    assert result['course_prompts_suppressed'] is suppressed
    assert bot.send_message.await_count == (0 if suppressed else 1)


def test_stale_closure_does_not_suppress_and_late_closure_stops_reminders(tmp_path):
    db = Database(tmp_path / 'db.sqlite3')
    db.initialize()
    save(db, 'no_class', NOW - timedelta(days=1))
    bot = AsyncMock()
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
    assert asyncio.run(tick_study(db, bot, 123, NOW))['calendar_policy'] == 'unverified'
    save(db, 'no_class', NOW + timedelta(minutes=1))
    asyncio.run(tick_study(db, bot, 123, NOW + timedelta(hours=1)))
    assert bot.send_message.await_count == 1
