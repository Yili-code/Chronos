import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from chronos.assignment_scan import run_assignment_scan
from chronos.assignments import Assignment
from chronos.db import Database


TZ = ZoneInfo("Asia/Taipei")


def _item(now, *, source_id="100", deadline=None, submitted=False):
    return Assignment("189684", source_id, "Homework", "Read chapter", now, deadline,
                      "source" if deadline else None, submission_status="submitted" if submitted else "unknown")


def _db(tmp_path):
    db = Database(tmp_path / "scanner.db")
    db.initialize()
    return db


def test_first_identical_failure_is_silent_second_notifies_once(tmp_path):
    db, bot = _db(tmp_path), AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 1}}
    now = datetime(2026, 10, 25, 12, tzinfo=TZ)
    def fail(*args, **kwargs):
        from chronos.tronclass_collector import CollectorFailure
        raise CollectorFailure("courses_shape")
    first = asyncio.run(run_assignment_scan(db, bot, 123, now, "u", "p", collect=fail))
    second = asyncio.run(run_assignment_scan(db, bot, 123, now + timedelta(days=7), "u", "p", collect=fail))
    third = asyncio.run(run_assignment_scan(db, bot, 123, now + timedelta(days=14), "u", "p", collect=fail))
    assert first["assignment_scan"] == "failure_recorded"
    assert second["assignment_scan"] == "failure_notified"
    assert third["assignment_scan"] == "failure_recorded"
    assert bot.send_message.await_count == 1


def test_first_scan_only_creates_future_unsubmitted_assignments(tmp_path):
    db, bot = _db(tmp_path), AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 1}}
    now = datetime(2026, 10, 25, 12, tzinfo=TZ)
    items = [_item(now, source_id="1", deadline=now + timedelta(days=1)),
             _item(now, source_id="2", deadline=now - timedelta(days=1)),
             _item(now, source_id="3", deadline=now + timedelta(days=1), submitted=True)]
    result = asyncio.run(run_assignment_scan(db, bot, 123, now, "u", "p", collect=lambda *args, **kwargs: items))
    assert result["created"] == 1
    assert result["baseline_skipped"] == 2
    assert len(db.list_assignment_keys()) == 1


def test_successful_empty_scan_sends_one_status(tmp_path):
    db, bot = _db(tmp_path), AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 1}}
    now = datetime(2026, 10, 25, 12, tzinfo=TZ)
    first = asyncio.run(run_assignment_scan(db, bot, 123, now, "u", "p", collect=lambda *args, **kwargs: []))
    second = asyncio.run(run_assignment_scan(db, bot, 123, now, "u", "p", collect=lambda *args, **kwargs: []))
    assert first["no_new_status_sent"]
    assert not second["no_new_status_sent"]
    assert bot.send_message.await_count == 1


def test_expired_baseline_survives_restart_and_extended_deadline(tmp_path):
    from dataclasses import replace
    db, bot = _db(tmp_path), AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 1}}
    now = datetime(2026, 10, 25, 6, tzinfo=TZ)
    old = _item(now, deadline=now - timedelta(days=1))
    asyncio.run(run_assignment_scan(db, bot, 123, now, "u", "p", collect=lambda *a, **k: [old]))
    reopened = Database(db.path)
    reopened.initialize()
    extended = replace(old, deadline=now + timedelta(days=3))
    result = asyncio.run(run_assignment_scan(reopened, bot, 123, now + timedelta(days=1), "u", "p", collect=lambda *a, **k: [extended]))
    assert result["created"] == 0
    assert not reopened.list_open_tasks()


def test_unknown_failure_notice_is_never_blindly_resent(tmp_path):
    from chronos.telegram import TelegramError
    from chronos.tronclass_collector import CollectorFailure
    db, bot = _db(tmp_path), AsyncMock()
    bot.send_message.side_effect = TelegramError("unknown delivery")
    now = datetime(2026, 10, 25, 6, tzinfo=TZ)
    def fail(*args, **kwargs):
        raise CollectorFailure("courses_shape")
    for day in range(4):
        asyncio.run(run_assignment_scan(db, bot, 123, now + timedelta(days=day), "u", "p", collect=fail))
    assert bot.send_message.await_count == 1
    assert not db.get_assignment_scan_state()["failure_notified"]


def test_overlapping_scan_is_suppressed_and_calendar_pause_does_not_collect(tmp_path):
    from unittest.mock import Mock
    from chronos.calendar_overrides import CalendarOverrides, apply_calendar_override
    db, bot = _db(tmp_path), AsyncMock()
    now = datetime(2026, 10, 25, 6, tzinfo=TZ)
    collect = Mock(return_value=[])
    db.mutate_task_action_state("assignment-scan-lease", lambda _: {"token": "other", "expires_at": (now + timedelta(minutes=2)).isoformat()})
    result = asyncio.run(run_assignment_scan(db, bot, 123, now, "u", "p", collect=collect))
    assert result["assignment_scan"] == "already_running"
    collect.assert_not_called()
    db.save_calendar_overrides(apply_calendar_override(CalendarOverrides(), "disable 2026-10-25").to_record())
    result = asyncio.run(run_assignment_scan(db, bot, 123, now + timedelta(minutes=3), "u", "p", collect=collect))
    assert result["assignment_scan"] == "paused_by_calendar"
    collect.assert_not_called()
    bot.send_message.assert_not_awaited()
