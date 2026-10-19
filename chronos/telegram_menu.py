"""Explicit, owner-scoped Telegram command menu registration."""
import asyncio

from .settings import Settings
from .telegram import TelegramClient

COMMANDS = {
    "backward": "Plan a deadline goal or review progress",
    "plan": "Review task plans",
    "today": "View today's calendar agenda",
    "tasks": "View open tasks",
    "help": "Show available commands",
    "tomorrow": "View tomorrow's calendar agenda",
    "free": "Find free calendar slots",
    "replan": "Review moves to free a calendar window",
}


async def register_menu(client, chat_id):
    scope = {"type": "chat", "chat_id": chat_id}
    current = await client.request("getMyCommands", {"scope": scope})
    if not current.get("ok"):
        raise RuntimeError("Unable to read Telegram command menu")
    commands = current.get("result") or []
    if not commands:
        default = await client.request("getMyCommands", {})
        if not default.get("ok"):
            raise RuntimeError("Unable to read default Telegram command menu")
        commands = default.get("result") or []
    merged = {item["command"]: item["description"] for item in commands}
    # Remove the placeholder accidentally registered by the first planning release.
    merged.pop('command', None)
    merged.update(COMMANDS)
    expected = [{"command": key, "description": value} for key, value in merged.items()]
    result = await client.request("setMyCommands", {"scope": scope, "commands": expected})
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected command menu update")
    actual = await client.request("getMyCommands", {"scope": scope})
    if not actual.get("ok") or actual.get("result") != expected:
        raise RuntimeError("Telegram command menu verification failed")
    return list(merged)


async def main():
    settings = Settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise RuntimeError("Telegram owner configuration is required")
    commands = await register_menu(TelegramClient(settings.telegram_bot_token), settings.telegram_chat_id)
    print("Verified owner menu: " + ", ".join(commands))


if __name__ == "__main__":
    asyncio.run(main())
