"""Thread-bound reply tracking; reminders go only to the configured owner.

A registry is mutated as one document so SQLite and Firestore share the same
read-before-write contract. Human replies resolve a wait; auto replies pause it.
"""
import re
import uuid
from datetime import datetime
from email.utils import parseaddr

from .gmail import GmailError
from .mail_correspondence import MailCorrespondence


ACTIVE = {"waiting_other", "waiting_owner"}


def reply(text):
    return lambda: "郵件追蹤\n" + text


class MailFollowups:
    def __init__(self, workflow):
        self.mail = workflow

    def registry(self):
        return self.mail.get("followups") or {}

    def update(self, identifier, token, **values):
        def transition(old):
            old = old or {}
            item = old.get(identifier)
            if not item or item.get("version") != token:
                return old
            return {**old, identifier: {**item, **values}}
        return self.mail.db.mutate_mail_state(self.mail.key("followups"), transition)

    def direction(self, message):
        address = parseaddr(message.get("sender", ""))[1].lower()
        if not address:
            raise GmailError("Message sender is unavailable; reply state cannot be verified")
        return "waiting_other" if ("SENT" in message.get("labels", []) or
            address == self.mail.settings.gmail_account.strip().lower()) else "waiting_owner"

    def describe(self, state):
        status = state.get("status")
        if status == "waiting_other":
            text = "目前等對方回覆。"
        elif status == "waiting_owner":
            text = "目前等你回覆，並非對方欠回覆。"
        else:
            return {"resolved": "已收到另一方回覆，此次追蹤已結束。",
                    "cancelled": "追蹤已取消。", "paused": "追蹤已暫停，請核對自動回覆或讀取／投遞狀態。",
                    "notifying": "提醒已開始投遞，結果仍待確認。", "reminded": "本次已提醒，不會持續催促。"}.get(status, "尚無追蹤。")
        if state.get("due_at"):
            due = datetime.fromtimestamp(state["due_at"], self.mail.settings.tz)
            text += f"若狀態未變，下次排程檢查時會在 {due:%m/%d %H:%M} 後提醒你一次。"
        else:
            text += "尚未設定提醒時間；可回覆「追蹤回覆：3 天」設定。"
        return text + " 不會寄出催覆郵件。"

    async def prepare(self, identifier, instruction, now=None):
        match = re.fullmatch(r"(?:追蹤回覆|track\s+reply)\s*[:：]?\s*(?:(\d{1,3})\s*(天|小時|days?|hours?))?", instruction, re.I)
        if match:
            duration = int(match[1] or 0) * (86400 if match[2] and match[2].lower() in {"天", "day", "days"} else 3600)
            if match[1] and not 3600 <= duration <= 90 * 86400:
                return reply("提醒間隔須介於 1 小時與 90 天。")
            thread_id, messages = await MailCorrespondence(self.mail).context(identifier)
            human = [m for m in messages if not m.get("automatic")]
            if not human:
                return reply("只有自動郵件，無法確認誰需要回覆；尚未建立追蹤。")
            previous = self.registry().get(identifier, {})
            latest = human[-1]
            reference = (now or datetime.now(self.mail.settings.tz)).timestamp()
            state = {"thread_id": thread_id, "version": uuid.uuid4().hex,
                     "status": "paused" if messages[-1].get("automatic") else self.direction(latest),
                     "waiting_for": self.direction(latest), "subject": latest.get("subject", "(無主旨)"),
                     "baseline_id": latest["id"], "latest_id": messages[-1]["id"],
                     "due_at": reference + duration if duration else None, "duration": duration,
                     "created_at": reference}
            def apply():
                def transition(old):
                    old = old or {}
                    if old.get(identifier, {}).get("version") != previous.get("version"):
                        return old
                    if identifier not in old and len(old) >= 100:
                        return old
                    return {**old, identifier: state}
                current = self.mail.db.mutate_mail_state(self.mail.key("followups"), transition)
                if current.get(identifier, {}).get("version") != state["version"]:
                    return "郵件追蹤\n追蹤已被另一個操作更新，或已達 100 個追蹤上限；請先查看狀態。"
                return "郵件追蹤\n" + self.describe(state)
            return apply
        if instruction.lower() in {"查看郵件追蹤", "查看追蹤", "show reply tracking"}:
            await self.refresh(now, only=identifier, notify=False)
            return reply(self.describe(self.registry().get(identifier, {})))
        if instruction.lower() in {"取消郵件追蹤", "停止追蹤", "cancel reply tracking"}:
            def cancel():
                def transition(old):
                    old = old or {}
                    if identifier not in old:
                        return old
                    return {**old, identifier: {**old[identifier], "status": "cancelled", "version": uuid.uuid4().hex}}
                old = self.registry().get(identifier, {})
                self.mail.db.mutate_mail_state(self.mail.key("followups"), transition)
                return "郵件追蹤\n已停止後續提醒。" + ("當次提醒已開始投遞，仍可能送達。" if old.get("status") == "notifying" else "")
            return cancel
        return None

    async def refresh(self, now=None, *, only=None, notify=True):
        reference = (now or datetime.now(self.mail.settings.tz)).timestamp()
        result = {"checked": 0, "resolved": 0, "paused": 0, "reminded": 0, "errors": 0}
        for identifier, state in self.registry().items():
            if (only is not None and identifier != only) or state.get("status") not in ACTIVE | {"paused"}:
                continue
            token = state["version"]
            result["checked"] += 1
            try:
                _, messages = await MailCorrespondence(self.mail).context(identifier)
                if messages[-1].get("automatic"):
                    self.update(identifier, token, status="paused", reason="automatic_reply")
                    result["paused"] += 1
                    continue
                ids = [m["id"] for m in messages]
                if state["baseline_id"] not in ids:
                    self.update(identifier, token, status="paused", reason="source_missing")
                    result["paused"] += 1
                    continue
                newer = [m for m in messages[ids.index(state["baseline_id"]) + 1:] if not m.get("automatic")]
                if any(self.direction(m) != state.get("waiting_for", state["status"]) for m in newer):
                    self.update(identifier, token, status="resolved", reason="reply_received", latest_id=messages[-1]["id"])
                    result["resolved"] += 1
                    continue
                if newer:
                    self.update(identifier, token, baseline_id=newer[-1]["id"], latest_id=newer[-1]["id"],
                                due_at=reference + state["duration"] if state["duration"] else None)
                    continue
                if (state.get("status") not in ACTIVE or not notify
                        or not state.get("due_at") or state["due_at"] > reference):
                    continue
                claim = uuid.uuid4().hex
                def transition(old):
                    old = old or {}
                    item = old.get(identifier, {})
                    if item.get("version") != token or item.get("status") not in ACTIVE:
                        return old
                    return {**old, identifier: {**item, "status": "notifying", "notification_claim": claim}}
                claimed = self.mail.db.mutate_mail_state(self.mail.key("followups"), transition)
                if claimed.get(identifier, {}).get("notification_claim") != claim:
                    continue
                waiting = "等對方回覆" if state["status"] == "waiting_other" else "待你回覆"
                await self.mail.deliver(f"followup-notification:{identifier}:{token}",
                    f"郵件追蹤提醒：{state.get('subject', '(無主旨)')[:160]}\n{waiting}。這是一次提醒，未寄出任何催覆郵件。請在原郵件卡片查看追蹤。")
                self.update(identifier, token, status="reminded")
                result["reminded"] += 1
            except GmailError:
                self.update(identifier, token, status="paused", reason="read_or_delivery_unverified")
                result["errors"] += 1
        return result
