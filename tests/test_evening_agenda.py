import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chronos.evening_agenda import render_agenda, send_evening_agenda
from chronos.google_calendar import GoogleCalendar, CalendarError
from chronos.settings import Settings
from test_deadline_reminders import store
from test_system import system

NOW = datetime.fromisoformat("2026-10-19T21:00:00+08:00")


def event(title="Study graphs"):
    return {"id": "1", "summary": title, "start": {"dateTime": "2026-10-20T17:00:00+08:00"},
            "end": {"dateTime": "2026-10-20T18:30:00+08:00"}}


def test_agenda_delivery_deduplicates_and_waits_until_evening(store):
    config = Settings(_env_file=None, enable_learning_calendar=True, telegram_chat_id=123)
    calendar, bot = AsyncMock(), AsyncMock()
    calendar.agenda.return_value = [event("<Graphs>")]
    bot.send_message.return_value = {"ok": True}
    def send(now=NOW):
        return asyncio.run(send_evening_agenda(store, calendar, bot, config, now))
    assert "waiting_for" in send(NOW.replace(hour=20))
    calendar.agenda.assert_not_awaited()
    assert send()["sent"] == 1
    send()
    assert bot.send_message.await_count == 1
    text = bot.send_message.await_args.args[1]
    assert "10/20 (Tue)" in text and "17:00–18:30 · 90 minutes" in text
    assert "&lt;Graphs&gt;" in text
    start, end = calendar.agenda.await_args.args
    assert start.isoformat() == "2026-10-20T00:00:00+08:00"
    assert end - start == timedelta(days=1)


def test_empty_day_and_uncertain_send_do_not_duplicate(store):
    config = Settings(_env_file=None, enable_learning_calendar=True, telegram_chat_id=123)
    calendar, bot = AsyncMock(), AsyncMock()
    calendar.agenda.return_value = []
    bot.send_message.side_effect = TimeoutError()
    def send():
        return asyncio.run(send_evening_agenda(store, calendar, bot, config, NOW))
    with pytest.raises(RuntimeError, match="uncertain"):
        send()
    assert "No events scheduled" in bot.send_message.await_args.args[1]
    with pytest.raises(RuntimeError, match="inspection"):
        send()
    assert bot.send_message.await_count == 1


def test_read_failure_is_retryable_without_empty_digest(store):
    config = Settings(_env_file=None, enable_learning_calendar=True, telegram_chat_id=123)
    calendar, bot = AsyncMock(), AsyncMock()
    calendar.agenda.side_effect = CalendarError("unavailable")
    with pytest.raises(CalendarError):
        asyncio.run(send_evening_agenda(store, calendar, bot, config, NOW))
    bot.send_message.assert_not_awaited()
    assert store.get_task_action_state("evening:agenda:2026-10-20") is None


def test_agenda_expands_pages_normalizes_primary_and_omits_declined():
    config = Settings(_env_file=None, calendar_account="owner@example.com", calendar_busy_ids="owner@example.com,primary")
    calendar = GoogleCalendar(config)
    first = event()
    cancelled = {**event(), "id": "2", "status": "cancelled"}
    declined = {**event(), "id": "3", "attendees": [{"self": True, "responseStatus": "declined"}]}
    calendar.request = AsyncMock(side_effect=[{"items": [first], "nextPageToken": "page2"},
                                            {"items": [cancelled, declined]}])
    result = asyncio.run(calendar.agenda(NOW, NOW + timedelta(days=1)))
    assert result == [first]
    assert calendar.request.await_count == 2
    assert calendar.request.await_args.kwargs["params"]["pageToken"] == "page2"
    assert calendar.request.await_args.args[1] == "calendars/primary/events"


def test_all_day_overnight_and_long_agendas():
    day = NOW.date() + timedelta(days=1)
    all_day = {"summary": "Holiday", "start": {"date": "2026-10-20"}, "end": {"date": "2026-10-21"}}
    overnight = {**event(), "end": {"dateTime": "2026-10-21T00:30:00+08:00"}}
    text = "\n".join(render_agenda([all_day, overnight], day, NOW.tzinfo))
    assert "All day · Holiday" in text
    assert "10/21 00:30" in text
    assert all(len(p) < 4096 for p in render_agenda([event("x" * 500)] * 30, day, NOW.tzinfo))


def test_endpoint_requires_scheduler_auth(system):
    client, _, _, _ = system
    assert client.post("/internal/agenda/tomorrow").status_code == 403
    assert client.post("/internal/agenda/tomorrow", headers={"X-Chronos-Scheduler-Secret": "test-scheduler"}).json() == {"enabled": False}
