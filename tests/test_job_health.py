import asyncio
from chronos.job_health import tracked_job, KEY
from test_deadline_reminders import store
import pytest


def test_job_health_retains_success_when_later_run_fails(store):
    fail = False
    @tracked_job(lambda: store)
    async def job():
        if fail:
            raise ValueError('private payload')
        return {'sent': 1}
    assert asyncio.run(job()) == {'sent': 1}
    first = store.get_task_action_state(KEY)['job']
    fail = True
    with pytest.raises(ValueError):
        asyncio.run(job())
    second = store.get_task_action_state(KEY)['job']
    assert second['last_success_at'] == first['last_success_at']
    assert second['consecutive_failures'] == 1
    assert second['error_type'] == 'ValueError'
    assert 'private payload' not in str(second)


def test_telemetry_failure_does_not_repeat_work(store, monkeypatch):
    calls = []
    @tracked_job(lambda: store)
    async def job():
        calls.append(1)
        return 7
    def broken(*args):
        raise OSError('unavailable')
    monkeypatch.setattr(store, 'mutate_task_action_state', broken)
    assert asyncio.run(job()) == 7
    assert calls == [1]
