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


def test_discovery_is_sent_once_and_completion_suppresses_future_delivery(tmp_path):
    db, now, task, bot = setup(tmp_path)
    for _ in range(4):
        asyncio.run(tick_assignments(db, bot, 123, now))
    assert bot.send_message.await_count == 1
    db.complete_task(task["task_id"], now)
    asyncio.run(tick_assignments(db, bot, 123, now + timedelta(hours=22)))
    assert bot.send_message.await_count == 1


def test_missing_deadline_is_still_a_single_first_seen_notice(tmp_path):
    db, now, task, bot = setup(tmp_path, False)
    for hours in (0, 1, 24):
        asyncio.run(tick_assignments(db, bot, 123, now + timedelta(hours=hours)))
    assert bot.send_message.await_count == 1
    text = bot.send_message.call_args.args[1]
    assert '/edit 清單順位' in text
    assert '/deadline' not in text
    assert '/assignment' not in text


def test_unknown_delivery_never_blindly_resends(tmp_path):
    db, now, task, bot = setup(tmp_path)
    bot.send_message.side_effect = TelegramError("transport")
    for hours in (0, 1, 2):
        asyncio.run(tick_assignments(db, bot, 123, now + timedelta(hours=hours)))
    assert bot.send_message.await_count == 1


def test_deadline_edits_do_not_generate_followup_notifications(tmp_path):
    db, now, task, bot = setup(tmp_path)
    asyncio.run(tick_assignments(db, bot, 123, now))
    db.edit_task(task["task_id"], "Report", None, "course")
    for _ in range(3):
        asyncio.run(tick_assignments(db, bot, 123, now))
    assert bot.send_message.await_count == 1


def test_source_changes_do_not_create_a_second_notification(tmp_path):
    from dataclasses import replace
    db, now, task, bot = setup(tmp_path)
    asyncio.run(tick_assignments(db, bot, 123, now))
    old = task['assignment']
    changed = replace(old, description='Updated instructions', discovered_at=now + timedelta(minutes=1))
    db.refresh_assignment(changed)
    asyncio.run(tick_assignments(db, bot, 123, now + timedelta(minutes=1)))
    assert bot.send_message.await_count == 1
    assert db.get_assignment(old.key)['assignment'].source_revision == 1
    db.refresh_assignment(replace(changed, discovered_at=now + timedelta(minutes=2)))
    assert db.get_assignment(old.key)['assignment'].source_revision == 1
    for minute in (2, 3, 4):
        asyncio.run(tick_assignments(db, bot, 123, now + timedelta(minutes=minute)))
    assert bot.send_message.await_count == 1


def test_new_assignment_includes_open_and_edit_buttons(tmp_path):
    db, now, _, bot = setup(tmp_path)
    asyncio.run(tick_assignments(db, bot, 123, now))

    markup = bot.send_message.call_args.kwargs["reply_markup"]
    assert markup["inline_keyboard"][0][0]["text"] == "Open in TronClass"
    assert markup["inline_keyboard"][0][1]["text"] == "Edit task"
