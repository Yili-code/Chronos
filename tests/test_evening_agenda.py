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
    calendar.agenda.return_value = [
        {**event(), "extendedProperties": {"private": {"chronosTask": "123"}}},
        {**event("Personal event"), "id": "personal"},
        {**event("Invalid task reference"), "id": "invalid",
         "extendedProperties": {"private": {"chronosTask": "<invalid>"}}},
    ]
    monkeypatch.setattr(main, "google_calendar", calendar)
    before = datetime.now(main.settings.tz)
    output = asyncio.run(main.prepare_message("/TODAY@ChronosBot"))()
    after = datetime.now(main.settings.tz)
    assert output["messages"][0]["text"].startswith("<b>")
    assert "Today" not in output["messages"][0]["text"]
    text = output["messages"][0]["text"]
    assert "10/20 17:00–10/20 18:30 · Study graphs" in text
    assert "10/20 17:00–10/20 18:30 · Personal event" in text
    assert "10/20 17:00–10/20 18:30 · Invalid task reference" in text
    assert "<invalid>" not in text
    assert "minutes" not in text and "(123)" not in text
    start, end = calendar.agenda.await_args.args
    assert start.date() == datetime.now(main.settings.tz).date()
    assert before <= start <= after
    assert end == datetime.combine(start.date() + timedelta(days=1), datetime.min.time(), main.settings.tz)
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


def test_completed_task_resolves_current_awaiting_without_inventing_feedback(store):
    task = store.create_task('Check operating system class scope', None, None, NOW)
    key = 'learning:task:' + str(task['id'])
    state = {'token': 'current-session', 'status': 'awaiting_feedback'}
    store.mutate_task_action_state(key, lambda old: state)
    assert store.get_task(task['id'])['status'] == 'open'
    item = {**event(task['title']), 'extendedProperties': {'private': {
        'chronosTask': str(task['id']), 'chronosToken': state['token']}}}
    now = NOW + timedelta(days=2)
    assert 'Awaiting' in '\n'.join(render_agenda([item], now.date(), NOW.tzinfo, db=store, now=now))
    store.complete_task(task['id'], now)
    text = '\n'.join(render_agenda([item], now.date(), NOW.tzinfo, db=store, now=now))
    assert '✓ Finished' in text and 'Awaiting' not in text
    assert store.get_task(task['id'])['status'] == 'done'
    assert store.get_task(9999999) is None
    assert store.get_task_action_state(key) == state
    assert store.get_task_action_state('learning:history:current-session') is None
    assert store.get_task_action_state('learning:profile') is None


def test_completed_task_preserves_prior_session_outcome_and_token_boundary(store):
    task = store.create_task('Read chapter', None, None, NOW)
    store.mutate_task_action_state('learning:task:' + str(task['id']),
        lambda old: {'token': 'current-session', 'status': 'awaiting_feedback'})
    store.complete_task(task['id'], NOW)
    props = {'chronosTask': str(task['id']), 'chronosToken': 'prior-session'}
    item = {**event(), 'extendedProperties': {'private': props}}
    now = NOW + timedelta(days=2)
    text = '\n'.join(render_agenda([item], now.date(), NOW.tzinfo, db=store, now=now))
    assert 'Awaiting' in text and 'Finished' not in text
    store.mutate_task_action_state('learning:history:prior-session',
        lambda old: {'feedback': {'outcome': 'partial', 'actual_minutes': 135}})
    text = '\n'.join(render_agenda([item], now.date(), NOW.tzinfo, db=store, now=now))
    assert 'Incomplete' in text and 'Finished' not in text


@pytest.mark.parametrize("clock", ["2026-10-24T14:30:00+08:00", "2026-10-24T15:59:59+00:00", "2026-10-23T16:00:00+00:00"])
def test_today_reads_only_remaining_local_day(clock):
    from chronos.evening_agenda import tomorrow_agenda

    config = Settings(_env_file=None)
    now = datetime.fromisoformat(clock)
    local_now = now.astimezone(config.tz)
    calendar = GoogleCalendar(config)
    calendar.verify_owner = AsyncMock()
    calendar.request = AsyncMock(return_value={"items": [
        {"id": "ongoing", "summary": "Ongoing", "start": {"dateTime": (local_now - timedelta(hours=1)).isoformat()},
         "end": {"dateTime": (local_now + timedelta(minutes=1)).isoformat()}},
        {"id": "all-day", "summary": "All-day item", "start": {"date": local_now.date().isoformat()},
         "end": {"date": (local_now.date() + timedelta(days=1)).isoformat()}},
    ]})
    text = "\n".join(asyncio.run(tomorrow_agenda(calendar, config, now, day_offset=0)))
    params = calendar.request.await_args.kwargs["params"]
    assert params["timeMin"] == local_now.isoformat()
    midnight = datetime.combine(local_now.date() + timedelta(days=1), datetime.min.time(), config.tz)
    assert params["timeMax"] == midnight.isoformat()
    assert "Ongoing" in text and "All day · All-day item" in text
    calendar.verify_owner.assert_awaited_once()


def test_today_remaining_day_can_be_empty():
    from chronos.evening_agenda import tomorrow_agenda

    config = Settings(_env_file=None)
    calendar = AsyncMock()
    calendar.agenda.return_value = []
    text = "\n".join(asyncio.run(tomorrow_agenda(calendar, config, NOW, day_offset=0)))
    assert "Today" not in text and "No events scheduled." in text
    assert calendar.agenda.await_args.args == (NOW, NOW.replace(hour=0) + timedelta(days=1))


def test_today_compact_format_matches_requested_layout():
    from chronos.evening_agenda import tomorrow_agenda

    config = Settings(_env_file=None)
    calendar = AsyncMock()
    calendar.agenda.return_value = [
        {"summary": "Codeforces Round 1129", "start": {"dateTime": "2026-10-24T19:05:00+08:00"},
         "end": {"dateTime": "2026-10-24T21:05:00+08:00"}},
        {"summary": "Study ISPM Chapter 2", "start": {"dateTime": "2026-10-24T18:00:00+08:00"},
         "end": {"dateTime": "2026-10-24T19:00:00+08:00"},
         "extendedProperties": {"private": {"chronosTask": "207"}}},
    ]
    parts = asyncio.run(tomorrow_agenda(calendar, config, datetime.fromisoformat("2026-10-24T17:33:00+08:00"), day_offset=0))
    assert parts == ["<b>10/24 (Sat)</b>\n\n18:00–19:00 · Study ISPM Chapter 2\n19:05–21:05 · Codeforces Round 1129"]


def test_compact_agenda_escapes_titles_and_keeps_continuations_compact():
    item = event("<Graphs>\nsecond line " + "x" * 450)
    parts = render_agenda([item] * 20, NOW.date() + timedelta(days=1), NOW.tzinfo, compact=True)
    assert len(parts) > 1 and all(len(part) < 4096 for part in parts)
    assert all("&lt;Graphs&gt; second line" in part and "minutes" not in part for part in parts)
    assert all("\n\n17:00" in part and "\n17:00" in part for part in parts)
