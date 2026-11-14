"""Read-only answers to scheduling questions about a bound invitation."""
import asyncio
from datetime import datetime, time, timedelta
import re

from .google_calendar import CalendarError
from .gmail import clean
from .mail_event_context import InvitationTiming, adjustable, invitation_window, participation_context, time_range
from .mail_event_travel import travel_context
from .nightly_planning import free_windows
from .study_reflow import bounds


MOVE = re.compile(r"(?:移到|挪到|改到|改期|調到|改在|延到|提前到|reschedul|move\b)", re.I)
QUESTION = re.compile(r"[?？]|什麼時候|哪[天個裡]|有沒有|是否|能否|可以|when\b|could\b|can\b", re.I)
AGENDA = re.compile(r"行程|衝突|空檔|交通|車程|出發|agenda|conflict|travel", re.I)
INVITATION_QUESTION = re.compile(r"能去|能參加|可以去嗎|可以參加嗎|適合參加|how long", re.I)
ANSWER = re.compile(r"^(?:請問\s*)?(?:(.+?)\s*)?(?:可以|能否|能|要)?\s*(?:移到|挪到|改到|改期|調到|改在|延到|提前到)", re.I)


async def prepare_invitation_followup(workflow, saved, instruction, now):
    screening = saved.get("screening") or {}
    timing = screening.get("invitation")
    if not timing or screening.get("expired"):
        return None
    if re.match(r"^(?:幫我|請幫我)?\s*(?:回信|回覆對方|寄信|草擬|向.+詢問)", instruction):
        return None
    moving = bool(MOVE.search(instruction) and QUESTION.search(instruction))
    if not moving and not (INVITATION_QUESTION.search(instruction)
                           or re.search(r"怎麼去|來得及|^(?:查|看)(?:看|一下)?.*(?:行程|衝突|交通)", instruction)
                           or (AGENDA.search(instruction) and QUESTION.search(instruction))):
        return None
    mail = saved.get("mail") or {}
    timing = InvitationTiming.model_validate(timing)
    if not moving:
        from .mail_screening import format_decision, MailDecision

        context = await participation_context(workflow, mail, timing, screening["summary"], now=now)
        return {"messages": [{"text": format_decision(mail, MailDecision.model_validate(screening),
            workflow.settings.gmail_account, participation=context, tz=workflow.settings.tz), "parse_mode": "HTML"}]}
    return await reschedule_options(workflow, mail, timing, instruction, now)


async def reschedule_options(workflow, mail, timing, instruction, now):
    tz = workflow.settings.tz
    start, end = invitation_window(mail, timing, tz)
    if start is None or end is None or start <= now:
        return "活動的未來時段尚未確認，無法核對改期選項。"
    calendar = workflow.calendar
    if calendar is None or not calendar.configured():
        return "行事曆尚未連線，無法核對改期選項。"
    day = datetime.combine(start.date(), time.min, tz)
    following = day + timedelta(days=1)
    horizon = max(following + timedelta(days=1), end)
    try:
        async with asyncio.timeout(20):
            await calendar.verify_owner()
            events, busy = await asyncio.gather(calendar.agenda(day, horizon), calendar.busy(day, horizon))
        rows = [(event, *bounds(event, tz)) for event in events if event.get("transparency") != "transparent"]
        conflicts = [(event, begin, finish) for event, begin, finish in rows if begin < end and start < finish]
        candidates = [row for row in conflicts if row[0].get("summary") and
                      row[0]["summary"].casefold() in instruction.casefold()]
        if not candidates:
            match = ANSWER.search(instruction)
            hint = (match[1] or "").strip() if match else ""
            if hint and hint not in {"它", "那個", "這個", "這場", "那場", "這個行程", "那個行程"}:
                candidates = [row for row in conflicts if hint.casefold() in row[0].get("summary", "").casefold()]
            elif len(conflicts) == 1:
                candidates = conflicts
        if len(candidates) != 1:
            names = "、".join(dict.fromkeys(clean(row[0].get("summary") or "未命名行程", 80) for row in conflicts[:5]))
            return f"你指哪個衝突行程？{names}" if names else "目前沒有找到與活動重疊的行程。"
        target, begin, finish = candidates[0]
        title = clean(target.get("summary") or "未命名行程", 80)
        if "date" in target.get("start", {}):
            return f"「{title}」是全天安排，請先確認可改期的日期與所需時長。"
        duration = finish - begin
        # Keep provider busy windows intact. The target's existing slot overlaps the
        # invitation, and preserving it also protects hidden simultaneous bookings.
        _, depart = await travel_context(workflow.settings, mail, timing.venue, rows, start)
        blocked = [*busy, (depart or start, end), *((a, b) for _, a, b in rows)]
        options = []
        for date in (day.date(), following.date()):
            for available, until in free_windows(date, blocked, workflow.settings):
                candidate = max(available, now)
                if candidate + duration <= until:
                    options.append(f"• {candidate:%m/%d} {time_range(candidate, candidate + duration)}")
        note = "以下只核對你的行事曆。"
        if depart is not None:
            note += f"已預留前往活動的交通時段（{depart:%m/%d %H:%M} 起）。"
        elif timing.venue:
            note += "交通時間待確認。"
        if not adjustable(workflow, target, now):
            note += "這項安排尚未確認可以改期，請先與主辦人或其他參與者確認。"
        if not options:
            return f"「{title}」在活動當天和隔天沒有找到足夠的替代時段。{note}"
        return "\n".join([f"「{title}」的改期候選", *options[:4], "", note])
    except (CalendarError, TimeoutError, ValueError, KeyError, TypeError):
        return "目前無法完整核對行事曆，改期時段待確認。"
