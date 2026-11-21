"""Account-aware Gmail browser links and legacy task source compatibility."""
import re
from urllib.parse import quote, unquote


def mail_url(mail, account):
    base = "https://mail.google.com/mail/?ui=2&authuser=" + quote(account, safe="")
    message = str(mail.get("rfc_message_id") or "").strip().strip("<>")
    if message and not any(c.isspace() for c in message) and "@" in message:
        return base + "#search/" + quote("in:anywhere rfc822msgid:" + message, safe="")
    return base + "#all/" + quote(str(mail.get("thread_id") or mail["id"]), safe="")


def source_url(task):
    source = (task.get("timing") or {}).get("source_text") or ""
    match = re.search(r"https://mail\.google\.com/mail/[^\s<>]+", source)
    if not match:
        return None
    url = match.group().rstrip(".,")
    legacy = re.fullmatch(r"https://mail\.google\.com/mail/u/([^/]+)/#all/([A-Za-z0-9_-]+)", url)
    if legacy:
        return mail_url({"id": legacy[2]}, unquote(legacy[1]))
    return url


def task_caption(task, limit=96):
    title = re.sub(r"^Read email:\s*", "郵件閱讀｜", task["title"], flags=re.IGNORECASE)
    return title if len(title) <= limit else title[:limit - 1].rstrip() + "…"
