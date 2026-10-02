import asyncio
from datetime import datetime, date, timedelta
import pytest
from unittest.mock import AsyncMock

from chronos.course_tracking import TAIPEI
from chronos.db import Database
from chronos.study_scheduler import tick_study
from chronos.telegram import TelegramError
from chronos.course_tracking import COURSE_SCHEDULE


@pytest.mark.parametrize("slot", COURSE_SCHEDULE, ids=lambda slot: slot.key)
def test_every_course_prompts_five_minutes_after_class(tmp_path, slot):
    db = Database(tmp_path / "study.db")
    db.initialize()
    day = date(2026, 10, 5) + timedelta(days=slot.weekday)
    due = datetime.combine(day, slot.prompt_time, tzinfo=TAIPEI)
    assert due == datetime.combine(day, slot.end, tzinfo=TAIPEI) + timedelta(minutes=5)
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 801}}
    asyncio.run(tick_study(db, bot, 123, due - timedelta(minutes=1)))
    assert db.get_course_session(f"{slot.key}:{day}") is None
    asyncio.run(tick_study(db, bot, 123, due))
    assert db.get_course_session(f"{slot.key}:{day}").prompt_message_id == 801


def test_prompt_boundary_and_restart_reconciliation(tmp_path):
    db = Database(tmp_path / "study.db")
    db.initialize()
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 501}}
    asyncio.run(tick_study(db, bot, 123, datetime(2026, 10, 5, 12, 9, tzinfo=TAIPEI)))
    bot.send_message.assert_not_awaited()
    asyncio.run(tick_study(db, bot, 123, datetime(2026, 10, 5, 12, 10, tzinfo=TAIPEI)))
    recreated = Database(db.path)
    asyncio.run(tick_study(recreated, bot, 123, datetime(2026, 10, 5, 12, 11, tzinfo=TAIPEI)))
    assert bot.send_message.await_count == 1
    assert recreated.get_course_session("security:2026-10-05").prompt_message_id == 501


def test_timeout_never_blindly_resends(tmp_path):
    db = Database(tmp_path / "study.db")
    db.initialize()
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramError("transport failed")
    for minute in (10, 15, 20):
        asyncio.run(tick_study(db, bot, 123, datetime(2026, 10, 5, 12, minute, tzinfo=TAIPEI)))
    assert bot.send_message.await_count == 1
    assert db.get_study_delivery("security:2026-10-05:prompt")["status"] == "uncertain"


def test_two_reminders_and_next_day_cleanup(tmp_path):
    db = Database(tmp_path / "study.db")
    db.initialize()
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 501}}
    for hour, minute in ((12, 10), (13, 9), (13, 10), (13, 10), (14, 10), (15, 10)):
        asyncio.run(tick_study(db, bot, 123, datetime(2026, 10, 5, hour, minute, tzinfo=TAIPEI)))
    assert bot.send_message.await_count == 3
    assert db.get_course_session("security:2026-10-05").reminder_count == 2
    assert bot.send_message.call_args.kwargs['reply_to_message_id'] == 501
    asyncio.run(tick_study(db, bot, 123, datetime(2026, 10, 6, 0, 0, tzinfo=TAIPEI)))
    assert db.get_course_session("security:2026-10-05").status.value == "missed"
    assert bot.send_message.await_count == 3


def test_answer_arriving_during_send_cannot_be_overwritten(tmp_path):
    db = Database(tmp_path / "study.db")
    db.initialize()
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 501}}
    asyncio.run(tick_study(db, bot, 123, datetime(2026, 10, 5, 12, 10, tzinfo=TAIPEI)))

    async def concurrent_answer(*args, **kwargs):
        db.process_update(77, lambda: db.record_course_reply(501, 502, 'Chapter 4'))
        return {"ok": True, "result": {"message_id": 503}}

    bot.send_message.side_effect = concurrent_answer
    asyncio.run(tick_study(db, bot, 123, datetime(2026, 10, 5, 13, 10, tzinfo=TAIPEI)))
    assert db.get_course_session("security:2026-10-05").status.value == "answered"
    asyncio.run(tick_study(db, bot, 123, datetime(2026, 10, 5, 14, 10, tzinfo=TAIPEI)))
    assert bot.send_message.await_count == 2


def test_restart_spaces_delayed_reminders_by_one_hour(tmp_path):
    db = Database(tmp_path / "study.db")
    db.initialize()
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 501}}
    for hour, minute in ((12, 10), (16, 0), (16, 1), (16, 59)):
        asyncio.run(tick_study(db, bot, 123, datetime(2026, 10, 5, hour, minute, tzinfo=TAIPEI)))
    assert bot.send_message.await_count == 2
    asyncio.run(tick_study(db, bot, 123, datetime(2026, 10, 5, 17, 0, tzinfo=TAIPEI)))
    assert bot.send_message.await_count == 3
