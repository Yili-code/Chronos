"""Calendar scheduling safety and learning lifecycle tests against both stores."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chronos.db import Database
from chronos.google_calendar import CalendarError, GoogleCalendar
from chronos.learning_calendar import LearningCalendar, find_slot
from chronos.settings import Settings
from test_firestore import database as firestore_database
from test_system import system
from chronos import main

NOW = datetime.fromisoformat("2026-10-19T09:00:00+08:00")


def test_batch_owner_duration_survives_sequential_approval_and_restart(setup):
    planner, task, calendar, bot = setup
    others = [planner.db.create_task(f'Lecture {i}', NOW + timedelta(days=3), None, NOW) for i in range(2)]
    ids = [others[1]['id'], task['id'], others[0]['id']]
    reply, queued = planner.request_proposals(ids, minutes=90)
    assert queued and '90 minutes each' in reply
    occupied = []
    async def busy(*args):
        return list(occupied)
    calendar.busy.side_effect = busy
    planner = LearningCalendar(planner.db, calendar, bot, planner.settings)
    for index, task_id in enumerate(ids):
        asyncio.run(planner.tick(NOW))
        state = planner.get(task_id)
        assert state['status'] == 'proposed'
        assert state['minutes'] == 90 and state['duration_source'] == 'owner'
        assert calendar.insert.await_count == index
        start, end = (datetime.fromisoformat(state[k]) for k in ('start', 'end'))
        assert end - start == timedelta(minutes=90)
        assert all(end <= a or start >= b for a, b in occupied)
        asyncio.run(planner.callback('approve', task_id, state['token'], 50))
        assert planner.get(task_id)['status'] == 'scheduled'
        assert calendar.insert.await_count == index + 1
        occupied.append((start, end))
    assert [int(call.args[0]['extendedProperties']['private']['chronosTask'])
        for call in calendar.insert.await_args_list] == ids


@pytest.mark.parametrize('status', ['scheduled', 'awaiting_feedback', 'creating', 'backward_managed'])
def test_batch_rejects_ineligible_member_without_changing_other_plans(setup, status):
    planner, task, calendar, _ = setup
    other = planner.db.create_task('Other', None, None, NOW)
    planner.update(task['id'], lambda _: {'status': 'proposed', 'token': 'original', 'minutes': 30})
    planner.update(other['id'], lambda _: {'status': status, 'token': 'blocked'})
    before = [planner.get(i) for i in (task['id'], other['id'])]
    _, queued = planner.request_proposals([task['id'], other['id']], minutes=90)
    assert not queued
    assert [planner.get(i) for i in (task['id'], other['id'])] == before
    assert planner.db.get_task_action_state('learning:proposal-queue') is None
    calendar.insert.assert_not_awaited()


def test_batch_retires_old_proposal_and_cannot_approve_stale_token(setup):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    old = planner.get(task['id'])
    other = planner.db.create_task('Other', None, None, NOW)
    planner.request_proposals([other['id'], task['id']], minutes=90)
    asyncio.run(planner.callback('approve', task['id'], old['token'], 50))
    calendar.insert.assert_not_awaited()
    assert planner.get(task['id'])['requested_minutes'] == 90


def test_plan_groups_pending_work_and_separates_booked_tasks(setup):
    planner, task, _, _ = setup
    other = planner.db.create_task('Remaining chapter', None, None, NOW)
    booked = planner.db.create_task('Booked chapter', None, None, NOW)
    for item, status in ((task, 'proposed'), (other, 'needs_replan'), (booked, 'scheduled')):
        planner.update(item['id'], lambda old, status=status: {'status': status})
    pending = planner.status()
    assert pending.count('<b>Awaiting approval</b>') == 1
    assert pending.count('<b>Needs replanning</b>') == 1
    assert 'Remaining chapter' in pending and 'Booked chapter' not in pending
    assert '/planedit ID your request' in pending
    assert 'Report progress' not in pending
    assert 'Booked chapter' in planner.status(booked_only=True)
    assert 'Remaining chapter' not in planner.status(booked_only=True)


def test_partial_report_closes_old_task_and_creates_one_successor(setup):
    from chronos.task_actions import task_fields
    planner, task, calendar, _ = setup
    planner.update(task['id'], lambda old: {
        'status': 'awaiting_feedback', 'token': 'handoff', 'snapshot': task_fields(task)})
    reply = planner.partial_reply(task['id'], 'handoff', '135 | 20/48 | 21')
    assert 'Recorded' in reply
    assert planner.db.get_task(task['id'])['status'] == 'done'
    successor = planner.db.list_open_tasks()[0]
    assert 'Continue pages 21–48' in successor['title']
    assert successor['project'] == task['project']
    assert successor['due_at'] == task['due_at']
    assert planner.get(successor['id'])['requested_minutes'] == 180
    assert planner.get(successor['id'])['status'] == 'queued'
    planner.partial_reply(task['id'], 'handoff', '135 | 20/48 | 21')
    assert len(planner.db.list_open_tasks()) == 1
    calendar.insert.assert_not_awaited()

@pytest.fixture(params=["sqlite", "firestore"])
def setup(request, tmp_path, monkeypatch):
    import chronos.learning_calendar as module
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(module, "datetime", Clock)
    db = firestore_database(monkeypatch) if request.param == "firestore" else Database(tmp_path / "plans.db")
    db.initialize()
    config = Settings(_env_file=None, enable_learning_calendar=True, learning_auto_accept=False, learning_enabled_since=NOW.isoformat(),
        telegram_chat_id=123, calendar_account="owner@example.com")
    calendar = AsyncMock()
    calendar.configured = lambda: True
    calendar.busy.return_value = []
    calendar.event.return_value = None
    async def insert(body):
        return {**body, "htmlLink": "https://calendar.google.com/event"}
    calendar.insert.side_effect = insert
    bot = AsyncMock()
    bot.request.return_value = {"ok": True}
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 50}}
    planner = LearningCalendar(db, calendar, bot, config)
    task = db.create_task("Study graphs", NOW + timedelta(days=3), "graphs", NOW, {})
    return planner, task, calendar, bot


@pytest.mark.parametrize('case', ['ended', 'future', 'moved', 'cancelled', 'foreign', 'changed', 'unavailable'])
def test_manual_feedback_verifies_booking_without_waiting_for_tick(setup, case):
    from chronos.task_actions import task_fields
    planner, task, calendar, _ = setup
    state = {'status': 'scheduled', 'token': 'manual-feedback', 'event_id': 'event',
             'snapshot': task_fields(task), 'end': (NOW - timedelta(hours=1)).isoformat()}
    planner.update(task['id'], lambda old: state)
    event = {'end': {'dateTime': (NOW + timedelta(hours=1) if case in {'future', 'moved'}
                                else NOW - timedelta(hours=1)).isoformat()},
             'extendedProperties': {'private': {'chronosToken': 'foreign' if case == 'foreign' else state['token']}}}
    if case == 'cancelled':
        event['status'] = 'cancelled'
    calendar.event.return_value = event
    if case == 'unavailable':
        calendar.event.side_effect = CalendarError('Unavailable')
    action = asyncio.run(planner.prepare_feedback(task, 'skipped', 0))
    if case == 'changed':
        planner.update(task['id'], lambda old: {**old, 'event_id': 'replacement'})
    reply = action()
    current = planner.get(task['id'])
    if case == 'ended':
        assert 'learning_recovery' in reply['messages'][0]
        assert current['status'] == 'awaiting_resolution'
        assert current['feedback']['outcome'] == 'skipped'
        assert 'already been handled' in action()
    else:
        assert current['status'] == 'scheduled'
        assert 'feedback' not in current
        assert not reply.startswith('Recorded.')
    calendar.insert.assert_not_awaited()


def test_slot_avoids_class_and_calendar_and_ends_by_21(setup):
    planner, task, _, _ = setup
    busy = [(NOW.replace(hour=12), NOW.replace(hour=15))]
    start, end = find_slot(task, NOW, busy, [], planner.settings, 90)
    assert start == NOW.replace(hour=15)
    assert end == NOW.replace(hour=16, minute=30)
    assert end.hour < 21
    # Monday class consumes 165 minutes, leaving at most 375 study minutes.
    reservations = [(NOW.replace(hour=13), NOW.replace(hour=19))]
    later = find_slot(task, NOW, [], reservations, planner.settings, 90)
    assert later[0].date() > NOW.date()


def test_only_one_proposal_is_shown_until_approval_succeeds(setup):
    planner, task, calendar, bot = setup
    other = planner.db.create_task("Study databases", NOW + timedelta(days=3), None, NOW, {})
    asyncio.run(planner.tick(NOW))
    first = planner.get(task["id"])
    assert planner.get(other["id"]) is None
    assert first["minutes"] == 90
    calendar.insert.assert_not_awaited()
    assert "10/19 (Mon) 13:00–14:30" in bot.send_message.await_args_list[0].args[1]
    assert "90 minutes" not in bot.send_message.await_args_list[0].args[1]
    assert "Calendar write waits for your approval" not in bot.send_message.await_args_list[0].args[1]
    # Frozen scheduling now is in the future relative to booking's wall clock when needed.
    reply = asyncio.run(planner.callback("approve", task["id"], first["token"], 50))
    assert "scheduled" in reply
    asyncio.run(planner.callback("approve", task["id"], first["token"], 50))
    assert calendar.insert.await_count == 1
    assert calendar.insert.await_args.args[0]['colorId'] == '10'
    assert planner.get(task["id"])["status"] == "scheduled"
    calendar.busy.return_value = [(datetime.fromisoformat(first["start"]), datetime.fromisoformat(first["end"]))]
    asyncio.run(planner.tick(NOW))
    second = planner.get(other["id"])
    assert second["status"] == "proposed"
    assert first["end"] <= second["start"] or second["end"] <= first["start"]


def test_initial_deadline_plan_can_preview_moving_an_owner_activity(setup):
    planner, task, calendar, _ = setup
    _, now = reflow_calendar(planner, calendar)
    deadline = now.replace(hour=18, minute=0)
    planner.db.edit_task(task["id"], task["title"], deadline, task["project"], task["timing"])

    planner.request_proposal(task["id"])
    asyncio.run(planner.tick(now))

    state = planner.get(task["id"])
    assert state["status"] == "proposed"
    assert state["start"] == now.replace(hour=13, minute=0).isoformat()
    assert state["calendar_moves"][0]["title"] == "Personal reading"
    text, _ = planner.proposal_content(planner.db.get_task(task["id"]), state)
    assert "estimate" not in text.lower()
    assert "Schedule changes requiring approval" in text
    calendar.move_event.assert_not_awaited()


def test_deadline_plan_button_prepares_and_displays_proposal(system, monkeypatch):
    import chronos.learning_calendar as learning_module
    class LateClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat("2026-10-19T22:00:00+08:00").astimezone(tz)
    monkeypatch.setattr(learning_module, "datetime", LateClock)
    client, tasks, bot, config = system
    config.enable_learning_calendar = True
    config.learning_enabled_since = "2020-01-01T00:00:00+08:00"
    calendar = AsyncMock()
    calendar.configured = lambda: True
    calendar.busy.return_value = []
    calendar.verify_owner.return_value = {"id": "owner@example.com"}
    monkeypatch.setattr(main, "google_calendar", calendar)
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 50}}
    task = tasks.create("Read chapter", LateClock.now(config.tz) + timedelta(days=1))
    payload = {"update_id": 99830, "callback_query": {"id": "plan",
        "data": f"task:plan:{task['id']}", "message": {"message_id": 49, "chat": {"id": 123}}}}

    response = client.post("/telegram/webhook", json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"})

    assert response.status_code == 200
    assert "is next" in bot.send_message.await_args_list[-2].args[1]
    assert "<b>Your proposed work session, Sir.</b>" in bot.send_message.await_args_list[-1].args[1]
    buttons = bot.send_message.await_args_list[-1].kwargs["reply_markup"]["inline_keyboard"]
    assert [button["text"] for row in buttons for button in row] == [
        "Confirm schedule", "Adjust plan", "Defer"]
    calendar.insert.assert_not_awaited()


def test_wrong_card_and_changed_task_do_not_book(setup):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    asyncio.run(planner.callback("approve", task["id"], state["token"], 999))
    calendar.insert.assert_not_awaited()
    planner.db.edit_task(task["id"], "Changed", None, None, {})
    reply = asyncio.run(planner.callback("approve", task["id"], state["token"], 50))
    assert "changed" in reply
    calendar.insert.assert_not_awaited()


def test_busy_after_proposal_requires_new_approval(setup):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    calendar.busy.return_value = [(datetime.fromisoformat(state["start"]), datetime.fromisoformat(state["end"]))]
    assert "busy" in asyncio.run(planner.callback("approve", task["id"], state["token"], 50))
    assert planner.get(task["id"])["status"] == "queued"
    calendar.insert.assert_not_awaited()


def test_uncertain_insert_reconciles_same_event_id(setup):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    captured = {}
    async def insert(body):
        captured.update(body)
        raise CalendarError("timeout")
    calendar.insert.side_effect = insert
    with pytest.raises(CalendarError):
        asyncio.run(planner.callback("approve", task["id"], state["token"], 50))
    assert planner.get(task["id"])["status"] == "creating"
    calendar.event.return_value = captured
    asyncio.run(planner.tick(NOW + timedelta(minutes=5)))
    assert calendar.insert.await_count == 1
    assert planner.get(task["id"])["status"] == "scheduled"


def test_ended_event_follows_up_once_and_feedback_is_durable(setup):
    planner, task, calendar, bot = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    asyncio.run(planner.callback("approve", task["id"], state["token"], 50))
    calendar.event.return_value = {"end": {"dateTime": state["end"]},
        "extendedProperties": {"private": {"chronosToken": state["token"]}}}
    end = datetime.fromisoformat(state["end"])
    asyncio.run(planner.tick(end + timedelta(minutes=1)))
    asyncio.run(planner.tick(end + timedelta(minutes=6)))
    assert bot.send_message.await_count == 2
    assert planner.get(task["id"])["status"] == "awaiting_feedback"
    assert "Completed" in planner.feedback(task, state["token"], "done", 60, "Finished faster")
    assert "already" in planner.feedback(task, state["token"], "done", 60)
    assert planner.db.list_open_tasks() == []
    profile = planner.db.get_task_action_state("learning:profile")
    assert profile["graphs"] == {"samples": 1, "average_minutes": 60}
    history = planner.db.get_task_action_state("learning:history:" + state["token"])
    assert history["feedback"]["reason"] == "Finished faster"


def test_edited_calendar_end_is_respected_after_proposal_cutoff(setup):
    planner, task, calendar, bot = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    asyncio.run(planner.callback("approve", task["id"], state["token"], 50))
    end = datetime.fromisoformat(state["end"])
    calendar.event.return_value = {"end": {"dateTime": (end + timedelta(hours=2)).isoformat()},
        "extendedProperties": {"private": {"chronosToken": state["token"]}}}
    asyncio.run(planner.tick(end + timedelta(minutes=1)))
    assert planner.get(task["id"])["status"] == "scheduled"
    asyncio.run(planner.tick(NOW.replace(hour=21)))
    assert bot.send_message.await_count == 2
    assert planner.get(task["id"])["status"] == "awaiting_feedback"


def test_no_slot_and_calendar_error_do_not_silently_book(setup):
    planner, task, calendar, bot = setup
    calendar.busy.return_value = [(NOW, NOW + timedelta(days=7))]
    asyncio.run(planner.tick(NOW))
    assert planner.get(task["id"])["status"] == "no_slot"
    assert "I could not find a suitable work session, Sir." in bot.send_message.await_args.args[1]
    calendar.insert.assert_not_awaited()


def test_existing_tasks_require_explicit_replan_and_completed_time_stays_reserved(setup):
    planner, task, calendar, bot = setup
    planner.settings.learning_enabled_since = (NOW + timedelta(seconds=1)).isoformat()
    asyncio.run(planner.tick(NOW))
    assert planner.get(task["id"]) is None
    bot.send_message.assert_not_awaited()


def test_completed_sessions_do_not_reset_daily_learning_budget(setup):
    planner, task, calendar, bot = setup
    planner.settings.learning_daily_minutes = 255  # Monday class 165 + one study block 90.
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    asyncio.run(planner.callback("approve", task["id"], state["token"], 50))
    calendar.event.return_value = {"end": {"dateTime": state["end"]},
        "extendedProperties": {"private": {"chronosToken": state["token"]}}}
    end = datetime.fromisoformat(state["end"])
    asyncio.run(planner.tick(end + timedelta(minutes=1)))
    planner.feedback(task, state["token"], "done", 90)
    other = planner.db.create_task("Another subject", NOW + timedelta(days=7), None, NOW, {})
    asyncio.run(planner.tick(end + timedelta(minutes=5)))
    assert datetime.fromisoformat(planner.get(other["id"])["start"]).date() > NOW.date()


def test_uncertain_telegram_delivery_does_not_resend_proposals(setup):
    planner, task, calendar, bot = setup
    bot.send_message.side_effect = RuntimeError("transport")
    with pytest.raises(CalendarError, match="uncertain"):
        asyncio.run(planner.tick(NOW))
    assert planner.get(task["id"])["delivery"] == "proposal_uncertain"
    asyncio.run(planner.tick(NOW + timedelta(minutes=5)))
    assert bot.send_message.await_count == 1
    calendar.insert.assert_not_awaited()


def test_transport_rejects_inaccessible_calendars_and_sanitizes_credentials():
    import httpx
    config = Settings(_env_file=None, calendar_client_id="secret-id", calendar_client_secret="secret-password",
        calendar_refresh_token="secret-refresh", calendar_account="owner@example.com")
    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "secret-access"})
        return httpx.Response(200, json={"calendars": {"primary": {"errors": [{"reason": "notFound"}]}}})
    client = GoogleCalendar(config, transport=httpx.MockTransport(handler))
    with pytest.raises(CalendarError, match="could not be checked"):
        asyncio.run(client.busy(NOW, NOW + timedelta(days=1)))


def test_callback_rejects_non_owner_and_plan_status_is_read_only(system, monkeypatch):
    client, tasks, bot, config = system
    config.enable_learning_calendar = True
    fake = AsyncMock()
    monkeypatch.setattr(main, "google_calendar", fake)
    payload = {"update_id": 99800, "callback_query": {"id": "c", "data": "learn:approve:1:" + "a" * 32,
        "message": {"message_id": 50, "chat": {"id": 999}}}}
    assert client.post("/telegram/webhook", json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"}).status_code == 403
    fake.insert.assert_not_awaited()


def test_owner_approval_webhook_receipts_prevent_duplicate_events(system, setup, monkeypatch):
    client, tasks, bot, config = system
    _, _, calendar, _ = setup
    config.enable_learning_calendar = True
    config.learning_enabled_since = "2020-01-01T00:00:00+08:00"
    monkeypatch.setattr(main, "google_calendar", calendar)
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 50}}
    task = tasks.create("Study graphs", NOW + timedelta(days=3))
    planner = main.learning_calendar()
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    payload = {"update_id": 99801, "callback_query": {"id": "c", "data": f"learn:approve:{task['id']}:{state['token']}",
        "message": {"message_id": 50, "chat": {"id": 123}}}}
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-hook"}
    assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 200
    assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 200
    assert calendar.insert.await_count == 1
    assert main.db.get_update(99801)["delivered"]


def test_learning_internal_endpoints_require_scheduler_secret(system):
    client, _, _, _ = system
    assert client.get("/internal/learning/status").status_code == 403
    assert client.post("/internal/learning").status_code == 403
    headers = {"X-Chronos-Scheduler-Secret": "test-scheduler"}
    assert client.get("/internal/learning/status", headers=headers).json() == {"enabled": False}
    assert client.post("/internal/learning", headers=headers).json() == {"enabled": False}


def test_reported_durations_only_adapt_after_three_samples(setup):
    planner, task, calendar, bot = setup
    planner.db.mutate_task_action_state("learning:profile", lambda old: {"graphs": {"samples": 3, "average_minutes": 61}})
    asyncio.run(planner.tick(NOW))
    assert planner.get(task["id"])["minutes"] == 75
    assert planner.settings.learning_daily_minutes == 540
    calendar.insert.assert_not_awaited()


def test_partial_feedback_preserves_evidence_and_queues_another_approval(setup):
    planner, task, calendar, bot = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    asyncio.run(planner.callback("approve", task["id"], state["token"], 50))
    calendar.event.return_value = {"end": {"dateTime": state["end"]},
        "extendedProperties": {"private": {"chronosToken": state["token"]}}}
    end = datetime.fromisoformat(state["end"])
    asyncio.run(planner.tick(end + timedelta(minutes=1)))
    planner.feedback(task, state["token"], "partial", 45, "Need another chapter")
    assert planner.get(task["id"])["status"] == "finished"
    successor_id = planner.get(task["id"])["successor_task_id"]
    asyncio.run(planner.tick(end + timedelta(minutes=5)))
    new = planner.get(successor_id)
    assert new["status"] == "proposed" and new["token"] != state["token"]
    assert calendar.insert.await_count == 1
    assert planner.db.get_task_action_state("learning:history:" + state["token"])["feedback"]["actual_minutes"] == 45


def test_primary_email_alias_is_normalized_before_freebusy_validation():
    import json
    import httpx
    config = Settings(_env_file=None, calendar_client_id="id", calendar_client_secret="secret",
        calendar_refresh_token="refresh", calendar_account="owner@example.com",
        calendar_busy_ids="primary,owner@example.com,family@example.com")
    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "access"})
        ids = {v["id"] for v in json.loads(request.content)["items"]}
        assert ids == {"primary", "family@example.com"}
        return httpx.Response(200, json={"calendars": {key: {"busy": []} for key in ids}})
    client = GoogleCalendar(config, transport=httpx.MockTransport(handler))
    assert asyncio.run(client.busy(NOW, NOW + timedelta(days=1))) == []


def test_edit_replaces_card_and_requires_fresh_approval(setup):
    planner, task, calendar, bot = setup
    asyncio.run(planner.tick(NOW))
    old = planner.get(task["id"])
    text, buttons = planner.proposal_content(task, old)
    assert "10/19 (Mon)" in text
    assert [b["text"] for b in buttons] == ["Confirm schedule", "Adjust plan", "Defer"]
    prompt = asyncio.run(planner.callback("edit", task["id"], old["token"], 50))
    rows = prompt["messages"][0]["reply_markup"]["inline_keyboard"]
    assert sum(not row[0]["callback_data"].startswith("learn:day") for row in rows) == 8
    prompt = asyncio.run(planner.callback("manual", task["id"], old["token"], 50))
    assert prompt["messages"][0]["reply_markup"]["force_reply"]
    assert "no longer available" in asyncio.run(planner.callback("approve", task["id"], old["token"], 50))
    assert "Updated" in asyncio.run(planner.revise(task["id"], old["token"], "10/20 18:00 60", NOW))
    revised = planner.get(task["id"])
    assert revised["token"] != old["token"]
    assert revised["minutes"] == 60
    assert revised["start"] == "2026-10-20T18:00:00+08:00"
    calendar.insert.assert_not_awaited()
    asyncio.run(planner.tick(NOW))
    recovered = asyncio.run(planner.callback("approve", task["id"], old["token"], 50))
    assert recovered["messages"][0]["learning_proposal"]["token"] == revised["token"]
    calendar.insert.assert_not_awaited()
    assert "scheduled" in asyncio.run(planner.callback("approve", task["id"], revised["token"], 50))
    assert calendar.insert.await_count == 1


@pytest.mark.parametrize("instruction,reason", [
    ("some time maybe", "Use MM/DD"),
    ("10/20 23:00 90", "future 10"),
    ("10/20 14:00 60", "conflicts"),
    ("10/23 17:00 60", "deadline"),
    ("02/30 17:00 60", "Invalid date"),
])
def test_invalid_edits_preserve_pending_state(setup, instruction, reason):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    old = planner.get(task["id"])
    asyncio.run(planner.callback("edit", task["id"], old["token"], 50))
    assert reason in asyncio.run(planner.revise(task["id"], old["token"], instruction, NOW))
    assert planner.get(task["id"])["status"] == "editing"
    assert planner.get(task["id"])["token"] == old["token"]
    calendar.insert.assert_not_awaited()


def test_course_policy_excludes_exams_and_ends_teaching_period(setup):
    from chronos.learning_calendar import class_windows
    planner, task, calendar, _ = setup
    policy = {"first_day": "2026-09-07", "last_regular_day": "2026-12-18",
              "excluded_dates": ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]}
    assert class_windows(NOW.date(), planner.settings.tz, policy)
    for day in ["2026-10-26", "2026-10-28", "2026-12-21", "2027-01-04"]:
        assert not class_windows(datetime.fromisoformat(day).date(), planner.settings.tz, policy)
    planner.db.mutate_task_action_state("learning:course-calendar-policy", lambda old: policy)
    exam_now = NOW + timedelta(days=7)
    planner.db.edit_task(task["id"], task["title"], exam_now + timedelta(days=3), "graphs", {})
    asyncio.run(planner.tick(exam_now))
    assert "T09:15:" in planner.get(task["id"])["start"]


def test_edit_prompt_reply_webhook_is_durable_and_does_not_book(system, setup, monkeypatch):
    client, tasks, bot, config = system
    _, _, calendar, _ = setup
    config.enable_learning_calendar = True
    config.learning_enabled_since = "2020-01-01T00:00:00+08:00"
    monkeypatch.setattr(main, "google_calendar", calendar)
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 50}}
    task = tasks.create("Study graphs", NOW + timedelta(days=3))
    planner = main.learning_calendar()
    asyncio.run(planner.tick(NOW))
    old = planner.get(task["id"])
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-hook"}
    callback = {"update_id": 99810, "callback_query": {"id": "edit", "data": f"learn:edit:{task['id']}:{old['token']}",
        "message": {"message_id": 50, "chat": {"id": 123}}}}
    assert client.post("/telegram/webhook", json=callback, headers=headers).status_code == 200
    callback["update_id"] = 99812
    callback["callback_query"]["data"] = f"learn:manual:{task['id']}:{old['token']}"
    assert client.post("/telegram/webhook", json=callback, headers=headers).status_code == 200
    binding = main.db.get_task_action_state("learning:edit-message:123:50")
    assert binding["token"] == old["token"]
    message = {"update_id": 99811, "message": {"message_id": 51, "chat": {"id": 123},
        "text": "10/20 18:00 60", "reply_to_message": {"message_id": 50, "chat": {"id": 123}}}}
    assert client.post("/telegram/webhook", json=message, headers=headers).status_code == 200
    token = planner.get(task["id"])["token"]
    assert client.post("/telegram/webhook", json=message, headers=headers).status_code == 200
    assert planner.get(task["id"])["token"] == token != old["token"]
    assert planner.get(task["id"])["status"] == "proposed"
    calendar.insert.assert_not_awaited()


@pytest.mark.parametrize("option,minutes", [("more", 120), ("lots", 180), ("shuffle", 90)])
def test_edit_options_generate_new_safe_proposal_without_booking(setup, option, minutes):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    old = planner.get(task["id"])
    asyncio.run(planner.callback("edit", task["id"], old["token"], 50))
    assert "Updated" in asyncio.run(planner.callback(option, task["id"], old["token"], 50))
    state = planner.get(task["id"])
    assert state["minutes"] == minutes
    assert state["token"] != old["token"]
    assert state["status"] == "proposed"
    if option == "shuffle":
        assert state["start"] >= old["end"] or state["end"] <= old["start"]
    recovered = asyncio.run(planner.callback(option, task["id"], old["token"], 50))
    assert recovered["messages"][0]["learning_proposal"]["token"] == state["token"]
    calendar.insert.assert_not_awaited()


def test_edit_options_fail_closed_when_calendar_has_no_slot(setup):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    old = planner.get(task["id"])
    asyncio.run(planner.callback("edit", task["id"], old["token"], 50))
    calendar.busy.return_value = [(NOW, NOW + timedelta(days=8))]
    for option in ("more", "lots", "shuffle"):
        assert "No alternative fits" in asyncio.run(planner.callback(option, task["id"], old["token"], 50))
        assert planner.get(task["id"])["status"] == "editing"
        assert planner.get(task["id"])["token"] == old["token"]
    calendar.insert.assert_not_awaited()


def test_edit_choice_webhook_binds_menu_and_delivers_new_approval_card(system, setup, monkeypatch):
    client, tasks, bot, config = system
    _, _, calendar, _ = setup
    config.enable_learning_calendar = True
    config.learning_enabled_since = "2020-01-01T00:00:00+08:00"
    monkeypatch.setattr(main, "google_calendar", calendar)
    bot.send_message.side_effect = [{"ok": True, "result": {"message_id": n}} for n in [50, 60, 70, 80]]
    task = tasks.create("Study graphs", NOW + timedelta(days=3))
    planner = main.learning_calendar()
    asyncio.run(planner.tick(NOW))
    old = planner.get(task["id"])
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-hook"}
    payload = {"update_id": 99820, "callback_query": {"id": "edit", "data": f"learn:edit:{task['id']}:{old['token']}",
        "message": {"message_id": 50, "chat": {"id": 123}}}}
    assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 200
    assert planner.get(task["id"])["edit_message_id"] == 60
    assert planner.get(task["id"])["message_id"] == 50
    assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 200
    assert bot.send_message.await_count == 2
    payload["update_id"] = 99821
    payload["callback_query"]["data"] = f"learn:more:{task['id']}:{old['token']}"
    payload["callback_query"]["message"]["message_id"] = 60
    assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 200
    revised = planner.get(task["id"])
    assert revised["message_id"] == 80 and revised["token"] != old["token"]
    assert revised["minutes"] == 120 and revised["delivery"] == "proposal_sent"
    assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 200
    assert bot.send_message.await_count == 4
    calendar.insert.assert_not_awaited()


def test_stale_edit_recovers_latest_menu_without_applying_an_old_choice(setup):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    menu = asyncio.run(planner.callback("edit", task["id"], "0" * 32, 999))
    assert menu["messages"][0]["learning_menu"]["token"] == state["token"]
    assert planner.get(task["id"])["status"] == "editing"
    planner.update(task["id"], lambda old: {**old, "edit_message_id": 60})
    # Returning to the original proposal can open Edit again.
    assert "learning_menu" in asyncio.run(planner.callback("edit", task["id"], state["token"], 50))["messages"][0]
    # A choice from an old menu only refreshes the view.
    assert "learning_menu" in asyncio.run(planner.callback("lots", task["id"], "0" * 32, 999))["messages"][0]
    assert planner.get(task["id"])["minutes"] == 90
    assert "Updated" in asyncio.run(planner.callback("more", task["id"], state["token"], 60))
    assert planner.get(task["id"])["minutes"] == 120
    calendar.insert.assert_not_awaited()


def test_stale_allow_webhook_refreshes_current_card_without_booking(system, setup, monkeypatch):
    client, tasks, bot, config = system
    _, _, calendar, _ = setup
    config.enable_learning_calendar = True
    config.learning_enabled_since = "2020-01-01T00:00:00+08:00"
    monkeypatch.setattr(main, "google_calendar", calendar)
    bot.send_message.side_effect = [{"ok": True, "result": {"message_id": n}} for n in [50, 60, 70]]
    task = tasks.create("Study graphs", NOW + timedelta(days=3))
    planner = main.learning_calendar()
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    payload = {"update_id": 99830, "callback_query": {"id": "stale", "data": f"learn:approve:{task['id']}:" + "0" * 32,
        "message": {"message_id": 999, "chat": {"id": 123}}}}
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-hook"}
    assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 200
    assert planner.get(task["id"])["message_id"] == 60
    assert planner.get(task["id"])["token"] == state["token"]
    calendar.insert.assert_not_awaited()
    payload["update_id"] = 99831
    payload["callback_query"]["data"] = f"learn:approve:{task['id']}:{state['token']}"
    payload["callback_query"]["message"]["message_id"] = 60
    assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 200
    assert calendar.insert.await_count == 1


@pytest.mark.parametrize("option", ["lots", "shuffle"])
def test_legacy_proposals_become_queued_and_keep_their_chosen_duration(setup, option):
    from chronos.task_actions import task_fields
    planner, task, calendar, _ = setup
    planner.db.edit_task(task["id"], task["title"], NOW.replace(hour=21), "graphs", {})
    asyncio.run(planner.tick(NOW))
    original = planner.get(task["id"])
    others = []
    cursor = datetime.fromisoformat(original["end"])
    for duration in [120, 90, 90, 90]:
        other = planner.db.create_task("Other study", NOW + timedelta(days=3), None, NOW, {})
        state = {**original, "token": str(other["id"]).zfill(32), "minutes": duration,
            "start": cursor.isoformat(), "end": (cursor + timedelta(minutes=duration)).isoformat(),
            "snapshot": task_fields(other)}
        planner.update(other["id"], lambda old: state)
        others.append((other, state))
        cursor += timedelta(minutes=duration)
    asyncio.run(planner.callback("edit", task["id"], original["token"], 50))
    reply = asyncio.run(planner.callback(option, task["id"], original["token"], 50))
    assert "Updated" in reply
    revised = planner.get(task["id"])
    assert revised["minutes"] == (180 if option == "lots" else 90)
    displaced = [(other, old) for other, old in others if planner.get(other["id"])["status"] == "queued"]
    assert displaced
    for other, old in displaced:
        assert planner.get(other["id"])["requested_minutes"] == old["minutes"]
        # Old Allow cannot book an evicted proposal.
        asyncio.run(planner.callback("approve", other["id"], old["token"], 50))
    calendar.insert.assert_not_awaited()
    asyncio.run(planner.tick(NOW))
    for other, old in displaced:
        current = planner.get(other["id"])
        assert current["requested_minutes"] == old["minutes"]
        assert current["token"] != old["token"]
        assert current["status"] == "queued"
    asyncio.run(planner.callback("approve", task["id"], revised["token"], 50))
    calendar.busy.return_value = [(datetime.fromisoformat(revised["start"]), datetime.fromisoformat(revised["end"]))]
    asyncio.run(planner.tick(NOW))
    assert planner.get(others[0][0]["id"])["minutes"] == 120
    assert sum(planner.get(other["id"])["status"] == "proposed" for other, _ in others) == 1


def test_revision_cannot_replace_an_approval_that_started_during_calendar_read(setup):
    planner, task, calendar, _ = setup
    other = planner.db.create_task("Other study", NOW + timedelta(days=3), None, NOW, {})
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    async def raced_busy(start, end):
        planner.update(task["id"], lambda old: {**old, "status": "creating"})
        return []
    calendar.busy.side_effect = raced_busy
    reply = asyncio.run(planner.revise(task["id"], state["token"], "10/19 13:00 180", NOW))
    assert "changed while editing" in reply
    assert planner.get(task["id"])["status"] == "creating"
    assert planner.get(task["id"])["token"] == state["token"]
    assert planner.get(other["id"]) is None
    calendar.insert.assert_not_awaited()


def test_edit_repeated_ticks_and_direct_planning_cannot_advance_the_queue(setup):
    planner, task, calendar, bot = setup
    other = planner.db.create_task("Next task", NOW + timedelta(days=3), None, NOW, {})
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    asyncio.run(planner.callback("edit", task["id"], state["token"], 50))
    asyncio.run(planner.tick(NOW + timedelta(minutes=5)))
    asyncio.run(planner.propose(other, NOW, []))
    assert planner.get(other["id"]) is None
    assert bot.send_message.await_count == 1
    assert "Updated" in asyncio.run(planner.revise(task["id"], state["token"], "10/20 18:00 120", NOW + timedelta(minutes=5)))
    asyncio.run(planner.tick(NOW + timedelta(minutes=5)))
    assert bot.send_message.await_count == 2
    assert planner.get(other["id"]) is None
    calendar.insert.assert_not_awaited()


def test_failed_booking_does_not_advance_the_queue(setup):
    planner, task, calendar, _ = setup
    other = planner.db.create_task("Next task", NOW + timedelta(days=3), None, NOW, {})
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    calendar.insert.side_effect = CalendarError("uncertain")
    with pytest.raises(CalendarError):
        asyncio.run(planner.callback("approve", task["id"], state["token"], 50))
    with pytest.raises(CalendarError):
        asyncio.run(planner.tick(NOW))
    assert planner.get(task["id"])["status"] == "creating"
    assert planner.get(other["id"]) is None


def test_approval_webhook_immediately_shows_exactly_one_next_proposal(system, setup, monkeypatch):
    client, tasks, bot, config = system
    _, _, calendar, _ = setup
    config.enable_learning_calendar = True
    config.learning_enabled_since = "2020-01-01T00:00:00+08:00"
    monkeypatch.setattr(main, "google_calendar", calendar)
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 50}}
    first = tasks.create("First task", NOW + timedelta(days=3))
    second = tasks.create("Second task", NOW + timedelta(days=3))
    third = tasks.create("Third task", NOW + timedelta(days=3))
    planner = main.learning_calendar()
    asyncio.run(planner.tick(NOW))
    state = planner.get(first["id"])
    assert planner.get(second["id"]) is None
    payload = {"update_id": 99840, "callback_query": {"id": "allow", "data": f"learn:approve:{first['id']}:{state['token']}",
        "message": {"message_id": 50, "chat": {"id": 123}}}}
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-hook"}
    assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 200
    assert planner.get(first["id"])["status"] == "scheduled"
    assert planner.get(second["id"])["status"] == "proposed"
    assert planner.get(third["id"]) is None
    assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 200
    assert calendar.insert.await_count == 1
    assert planner.get(third["id"]) is None
    assert bot.send_message.await_count == 3  # First proposal, booking reply, next proposal.


def test_checkins_continue_while_the_next_proposal_is_waiting(setup):
    planner, task, calendar, bot = setup
    other = planner.db.create_task("Next task", NOW + timedelta(days=3), None, NOW, {})
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    asyncio.run(planner.callback("approve", task["id"], state["token"], 50))
    asyncio.run(planner.tick(NOW))
    second = planner.get(other["id"])
    calendar.event.return_value = {"end": {"dateTime": state["end"]},
        "extendedProperties": {"private": {"chronosToken": state["token"]}}}
    asyncio.run(planner.tick(datetime.fromisoformat(state["end"]) + timedelta(minutes=1)))
    assert planner.get(task["id"])["status"] == "awaiting_feedback"
    assert planner.get(other["id"])["token"] == second["token"]
    assert bot.send_message.await_count == 3


def test_weekday_period_respects_duration_conflicts_and_stays_editable(setup):
    from chronos.learning_calendar import study_window
    planner, task, calendar, _ = setup
    begin, end, minutes = study_window("星期日早上", NOW - timedelta(days=2), 180)
    assert begin.isoformat() == "2026-10-18T09:00:00+08:00"
    assert end.hour == 12 and minutes == 180
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    planner.update(task["id"], lambda old: {**old, "status": "editing", "minutes": 180})
    menu = planner.edit_menu(task["id"], planner.get(task["id"]))
    assert len(menu["messages"][0]["reply_markup"]["inline_keyboard"]) == 6
    calendar.busy.return_value = [(NOW.replace(day=20, hour=10, minute=30), NOW.replace(day=20, hour=12))]
    reply = asyncio.run(planner.revise(task["id"], state["token"], "星期二早上", NOW))
    assert "No continuous 180-minute slot" in reply
    assert planner.get(task["id"])["status"] == "editing"
    assert "Updated" in asyncio.run(planner.revise(task["id"], state["token"], "星期二早上 90", NOW))
    assert planner.get(task["id"])["start"] == "2026-10-20T09:00:00+08:00"
    calendar.insert.assert_not_awaited()


def test_manual_plain_text_retries_never_create_tasks(system, setup, monkeypatch):
    client, tasks, bot, config = system
    _, _, calendar, _ = setup
    config.enable_learning_calendar = True
    config.learning_enabled_since = "2020-01-01T00:00:00+08:00"
    monkeypatch.setattr(main, "google_calendar", calendar)
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 50}}
    task = tasks.create("Study graphs", NOW + timedelta(days=3))
    planner = main.learning_calendar()
    asyncio.run(planner.tick(NOW))
    token = planner.get(task["id"])["token"]
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-hook"}
    for index, action in enumerate(["edit", "manual"]):
        payload = {"update_id": 99850 + index, "callback_query": {"id": action,
            "data": f"learn:{action}:{task['id']}:{token}", "message": {"message_id": 50, "chat": {"id": 123}}}}
        assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 200
    def send(update_id, text):
        return client.post("/telegram/webhook", headers=headers,
            json={"update_id": update_id, "message": {"message_id": update_id, "chat": {"id": 123}, "text": text}})
    assert send(99852, "not a time").status_code == 200
    assert main.db.get_task_action_state("learning:edit-chat:123")["token"] == token
    assert send(99853, "10/28 09:00").status_code == 200
    assert "deadline 10/22" in bot.send_message.await_args.args[1]
    assert len(tasks.list_open()) == 1
    assert send(99854, "星期二早上").status_code == 200
    assert planner.get(task["id"])["start"] == "2026-10-20T09:00:00+08:00"
    assert not main.db.get_task_action_state("learning:edit-chat:123")
    assert send(99854, "星期二早上").status_code == 200
    assert len(tasks.list_open()) == 1
    main.ai.parse.assert_not_awaited()
    calendar.insert.assert_not_awaited()


def test_orphan_date_messages_do_not_become_tasks(system):
    client, tasks, bot, _ = system
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-hook"}
    for index, text in enumerate(["10/28 09:00", "星期日早上"]):
        response = client.post("/telegram/webhook", headers=headers,
            json={"update_id": 99860 + index, "message": {"chat": {"id": 123}, "text": text}})
        assert response.status_code == 200
        assert "No task was created" in bot.send_message.await_args.args[1]
    assert not tasks.list_open()
    main.ai.parse.assert_not_awaited()


@pytest.mark.parametrize("from_menu", [False, True])
def test_postpone_keeps_task_open_and_advances_without_calendar_write(setup, from_menu):
    from chronos.tasks import TaskService, format_tasks
    planner, task, calendar, _ = setup
    other = planner.db.create_task("Ready task", NOW + timedelta(days=3), None, NOW, {})
    asyncio.run(planner.tick(NOW))
    old = planner.get(task["id"])
    message_id = 50
    if from_menu:
        asyncio.run(planner.callback("edit", task["id"], old["token"], 50))
        planner.update(task["id"], lambda value: {**value, "edit_message_id": 60})
        message_id = 60
    planner.db.mutate_task_action_state("learning:edit-chat:123", lambda old_value: {"task_id": task["id"], "token": old["token"]})
    reply = asyncio.run(planner.callback("postpone", task["id"], old["token"], message_id))
    assert "Waiting for external conditions" in reply
    assert f"/plan {task['id']}" in reply
    state = planner.get(task["id"])
    assert state["status"] == "postponed" and state["token"] != old["token"]
    assert "start" not in state and "end" not in state
    assert not planner.db.get_task_action_state("learning:edit-chat:123")
    service = TaskService(planner.db, planner.settings.tz)
    live = service.get_open_by_id(task["id"])
    assert live["due_at"] == task["due_at"]
    assert live["planning_status"] == "postponed"
    assert "Waiting for external conditions" in format_tasks(service.list_open(), planner.settings.tz)
    asyncio.run(planner.tick(NOW))
    assert planner.get(other["id"])["status"] == "proposed"
    asyncio.run(planner.tick(NOW + timedelta(days=1)))
    assert planner.get(task["id"])["status"] == "postponed"
    assert "waiting for external conditions" in asyncio.run(planner.callback("approve", task["id"], old["token"], 50))
    calendar.insert.assert_not_awaited()


def test_postpone_cannot_cancel_an_approved_booking(setup):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    asyncio.run(planner.callback("approve", task["id"], state["token"], 50))
    reply = asyncio.run(planner.callback("postpone", task["id"], state["token"], 50))
    assert "No scheduled event was changed" in reply
    assert planner.get(task["id"])["status"] == "scheduled"
    assert calendar.insert.await_count == 1


def test_postpone_webhook_is_idempotent_and_plan_resumes_explicitly(system, setup, monkeypatch):
    client, tasks, bot, config = system
    _, _, calendar, _ = setup
    config.enable_learning_calendar = True
    config.learning_enabled_since = "2020-01-01T00:00:00+08:00"
    monkeypatch.setattr(main, "google_calendar", calendar)
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 50}}
    task = tasks.create("Waiting task", NOW + timedelta(days=3))
    other = tasks.create("Ready task", NOW + timedelta(days=3))
    planner = main.learning_calendar()
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-hook"}
    payload = {"update_id": 99870, "callback_query": {"id": "postpone",
        "data": f"learn:postpone:{task['id']}:{state['token']}", "message": {"message_id": 50, "chat": {"id": 123}}}}
    assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 200
    token = planner.get(task["id"])["token"]
    assert planner.get(other["id"])["status"] == "proposed"
    assert client.post("/telegram/webhook", json=payload, headers=headers).status_code == 200
    assert planner.get(task["id"])["token"] == token
    resume = {"update_id": 99871, "message": {"message_id": 51, "chat": {"id": 123}, "text": f"/plan {task['id']}"}}
    assert client.post("/telegram/webhook", json=resume, headers=headers).status_code == 200
    assert planner.get(task["id"])["status"] == "proposed"
    assert planner.get(other["id"])["status"] == "queued"
    assert "planning_status" not in tasks.get_open_by_id(task["id"])
    assert "messages" in asyncio.run(planner.callback("approve", task["id"], state["token"], 50))
    calendar.insert.assert_not_awaited()


@pytest.mark.parametrize("title", ["Check course scope", "Check software engineering 10/14 class scope",
    "Check operating system class scope", "確認作業系統上課範圍"])
def test_course_scope_rule_defaults_to_ten_minutes_even_for_legacy_queue(setup, title):
    planner, task, calendar, _ = setup
    planner.db.edit_task(task["id"], title, NOW + timedelta(days=3), "graphs", {})
    planner.update(task["id"], lambda old: {"status": "queued", "requested_minutes": 90})
    planner.db.mutate_task_action_state("learning:profile", lambda old: {"graphs": {"samples": 5, "average_minutes": 120}})
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    assert state["minutes"] == 10 and state["duration_source"] == "course_scope_rule"
    assert datetime.fromisoformat(state["end"]) - datetime.fromisoformat(state["start"]) == timedelta(minutes=10)
    menu = planner.edit_menu(task["id"], state)["messages"][0]["reply_markup"]["inline_keyboard"]
    assert menu[0][0]["text"] == "15 min"
    assert menu[1][0]["text"] == "20 min"
    calendar.insert.assert_not_awaited()


def test_course_scope_manual_duration_survives_resumption(setup):
    planner, task, _, _ = setup
    planner.db.edit_task(task["id"], "Check course scope", NOW + timedelta(days=3), "graphs", {})
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    assert "Updated" in asyncio.run(planner.revise(task["id"], state["token"], "10/20 18:00 20", NOW))
    revised = planner.get(task["id"])
    planner.update(task["id"], lambda old: {**old, "status": "queued"})
    asyncio.run(planner.tick(NOW))
    assert planner.get(task["id"])["minutes"] == 20
    assert planner.get(task["id"])["duration_source"] == "owner"
    state = planner.get(task["id"])
    assert "Updated" in asyncio.run(planner.revise(task["id"], state["token"], "10/20 17:00 10", NOW))
    assert planner.get(task["id"])["minutes"] == 10


def test_revision_respects_daily_budget_busy_events_and_scheduler_lease(setup):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    planner.db.mutate_task_action_state("learning:day:2026-10-20", lambda old: {"booked": 350})
    assert "limit" in asyncio.run(planner.revise(task["id"], state["token"], "10/20 18:00 60", NOW))
    planner.db.mutate_task_action_state("learning:day:2026-10-20", lambda old: {})
    calendar.busy.return_value = [(NOW.replace(day=20, hour=18), NOW.replace(day=20, hour=19))]
    assert "conflicts" in asyncio.run(planner.revise(task["id"], state["token"], "10/20 18:00 60", NOW))
    planner.db.mutate_task_action_state("learning:tick", lambda old: {
        "claim": "scheduler", "until": (NOW + timedelta(minutes=5)).isoformat()})
    assert "shortly" in asyncio.run(planner.revise(task["id"], state["token"], "10/20 18:00 60", NOW))
    assert planner.get(task["id"])["token"] == state["token"]
    calendar.insert.assert_not_awaited()

@pytest.mark.parametrize('hour,minute,duration,allowed', [
    (11, 30, 30, True), (11, 45, 30, False),
    (12, 0, 10, True), (12, 0, 15, False), (12, 55, 10, True),
    (13, 0, 90, True), (16, 30, 30, True), (16, 45, 30, False),
    (17, 0, 10, True), (17, 0, 15, False), (18, 0, 90, True),
])
def test_daily_short_task_windows_boundaries(hour, minute, duration, allowed):
    from chronos.learning_calendar import overlaps, short_task_windows
    for offset in range(7):
        start = (NOW + timedelta(days=offset)).replace(hour=hour, minute=minute)
        assert (not overlaps(start, start + timedelta(minutes=duration),
                             short_task_windows(start.date(), start.tzinfo, duration))) == allowed


@pytest.mark.parametrize('randomize', [False, True])
@pytest.mark.parametrize('duration', [10, 15, 90])
def test_find_slot_enforces_short_windows_for_all_search_modes(setup, randomize, duration):
    planner, task, _, _ = setup
    start = NOW.replace(day=24, hour=12)
    task = {**task, 'due_at': start.replace(hour=13).isoformat()}
    slot = find_slot(task, NOW, [], [], planner.settings, duration,
                     randomize=randomize, window=(start, start.replace(hour=13)))
    assert (slot is not None) == (duration == 10)


@pytest.mark.parametrize('instruction', ['10/20 12:00 15', '10/20 16:45 30', '10/20 17:00 90'])
def test_manual_edit_rejects_long_blocks_in_short_windows(setup, instruction):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task['id'])
    assert 'at most 10 minutes' in asyncio.run(planner.revise(task['id'], state['token'], instruction, NOW))
    assert planner.get(task['id']) == state
    calendar.insert.assert_not_awaited()


def test_approval_requeues_legacy_long_proposal_in_short_window(setup):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    state = planner.update(task['id'], lambda old: {**old,
        'start': NOW.replace(day=20, hour=17).isoformat(),
        'end': NOW.replace(day=20, hour=18, minute=30).isoformat()})
    reply = asyncio.run(planner.callback('approve', task['id'], state['token'], 50))
    assert 'new execution plan' in reply
    assert planner.get(task['id'])['status'] == 'queued'
    calendar.insert.assert_not_awaited()

@pytest.mark.parametrize('minutes', [10, 47, 300])
def test_ai_estimate_is_cached_and_session_capped(setup, minutes):
    from chronos.ai import DurationOutput
    planner, task, calendar, _ = setup
    planner.ai = AsyncMock()
    planner.ai.estimate_duration.return_value = DurationOutput(
        estimated_minutes=minutes, confidence='low', reason='Assumes one chapter without extra exercises.')
    asyncio.run(planner.tick(NOW))
    state = planner.get(task['id'])
    assert state['minutes'] == min(180, ((minutes + 4) // 5) * 5)
    assert state['duration_source'] == 'ai'
    assert 'estimate' not in planner.proposal_content(task, state)[0].lower()
    planner.update(task['id'], lambda old: {**old, 'status': 'queued', 'requested_minutes': old['minutes']})
    asyncio.run(planner.tick(NOW))
    assert planner.ai.estimate_duration.await_count == 1
    calendar.insert.assert_not_awaited()


def test_ai_estimator_failure_uses_default_and_retries_next_replan(setup):
    from chronos.ai import AIError, DurationOutput
    planner, task, _, _ = setup
    planner.ai = AsyncMock()
    planner.ai.estimate_duration.side_effect = AIError('unavailable')
    asyncio.run(planner.tick(NOW))
    state = planner.get(task['id'])
    assert state['minutes'] == 90 and state['duration_source'] == 'default'
    assert 'estimate' not in planner.proposal_content(task, state)[0].lower()
    planner.ai.estimate_duration.side_effect = None
    planner.ai.estimate_duration.return_value = DurationOutput(estimated_minutes=30, confidence='medium', reason='One exercise.')
    planner.update(task['id'], lambda old: {**old, 'status': 'queued'})
    asyncio.run(planner.tick(NOW))
    assert planner.get(task['id'])['minutes'] == 30


@pytest.mark.parametrize('source', ['owner', 'course_scope_rule'])
def test_explicit_duration_and_scope_rule_bypass_ai(setup, source):
    planner, task, _, _ = setup
    planner.ai = AsyncMock()
    if source == 'course_scope_rule':
        planner.db.edit_task_with_undo(task['id'], 'Check software engineering class scope', None, None, {})
    else:
        planner.update(task['id'], lambda old: {'status': 'queued', 'requested_minutes': 60, 'duration_source': 'owner'})
    asyncio.run(planner.tick(NOW))
    assert planner.get(task['id'])['minutes'] == (10 if source == 'course_scope_rule' else 60)
    planner.ai.estimate_duration.assert_not_awaited()


def test_changed_task_invalidates_ai_estimate(setup):
    from chronos.ai import DurationOutput
    planner, task, _, _ = setup
    planner.ai = AsyncMock()
    planner.ai.estimate_duration.return_value = DurationOutput(estimated_minutes=30, confidence='low', reason='Small scope.')
    asyncio.run(planner.tick(NOW))
    planner.db.edit_task_with_undo(task['id'], 'Study two chapters', None, None, {})
    planner.update(task['id'], lambda old: {**old, 'status': 'queued'})
    planner.ai.estimate_duration.return_value = DurationOutput(estimated_minutes=120, confidence='medium', reason='Two chapters.')
    asyncio.run(planner.tick(NOW))
    assert planner.get(task['id'])['minutes'] == 120
    assert planner.ai.estimate_duration.await_count == 2


def test_ai_uses_completed_evidence_and_falls_back_to_profile(setup):
    from chronos.ai import AIError
    planner, task, _, _ = setup
    evidence = {'samples': 4, 'average_minutes': 42}
    planner.db.mutate_task_action_state('learning:profile', lambda old: {'graphs': evidence})
    planner.ai = AsyncMock()
    planner.ai.estimate_duration.side_effect = AIError('unavailable')
    asyncio.run(planner.tick(NOW))
    planner.ai.estimate_duration.assert_awaited_once_with(task, evidence)
    state = planner.get(task['id'])
    assert state['minutes'] == 45 and state['duration_source'] == 'profile'


def test_feedback_card_combines_time_and_duration_and_explains_input(setup):
    planner, task, _, bot = setup
    state = {'token': 'checkin', 'status': 'awaiting_feedback',
             'start': NOW.isoformat(), 'minutes': 10, 'delivery': 'feedback_pending'}
    planner.update(task['id'], lambda old: state)
    asyncio.run(planner.deliver_feedback(task, state))
    text = bot.send_message.await_args.args[1]
    assert '10/19 (Mon) 09:00 · 10 minutes planned' in text
    assert '\nWhen ' not in text and '\nPlanned ' not in text
    assert '[reason]' not in text
    assert f'<code>/taskupdate {task["id"]} 15 done</code>' in text
    assert 'What is the outcome? Choose below.' in text
    assert 'For 15 minutes completed' in text
    assert not any('\u4e00' <= char <= '\u9fff' for char in text)
    assert len(text.splitlines()) <= 9
    assert '\n\n' in text


@pytest.mark.parametrize('timing', ['soon', 'keep', 'exact'])
def test_natural_schedule_edit_preserves_remaining_work_and_progress(setup, timing):
    from chronos.ai import ScheduleEditOutput
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task['id'])
    planner.ai = AsyncMock()
    planner.ai.parse_schedule_edit.return_value = ScheduleEditOutput(actionable=True, minutes=60,
        timing=timing, start='2026-10-20T09:00:00+08:00' if timing=='exact' else None,
        progress_note='已完成部分')
    reply = asyncio.run(planner.revise(task['id'], state['token'], '待會花一小時應該就可以看得完（已完成部分）', NOW))
    assert 'Updated' in reply
    revised = planner.get(task['id'])
    assert revised['minutes'] == 60 and revised['duration_source'] == 'owner'
    assert revised['progress_note'] == '已完成部分'
    assert revised['token'] != state['token']
    assert '已完成部分' in planner.proposal_content(task, revised)[0]
    if timing == 'keep':
        assert revised['start'] == state['start']
    assert planner.db.list_open_tasks()[0]['status'] == 'open'
    calendar.insert.assert_not_awaited()


@pytest.mark.parametrize('failure', ['unrelated', 'unavailable', 'naive', 'conflict'])
def test_natural_schedule_edit_keeps_state_on_failure(setup, failure):
    from chronos.ai import AIError, ScheduleEditOutput
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task['id'])
    planner.ai = AsyncMock()
    if failure == 'unavailable':
        planner.ai.parse_schedule_edit.side_effect = AIError('offline')
    else:
        planner.ai.parse_schedule_edit.return_value = ScheduleEditOutput(
            actionable=failure!='unrelated', minutes=60, timing='exact',
            start='2026-10-20T09:00:00' if failure=='naive' else '2026-10-20T17:00:00+08:00', progress_note='note')
    reply=asyncio.run(planner.revise(task['id'], state['token'], 'natural request', NOW))
    assert 'Updated' not in reply
    assert planner.get(task['id']) == state
    calendar.insert.assert_not_awaited()

@pytest.mark.parametrize('text', ['已完成', '完成了', 'done', 'Already done.'])
def test_completion_in_edit_finishes_without_booking_or_invented_actuals(setup, text):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    state=planner.get(task['id'])
    reply=asyncio.run(planner.revise(task['id'],state['token'],text,NOW))
    assert 'Completed' in reply
    assert planner.get(task['id'])['status']=='finished'
    assert planner.get(task['id'])['feedback']['actual_minutes'] is None
    assert planner.db.list_open_tasks()==[]
    assert planner.db.get_task_action_state('learning:proposal-queue')['active_task_id'] is None
    assert planner.db.get_task_action_state('learning:profile') is None
    calendar.insert.assert_not_awaited()


def test_partial_progress_is_not_completion(setup):
    planner,task,_,_=setup
    asyncio.run(planner.tick(NOW));state=planner.get(task['id'])
    asyncio.run(planner.revise(task['id'],state['token'],'已完成部分',NOW))
    assert planner.db.list_open_tasks()
    assert planner.get(task['id'])['status']=='proposed'


def test_stale_completion_cannot_finish_a_changed_task(setup):
    planner,task,_,_=setup
    asyncio.run(planner.tick(NOW));state=planner.get(task['id'])
    planner.db.edit_task_with_undo(task['id'], 'Changed scope', None, None, {})
    assert 'changed' in asyncio.run(planner.revise(task['id'],state['token'],'已完成',NOW))
    assert planner.db.list_open_tasks()
    assert planner.get(task['id'])['token']==state['token']


def test_late_edit_requires_confirmation_then_fresh_approval(setup):
    planner,task,calendar,_=setup
    asyncio.run(planner.tick(NOW));state=planner.get(task['id'])
    reply=asyncio.run(planner.revise(task['id'],state['token'],'10/20 20:00 90',NOW))
    assert 'Reply Yes or No' in reply
    pending=planner.get(task['id'])
    assert pending['token']==state['token'] and pending['status']=='editing'
    assert pending['start']==state['start']
    reply=asyncio.run(planner.revise(task['id'],state['token'],'Yes',NOW))
    assert 'Updated' in reply
    updated=planner.get(task['id'])
    assert updated['start']=='2026-10-20T20:00:00+08:00'
    assert updated['late_window']=={'start':updated['start'],'end':updated['end']}
    assert updated['token']!=state['token']
    assert 'pending_late' not in updated
    calendar.insert.assert_not_awaited()
    assert planner.settings.learning_end_hour==21


@pytest.mark.parametrize('answer', ['No','expired','changed'])
def test_late_confirmation_does_not_relax_rules_on_rejection_or_stale_input(setup,answer):
    planner,task,calendar,_=setup
    asyncio.run(planner.tick(NOW));state=planner.get(task['id'])
    asyncio.run(planner.revise(task['id'],state['token'],'10/20 20:00 90',NOW))
    when=NOW+timedelta(minutes=16) if answer=='expired' else NOW
    if answer=='changed':
        planner.db.edit_task_with_undo(task['id'],'Changed task',None,None,{})
    reply=asyncio.run(planner.revise(task['id'],state['token'],'No' if answer=='No' else 'Yes',when))
    assert 'Updated' not in reply
    assert planner.get(task['id']).get('late_window') is None
    calendar.insert.assert_not_awaited()


def test_evening_window_requests_late_exception_when_only_later_slot_fits(setup):
    planner,task,calendar,_=setup
    asyncio.run(planner.tick(NOW));state=planner.get(task['id'])
    calendar.busy.return_value=[(NOW.replace(day=20,hour=18),NOW.replace(day=20,hour=20))]
    reply=asyncio.run(planner.revise(task['id'],state['token'],'10/20 evening 90',NOW))
    assert 'Reply Yes or No' in reply
    assert planner.get(task['id'])['pending_late']['instruction']=='2026-10-20 20:00 90'
    calendar.insert.assert_not_awaited()


def test_confirmed_late_proposal_still_needs_allow_to_book(setup):
    planner,task,calendar,_=setup
    asyncio.run(planner.tick(NOW));state=planner.get(task['id'])
    asyncio.run(planner.revise(task['id'],state['token'],'10/20 20:00 90',NOW))
    asyncio.run(planner.revise(task['id'],state['token'],'Yes',NOW))
    asyncio.run(planner.tick(NOW));confirmed=planner.get(task['id'])
    calendar.insert.assert_not_awaited()
    reply=asyncio.run(planner.callback('approve',task['id'],confirmed['token'],confirmed['message_id']))
    assert 'scheduled' in reply
    assert planner.get(task['id'])['status']=='scheduled'
    calendar.insert.assert_awaited_once()

@pytest.mark.parametrize('instruction',['改成今天晚上（允許超過九點）','今晚','tonight'])
def test_tonight_permission_request_is_deterministic_and_still_needs_confirmation(setup,instruction):
    planner,task,calendar,_=setup
    evening=NOW.replace(hour=19,minute=38)
    asyncio.run(planner.tick(NOW));state=planner.get(task['id'])
    reply=asyncio.run(planner.revise(task['id'],state['token'],instruction,evening))
    assert 'Reply Yes or No' in reply
    pending=planner.get(task['id'])
    assert pending['status']=='editing'
    assert pending['pending_late']['instruction']=='2026-10-19 20:00 90'
    assert not pending.get('late_window')
    calendar.insert.assert_not_awaited()

def test_partial_pages_close_task_and_estimate_successor_remaining(setup):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task['id'])
    planner.update(task['id'], lambda old: {**old, 'status': 'awaiting_feedback'})
    reply = planner.partial_reply(task['id'], state['token'], '135 | 20/48 | 21')
    assert 'Recorded' in reply
    saved = planner.get(task['id'])
    assert saved['feedback']['actual_minutes'] == 135
    assert saved['progress']['next_page'] == 21
    assert saved['requested_minutes'] == 180
    assert saved['status'] == 'finished'
    assert planner.get(saved['successor_task_id'])['status'] == 'queued'
    assert 'no longer open' in planner.partial_reply(task['id'], state['token'], '135 | 20/48 | 21')
    calendar.insert.assert_not_awaited()


def test_earlier_revises_without_calendar_write(setup):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    planner.update(task['id'], lambda old: {**old,
        'start': (NOW + timedelta(days=2)).isoformat(),
        'end': (NOW + timedelta(days=2, minutes=90)).isoformat()})
    state = planner.get(task['id'])
    reply = asyncio.run(planner.callback('earlier', task['id'], state['token'], 50))
    assert 'Updated' in reply
    assert planner.get(task['id'])['start'] < state['start']
    calendar.insert.assert_not_awaited()


def reflow_calendar(planner, calendar):
    from copy import deepcopy
    start = datetime.fromisoformat('2026-10-18T13:00:00+08:00')
    def event(key, title, begin, end, **extra):
        return {'id': key, 'etag': 'v1', 'summary': title, '_calendar_id': 'primary',
            'creator': {'email': 'owner@example.com'}, 'organizer': {'self': True},
            'start': {'dateTime': begin.isoformat()}, 'end': {'dateTime': end.isoformat()}, **extra}
    data = {'personal': event('personal', 'Personal reading', start, start + timedelta(hours=2)),
        'fixed': event('fixed', 'Shared meeting', start + timedelta(hours=2), start + timedelta(hours=5),
            attendees=[{'email': 'guest@example.com'}])}
    async def agenda(begin, end):
        return [deepcopy(e) for e in data.values()]
    async def fetch(key):
        return deepcopy(data.get(key))
    async def move(key, body, etag):
        assert data[key]['etag'] == etag
        data[key].update(deepcopy(body))
        data[key]['etag'] = 'v2'
        return deepcopy(data[key])
    async def busy(begin, end):
        from chronos.study_reflow import bounds
        return [bounds(e, planner.settings.tz) for e in data.values()
            if bounds(e, planner.settings.tz)[0] < end and bounds(e, planner.settings.tz)[1] > begin]
    calendar.agenda.side_effect = agenda
    calendar.event.side_effect = fetch
    calendar.move_event.side_effect = move
    calendar.busy.side_effect = busy
    return data, start - timedelta(minutes=30)


def test_earlier_personal_moves_are_previewed_then_applied(setup):
    from chronos.study_reflow import apply
    from chronos.task_actions import task_fields
    planner, task, calendar, _ = setup
    data, now = reflow_calendar(planner, calendar)
    state = {'token': 'a' * 32, 'status': 'proposed', 'snapshot': task_fields(task),
        'minutes': 90, 'start': now.replace(hour=18, minute=0).isoformat(),
        'end': now.replace(hour=19, minute=30).isoformat(), 'message_id': 50}
    planner.update(task['id'], lambda old: state)
    reply = asyncio.run(planner.suggest_revision(task, state, 'earlier', now))
    assert 'Updated' in reply
    revised = planner.get(task['id'])
    assert revised['start'] == now.replace(hour=13, minute=0).isoformat()
    assert revised['calendar_moves'][0]['event_id'] == 'personal'
    text, _ = planner.proposal_content(task, revised)
    assert 'Schedule changes requiring approval' in text and 'Personal reading' in text
    calendar.move_event.assert_not_awaited()
    asyncio.run(apply(planner, task, revised, now))
    assert data['personal']['start']['dateTime'] == now.replace(hour=18, minute=0).isoformat()
    assert data['fixed']['etag'] == 'v1'
    asyncio.run(apply(planner, task, revised, now))
    assert calendar.move_event.await_count == 1


def test_changed_personal_event_invalidates_reflow_before_writes(setup):
    from chronos.study_reflow import preview, apply
    planner, task, calendar, _ = setup
    data, now = reflow_calendar(planner, calendar)
    state = {'token': 'a' * 32, 'minutes': 90, 'start': now.replace(hour=18, minute=0).isoformat()}
    slot, moves = asyncio.run(preview(planner, task, state, now))
    state.update(start=slot[0].isoformat(), end=slot[1].isoformat(), calendar_moves=moves)
    data['personal']['etag'] = 'externally-edited'
    with pytest.raises(CalendarError, match='changed'):
        asyncio.run(apply(planner, task, state, now))
    calendar.move_event.assert_not_awaited()


def test_shared_recurring_and_all_day_events_are_fixed(setup):
    from chronos.study_reflow import movable
    planner, _, calendar, _ = setup
    data, now = reflow_calendar(planner, calendar)
    assert movable(data['personal'], planner.settings, now)
    assert not movable(data['fixed'], planner.settings, now)
    assert not movable({**data['personal'], 'recurringEventId': 'series'}, planner.settings, now)
    assert not movable({**data['personal'], '_calendar_id': 'secondary'}, planner.settings, now)
    assert not movable({**data['personal'], 'summary': 'Lecture'}, planner.settings, now)
    assert not movable({**data['personal'], 'start': {'date': '2026-10-18'},
        'end': {'date': '2026-10-19'}}, planner.settings, now)


def test_calendar_move_sends_conditional_version_header():
    import httpx
    config = Settings(_env_file=None, calendar_account='owner@example.com')
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(412, json={'error': 'version changed'})
    calendar = GoogleCalendar(config, transport=httpx.MockTransport(handler))
    calendar.token = AsyncMock(return_value='test-token')
    with pytest.raises(CalendarError, match='HTTP 412'):
        asyncio.run(calendar.move_event('personal', {'start': {'dateTime': NOW.isoformat()}}, 'v1'))
    assert seen[0].headers['If-Match'] == 'v1'
    assert seen[0].method == 'PATCH'
    assert seen[0].url.params['sendUpdates'] == 'none'


def test_tracked_study_move_reconciles_budget_after_uncertain_write(setup):
    from chronos.study_reflow import preview, apply, reconcile
    planner, task, calendar, _ = setup
    data, now = reflow_calendar(planner, calendar)
    other = planner.db.create_task('Other study', NOW + timedelta(days=3), None, NOW, {})
    linked = {'status': 'scheduled', 'token': 'b' * 32, 'event_id': 'personal', 'minutes': 120,
        'start': data['personal']['start']['dateTime'], 'end': data['personal']['end']['dateTime']}
    planner.update(other['id'], lambda old: linked)
    data['personal']['extendedProperties'] = {'private': {'chronosTask': str(other['id']), 'chronosToken': linked['token']}}
    day = now.date().isoformat()
    planner.db.mutate_task_action_state('learning:day:' + day, lambda old: {linked['token']: 120})
    state = {'token': 'a' * 32, 'minutes': 90, 'start': now.replace(hour=18, minute=0).isoformat()}
    slot, moves = asyncio.run(preview(planner, task, state, now))
    state.update(start=slot[0].isoformat(), end=slot[1].isoformat(), calendar_moves=moves)
    original_move = calendar.move_event.side_effect
    async def uncertain(*args):
        await original_move(*args)
        raise CalendarError('Ambiguous response')
    calendar.move_event.side_effect = uncertain
    with pytest.raises(CalendarError, match='Ambiguous'):
        asyncio.run(apply(planner, task, state, now))
    asyncio.run(reconcile(planner, state))
    assert planner.get(other['id'])['start'] == moves[0]['start']
    assert planner.db.get_task_action_state('learning:day:' + day)[linked['token']] == 120
    asyncio.run(apply(planner, task, state, now))
    assert calendar.move_event.await_count == 1


def test_partial_feedback_replies_stay_in_feedback_flow(system, setup, monkeypatch):
    client, tasks, bot, config = system
    _, _, calendar, _ = setup
    config.enable_learning_calendar = True
    config.learning_enabled_since = '2020-01-01T00:00:00+08:00'
    monkeypatch.setattr(main, 'google_calendar', calendar)
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 50}}
    task = tasks.create('Read chapter', NOW + timedelta(days=3))
    planner = main.learning_calendar()
    asyncio.run(planner.tick(NOW))
    state = planner.get(task['id'])
    planner.update(task['id'], lambda old: {**old, 'status': 'awaiting_feedback'})
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    payload = {'update_id': 99900, 'callback_query': {'id': 'partial',
        'data': f"learn:partial:{task['id']}:{state['token']}",
        'message': {'message_id': 50, 'chat': {'id': 123}}}}
    assert client.post('/telegram/webhook', json=payload, headers=headers).status_code == 200
    assert main.db.get_task_action_state('learning:edit-chat:123')['mode'] == 'feedback'
    for offset, text in enumerate(['invalid report', '135 | 20/48 | 21']):
        assert client.post('/telegram/webhook', headers=headers, json={
            'update_id': 99901 + offset, 'message': {'message_id': 99901 + offset,
                'chat': {'id': 123}, 'text': text}}).status_code == 200
        if offset == 0:
            assert main.db.get_task_action_state('learning:edit-chat:123')['mode'] == 'feedback'
    assert planner.get(task['id'])['progress']['next_page'] == 21
    assert not main.db.get_task_action_state('learning:edit-chat:123')
    assert len(tasks.list_open()) == 1
    main.ai.parse.assert_not_awaited()


@pytest.mark.parametrize('change', ['budget', 'deadline'])
def test_reflow_approval_rechecks_limits_and_task_deadline(setup, change):
    from chronos.study_reflow import preview, apply
    planner, task, calendar, _ = setup
    data, now = reflow_calendar(planner, calendar)
    other = planner.db.create_task('Other study', NOW + timedelta(days=3), None, NOW, {})
    linked = {'status': 'scheduled', 'token': 'b' * 32, 'event_id': 'personal', 'minutes': 120,
        'start': data['personal']['start']['dateTime'], 'end': data['personal']['end']['dateTime']}
    planner.update(other['id'], lambda old: linked)
    data['personal']['extendedProperties'] = {'private': {'chronosTask': str(other['id']), 'chronosToken': linked['token']}}
    state = {'token': 'a' * 32, 'minutes': 90, 'start': now.replace(hour=18, minute=0).isoformat()}
    slot, moves = asyncio.run(preview(planner, task, state, now))
    state.update(start=slot[0].isoformat(), end=slot[1].isoformat(), calendar_moves=moves)
    if change == 'budget':
        planner.db.mutate_task_action_state('learning:day:' + now.date().isoformat(), lambda old: {'other-session': 500})
    else:
        planner.db.edit_task_with_undo(other['id'], 'Other study', now, None, {})
    with pytest.raises(CalendarError):
        asyncio.run(apply(planner, task, state, now))
    calendar.move_event.assert_not_awaited()


def test_earlier_is_available_from_existing_edit_cards(setup):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    planner.update(task['id'], lambda old: {**old, 'status': 'editing', 'edit_message_id': 60,
        'start': (NOW + timedelta(days=2)).isoformat(),
        'end': (NOW + timedelta(days=2, minutes=90)).isoformat()})
    state = planner.get(task['id'])
    menu = planner.edit_menu(task['id'], state)
    assert any(row[0]['text'] == 'Earlier' for row in menu['messages'][0]['reply_markup']['inline_keyboard'])
    reply = asyncio.run(planner.callback('earlier', task['id'], state['token'], 60))
    assert 'Updated' in reply
    calendar.insert.assert_not_awaited()


def test_booked_edit_bypasses_other_proposal_and_preserves_progress(setup):
    from types import SimpleNamespace
    from chronos.task_actions import task_fields
    planner, task, calendar, _ = setup
    old_start = NOW.replace(hour=14)
    state = {'status': 'scheduled', 'token': 'booked', 'event_id': 'existing',
             'start': old_start.isoformat(), 'end': (old_start + timedelta(minutes=60)).isoformat(),
             'minutes': 60, 'snapshot': task_fields(task)}
    planner.update(task['id'], lambda old: state)
    planner.db.mutate_task_action_state('learning:day:2026-10-19', lambda old: {'booked': 60})
    other = planner.db.create_task('Other proposal', None, None, NOW)
    planner.update(other['id'], lambda old: {'status': 'proposed', 'token': 'other'})
    event = {'id': 'existing', 'etag': 'v1', '_calendar_id': planner.settings.calendar_id,
             'start': {'dateTime': state['start']}, 'end': {'dateTime': state['end']},
             'extendedProperties': {'private': {'chronosTask': str(task['id']), 'chronosToken': 'booked'}}}
    calendar.event.return_value = event
    calendar.agenda.return_value = [event]
    planner.ai = SimpleNamespace(parse_schedule_edit=AsyncMock(return_value=SimpleNamespace(
        actionable=True, timing='exact', start='2026-10-19T14:30:00+08:00', minutes=30,
        progress_note='Partly completed')))
    async def move(event_id, body, etag):
        assert event_id == event['id'] and etag == event['etag']
        event.update(body)
        event['etag'] = 'v2'
        return event
    calendar.move_event.side_effect = move
    reply = asyncio.run(planner.revise(task['id'], 'booked', '今天兩點半 已完成部分', NOW))
    assert 'updated' in reply
    assert planner.get(task['id'])['progress_note'] == 'Partly completed'
    assert planner.get(task['id'])['status'] == 'scheduled'
    assert planner.get(other['id'])['status'] == 'proposed'
    assert planner.db.get_task_action_state('learning:day:2026-10-19') == {'booked': 30}
    calendar.insert.assert_not_awaited()
    calendar.move_event.assert_awaited_once()
    # A separate event overlapping the desired time must still block the move.
    calendar.move_event.reset_mock()
    changed = planner.get(task['id'])
    event['start']['dateTime'], event['end']['dateTime'] = changed['start'], changed['end']
    calendar.agenda.return_value = [event, {**event, 'id': 'fixed'}]
    reply = asyncio.run(planner.revise(task['id'], 'booked', '10/19 14:30 30', NOW))
    assert 'conflicts' in reply
    calendar.move_event.assert_not_awaited()


def test_booked_edit_recovers_uncertain_calendar_write(setup):
    from chronos.task_actions import task_fields
    planner, task, calendar, _ = setup
    start = NOW.replace(hour=14)
    state = {'status': 'scheduled', 'token': 'booked', 'event_id': 'existing',
             'start': start.isoformat(), 'end': (start + timedelta(minutes=60)).isoformat(),
             'minutes': 60, 'snapshot': task_fields(task)}
    planner.update(task['id'], lambda old: state)
    event = {'id': 'existing', 'etag': 'v1', '_calendar_id': planner.settings.calendar_id,
             'start': {'dateTime': state['start']}, 'end': {'dateTime': state['end']},
             'extendedProperties': {'private': {'chronosTask': str(task['id']), 'chronosToken': 'booked'}}}
    calendar.event.return_value = event
    calendar.agenda.return_value = [event]
    async def uncertain(event_id, body, etag):
        event.update(body)
        event['etag'] = 'v2'
        raise CalendarError('Uncertain write')
    calendar.move_event.side_effect = uncertain
    with pytest.raises(CalendarError):
        asyncio.run(planner.revise(task['id'], 'booked', '10/19 14:30 30', NOW))
    assert planner.get(task['id']).get('pending_reschedule')
    reply = asyncio.run(planner.revise(task['id'], 'booked', '10/19 14:30 30', NOW))
    assert 'recovered' in reply
    assert planner.get(task['id'])['start'] == '2026-10-19T14:30:00+08:00'
    assert 'pending_reschedule' not in planner.get(task['id'])
    calendar.move_event.assert_awaited_once()


def test_explicit_plan_prioritizes_requested_task_and_invalidates_old_card(setup):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    old = planner.get(task["id"])
    other = planner.db.create_task("Requested next", NOW + timedelta(days=3), "general", NOW)
    reply = planner.request_proposal(other["id"])
    assert f"#{other['id']} is next" in reply
    assert planner.get(task["id"])["status"] == "queued"
    assert planner.get(task["id"])["token"] != old["token"]
    asyncio.run(planner.tick(NOW))
    assert planner.get(other["id"])["status"] == "proposed"
    calendar.insert.assert_not_awaited()


def test_explicit_plan_preserves_booking_in_progress(setup):
    planner, task, calendar, _ = setup
    planner.update(task["id"], lambda old: {"status": "creating", "token": "locked"})
    planner.db.mutate_task_action_state("learning:proposal-queue", lambda old: {"active_task_id": task["id"]})
    assert "already scheduled" in planner.request_proposal(task["id"])
    assert planner.get(task["id"])["token"] == "locked"
    calendar.insert.assert_not_awaited()


@pytest.mark.parametrize("action", ["later", "longer", "shorter"])
def test_direct_proposal_adjustments_require_fresh_approval(setup, action):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    old = planner.get(task["id"])
    reply = asyncio.run(planner.callback(action, task["id"], old["token"], 50))
    current = planner.get(task["id"])
    assert reply.startswith("Updated")
    assert current["token"] != old["token"]
    if action == "later":
        assert datetime.fromisoformat(current["start"]) > datetime.fromisoformat(old["start"])
        assert current["minutes"] == old["minutes"]
    else:
        expected = old["minutes"] + 30 if action == "longer" else 70
        assert current["minutes"] == expected
    calendar.insert.assert_not_awaited()


@pytest.mark.parametrize("finish", ["2026-10-19T21:00:00+08:00", "2026-10-19T23:30:00+08:00", "2026-10-20T00:30:00+08:00"])
def test_checkins_run_after_cutoff_without_new_proposals(setup, finish):
    planner, task, calendar, bot = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task['id'])
    asyncio.run(planner.callback('approve', task['id'], state['token'], 50))
    end = datetime.fromisoformat(finish)
    calendar.event.return_value = {'end': {'dateTime': finish},
        'extendedProperties': {'private': {'chronosToken': state['token']}}}
    # The live Calendar end can extend past the originally booked end.
    asyncio.run(planner.tick(end - timedelta(minutes=1)))
    assert planner.get(task['id'])['status'] == 'scheduled'
    queued = planner.db.create_task('Next study block', None, None, NOW)
    asyncio.run(planner.tick(end))
    asyncio.run(planner.tick(end + timedelta(minutes=5)))
    assert planner.get(task['id'])['status'] == 'awaiting_feedback'
    assert bot.send_message.await_count == 2
    assert planner.get(queued['id']) is None
    calendar.insert.assert_awaited_once()


@pytest.mark.parametrize('free_minutes,visible', [(89, False), (90, True), (91, True)])
def test_date_options_require_continuous_duration(setup, free_minutes, visible):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task['id'])
    state.update(minutes=90)
    day = (NOW + timedelta(days=1)).replace(hour=9)
    # Remove class load to isolate the exact-duration boundary.
    planner.db.mutate_task_action_state('learning:course-calendar-policy', lambda old: {
        'first_day': '2026-01-01', 'last_regular_day': '2026-12-31',
        'excluded_dates': [(NOW + timedelta(days=i)).date().isoformat() for i in range(1, 4)]})
    calendar.busy.return_value = [(day + timedelta(minutes=free_minutes), day.replace(hour=23))]
    menu = asyncio.run(planner.dated_edit_menu(task['id'], state, NOW))
    labels = [row[0]['text'] for row in menu['messages'][0]['reply_markup']['inline_keyboard']]
    assert ('10/20 (Tue)' in labels) is visible
    calendar.insert.assert_not_awaited()


def test_date_callback_rechecks_busy_calendar_without_booking(setup):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    state = planner.update(task['id'], lambda old: {**old, 'status': 'editing', 'edit_message_id': 60})
    tomorrow = (NOW + timedelta(days=1)).replace(hour=0)
    calendar.busy.return_value = [(tomorrow, tomorrow + timedelta(days=1))]
    reply = asyncio.run(planner.callback('day20261020', task['id'], state['token'], 60))
    assert 'No continuous' in reply
    assert planner.get(task['id'])['token'] == state['token']
    calendar.insert.assert_not_awaited()


def test_updated_notice_is_deleted_after_seven_seconds(system, monkeypatch):
    _, service, telegram, _ = system
    monkeypatch.setattr(main, 'telegram', telegram)
    pause = AsyncMock()
    monkeypatch.setattr(main.asyncio, 'sleep', pause)
    telegram.send_message.return_value = {'ok': True, 'result': {'message_id': 987}}
    telegram.request.return_value = {'ok': True}
    receipt = {'delivered': False, 'reply': 'Updated. Review the new execution plan and press Confirm schedule to book it.'}
    asyncio.run(main.deliver_learning_reply(123, 1234567, receipt))
    pause.assert_awaited_once_with(7)
    telegram.request.assert_awaited_with('deleteMessage', {'chat_id': 123, 'message_id': 987})


def test_owner_task_effort_drives_proposal_without_ai_estimate(setup):
    planner, task, calendar, _ = setup
    from chronos.tasks import TaskService
    TaskService(planner.db, planner.settings.tz).edit(task['id'], task['title'], datetime.fromisoformat(task['due_at']), task['project'],
                           {"estimated_minutes": 120})
    planner.ai = AsyncMock()
    asyncio.run(planner.tick(NOW))
    state = planner.get(task['id'])
    assert state['minutes'] == 120 and state['duration_source'] == 'owner'
    planner.ai.estimate_duration.assert_not_awaited()
    calendar.insert.assert_not_awaited()


@pytest.mark.parametrize('finish', ['approve', 'postpone', 'reject'])
def test_explicit_batch_continues_after_quiet_hours_without_forcing_tick(setup, finish):
    planner, task, calendar, bot = setup
    late = NOW.replace(hour=21, minute=25)
    other = planner.db.create_task('Next requested chapter', NOW + timedelta(days=3), None, NOW)
    unrelated = planner.db.create_task('Automatic backlog', None, None, NOW)
    planner.update(unrelated['id'], lambda old: {'status': 'queued', 'requested_minutes': 30})
    ids = [task['id'], other['id']]
    planner.request_proposals(ids, minutes=90, immediate=True)
    asyncio.run(planner.tick(late, force_proposal=True))
    first = planner.get(task['id'])
    assert first['status'] == 'proposed'
    asyncio.run(planner.callback(finish, task['id'], first['token'], 50))
    # New service instance models process restart / the next scheduler invocation.
    planner = LearningCalendar(planner.db, calendar, bot, planner.settings)
    asyncio.run(planner.tick(late))
    state = planner.get(other['id'])
    assert state['status'] == 'proposed' and state['delivery'] == 'proposal_sent'
    assert state['minutes'] == 90 and state['duration_source'] == 'owner'
    assert datetime.fromisoformat(state['start']).date() > late.date()
    assert calendar.insert.await_count == int(finish == 'approve')
    # Repeated ticks do not replay an already-delivered preview or book it.
    count = bot.send_message.await_count
    asyncio.run(planner.tick(late))
    assert bot.send_message.await_count == count
    asyncio.run(planner.callback('reject', other['id'], state['token'], 50))
    result = asyncio.run(planner.tick(late))
    assert result['quiet_hours'] is True
    assert planner.get(unrelated['id'])['status'] == 'queued'
    assert calendar.insert.await_count == int(finish == 'approve')


def test_quiet_hours_preserve_automatic_backlog_without_owner_request(setup):
    planner, task, calendar, bot = setup
    planner.update(task['id'], lambda old: {'status': 'queued', 'requested_minutes': 90})
    result = asyncio.run(planner.tick(NOW.replace(hour=21, minute=25)))
    assert result['quiet_hours'] is True
    assert planner.get(task['id'])['status'] == 'queued'
    bot.send_message.assert_not_awaited()
    calendar.insert.assert_not_awaited()



def test_quiet_batch_recovers_rejected_delivery_without_regenerating_plan(setup):
    planner, task, calendar, bot = setup
    late = NOW.replace(hour=22)
    planner.request_proposals([task['id']], minutes=90, immediate=True)
    bot.send_message.return_value = {'ok': False}
    with pytest.raises(CalendarError, match='will be retried'):
        asyncio.run(planner.tick(late))
    pending = planner.get(task['id'])
    assert pending['status'] == 'proposed' and pending['delivery'] == 'proposal_pending'
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 50}}
    planner = LearningCalendar(planner.db, calendar, bot, planner.settings)
    asyncio.run(planner.tick(late))
    sent = planner.get(task['id'])
    assert sent['token'] == pending['token'] and sent['start'] == pending['start']
    assert sent['delivery'] == 'proposal_sent'
    calendar.insert.assert_not_awaited()


def test_replacing_explicit_batch_does_not_resume_old_members_at_night(setup):
    planner, task, calendar, bot = setup
    late = NOW.replace(hour=22)
    other = planner.db.create_task('Replacement request', None, None, NOW)
    planner.request_proposals([task['id']], minutes=90, immediate=True)
    planner.request_proposals([other['id']], minutes=30, immediate=True)
    asyncio.run(planner.tick(late))
    state = planner.get(other['id'])
    assert state['status'] == 'proposed' and state['minutes'] == 30
    assert planner.get(task['id'])['status'] == 'queued'
    asyncio.run(planner.callback('reject', other['id'], state['token'], 50))
    result = asyncio.run(planner.tick(late))
    assert result['quiet_hours'] is True
    assert planner.get(task['id'])['status'] == 'queued'
    calendar.insert.assert_not_awaited()


def test_compact_plan_overview_and_pagination_are_read_only(setup):
    from chronos.plan_overview import view
    planner, task, calendar, bot = setup
    states = ['needs_replan', 'queued', 'queued', 'queued', 'queued',
              'postponed', 'scheduled', 'proposed', 'editing', 'awaiting_feedback']
    records = [task] + [planner.db.create_task(f'Work <{i}>', None, None, NOW)
                        for i in range(len(states) - 1)]
    for record, status in zip(records, states):
        planner.update(record['id'], lambda old, status=status: {'status': status})
    before = [planner.get(record['id']) for record in records]
    summary = view(planner)
    assert '5 to arrange · 1 deferred · 1 scheduled' in summary['text']
    assert '3 awaiting your response' in summary['text']
    assert 'Commands' not in summary['text'] and '/planedit' not in summary['text']
    choices = [b for row in summary['reply_markup']['inline_keyboard'] for b in row
               if b['callback_data'].startswith('task:plan:')]
    assert len(choices) == 2
    assert {b['callback_data'] for b in choices} == {
        f"task:plan:{record['id']}" for record in records[:2]}
    first, last = view(planner, 'all'), view(planner, 'all', 999999)
    assert first['text'].count('<b>') == 7
    assert last['text'].count('<b>') == 5
    assert 'Work &lt;' in first['text']
    assert 'Awaiting approval' in last['text']
    assert 'Scheduled' in view(planner, 'scheduled')['text']
    assert [planner.get(record['id']) for record in records] == before
    calendar.insert.assert_not_awaited()
    bot.send_message.assert_not_awaited()


def test_plan_navigation_uses_saved_receipts_without_generation(system, monkeypatch):
    client, service, bot, config = system
    config.enable_learning_calendar = True
    task = service.create('Arrange this')
    planner = main.learning_calendar()
    planner.update(task['id'], lambda old: {'status': 'queued'})
    monkeypatch.setattr(main, 'learning_calendar', lambda: planner)
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    payload = {'update_id': 998991, 'message': {'message_id': 70,
               'chat': {'id': 123}, 'text': '/plan'}}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert bot.send_message.await_count == 1
    assert 'to arrange' in bot.send_message.await_args.args[1]
    navigation = {'update_id': 998992, 'callback_query': {'id': 'overview',
                  'data': 'planview:all:0', 'from': {'id': 123},
                  'message': {'message_id': 71, 'chat': {'id': 123}}}}
    assert client.post('/telegram/webhook', headers=headers, json=navigation).status_code == 200
    assert client.post('/telegram/webhook', headers=headers, json=navigation).status_code == 200
    assert bot.send_message.await_count == 2
    assert 'Arrange this' in bot.send_message.await_args.args[1]
    assert planner.get(task['id']) == {'status': 'queued'}
    main.ai.estimate_duration.assert_not_awaited()
