"""Read-only search for the next uninterrupted two-hour calendar opening."""
from datetime import datetime, timedelta, time

from .learning_calendar import short_task_windows, overlaps
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
        from .nightly_planning import class_blocks
        blocked = [*busy, *class_blocks(day, settings, course_policy),
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
    from .backward_planning import reserved_windows
    busy = [*busy,*reserved_windows(db)]
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


async def bounded_free_message(db, calendar, settings, start, end, now):
    """Report actual gaps within owner bounds; never replace them with next week."""
    from .nightly_planning import free_windows
    from .backward_planning import reserved_windows
    start,end,now=[v.astimezone(settings.tz) for v in (start,end,now)]
    start=max(start,now)
    if start>=end:
        return '指定的時間範圍已結束，沒有查詢其他日期。'
    await calendar.verify_owner()
    busy=[*await calendar.busy(start,end),*reserved_windows(db)]
    policy=db.get_task_action_state('learning:course-calendar-policy')
    gaps=[]
    for offset in range((end.date()-start.date()).days+1):
        day=start.date()+timedelta(days=offset)
        for a,b in free_windows(day,busy,settings,policy,minimum_minutes=1):
            a,b=max(a,start),min(b,end)
            if a.second or a.microsecond:a=a.replace(second=0,microsecond=0)+timedelta(minutes=1)
            if b-a>=timedelta(minutes=1):gaps.append((a,b))
    total=sum(int((b-a).total_seconds()//60) for a,b in gaps)
    lines=[f'{start:%m/%d %H:%M}–{end:%m/%d %H:%M} 的可用空檔：共 {total} 分鐘。',
           f'以 {settings.learning_start_hour:02d}:00–{settings.learning_end_hour:02d}:00 為範圍，已扣除課程、課前準備、用餐及已保留時段。']
    lines.extend(f'{a:%m/%d %H:%M}–{b:%H:%M} · {int((b-a).total_seconds()//60)} 分鐘' for a,b in gaps)
    return '\n'.join(lines)
