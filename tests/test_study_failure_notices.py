import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from chronos.db import Database
from chronos.study_delivery import StudyDeliveryLedger
from chronos.study_scheduler import notify_study_failures
from chronos.telegram import TelegramError
import pytest


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


@pytest.mark.parametrize('category,expected', [
    ('announcement','課程公告'), ('assignment','作業通知'), ('calendar','校曆'),
    ('preparation','作業草稿'), ('ai-budget','AI 額度'),
    ('unrecognized','Study 通知')])
def test_failure_notice_identifies_feature_without_echoing_keys(tmp_path, category, expected):
    db = Database(tmp_path / 'notice.db')
    db.initialize()
    now = datetime(2026,10,5,tzinfo=timezone.utc)
    key = category + ':PRIVATE_SYNTHETIC_IDENTIFIER'
    ledger = StudyDeliveryLedger(db)
    claim = ledger.claim(key,now)
    ledger.finish(key,claim,now)
    bot = AsyncMock()
    bot.send_message.return_value = {'ok':True,'result':{'message_id':1}}
    asyncio.run(notify_study_failures(db,bot,123,now))
    text = bot.send_message.call_args.args[1]
    assert expected in text
    assert 'PRIVATE_SYNTHETIC_IDENTIFIER' not in text
