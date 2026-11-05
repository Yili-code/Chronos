"""Explicit read-only Gmail search with durable, individually bound result cards."""
import asyncio
import json
import re
import time
import uuid

from .gmail import GmailError, message_id
from .mail_workflow import format_mail_card
from .telegram import TelegramError


def command(text):
    return bool(re.match(r"^(?:/mail_search(?:_next)?(?:\s|$)|搜尋郵件(?:\s*[:：]|$))", text.strip(), re.I))


async def prepare(mail, text, chat_id):
    mail.require_config()
    account = mail.settings.gmail_account.strip().lower()
    text = text.strip()
    next_match = re.fullmatch(r"/mail_search_next\s+([a-f0-9]{8})", text)
    state = mail.get("search")
    if next_match:
        if (not state or state.get("token") != next_match[1] or state.get("chat_id") != chat_id
                or state.get("expires", 0) <= time.time() or not state.get("next_page")):
            return lambda: "搜尋頁碼已過期或沒有下一頁，請重新搜尋。沒有變更郵件。"
        query, cursor = state["query"], state["next_page"]
    else:
        match = re.fullmatch(r"(?:/mail_search\s+|搜尋郵件\s*[:：]\s*)(.+)", text, re.I | re.S)
        query = match[1].strip() if match else ""
        if not query or len(query) > 500 or any(ord(c) < 32 for c in query):
            return lambda: "請提供 1 至 500 字元的單行 Gmail 查詢，例如 /mail_search from:client@example.org subject:報價。搜尋不會寄信或修改郵件。"
        cursor = None
    async def fetch():
        await mail.gmail.verify_account()
        ids, next_page = await mail.gmail.search_page(query, cursor)
        if not isinstance(ids, list) or len(ids) > 5 or len(ids) != len(set(ids)):
            raise GmailError("Search results could not be verified")
        results = []
        observed = {}
        for identifier in ids:
            message_id(identifier)
            observed[mail.key("message:" + identifier)] = mail.get("message:" + identifier)
            item = await mail.gmail.read(identifier)
            if not isinstance(item, dict) or item.get("id") != identifier or not item.get("thread_id"):
                raise GmailError("Search message identity could not be verified")
            if len(json.dumps(item, ensure_ascii=False).encode("utf-8")) > 300000:
                raise GmailError("Search message exceeds the safe local storage limit")
            results.append(item)
        return results, next_page, observed
    try:
        results, next_page, observed = await asyncio.wait_for(fetch(), timeout=45)
    except (GmailError, TimeoutError):
        return lambda: "搜尋未能完整完成，沒有呈現部分結果，也不能判定沒有符合郵件。請稍後重試。"
    token = uuid.uuid4().hex[:8]
    expected = {mail.key("search"): state, **observed}
    changes = {mail.key("search"): {"token": token, "query": query, "next_page": next_page,
                                    "expires": time.time() + 900, "chat_id": chat_id}}
    messages = [{"search_namespace": mail.namespace, "text": f"Gmail 搜尋｜本頁 {len(results)} 封（唯讀）\n"
                 + (f"還有結果：/mail_search_next {token}" if next_page else "本次查詢已無下一頁。")
                 + "\n回覆個別郵件卡片可草擬回覆或建立追蹤；目前沒有建立草稿、寄信或更改郵件。"}]
    for item in results:
        key = mail.key("message:" + item["id"])
        old = observed[key]
        changes[key] = {**(old or {}), "mail": item}
        shown = {**item, "subject": str(item.get("subject", ""))[:180], "sender": str(item.get("sender", ""))[:180]}
        excerpt = str(item.get("body") or item.get("snippet") or "（無可讀文字）")[:600]
        card = format_mail_card(shown, "原文摘錄：" + excerpt, mail.settings.gmail_account)
        while len(card.encode("utf-16-le")) // 2 > 3900 and excerpt:
            excerpt = excerpt[:len(excerpt) // 2]
            card = format_mail_card(shown, "原文摘錄：" + excerpt, mail.settings.gmail_account)
        if len(card.encode("utf-16-le")) // 2 > 3900:
            return lambda: "搜尋結果的標頭超過完整呈現上限，結果未套用；請在 Gmail 查看。"
        messages.append({"text": card,
                         "parse_mode": "HTML", "search_mail_id": item["id"]})
    def apply():
        if mail.settings.gmail_account.strip().lower() != account or (next_match and state["expires"] <= time.time()):
            return "搜尋帳號已變更或頁碼已過期，結果未套用；請重新搜尋。"
        if not mail.db.compare_mail_states(expected, changes):
            return "搜尋或郵件狀態在準備期間已變更，結果未套用；請重新搜尋。"
        return {"messages": messages}
    return apply


async def deliver(mail, chat_id, update_id, receipt):
    messages = receipt.get("messages") or [{"text": receipt["reply"]}]
    if receipt["delivered"]:
        return
    if messages[0].get("search_namespace", mail.namespace) != mail.namespace:
        raise GmailError("Search belongs to another configured account; start a new search")
    for index in range(receipt.get("delivered_count", 0), len(messages)):
        part = messages[index]
        try:
            result = await mail.telegram.send_message(chat_id, part["text"], parse_mode=part.get("parse_mode"), requires_response=bool(part.get("search_mail_id")))
        except TelegramError:
            raise GmailError("Search delivery pending; retry the same request") from None
        if not result.get("ok"):
            raise GmailError("Search delivery pending; retry the same request")
        if part.get("search_mail_id"):
            message_id = result.get("result", {}).get("message_id")
            if not message_id:
                raise GmailError("Search card delivery identity missing")
            mail.patch(f"telegram:{chat_id}:{message_id}", mail_id=part["search_mail_id"])
        if receipt.get("messages"):
            mail.db.mark_update_message_delivered(update_id, index + 1)
        else:
            mail.db.mark_update_delivered(update_id)
