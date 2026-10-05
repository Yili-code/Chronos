import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from chronos.course_day_commands import classday_action
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from chronos.study_scheduler import tick_study
from test_firestore import database as firestore_fake


@pytest.mark.parametrize('backend', ['sqlite', 'firestore'])
def test_scope_and_reset(backend, tmp_path, monkeypatch):
    db = firestore_fake(monkeypatch) if backend == 'firestore' else Database(tmp_path / 'days.db')
    db.initialize()
    action = classday_action(db, 'classday 2026-10-07 software-engineering off')
    db.process_update(908, action)
    assert db.get_course_day_decision('software-engineering:2026-10-07') == 'off'
    assert db.get_course_day_decision('graph-algorithms:2026-10-07') == 'auto'
    bot = AsyncMock()
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
    now = datetime(2026, 10, 7, 16, 10, tzinfo=TAIPEI)
    asyncio.run(tick_study(db, bot, 123, now))
    assert db.get_course_session('software-engineering:2026-10-07') is None
    assert db.get_course_session('graph-algorithms:2026-10-07') is not None
    classday_action(db, 'classday 2026-10-07 software-engineering auto')()
    asyncio.run(tick_study(db, bot, 123, now))
    assert db.get_course_session('software-engineering:2026-10-07') is not None
    assert '用法' in classday_action(db, 'classday 2026-10-08 software-engineering off')()
