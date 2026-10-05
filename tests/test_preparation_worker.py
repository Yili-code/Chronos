import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from chronos.assignments import Assignment
from chronos.assignment_preparation import PreparationDraft
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from chronos.preparation_commands import prepare_action
from chronos.preparation_worker import run_preparation_pass
from test_assignment_preparation import payload
from test_firestore import database as firestore_fake


@pytest.mark.parametrize('backend', ['sqlite', 'firestore'])
def test_persist_before_delivery_and_resume_without_generation(backend, tmp_path, monkeypatch):
    db = firestore_fake(monkeypatch) if backend == 'firestore' else Database(tmp_path / 'worker.db')
    db.initialize()
    now = datetime.now(TAIPEI)
    task = db.create_assignment(Assignment('c', 's', 'Lab', 'Implement sort', now))
    prepare_action(db, f"prepare {task['task_id']}", now)()
    generate = AsyncMock(return_value=PreparationDraft(**payload()))
    monkeypatch.setattr('chronos.preparation_worker.generate_preparation', generate)
    bot = AsyncMock()
    async def reject(*args):
        assert db.list_preparations()[0][1]['draft'] is not None
        return {'ok': False, 'error_code': 400}
    bot.send_message.side_effect = reject
    asyncio.run(run_preparation_pass(db, None, bot, 123, now))
    assert db.list_preparations()[0][1]['status'] == 'ready'
    bot.send_message.side_effect = None
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
    asyncio.run(run_preparation_pass(db, None, bot, 123, now + timedelta(minutes=5)))
    asyncio.run(run_preparation_pass(db, None, bot, 123, now + timedelta(minutes=6)))
    assert generate.await_count == 1
    assert db.list_preparations()[0][1]['status'] == 'sent'


def test_unknown_generation_never_repeats(tmp_path, monkeypatch):
    db = Database(tmp_path / 'unknown.db')
    db.initialize()
    now = datetime.now(TAIPEI)
    task = db.create_assignment(Assignment('c', 's', 'Lab', 'Implement sort', now))
    prepare_action(db, f"prepare {task['task_id']}", now)()
    generate = AsyncMock(side_effect=RuntimeError('synthetic'))
    monkeypatch.setattr('chronos.preparation_worker.generate_preparation', generate)
    bot = AsyncMock()
    for minute in (0, 1, 11):
        asyncio.run(run_preparation_pass(db, None, bot, 123, now + timedelta(minutes=minute)))
    assert generate.await_count == 1
    bot.send_message.assert_not_awaited()
    assert db.list_preparations()[0][1]['status'] == 'uncertain'
