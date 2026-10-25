import asyncio
from unittest.mock import AsyncMock

from chronos.telegram_menu import COMMANDS, register_menu


def test_registration_only_lists_commands_usable_without_parameters():
    expected = [{"command": k, "description": v} for k, v in COMMANDS.items()]
    assert set(COMMANDS) == {"task", "plan", "studyreset", "today", "tomorrow", "free", "info"}
    client = AsyncMock()
    client.request.side_effect = [{"ok": True}, {"ok": True, "result": expected}]
    assert asyncio.run(register_menu(client, 123)) == list(COMMANDS)
    assert client.request.call_args_list[0].args == (
        "setMyCommands", {"scope": {"type": "chat", "chat_id": 123}, "commands": expected})
