"""Bound-mail natural planning with atomic local-only effects."""
import asyncio
from dataclasses import dataclass
import json
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .ai import AIError
from .mail_correspondence import MailCorrespondence, fingerprint
from .mail_followups import MailFollowups
from .google_calendar import CalendarError
from .gmail import email_address


class MailPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    draft: bool = False
    meeting: bool = False
    tracking_hours: int | None = Field(default=None, ge=1, le=2160)
    target: str | None = Field(default=None, max_length=254)
    question: str | None = Field(default=None, max_length=200)


@dataclass
class LocalChange:
    key: str
    value: dict
    result: str | dict


MEETING_REQUEST = re.compile(r"(?:協調|安排|規劃)(?:一[個場]|這[個場])?(?:會面|會議)|(?:會面|會議)(?:候選|協調)|(?:plan|coordinate|schedule|propose)\s+(?:a\s+)?meeting", re.I)
DRAFT_CUES = re.compile(r"草擬|起草|撰寫|回覆|回信|回給|婉拒|拒絕|答應|draft|reply|respond|decline", re.I)
CONTEXT_DRAFT = re.compile(
    r"(?:(?:請幫我|幫我|請)\s*)?(?:(?:草擬|起草|撰寫)\s*)?(?:回覆|回信)(?:這封(?:郵件|信)|此(?:郵件|信))?|"
    r"(?:please\s+)?(?:draft\s+(?:a\s+)?)?(?:reply|respond)(?:\s+to\s+(?:this|the)\s+(?:email|message))?", re.I)
IMPLICIT_DRAFT_START = re.compile(r"^(?:(?:請|幫我)\s*)?(?:照|依照|根據|回信|回給|reply\b|respond\b)", re.I)
NEGATED_OPERATION = re.compile(
    r"(?:不要|別|不必|不用)(?:\s|幫我|替我|為我|先|再|直接|實際|真的|自動|擅自|現在|目前|暫時)*(?:回覆|回信|草擬|起草|撰寫|追蹤|提醒|協調|安排|更新|刪除|保存|儲存)|"
    r"\b(?:do\s+not|don't|never)(?:\s+(?:actually|automatically|directly|yet|ever|really|immediately))*\s+(?:draft|reply|respond|track|remind|schedule|update|delete|save)\b", re.I)


def draft_request(text):
    return bool(DRAFT_CUES.search(text))


def natural_request(text):
    return bool(re.match(r"^(?:幫我|請幫我|處理這封信\s*[:：]|handle this email\s*:)", text, re.I)
                or (IMPLICIT_DRAFT_START.match(text) and draft_request(text))
                or (re.match(r"^(?:草擬|起草|撰寫|draft)", text, re.I)
                    and (re.search(r"追蹤|提醒|track|follow.up", text, re.I) or MEETING_REQUEST.search(text)))
                or (re.match(r"^(?:協調會面|會面邀約|plan\s+meeting)", text, re.I)
                    and re.search(r"草擬|起草|撰寫|回覆|追蹤|提醒|draft|reply|track|follow.up", text, re.I)))


def response(text):
    # Keep subsequent owner replies bound to the same source card.
    return lambda: "回覆草稿\n郵件流程：" + text


def explicit_meeting_minutes(instruction):
    clauses = [part for part in re.split(r"[，,；;。\n]", instruction) if not re.search(r"追蹤|提醒|track|follow.up", part, re.I)]
    text = "；".join(clauses)
    durations = list(re.finditer(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(小時|分鐘|天|hours?|hrs?|minutes?|mins?|days?)(?![a-z]|\s*(?:後|后|前|later\b|ago\b))", text, re.I))
    if not durations:
        return None
    if any(text[left.end():right.start()].strip() for left, right in zip(durations, durations[1:])):
        return -1
    total = sum(float(m[1]) * (1440 if m[2].lower() in {"天", "day", "days"}
        else 60 if m[2].lower() in {"小時", "hour", "hours", "hr", "hrs"} else 1) for m in durations)
    return int(total) if total.is_integer() and 15 <= total <= 240 else -1


async def prepare(mail, identifier, instruction, now=None):
    if len(instruction) > 4000:
        return response("要求超過 4,000 字元，請縮短後重試；尚未套用。")
    if NEGATED_OPERATION.search(instruction):
        return response("操作本身含否定或矛盾，請明確指定要執行的步驟；整個流程未套用。")
    # Model output cannot grant remote effects or discard risky clauses.
    if re.search(r"寄|發送|发送|刪|删除|封存|邀請|邀约|行事曆|安排(?!(?:一[個場]|這[個場])?(?:會面|會議))|改期|取消|附件|收件|副本|密件|全體|所有人|reply[ -]?all|搜尋|搜索|找出|建立任務|新增任務|聯絡人|每隔|每天|每週|工作日|"
                 r"如果|若|等.*(?:後|后)|\b(?:send|save|delete|archive|invite|reschedule|calendar|contact|task|every|"
                 r"cancel|attach\w*|search|bcc|cc|unless|if|after)\b", instruction, re.I):
        return response("包含條件、否定或需另行確認的操作，請拆開要求。不會自動寄出。可先草擬回覆並設定明確間隔的追蹤；尚未套用。")
    account = mail.settings.gmail_account
    calendar_binding = (mail.settings.calendar_account, mail.settings.calendar_id)
    keys = ["message:" + identifier, "reply:" + identifier, "meeting:" + identifier,
            "followups", "contacts"]
    expected = {mail.key(key): mail.get(key) for key in keys}
    # The planner sees only the owner's instruction, never third-party mail text.
    try:
        plan = (MailPlan(draft=True) if CONTEXT_DRAFT.fullmatch(instruction) else await asyncio.wait_for(mail.ai._generate_output(
            "Interpret only the owner's bound-email request into a local draft, ONE meeting candidate and/or reply tracking. "
            "meeting means explicit meeting coordination, a local tentative candidate only: no hold, invitation or agreement. "
            "Do not mark meeting true for a draft that merely mentions a meeting. Preserve all constraints for each requested step. "
            "An implicit request to decline/respond using previous correspondence is a local draft request. "
            "Content constraints such as withholding private information do not negate the drafting operation. "
            "Questions here clarify operation scope, not missing reply content or stance: the downstream drafter "
            "reads the full thread and asks grounded owner questions if needed. Do not ask a generic draft request to restate content before that read. "
            "Never return commands, authorization or invented details. draft means a local unsent reply. "
            "tracking_hours requires one explicit elapsed hours/days duration in the owner request. "
            "target may only be an email address explicitly provided by the owner; otherwise null. "
            "Ask question for contradictions, negated actions, conditional tracking after a future send, "
            "missing duration, search, attachment, recipient changes, save/send, Calendar mutations or other unsupported actions. "
            "If any part is unsupported or unclear, ask question for the whole request; never silently omit it. "
            "Do not replace waiting-owner with waiting-other: current direction is verified separately.",
            json.dumps({"owner_request": instruction}, ensure_ascii=False),
            MailPlan.model_json_schema(), MailPlan, "mail local workflow"), timeout=60))
        plan = MailPlan.model_validate(plan)
    except (AIError, ValueError, TimeoutError):
        return response("意圖解析暫時失敗，草稿與追蹤均未套用。")
    if plan.question:
        return response(plan.question + "\n草稿與追蹤均未套用。")
    draft_words = re.sub(r"追蹤回覆|track\s+repl(?:y|ies)", "", instruction, flags=re.I)
    draft_requested = draft_request(draft_words)
    tracking_requested = bool(re.search(r"追蹤|提醒|track|follow.up", instruction, re.I))
    meeting_requested = bool(MEETING_REQUEST.search(instruction))
    if (plan.draft != draft_requested or plan.meeting != meeting_requested
            or (plan.tracking_hours is not None) != tracking_requested):
        return response("無法完整核對要求；請指定草擬內容，以及是否設定幾小時或幾天後的追蹤。尚未套用。")
    if not plan.draft and not plan.meeting and plan.tracking_hours is None:
        return response("請指定草擬回覆或明確間隔的郵件追蹤；尚未套用。")
    if plan.tracking_hours is not None:
        duration_text = instruction
        if plan.meeting:
            clauses = [part for part in re.split(r"[，,；;。\n]", instruction) if re.search(r"追蹤|提醒|track|follow.up", part, re.I)]
            if len(clauses) != 1:
                return response("請用逗號或分號分開會面長度與追蹤間隔；整個流程未套用。")
            duration_text = clauses[0]
        durations = re.findall(r"(?<![\d.])(\d+)\s*(天|小時|days?|hours?)(?![a-z])", duration_text, re.I)
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
    meeting = None
    if plan.meeting:
        from .meeting_coordination import MeetingCoordination
        try:
            change = await MeetingCoordination(mail, mail.calendar).propose(identifier, instruction,
                now or datetime.now(mail.settings.tz), stage=True)
        except CalendarError:
            return response("無法完整核對行事曆；整個流程未套用。")
        if not isinstance(change, LocalChange):
            return response("會面步驟未能準備，流程未套用。\n" + display(change()))
        meeting = change.value
        if any(email_address(address) != meeting["recipient"] for address in addresses):
            return response("明確指定的 Email 與所選郵件的會面對象不一致；整個流程未套用。")
        minutes = explicit_meeting_minutes(instruction)
        if minutes is not None and minutes != meeting["minutes"]:
            return response("會面長度與明確要求不一致或有歧義；整個流程未套用。")
        changes.append(change)
    if plan.draft:
        change = await correspondence.propose(identifier, instruction, stage=True, meeting_override=meeting)
        if not isinstance(change, LocalChange):
            return response("草擬步驟未能準備，流程未套用。\n" + display(change()))
        if change.value.get("questions"):
            return response("草擬仍需資料，流程未套用。\n" + "\n".join(change.value["questions"]))
        if meeting is not None and change.value.get("to") != [meeting["recipient"]]:
            return response("草稿主要收件者與會面對象不一致；整個流程未套用，請先核對收件者。")
        changes.append(change)
    if plan.tracking_hours is not None:
        command = f"追蹤回覆：{plan.tracking_hours} 小時"
        if plan.target:
            command += "；對象：" + plan.target
        change = await MailFollowups(mail).prepare(identifier, command, now, stage=True)
        if not isinstance(change, LocalChange):
            return response("追蹤步驟未能準備，流程未套用。\n" + display(change()))
        changes.append(change)
    if meeting is not None:
        start, end = datetime.fromisoformat(meeting["start"]), datetime.fromisoformat(meeting["end"])
        try:
            await mail.calendar.verify_owner()
            busy = await mail.calendar.busy(start, end)
            if start <= (now or datetime.now(mail.settings.tz)) or any(start < stop and end > begin for begin, stop in busy):
                return response("候選時間已過或空檔已變更；整個流程未套用。")
        except CalendarError:
            return response("最終行事曆核對失敗；整個流程未套用。")
    _, latest = await correspondence.context(identifier)
    if fingerprint(latest) != source or any(c.key.startswith(("reply:", "meeting:")) and c.value["source"] != source for c in changes):
        return response("郵件串在準備期間已變更，流程未套用；請重新提出要求。")
    text = "回覆草稿\n郵件流程已套用（僅本機）：\n\n" + "\n\n".join(display(c.result) for c in changes)
    if len(text.encode("utf-16-le")) // 2 > 3900:
        return response("結果超過完整預覽上限，請縮短草稿要求；流程未套用。")
    def apply():
        if (mail.settings.gmail_account != account or (plan.meeting and
                (mail.settings.calendar_account, mail.settings.calendar_id) != calendar_binding)
                or (meeting is not None and datetime.fromisoformat(meeting["start"]) <= (now or datetime.now(mail.settings.tz)))):
            return "回覆草稿\n帳號、行事曆或候選時間已變更；整個流程未套用。"
        updated = {mail.key(c.key): c.value for c in changes}
        if not mail.db.compare_mail_states(expected, updated):
            return "回覆草稿\n郵件、草稿、會面、聯絡偏好或追蹤狀態已變更，整個流程未套用。請重新提出要求。"
        return text
    return apply


def display(value):
    return value if isinstance(value, str) else "\n\n".join(m["text"] for m in value["messages"])
