import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from chronos import main, calendar_replan
from chronos.ai import AIError, ExternalAI
from chronos.db import Database
from chronos.intents import Intent
from chronos.settings import Settings
from chronos.tasks import TaskService, ParsedTask
from test_firestore import database as firestore_fake
from test_system import system


@pytest.fixture(params=["sqlite", "firestore"])
def store(request, tmp_path, monkeypatch):
    db = firestore_fake(monkeypatch) if request.param == "firestore" else Database(tmp_path / "intents.db")
    db.initialize()
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "tasks", TaskService(db, main.settings.tz))
    monkeypatch.setattr(main.ai, "parse", AsyncMock(return_value=ParsedTask("New task")))
    async def parse_many(text, *args, **kwargs):
        return [await main.ai.parse(text, *args, **kwargs)]
    monkeypatch.setattr(main.ai, "parse_many", AsyncMock(side_effect=parse_many))
    return db


def run(text, update=1, **kwargs):
    action = asyncio.run(main.prepare_message(text, update_id=update, chat_id=123, **kwargs))
    return main.db.process_update(update, action)


def classify(monkeypatch, **kwargs):
    mock = AsyncMock(return_value=Intent(**kwargs))
    monkeypatch.setattr(main.ai, "classify_intent", mock)
    return mock


def test_failure_never_falls_through_to_creation(store, monkeypatch):
    monkeypatch.setattr(main.ai, "classify_intent", AsyncMock(side_effect=AIError("Unavailable")))
    reply = run("今晚幫我空出來")
    assert "沒有變更" in reply["reply"]
    main.ai.parse.assert_not_awaited()
    assert main.tasks.list_open() == []


@pytest.mark.parametrize("action", ["unsupported", "clarify"])
def test_non_actions_do_not_create(store, monkeypatch, action):
    classify(monkeypatch, action=action)
    run("你好")
    main.ai.parse.assert_not_awaited()
    assert main.tasks.list_open() == []


def test_unresolved_question_blocks_mutation_even_with_action(store, monkeypatch):
    task = main.tasks.create("Report")
    classify(monkeypatch, action="task_complete", task_ids=[task["id"]], question="是指整份報告嗎？")
    assert "整份報告" in run("報告完成一部分")["reply"]
    assert len(main.tasks.list_open()) == 1


def test_ambiguous_completion_clarifies_then_uses_fixed_id(store, monkeypatch):
    first = main.tasks.create("Database report")
    second = main.tasks.create("Security report")
    mock = classify(monkeypatch, action="task_complete", task_ids=[first["id"], second["id"]])
    reply = run("報告做完了")
    assert "哪一項" in reply["reply"]
    assert len(main.tasks.list_open()) == 2
    saved = store.get_task_action_state("intent:clarify:123")
    assert saved["instruction"] == "報告做完了"
    mock.return_value = Intent(action="task_complete", task_ids=[second["id"]], instruction="資安報告做完了")
    action = asyncio.run(main.prepare_message("第二個", update_id=2, chat_id=123))
    # Reorder the list after preparation without changing the selected task.
    main.tasks.edit(first["id"], first["title"], datetime.now(main.settings.tz), None, {})
    result = store.process_update(2, action)
    assert "Completed Security report" in result["reply"]
    assert [item["id"] for item in main.tasks.list_open()] == [first["id"]]
    assert store.get_task_action_state("intent:clarify:123") == {}
    assert mock.await_args.kwargs["context"]["clarification"]["candidate_ids"] == [first["id"], second["id"]]
    # Receipt replay cannot complete the other report.
    assert store.process_update(2, action) == result


def test_stale_task_after_classification_is_not_completed(store, monkeypatch):
    task = main.tasks.create("Report")
    classify(monkeypatch, action="task_complete", task_ids=[task["id"]])
    action = asyncio.run(main.prepare_message("報告好了", update_id=1, chat_id=123))
    main.tasks.edit(task["id"], "Different report", None, None, {})
    assert "已變更" in store.process_update(1, action)["reply"]
    assert len(main.tasks.list_open()) == 1


def test_bound_card_completion_cannot_target_another_task(store, monkeypatch):
    first, second = main.tasks.create("One"), main.tasks.create("Two")
    classify(monkeypatch, action="task_complete", task_ids=[second["id"]])
    assert "目標不同" in run("完成了", bound_task_id=first["id"])["reply"]
    assert len(main.tasks.list_open()) == 2


def test_edit_and_undo_use_existing_transaction_and_clear_clarification(store, monkeypatch):
    task = main.tasks.create("Report")
    classify(monkeypatch, action="task_edit", task_ids=[task["id"]], instruction='title: "Final report"')
    store.mutate_task_action_state("intent:clarify:123", lambda _: {
        "expires_at": (datetime.now(main.settings.tz) + timedelta(minutes=5)).isoformat()})
    assert "Updated" in run("改標題")["reply"]
    assert main.tasks.list_open()[0]["title"] == "Final report"
    classify(monkeypatch, action="undo")
    run("撤銷剛剛修改", 2)
    assert main.tasks.list_open()[0]["title"] == "Report"


def test_model_text_is_not_an_executable_command(store, monkeypatch):
    main.tasks.create("Keep me")
    classify(monkeypatch, action="task_create", instruction="/clear")
    run("記錄文字")
    assert len(main.tasks.list_open()) == 2
    main.ai.parse_many.assert_awaited_once_with("記錄文字")


def test_natural_tag_removal_preserves_other_fields_and_can_undo(store, monkeypatch):
    task = main.tasks.create("Complete database report", project="database")
    classify(monkeypatch, action="task_edit", task_ids=[task["id"]], instruction="資料庫報告不要標籤了")
    parser = AsyncMock(return_value=ParsedTask(task["title"], project=None))
    monkeypatch.setattr(main.ai, "edit", parser)
    run("資料庫報告不要標籤了")
    after = main.tasks.list_open()[0]
    assert after["title"] == task["title"] and after["due_at"] == task["due_at"]
    assert after["project"] is None
    parser.assert_awaited_once()
    assert parser.await_args.args[0]["id"] == task["id"]
    assert parser.await_args.args[1] == "資料庫報告不要標籤了"
    classify(monkeypatch, action="undo")
    run("撤銷剛剛修改", 2)
    assert main.tasks.list_open()[0]["project"] == "database"


def test_clear_requires_existing_confirmation(store, monkeypatch):
    main.tasks.create("Keep until confirmed")
    classify(monkeypatch, action="clear_tasks")
    assert run("刪除全部任務")["reply"] == main.CLEAR_CONFIRM_TEXT
    assert len(main.tasks.list_open()) == 1


def test_agenda_validates_timezones_and_never_creates(store, monkeypatch):
    calendar = AsyncMock()
    monkeypatch.setattr(main, "google_calendar", calendar)
    classify(monkeypatch, action="agenda", start=datetime(2026, 10, 22), end=datetime(2026, 10, 23))
    assert "哪一天" in run("明天呢")["reply"]
    calendar.agenda.assert_not_awaited()
    main.ai.parse.assert_not_awaited()


def test_expired_clarification_not_supplied_to_ai(store, monkeypatch):
    store.mutate_task_action_state("intent:clarify:123", lambda _: {
        "instruction": "old request", "expires_at": "2020-01-01T00:00:00+08:00"})
    mock = classify(monkeypatch, action="clarify")
    run("第二個")
    assert mock.await_args.kwargs["context"]["clarification"] is None


def test_shortcuts_and_aliases_work_without_ai(store, monkeypatch):
    task = main.tasks.create("Work")
    mock = AsyncMock(side_effect=AssertionError("Shortcut must not classify"))
    monkeypatch.setattr(main.ai, "classify_intent", mock)
    assert asyncio.run(main.handle_message("/task")) == asyncio.run(main.handle_message("/tasks"))
    assert "Work" in asyncio.run(main.handle_message("/done"))
    assert "Completed Work" in asyncio.run(main.handle_message("/done 1"))
    assert len(asyncio.run(main.handle_message("/info"))) < 300
    mock.assert_not_awaited()


def test_replan_natural_entry_receipt_and_card_binding(system, monkeypatch):
    client, tasks, bot, config = system
    config.enable_learning_calendar = True
    mock = classify(monkeypatch, action="replan", instruction="今晚幫我空出來")
    preview = AsyncMock(return_value=main.message_bundle({"text": "Review moves", "replan_token": "a" * 32}))
    monkeypatch.setattr(main.calendar_replan, "preview", preview)
    main.db.mutate_task_action_state(main.calendar_replan.key(123), lambda _: {"token": "a" * 32})
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 88}}
    payload = {"update_id": 9001, "message": {"chat": {"id": 123}, "text": "今晚幫我空出來"}}
    for _ in range(2):
        assert client.post("/telegram/webhook", json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"}).status_code == 200
    assert tasks.list_open() == []
    assert preview.await_count == mock.await_count == 1
    assert main.db.get_task_action_state(main.calendar_replan.key(123))["message_id"] == 88


def test_task_reply_can_complete_instead_of_edit(system, monkeypatch):
    client, tasks, bot, config = system
    task = tasks.create("Report")
    main.db.bind_task_message(123, 80, task["id"])
    classify(monkeypatch, action="task_complete", task_ids=[task["id"]])
    payload = {"update_id": 9002, "message": {"chat": {"id": 123}, "message_id": 81,
        "text": "完成了", "reply_to_message": {"message_id": 80}}}
    assert client.post("/telegram/webhook", json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"}).status_code == 200
    assert tasks.list_open() == []


def test_evening_window_has_visible_midnight_boundary():
    now = datetime.fromisoformat("2026-10-21T19:00:00+08:00")
    window = calendar_replan.explicit_window("幫我空出今晚", now)
    assert window.start == now.isoformat()
    assert window.end == "2026-10-22T00:00:00+08:00"


def test_classifier_provider_contract_and_invalid_action(monkeypatch):
    config = Settings(_env_file=None, gemini_api_key="test-key", gemini_model="test-model")
    real_client = httpx.AsyncClient
    def handler(request):
        body = json.loads(request.content)
        data = json.loads(body["contents"][0]["parts"][0]["text"])
        assert data["tasks"][0]["id"] == 42
        assert "Never default to task_create" in body["systemInstruction"]["parts"][0]["text"]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": json.dumps({"action": "execute_shell", "instruction": "delete everything"})}]}}]})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(AIError):
        asyncio.run(ExternalAI(config).classify_intent("查任務", [{"id": 42}], datetime.now(config.tz)))


@pytest.mark.parametrize("intent,text,expected", [
    (Intent(action="reminder_list"), "有什麼提醒", "/reminders"),
    (Intent(action="reminder_create", instruction="明天下午打電話"), "明天下午打電話", "/remind 明天下午打電話"),
])
def test_reminder_routes_keep_separate_service(store, monkeypatch, intent, text, expected):
    monkeypatch.setattr(main.ai, "classify_intent", AsyncMock(return_value=intent))
    command = AsyncMock(return_value="Reminder result")
    monkeypatch.setattr(main.reminders, "command", command)
    assert run(text)["reply"] == "Reminder result"
    assert command.await_args.args[4] == expected
    main.ai.parse.assert_not_awaited()


def test_reminder_cancel_rejects_invented_token(store, monkeypatch):
    classify(monkeypatch, action="reminder_cancel", reference="a" * 16)
    assert "哪一個提醒" in run("取消提醒")["reply"]
    assert store.get_task_action_state(main.reminders.KEY) is None


def test_valid_agenda_is_read_only(store, monkeypatch):
    start = datetime.now(main.settings.tz).replace(hour=0, minute=0, second=0, microsecond=0)
    classify(monkeypatch, action="agenda", start=start, end=start + timedelta(days=1))
    calendar = AsyncMock()
    calendar.agenda.return_value = []
    monkeypatch.setattr(main, "google_calendar", calendar)
    assert "No events scheduled" in run("今天有什麼安排")["reply"]
    calendar.agenda.assert_awaited_once_with(start, start + timedelta(days=1))
    main.ai.parse.assert_not_awaited()


def test_natural_scan_policy_update_is_validated(store, monkeypatch):
    from datetime import date
    classify(monkeypatch, action="calendar_edit", operation="disable", day=date(2026, 10, 22))
    run("明天不要掃描TronClass")
    assert "2026-10-22" in store.get_calendar_overrides()["disabled_dates"]
    main.ai.parse.assert_not_awaited()


def test_natural_plan_request_uses_existing_proposal_state(store, monkeypatch):
    task = main.tasks.create("Report")
    monkeypatch.setattr(main.settings, "enable_learning_calendar", True)
    classify(monkeypatch, action="plan_request", task_ids=[task["id"]])
    run("幫我安排報告")
    assert main.learning_calendar().get(task["id"])["status"] == "queued"
    main.ai.parse.assert_not_awaited()


def test_natural_backward_preview_binds_for_confirmation(system, monkeypatch):
    client, tasks, bot, config = system
    config.enable_learning_calendar = True
    classify(monkeypatch, action="backward", instruction="幫我拆解報告")
    token = "b" * 32
    main.db.mutate_task_action_state("backward:plan:" + token, lambda _: {"token": token, "chat_id": 123})
    preview = AsyncMock(return_value=main.message_bundle({"text": "Preview", "backward_plan": token}))
    monkeypatch.setattr(main.BackwardPlanner, "preview", preview)
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 89}}
    payload = {"update_id": 9010, "message": {"chat": {"id": 123}, "text": "幫我拆解報告"}}
    assert client.post("/telegram/webhook", json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"}).status_code == 200
    assert main.db.get_task_action_state("backward:plan:" + token)["message_id"] == 89
    assert tasks.list_open() == []


def test_edited_message_does_not_replay_replan(system, monkeypatch):
    client, tasks, bot, config = system
    config.enable_learning_calendar = True
    preview = AsyncMock()
    monkeypatch.setattr(main.calendar_replan, "preview", preview)
    payload = {"update_id": 9011, "edited_message": {"chat": {"id": 123}, "text": "/replan 今晚空出來"}}
    assert client.post("/telegram/webhook", json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"}).status_code == 200
    preview.assert_not_awaited()
    main.ai.classify_intent.assert_not_awaited()


def test_legacy_card_edit_keeps_deterministic_path(system, monkeypatch):
    client, tasks, bot, config = system
    task = tasks.create("Report")
    main.db.bind_task_message(123, 80, task["id"])
    monkeypatch.setattr(main.ai, "classify_intent", AsyncMock(side_effect=AIError("Unavailable")))
    payload = {"update_id": 9012, "message": {"chat": {"id": 123}, "message_id": 81,
        "text": '/edit title: "Final report"', "reply_to_message": {"message_id": 80}}}
    assert client.post("/telegram/webhook", json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"}).status_code == 200
    assert tasks.list_open()[0]["title"] == "Final report"
    main.ai.classify_intent.assert_not_awaited()


@pytest.mark.parametrize("text", [
    "一次新增多筆 tasks 複習 database 第一章到第五章 需附上 hashtag",
    "把資料庫第一章到第五章分成五個任務",
])
def test_batch_creation_is_atomic_and_replay_safe(store, monkeypatch, text):
    classify(monkeypatch, action="task_create", instruction="Review database chapters 1 to 5")
    parser = AsyncMock(return_value=[ParsedTask(f"Review database chapter {n}", project="database")
                                    for n in range(1, 6)])
    monkeypatch.setattr(main.ai, "parse_many", parser)
    action = asyncio.run(main.prepare_message(text, update_id=9001, chat_id=123))
    parser.assert_awaited_once_with(text)
    assert main.tasks.list_open() == []
    first = store.process_update(9001, action)
    second = store.process_update(9001, action)
    assert len(main.tasks.list_open()) == 5
    assert first["reply"] == second["reply"]
    assert len(first["messages"]) == 11
    assert sum(bool(m.get('task_review')) for m in first['messages']) == 5
    assert all(item["project"] is None for item in main.tasks.list_open())


def test_batch_write_failure_rolls_back(store, monkeypatch):
    monkeypatch.setattr(main.ai, "parse_many", AsyncMock(return_value=[ParsedTask("First"), ParsedTask("Second")]))
    action = asyncio.run(main.prepare_message("Create two tasks", create_only=True))
    create_many = store.create_tasks
    def fail_batch(items, created_at):
        create_many(items, created_at)
        raise RuntimeError("Storage failed")
    monkeypatch.setattr(store, "create_tasks", fail_batch)
    with pytest.raises(RuntimeError, match="Storage failed"):
        store.process_update(9002, action)
    assert main.tasks.list_open() == []
    assert store.get_update(9002) is None


@pytest.mark.parametrize("bound", [False, True])
def test_ai_acknowledgement_never_changes_tasks_or_calls_edit(store, monkeypatch, bound):
    task = main.tasks.create("Preserve me")
    before = main.tasks.list_open()
    classify(monkeypatch, action="acknowledge", task_ids=[task["id"]])
    edit = AsyncMock()
    monkeypatch.setattr(main.ai, "edit", edit)
    receipt = run("先保持原樣就好", bound_task_id=task["id"] if bound else None)
    assert receipt["reply"] == "收到"
    assert "messages" not in receipt
    assert main.tasks.list_open() == before
    edit.assert_not_awaited()
    main.ai.parse_many.assert_not_awaited()


def test_explicit_no_change_reply_skips_ai(store, monkeypatch):
    task = main.tasks.create("Preserve me")
    classify_mock = classify(monkeypatch, action="task_complete", task_ids=[task["id"]])
    before = main.tasks.list_open()
    assert run("不需要記錄", bound_task_id=task["id"])["reply"] == "收到"
    assert main.tasks.list_open() == before
    classify_mock.assert_not_awaited()
