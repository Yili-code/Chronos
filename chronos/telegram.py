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
            return {"ok": False, "description": "Telegram 尚未設定"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(f"https://api.telegram.org/bot{self.token}/{method}", json=payload)
        except httpx.HTTPError as error:
            raise TelegramError(f"Telegram API transport failed: {type(error).__name__}") from None
        try:
            result = response.json()
        except ValueError:
            return {"ok": False, "error_code": response.status_code, "description": "Telegram API 回傳非 JSON 內容"}
        if not response.is_success:
            return {
                "ok": False,
                "error_code": result.get("error_code", response.status_code),
                "description": result.get("description", "Telegram API 拒絕請求"),
            }
        return result

    async def send_message(self, chat_id: int, text: str, parse_mode: str | None = None) -> dict:
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return await self.request("sendMessage", payload)

    async def set_webhook(self, url: str, secret: str = "") -> dict:
        payload = {"url": url, "allowed_updates": ["message"]}
        if secret:
            payload["secret_token"] = secret
        return await self.request("setWebhook", payload)

