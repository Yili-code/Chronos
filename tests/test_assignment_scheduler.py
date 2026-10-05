import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from chronos.assignments import Assignment
from chronos.assignment_scheduler import tick_assignments
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from chronos.telegram import TelegramError


def setup(tmp_path, deadline=True):
    db = Database(tmp_path / "reminders.db")
    db.initialize()
    now = datetime(2026, 10, 5, 12, tzinfo=TAIPEI)
    item = Assignment("course", "source", "Report", "Instructions", now,
                      now + timedelta(days=1) if deadline else None, "source" if deadline else None)
    task = db.create_assignment(item)
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 10}}
    return db, now, task, bot


def test_discovery_reminder_dedup_and_completion(tmp_path):
    db, now, task, bot = setup(tmp_path)
    for _ in range(4):
        asyncio.run(tick_assignments(db, bot, 123, now))
    assert bot.send_message.await_count == 2  # discovery, current reminder window
    db.complete_task(task["task_id"], now)
    asyncio.run(tick_assignments(db, bot, 123, now + timedelta(hours=22)))
    assert bot.send_message.await_count == 2


def test_missing_deadline_asks_once_without_timed_reminders(tmp_path):
    db, now, task, bot = setup(tmp_path, False)
    for hours in (0, 1, 24):
        asyncio.run(tick_assignments(db, bot, 123, now + timedelta(hours=hours)))
    assert bot.send_message.await_count == 1
    assert f"/deadline {task['task_id']}" in bot.send_message.call_args.args[1]


def test_unknown_delivery_never_blindly_resends(tmp_path):
    db, now, task, bot = setup(tmp_path)
    bot.send_message.side_effect = TelegramError("transport")
    for hours in (0, 1, 2):
        asyncio.run(tick_assignments(db, bot, 123, now + timedelta(hours=hours)))
    assert bot.send_message.await_count == 1


def test_removed_deadline_gets_one_new_confirmation_question(tmp_path):
    db, now, task, bot = setup(tmp_path)
    asyncio.run(tick_assignments(db, bot, 123, now))
    db.edit_task(task["task_id"], "Report", None, "course")
    for _ in range(3):
        asyncio.run(tick_assignments(db, bot, 123, now))
    assert bot.send_message.await_count == 2
    assert "截止時間尚未提供" in bot.send_message.call_args.args[1]


def test_source_changes_notify_once_but_identical_refresh_does_not(tmp_path):
    from dataclasses import replace
    db, now, task, bot = setup(tmp_path)
    asyncio.run(tick_assignments(db, bot, 123, now))
    old = task['assignment']
    changed = replace(old, description='Updated instructions', discovered_at=now + timedelta(minutes=1))
    db.refresh_assignment(changed)
    asyncio.run(tick_assignments(db, bot, 123, now + timedelta(minutes=1)))
    assert '作業來源更新' in bot.send_message.call_args.args[1]
    assert db.get_assignment(old.key)['assignment'].source_revision == 1
    db.refresh_assignment(replace(changed, discovered_at=now + timedelta(minutes=2)))
    assert db.get_assignment(old.key)['assignment'].source_revision == 1
    for minute in (2, 3, 4):
        asyncio.run(tick_assignments(db, bot, 123, now + timedelta(minutes=minute)))
    texts = [call.args[1] for call in bot.send_message.call_args_list]
    assert sum('作業來源更新' in text for text in texts) == 1
