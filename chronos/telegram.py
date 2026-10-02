import httpx


class TelegramError(RuntimeError):
    """A transport failure that never includes the bot token or request URL."""


class TelegramClient:
    def __init__(self, token: str):
        self.token = token

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def request(self, method: str, payload: dict) -> dict:
        if not self.enabled:
            return {"ok": False, "description": "Telegram is not configured"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(f"https://api.telegram.org/bot{self.token}/{method}", json=payload)
        except httpx.HTTPError as error:
            raise TelegramError(f"Telegram API transport failed: {type(error).__name__}") from None
        try:
            result = response.json()
        except ValueError:
            return {"ok": False, "error_code": response.status_code, "description": "Telegram API returned non-JSON content"}
        if not response.is_success:
            return {
                "ok": False,
                "error_code": result.get("error_code", response.status_code),
                "description": result.get("description", "Telegram API rejected the request"),
            }
        return result

    async def send_message(
        self, chat_id: int, text: str, parse_mode: str | None = None, reply_markup: dict | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict:
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        return await self.request("sendMessage", payload)

    async def answer_callback_query(self, callback_query_id: str) -> dict:
        return await self.request("answerCallbackQuery", {"callback_query_id": callback_query_id})

    async def set_webhook(self, url: str, secret: str = "") -> dict:
        payload = {"url": url, "allowed_updates": ["message", "callback_query"]}
        if secret:
            payload["secret_token"] = secret
        return await self.request("setWebhook", payload)

