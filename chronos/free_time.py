"""Read-only search for the next uninterrupted two-hour calendar opening."""
from datetime import datetime, timedelta, time

from .learning_calendar import class_windows, short_task_windows, overlaps
from .task_timing import display_date


def next_two_hours(now, busy, settings, course_policy=None):
    now = now.astimezone(settings.tz)
    horizon = now + timedelta(days=7)
    duration = timedelta(hours=2)
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        start = max(now, datetime.combine(day, time(settings.learning_start_hour), settings.tz))
        stop = min(horizon, datetime.combine(day, time(settings.learning_end_hour % 24), settings.tz)
                   + (timedelta(days=1) if settings.learning_end_hour == 24 else timedelta()))
        blocked = [*busy, *class_windows(day, settings.tz, course_policy),
                   *short_task_windows(day, settings.tz, 120)]
        while start + duration <= stop:
            if start.second or start.microsecond:
                start = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
            if start + duration > stop:
                break
            conflicts = [(begin, end) for begin, end in blocked
                         if overlaps(start, start + duration, [(begin, end)])]
            if not conflicts:
                return start, start + duration
            start = max(end for _, end in conflicts)
    return None


async def free_slot_offer(db, calendar, settings, now=None):
    now = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    await calendar.verify_owner()
    busy = await calendar.busy(now, now + timedelta(days=7))
    policy = db.get_task_action_state("learning:course-calendar-policy")
    slot = next_two_hours(now, busy, settings, policy)
    if slot is None:
        return {"text": "No free two-hour slot in the next 7 days, excluding meals, classes and calendar events."}
    start, end = slot
    return {"text": f"{display_date(start.date().isoformat())} · {start:%H:%M}–{end:%H:%M}",
            "free_slot": {"start": start.isoformat(), "end": end.isoformat(),
                          "expires_at": min(start, now + timedelta(hours=2)).isoformat()}}


async def free_two_hour_message(db, calendar, settings, now=None):
    return (await free_slot_offer(db, calendar, settings, now))["text"]
