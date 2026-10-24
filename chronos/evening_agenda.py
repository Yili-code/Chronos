"""Cloud-driven tomorrow agenda with per-part durable delivery receipts."""
from datetime import datetime, timedelta, time
from html import escape
import re
from uuid import uuid4

from .task_timing import display_date


def render_agenda(events, day, tz, label="Tomorrow", db=None, now=None, show_task_ids=False, *, heading=None, compact=False):
    if heading is None:
        date = display_date(day.isoformat())
        heading = f"<b>{date if compact else label + ' · ' + date}</b>"
    now = now or datetime.now(tz)
    rows = []
    for event in events:
        title = escape((event.get("summary") or "Untitled event")[:500])
        if compact:
            title = " ".join(title.split())
        props = event.get("extendedProperties", {}).get("private", {})
        token = props.get("chronosToken")
        marker = None
        if db is not None and token:
            current = None
            state = db.get_task_action_state("learning:history:" + token) or {}
            if not state and props.get("chronosTask"):
                current = db.get_task_action_state("learning:task:" + props["chronosTask"]) or {}
                if current.get("token") == token:
                    state = current
            outcome = state.get("feedback", {}).get("outcome")
            marker = {"done": "✓ Finished", "partial": "◐ Incomplete", "skipped": "− Skipped"}.get(outcome)
            task_id = str(props.get("chronosTask", ""))
            if marker is None and task_id.isdecimal():
                current = current if current is not None else db.get_task_action_state("learning:task:" + task_id) or {}
                if current.get("token") == token:
                    task = db.get_task(int(task_id))
                    if task and task.get("status") == "done":
                        marker = "✓ Finished"
            finish = event.get("end", {}).get("dateTime")
            if marker is None and finish and datetime.fromisoformat(finish) <= now:
                marker = "◇ Awaiting"
        if marker:
            title += (" · " if compact else "\n") + marker
        if event["start"].get("date"):
            start = event["start"]["date"]
            rows.append((start, f"All day · {title}"))
        else:
            start = datetime.fromisoformat(event["start"]["dateTime"]).astimezone(tz)
            end = datetime.fromisoformat(event["end"]["dateTime"]).astimezone(tz)
            duration = round((end - start).total_seconds() / 60)
            task_id = str(props.get("chronosTask", ""))
            task_suffix = f"({task_id})" if show_task_ids and re.fullmatch(r"[1-9][0-9]*", task_id) else ""
            begin = start.strftime("%H:%M") if start.date() == day else start.strftime("%m/%d %H:%M")
            stop = end.strftime("%H:%M") if end.date() == day else end.strftime("%m/%d %H:%M")
            row = f"{begin}–{stop} · {title}" if compact else f"{begin}–{stop} · {duration} minutes{task_suffix}\n{title}"
            rows.append((start.isoformat(), row))
    parts, text = [], heading
    first_row = True
    for _, row in sorted(rows):
        if len(text) + len(row) > 3000:
            parts.append(text)
            text = heading + " · continued"
            first_row = True
        separator = "\n\n" if first_row or not compact else "\n"
        text += (separator if text else "") + row
        first_row = False
    parts.append(text if rows else (heading + "\n\n" if heading else "") + "No events scheduled.")
    return parts


def render_window_agenda(events, start, end, tz, db=None):
    heading = f"{start:%m/%d %H:%M}–{end:%m/%d %H:%M}" if len(events) > 1 else ""
    return render_agenda(events, start.date(), tz, db=db, heading=heading)


async def tomorrow_agenda(calendar, settings, now=None, *, day_offset=1, db=None):
    """Read the remaining today or full future day without delivery receipts."""
    now = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    day = now.date() + timedelta(days=day_offset)
    start = datetime.combine(day, time.min, settings.tz)
    end = start + timedelta(days=1)
    if day_offset == 0:
        start = now
    await calendar.verify_owner()
    events = await calendar.agenda(start, end)
    return render_agenda(events, day, settings.tz, "Today" if day_offset == 0 else "Tomorrow", db=db, now=now, compact=day_offset == 0)


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
        parts = render_agenda(events, day, settings.tz, db=db, now=now)
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
