"""Explicit owner-maintained local contacts and sourced relationship notes.

This store does not claim Google Contacts synchronization or verified identity.
All operations use one versioned document for both database backends.
"""
import re
import json
import time
import uuid
from datetime import date, datetime
from email.utils import getaddresses

from .gmail import GmailError, email_address, reviewed_envelope


FIELDS = {"姓名": "name", "公司": "company", "職稱": "role"}


def answer(text):
    return lambda: "聯絡人\n" + text


class ContactRelationships:
    def __init__(self, mail):
        self.mail = mail

    def registry(self):
        return self.mail.get("contacts") or {"items": {}, "pending": {}}

    def mutate(self, fn):
        def transition(old):
            updated = fn(old or {"items": {}, "pending": {}})
            if len(json.dumps(updated, ensure_ascii=False).encode("utf-8")) > 700000:
                raise GmailError("Local contact storage capacity exceeded; no change was saved")
            return updated
        return self.mail.db.mutate_mail_state(self.mail.key("contacts"), transition)

    def source_address(self, identifier):
        source = (self.mail.get("message:" + identifier) or {}).get("mail", {})
        sender = source.get("sender", "")
        if not isinstance(sender, str) or source.get("sender_ambiguous") or len(getaddresses([sender])) != 1:
            return None
        address = email_address(sender)
        try:
            reviewed_envelope(address)
        except GmailError:
            return None
        return address

    def display(self, contact, page=1):
        addresses = contact["emails"][(page - 1) * 3:page * 3]
        return (f"{contact['name']} · {contact.get('company') or '公司未提供'}\n"
                f"Email（第 {page} 頁，共 {(len(contact['emails']) + 2) // 3} 頁）：{', '.join(addresses)}\n職稱：{contact.get('role') or '未提供'}\n"
                f"ID：{contact['id']}　版本：{contact['version']}\n"
                "此為 Chronos 本機紀錄，尚未同步 Google Contacts；Email 來源不代表身份已驗證。")

    def parse_fields(self, text):
        result = {}
        for index, part in enumerate(re.split(r"[；;]", text)):
            pair = re.split(r"[:：]", part.strip(), maxsplit=1)
            if len(pair) == 1 and index == 0:
                result["name"] = pair[0]
            elif len(pair) == 2 and pair[0].strip() in FIELDS:
                field = FIELDS[pair[0].strip()]
                if field in result:
                    return None
                result[field] = pair[1].strip()
            else:
                return None
        if any(not value or len(value) > 80 for value in result.values()):
            return None
        return result

    def prepare(self, identifier, instruction, now=None):
        reference = (now or datetime.now(self.mail.settings.tz)).astimezone(self.mail.settings.tz)
        create = re.fullmatch(r"(?:新增聯絡人|加入聯絡人|記住聯絡人)\s*[:：]\s*(.+)", instruction, re.S)
        if create:
            fields, address = self.parse_fields(create[1]), self.source_address(identifier)
            if (not fields or not fields.get("name") or not address
                    or address.lower() == self.mail.settings.gmail_account.strip().lower()):
                return answer("請指定姓名，並使用有明確寄件者的郵件卡片。例如：新增聯絡人：Alex；公司：Acme；職稱：工程師。")
            contact_id = uuid.uuid4().hex[:12]
            contact = {"id": contact_id, **fields, "emails": [address], "version": uuid.uuid4().hex[:8],
                       "source": {"kind": "owner_mail_card", "message_id": identifier},
                       "notes": [], "do_not_contact": False, "created_at": reference.isoformat()}
            def apply():
                def transition(old):
                    if (self.source_address(identifier) != address or len(old["items"]) >= 100
                            or any(address in c["emails"] for c in old["items"].values())):
                        return old
                    return {**old, "items": {**old["items"], contact_id: contact}}
                state = self.mutate(transition)
                if contact_id not in state["items"]:
                    return "聯絡人\n來源身份已變更、這個 Email 已有紀錄，或已達 100 人上限；沒有重複建立或自動合併。"
                duplicates = sum(c["name"] == fields["name"] for c in state["items"].values())
                return "聯絡人\n已建立本機紀錄。" + ("有同名紀錄，仍以不同 ID 分開。\n" if duplicates > 1 else "\n") + self.display(contact)
            return apply
        if instruction in {"查看聯絡人", "聯絡人資料"}:
            address = self.source_address(identifier)
            def view():
                if not address or self.source_address(identifier) != address:
                    return "聯絡人\n寄件者身份無法明確核對；沒有關聯其他聯絡人，請先在 Gmail 核對。"
                matches = [c for c in self.registry()["items"].values() if address in c["emails"]]
                return "聯絡人\n" + ("\n\n".join(self.display(c) for c in matches) if matches else "這封信的寄件者尚無本機聯絡人紀錄。")
            return view
        update = re.fullmatch(r"更新聯絡人\s+([a-f0-9]{12})\s+([a-f0-9]{8})\s*[:：]\s*(.+)", instruction, re.S)
        if update:
            contact_id, version = update[1], update[2]
            fields = self.parse_fields(update[3])
            if not fields:
                return answer("可更新姓名、公司、職稱；請明確列出欄位，不會猜測資料。")
            return self.change(contact_id, version, lambda old: {**old, **fields})
        remove = re.fullmatch(r"刪除聯絡人\s+([a-f0-9]{12})", instruction)
        merge = re.fullmatch(r"合併聯絡人\s+([a-f0-9]{12})\s+([a-f0-9]{12})", instruction)
        if remove or merge:
            ids = [remove[1]] if remove else [merge[1], merge[2]]
            items = self.registry()["items"]
            if len(set(ids)) != len(ids) or any(i not in items for i in ids):
                return answer("聯絡人 ID 不明或重複；沒有修改。")
            if merge:
                left, right = (items[i] for i in ids)
                conflicts = [key for key in ("name", "company", "role", "birthday", "cadence_days")
                             if left.get(key) and right.get(key) and left[key] != right[key]]
                if (conflicts or len(left.get("notes", []) + right.get("notes", [])) > 20
                        or len(left.get("changes", []) + right.get("changes", [])) > 10):
                    return answer("資料衝突或互動紀錄超過合併容量；請先明確更新資料，不會自動挑選或丟棄紀錄。")
            token = uuid.uuid4().hex[:8]
            pending = {"ids": ids, "versions": [items[i]["version"] for i in ids], "expires": time.time() + 600}
            def propose():
                def transition(old):
                    active = {k: v for k, v in old["pending"].items() if v["expires"] > time.time()}
                    if len(active) >= 20:
                        return old
                    return {**old, "pending": {**active, token: pending}}
                state = self.mutate(transition)
                if token not in state["pending"]:
                    return "聯絡人\n待確認操作已達上限，請稍後重試。"
                names = "、".join(items[i]["name"] + " (" + i + ")" for i in ids)
                return f"聯絡人\n待確認本機{'刪除' if remove else '合併'}：{names}\n確認後回覆：確認聯絡人操作 {token}\n尚未修改聯絡人，Google Contacts 不受影響。"
            return propose
        confirm = re.fullmatch(r"確認聯絡人操作\s+([a-f0-9]{8})", instruction)
        if confirm:
            token = confirm[1]
            def apply():
                succeeded = False
                def transition(old):
                    nonlocal succeeded
                    succeeded = False
                    pending = old["pending"].get(token)
                    if not pending or pending["expires"] <= time.time():
                        return old
                    ids = pending["ids"]
                    if any(i not in old["items"] or old["items"][i]["version"] != v for i, v in zip(ids, pending["versions"])):
                        return old
                    items = dict(old["items"])
                    if len(ids) == 2:
                        left, right = (items[i] for i in ids)
                        merged = {**right, **{k: v for k, v in left.items() if v not in (None, "", [])}}
                        merged.update(emails=list(dict.fromkeys(left["emails"] + right["emails"])),
                                      notes=left.get("notes", []) + right.get("notes", []),
                                      changes=left.get("changes", []) + right.get("changes", []),
                                      do_not_contact=left.get("do_not_contact", False) or right.get("do_not_contact", False),
                                      version=uuid.uuid4().hex[:8])
                        merged["sources"] = left.get("sources", [left["source"]]) + right.get("sources", [right["source"]])
                        items[ids[0]] = merged
                    del items[ids[-1]]
                    succeeded = True
                    return {"items": items, "pending": {k: v for k, v in old["pending"].items() if k != token}}
                self.mutate(transition)
                return "聯絡人\n已完成本機操作，Google Contacts 未變更。" if succeeded else "聯絡人\n確認已過期或資料版本已變更；沒有修改。"
            return apply
        note = re.fullmatch(r"記錄互動\s+([a-f0-9]{12})\s+([a-f0-9]{8})\s*[:：]\s*(.{1,200})", instruction, re.S)
        if note:
            def add(old):
                if len(old.get("notes", [])) >= 20:
                    return None
                return {**old, "notes": old.get("notes", []) + [{"text": note[3], "at": reference.isoformat(), "source": "owner"}]}
            return self.change(note[1], note[2], add)
        preference = re.fullmatch(r"聯絡偏好\s+([a-f0-9]{12})\s+([a-f0-9]{8})\s*[:：]\s*(不主動聯絡|允許聯絡|每\d{1,3}天|生日\d{2}-\d{2})", instruction)
        if preference:
            value = preference[3]
            if value.startswith("生日"):
                try:
                    date(2000, int(value[2:4]), int(value[5:7]))
                except ValueError:
                    return answer("生日日期無效；沒有修改，也不會推測生日。")
                patch = {"birthday": value[2:]}
            elif value.startswith("每"):
                days = int(value[1:-1])
                if not 7 <= days <= 365:
                    return answer("建議聯絡間隔為 7 至 365 天；不會自動發送訊息。")
                patch = {"cadence_days": days}
            else:
                patch = {"do_not_contact": value == "不主動聯絡"}
            return self.change(preference[1], preference[2], lambda old: {**old, **patch})
        suggestions = re.fullmatch(r"關係建議\s+([a-f0-9]{12})", instruction)
        if suggestions:
            contact = self.registry()["items"].get(suggestions[1])
            if not contact:
                return answer("找不到此 ID，沒有推測或關聯其他人。")
            if contact.get("do_not_contact"):
                return answer("這位聯絡人設定為不主動聯絡，不建議發送關懷訊息。")
            birthday = contact.get("birthday") == reference.strftime("%m-%d")
            notes = contact.get("notes", [])
            cadence = contact.get("cadence_days")
            elapsed = (reference - datetime.fromisoformat(notes[-1]["at"])).total_seconds() / 86400 if notes else None
            due = cadence is not None and elapsed is not None and elapsed >= cadence
            if birthday or due:
                return answer(f"依你提供的{'生日' if birthday else '互動紀錄與聯絡間隔'}，可以考慮聯繫 {contact['name']}。這只是建議，沒有寄出訊息。")
            return answer("目前沒有足夠的生日、互動日期或聯絡間隔依據，不會推測近況或安排聯繫。")
        addresses = re.fullmatch(r"聯絡方式\s+([a-f0-9]{12})(?:\s+第\s*(\d{1,2})\s*頁)?", instruction)
        if addresses:
            contact = self.registry()["items"].get(addresses[1])
            page = int(addresses[2] or 1)
            if not contact or not 1 <= page <= (len(contact["emails"]) + 2) // 3:
                return answer("找不到此聯絡人或頁碼，沒有修改資料。")
            return answer(self.display(contact, page))
        history = re.fullmatch(r"關係紀錄\s+([a-f0-9]{12})(?:\s+第\s*(\d{1,2})\s*頁)?", instruction)
        if history:
            contact = self.registry()["items"].get(history[1])
            if not contact:
                return answer("找不到此 ID，沒有推測或關聯其他人。")
            page = int(history[2] or 1)
            all_notes = list(reversed(contact.get("notes", [])))
            if not 1 <= page <= max(1, (len(all_notes) + 4) // 5):
                return answer("互動紀錄沒有此頁，沒有修改資料。")
            notes = "\n".join(n["at"] + "：" + n["text"] for n in all_notes[(page - 1) * 5:page * 5]) or "尚無使用者提供的互動紀錄。"
            return answer(f"{contact['name']} · {contact['id']}\n互動紀錄第 {page} 頁，共 {max(1, (len(all_notes) + 4) // 5)} 頁\n聯絡偏好：" + ("不主動聯絡" if contact.get("do_not_contact") else "尚無拒絕聯絡設定")
                          + "\n生日：" + contact.get("birthday", "未提供") + "\n" + notes)
        return None

    def change(self, contact_id, version, change):
        def apply():
            succeeded = False
            def transition(old):
                nonlocal succeeded
                succeeded = False
                contact = old["items"].get(contact_id)
                if not contact or contact["version"] != version:
                    return old
                updated = change(contact)
                if updated is None:
                    return old
                changed = {key: contact.get(key) for key in ("name", "company", "role", "birthday", "cadence_days", "do_not_contact")
                           if contact.get(key) != updated.get(key)}
                if changed:
                    if len(contact.get("changes", [])) >= 10:
                        return old
                    updated = {**updated, "changes": contact.get("changes", []) + [{
                        "at": datetime.now(self.mail.settings.tz).isoformat(), "before": changed}]}
                succeeded = True
                return {**old, "items": {**old["items"], contact_id: {**updated, "version": uuid.uuid4().hex[:8]}}}
            state = self.mutate(transition)
            return "聯絡人\n已更新本機紀錄。\n" + self.display(state["items"][contact_id]) if succeeded else "聯絡人\nID、版本或容量不符；沒有覆寫資料。"
        return apply
