"""Send two labeled owner-only messages to verify Telegram delivery and reply references.

Does not change the webhook or poll updates. Never print response bodies or URLs.
"""
import asyncio
import json
import logging

from chronos.settings import settings
from chronos.telegram import TelegramClient
from chronos.study_scheduler import delivery_outcome


async def main():
    logging.disable(logging.CRITICAL)
    evidence = {"initial_delivery": False, "reply_reference_delivery": False}
    try:
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            raise ValueError("missing configuration")
        client = TelegramClient(settings.telegram_bot_token)
        first = await client.send_message(settings.telegram_chat_id,
            "[Chronos Phase 1 整合測試] 驗證課後通知送達。這是測試訊息，不需要回覆，也不會建立作業。")
        message_id, _ = delivery_outcome(first)
        if message_id is None:
            raise ValueError("unconfirmed delivery")
        evidence["initial_delivery"] = True
        second = await client.send_message(settings.telegram_chat_id,
            "[整合測試完成] 已驗證通知與原始訊息的 Reply 關聯。正式課表排程尚未啟用。",
            reply_to_message_id=message_id)
        second_id, _ = delivery_outcome(second)
        evidence["reply_reference_delivery"] = second_id is not None
    except Exception as error:
        evidence["error_type"] = type(error).__name__
    print(json.dumps(evidence))
    return 0 if all(evidence.get(key) for key in ("initial_delivery", "reply_reference_delivery")) else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
