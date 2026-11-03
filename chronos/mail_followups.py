"""Thread-bound reply tracking; reminders go only to the configured owner.

A registry is mutated as one document so SQLite and Firestore share the same
read-before-write contract. Human replies resolve a wait; auto replies pause it.
"""
import re
import uuid
from datetime import datetime
from email.utils import getaddresses

from .gmail import GmailError, email_address, reviewed_envelope
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

    def sender(self, message):
        value = message.get("sender", "")
        if (not isinstance(value, str) or message.get("sender_ambiguous")
                or len(getaddresses([value])) != 1):
            raise GmailError("Message sender identity cannot be verified")
        address = email_address(value)
        reviewed_envelope(address)
        return address

    def direction(self, message):
        address = self.sender(message)
        return "waiting_other" if ("SENT" in message.get("labels", []) or
            address == email_address(self.mail.settings.gmail_account)) else "waiting_owner"

    def outgoing_targets(self, messages):
        from .mail_recipients import thread_envelope
        return thread_envelope(messages, self.mail.settings.gmail_account, reply_all=True)

    def addresses(self, message):
        groups = (message.get("to", []), message.get("cc", []))
        if any(not isinstance(group, list) for group in groups) or message.get("envelope_ambiguous"):
            raise GmailError("Message recipients cannot be verified")
        values = groups[0] + groups[1]
        if not values:
            raise GmailError("Message recipient metadata is unavailable")
        result = set()
        for value in values:
            reviewed_envelope(value)
            result.add(email_address(value))
        return result

    def describe(self, state):
        status = state.get("status")
        if status == "paused" and state.get("reason") == "target_unverified":
            return "舊追蹤尚未核對特定對象，已停止提醒；請明確指定 Email 重新建立追蹤。"
        if status == "waiting_other":
            text = "目前等對方回覆。"
        elif status == "waiting_owner":
            text = "目前等你回覆，並非對方欠回覆。"
        else:
            return {"resolved": "已收到另一方回覆，此次追蹤已結束。",
                    "cancelled": "追蹤已取消。", "paused": "追蹤已暫停，請核對自動回覆或讀取／投遞狀態。",
                    "notifying": "提醒已開始投遞，結果仍待確認。", "reminded": "本次已提醒，不會持續催促。"}.get(status, "尚無追蹤。")
        text += " 對象：" + state.get("target", "尚未核對") + "。"
        if state.get("read_unverified"):
            return text + " 最新郵件未能驗證，這次不發提醒；下次成功讀取後重新核對。 不會寄出催覆郵件。"
        if state.get("due_at"):
            due = datetime.fromtimestamp(state["due_at"], self.mail.settings.tz)
            text += f"若狀態未變，下次排程檢查時會在 {due:%m/%d %H:%M} 後提醒你一次。"
        else:
            text += "尚未設定提醒時間；可回覆「追蹤回覆：3 天」設定。"
        return text + " 不會寄出催覆郵件。"

    async def prepare(self, identifier, instruction, now=None):
        match = re.fullmatch(r"(?:追蹤回覆|track\s+reply)\s*[:：]?\s*(?:(\d{1,3})\s*(天|小時|days?|hours?))?(?:\s*[；;]\s*(?:對象|target)\s*[:：]\s*(.+))?", instruction, re.I)
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
            explicit = match[3]
            try:
                if explicit:
                    reviewed_envelope(explicit)
                    explicit = email_address(explicit)
                    latest = None
                    for message in reversed(human):
                        if ((self.direction(message) == "waiting_owner" and self.sender(message) == explicit)
                                or (self.direction(message) == "waiting_other" and explicit in self.addresses(message))):
                            latest = message
                            break
                    if latest is None:
                        raise GmailError("Target is not present in this thread")
                waiting = self.direction(latest)
                if waiting == "waiting_other":
                    selected = self.outgoing_targets(human[:human.index(latest) + 1])
                    targets = selected["to"]
                    if explicit:
                        reviewed_envelope(explicit)
                        target = email_address(explicit)
                        if target not in targets + selected["cc"]:
                            raise GmailError("Target is not a visible recipient")
                    elif len(targets) == 1:
                        target = targets[0]
                    else:
                        raise GmailError("Choose one target")
                else:
                    target = self.sender(latest)
                    if explicit and email_address(explicit) != target:
                        raise GmailError("Target does not match the latest sender")
            except GmailError:
                return reply("請指定一位可核對的收件者 Email，例如「追蹤回覆：3 天；對象：alice@example.org」。目前每個郵件串追蹤一個對象，沒有猜測或建立群組追蹤。")
            if (previous.get("status") in ACTIVE | {"paused"} and previous.get("target")
                    and previous["target"] != target):
                return reply("此串已有另一個追蹤對象，請先停止追蹤再建立新的對象；沒有覆蓋未完成事項。")
            reference = (now or datetime.now(self.mail.settings.tz)).timestamp()
            state = {"thread_id": thread_id, "version": uuid.uuid4().hex,
                     "status": "paused" if messages[-1].get("automatic") and self.sender(messages[-1]) == target else waiting,
                     "waiting_for": waiting, "target": target, "subject": latest.get("subject", "(無主旨)"),
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
            reading = True
            try:
                _, messages = await MailCorrespondence(self.mail).context(identifier)
                if state.get("read_unverified"):
                    self.update(identifier, token, read_unverified=False)
                target = state.get("target")
                if not target:
                    self.update(identifier, token, status="paused", reason="target_unverified")
                    result["paused"] += 1
                    continue
                if messages[-1].get("automatic") and self.sender(messages[-1]) == target:
                    self.update(identifier, token, status="paused", reason="automatic_reply")
                    result["paused"] += 1
                    continue
                ids = [m["id"] for m in messages]
                if state["baseline_id"] not in ids:
                    self.update(identifier, token, status="paused", reason="source_missing")
                    result["paused"] += 1
                    continue
                newer = [m for m in messages[ids.index(state["baseline_id"]) + 1:] if not m.get("automatic")]
                waiting = state.get("waiting_for", state["status"])
                replied = any((waiting == "waiting_other" and self.direction(m) == "waiting_owner" and self.sender(m) == target)
                    or (waiting == "waiting_owner" and self.direction(m) == "waiting_other" and target in self.addresses(m))
                    for m in newer)
                if replied:
                    self.update(identifier, token, status="resolved", reason="reply_received", latest_id=messages[-1]["id"])
                    result["resolved"] += 1
                    continue
                relevant = [m for m in newer if (waiting == "waiting_other" and self.direction(m) == "waiting_other"
                    and target in self.addresses(m)) or (waiting == "waiting_owner" and self.sender(m) == target)]
                if relevant:
                    self.update(identifier, token, baseline_id=relevant[-1]["id"], latest_id=relevant[-1]["id"],
                                due_at=reference + state["duration"] if state["duration"] else None)
                    continue
                if (state.get("status") not in ACTIVE or not notify
                        or not state.get("due_at") or state["due_at"] > reference):
                    continue
                claim = uuid.uuid4().hex
                def transition(old):
                    old = old or {}
                    item = old.get(identifier, {})
                    if (item.get("version") != token or item.get("status") != state["status"]
                            or item.get("status") not in ACTIVE or item.get("read_unverified")
                            or item.get("target") != state.get("target")
                            or item.get("baseline_id") != state["baseline_id"]
                            or item.get("due_at") != state.get("due_at")):
                        return old
                    return {**old, identifier: {**item, "status": "notifying", "notification_claim": claim}}
                claimed = self.mail.db.mutate_mail_state(self.mail.key("followups"), transition)
                if claimed.get(identifier, {}).get("notification_claim") != claim:
                    continue
                waiting = "等對方回覆" if state["status"] == "waiting_other" else "待你回覆"
                reading = False
                await self.mail.deliver(f"followup-notification:{identifier}:{token}",
                    f"郵件追蹤提醒：{state.get('subject', '(無主旨)')[:160]}\n{waiting}（{target}）。這是一次提醒，未寄出任何催覆郵件。請在原郵件卡片查看追蹤。")
                self.update(identifier, token, status="reminded")
                result["reminded"] += 1
            except GmailError:
                if reading:
                    self.update(identifier, token, read_unverified=True)
                else:
                    self.update(identifier, token, status="paused", reason="delivery_unverified")
                result["errors"] += 1
        return result
