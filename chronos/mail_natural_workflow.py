"""Bound-mail natural planning with atomic local-only effects."""
import asyncio
from dataclasses import dataclass
import json
import re

from pydantic import BaseModel, ConfigDict, Field

from .ai import AIError
from .mail_correspondence import MailCorrespondence, fingerprint
from .mail_followups import MailFollowups


class MailPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    draft: bool = False
    tracking_hours: int | None = Field(default=None, ge=1, le=2160)
    target: str | None = Field(default=None, max_length=254)
    question: str | None = Field(default=None, max_length=200)


@dataclass
class LocalChange:
    key: str
    value: dict
    result: str | dict


def natural_request(text):
    return bool(re.match(r"^(?:幫我|請幫我|處理這封信\s*[:：]|handle this email\s*:)", text, re.I)
                or (re.match(r"^(?:草擬|起草|撰寫|draft)", text, re.I)
                    and re.search(r"追蹤|提醒|track|follow.up", text, re.I)))


def response(text):
    # Keep subsequent owner replies bound to the same source card.
    return lambda: "回覆草稿\n郵件流程：" + text


async def prepare(mail, identifier, instruction, now=None):
    if len(instruction) > 4000:
        return response("要求超過 4,000 字元，請縮短後重試；尚未套用。")
    # Model output cannot grant remote effects or discard risky clauses.
    if re.search(r"寄|發送|发送|刪|删除|封存|邀請|邀约|會面|會議|行事曆|安排|協調|改期|取消|附件|收件|副本|密件|全體|所有人|reply[ -]?all|搜尋|搜索|找出|建立任務|新增任務|聯絡人|每隔|每天|每週|工作日|"
                 r"不要|別|不必|不用|如果|若|等.*(?:後|后)|\b(?:send|save|delete|archive|invite|reschedule|meeting|calendar|contact|task|every|"
                 r"cancel|attach\w*|search|bcc|cc|unless|if|after|don't|do not)\b", instruction, re.I):
        return response("包含條件、否定或需另行確認的操作，請拆開要求。不會自動寄出。可先草擬回覆並設定明確間隔的追蹤；尚未套用。")
    keys = ["message:" + identifier, "reply:" + identifier, "meeting:" + identifier,
            "followups", "contacts"]
    expected = {mail.key(key): mail.get(key) for key in keys}
    # The planner sees only the owner's instruction, never third-party mail text.
    try:
        plan = await asyncio.wait_for(mail.ai._generate_output(
            "Interpret only the owner's bound-email request into a local draft and/or reply tracking. "
            "Never return commands, authorization or invented details. draft means a local unsent reply. "
            "tracking_hours requires one explicit elapsed hours/days duration in the owner request. "
            "target may only be an email address explicitly provided by the owner; otherwise null. "
            "Ask question for contradictions, negated actions, conditional tracking after a future send, "
            "missing duration, search, attachment, recipient changes, save/send, Calendar or other unsupported actions. "
            "If any part is unsupported or unclear, ask question for the whole request; never silently omit it. "
            "Do not replace waiting-owner with waiting-other: current direction is verified separately.",
            json.dumps({"owner_request": instruction}, ensure_ascii=False),
            MailPlan.model_json_schema(), MailPlan, "mail local workflow"), timeout=60)
        plan = MailPlan.model_validate(plan)
    except (AIError, ValueError, TimeoutError):
        return response("意圖解析暫時失敗，草稿與追蹤均未套用。")
    if plan.question:
        return response(plan.question + "\n草稿與追蹤均未套用。")
    draft_words = re.sub(r"追蹤回覆|track\s+repl(?:y|ies)", "", instruction, flags=re.I)
    draft_requested = bool(re.search(r"草擬|起草|撰寫|回覆|回信|draft|reply", draft_words, re.I))
    tracking_requested = bool(re.search(r"追蹤|提醒|track|follow.up", instruction, re.I))
    if plan.draft != draft_requested or (plan.tracking_hours is not None) != tracking_requested:
        return response("無法完整核對要求；請指定草擬內容，以及是否設定幾小時或幾天後的追蹤。尚未套用。")
    if not plan.draft and plan.tracking_hours is None:
        return response("請指定草擬回覆或明確間隔的郵件追蹤；尚未套用。")
    if plan.tracking_hours is not None:
        durations = re.findall(r"(?<![\d.])(\d+)\s*(天|小時|days?|hours?)(?![a-z])", instruction, re.I)
        hours = [int(n) * (24 if unit.lower() in {"天", "day", "days"} else 1) for n, unit in durations]
        if hours != [plan.tracking_hours]:
            return response("追蹤需單一明確間隔（1 小時至 90 天）；尚未套用。")
    if plan.target and not re.search(r"(?<![\w.@+-])" + re.escape(plan.target) + r"(?![\w.@+-])", instruction, re.I):
        return response("追蹤對象未由你明確提供；尚未套用。")
    addresses = re.findall(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}", instruction, re.I)
    if plan.tracking_hours is not None and addresses and (
            len(set(a.casefold() for a in addresses)) != 1 or not plan.target
            or plan.target.casefold() != addresses[0].casefold()):
        return response("要求中的 Email 與追蹤對象無法完整核對；尚未套用。")
    correspondence = MailCorrespondence(mail)
    _, before = await correspondence.context(identifier)
    source = fingerprint(before)
    changes = []
    if plan.draft:
        change = await correspondence.propose(identifier, instruction, stage=True)
        if not isinstance(change, LocalChange):
            return response("草擬步驟未能準備，流程未套用。\n" + display(change()))
        if change.value.get("questions"):
            return response("草擬仍需資料，流程未套用。\n" + "\n".join(change.value["questions"]))
        changes.append(change)
    if plan.tracking_hours is not None:
        command = f"追蹤回覆：{plan.tracking_hours} 小時"
        if plan.target:
            command += "；對象：" + plan.target
        change = await MailFollowups(mail).prepare(identifier, command, now, stage=True)
        if not isinstance(change, LocalChange):
            return response("追蹤步驟未能準備，流程未套用。\n" + display(change()))
        changes.append(change)
    _, latest = await correspondence.context(identifier)
    if fingerprint(latest) != source or any(c.key.startswith("reply:") and c.value["source"] != source for c in changes):
        return response("郵件串在準備期間已變更，流程未套用；請重新提出要求。")
    text = "回覆草稿\n郵件流程已套用（僅本機）：\n\n" + "\n\n".join(display(c.result) for c in changes)
    if len(text.encode("utf-16-le")) // 2 > 3900:
        return response("結果超過完整預覽上限，請縮短草稿要求；流程未套用。")
    def apply():
        updated = {mail.key(c.key): c.value for c in changes}
        if not mail.db.compare_mail_states(expected, updated):
            return "回覆草稿\n郵件、草稿、會面、聯絡偏好或追蹤狀態已變更，整個流程未套用。請重新提出要求。"
        return text
    return apply


def display(value):
    return value if isinstance(value, str) else "\n\n".join(m["text"] for m in value["messages"])
