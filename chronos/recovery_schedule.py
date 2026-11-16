"""Prepare and revalidate one Calendar exception for a missed work block."""
import re
from datetime import datetime, timedelta
from uuid import uuid4

from .backward_planning import reserved_windows
from .course_tracking import COURSE_SCHEDULE
from .google_calendar import CalendarError
from .learning_calendar import class_windows, find_slot, overlaps, study_window
from .study_reflow import bounds
from .task_actions import task_fields


PERIOD = re.compile(r"(?:(?:下|這|本)?(?:星期|週|周)[一二三四五六日天]|今天|明天|\d{4}-\d{2}-\d{2})"
                    r"\s*(?:早上|上午|下午|晚上|晚間)")
LOCATION = re.compile(r"(?:去|在)([^去在，。；！？\s]{1,40}?)(?:處理|完成|做|讀|學習)(?:這|該|此|任務|功課|$)")


def hint(reason):
    return PERIOD.search(reason or "")


def normalize(text):
    return re.sub(r"[\W_]", "", text.casefold()).removeprefix("圖論")


def class_reference(reason, weekday):
    return next((slot for slot in COURSE_SCHEDULE if slot.weekday == weekday
                 and normalize(slot.name) + "課" in normalize(reason)), None)


def course_interval(planner, day, course):
    if course is None:
        return None
    tz = planner.settings.tz
    pair = (datetime.combine(day, course.start, tz), datetime.combine(day, course.end, tz))
    policy = planner.db.get_task_action_state("learning:course-calendar-policy")
    return pair if pair in class_windows(day, tz, policy) else None


def reservations(planner, task_id):
    return [*reserved_windows(planner.db), *(
        (datetime.fromisoformat(state["start"]), datetime.fromisoformat(state["end"]))
        for task in planner.db.list_open_tasks() if task["id"] != task_id
        for state in [planner.get(task["id"])] if state and state.get("start")
        and state["status"] in {"creating", "scheduled"})]


async def conflicts(planner, start, end, events, course):
    seen = []
    calendar_ranges = []
    for event in events:
        if event.get("transparency") == "transparent":
            continue
        begin, stop = bounds(event, planner.settings.tz)
        if not overlaps(start, end, [(begin, stop)]):
            continue
        calendar_ranges.append((begin, stop))
        seen.append({"id": event["id"], "calendar": event["_calendar_id"],
                     "version": event.get("etag"), "title": event.get("summary") or "Calendar 忙碌時段",
                     "start": begin.isoformat(), "end": stop.isoformat()})
    # FreeBusy may expose a block whose details aren't visible. Only offer
    # exceptions for intervals we can account for in the confirmation card.
    for begin, stop in await planner.calendar.busy(start, end):
        cursor = max(begin, start)
        stop = min(stop, end)
        for a, b in sorted(calendar_ranges):
            if a > cursor:
                break
            cursor = max(cursor, b)
        if cursor < stop:
            return None
    if course:
        interval = course_interval(planner, start.date(), course)
        if interval and interval not in calendar_ranges and overlaps(start, end, [interval]):
            seen.append({"id": course.key, "calendar": "course",
                         "title": course.name, "start": interval[0].isoformat(),
                         "end": interval[1].isoformat()})
    return sorted(seen, key=lambda event: (event["calendar"], event["id"]))


def available_slot(planner, task, now, window, minutes, course, *, lead_minutes=10):
    day = window[0].date().isoformat()
    budget = sum((planner.db.get_task_action_state("learning:day:" + day) or {}).values())
    return find_slot(task, now, [], reservations(planner, task["id"]), planner.settings, minutes,
                     {day: budget}, planner.db.get_task_action_state("learning:course-calendar-policy"),
                     window=window, lead_minutes=lead_minutes,
                     overlap_class=course_interval(planner, window[0].date(), course))


async def preview(planner, task, state, now):
    from .session_recovery import card

    recovery = state["recovery"]
    reason = recovery.get("reason", "")
    period = hint(reason)
    if recovery["stage"] != "decision" or period is None:
        return "這張卡片沒有可查看的指定時段。"
    resolved = study_window(period[0], now, state["minutes"])
    if not resolved:
        return card(planner, task, state, now, notice="無法判斷指定的日期。")
    begin, end, minutes = resolved
    if end <= now or begin >= now + timedelta(days=7):
        return card(planner, task, state, now, notice="指定時段必須在未來七天內。")
    course = class_reference(reason, begin.weekday())
    try:
        await planner.calendar.verify_owner()
        events = await planner.calendar.agenda(begin, end, deduplicate=False)
    except CalendarError:
        return card(planner, task, state, now, notice="暫時無法讀取 Calendar，請稍後再試。")
    window = (begin, end)
    if course:
        title = normalize(course.name)
        matches = {bounds(event, planner.settings.tz) for event in events
                   if title in normalize(event.get("summary") or "") and event.get("start", {}).get("dateTime")
                   and event.get("transparency") != "transparent"}
        if len(matches) != 1:
            return card(planner, task, state, now, notice="指定時段內找不到唯一對應的課程。")
        class_start, class_end = matches.pop()
        window = (max(begin, class_start), min(end, class_end))
    slot = available_slot(planner, task, now, window, minutes, course)
    if not slot:
        return card(planner, task, state, now, notice="指定時段放不下這項任務。")
    start, stop = slot
    try:
        reviewed = await conflicts(planner, start, stop, events, course)
    except CalendarError:
        reviewed = None
    if reviewed is None:
        return card(planner, task, state, now, notice="這段時間有無法辨識的 Calendar 衝突。")
    if len(reviewed) > 12:
        return card(planner, task, state, now, notice="這段時間的重疊行程太多，請選擇其他安排。")
    location = LOCATION.search(reason, period.end())
    proposal = {"start": start.isoformat(), "end": stop.isoformat(),
                "location": location[1] if location else "",
                "expires_at": (now + timedelta(minutes=15)).isoformat(), "conflicts": reviewed,
                "course": course.key if course else None}
    token = uuid4().hex
    changed = planner.update(task["id"], lambda old: {
        **old, "token": token, "message_id": None,
        "recovery": {**old["recovery"], "stage": "schedule_review", "proposal": proposal}}
        if old.get("token") == state["token"] and old["status"] == "awaiting_resolution"
        and old["recovery"] == recovery else old)
    if changed.get("token") != token:
        return "安排已更新，請使用最新卡片。"
    return card(planner, task, changed, now)


def confirm(planner, task, state, now):
    from .session_recovery import card

    proposal = state["recovery"].get("proposal")
    if state["status"] != "awaiting_resolution" or state["recovery"]["stage"] != "schedule_review" or not proposal:
        return "這個安排已處理，請使用最新卡片。"
    if proposal["expires_at"] <= now.isoformat():
        return card(planner, task, state, now, notice="確認已過期。請返回並重新查看時段。")
    claim = uuid4().hex
    old_booking_fields = {"calendar_moves", "exact_slot", "owner_window", "late_window",
                          "pending_reschedule", "edit_message_id", "superseded_schedule_id"}
    approved = planner.update(task["id"], lambda old: {
        **{key: value for key, value in old.items() if key not in old_booking_fields},
        "status": "creating", "approved_at": now.isoformat(), "recovery_claim": claim,
        "start": proposal["start"], "end": proposal["end"], "recovery_booking": proposal,
        "recovery": {**state["recovery"], "stage": "settled", "decision": "rbook",
                     "decided_at": now.isoformat()},
        "duration_source": "owner", "auto_accepted": False}
        if old.get("token") == state["token"] and old["status"] == "awaiting_resolution"
        and old["recovery"] == state["recovery"] and old["snapshot"] == task_fields(task) else old)
    if approved.get("recovery_claim") != claim:
        return "這個安排已處理，請使用最新卡片。"
    return "已確認這次的安排。Calendar 寫入後會回報結果。"


async def still_valid(planner, task, state, now, start, end):
    proposal = state["recovery_booking"]
    course = next((c for c in COURSE_SCHEDULE if c.key == proposal["course"]
                   and c.weekday == start.weekday()), None)
    events = await planner.calendar.agenda(start, end, deduplicate=False)
    current = await conflicts(planner, start, end, events, course)
    live = next((t for t in planner.db.list_open_tasks() if t["id"] == task["id"]), None)
    latest = planner.get(task["id"])
    return (current == proposal["conflicts"] and live is not None and task_fields(live) == state["snapshot"]
            and latest and latest["status"] == "creating" and latest["token"] == state["token"]
            and available_slot(planner, live, now, (start, end), state["minutes"], course, lead_minutes=0) == (start, end))


def reopen(planner, task, state):
    recovery = {key: value for key, value in state["recovery"].items()
                if key not in {"proposal", "decision", "decided_at"}}
    recovery["stage"] = "decision"
    planner.update(task["id"], lambda old: {
        **{key: value for key, value in old.items() if key not in {"recovery_booking", "recovery_claim"}},
        "status": "awaiting_resolution", "recovery": recovery, "token": uuid4().hex,
        "message_id": None, "delivery": "recovery_pending"}
        if old.get("token") == state["token"] and old["status"] == "creating" else old)


async def deliver_changed(planner, task, state, now):
    from .session_recovery import card

    message = card(planner, task, state, now, notice="時間或衝突有變動，請重新查看指定時段。")["messages"][0]
    buttons = [button for row in message["reply_markup"]["inline_keyboard"] for button in row]
    await planner.send(task["id"], state, message["text"], buttons, "recovery")
