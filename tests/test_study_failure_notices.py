import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from chronos.db import Database
from chronos.study_delivery import StudyDeliveryLedger
from chronos.study_scheduler import notify_study_failures
from chronos.telegram import TelegramError


def test_uncertain_delivery_has_one_durable_notice(tmp_path):
    db = Database(tmp_path / "study.db")
    db.initialize()
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    ledger = StudyDeliveryLedger(db)
    claim = ledger.claim("course:prompt", now)
    ledger.finish("course:prompt", claim, now)
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 10}}
    for _ in range(3):
        result = asyncio.run(notify_study_failures(db, bot, 123, now))
    assert bot.send_message.await_count == 1
    assert result["failure_notices_unresolved"] == 0


def test_failed_notice_does_not_create_recursive_notifications(tmp_path):
    db = Database(tmp_path / "study.db")
    db.initialize()
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    ledger = StudyDeliveryLedger(db)
    claim = ledger.claim("course:prompt", now)
    ledger.finish("course:prompt", claim, now)
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramError("transport failed")
    for _ in range(3):
        result = asyncio.run(notify_study_failures(db, bot, 123, now))
    assert bot.send_message.await_count == 1
    assert result["failure_notices_unresolved"] == 1
    assert db.list_failed_study_deliveries() == ["course:prompt"]
