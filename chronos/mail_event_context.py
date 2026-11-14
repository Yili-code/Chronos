"""Read-only Calendar context for optional participation cards."""
import asyncio
from datetime import datetime, time, timedelta
from email.utils import parsedate_to_datetime
from html import escape

from pydantic import BaseModel, ConfigDict, Field

from .gmail import clean
from .google_calendar import CalendarError
from .mail_event_travel import travel_context
from .mail_task_event import grounded
from .study_reflow import bounds, movable, verified_booking


class InvitationTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: str = Field(max_length=50)
    end: str | None = Field(max_length=50)
    start_evidence: str = Field(max_length=1000)
    end_evidence: str = Field(max_length=1000)
    venue: str = Field(default="", max_length=250)


def invitation_window(mail, timing, tz):
    if timing is None:
        return None, None
    source = "\n".join(clean(mail.get(key) or "", 12000) for key in ("subject", "body", "alternative_body", "snippet"))
    try:
        if mail.get("received_at"):
            reference = datetime.fromtimestamp(mail["received_at"], tz)
        else:
            try:
                reference = datetime.fromisoformat(mail.get("date") or "")
            except ValueError:
                reference = parsedate_to_datetime(mail.get("date") or "")
            if reference.utcoffset() is not None:
                reference = reference.astimezone(tz)
    except (ValueError, TypeError, OverflowError, OSError):
        reference = None
    start = grounded(timing.start, timing.start_evidence, source, tz, reference=reference, invitation=True)
    if start is None:
        return None, None
    end = grounded(timing.end, timing.end_evidence, source, tz, start=start, reference=reference, invitation=True)
    return start.astimezone(tz), end.astimezone(tz) if end and end > start else None


def agenda_lines(events, day, label):
    following = day + timedelta(days=1)
    rows = []
    for event, start, end in events:
        if start >= following or end <= day:
            continue
        if "date" in event["start"]:
            window = "全天"
        else:
            window = f"{max(day, start):%H:%M}–{end:%H:%M}" if end < following else f"{max(day, start):%H:%M}–24:00"
        title = escape(clean(event.get("summary") or "未命名行程", 48))
        rows.append(f"• {window}　{title}")
    lines = ["", f"<b>{label} · {day:%m/%d}</b>", *(rows[:5] or ["行事曆上沒有安排。"])]
    if len(rows) > 5:
        lines.append(f'另有 {len(rows) - 5} 項，<a href="https://calendar.google.com/calendar/r/day/{day:%Y/%m/%d}">查看完整行程</a>。')
    return lines


def time_range(start, end):
    clock = "%H:%M:%S" if any(value.second or value.microsecond for value in (start, end)) else "%H:%M"
    end_format = clock if start.date() == end.date() else "%m/%d " + clock
    return f"{start:{clock}}–{end:{end_format}}"


def adjustable(workflow, event, now):
    props = event.get("extendedProperties", {}).get("private", {})
    if props.get("chronosMailEvent"):
        return False  # An invitation previously accepted by the owner is a commitment.
    linked = props.get("chronosTask")
    state = workflow.db.get_task_action_state(f"learning:task:{linked}") if linked else None
    if linked and (not str(linked).isdecimal() or not verified_booking(event, workflow.settings, int(linked), state)):
        return False
    return movable(event, workflow.settings, now, task_id=int(linked) if linked else None, study_state=state)


def conflict_lines(workflow, events, start, end, now, benefit, busy):
    if end is None:
        return ["結束時間未確認，無法判斷完整時段是否衝突。"]
    fixed, flexible = [], []
    for event, begin, finish in events:
        if begin >= end or finish <= start or event.get("transparency") == "transparent":
            continue
        title = escape(clean(event.get("summary") or "未命名行程", 48))
        target = flexible if adjustable(workflow, event, now) else fixed
        target.append((title, time_range(max(start, begin), min(end, finish))))
    # Some calendars can report availability without exposing event details.
    if busy is None:
        notice = "空閒狀態未能完整核對。"
    else:
        windows = [(a, b) for event, a, b in events if event.get("transparency") != "transparent"]
        unknown = False
        for a, b in busy:
            cursor, stop = max(start, a), min(end, b)
            for begin, finish in windows:
                if begin > cursor or cursor >= stop:
                    break
                cursor = max(cursor, finish)
            if cursor < stop:
                unknown = True
                break
        notice = "另有忙碌時段未顯示行程名稱，先視為衝突。" if unknown else ""
    if not fixed and not flexible:
        return [notice or "活動時段沒有查到衝突。"]
    lines = [notice] if notice else []
    for label, rows in (("固定或未確認可取消", fixed), ("可調整的個人安排", flexible)):
        if rows:
            lines.append(f"<b>{label}</b>")
            lines.extend(f"• {window}　{title}" for title, window in rows[:3])
            if len(rows) > 3:
                lines.append(f"另有 {len(rows) - 3} 項重疊。")
    if flexible:
        names = "、".join(title for title, _ in flexible[:2])
        if len(flexible) > 2:
            names += f"等 {len(flexible)} 項安排"
        condition = "先確認其他衝突能否處理；" if fixed or notice else ""
        lines.extend(["", "<b>取捨</b>",
                      f"• 參加活動 · {benefit} {condition}須另排「{names}」。",
                      f"• 保留原安排 · 依原定時間進行「{names}」，會錯過本場活動。"])
    return lines


async def participation_context(workflow, mail, timing, summary, *, now=None):
    tz = workflow.settings.tz
    start, end = invitation_window(mail, timing, tz)
    now = now or datetime.now(tz)
    if start is None or (end or start) <= now:
        return ["", "<b>行程核對</b>", "未確認可用的活動日期與開始時間，無法核對當天及隔天行程。"]
    day = datetime.combine(start.date(), time.min, tz)
    following = day + timedelta(days=1)
    day_after = following + timedelta(days=1)
    window = f"{start:%m/%d %H:%M}"
    if end is None:
        window += " 起（結束時間待確認）"
    else:
        window += f"–{end:%H:%M}" if end.date() == start.date() else f"–{end:%m/%d %H:%M}"
    lines = ["", f"<b>行程核對 · {window}</b>"]
    calendar = workflow.calendar
    if calendar is None or not calendar.configured():
        return [*lines, "尚未連線行事曆，無法核對衝突或兩天的安排。"]
    try:
        async with asyncio.timeout(20):
            await calendar.verify_owner()
            raw_events = await calendar.agenda(day, max(day_after, end or day_after))
        events = sorted(((event, *bounds(event, tz)) for event in raw_events), key=lambda row: row[1])
    except (CalendarError, TimeoutError, ValueError, TypeError, KeyError):
        return [*lines, "暫時無法完整讀取行事曆，衝突與兩天的安排待確認。"]
    busy = None
    if end is not None:
        try:
            busy = await asyncio.wait_for(calendar.busy(start, end), timeout=8)
        except (CalendarError, TimeoutError):
            pass
    travel, _ = await travel_context(workflow.settings, mail, timing.venue, events, start)
    return [*lines, *conflict_lines(workflow, events, start, end, now, escape(clean(summary, 110)), busy), *travel,
            *agenda_lines(events, day, "活動當天"), *agenda_lines(events, following, "隔天")]
