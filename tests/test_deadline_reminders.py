import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chronos import main
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from chronos.deadline_reminders import reminder_stage, send_deadline_reminders
from chronos.settings import Settings
from chronos.telegram import TelegramError
from test_firestore import database as firestore_fake
from test_system import system


NOW = datetime(2026, 10, 16, 12, tzinfo=TAIPEI)


@pytest.fixture(params=["sqlite", "firestore"])
def store(request, tmp_path, monkeypatch):
    if request.param == "firestore":
        return firestore_fake(monkeypatch)
    db = Database(tmp_path / "deadlines.db")
    db.initialize()
    return db


def bot():
    result = AsyncMock()
    result.enabled = True
    result.send_message.return_value = {"ok": True, "result": {"message_id": 10}}
    return result


def send(store, telegram, now=NOW):
    config = Settings(_env_file=None, telegram_chat_id=123)
    return asyncio.run(send_deadline_reminders(store, telegram, config, now=now))


def test_two_stages_deduplicate_and_overdue_stops(store):
    store.create_task("<Report>", NOW + timedelta(hours=24), None, NOW, {})
    telegram = bot()
    assert send(store, telegram) == {"sent": 1}
    assert "Due: 10/17 12:00" in telegram.send_message.call_args.args[1]
    assert send(store, telegram) == {"sent": 0}
    assert send(store, telegram, NOW + timedelta(hours=23)) == {"sent": 1}
    assert send(store, telegram, NOW + timedelta(hours=23, minutes=5)) == {"sent": 0}
    assert send(store, telegram, NOW + timedelta(hours=24)) == {"sent": 0}
    assert telegram.send_message.await_count == 2
    assert "&lt;Report&gt;" in telegram.send_message.call_args.args[1]


def test_late_start_sends_only_relevant_stage_and_skips_non_deadlines(store):
    for timing in ({}, {"event": "2026-10-16"}, {"scheduled": "2026-10-16"}):
        store.create_task("No deadline", None, None, NOW, timing)
    store.create_task("Soon", NOW + timedelta(minutes=30), None, NOW, {})
    telegram = bot()
    assert send(store, telegram) == {"sent": 1}
    assert "Due within 1 hour" in telegram.send_message.call_args.args[1]
    assert send(store, telegram) == {"sent": 0}


def test_date_only_has_no_invented_cutoff(store):
    task = store.create_task("Date only", None, None, NOW, {"due_date": "2026-10-17"})
    telegram = bot()
    assert send(store, telegram, NOW.replace(hour=8, minute=59)) == {"sent": 0}
    assert send(store, telegram, NOW.replace(hour=9)) == {"sent": 1}
    assert send(store, telegram, NOW + timedelta(days=1, hours=-3)) == {"sent": 1}
    assert "time unspecified" in telegram.send_message.call_args.args[1]
    assert "Due: 10/17 · time unspecified" in telegram.send_message.call_args.args[1]
    assert send(store, telegram, NOW + timedelta(days=2)) == {"sent": 0}


def test_changed_removed_and_completed_deadlines(store):
    task = store.create_task("Soon", NOW + timedelta(hours=2), None, NOW, {})
    telegram = bot()
    assert send(store, telegram)["sent"] == 1
    store.edit_task(task["id"], "New", NOW + timedelta(hours=3), None, {})
    assert send(store, telegram)["sent"] == 1
    store.edit_task(task["id"], "Removed", None, None, {})
    assert send(store, telegram)["sent"] == 0
    store.edit_task(task["id"], "Done", NOW + timedelta(minutes=20), None, {})
    store.complete_task(task["id"], NOW)
    assert send(store, telegram)["sent"] == 0


def test_delivery_uncertainty_does_not_resend_or_block_other_tasks(store):
    store.create_task("Uncertain", NOW + timedelta(hours=2), None, NOW, {})
    store.create_task("Other", NOW + timedelta(hours=3), None, NOW, {})
    telegram = bot()
    telegram.send_message.side_effect = [TelegramError("transport"), {"ok": True}]
    with pytest.raises(RuntimeError):
        send(store, telegram)
    assert telegram.send_message.await_count == 2
    with pytest.raises(RuntimeError):
        send(store, telegram)
    assert telegram.send_message.await_count == 2


def test_definite_rejection_can_retry(store):
    store.create_task("Soon", NOW + timedelta(hours=2), None, NOW, {})
    telegram = bot()
    telegram.send_message.side_effect = [{"ok": False, "error_code": 429}, {"ok": True}]
    with pytest.raises(RuntimeError):
        send(store, telegram)
    assert send(store, telegram) == {"sent": 1}
    assert send(store, telegram) == {"sent": 0}


def test_sending_claim_survives_crash_and_service_restart(tmp_path):
    db = Database(tmp_path / "restart.db")
    db.initialize()
    db.create_task("Soon", NOW + timedelta(hours=2), None, NOW, {})
    telegram = bot()
    telegram.send_message.side_effect = RuntimeError("Crash before recording delivery")
    with pytest.raises(RuntimeError):
        send(db, telegram)
    restarted = Database(db.path)
    restarted.initialize()
    telegram.send_message.side_effect = None
    with pytest.raises(RuntimeError):
        send(restarted, telegram)
    assert telegram.send_message.await_count == 1


def test_overlapping_runs_cannot_send_twice(store):
    store.create_task("Soon", NOW + timedelta(hours=2), None, NOW, {})
    telegram = bot()
    config = Settings(_env_file=None, telegram_chat_id=123)
    async def overlapping(*args, **kwargs):
        with pytest.raises(RuntimeError):
            await send_deadline_reminders(store, telegram, config, now=NOW)
        return {"ok": True}
    telegram.send_message.side_effect = overlapping
    assert send(store, telegram) == {"sent": 1}
    assert telegram.send_message.await_count == 1


def test_completion_between_claim_and_send_cancels_notice(store, monkeypatch):
    task = store.create_task("Soon", NOW + timedelta(hours=2), None, NOW, {})
    telegram = bot()
    mutate = store.mutate_task_action_state
    def complete_after_claim(key, transition):
        state = mutate(key, transition)
        if state.get("status") == "sending":
            store.complete_task(task["id"], NOW)
        return state
    monkeypatch.setattr(store, "mutate_task_action_state", complete_after_claim)
    assert send(store, telegram) == {"sent": 0}
    telegram.send_message.assert_not_awaited()


def test_endpoint_auth_scheduler_and_no_ai(system, monkeypatch):
    client, service, telegram, config = system
    parse = AsyncMock()
    monkeypatch.setattr(main.ai, "parse", parse)
    assert main.scheduler.get_job("deadline_reminders") is not None
    assert client.post('/internal/deadlines').status_code == 403
    assert client.post('/internal/deadlines', headers={'X-Chronos-Scheduler-Secret': 'wrong'}).status_code == 403
    assert client.post('/internal/deadlines', headers={'X-Chronos-Scheduler-Secret': 'test-scheduler'}).status_code == 200
    parse.assert_not_awaited()
