import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest

from chronos import main
from chronos.db import Database
from chronos.image_deadlines import ImageDeadline, ImageDeadlines, confirm_photo, prepare_photo
from chronos.tasks import TaskService
from chronos.telegram import TelegramClient, TelegramError
from test_firestore import database
from test_system import system

NOW = datetime(2026, 10, 14, tzinfo=ZoneInfo("Asia/Taipei"))


@pytest.fixture(params=["sqlite", "firestore"])
def store(request, tmp_path, monkeypatch):
    if request.param == "firestore":
        return database(monkeypatch)
    db = Database(tmp_path / "images.db")
    db.initialize()
    return db


def propose(tasks, ids):
    result = ImageDeadlines(items=[ImageDeadline(source_title=f"Assignment {i}", task_id=i,
        year=None, month=11, day=8, clock="23:59") for i in ids])
    ai = SimpleNamespace(_generate_output=AsyncMock(return_value=result))
    bot = SimpleNamespace(download_photo=AsyncMock(return_value=b"\xff\xd8\xfftest"))
    output = asyncio.run(prepare_photo(ai, bot, tasks, SimpleNamespace(file_id="file"),
                                      "add their deadline", 100, 123, NOW))
    return output, ai, bot


def test_preview_confirmation_and_duplicate_receipts(store):
    tasks = TaskService(store, NOW.tzinfo)
    original = [tasks.create(f"Assignment {i}", project="course", timing={"event": "2026-10-18"}) for i in range(4)]
    output, ai, bot = propose(tasks, [t["id"] for t in original])
    assert tasks.list_open() == original
    assert "Year absent" in output["messages"][0]["text"]
    assert "inlineData" in ai._generate_output.call_args.kwargs["image_part"]
    store.process_update(100, lambda: output)
    for index, task in enumerate(original):
        for _ in range(2):
            receipt = store.process_update(200 + index, lambda: confirm_photo(store, tasks, f"image:100:{index}", 123, NOW))
            assert "Updated" in receipt["reply"]
        saved = tasks.get_open_by_id(task["id"])
        assert saved["due_at"] == "2026-11-08T23:59:00+08:00"
        assert saved["timing"]["event"] == "2026-10-18"
        assert saved["project"] == "course"
    assert "already applied" in store.process_update(300, lambda: confirm_photo(store, tasks, "image:100:0", 123, NOW))["reply"]


@pytest.mark.parametrize("case", ["changed", "closed", "expired", "wrong_chat"])
def test_stale_or_unauthorized_proposals_do_not_write(store, case):
    tasks = TaskService(store, NOW.tzinfo)
    task = tasks.create("Assignment")
    output, _, _ = propose(tasks, [task["id"]])
    store.process_update(100, lambda: output)
    if case == "changed":
        tasks.edit(task["id"], "New title", None, None, {})
    if case == "closed":
        tasks.complete(task["id"])
    before = tasks.list_open()
    store.process_update(200, lambda: confirm_photo(store, tasks, "image:100:0",
        999 if case == "wrong_chat" else 123, NOW + timedelta(days=2) if case == "expired" else NOW))
    assert tasks.list_open() == before


def test_caption_photo_webhook_and_redelivery(system, monkeypatch):
    client, tasks, bot, config = system
    task = tasks.create("Assignment")
    output, ai, download = propose(tasks, [task["id"]])
    monkeypatch.setattr(main.ai, "_generate_output", ai._generate_output)
    monkeypatch.setattr(bot, "download_photo", download.download_photo)
    payload = {"update_id": 100, "message": {"message_id": 10, "chat": {"id": 123},
        "caption": "add their deadline", "photo": [{"file_id": "small", "width": 10, "height": 10},
        {"file_id": "large", "width": 100, "height": 100}]}}
    for _ in range(2):
        assert client.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"}, json=payload).status_code == 200
    download.download_photo.assert_awaited_with("large")
    assert bot.send_message.await_count == 1
    assert tasks.get_open_by_id(task["id"])["due_at"] is None
    main.ai.parse.assert_not_awaited()
    callback = {"update_id": 101, "callback_query": {"id": "c", "data": "image:100:0",
        "message": {"message_id": 11, "chat": {"id": 123}}}}
    assert client.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"}, json=callback).status_code == 200
    assert tasks.get_open_by_id(task["id"])["due_at"] is not None


@pytest.mark.parametrize("path,content", [("../secret.jpg", b"abc"), ("photos/file.jpg", b"not jpeg"),
                                        ("photos/file.jpg", b"\xff\xd8\xffvalid")])
def test_photo_download_validates_path_and_content(monkeypatch, path, content):
    client = TelegramClient("secret-token")
    client.request = AsyncMock(return_value={"ok": True, "result": {"file_path": path}})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=content)), **kw))
    if content.startswith(b"\xff\xd8\xff"):
        assert asyncio.run(client.download_photo("id")) == content
    else:
        with pytest.raises(TelegramError) as error:
            asyncio.run(client.download_photo("id"))
        assert "secret-token" not in str(error.value)


@pytest.mark.parametrize("case", ["date_only", "unmatched", "duplicate", "invalid_date", "ai_failure", "no_caption"])
def test_extraction_edge_cases(store, case):
    from chronos.ai import AIError
    tasks = TaskService(store, NOW.tzinfo)
    task = tasks.create("Assignment")
    item = ImageDeadline(source_title="Assignment", task_id=None if case == "unmatched" else task["id"],
        year=2026, month=2 if case == "invalid_date" else 11,
        day=30 if case == "invalid_date" else 8, clock=None if case == "date_only" else "23:59")
    ai = SimpleNamespace(_generate_output=AsyncMock(return_value=ImageDeadlines(items=[item, item] if case == "duplicate" else [item])))
    if case == "ai_failure":
        ai._generate_output.side_effect = AIError("Unavailable")
    bot = SimpleNamespace(download_photo=AsyncMock(return_value=b"jpeg"))
    output = asyncio.run(prepare_photo(ai, bot, tasks, SimpleNamespace(file_id="id"),
        "" if case == "no_caption" else "add deadlines", 100, 123, NOW))
    assert tasks.get_open_by_id(task["id"])["due_at"] is None
    if case == "date_only":
        store.process_update(100, lambda: output)
        store.process_update(101, lambda: confirm_photo(store, tasks, "image:100:0", 123, NOW))
        saved = tasks.get_open_by_id(task["id"])
        assert saved["due_at"] is None and saved["timing"]["due_date"] == "2026-11-08"
    elif case == "unmatched":
        assert "reply_markup" not in output["messages"][0]
    elif case == "duplicate":
        assert "reply_markup" not in output["messages"][1]
    else:
        assert isinstance(output, str) and "No task was changed" in output


def test_multimodal_request_uses_existing_ai_transport(monkeypatch):
    from chronos.ai import ExternalAI
    from test_ai import config, mock_provider
    def handler(request):
        import json
        body = json.loads(request.content)
        assert body["contents"][0]["parts"][1] == {"inlineData": {"mimeType": "image/jpeg", "data": "YWJj"}}
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"items": []}'}]}}]})
    mock_provider(monkeypatch, handler)
    result = asyncio.run(ExternalAI(config())._generate_output("Extract deadlines", "caption",
        ImageDeadlines.model_json_schema(), ImageDeadlines, "task",
        image_part={"inlineData": {"mimeType": "image/jpeg", "data": "YWJj"}}))
    assert result.items == []


@pytest.mark.parametrize("case", ["past", "weekday_mismatch", "missing_time", "invalid_date"])
def test_image_event_requests_clarification_without_creating_a_preview(store, case):
    from hashlib import sha256
    from chronos.image_events import ImageEvent, prepare_event_photo
    from chronos.settings import Settings

    config = Settings(_env_file=None, telegram_chat_id=123)
    now = datetime(2026, 11, 17, tzinfo=config.tz)
    fields = {"title": "Web 基礎概念與資安實戰入門", "year": None, "month": 11, "day": 4,
              "weekday": 2, "start": "18:00", "end": "20:00", "location": "資工系館 301 電腦教室",
              "category": "other", "description": None}
    changes, hint = {
        "past": ({}, "2026-11-04 18:00–20:00，時間已過"),
        "weekday_mismatch": ({"day": 28}, "日期與星期不一致"),
        "missing_time": ({"end": None}, "請補充"),
        "invalid_date": ({"day": 31}, "日期或時間有誤"),
    }[case]
    ai = SimpleNamespace(_generate_output=AsyncMock(return_value=ImageEvent(**{**fields, **changes})))
    bot = SimpleNamespace(download_photo=AsyncMock(return_value=b"image"))
    reply = asyncio.run(prepare_event_photo(store, ai, bot, config, SimpleNamespace(file_id="file"),
                                           "將此活動加進行程", 100, 123, now))
    assert hint in reply
    token = sha256(b"event:123:100").hexdigest()[:32]
    assert store.get_task_action_state("free:booking:" + token) is None
    assert store.list_open_tasks() == []


def test_event_photo_webhook_previews_then_books_calendar_once(system, monkeypatch):
    from chronos.image_events import ImageEvent

    client, tasks, bot, config = system
    day = (datetime.now(config.tz) + timedelta(days=3)).date()
    event = ImageEvent(title="IND 社課｜Web 基礎概念與資安實戰入門", year=day.year, month=day.month, day=day.day,
                       weekday=day.weekday(), start="18:00", end="20:00", location="資工系館 301 電腦教室",
                       category="other", description="HTTP、Burp Suite 與 Web CTF 練習")
    model = AsyncMock(return_value=event)
    download = AsyncMock(return_value=b"image")
    monkeypatch.setattr(main.ai, "_generate_output", model)
    monkeypatch.setattr(bot, "download_photo", download)
    monkeypatch.setattr("chronos.free_booking.class_windows", lambda *args: [])
    calendar = AsyncMock()
    calendar.event.return_value = None
    calendar.busy.return_value = []

    async def insert(body):
        calendar.event.return_value = body
        return body

    calendar.insert.side_effect = insert
    monkeypatch.setattr(main, "google_calendar", calendar)
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 11}}
    payload = {"update_id": 100, "message": {"message_id": 10, "chat": {"id": 123},
               "caption": "將此活動加進行程", "photo": [{"file_id": "file", "width": 100, "height": 150}]}}
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-hook"}
    for _ in range(2):
        assert client.post("/telegram/webhook", headers=headers, json=payload).status_code == 200
    download.assert_awaited_once_with("file")
    model.assert_awaited_once()
    assert model.await_args.kwargs["image_part"]["inlineData"]["mimeType"] == "image/jpeg"
    calendar.insert.assert_not_awaited()
    assert bot.send_message.await_count == 1
    assert "資工系館 301 電腦教室" in bot.send_message.await_args.args[1]
    markup = bot.send_message.await_args.kwargs["reply_markup"]
    allow, cancel = markup["inline_keyboard"][0]
    assert allow["text"] == "Allow" and cancel["text"] == "Cancel"
    callback = {"update_id": 101, "callback_query": {"id": "confirm", "data": allow["callback_data"],
                "message": {"message_id": 11, "chat": {"id": 123}}}}
    for _ in range(2):
        assert client.post("/telegram/webhook", headers=headers, json=callback).status_code == 200
    calendar.insert.assert_awaited_once()
    booking = calendar.insert.await_args.args[0]
    assert booking["summary"] == event.title and booking["location"] == event.location
    assert booking["start"]["dateTime"] == f"{day.isoformat()}T18:00:00+08:00"
    assert booking["end"]["dateTime"] == f"{day.isoformat()}T20:00:00+08:00"
    assert tasks.list_open() == []
    main.ai.parse.assert_not_awaited()
