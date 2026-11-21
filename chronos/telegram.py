import httpx
import logging

from .message_lifecycle import MessageExpiry

# Telegram credentials appear in request paths. Never permit library transport
# logs to print those paths, even when the application's root logger is INFO.
logging.getLogger("httpx").disabled = True
logging.getLogger("httpcore").disabled = True


class TelegramError(RuntimeError):
    """A transport failure that never includes the bot token or request URL."""


class TelegramClient:
    async def download_photo(self, file_id: str) -> bytes:
        """Fetch a bounded JPEG from Telegram without exposing credential URLs."""
        import re
        limit = 8 * 1024 * 1024
        result = await self.request("getFile", {"file_id": file_id})
        info = result.get("result") or {}
        path = info.get("file_path", "")
        if (not result.get("ok") or not isinstance(path, str)
                or not re.fullmatch(r"[A-Za-z0-9_/-]+\.jpg", path)
                or ".." in path or path.startswith("/")
                or info.get("file_size", 0) > limit):
            raise TelegramError("Photo unavailable or too large (maximum 8 MB).")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                async with client.stream("GET", f"https://api.telegram.org/file/bot{self.token}/{path}") as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > limit:
                            raise TelegramError("Photo exceeds 8 MB.")
        except httpx.HTTPError:
            raise TelegramError("Photo download failed; please resend it.") from None
        if not content.startswith(b"\xff\xd8\xff"):
            raise TelegramError("Unsupported photo format; please send a JPEG photo.")
        return bytes(content)

    def __init__(self, token: str, *, expiry_store=None):
        self.token = token
        self.expiry = MessageExpiry(self, expiry_store)
        self.expiry_fallback = MessageExpiry(self)

    async def flush_expirations(self, *, wait=True):
        try:
            await self.expiry.drain(wait=wait)
        except Exception:
            logging.getLogger(__name__).warning("Durable Telegram expiry recovery pending")
        await self.expiry_fallback.drain(wait=wait)

    async def track_delivery(self, result, chat_id, text='', markup=None, **options):
        try:
            await self.expiry.track(result, chat_id, text, markup, **options)
            message_id = (result.get('result') or {}).get('message_id') if result.get('ok') else None
            key = f'{chat_id}:{message_id}'
            self.expiry_fallback.mutate(lambda old: {k:v for k,v in old.items() if k != key})
        except Exception:
            # Cleanup metadata must never turn a successful send into a resend.
            logging.getLogger(__name__).warning("Telegram expiry using local fallback")
            await self.expiry_fallback.track(result, chat_id, text, markup, **options)

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
        if not isinstance(result, dict):
            return {"ok": False, "description": "Telegram API returned an invalid response shape"}
        if not response.is_success:
            return {
                "ok": False,
                "error_code": result.get("error_code", response.status_code),
                "description": result.get("description", "Telegram API rejected the request"),
            }
        if method in {'editMessageText', 'editMessageReplyMarkup'} and result.get('ok'):
            if payload.get('chat_id') is not None and isinstance(payload.get('message_id'), int):
                await self.track_delivery({'ok': True, 'result': {'message_id': payload['message_id']}},
                    payload['chat_id'], payload.get('text', ''), payload.get('reply_markup'))
        if method == 'deleteMessage' and result.get('ok'):
            key = f"{payload.get('chat_id')}:{payload.get('message_id')}"
            try:
                self.expiry.mutate(lambda old: {k:v for k,v in old.items() if k != key})
            except Exception:
                logging.getLogger(__name__).warning("Telegram deletion metadata cleanup pending")
        return result

    async def send_message(
        self, chat_id: int, text: str, parse_mode: str | None = None, reply_markup: dict | None = None,
        reply_to_message_id: int | None = None,
        *, requires_response: bool | None = None, delete_after_seconds: int | None = None,
    ) -> dict:
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        result = await self.request("sendMessage", payload)
        await self.track_delivery(result, chat_id, text, reply_markup, requires_reply=requires_response,
                                delete_after_seconds=delete_after_seconds)
        return result

    async def answer_callback_query(self, callback_query_id: str) -> dict:
        return await self.request("answerCallbackQuery", {"callback_query_id": callback_query_id})

    async def send_document(self, chat_id: int, filename: str, content: bytes, *, requires_response: bool = False) -> dict:
        if not self.enabled:
            return {"ok": False, "error_code": 401}
        try:
            async with httpx.AsyncClient(timeout=40) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{self.token}/sendDocument",
                    data={"chat_id": str(chat_id)},
                    files={"document": (filename, content, "text/markdown; charset=utf-8")},
                )
        except httpx.HTTPError as error:
            raise TelegramError(f"Telegram document transport failed: {type(error).__name__}") from None
        try:
            result = response.json()
        except ValueError:
            raise TelegramError("Telegram document outcome is unknown") from None
        if not isinstance(result, dict):
            raise TelegramError("Telegram document outcome is unknown")
        await self.track_delivery(result, chat_id, requires_reply=requires_response)
        return result

    async def set_webhook(self, url: str, secret: str = "") -> dict:
        payload = {"url": url, "allowed_updates": ["message", "edited_message", "callback_query"]}
        if secret:
            payload["secret_token"] = secret
        return await self.request("setWebhook", payload)

