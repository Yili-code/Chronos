"""Standing owner authorization for routine work sessions, with durable receipts."""
from datetime import datetime, time, timedelta
from html import escape

from .task_actions import task_fields
from .task_timing import display_date
from .mail_links import source_url, task_caption


def routine(planner, state):
    if not planner.settings.learning_auto_accept or state.get('status') != 'proposed' or state.get('calendar_moves'):
        return False
    start, end = (datetime.fromisoformat(state[k]).astimezone(planner.settings.tz) for k in ('start', 'end'))
    begin = datetime.combine(start.date(), time(planner.settings.learning_start_hour), planner.settings.tz)
    cutoff = datetime.combine(start.date(), time(planner.settings.learning_end_hour % 24), planner.settings.tz)
    if planner.settings.learning_end_hour == 24:
        cutoff += timedelta(days=1)
    from .study_constraints import approved_window
    return approved_window(state,start,end) if state.get("owner_window") else begin <= start < end <= cutoff


async def accept(planner, task, state, now):
    if not routine(planner, state):
        return False
    from .learning_calendar import class_windows, short_task_windows, overlaps
    from .nightly_planning import deadline
    from .backward_planning import reserved_windows
    await planner.calendar.verify_owner()
    start, end = (datetime.fromisoformat(state[k]) for k in ('start', 'end'))
    busy = await planner.calendar.busy(start, end)
    policy = planner.db.get_task_action_state('learning:course-calendar-policy')
    classes = class_windows(start.astimezone(planner.settings.tz).date(), planner.settings.tz, policy)
    day = start.astimezone(planner.settings.tz).date().isoformat()
    booked = sum((planner.db.get_task_action_state('learning:day:' + day) or {}).values())
    due = deadline(task, planner.settings)
    live = next((t for t in planner.db.list_open_tasks() if t['id'] == task['id']), None)
    valid = (live and task_fields(live) == state.get('snapshot') and start > now
        and end - start == timedelta(minutes=state['minutes']) and (due is None or end <= due)
        and booked + state['minutes'] + sum((b-a).total_seconds()/60 for a,b in classes) <= planner.settings.learning_daily_minutes
        and not overlaps(start, end, [*busy, *classes, *reserved_windows(planner.db),
            *short_task_windows(start.astimezone(planner.settings.tz).date(), planner.settings.tz, state['minutes'])]))
    if not valid:
        planner.update(task['id'], lambda old: {**old, 'status': 'queued'}
            if old and old.get('token') == state['token'] and old.get('status') == 'proposed' else old)
        return True
    creating = planner.update(task['id'], lambda old: {**old, 'status': 'creating',
        'auto_accepted': True, 'approved_at': now.isoformat()}
        if old and old == state else old)
    if creating and creating.get('status') == 'creating' and creating.get('auto_accepted'):
        await planner.book(task, creating, now)
        await receipt(planner, task, planner.get(task['id']))
    return True


async def receipt(planner, task, state):
    if not state or state.get('status') != 'scheduled' or state.get('delivery') != 'scheduled_pending':
        return
    start, end = (datetime.fromisoformat(state[k]).astimezone(planner.settings.tz) for k in ('start', 'end'))
    text = (f"<b>已安排</b>\n{escape(task_caption(task))}\n"
            f"{display_date(start.date().isoformat())} {start:%H:%M}–"
            + (f"{display_date(end.date().isoformat())} " if end.date() != start.date() else '')
            + f"{end:%H:%M}")
    url = source_url(task)
    if url:
        text += f'\n<a href="{escape(url, quote=True)}">開啟郵件</a>'
    await planner.send(task['id'], state, text,
        planner.buttons(task['id'], state, [('adjustbooked', '調整時間')]), 'scheduled')
    current = planner.get(task['id']) or {}
    if url and current.get('delivery') == 'scheduled_sent' and current.get('message_id'):
        import time
        message_id = str(current['message_id'])
        planner.db.mutate_task_action_state('learning:mail-schedule-notices', lambda old: {
            **(old or {}), message_id: time.time() + 7})
        await expire_mail_receipts(planner, wait=True)


async def expire_mail_receipts(planner, *, wait=False):
    """Persist expiry so a later tick can recover interrupted notice cleanup."""
    import asyncio
    import time
    key = 'learning:mail-schedule-notices'
    notices = planner.db.get_task_action_state(key) or {}
    for message_id, deadline in notices.items():
        remaining = deadline - time.time()
        if remaining > 0 and not wait:
            continue
        if remaining > 0:
            await asyncio.sleep(remaining)
        try:
            result = await planner.telegram.request('deleteMessage', {
                'chat_id': planner.settings.telegram_chat_id, 'message_id': int(message_id)})
        except RuntimeError:
            continue
        if result.get('ok') or (result.get('error_code') == 400 and 'not found' in result.get('description', '').lower()):
            planner.db.mutate_task_action_state(key, lambda old: {
                k: v for k, v in (old or {}).items() if k != message_id})
