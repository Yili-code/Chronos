import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

from chronos.course_tracking import TAIPEI
from chronos.db import Database
from chronos.study_scheduler import tick_study
from chronos.telegram import TelegramError


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
