"""Small Gmail transport. Deliberately exposes neither send nor permanent delete."""
import asyncio
import base64
import re
import time
from html import unescape

import httpx


class GmailError(RuntimeError):
    """Safe for logs: never includes OAuth credentials or message bodies."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def message_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise GmailError("Invalid Gmail message ID")
    return value


def clean(value, limit=2000):
    return re.sub(r"\s+", " ", unescape(str(value))).strip()[:limit]


def decode_message(raw):
    payload = raw.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    plain, html = [], []

    def visit(part):
        # Do not download or interpret attachments, including attached messages.
        if part.get("filename"):
            return
        data = part.get("body", {}).get("data", "")
        if data and part.get("mimeType") in {"text/plain", "text/html"}:
            try:
                text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            except (ValueError, TypeError):
                text = ""
            (plain if part["mimeType"] == "text/plain" else html).append(text)
        for child in part.get("parts", []):
            visit(child)

    visit(payload)
    body = "\n".join(plain)
    if not body:
        body = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", "\n".join(html), flags=re.S | re.I)
        body = re.sub(r"<[^>]+>", " ", body)
    normalized_body = clean(body, 1000000)
    return {
        "id": message_id(raw["id"]), "thread_id": raw.get("threadId", ""),
        "labels": raw.get("labelIds", []), "subject": clean(headers.get("subject", "(無主旨)"), 240),
        "sender": clean(headers.get("from", ""), 240), "date": clean(headers.get("date", ""), 100),
        "snippet": clean(raw.get("snippet", ""), 600), "body": normalized_body[:12000],
        "body_truncated": len(normalized_body) > 12000,
        "unsubscribe": bool(headers.get("list-unsubscribe")),
        "has_reply": bool(headers.get("in-reply-to") or headers.get("references")),
    }


class GmailClient:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.transport = transport
        self._token = ""
        self._expires = 0
        self._lock = asyncio.Lock()

    async def _access_token(self):
        async with self._lock:
            if self._token and time.monotonic() < self._expires:
                return self._token
            config = self.settings
            if not all((config.gmail_client_id, config.gmail_client_secret, config.gmail_refresh_token)):
                raise GmailError("Gmail OAuth is not configured")
            try:
                async with httpx.AsyncClient(timeout=20, transport=self.transport) as client:
                    response = await client.post("https://oauth2.googleapis.com/token", data={
                        "client_id": config.gmail_client_id, "client_secret": config.gmail_client_secret,
                        "refresh_token": config.gmail_refresh_token, "grant_type": "refresh_token",
                    })
                if not response.is_success:
                    raise GmailError(f"Gmail OAuth failed (HTTP {response.status_code}); reconnect Gmail")
                token = response.json()
                self._token = token["access_token"]
                self._expires = time.monotonic() + max(0, int(token.get("expires_in", 3600)) - 60)
                return self._token
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                raise GmailError("Gmail OAuth request failed") from None

    async def _request(self, method, path, **kwargs):
        token = await self._access_token()
        try:
            async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
                response = await client.request(method, "https://gmail.googleapis.com/gmail/v1/users/me/" + path,
                                                headers={"Authorization": f"Bearer {token}"}, **kwargs)
            if not response.is_success:
                if response.status_code == 401:
                    self._expires = 0
                raise GmailError(f"Gmail request failed (HTTP {response.status_code})", response.status_code)
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise GmailError("Gmail request failed; outcome may be unknown") from None

    async def verify_account(self):
        expected = self.settings.gmail_account.strip().lower()
        if not expected:
            raise GmailError("Expected Gmail account is required")
        profile = await self._request("GET", "profile")
        if str(profile.get("emailAddress", "")).lower() != expected:
            raise GmailError("Gmail account does not match configuration")
        return expected

    async def unread_ids(self, limit, skip=None, on_page=None):
        ids, page = [], None
        while len(ids) < limit:
            if on_page:
                on_page()
            params = {"q": "is:unread -in:trash -in:spam", "maxResults": min(100, limit - len(ids))}
            if page:
                params["pageToken"] = page
            data = await self._request("GET", "messages", params=params)
            for item in data.get("messages", []):
                identifier = message_id(item["id"])
                if skip is None or not skip(identifier):
                    ids.append(identifier)
            page = data.get("nextPageToken")
            if not page:
                break
        return list(dict.fromkeys(ids)), bool(page)

    async def read(self, identifier):
        return decode_message(await self._request("GET", f"messages/{message_id(identifier)}", params={"format": "full"}))

    async def trash(self, identifier):
        await self._request("POST", f"messages/{message_id(identifier)}/trash")

    async def mark_read(self, identifier):
        await self._request("POST", f"messages/{message_id(identifier)}/modify", json={"removeLabelIds": ["UNREAD"]})
