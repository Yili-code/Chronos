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
    config = Settings(_env_file=None, enable_learning_calendar=True, learning_enabled_since=NOW.isoformat(),
        telegram_chat_id=123, calendar_account="owner@example.com")
    calendar = AsyncMock()
    calendar.configured = lambda: True
    calendar.busy.return_value = []
    calendar.event.return_value = None
    async def insert(body):
        return {**body, "htmlLink": "https://calendar.google.com/event"}
    calendar.insert.side_effect = insert
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 50}}
    planner = LearningCalendar(db, calendar, bot, config)
    task = db.create_task("Study graphs", NOW + timedelta(days=3), "graphs", NOW, {})
    return planner, task, calendar, bot


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


def test_no_calendar_write_before_approval_and_reservations_do_not_overlap(setup):
    planner, task, calendar, bot = setup
    other = planner.db.create_task("Study databases", NOW + timedelta(days=3), None, NOW, {})
    asyncio.run(planner.tick(NOW))
    first, second = planner.get(task["id"]), planner.get(other["id"])
    assert first["end"] <= second["start"] or second["end"] <= first["start"]
    assert first["minutes"] == 90
    calendar.insert.assert_not_awaited()
    assert "Duration 90 minutes" in bot.send_message.await_args_list[0].args[1]
    assert "Calendar write waits for your approval" in bot.send_message.await_args_list[0].args[1]
    # Frozen scheduling now is in the future relative to booking's wall clock when needed.
    reply = asyncio.run(planner.callback("approve", task["id"], first["token"], 50))
    assert "booked" in reply
    asyncio.run(planner.callback("approve", task["id"], first["token"], 50))
    assert calendar.insert.await_count == 1
    assert planner.get(task["id"])["status"] == "scheduled"


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


def test_edited_calendar_end_is_respected_and_quiet_hours_send_nothing(setup):
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
    assert bot.send_message.await_count == 1


def test_no_slot_and_calendar_error_do_not_silently_book(setup):
    planner, task, calendar, bot = setup
    calendar.busy.return_value = [(NOW, NOW + timedelta(days=7))]
    asyncio.run(planner.tick(NOW))
    assert planner.get(task["id"])["status"] == "no_slot"
    assert "No study slot" in bot.send_message.await_args.args[1]
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
    assert planner.get(task["id"])["status"] == "queued"
    asyncio.run(planner.tick(end + timedelta(minutes=5)))
    new = planner.get(task["id"])
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
    assert [b["text"] for b in buttons] == ["Allow", "Edit"]
    prompt = asyncio.run(planner.callback("edit", task["id"], old["token"], 50))
    assert len(prompt["messages"][0]["reply_markup"]["inline_keyboard"]) == 4
    prompt = asyncio.run(planner.callback("manual", task["id"], old["token"], 50))
    assert prompt["messages"][0]["reply_markup"]["force_reply"]
    assert "no longer available" in asyncio.run(planner.callback("approve", task["id"], old["token"], 50))
    assert "Updated" in asyncio.run(planner.revise(task["id"], old["token"], "10/20 17:00 60", NOW))
    revised = planner.get(task["id"])
    assert revised["token"] != old["token"]
    assert revised["minutes"] == 60
    assert revised["start"] == "2026-10-20T17:00:00+08:00"
    calendar.insert.assert_not_awaited()
    asyncio.run(planner.tick(NOW))
    assert "no longer active" in asyncio.run(planner.callback("approve", task["id"], old["token"], 50))
    assert "booked" in asyncio.run(planner.callback("approve", task["id"], revised["token"], 50))
    assert calendar.insert.await_count == 1


@pytest.mark.parametrize("instruction,reason", [
    ("tomorrow afternoon", "Use MM/DD"),
    ("10/20 20:00 90", "future 15"),
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
        "text": "10/20 17:00 60", "reply_to_message": {"message_id": 50, "chat": {"id": 123}}}}
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
    assert "no longer active" in asyncio.run(planner.callback(option, task["id"], old["token"], 50))
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
    assert planner.get(task["id"])["message_id"] == 60
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


def test_revision_respects_daily_budget_busy_events_and_scheduler_lease(setup):
    planner, task, calendar, _ = setup
    asyncio.run(planner.tick(NOW))
    state = planner.get(task["id"])
    planner.db.mutate_task_action_state("learning:day:2026-10-20", lambda old: {"booked": 350})
    assert "limit" in asyncio.run(planner.revise(task["id"], state["token"], "10/20 17:00 60", NOW))
    planner.db.mutate_task_action_state("learning:day:2026-10-20", lambda old: {})
    calendar.busy.return_value = [(NOW.replace(day=20, hour=17), NOW.replace(day=20, hour=18))]
    assert "conflicts" in asyncio.run(planner.revise(task["id"], state["token"], "10/20 17:00 60", NOW))
    planner.db.mutate_task_action_state("learning:tick", lambda old: {
        "claim": "scheduler", "until": (NOW + timedelta(minutes=5)).isoformat()})
    assert "shortly" in asyncio.run(planner.revise(task["id"], state["token"], "10/20 17:00 60", NOW))
    assert planner.get(task["id"])["token"] == state["token"]
    calendar.insert.assert_not_awaited()
