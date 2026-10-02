from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import pytest
from chronos.db import Database
from chronos.note_delivery import deliver_summary
from chronos.telegram import TelegramError
from test_note_record import note


def setup(tmp_path):
    db = Database(tmp_path / "notes.db")
    db.initialize()
    db.save_study_note(note(markdown="甲" * 6500))
    return db, dict(chat_id=123, fingerprint="a" * 64, now=datetime.now(timezone.utc))


def success(message):
    return {"ok": True, "result": {"message_id": message}}


@pytest.mark.asyncio
async def test_split_delivery_preserves_text_and_deduplicates(tmp_path):
    db, args = setup(tmp_path)
    bot = AsyncMock()
    bot.send_message.side_effect = [success(1), success(2), success(3)]
    assert await deliver_summary(db, bot, **args) == "sent"
    assert await deliver_summary(db, bot, **args) == "sent"
    messages = [call.args[1] for call in bot.send_message.call_args_list]
    assert len(messages) == 3
    assert "".join(message.split("\n\n", 1)[1] for message in messages) == "甲" * 6500
    assert all(len(message.encode("utf-16-le")) // 2 < 4096 for message in messages)


@pytest.mark.asyncio
async def test_rejection_resumes_at_unsent_chunk(tmp_path):
    db, args = setup(tmp_path)
    bot = AsyncMock()
    bot.send_message.side_effect = [success(1), {"ok": False, "error_code": 429}, success(2), success(3)]
    assert await deliver_summary(db, bot, **args) == "retry"
    assert await deliver_summary(db, bot, **args) == "retry"
    assert bot.send_message.await_count == 2
    args["now"] += timedelta(minutes=5)
    assert await deliver_summary(db, bot, **args) == "sent"
    messages = [call.args[1] for call in bot.send_message.call_args_list]
    assert [message.split("\n")[0] for message in messages] == ["課程摘要 1/3", "課程摘要 2/3", "課程摘要 2/3", "課程摘要 3/3"]


@pytest.mark.asyncio
async def test_unknown_outcome_stops_remaining_chunks(tmp_path):
    db, args = setup(tmp_path)
    bot = AsyncMock()
    bot.send_message.side_effect = [success(1), TelegramError("unknown")]
    assert await deliver_summary(db, bot, **args) == "uncertain"
    args["now"] += timedelta(days=1)
    assert await deliver_summary(db, bot, **args) == "uncertain"
    assert bot.send_message.await_count == 2
