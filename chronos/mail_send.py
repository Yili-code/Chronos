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
        from .mail_recipients import envelope, transport_options, preferences_allow
        _, messages = await MailCorrespondence(self.mail).context(identifier)
        if fingerprint(messages) != state["source"]:
            return None
        selected = envelope(state)
        if not preferences_allow(self.mail, selected):
            return None
        options = {"expected_envelope": selected} if transport_options(state) else {}
        native = await self.mail.gmail.read_plain_draft(state["gmail_draft_id"], **options)
        normalized = lambda text: text.replace("\r\n", "\n").rstrip("\n")
        if (native.get("recipient") != state["recipient"] or native.get("thread_id") != state["thread_id"]
                or native.get("subject") != state["subject"] or native.get("parent_id") != state["parent_id"]
                or normalized(native.get("body", "")) != normalized(state["body"])):
            return None
        if (native.get("to", [native.get("recipient")]) != selected["to"]
                or native.get("cc", []) != selected["cc"] or native.get("bcc", []) != selected["bcc"]):
            return None
        from .mail_meeting_context import verify as verify_meeting
        if not await verify_meeting(self.mail, identifier, state):
            return None
        return fingerprint(native)

    async def prepare(self, identifier, instruction):
        review = re.fullmatch(r"(?:準備寄出回覆草稿\s+|prepare_send_)([a-f0-9]{8})", instruction)
        confirm = re.fullmatch(r"(?:確認寄出回覆\s+|send_reply_)([a-f0-9]{8})", instruction)
        cancel_callback = re.fullmatch(r"stop_send_([a-f0-9]{8})", instruction)
        cancel = instruction == "取消寄送回覆" or bool(cancel_callback)
        if not (review or confirm or cancel):
            return None
        state = self.mail.get("reply:" + identifier) or {}
        if cancel:
            if cancel_callback and state.get("send_token") != cancel_callback[1]:
                return text_result("這個寄送確認已過期，沒有取消其他版本或撤回郵件。")
            if state.get("status") not in {"send_review", "sending", "send_uncertain", "sent", "gmail_draft"}:
                return text_result("目前沒有待寄送確認；沒有修改或寄出郵件。")
            def apply():
                def transition(old):
                    if (old and old.get("version") == state.get("version") and old.get("status") == "send_review"
                            and old.get("send_token") == state.get("send_token")):
                        return {**old, "status": "gmail_draft", "send_token": None}
                    return old or {}
                current = self.mail.db.mutate_mail_state(self.mail.key("reply:" + identifier), transition)
                if current.get("status") == "send_review":
                    return "回覆草稿\n寄送確認已變更，沒有取消其他確認；請使用最新預覽。"
                if current.get("status") in {"sending", "send_uncertain", "sent"}:
                    return "回覆草稿\n寄送已開始或已有結果，不能宣稱撤回；請核對寄件備份。"
                return "回覆草稿\n寄送確認已取消，Gmail 草稿保留。"
            return apply
        if state.get("status") in {"sending", "send_uncertain", "sent"}:
            return text_result(MailCorrespondence(self.mail).preview(state))
        if review:
            if review[1] != state.get("version") or state.get("status") not in {"review", "gmail_draft", "send_review"}:
                return text_result("請先儲存並選擇最新 Gmail 草稿；尚未寄出。")
            if state["status"] == "review":
                preparation = await MailCorrespondence(self.mail).save(identifier, state["version"])
                state = self.mail.get("reply:" + identifier) or {}
                if state.get("status") != "gmail_draft":
                    return preparation
            verified = await self.verify(identifier, state)
            if not verified:
                return text_result("郵件串、相關會面、聯絡偏好或 Gmail 草稿已變更，寄送已阻擋；請核對後重新確認。")
            token = uuid.uuid4().hex[:8]
            from .mail_recipients import envelope
            selected = envelope(state)
            roles = "\n".join(label + "：" + ", ".join(selected[role]) for role, label in
                (("to", "將寄給"), ("cc", "CC"), ("bcc", "BCC（僅你可見的審查名單）")) if selected[role])
            def apply():
                current = self.update(identifier, state["version"], state["status"], status="send_review",
                    send_token=token, send_snapshot=verified, send_expires=time.time() + 900)
                if current.get("send_token") != token:
                    return "回覆草稿\n狀態已變更；尚未開始寄送，請先查看草稿。"
                text = (f"回覆草稿\n{roles}\n主旨：{state['subject']}\n\n{state['body']}\n\n"
                        f"這會對外寄出上面核對的內容，尚未寄出。若內容、對象都正確，回覆：確認寄出回覆 {token}\n"
                        "確認碼 15 分鐘內有效；寄出內容以上面預覽為準。修改後請取消本次確認並重新草擬。")
                return {"messages": [{"text": text, "mail_actions": [
                    {"kind": "send_reply", "token": token}, {"kind": "stop_send", "token": token}]}]}
            return apply
        if (state.get("status") != "send_review" or confirm[1] != state.get("send_token")
                or state.get("send_expires", 0) <= time.time()):
            return text_result("寄送確認已失效；沒有寄出郵件。")
        verified = await self.verify(identifier, state)
        if not verified or verified != state["send_snapshot"]:
            self.update(identifier, state["version"], "send_review", status="gmail_draft", send_token=None)
            return text_result("草稿、相關會面、聯絡偏好或郵件串在確認前變更，寄送已阻擋；請重新核對。")
        claim = uuid.uuid4().hex
        from .mail_recipients import envelope, transport_options, preferences_allow
        selected = envelope(state)
        def transition(old):
            if (old and old.get("version") == state["version"] and old.get("status") == "send_review"
                    and old.get("send_token") == confirm[1] and old.get("send_expires", 0) > time.time()):
                from .mail_meeting_context import matches as meeting_matches
                if not preferences_allow(self.mail, selected) or not meeting_matches(self.mail, identifier, old):
                    return {**old, "status": "gmail_draft", "send_token": None}
                return {**old, "status": "sending", "send_claim": claim}
            return old or {}
        current = self.mail.db.mutate_mail_state(self.mail.key("reply:" + identifier), transition)
        if current.get("send_claim") != claim:
            return text_result("確認或聯絡偏好已變更；這次沒有提交寄送。")
        try:
            sent_id = await self.mail.gmail.send_reviewed_draft(state["gmail_draft_id"], state["thread_id"],
                state["recipient"], state["subject"], state["body"], state["parent_id"], state["references"], **transport_options(state))
        except GmailError as error:
            definite = error.status in {400, 401, 403, 429}
            self.update(identifier, state["version"], "sending", status="gmail_draft" if definite else "send_uncertain", send_token=None)
            return text_result("Gmail 拒絕寄送，未確認寄出；請處理權限或額度後重新核對。" if definite
                else "寄送結果不確定；請先核對 Gmail 寄件備份，不會自動重寄，也不能宣稱未寄出。")
        self.update(identifier, state["version"], "sending", status="sent", sent_message_id=sent_id, send_token=None)
        return text_result("Gmail 已接受寄送並回傳 SENT 記錄；這不代表收件者已讀或已確認。")
