import httpx


class TelegramClient:
    def __init__(self, token: str):
        self.token = token

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def request(self, method: str, payload: dict) -> dict:
        if not self.enabled:
            return {"ok": False, "description": "Telegram 尚未設定"}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(f"https://api.telegram.org/bot{self.token}/{method}", json=payload)
            response.raise_for_status()
            return response.json()

    async def send_message(self, chat_id: int, text: str) -> dict:
        return await self.request("sendMessage", {"chat_id": chat_id, "text": text})

    async def set_webhook(self, url: str, secret: str = "") -> dict:
        payload = {"url": url, "allowed_updates": ["message"]}
        if secret:
            payload["secret_token"] = secret
        return await self.request("setWebhook", payload)

