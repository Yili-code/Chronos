from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from chronos.db import Database
from chronos.note_delivery import export_note
from chronos.telegram import TelegramError
from test_note_record import note


@pytest.mark.asyncio
async def test_every_split_segment_message_has_source_and_resume_is_deduplicated(tmp_path):
    from chronos.note_delivery import deliver_summary
    from chronos.note_commands import note_command
    db = Database(tmp_path / "segments.db")
    db.initialize()
    value = note().model_copy(update={"page_start": 1, "page_end": 1,
        "segment_index": 1, "segment_total": 1, "pagination_version": "physical-three-v1",
        "markdown": "重點" * 3200})
    db.save_study_note(value)
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 77}}
    args = dict(chat_id=1, fingerprint=value.content_fingerprint, now=datetime.now(timezone.utc))
    assert await deliver_summary(db, bot, **args) == "sent"
    assert await deliver_summary(db, bot, **args) == "sent"
    assert bot.send_message.await_count == 3
    for call in bot.send_message.await_args_list:
        message = call.args[1]
        assert value.sources[0].filename in message
        assert "p. 1–1" in message and "重點 1/1" in message
        assert len(message.encode('utf-16-le')) // 2 <= 4096
    assert "p. 1–1" in note_command(db, "notes")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [("success", "sent"), ("timeout", "uncertain"), ("rejected", "retry")])
async def test_export_retries_do_not_blindly_resend(tmp_path, mode, expected):
    db = Database(tmp_path / "notes.db")
    db.initialize()
    value = note()
    db.save_study_note(value)
    bot = AsyncMock()
    if mode == "timeout":
        bot.send_document.side_effect = TelegramError("unknown")
    else:
        bot.send_document.return_value = ({"ok": True, "result": {"message_id": 77, "document": {"file_id": "fake"}}}
                                         if mode == "success" else {"ok": False, "error_code": 429})
    args = dict(chat_id=1, update_id=2, fingerprint=value.content_fingerprint, now=datetime.now(timezone.utc))
    assert await export_note(db, bot, **args) == expected
    assert await export_note(db, bot, **args) == expected
    assert bot.send_document.await_count == 1
    assert bot.send_document.call_args.args[2] == value.markdown.encode("utf-8")
