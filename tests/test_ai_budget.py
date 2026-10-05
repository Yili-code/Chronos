from datetime import datetime, timedelta
import pytest
from chronos.ai_budget import DailyAIBudget, BudgetExceeded
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
