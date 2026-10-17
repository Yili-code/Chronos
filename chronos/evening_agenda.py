"""Cloud-driven tomorrow agenda with per-part durable delivery receipts."""
from datetime import datetime, timedelta, time
from html import escape
from uuid import uuid4

from .task_timing import display_date


def render_agenda(events, day, tz, label="Tomorrow"):
    heading = f"<b>{label} · {display_date(day.isoformat())}</b>"
    rows = []
    for event in events:
        title = escape((event.get("summary") or "Untitled event")[:500])
        if event["start"].get("date"):
            start = event["start"]["date"]
            rows.append((start, f"All day · {title}"))
        else:
            start = datetime.fromisoformat(event["start"]["dateTime"]).astimezone(tz)
            end = datetime.fromisoformat(event["end"]["dateTime"]).astimezone(tz)
            duration = round((end - start).total_seconds() / 60)
            begin = start.strftime("%H:%M") if start.date() == day else start.strftime("%m/%d %H:%M")
            stop = end.strftime("%H:%M") if end.date() == day else end.strftime("%m/%d %H:%M")
            rows.append((start.isoformat(), f"{begin}–{stop} · {duration} minutes\n{title}"))
    parts, text = [], heading
    for _, row in sorted(rows):
        if len(text) + len(row) > 3000:
            parts.append(text)
            text = heading + " · continued"
        text += "\n\n" + row
    parts.append(text if rows else heading + "\n\nNo events scheduled.")
    return parts


async def tomorrow_agenda(calendar, settings, now=None, *, day_offset=1):
    """Read the full local day live, without changing evening delivery receipts."""
    now = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    day = now.date() + timedelta(days=day_offset)
    start = datetime.combine(day, time.min, settings.tz)
    await calendar.verify_owner()
    events = await calendar.agenda(start, start + timedelta(days=1))
    return render_agenda(events, day, settings.tz, "Today" if day_offset == 0 else "Tomorrow")


async def send_evening_agenda(db, calendar, telegram, settings, now=None):
    if not settings.enable_learning_calendar:
        return {"enabled": False}
    now = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    if now.hour != 21:
        return {"waiting_for": "21:00 Asia/Taipei"}
    day = now.date() + timedelta(days=1)
    key = "evening:agenda:" + day.isoformat()
    state = db.get_task_action_state(key)
    if not state:
        await calendar.verify_owner()
        start = datetime.combine(day, time.min, settings.tz)
        events = await calendar.agenda(start, start + timedelta(days=1))
        parts = render_agenda(events, day, settings.tz)
        state = db.mutate_task_action_state(key, lambda old: old or {"parts": parts, "next": 0, "status": "pending"})
    while state["next"] < len(state["parts"]):
        claim = uuid4().hex
        state = db.mutate_task_action_state(key, lambda old: {**old, "status": "sending", "claim": claim}
            if old["status"] == "pending" else old)
        if state.get("claim") != claim:
            if state["status"] in {"sending", "uncertain"}:
                raise RuntimeError("Evening agenda delivery needs inspection; no duplicate was sent.")
            return {"already_sent": True}
        try:
            result = await telegram.send_message(settings.telegram_chat_id, state["parts"][state["next"]], parse_mode="HTML")
        except Exception:
            db.mutate_task_action_state(key, lambda old: {**old, "status": "uncertain"})
            raise RuntimeError("Evening agenda delivery is uncertain; inspect before retrying.") from None
        if not result.get("ok"):
            db.mutate_task_action_state(key, lambda old: {**old, "status": "pending"})
            raise RuntimeError("Evening agenda was rejected and remains pending.")
        state = db.mutate_task_action_state(key, lambda old: {**old, "next": old["next"] + 1,
            "status": "sent" if old["next"] + 1 == len(old["parts"]) else "pending"})
    return {"sent": len(state["parts"]), "for_date": day.isoformat()}
