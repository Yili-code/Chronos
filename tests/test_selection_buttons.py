from unittest.mock import AsyncMock
from chronos import main
from chronos.selection_store import SelectionStore
from chronos.selection_buttons import selection_view
from test_selection_store import selection
from test_system import system


def test_webhook_selection_is_explicit_and_duplicate_safe(system, monkeypatch):
    client, _, bot, _ = system
    initial = SelectionStore(main.db).create("pick", 123, selection())
    SelectionStore(main.db).bind_message("pick", 123, 5)
    text, markup = selection_view("pick", initial)
    assert "已選 0" in text
    assert markup["inline_keyboard"][-1][0]["callback_data"] == "pdf:pick:0:done"
    monkeypatch.setattr(bot, "request", AsyncMock(return_value={"ok": True}))
    for data in ["pdf:pick:0:s0", "pdf:pick:0:s0", "pdf:pick:1:s1", "pdf:pick:2:done"]:
        response = client.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "test-hook"},
            json={"update_id": 901, "callback_query": {"id": "callback", "data": data,
                  "message": {"message_id": 5, "chat": {"id": 123}}}})
        assert response.status_code == 200
    state = main.db.get_material_selection("pick")
    assert state["revision"] == 3
    assert state["selection"]["confirmed"]
    assert state["selection"]["selected_ids"] == ["a", "b"]
    main.ai.parse.assert_not_awaited()
