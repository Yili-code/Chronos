from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from chronos.selection_delivery import send_selection
from chronos.selection_buttons import apply_callback
from chronos.selection_store import SelectionStore
from chronos.db import Database
from test_selection_store import selection


@pytest.mark.asyncio
async def test_selection_send_binds_once_and_recovers_binding(tmp_path):
    db = Database(tmp_path / "selection.db")
    db.initialize()
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 42}}
    args = dict(key="pick", chat_id=123, selection=selection(), now=datetime.now(timezone.utc))
    assert await send_selection(db, bot, **args) == "sent"
    assert db.get_material_selection("pick")["message_id"] == 42
    db.mutate_material_selection("pick", lambda state: {**state, "message_id": None})
    assert await send_selection(db, bot, **args) == "sent"
    assert bot.send_message.await_count == 1
    assert db.get_material_selection("pick")["message_id"] == 42
    with pytest.raises(ValueError):
        apply_callback(db, 123, "pdf:pick:0:s0", message_id=99)
    apply_callback(db, 123, "pdf:pick:0:s0", message_id=42)
    assert db.get_material_selection("pick")["selection"]["selected_ids"] == ["a"]
    with pytest.raises(ValueError):
        SelectionStore(db).bind_message("pick", 123, 99)
