"""Cloud-driven tomorrow agenda with per-part durable delivery receipts."""
from datetime import datetime, timedelta, time
from html import escape
from hashlib import sha256
import re
from uuid import uuid4

from .task_timing import display_date


def render_agenda(events, day, tz, label="Tomorrow", db=None, now=None, show_task_ids=False, *, heading=None):
    if heading is None:
        heading = f"<b>{label} · {display_date(day.isoformat())}</b>"
    now = now or datetime.now(tz)
    rows = []
    for event in events:
        title = escape((event.get("summary") or "Untitled event")[:500])
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
            title += f"\n{marker}"
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
            rows.append((start.isoformat(), f"{begin}–{stop} · {duration} minutes{task_suffix}\n{title}"))
    parts, text = [], heading
    for _, row in sorted(rows):
        if len(text) + len(row) > 3000:
            parts.append(text)
            text = heading + " · continued"
        text += ("\n\n" if text else "") + row
    parts.append(text if rows else (heading + "\n\n" if heading else "") + "No events scheduled.")
    return parts


def render_window_agenda(events, start, end, tz, db=None):
    heading = f"{start:%m/%d %H:%M}–{end:%m/%d %H:%M}" if len(events) > 1 else ""
    return render_agenda(events, start.date(), tz, db=db, heading=heading)


async def tomorrow_agenda(calendar, settings, now=None, *, day_offset=1, db=None):
    """Read the full local day live, without changing evening delivery receipts."""
    now = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    day = now.date() + timedelta(days=day_offset)
    start = datetime.combine(day, time.min, settings.tz)
    await calendar.verify_owner()
    events = await calendar.agenda(start, start + timedelta(days=1))
    return render_agenda(events, day, settings.tz, "Today" if day_offset == 0 else "Tomorrow", db=db, now=now, show_task_ids=day_offset == 0)


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


async def send_today_agenda(db, calendar, telegram, settings, now=None):
    """Deliver today's agenda after 08:00 and replace its messages only on change."""
    if not settings.enable_learning_calendar:
        return {"enabled": False}
    now = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    if now.hour < 8:
        return {"waiting_for": "08:00 Asia/Taipei"}

    day = now.date()
    key = "morning:agenda:" + day.isoformat()
    await calendar.verify_owner()
    start = datetime.combine(day, time.min, settings.tz)
    events = await calendar.agenda(start, start + timedelta(days=1))
    parts = render_agenda(events, day, settings.tz, "Today", now=now)
    fingerprint = sha256("\0".join(parts).encode("utf-8")).hexdigest()
    state = db.get_task_action_state(key)

    if state and state.get("status") in {"sending", "deleting", "uncertain"}:
        raise RuntimeError("Today's agenda delivery needs inspection; no duplicate was sent.")
    if state and state.get("status") == "sent" and state.get("fingerprint") == fingerprint:
        return {"changed": False, "for_date": day.isoformat()}

    claim = uuid4().hex
    if state and state.get("status") == "sent":
        old_message_ids = state.get("message_ids", [])
        claimed = db.mutate_task_action_state(key, lambda old: {
            "status": "deleting", "claim": claim, "fingerprint": fingerprint,
            "parts": parts, "old_message_ids": old_message_ids, "next_delete": 0,
            "message_ids": [], "next_send": 0,
        } if old == state else old)
        if claimed.get("claim") != claim:
            return {"busy": True, "for_date": day.isoformat()}
    else:
        claimed = db.mutate_task_action_state(key, lambda old: old or {
            "status": "sending", "claim": claim, "fingerprint": fingerprint,
            "parts": parts, "message_ids": [], "next_send": 0,
        })
        if claimed.get("claim") != claim:
            if claimed.get("fingerprint") == fingerprint and claimed.get("status") == "sent":
                return {"changed": False, "for_date": day.isoformat()}
            return {"busy": True, "for_date": day.isoformat()}

    try:
        while claimed["status"] == "deleting" and claimed["next_delete"] < len(claimed["old_message_ids"]):
            message_id = claimed["old_message_ids"][claimed["next_delete"]]
            result = await telegram.request("deleteMessage", {
                "chat_id": settings.telegram_chat_id, "message_id": message_id,
            })
            description = result.get("description", "").casefold()
            if not result.get("ok") and "message to delete not found" not in description:
                raise RuntimeError("Telegram could not delete the previous agenda message.")
            claimed = db.mutate_task_action_state(key, lambda old: {
                **old, "next_delete": old["next_delete"] + 1,
            } if old and old.get("claim") == claim else old)
            if not claimed or claimed.get("claim") != claim:
                raise RuntimeError("Today's agenda delivery claim changed; inspect before retrying.")
        if claimed["status"] == "deleting":
            claimed = db.mutate_task_action_state(key, lambda old: {
                **old, "status": "sending",
            } if old and old.get("claim") == claim else old)

        while claimed["next_send"] < len(claimed["parts"]):
            result = await telegram.send_message(settings.telegram_chat_id,
                claimed["parts"][claimed["next_send"]], parse_mode="HTML")
            if not result.get("ok"):
                raise RuntimeError("Today's agenda send was rejected and needs inspection.")
            message = result.get("result") or {}
            message_id = message.get("message_id")
            if not isinstance(message_id, int):
                raise RuntimeError("Today's agenda send needs inspection; Telegram returned no message ID.")
            claimed = db.mutate_task_action_state(key, lambda old: {
                **old, "message_ids": [*old["message_ids"], message_id],
                "next_send": old["next_send"] + 1,
            } if old and old.get("claim") == claim else old)
            if not claimed or claimed.get("claim") != claim:
                raise RuntimeError("Today's agenda delivery claim changed; inspect before retrying.")
    except Exception:
        # A timeout may mean Telegram accepted a send or delete. Keep the claim
        # durable so the next poll cannot create duplicates or lose the receipt.
        db.mutate_task_action_state(key, lambda old: {
            **old, "status": "uncertain",
        } if old and old.get("claim") == claim and old.get("status") != "uncertain" else old)
        raise

    db.mutate_task_action_state(key, lambda old: {
        **old, "status": "sent", "claim": None,
        "old_message_ids": [], "next_delete": 0, "next_send": 0,
    } if old and old.get("claim") == claim else old)
    return {"sent": len(parts), "changed": bool(state), "for_date": day.isoformat()}
