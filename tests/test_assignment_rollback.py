import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chronos import cloud_assignment_rollback as rollback
from chronos.assignments import Assignment
from chronos.course_tracking import TAIPEI
from chronos.settings import Settings
from test_firestore import database


@pytest.fixture
def setup(monkeypatch):
    db = database(monkeypatch)
    now = datetime(2026, 10, 16, 6, tzinfo=TAIPEI)
    item = Assignment('189684', '1', 'Synthetic', '', now)
    task_id = db.create_assignment(item)['task_id']
    db.mutate_study_delivery(f'assignment:{item.key}:discovered', lambda _: {'status': 'sent', 'message_id': 10})
    bot = AsyncMock()
    bot.enabled = True
    bot.request.return_value = {'ok': True}
    monkeypatch.setattr(rollback, 'create_database', lambda _: db)
    monkeypatch.setattr(rollback, 'TelegramClient', lambda _: bot)
    monkeypatch.setattr(rollback, 'settings', Settings(_env_file=None, telegram_chat_id=123))
    monkeypatch.setenv('CHRONOS_ASSIGNMENT_ROLLBACK_START', (now - timedelta(minutes=1)).isoformat())
    monkeypatch.setenv('CHRONOS_ASSIGNMENT_ROLLBACK_END', (now + timedelta(minutes=1)).isoformat())
    monkeypatch.delenv('CHRONOS_ASSIGNMENT_ROLLBACK_APPLY', raising=False)
    return db, bot, item, task_id


def test_preview_is_read_only_and_explicit_confirmation_is_required(setup, monkeypatch):
    db, bot, item, task_id = setup
    before = repr(db.client.data)
    preview = asyncio.run(rollback.rollback())
    assert preview['eligible'] == 1 and preview['writes'] == 0
    assert repr(db.client.data) == before
    bot.request.assert_not_awaited()
    monkeypatch.setenv('CHRONOS_ASSIGNMENT_ROLLBACK_APPLY', 'true')
    with pytest.raises(ValueError):
        asyncio.run(rollback.rollback())
    monkeypatch.setenv('CHRONOS_ASSIGNMENT_ROLLBACK_CONFIRM', preview['plan_hash'])
    result = asyncio.run(rollback.rollback())
    assert result == {'mode': 'apply', 'rolled_back': 1, 'telegram_pending': 0}
    assert db.get_assignment(item.key) is None
    assert db.get_task_action_state('assignment-rollback-' + preview['plan_hash']) is not None
    asyncio.run(rollback.rollback())
    assert bot.request.await_count == 1


def test_changed_target_rejects_confirmation_without_deleting(setup, monkeypatch):
    db, bot, item, task_id = setup
    preview = asyncio.run(rollback.rollback())
    db.edit_task(task_id, 'Owner edited this', None, None, {})
    monkeypatch.setenv('CHRONOS_ASSIGNMENT_ROLLBACK_APPLY', 'true')
    monkeypatch.setenv('CHRONOS_ASSIGNMENT_ROLLBACK_CONFIRM', preview['plan_hash'])
    with pytest.raises(ValueError, match='plan changed'):
        asyncio.run(rollback.rollback())
    assert db.get_assignment(item.key) is not None
    bot.request.assert_not_awaited()


def test_failed_message_cleanup_resumes_without_redeleting_records(setup, monkeypatch):
    db, bot, item, task_id = setup
    preview = asyncio.run(rollback.rollback())
    monkeypatch.setenv('CHRONOS_ASSIGNMENT_ROLLBACK_APPLY', 'true')
    monkeypatch.setenv('CHRONOS_ASSIGNMENT_ROLLBACK_CONFIRM', preview['plan_hash'])
    bot.request.side_effect = RuntimeError('interrupted')
    assert asyncio.run(rollback.rollback())['telegram_pending'] == 1
    bot.request.side_effect = None
    assert asyncio.run(rollback.rollback())['telegram_pending'] == 0
    assert db.get_assignment(item.key) is None
