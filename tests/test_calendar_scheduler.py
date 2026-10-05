import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from chronos.academic_calendar import OFFICIAL_CALENDAR_URL
from chronos.calendar_scheduler import holiday_notices, tick_calendar
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from chronos.telegram import TelegramError


def clock(day=8, hour=12):
    return datetime(2026, 10, day, hour, tzinfo=TAIPEI)


def snapshot(now, classes=("no_class",)):
    return {"source_url": OFFICIAL_CALENDAR_URL, "fetched_at": now.isoformat(),
            "content_sha256": "a" * 64,
            "events": [{"start_date": "2026-10-09", "end_date": "2026-10-09",
                        "classification": kind, "text": "Official event"}
                       for kind in classes]}


@pytest.mark.parametrize("day,hour,count", [(8, 11, 0), (8, 12, 1), (8, 23, 1),
                                          (9, 7, 0), (9, 8, 1), (9, 23, 1), (10, 12, 0)])
def test_notice_windows(day, hour, count):
    now = clock(day, hour)
    assert len(holiday_notices(snapshot(now), now)) == count
    assert len(holiday_notices(snapshot(now), now.astimezone(timezone.utc))) == count


@pytest.mark.parametrize("classes", [("normal_instruction",), ("exam_period",),
                                    ("needs_confirmation",), ("other",),
                                    ("no_class", "normal_instruction")])
def test_only_unambiguous_closures_notify(classes):
    now = clock()
    assert holiday_notices(snapshot(now, classes), now) == []


def test_missing_stale_future_and_naive_evidence():
    now = clock()
    for value in (None, snapshot(now - timedelta(days=1)), snapshot(now + timedelta(seconds=1))):
        assert holiday_notices(value, now) == []
    with pytest.raises(ValueError):
        holiday_notices(snapshot(now), now.replace(tzinfo=None))


def test_late_discovery_and_refresh_do_not_repeat(tmp_path):
    db = Database(tmp_path / "calendar.db")
    db.initialize()
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 42}}
    now = clock(8, 17)  # source first becomes available after noon
    db.save_calendar_snapshot(snapshot(now))
    assert asyncio.run(tick_calendar(db, bot, 123, now))["holiday_notices_sent"] == 1
    db.save_calendar_snapshot({**snapshot(clock(8, 18)), "content_sha256": "b" * 64})
    assert asyncio.run(tick_calendar(db, bot, 123, clock(8, 18)))["holiday_notices_sent"] == 0
    # New day's evidence is required; yesterday's snapshot cannot trigger today's notice.
    assert not asyncio.run(tick_calendar(db, bot, 123, clock(9, 8)))["calendar_current"]
    db.save_calendar_snapshot(snapshot(clock(9, 9)))
    assert asyncio.run(tick_calendar(db, bot, 123, clock(9, 9)))["holiday_notices_sent"] == 1
    assert bot.send_message.await_count == 2


def test_uncertain_send_not_replayed_after_restart(tmp_path):
    path = tmp_path / "calendar.db"
    db = Database(path)
    db.initialize()
    now = clock()
    db.save_calendar_snapshot(snapshot(now))
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramError("unknown delivery")
    asyncio.run(tick_calendar(db, bot, 123, now))
    reopened = Database(path)
    reopened.initialize()
    asyncio.run(tick_calendar(reopened, bot, 123, now + timedelta(minutes=5)))
    assert bot.send_message.await_count == 1


def test_explicit_rejection_waits_before_retry(tmp_path):
    db = Database(tmp_path / "calendar.db")
    db.initialize()
    now = clock()
    db.save_calendar_snapshot(snapshot(now))
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": False, "error_code": 400}
    asyncio.run(tick_calendar(db, bot, 123, now))
    asyncio.run(tick_calendar(db, bot, 123, now + timedelta(minutes=1)))
    assert bot.send_message.await_count == 1
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 42}}
    assert asyncio.run(tick_calendar(db, bot, 123, now + timedelta(minutes=5)))["holiday_notices_sent"] == 1
