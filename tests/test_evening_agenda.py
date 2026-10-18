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
    assert result == [{**first, "_calendar_id": "primary"}]
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


def test_tomorrow_command_reads_full_day_without_consuming_evening_receipt(system,monkeypatch):
    from chronos import main
    client,tasks,bot,config=system
    calendar=AsyncMock()
    calendar.agenda.return_value=[event('Later'),{'summary':'All-day item','start':{'date':'2026-10-20'},'end':{'date':'2026-10-21'}}]
    monkeypatch.setattr(main,'google_calendar',calendar)
    output=asyncio.run(main.prepare_message('/tomorrow'))()
    text=output['messages'][0]['text']
    assert 'Tomorrow' in text and '\n\n' in text
    assert text.index('All day')<text.index('17:00')
    start,end=calendar.agenda.await_args.args
    assert start.hour==0 and end-start==timedelta(days=1)
    assert main.db.get_task_action_state('evening:agenda:'+start.date().isoformat()) is None
    assert tasks.list_open()==[]
    main.ai.parse.assert_not_awaited()


def test_tomorrow_command_reports_calendar_failure_without_false_empty_day(system,monkeypatch):
    from chronos import main
    calendar=AsyncMock();calendar.verify_owner.side_effect=CalendarError('unavailable')
    monkeypatch.setattr(main,'google_calendar',calendar)
    reply=asyncio.run(main.prepare_message('/tomorrow'))()
    assert 'unavailable' in reply and 'No events' not in reply


def test_today_command(system, monkeypatch):
    from chronos import main
    from datetime import datetime
    calendar = AsyncMock()
    calendar.agenda.return_value = []
    monkeypatch.setattr(main, "google_calendar", calendar)
    output = asyncio.run(main.prepare_message("/TODAY@ChronosBot"))()
    assert "Today" in output["messages"][0]["text"]
    start, end = calendar.agenda.await_args.args
    assert start.date() == datetime.now(main.settings.tz).date()
    assert start.hour == 0 and end-start == timedelta(days=1)
    main.ai.parse.assert_not_awaited()


@pytest.mark.parametrize("outcome,marker", [("done", "✓ Finished"), ("partial", "◐ Incomplete"), ("skipped", "− Skipped"), (None, "◇ Awaiting")])
def test_agenda_progress_markers(outcome, marker):
    from unittest.mock import Mock
    db = Mock()
    db.get_task_action_state.return_value = {"feedback": {"outcome": outcome}}
    item = {**event(), "extendedProperties": {"private": {"chronosToken": "old-session"}}}
    now = NOW + timedelta(days=2)
    text = "\n".join(render_agenda([item], now.date(), NOW.tzinfo, db=db, now=now))
    assert "Study graphs\n" + marker in text
    db.get_task_action_state.assert_called_once_with("learning:history:old-session")


def test_future_and_untracked_events_have_no_awaiting_marker():
    from unittest.mock import Mock
    db = Mock()
    db.get_task_action_state.return_value = {}
    item = {**event(), "extendedProperties": {"private": {"chronosToken": "future"}}}
    text = "\n".join(render_agenda([item, event()], NOW.date(), NOW.tzinfo, db=db, now=NOW))
    assert "Awaiting" not in text
