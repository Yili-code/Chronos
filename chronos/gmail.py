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


def reviewed_envelope(recipient, *, to=None, cc=(), bcc=()):
    groups = {"to": [recipient] if to is None else to, "cc": cc, "bcc": bcc}
    result, seen = {}, set()
    for role, values in groups.items():
        if not isinstance(values, (list, tuple)):
            raise GmailError("Recipient groups must be explicit address lists", 400)
        result[role] = []
        for address in values:
            if (not isinstance(address, str) or not address.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in address)
                    or not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", address)
                    or parseaddr(address)[1] != address):
                raise GmailError("Invalid recipient address", 400)
            address = email_address(address)
            if address in seen:
                raise GmailError("Duplicate recipients require explicit correction", 400)
            seen.add(address)
            result[role].append(address)
    if not result["to"] or result["to"][0] != email_address(recipient) or len(seen) > 10:
        raise GmailError("Invalid or oversized recipient envelope", 400)
    try:
        if sum(len(value.encode("utf-16-le")) // 2 for value in seen) > 900:
            raise GmailError("Recipient list exceeds complete owner-preview capacity", 400)
    except UnicodeError:
        raise GmailError("Invalid recipient encoding", 400) from None
    return result


def decode_message(raw):
    labels = raw.get("labelIds", [])
    if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
        raise GmailError("Gmail returned invalid message labels")
    payload = raw.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    counts = {}
    for header in payload.get("headers", []):
        name = header["name"].lower()
        counts[name] = counts.get(name, 0) + 1
    to = [email_address(address) for _, address in getaddresses([headers.get("to", "")]) if address]
    cc = [email_address(address) for _, address in getaddresses([headers.get("cc", "")]) if address]
    sender_invalid = False
    try:
        reviewed_envelope(email_address(headers.get("from", "")))
        if headers.get("reply-to"):
            reviewed_envelope(email_address(headers["reply-to"]))
    except GmailError:
        sender_invalid = True
    plain, html = [], []
    attachments = []
    incomplete = False
    visited = 0
    encoded_size = 0

    def visit(part, depth=0, path="0"):
        nonlocal incomplete, visited, encoded_size
        if depth > 20 or visited >= 100:
            incomplete = True
            return
        visited += 1
        # Do not download or interpret attachments, including attached messages.
        mime = part.get("mimeType", "")
        if (part.get("filename") or part.get("body", {}).get("attachmentId")
                or mime == "message/rfc822"):
            incomplete = True
            if len(attachments) < 20:
                size = part.get("body", {}).get("size")
                attachments.append({"path": path, "filename": clean(part.get("filename") or "(內文區塊)", 160),
                    "mime_type": clean(mime, 100), "size": size if type(size) is int and 0 <= size <= 26214400 else None})
            return
        data = part.get("body", {}).get("data", "")
        if data and mime in {"text/plain", "text/html"}:
            encoded_size += len(data)
            if encoded_size > 131072:
                incomplete = True
                return
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
        children = part.get("parts", [])
        if not isinstance(children, list):
            incomplete = True
            return
        remaining = max(0, 100 - visited)
        if len(children) > remaining:
            incomplete = True
        for index, child in enumerate(children[:remaining]):
            visit(child, depth + 1, path + "." + str(index))

    visit(payload)
    plain_body = clean("\n".join(plain), 1000000)
    html_body = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", "\n".join(html), flags=re.S | re.I)
    html_body = clean(re.sub(r"<[^>]+>", " ", html_body), 1000000)
    normalized_body = plain_body or html_body
    alternative_body = html_body if plain_body and html_body != plain_body else ""
    return {
        "received_at": int(raw.get("internalDate", 0)) // 1000,
        "id": message_id(raw["id"]), "thread_id": raw.get("threadId", ""),
        "labels": labels, "subject": clean(headers.get("subject", "(無主旨)"), 240),
        "sender": clean(headers.get("from", ""), 240), "date": clean(headers.get("date", ""), 100),
        "snippet": clean(raw.get("snippet", ""), 600), "body": normalized_body[:12000],
        "body_truncated": len(normalized_body) > 12000 or len(alternative_body) > 12000,
        "alternative_body": alternative_body[:12000],
        "attachments": attachments,
        "body_incomplete": incomplete,
        "unsubscribe": bool(headers.get("list-unsubscribe")),
        "has_reply": bool(headers.get("in-reply-to") or headers.get("references")),
        "reply_to": headers.get("reply-to", ""),
        "rfc_message_id": headers.get("message-id", ""),
        "references": headers.get("references", ""),
        "to": to, "cc": cc,
        "sender_ambiguous": (sender_invalid or counts.get("from", 0) != 1 or counts.get("reply-to", 0) > 1
            or len(getaddresses([headers.get("from", "")])) != 1
            or (bool(headers.get("reply-to")) and len(getaddresses([headers["reply-to"]])) != 1)),
        "envelope_ambiguous": counts.get("to", 0) > 1 or counts.get("cc", 0) > 1,
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
        raw = await self._request("GET", f"messages/{message_id(identifier)}", params={"format": "full"})
        try:
            return decode_message(raw)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise GmailError("Gmail returned an invalid message") from None

    async def read_attachment(self, identifier, path):
        """Read one provider part in memory; never open URLs, files or executable content."""
        if (not isinstance(path, str) or not re.fullmatch(r"0(?:\.[0-9]{1,2}){0,20}", path)
                or any(int(index) >= 100 for index in path.split(".")[1:])):
            raise GmailError("Invalid attachment selection", 400)
        identifier = message_id(identifier)
        raw = await self._request("GET", f"messages/{identifier}", params={"format": "full"})
        try:
            if raw["id"] != identifier:
                raise ValueError("Message mismatch")
            part = raw["payload"]
            for index in path.split(".")[1:]:
                part = part["parts"][int(index)]
            body = part.get("body", {})
            declared = body.get("size", 0)
            if type(declared) is not int or declared < 0 or declared > 1048576:
                raise ValueError("Attachment size is unsupported")
            if body.get("attachmentId"):
                remote = body["attachmentId"]
                if not isinstance(remote, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,4096}", remote):
                    raise ValueError("Invalid provider attachment id")
                body = await self._request("GET", f"messages/{identifier}/attachments/{remote}")
            data = body.get("data", "")
            if not isinstance(data, str) or len(data) > 1398104:
                raise ValueError("Attachment exceeds read budget")
            decoded = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
            if len(decoded) > 1048576 or (declared and len(decoded) != declared):
                raise ValueError("Attachment size does not match metadata")
            return {"filename": clean(part.get("filename") or "(內文區塊)", 160),
                "mime_type": clean(part.get("mimeType", ""), 100), "data": decoded}
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise GmailError("Attachment could not be safely read; no content was executed") from None

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

    def reply_payload(self, thread_id, recipient, subject, body, parent_id, references="", *, to=None, cc=(), bcc=()):
        """Build immutable plain MIME for the explicitly reviewed envelope."""
        envelope = reviewed_envelope(recipient, to=to, cc=cc, bcc=bcc)
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
        message["To"], message["Subject"] = ", ".join(envelope["to"]), subject
        for role in ("cc", "bcc"):
            if envelope[role]:
                message[role.title()] = ", ".join(envelope[role])
        message["In-Reply-To"], message["References"] = parent_id, " ".join(ids)
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        return {"threadId": message_id(thread_id), "raw": raw}

    async def create_reply_draft(self, thread_id, recipient, subject, body, parent_id, references="", **envelope):
        payload = self.reply_payload(thread_id, recipient, subject, body, parent_id, references, **envelope)
        result = await self._request("POST", "drafts", json={"message": payload})
        if (not isinstance(result, dict) or not isinstance(result.get("message"), dict)
                or "DRAFT" not in result["message"].get("labelIds", [])
                or "SENT" in result["message"].get("labelIds", [])
                or result["message"].get("threadId") != thread_id):
            raise GmailError("Gmail draft result is incomplete; outcome may be unknown")
        message_id(result.get("id"))
        return result["id"]

    async def read_plain_draft(self, identifier, *, expected_envelope=None):
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
                    or len(mime.get_all("To", [])) != 1 or len(mime.get_all("Cc", [])) > 1 or len(mime.get_all("Bcc", [])) > 1
                    or any(name.lower().startswith("resent-") for name in mime.keys())):
                raise ValueError("Unsupported draft envelope")
            addresses = getaddresses([str(mime["To"])])
            if not addresses:
                raise ValueError("Ambiguous recipient")
            envelope = reviewed_envelope(email_address(addresses[0][1]),
                to=[email_address(address) for _, address in addresses],
                cc=[email_address(address) for _, address in getaddresses([str(mime.get("Cc", ""))]) if address],
                bcc=[email_address(address) for _, address in getaddresses([str(mime.get("Bcc", ""))]) if address])
            if expected_envelope is None:
                if len(addresses) != 1 or mime.get_all("Cc") or mime.get_all("Bcc"):
                    raise ValueError("Recipient expansion was not approved")
            elif envelope != expected_envelope:
                raise ValueError("Native recipients differ from approved recipients")
            return {"recipient": envelope["to"][0], **envelope, "subject": str(mime.get("Subject", "")),
                    "body": mime.get_content(), "parent_id": str(mime.get("In-Reply-To", "")),
                    "thread_id": message_id(message["threadId"]), "message_id": message_id(message["id"])}
        except (ValueError, TypeError, KeyError, AttributeError, LookupError):
            raise GmailError("Native draft is incomplete or has unsupported content; sending blocked") from None

    async def send_reviewed_draft(self, identifier, thread_id, recipient, subject, body, parent_id, references="", **envelope):
        payload = self.reply_payload(thread_id, recipient, subject, body, parent_id, references, **envelope)
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
