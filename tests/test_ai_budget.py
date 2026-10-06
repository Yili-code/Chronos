from datetime import datetime, timedelta
import pytest
from chronos.ai_budget import DailyAIBudget, BudgetExceeded
from chronos.ai_budget import budget_report, notify_budget
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from test_firestore import database as firestore_fake


@pytest.fixture(params=['sqlite', 'firestore'])
def db(request, tmp_path, monkeypatch):
    value = firestore_fake(monkeypatch) if request.param == 'firestore' else Database(tmp_path / 'budget.db')
    value.initialize()
    return value


NOW = datetime(2026, 10, 5, 23, 59, tzinfo=TAIPEI)


def test_shared_budget_near_limit_and_midnight(db):
    first = DailyAIBudget(db, request_limit=5, token_limit=100)
    second = DailyAIBudget(db, request_limit=5, token_limit=100)
    assert not first.reserve(NOW, 30)['near_limit']
    assert second.reserve(NOW, 50)['near_limit']
    with pytest.raises(BudgetExceeded):
        first.reserve(NOW, 21)
    assert second.reserve(NOW, 20)['requests'] == 3
    assert first.reserve(NOW + timedelta(minutes=1), 10)['requests'] == 1


def test_request_limit_and_disabled_budget(db):
    budget = DailyAIBudget(db, request_limit=1, token_limit=100)
    budget.reserve(NOW, 1)
    with pytest.raises(BudgetExceeded):
        budget.reserve(NOW, 1)
    with pytest.raises(BudgetExceeded):
        DailyAIBudget(db, request_limit=0, token_limit=0).reserve(NOW + timedelta(days=1), 1)


def test_concurrent_sqlite_workers_cannot_overspend(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / 'shared.db'
    db = Database(path)
    db.initialize()
    def reserve(_):
        try:
            DailyAIBudget(Database(path), request_limit=5, token_limit=100).reserve(NOW, 1)
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(20))) == 5


def test_budget_query_does_not_create_or_change_state(db):
    assert budget_report(db, NOW) == 'Study AI：今日尚無用量紀錄。'
    assert db.get_ai_budget('2026-10-05') is None
    DailyAIBudget(db, request_limit=5, token_limit=100).reserve(NOW, 10)
    before = db.get_ai_budget('2026-10-05')
    assert budget_report(db, NOW) == 'Study AI：1/5 requests · ~10/100 tokens'
    assert db.get_ai_budget('2026-10-05') == before


def test_budget_notice_sent_only_once(db):
    import asyncio
    from unittest.mock import AsyncMock
    bot = AsyncMock()
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
    for _ in range(3):
        asyncio.run(notify_budget(db, bot, 123, 'near_limit', NOW))
    assert bot.send_message.await_count == 1
