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
    bot.request.return_value = {"ok": True}
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


def test_only_one_proposal_is_shown_until_approval_succeeds(setup):
    planner, task, calendar, bot = setup
    other = planner.db.create_task("Study databases", NOW + timedelta(days=3), None, NOW, {})
    asyncio.run(planner.tick(NOW))
    first = planner.get(task["id"])
    assert planner.get(other["id"]) is None
    assert first["minutes"] == 90
    calendar.insert.assert_not_awaited()
    assert " · 90 minutes" in bot.send_message.await_args_list[0].args[1]
    assert "Calendar write waits for your approval" not in bot.send_message.await_args_list[0].args[1]
    # Frozen scheduling now is in the future relative to booking's wall clock when needed.
    reply = asyncio.run(planner.callback("approve", task["id"], first["token"], 50))
    assert "booked" in reply
    asyncio.run(planner.callback("approve", task["id"], first["token"], 50))
    assert calendar.insert.await_count == 1
    assert planner.get(task["id"])["status"] == "scheduled"
    calendar.busy.return_value = [(datetime.fromisoformat(first["start"]), datetime.fromisoformat(first["end"]))]
    asyncio.run(planner.tick(NOW))
    second = planner.get(other["id"])
    assert second["status"] == "proposed"
    assert first["end"] <= second["start"] or second["end"] <= first["start"]


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
    assert [b["text"] for b in buttons] == ["Allow", "Edit", "Postpone"]
    prompt = asyncio.run(planner.callback("edit", task["id"], old["token"], 50))
    assert len(prompt["messages"][0]["reply_markup"]["inline_keyboard"]) == 5
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
    assert "booked" in asyncio.run(planner.callback("approve", task["id"], revised["token"], 50))
    assert calendar.insert.await_count == 1


@pytest.mark.parametrize("instruction,reason", [
    ("some time maybe", "Use MM/DD"),
    ("10/20 20:00 90", "future 10"),
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
    assert len(menu["messages"][0]["reply_markup"]["inline_keyboard"]) == 3
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
    assert "No booked event was changed" in reply
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
    assert planner.get(task["id"])["status"] == "queued"
    assert "planning_status" not in tasks.get_open_by_id(task["id"])
    assert "queued" in asyncio.run(planner.callback("approve", task["id"], state["token"], 50))
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
    assert menu[0][0]["text"] == "A bit longer · 15 min"
    assert menu[1][0]["text"] == "Much longer · 20 min"
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
    assert 'new proposal' in reply
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
    assert 'AI estimate' in planner.proposal_content(task, state)[0]
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
    assert 'AI estimate unavailable' in planner.proposal_content(task, state)[0]
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
    assert f'<code>/studyfeedback {task["id"]} 15 done</code>' in text
    assert 'How did it go? Choose below.' in text
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
    assert '已完成' in reply
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
    assert '已變更' in asyncio.run(planner.revise(task['id'],state['token'],'已完成',NOW))
    assert planner.db.list_open_tasks()
    assert planner.get(task['id'])['token']==state['token']
