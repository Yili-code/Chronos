import asyncio
from unittest.mock import AsyncMock

from chronos.telegram_menu import COMMANDS, register_menu


def test_registration_preserves_existing_commands_and_verifies_owner_scope():
    existing = [{"command": "custom", "description": "Custom command"},
                {"command": "help", "description": "Old help command"},
                {"command": "command", "description": "Accidental placeholder"},
                {"command": "backward", "description": "Old standalone help entry"}]
    expected = existing[:1] + [{"command": k, "description": v} for k, v in COMMANDS.items()]
    client = AsyncMock()
    client.request.side_effect = [
        {"ok": True, "result": []}, {"ok": True, "result": existing},
        {"ok": True}, {"ok": True, "result": expected},
    ]
    assert asyncio.run(register_menu(client, 123)) == ["custom", *COMMANDS]
    assert client.request.call_args_list[2].args == (
        "setMyCommands", {"scope": {"type": "chat", "chat_id": 123}, "commands": expected})
