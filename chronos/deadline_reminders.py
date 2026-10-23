"""Deadline notices with durable claims, independent of the daily task list."""
from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
from html import escape
from uuid import uuid4

from .telegram import TelegramError
from .task_timing import display_time


def deadline_value(task):
    return task.get("due_at") or (task.get("timing") or {}).get("due_date")


def reminder_stage(task, now, tz):
    """Return only the most relevant stage; never replay an older stage."""
    value = deadline_value(task)
    if not value or task.get("status", "open") != "open":
        return None
    local = now.astimezone(tz)
    if len(value) == 10:
        day = datetime.fromisoformat(value).date()
        if local.date() > day:
            return None
        today_notice = datetime.combine(day, time(9), tzinfo=tz)
        if local >= today_notice:
            return "today", "Due today · time unspecified"
        if local.date() < day and local >= today_notice - timedelta(days=1):
            return "day", "Due tomorrow"
        return None
    due = datetime.fromisoformat(value)
    if due.utcoffset() is None:
        return None
    remaining = due.astimezone(timezone.utc) - now.astimezone(timezone.utc)
    if remaining <= timedelta(0) or remaining > timedelta(hours=24):
        return None
    return ("hour", "Due within 1 hour") if remaining <= timedelta(hours=1) else ("day", "Due within 24 hours")


async def send_deadline_reminders(db, telegram, settings, *, now=None):
    if not settings.telegram_chat_id or not telegram.enabled:
        return {"sent": 0, "disabled": True}
    now = now or datetime.now(settings.tz)
    sent = 0
    pending = 0
    for task in db.list_open_tasks():
        stage = reminder_stage(task, now, settings.tz)
        if stage is None:
            continue
        value = deadline_value(task)
        digest = sha256(f"{task['id']}:{value}:{stage[0]}".encode()).hexdigest()
        key = f"deadline-{digest}"
        claim = uuid4().hex

        def reserve(previous):
            if previous and previous.get("status") in {"sent", "sending", "uncertain"}:
                return previous
            current = next((item for item in db.list_open_tasks() if item["id"] == task["id"]), None)
            if current is None or deadline_value(current) != value:
                return {"status": "cancelled"}
            return {"status": "sending", "claim": claim, "task_id": task["id"],
                    "deadline": value, "stage": stage[0], "started_at": now.isoformat()}

        state = db.mutate_task_action_state(key, reserve)
        if state.get("claim") != claim:
            pending += state.get("status") in {"sending", "uncertain"}
            continue
        # Check again just before delivery, since the task may have been edited
        # after the claim transaction. Telegram cannot join the DB transaction.
        current = next((item for item in db.list_open_tasks() if item["id"] == task["id"]), None)
        if current is None or deadline_value(current) != value:
            db.mutate_task_action_state(key, lambda old: {**old, "status": "cancelled"})
            continue
        due_text = display_time(value, settings.tz)
        if len(value) == 10:
            due_text += " · time unspecified"
        deadline_line = f"Due {due_text}"
        if stage[0] == "hour":
            deadline_line += " · within 1 hour"
        text = (f"<b>A deadline for your attention, Sir.</b>\n{escape(deadline_line)}\n\n"
                f"<b>{escape(current['title'][:1500])}</b>")
        try:
            result = await telegram.send_message(settings.telegram_chat_id, text, parse_mode="HTML",
                reply_markup={"inline_keyboard": [[
                    {"text": "Edit task", "callback_data": f"task:edit:{task['id']}"},
                    {"text": "Plan tasks", "callback_data": f"task:plan:{task['id']}"},
                ]]})
        except TelegramError:
            db.mutate_task_action_state(key, lambda old: {**old, "status": "uncertain"})
            pending += 1
            continue
        if result.get("ok"):
            db.mutate_task_action_state(key, lambda old: {**old, "status": "sent", "sent_at": now.isoformat()})
            sent += 1
        else:
            code = result.get("error_code", 500)
            status = "retry" if 400 <= code < 500 else "uncertain"
            db.mutate_task_action_state(key, lambda old: {**old, "status": status})
            pending += 1
    if pending:
        raise RuntimeError("Deadline reminder delivery is pending or uncertain")
    return {"sent": sent}
