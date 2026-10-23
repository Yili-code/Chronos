"""Preference persistence and routing, using both production storage contracts."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from chronos import main, preferences
from chronos.intents import Intent
from test_intents import store, run, classify


def test_preferences_survive_new_turn_and_are_scoped(store, monkeypatch):
    classify(monkeypatch, action="preference", preference_operation="set",
             preference_key="name", preference_value="YiLi")
    assert "已記住" in run("以後叫我 YiLi")["reply"]
    mock = classify(monkeypatch, action="task_list")
    run("看看我的任務", 2)
    assert mock.await_args.kwargs["context"]["preferences"] == {"name": "YiLi"}
    assert preferences.read(store, 456) == {}
    assert main.tasks.list_open() == []


def test_query_update_forget_and_receipt_replay(store, monkeypatch):
    classify(monkeypatch, action="preference", preference_operation="set",
             preference_key="language", preference_value="繁體中文")
    action = asyncio.run(main.prepare_message("記住我喜歡繁體中文", update_id=1, chat_id=123))
    first = store.process_update(1, action)
    classify(monkeypatch, action="preference", preference_operation="set",
             preference_key="language", preference_value="English")
    run("語言改成英文", 2)
    assert store.process_update(1, action) == first
    assert preferences.read(store, 123) == {"language": "English"}
    classify(monkeypatch, action="preference", preference_operation="list")
    assert "English" in run("我有哪些偏好", 3)["reply"]
    classify(monkeypatch, action="preference", preference_operation="forget", preference_key="language")
    assert "已忘記" in run("忘記語言偏好", 4)["reply"]
    assert preferences.read(store, 123) == {}
    assert "找不到" in run("再忘記一次", 5)["reply"]


def test_missing_preference_value_clarifies_without_write(store, monkeypatch):
    classify(monkeypatch, action="preference", preference_operation="set", preference_key="language")
    assert "請說明" in run("記住我的語言偏好")["reply"]
    assert preferences.read(store, 123) == {}


def test_preferences_are_bounded_and_rendered_as_data(store):
    for index in range(20):
        preferences.apply(store, 123, "set", str(index), "value")
    assert "20" in preferences.apply(store, 123, "set", "overflow", "value")
    assert len(preferences.read(store, 123)) == 20
    preferences.apply(store, 123, "set", "0", "<b>untrusted</b>")
    assert "&lt;b&gt;" in preferences.apply(store, 123, "list")


def test_without_telegram_identity_preferences_are_not_written(store, monkeypatch):
    classify(monkeypatch, action="preference", preference_operation="set", preference_key="name", preference_value="YiLi")
    action = asyncio.run(main.prepare_message("以後叫我 YiLi"))
    assert "Telegram" in action()
    assert preferences.read(store, None) == {}


def test_preference_question_never_writes(store, monkeypatch):
    classify(monkeypatch, action="preference", preference_operation="set", preference_key="language",
             preference_value="English", question="要使用英文嗎？")
    assert "英文" in run("改用英文？")["reply"]
    assert preferences.read(store, 123) == {}
