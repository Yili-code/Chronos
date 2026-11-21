"""Read-only planning and evidence-limited time reports; no model inference."""
from datetime import datetime, time, timedelta, timezone
from html import escape
import heapq
from uuid import uuid4

from .deadline_reminders import deadline_value
from .evening_agenda import render_agenda
from .google_calendar import CalendarError
from .nightly_planning import free_windows
from .study_reflow import bounds
from .telegram import TelegramError


def minutes(start, end):
    return (end.astimezone(timezone.utc)-start.astimezone(timezone.utc)).total_seconds()/60


def intervals(events, start, end, tz):
    result = []
    for event in events:
        if event.get('status') == 'cancelled' or event.get('transparency') == 'transparent':
            continue
        a, b = bounds(event, tz)
        if a.utcoffset() is None or b.utcoffset() is None or minutes(a, b) <= 0:
            raise CalendarError('Calendar contains an invalid event interval. Report unavailable.')
        a, b = (v.astimezone(timezone.utc) for v in (a, b))
        a, b = max(a, start.astimezone(timezone.utc)), min(b, end.astimezone(timezone.utc))
        if a < b:
            result.append((a, b))
    return sorted(result)


def occupied_minutes(windows):
    merged = []
    for a, b in sorted(windows):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return sum(minutes(a, b) for a, b in merged)


def conflict_count(windows):
    active, count = [], 0
    for a, b in sorted(windows):
        while active and active[0] <= a:
            heapq.heappop(active)
        count += len(active)
        heapq.heappush(active, b)
    return count


def deadline_rows(tasks, start, end, tz):
    rows = []
    for task in tasks:
        value = deadline_value(task)
        if not value:
            continue
        if len(value) == 10:
            included = start.date() <= datetime.fromisoformat(value).date() < end.date()
            label = value + ' · time unspecified'
        else:
            due = datetime.fromisoformat(value).astimezone(tz)
            included = start <= due < end
            label = due.strftime('%Y-%m-%d %H:%M %Z')
        if included:
            rows.append(f'{escape(label)} · #{task["id"]} {escape(task["title"][:150])}')
    return sorted(rows)


async def report(kind, db, calendar, settings, *, now=None):
    now = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    midnight = datetime.combine(now.date(), time.min, settings.tz)
    if kind == 'week':
        start = midnight + timedelta(days=7-now.weekday())
        end = start + timedelta(days=7)
    elif kind == 'time':
        start, end = midnight-timedelta(days=7), midnight
    elif kind == 'brief':
        start, end = midnight, midnight+timedelta(days=1)
    else:
        raise ValueError('Unknown report')
    await calendar.verify_owner()
    events = await calendar.agenda(start, end)
    windows = intervals(events, start, end, settings.tz)
    if kind == 'time':
        actual, samples, missing, seen = 0, 0, 0, set()
        for event in events:
            token = event.get('extendedProperties', {}).get('private', {}).get('chronosToken')
            if not token or token in seen:
                continue
            seen.add(token)
            state = db.get_task_action_state('learning:history:'+token) or {}
            feedback = state.get('feedback', {})
            value = feedback.get('actual_minutes')
            recorded = feedback.get('recorded_at')
            if (type(value) not in (int, float) or not 0 <= value <= 720 or not recorded):
                missing += 1
                continue
            try:
                recorded = datetime.fromisoformat(recorded)
                if recorded.utcoffset() is None or not start <= recorded < end:
                    missing += 1
                    continue
            except (ValueError, TypeError):
                missing += 1
                continue
            samples += 1
            actual += value
        text = (f'<b>Time usage · {start:%Y-%m-%d}–{end:%Y-%m-%d} (exclusive end)</b>\n\n'
                f'Planned occupied time: {occupied_minutes(windows):g} minutes (overlaps counted once).\n'
                f'Actual reported time: {actual:g} minutes from {samples} Calendar-linked samples.\n'
                f'Missing or out-of-range reports: {missing}.\n'
                'Actual time is self-reported; this is not complete time tracking. '
                'Unlinked work, category breakdowns and long-term trends are unknown. '
                'Record session duration after work to improve coverage.')
        return [{'text': text, 'parse_mode': 'HTML', 'requires_response': False}]
    tasks = db.list_open_tasks()
    rows = deadline_rows(tasks, start, end, settings.tz)
    if kind == 'brief':
        parts = render_agenda(events, start.date(), settings.tz, label='Daily briefing', db=db, now=now)
        parts.append(f'Overlapping event pairs: {conflict_count(windows)}.\n'
                     'Travel buffers are not inferred; confirm travel time for adjacent appointments.')
    else:
        policy = db.get_task_action_state('learning:course-calendar-policy')
        from .backward_planning import reserved_windows
        blocked = [*windows, *reserved_windows(db)]
        available = []
        for offset in range(7):
            available.extend(free_windows((start+timedelta(days=offset)).date(), blocked, settings, policy))
        available = [(a.astimezone(settings.tz), b.astimezone(settings.tz)) for a, b in available]
        capacity = sum(minutes(a, b) for a, b in available)
        estimates = [(task.get('timing') or {}).get('estimated_minutes') for task in tasks]
        known = sum(value for value in estimates if type(value) is int and value > 0)
        unknown = sum(type(value) is not int or value <= 0 for value in estimates)
        conflicts = conflict_count(windows)
        parts = [(f'<b>Next week · {start:%Y-%m-%d}–{end:%Y-%m-%d} (exclusive end)</b>\n\n'
                  f'Calendar occupied time: {occupied_minutes(windows):g} minutes.\n'
                  f'Available work blocks ≥30 minutes: {capacity:g} minutes.\n'
                  f'Open backlog estimate: {known} minutes; {unknown} tasks have no estimate.\n'
                  f'Overlapping event pairs: {conflicts}.\n'
                  + ('Known backlog exceeds available capacity. Choose priorities before scheduling.\n' if known > capacity else '')
                  + 'Proposed free blocks (no events changed):\n'
                  + ('\n'.join(f'{a:%m/%d %H:%M}–{b:%H:%M}' for a, b in available[:14]) or 'None.')
                  + ('\nAdditional blocks omitted; narrow the date range for detail.' if len(available)>14 else ''))]
        for offset in range(7):
            day = start+timedelta(days=offset)
            day_events = [event for event in events if
                          bounds(event, settings.tz)[0] < day+timedelta(days=1)
                          and bounds(event, settings.tz)[1] > day]
            parts.extend(render_agenda(day_events, day.date(), settings.tz, label='Week', now=now))
    uncertain = [task for task in tasks if (task.get('timing') or {}).get('uncertain')]
    from .tasks import format_task_block
    task_lines = []
    if kind == 'brief':
        for task in tasks:
            task_lines.extend(format_task_block(task, settings.tz, now=now))
    details = ('<b>Deadlines</b>\n' + ('\n'.join(rows) or 'No deadlines in this range.')
               + f'\nOpen tasks: {len(tasks)}; uncertain timing: {len(uncertain)}.'
               + ''.join('\n'+escape(task['title'][:150])+': '+escape(task['timing']['uncertain'][:300])
                         for task in uncertain)
               + ('\n<b>Open tasks</b>\n'+'\n'.join(task_lines) if task_lines else ''))
    # Split at row boundaries, never truncate a deadline or a Telegram HTML tag.
    for row in details.splitlines():
        if not parts or len((parts[-1]+row+'\n').encode('utf-16-le')) // 2 > 3000:
            parts.append('')
        parts[-1] += '\n'+row
    return [{'text': text, 'parse_mode': 'HTML', 'requires_response': False} for text in parts]


async def scheduled(db, calendar, telegram, settings, *, now=None):
    """Use the existing daily scheduler, with durable per-part delivery claims."""
    if not settings.telegram_chat_id or not telegram.enabled:
        return {'sent': 0}
    now = now or datetime.now(settings.tz)
    sent = 0
    for kind in (['brief', 'week'] if now.astimezone(settings.tz).weekday() == 0 else ['brief']):
        key = f'executive:{kind}:{settings.telegram_chat_id}:{now.astimezone(settings.tz).date()}'
        state = db.get_task_action_state(key)
        if not state:
            parts = await report(kind, db, calendar, settings, now=now)
            state = db.mutate_task_action_state(key, lambda old: old or
                       {'parts': parts, 'next': 0, 'status': 'pending', 'generated_at': now.isoformat()})
        while state['next'] < len(state['parts']):
            claim = uuid4().hex
            state = db.mutate_task_action_state(key, lambda old:
                {**old, 'status': 'sending', 'claim': claim} if old['status'] == 'pending' else old)
            if state.get('claim') != claim:
                if state['status'] in {'sending', 'uncertain'}:
                    raise RuntimeError('Executive report delivery needs inspection; no blind retry.')
                break
            try:
                result = await telegram.send_message(settings.telegram_chat_id, **{**state['parts'][state['next']], 'requires_response': False})
            except TelegramError:
                db.mutate_task_action_state(key, lambda old: {**old, 'status': 'uncertain'})
                raise RuntimeError('Executive report delivery is uncertain; inspect before retrying.') from None
            if not result.get('ok'):
                status = 'pending' if 400 <= result.get('error_code', 500) < 500 else 'uncertain'
                db.mutate_task_action_state(key, lambda old: {**old, 'status': status})
                raise RuntimeError('Executive report delivery failed; inspect delivery state.')
            state = db.mutate_task_action_state(key, lambda old: {**old, 'next': old['next']+1,
                'status': 'sent' if old['next']+1 == len(old['parts']) else 'pending'})
            sent += 1
    return {'sent': sent}
