from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from chronos.db import Database
from chronos.note_delivery import export_note
from chronos.telegram import TelegramError
from test_note_record import note


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
