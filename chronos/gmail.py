"""Narrow Gmail transport; outbound mail requires the reviewed-draft workflow."""
import asyncio
import base64
import re
import time
from html import unescape
from email.message import EmailMessage
from email.utils import parseaddr, getaddresses
from email import policy
from email.parser import BytesParser

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


def email_address(value):
    """Preserve the recipient local part; only normalize its domain."""
    address = parseaddr(str(value))[1]
    local, separator, domain = address.rpartition("@")
    return local + separator + domain.lower() if separator else address


def decode_message(raw):
    payload = raw.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    plain, html = [], []
    incomplete = False

    def visit(part):
        nonlocal incomplete
        # Do not download or interpret attachments, including attached messages.
        mime = part.get("mimeType", "")
        if (part.get("filename") or part.get("body", {}).get("attachmentId")
                or mime == "message/rfc822"):
            incomplete = True
            return
        data = part.get("body", {}).get("data", "")
        if data and mime in {"text/plain", "text/html"}:
            try:
                text = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True).decode("utf-8")
            except (ValueError, TypeError, UnicodeDecodeError):
                incomplete = True
                text = ""
            (plain if part["mimeType"] == "text/plain" else html).append(text)
        elif mime and mime not in {"text/plain", "text/html"} and not mime.startswith("multipart/"):
            incomplete = True
            return
        elif not data and part.get("body", {}).get("size", 0):
            incomplete = True
        for child in part.get("parts", []):
            visit(child)

    visit(payload)
    body = "\n".join(plain)
    if not body:
        body = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", "\n".join(html), flags=re.S | re.I)
        body = re.sub(r"<[^>]+>", " ", body)
    normalized_body = clean(body, 1000000)
    return {
        "received_at": int(raw.get("internalDate", 0)) // 1000,
        "id": message_id(raw["id"]), "thread_id": raw.get("threadId", ""),
        "labels": raw.get("labelIds", []), "subject": clean(headers.get("subject", "(無主旨)"), 240),
        "sender": clean(headers.get("from", ""), 240), "date": clean(headers.get("date", ""), 100),
        "snippet": clean(raw.get("snippet", ""), 600), "body": normalized_body[:12000],
        "body_truncated": len(normalized_body) > 12000,
        "body_incomplete": incomplete,
        "unsubscribe": bool(headers.get("list-unsubscribe")),
        "has_reply": bool(headers.get("in-reply-to") or headers.get("references")),
        "reply_to": headers.get("reply-to", ""),
        "rfc_message_id": headers.get("message-id", ""),
        "references": headers.get("references", ""),
        "automatic": headers.get("auto-submitted", "no").lower() != "no" or bool(
            headers.get("x-autoreply") or headers.get("x-autorespond")),
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
        return await self.message_ids(limit, "is:unread -in:trash -in:spam", skip, on_page)

    async def message_ids(self, limit, query, skip=None, on_page=None):
        ids, page = [], None
        while len(ids) < limit:
            if on_page:
                on_page()
            params = {"q": query, "maxResults": min(100, limit - len(ids))}
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

    async def thread(self, identifier):
        data = await self._request("GET", f"threads/{message_id(identifier)}", params={"format": "full"})
        if not isinstance(data, dict) or data.get("id") != identifier or not isinstance(data.get("messages"), list) or not data["messages"]:
            raise GmailError("Gmail returned an incomplete thread")
        try:
            messages = [decode_message(raw) for raw in data["messages"]]
        except (ValueError, TypeError, KeyError, AttributeError):
            raise GmailError("Gmail returned an invalid thread") from None
        if len(messages) > 100 or any(m["thread_id"] != identifier for m in messages):
            raise GmailError("Gmail thread is too large or inconsistent; review in Gmail")
        return sorted(messages, key=lambda m: m["received_at"])

    def reply_payload(self, thread_id, recipient, subject, body, parent_id, references=""):
        """Build immutable, single-recipient plain MIME with no hidden recipients."""
        if (not isinstance(recipient, str) or re.search(r"[\r\n]", recipient)
                or not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", recipient)
                or parseaddr(recipient)[1] != recipient):
            raise GmailError("A single validated reply address is required")
        if (not isinstance(subject, str) or len(subject) > 240 or re.search(r"[\r\n]", subject)
                or not isinstance(body, str) or not body.strip() or len(body) > 4000):
            raise GmailError("Invalid draft content")
        if not isinstance(parent_id, str) or not re.fullmatch(r"<[^<>\s]+@[^<>\s]+>", parent_id):
            raise GmailError("Original message header is missing or invalid")
        if not isinstance(references, str) or re.search(r"[\r\n]", references):
            raise GmailError("Invalid reply references")
        ids = re.findall(r"<[^<>\s]+@[^<>\s]+>", references)[-20:]
        if parent_id not in ids:
            ids.append(parent_id)
        message = EmailMessage()
        message["To"], message["Subject"] = recipient, subject
        message["In-Reply-To"], message["References"] = parent_id, " ".join(ids)
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        return {"threadId": message_id(thread_id), "raw": raw}

    async def create_reply_draft(self, thread_id, recipient, subject, body, parent_id, references=""):
        payload = self.reply_payload(thread_id, recipient, subject, body, parent_id, references)
        result = await self._request("POST", "drafts", json={"message": payload})
        if (not isinstance(result, dict) or not isinstance(result.get("message"), dict)
                or "DRAFT" not in result["message"].get("labelIds", [])
                or "SENT" in result["message"].get("labelIds", [])
                or result["message"].get("threadId") != thread_id):
            raise GmailError("Gmail draft result is incomplete; outcome may be unknown")
        message_id(result.get("id"))
        return result["id"]

    async def read_plain_draft(self, identifier):
        result = await self._request("GET", f"drafts/{message_id(identifier)}", params={"format": "raw"})
        try:
            message = result["message"]
            if (result["id"] != identifier or "DRAFT" not in message["labelIds"]
                    or "SENT" in message["labelIds"]):
                raise ValueError("Invalid draft state")
            raw = message["raw"]
            if not isinstance(raw, str) or len(raw) > 60000:
                raise ValueError("Draft exceeds reviewed-content bounds")
            mime = BytesParser(policy=policy.default).parsebytes(base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True))
            if (mime.defects or mime.is_multipart() or mime.get_content_type() != "text/plain" or mime.get_filename()
                    or mime.get_all("Cc") or mime.get_all("Bcc") or len(mime.get_all("To", [])) != 1
                    or any(name.lower().startswith("resent-") for name in mime.keys())):
                raise ValueError("Unsupported draft envelope")
            addresses = getaddresses([str(mime["To"])])
            if len(addresses) != 1:
                raise ValueError("Ambiguous recipient")
            return {"recipient": email_address(addresses[0][1]), "subject": str(mime.get("Subject", "")),
                    "body": mime.get_content(), "parent_id": str(mime.get("In-Reply-To", "")),
                    "thread_id": message_id(message["threadId"]), "message_id": message_id(message["id"])}
        except (ValueError, TypeError, KeyError, AttributeError, LookupError):
            raise GmailError("Native draft is incomplete or has unsupported content; sending blocked") from None

    async def send_reviewed_draft(self, identifier, thread_id, recipient, subject, body, parent_id, references=""):
        payload = self.reply_payload(thread_id, recipient, subject, body, parent_id, references)
        result = await self._request("POST", "drafts/send", json={"id": message_id(identifier), "message": payload})
        if (not isinstance(result, dict) or result.get("threadId") != thread_id
                or "SENT" not in result.get("labelIds", []) or "DRAFT" in result.get("labelIds", [])):
            raise GmailError("Gmail send result is incomplete; outcome may be unknown")
        return message_id(result.get("id"))

    async def trash(self, identifier):
        await self._request("POST", f"messages/{message_id(identifier)}/trash")

    async def mark_read(self, identifier):
        await self._request("POST", f"messages/{message_id(identifier)}/modify", json={"removeLabelIds": ["UNREAD"]})

    async def archive(self, identifier):
        await self._request("POST", f"messages/{message_id(identifier)}/modify", json={"removeLabelIds": ["INBOX"]})
