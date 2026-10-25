"""Explicit, owner-scoped Telegram command menu registration."""
import asyncio

from .settings import Settings
from .telegram import TelegramClient

COMMANDS = {
    "task": "查看任務",
    "plan": "Review task plans",
    "today": "View today's calendar agenda",
    "tomorrow": "View tomorrow's calendar agenda",
    "free": "Find free calendar slots",
    "info": "簡短說明",
}


async def register_menu(client, chat_id):
    scope = {"type": "chat", "chat_id": chat_id}
    # Replace the owner menu exactly; hidden legacy handlers remain compatible.
    expected = [{"command": key, "description": value} for key, value in COMMANDS.items()]
    result = await client.request("setMyCommands", {"scope": scope, "commands": expected})
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected command menu update")
    actual = await client.request("getMyCommands", {"scope": scope})
    if not actual.get("ok") or actual.get("result") != expected:
        raise RuntimeError("Telegram command menu verification failed")
    return list(COMMANDS)


async def main():
    settings = Settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise RuntimeError("Telegram owner configuration is required")
    commands = await register_menu(TelegramClient(settings.telegram_bot_token), settings.telegram_chat_id)
    print("Verified owner menu: " + ", ".join(commands))


if __name__ == "__main__":
    asyncio.run(main())
