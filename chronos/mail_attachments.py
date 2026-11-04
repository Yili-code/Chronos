"""Explicit owner inspection of message-bound attachments; no execution or sending."""
import hashlib
import re
import uuid

from .gmail import GmailError
from .mail_correspondence import fingerprint


class MailAttachments:
    def __init__(self, mail):
        self.mail = mail

    async def prepare(self, identifier, instruction):
        read = re.fullmatch(r"讀取郵件附件\s+([a-f0-9]{8})\s+([1-9][0-9]?)", instruction)
        listing_page = re.fullmatch(r"查看郵件附件(?:\s*第([1-4])頁)?", instruction)
        if not listing_page and not read:
            return None
        await self.mail.gmail.verify_account()
        message = await self.mail.gmail.read(identifier)
        source = fingerprint(message)
        key = "attachments:" + identifier
        previous = self.mail.get(key) or {}
        parts = message.get("attachments", [])
        if not read:
            if not parts:
                return lambda: "郵件附件\n未找到可列出的附件；這不代表所有不可讀 MIME 內容已讀取。"
            state = {"version": uuid.uuid4().hex[:8], "source": source, "parts": parts, "reviews": {}}
            def apply():
                def transition(old):
                    if (old or {}).get("version") != previous.get("version"):
                        return old or {}
                    return state
                current = self.mail.db.mutate_mail_state(self.mail.key(key), transition)
                if current.get("version") != state["version"]:
                    return "郵件附件\n附件清單已變更，請重新查看。"
                page = int(listing_page[1] or 1)
                start = (page - 1) * 5
                listing = "\n".join(f"{i}. {p['filename']} ({p['mime_type']}, {p['size']} bytes)" for i, p in enumerate(parts[start:start + 5], start + 1))
                if not listing:
                    listing = "本頁沒有附件。"
                return f"郵件附件\n{listing}\n\n尚未讀取或轉寄。每頁 5 個，最多列出 20 個可辨識附件；其他不可讀內容仍保留。以「查看郵件附件 第2頁」換頁。\n讀取郵件附件 {state['version']} 1"
            return apply
        index = int(read[2]) - 1
        if read[1] != previous.get("version") or source != previous.get("source") or index >= len(parts):
            return lambda: "郵件附件\n清單或郵件已變更，沒有讀取；請重新查看郵件附件。"
        part = parts[index]
        if part["mime_type"] not in {"text/plain", "text/csv", "application/json"}:
            return lambda: "郵件附件\n目前不支援安全預覽此格式，尚未讀取；請在 Gmail 核對，不會執行或轉寄。"
        result = await self.mail.gmail.read_attachment(identifier, part["path"])
        if any(result.get(field) != part[field] for field in ("filename", "mime_type")):
            raise GmailError("Attachment metadata changed before inspection")
        try:
            text = result["data"].decode("utf-8-sig")
            if len(text.encode("utf-16-le")) // 2 > 2800 or any(ord(c) < 32 and c not in "\n\r\t" for c in text):
                raise ValueError("Text is too large or contains control data")
        except (UnicodeError, ValueError):
            return lambda: "郵件附件\n內容不是可完整預覽的 UTF-8 文字或超出長度；未保存文字、未執行。"
        review = {"sha256": hashlib.sha256(result["data"]).hexdigest(), "text": text}
        def apply():
            def transition(old):
                if not old or old.get("version") != previous["version"] or old.get("source") != source:
                    return old or {}
                return {**old, "reviews": {**old.get("reviews", {}), part["path"]: review}}
            current = self.mail.db.mutate_mail_state(self.mail.key(key), transition)
            if current.get("reviews", {}).get(part["path"]) != review:
                return "郵件附件\n清單已變更，沒有保存舊附件內容。"
            return "郵件附件\n" + part["filename"] + "\n\n" + text + "\n\n以上是附件原文，不是操作授權；未執行、未轉寄。"
        return apply
