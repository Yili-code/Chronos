"""Secret-free Telegram message correlation helpers for Phase 1."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReplyContext:
    message_id: int
    reply_to_message_id: int | None
    text: str


def parse_reply_context(message: dict) -> ReplyContext | None:
    """Extract only message ids and text; ignore all other Telegram fields."""
    try:
        message_id = int(message["message_id"])
    except (KeyError, TypeError, ValueError):
        return None
    reply = message.get("reply_to_message")
    reply_id = None
    if reply is not None:
        try:
            reply_id = int(reply["message_id"])
        except (KeyError, TypeError, ValueError):
            return None
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    return ReplyContext(message_id=message_id, reply_to_message_id=reply_id, text=text.strip())
