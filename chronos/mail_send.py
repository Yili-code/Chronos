"""Explicit owner confirmation of immutable reviewed mail; never automatic send."""
import re
import time
import uuid

from .gmail import GmailError
from .mail_correspondence import MailCorrespondence, fingerprint, text_result


class MailSend:
    def __init__(self, mail):
        self.mail = mail

    def update(self, identifier, version, expected, **values):
        return self.mail.db.mutate_mail_state(self.mail.key("reply:" + identifier), lambda old:
            {**old, **values} if old and old.get("version") == version and old.get("status") == expected else (old or {}))

    async def verify(self, identifier, state):
        _, messages = await MailCorrespondence(self.mail).context(identifier)
        if fingerprint(messages) != state["source"]:
            return None
        native = await self.mail.gmail.read_plain_draft(state["gmail_draft_id"])
        normalized = lambda text: text.replace("\r\n", "\n").rstrip("\n")
        if (native.get("recipient") != state["recipient"] or native.get("thread_id") != state["thread_id"]
                or native.get("subject") != state["subject"] or native.get("parent_id") != state["parent_id"]
                or normalized(native.get("body", "")) != normalized(state["body"])):
            return None
        return fingerprint(native)

    async def prepare(self, identifier, instruction):
        review = re.fullmatch(r"準備寄出回覆草稿\s+([a-f0-9]{8})", instruction)
        confirm = re.fullmatch(r"確認寄出回覆\s+([a-f0-9]{8})", instruction)
        cancel = instruction == "取消寄送回覆"
        if not (review or confirm or cancel):
            return None
        state = self.mail.get("reply:" + identifier) or {}
        if cancel:
            if state.get("status") not in {"send_review", "sending", "send_uncertain", "sent", "gmail_draft"}:
                return text_result("目前沒有待寄送確認；沒有修改或寄出郵件。")
            def apply():
                current = self.update(identifier, state.get("version"), "send_review", status="gmail_draft", send_token=None)
                if current.get("status") in {"sending", "send_uncertain", "sent"}:
                    return "回覆草稿\n寄送已開始或已有結果，不能宣稱撤回；請核對寄件備份。"
                return "回覆草稿\n寄送確認已取消，Gmail 草稿保留。"
            return apply
        if state.get("status") in {"sending", "send_uncertain", "sent"}:
            return text_result(MailCorrespondence(self.mail).preview(state))
        if review:
            if review[1] != state.get("version") or state.get("status") not in {"gmail_draft", "send_review"}:
                return text_result("請先儲存並選擇最新 Gmail 草稿；尚未寄出。")
            verified = await self.verify(identifier, state)
            if not verified:
                return text_result("郵件串或 Gmail 草稿已變更，寄送已阻擋；請在 Gmail 核對後重新草擬。")
            token = uuid.uuid4().hex[:8]
            def apply():
                current = self.update(identifier, state["version"], state["status"], status="send_review",
                    send_token=token, send_snapshot=verified, send_expires=time.time() + 900)
                if current.get("send_token") != token:
                    return "回覆草稿\n狀態已變更；尚未開始寄送，請先查看草稿。"
                return (f"回覆草稿\n將寄給：{state['recipient']}\n主旨：{state['subject']}\n\n{state['body']}\n\n"
                        f"這會對外寄出上面核對的內容，尚未寄出。若內容、對象都正確，回覆：確認寄出回覆 {token}\n"
                        "確認碼 15 分鐘內有效；寄出內容以上面預覽為準。修改後請取消本次確認並重新草擬。")
            return apply
        if (state.get("status") != "send_review" or confirm[1] != state.get("send_token")
                or state.get("send_expires", 0) <= time.time()):
            return text_result("寄送確認已失效；沒有寄出郵件。")
        verified = await self.verify(identifier, state)
        if not verified or verified != state["send_snapshot"]:
            self.update(identifier, state["version"], "send_review", status="gmail_draft", send_token=None)
            return text_result("草稿或郵件串在確認前變更，寄送已阻擋；請重新核對。")
        claim = uuid.uuid4().hex
        def transition(old):
            if (old and old.get("version") == state["version"] and old.get("status") == "send_review"
                    and old.get("send_token") == confirm[1] and old.get("send_expires", 0) > time.time()):
                return {**old, "status": "sending", "send_claim": claim}
            return old or {}
        current = self.mail.db.mutate_mail_state(self.mail.key("reply:" + identifier), transition)
        if current.get("send_claim") != claim:
            return text_result("另一個操作已處理此確認，沒有再次寄出。")
        try:
            sent_id = await self.mail.gmail.send_reviewed_draft(state["gmail_draft_id"], state["thread_id"],
                state["recipient"], state["subject"], state["body"], state["parent_id"], state["references"])
        except GmailError as error:
            definite = error.status in {400, 401, 403, 429}
            self.update(identifier, state["version"], "sending", status="gmail_draft" if definite else "send_uncertain", send_token=None)
            return text_result("Gmail 拒絕寄送，未確認寄出；請處理權限或額度後重新核對。" if definite
                else "寄送結果不確定；請先核對 Gmail 寄件備份，不會自動重寄，也不能宣稱未寄出。")
        self.update(identifier, state["version"], "sending", status="sent", sent_message_id=sent_id, send_token=None)
        return text_result("Gmail 已接受寄送並回傳 SENT 記錄；這不代表收件者已讀或已確認。")
