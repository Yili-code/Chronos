"""Explicit recipient roles; no AI-selected addresses or hidden BCC expansion."""
import re
import time
import uuid
from email.utils import getaddresses

from .gmail import GmailError, email_address, reviewed_envelope


def envelope(state):
    return reviewed_envelope(state["recipient"], to=state.get("to"), cc=state.get("cc", []), bcc=state.get("bcc", []))


def transport_options(state):
    selected = envelope(state)
    return selected if len(selected["to"]) > 1 or selected["cc"] or selected["bcc"] else {}


def preferences_allow(mail, selected):
    addresses = {address for group in selected.values() for address in group}
    contacts = (mail.get("contacts") or {}).get("items", {})
    return not any(addresses.intersection(c.get("emails", [])) and c.get("do_not_contact") for c in contacts.values())


def thread_envelope(messages, owner, reply_all=False):
    latest = messages[-1]
    if latest.get("sender_ambiguous") or (reply_all and latest.get("envelope_ambiguous")):
        raise GmailError("Message recipient headers are ambiguous", 400)
    owned = {owner.strip().lower()} | {email_address(m.get("sender", "")).lower() for m in messages if "SENT" in m.get("labels", [])}
    sender = email_address(latest.get("sender", ""))
    if "SENT" in latest.get("labels", []):
        targets = [address for address in latest.get("to", []) if address.lower() not in owned]
    elif sender.lower() not in owned:
        targets = [sender]
        if reply_all:
            targets += [address for address in latest.get("to", []) if address.lower() not in owned]
    else:
        raise GmailError("The latest message does not identify an external reply target", 400)
    targets = list(dict.fromkeys(targets))
    if not targets or (not reply_all and len(targets) != 1):
        raise GmailError("Select a single target or explicitly request Reply All", 400)
    copied = list(dict.fromkeys(address for address in latest.get("cc", [])
        if address.lower() not in owned and address not in targets)) if reply_all else []
    return reviewed_envelope(targets[0], to=targets, cc=copied)


class MailRecipients:
    def __init__(self, mail):
        self.mail = mail

    def prepare(self, identifier, instruction):
        match = re.fullmatch(r"設定回覆收件者\s+([a-f0-9]{8})\s*[:：]\s*(.+)", instruction, re.S)
        if not match:
            return None
        from .mail_correspondence import MailCorrespondence, text_result
        state = self.mail.get("reply:" + identifier) or {}
        if state.get("version") != match[1] or state.get("status") != "review":
            return text_result("請先準備並選擇最新本機草稿；已保存至 Gmail 或正在寄送的版本不會被改寫。")
        if re.search(r"[\r\n]", match[2]):
            return text_result("收件者設定請使用單行 To／CC／BCC 與分號分隔，不能夾帶換行標頭；沒有修改。")
        selected = envelope(state)
        seen = set()
        try:
            for part in re.split(r"[；;]", match[2]):
                pair = re.split(r"[:：]", part.strip(), maxsplit=1)
                if len(pair) != 2 or pair[0].strip().lower() not in {"to", "cc", "bcc"}:
                    raise ValueError("Explicit roles required")
                role, value = pair[0].strip().lower(), pair[1].strip()
                if role in seen:
                    raise ValueError("Repeated role")
                seen.add(role)
                selected[role] = [email_address(address) for _, address in getaddresses([value]) if address]
            selected = reviewed_envelope(selected["to"][0] if selected["to"] else "", **selected)
        except (ValueError, GmailError):
            return text_result("收件者格式、重複角色或數量不符；請明確列出 To、CC、BCC 的 Email，沒有修改。")
        if not preferences_allow(self.mail, selected):
            return text_result("名單中有不主動聯絡的對象，請先核對聯絡偏好；沒有修改收件者。")
        token = uuid.uuid4().hex[:8]
        updated = {**state, **selected, "recipient": selected["to"][0], "version": token, "created_at": time.time()}
        preview = MailCorrespondence(self.mail).preview(updated)
        if len(preview.encode("utf-16-le")) // 2 > 3600:
            return text_result("收件者與正文超過完整預覽長度，請先縮短草稿後再設定；沒有截斷或修改名單。")
        def apply():
            def transition(old):
                if not old or old.get("version") != state["version"] or old.get("status") != "review":
                    return old or {}
                if not preferences_allow(self.mail, selected):
                    return old
                return updated
            current = self.mail.db.mutate_mail_state(self.mail.key("reply:" + identifier), transition)
            if current.get("version") != token:
                return "回覆草稿\n草稿或聯絡偏好已變更；沒有改寫名單，請使用最新版本。"
            return MailCorrespondence(self.mail).presentation(updated, preview)
        return apply
