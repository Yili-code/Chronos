"""Validated whole-day allocation; Calendar writes still require owner approval."""
from datetime import datetime, time, timedelta
from html import escape
import re
from uuid import uuid4

from .ai import AIError, NightlyPlanOutput
from .google_calendar import CalendarError
from .task_actions import task_fields
from .tasks import is_course_scope_task


def day_bounds(day, settings):
    begin = datetime.combine(day, time(settings.learning_start_hour), settings.tz)
    end = datetime.combine(day, time(settings.learning_end_hour % 24), settings.tz)
    return begin, end + (timedelta(days=1) if settings.learning_end_hour == 24 else timedelta())


def class_blocks(day, settings, policy):
    from .learning_calendar import class_windows
    return [(start - timedelta(minutes=settings.learning_class_preparation_minutes), end)
            for start, end in class_windows(day, settings.tz, policy)]


def free_windows(day, busy, settings, policy=None, *, minimum_minutes=30):
    """Subtract merged commitments; 30 minutes is a gap threshold, not a grid."""
    from .learning_calendar import short_task_windows
    begin, end = day_bounds(day, settings)
    blocked = sorted((max(begin, a), min(end, b)) for a, b in
        [*busy, *class_blocks(day, settings, policy), *short_task_windows(day, settings.tz, 30)]
        if a < end and b > begin)
    cursor, result = begin, []
    for start, stop in blocked:
        if start - cursor >= timedelta(minutes=minimum_minutes):
            result.append((cursor, start))
        cursor = max(cursor, stop)
    if end - cursor >= timedelta(minutes=minimum_minutes):
        result.append((cursor, end))
    return result


def eligible(task, state, now):
    if is_course_scope_task(task):
        return False
    if ((task.get('timing') or {}).get('event') and re.search(r'contest|meeting|gathering', task['title'], re.I)
            and not re.match(r'prepare|study|review|write|complete', task['title'], re.I)):
        return False
    if not state:
        return True
    # Refresh only expired, unbooked nightly work. Explicit deferrals and uncertain delivery stay untouched.
    return bool(state.get('nightly_slot') and state.get('duration_source') == 'nightly_ai' and state.get('status') in {'queued', 'proposed', 'no_slot', 'needs_replan'}
        and state['nightly_slot']['end'] <= now.isoformat()
        and not state.get('delivery', '').endswith(('_uncertain', '_sending')))


def state_window(state):
    return state.get('nightly_slot', state) if state.get('duration_source') == 'nightly_ai' else state


def check_slot(planner, task, state, now, busy, booked_minutes=None):
    """Return a valid exact allocation or concrete reasons why it was invalidated."""
    from .learning_calendar import class_windows, overlaps, short_task_windows
    from .backward_planning import reserved_windows
    cfg = planner.settings
    start, end = (datetime.fromisoformat(state['nightly_slot'][k]).astimezone(cfg.tz) for k in ('start', 'end'))
    from .study_constraints import approved_window
    begin, stop = day_bounds(start.date(), cfg.model_copy(update={'learning_start_hour':0,'learning_end_hour':24}) if state.get('owner_window') else cfg)
    working_allowed=approved_window(state,start,end) if state.get('owner_window') else begin<=start<end<=stop
    due = deadline(task, cfg)
    policy = planner.db.get_task_action_state('learning:course-calendar-policy')
    classes = class_blocks(start.date(), cfg, policy)
    reserved = reserved_windows(planner.db)
    pending = []
    load = sum((b - a).total_seconds() / 60 for a, b in class_windows(start.date(), cfg.tz, policy))
    day_key = start.date().isoformat()
    load += (booked_minutes[day_key] if booked_minutes is not None and day_key in booked_minutes else
             sum((planner.db.get_task_action_state('learning:day:' + day_key) or {}).values()))
    for other in planner.db.list_open_tasks():
        old = planner.get(other['id'])
        if other['id'] == task['id'] or not old or old.get('status') not in {'queued', 'planning', 'proposed', 'editing', 'creating'}:
            continue
        source = state_window(old)
        if source.get('start') and source.get('end'):
            a, b = (datetime.fromisoformat(source[k]).astimezone(cfg.tz) for k in ('start', 'end'))
            pending.append((a, b))
            load += max(0, (min(stop, b) - max(begin, a)).total_seconds() / 60)
    reasons = []
    if not working_allowed:
        reasons.append('working_hours')
    if start <= now:
        reasons.append('elapsed')
    if due is not None and end > due:
        reasons.append('deadline')
    if int((end - start).total_seconds() / 60) != state['requested_minutes']:
        reasons.append('duration_changed')
    if load + state['requested_minutes'] > cfg.learning_daily_minutes:
        reasons.append('daily_limit')
    if overlaps(start, end, [*busy, *reserved]):
        reasons.append('calendar_conflict')
    if overlaps(start, end, [*classes, *short_task_windows(start.date(), cfg.tz, 30)]):
        reasons.append('fixed_commitment')
    if overlaps(start, end, pending):
        reasons.append('pending_plan')
    return ((start, end) if not reasons else None), reasons


def slot_allowed(planner, task, state, now, busy, booked_minutes=None):
    return check_slot(planner, task, state, now, busy, booked_minutes)[0]


def replacement_slot(planner, task, state, now, busy, booked_minutes=None):
    """Search current capacity while retaining every other pending allocation."""
    from .learning_calendar import find_slot, short_task_windows
    from .backward_planning import reserved_windows
    cfg, db = planner.settings, planner.db
    policy = db.get_task_action_state('learning:course-calendar-policy')
    pending = []
    for other in db.list_open_tasks():
        old = planner.get(other['id'])
        if other['id'] == task['id'] or not old or old.get('status') not in {'queued', 'planning', 'proposed', 'editing', 'creating'}:
            continue
        source = state_window(old)
        if source.get('start') and source.get('end'):
            pending.append(tuple(datetime.fromisoformat(source[k]).astimezone(cfg.tz) for k in ('start', 'end')))
    budgets, classes = {}, []
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        key = day.isoformat()
        begin, end = day_bounds(day, cfg)
        classes.extend([*class_blocks(day, cfg, policy), *short_task_windows(day, cfg.tz, 30)])
        budgets[key] = ((booked_minutes[key] if booked_minutes is not None and key in booked_minutes else
                        sum((db.get_task_action_state('learning:day:' + key) or {}).values()))
                        + sum(max(0, (min(end, b) - max(begin, a)).total_seconds() / 60) for a, b in pending))
    return find_slot(task, now, [*busy, *reserved_windows(db), *pending, *classes], [], cfg,
                     state['requested_minutes'], budgets, policy)


def deadline(task, settings):
    value = task.get('due_at') or (task.get('timing') or {}).get('due_date')
    if not value:
        return None
    return (datetime.fromisoformat(value).astimezone(settings.tz) if len(value) > 10 else
            datetime.combine(datetime.fromisoformat(value).date(), time.max, settings.tz))


def validate(output, tasks, windows, budget, settings):
    output = NightlyPlanOutput.model_validate(output)
    by_id, seen, slots, total = {t['id']: t for t in tasks}, set(), [], 0
    for item in sorted(output.sessions, key=lambda s: s.start.isoformat()):
        if item.task_id not in by_id or item.task_id in seen:
            raise ValueError('Unknown or repeated task in nightly plan')
        if item.start.utcoffset() is None or item.end.utcoffset() is None:
            raise ValueError('Nightly times need a timezone')
        start, end = item.start.astimezone(settings.tz), item.end.astimezone(settings.tz)
        seconds = (end - start).total_seconds()
        if not 600 <= seconds <= 10800 or seconds % 60 or start.second or start.microsecond or end.second or end.microsecond:
            raise ValueError('Invalid whole-minute work session')
        minutes = int(seconds / 60)
        if minutes > item.estimated_minutes or not any(a <= start < end <= b for a, b in windows):
            raise ValueError('Session exceeds available time or remaining work')
        due = deadline(by_id[item.task_id], settings)
        if due and end > due:
            raise ValueError('Session exceeds task deadline')
        if any(start < b and end > a for a, b in slots):
            raise ValueError('Overlapping nightly sessions')
        seen.add(item.task_id)
        slots.append((start, end))
        total += minutes
    if total > budget:
        raise ValueError('Nightly workload budget exceeded')
    return sorted(output.sessions, key=lambda s: s.start.astimezone(settings.tz))


def overview(sessions, tasks, settings, day):
    by_id = {t['id']: t for t in tasks}
    lines = [f"<b>Tomorrow's task plan · {day:%m/%d}</b>"]
    for item in sessions:
        start, end = item.start.astimezone(settings.tz), item.end.astimezone(settings.tz)
        lines.append(f"{start:%H:%M}–{end:%H:%M} · #{item.task_id} {escape(by_id[item.task_id]['title'][:40])}")
    lines.append('預覽而已；每項行程確認後才加入 Calendar。')
    return '\n'.join(lines)


async def run(planner, now=None):
    cfg, db = planner.settings, planner.db
    if not cfg.enable_learning_calendar:
        return {'enabled': False}
    if not cfg.learning_enabled_since or not planner.calendar.configured():
        return {'enabled': True, 'waiting_for': 'calendar authorization and activation'}
    now = (now or datetime.now(cfg.tz)).astimezone(cfg.tz)
    if now.hour != 21:
        return {'waiting_for': '21:00 Asia/Taipei'}
    day, claim = now.date(), uuid4().hex
    target = day + timedelta(days=1)
    begin, end = day_bounds(target, cfg)
    key = 'learning:nightly:' + day.isoformat()

    def acquire(old):
        if old and (old.get('status') == 'complete' or 'status' not in old):
            return old
        if old and old.get('status') == 'planning' and old.get('lease_until', '') > now.isoformat():
            return old
        return {'date': day.isoformat(), 'target_date': target.isoformat(), 'status': 'planning',
                'claim': claim, 'lease_until': (now + timedelta(minutes=5)).isoformat()}
    receipt = db.mutate_task_action_state(key, acquire)
    if receipt.get('status') == 'complete' or 'status' not in receipt:
        result = await planner.tick(now) if receipt.get('task_ids') else {}
        return {**result, 'nightly_task_ids': receipt.get('task_ids', []), 'date': day.isoformat()}
    if receipt.get('claim') != claim:
        return {'already_running': True, 'date': day.isoformat()}
    try:
        items = db.list_open_tasks()
        states = {t['id']: planner.get(t['id']) for t in items}
        expired = {t['id'] for t in items if states[t['id']] and eligible(t, states[t['id']], now)}
        candidates = [t for t in items if eligible(t, states[t['id']], now)
                      and (deadline(t, cfg) is None or deadline(t, cfg) > begin)]
        candidates.sort(key=lambda t: (deadline(t, cfg) is None,
            deadline(t, cfg) or end + timedelta(days=36500), t['created_at'], t['id']))
        candidates = candidates[:20]
        policy = db.get_task_action_state('learning:course-calendar-policy')
        booked = sum((db.get_task_action_state('learning:day:' + target.isoformat()) or {}).values())
        history = db.get_task_action_state('learning:profile') or {}
        reservations, pending_minutes = [], 0
        candidate_ids = {t['id'] for t in candidates}
        for task in items:
            state = states[task['id']]
            if not state or task['id'] in candidate_ids:
                continue
            source = state_window(state)
            if state.get('status') in {'queued', 'planning', 'proposed', 'editing', 'creating', 'scheduled', 'awaiting_feedback'} and source.get('start') and source.get('end'):
                a, b = (datetime.fromisoformat(source[k]).astimezone(cfg.tz) for k in ('start', 'end'))
                reservations.append((a, b))
                if state['status'] not in {'scheduled', 'awaiting_feedback'}:
                    pending_minutes += max(0, (min(end, b) - max(begin, a)).total_seconds() / 60)
        from .learning_calendar import class_windows
        from .backward_planning import reserved_windows
        class_minutes = sum((b - a).total_seconds() / 60 for a, b in class_windows(target, cfg.tz, policy))
        budget = max(0, int(cfg.learning_daily_minutes - class_minutes - booked - pending_minutes))
        windows, sessions = [], []
        if candidates:
            await planner.calendar.verify_owner()
            busy = await planner.calendar.busy(begin, end)
            windows = free_windows(target, [*busy, *reservations, *reserved_windows(db)], cfg, policy)
            if windows and budget >= 10:
                if planner.ai is None:
                    raise AIError('Nightly planning requires AI duration allocation')
                output = await planner.ai.plan_tomorrow(
                    [{'id': t['id'], **task_fields(t), 'created_at': t['created_at']} for t in candidates],
                    [{'start': a.isoformat(), 'end': b.isoformat()} for a, b in windows], budget, history)
                sessions = validate(output, candidates, windows, budget, cfg)
        summary = overview(sessions, candidates, cfg, target) if sessions else ''
        by_id = {t['id']: t for t in candidates}

        def save():
            current_receipt = db.get_task_action_state(key)
            current = {t['id']: t for t in db.list_open_tasks()}
            current_states = {i: planner.get(i) for i in set(by_id) | expired}
            queue = db.get_task_action_state('learning:proposal-queue') or {}
            if not current_receipt or current_receipt.get('claim') != claim:
                return None
            if (any(i not in current or task_fields(current[i]) != task_fields(by_id[i]) or current_states[i] != states[i] for i in by_id)
                    or any(current_states[i] != states[i] for i in expired)):
                raise CalendarError('Tasks changed during nightly allocation; retry before applying a plan.')
            retired = list(queue.get('retire_message_ids', []))
            selected_ids = {s.task_id for s in sessions}
            expired_unselected = expired - selected_ids
            for i in expired_unselected:
                old = current_states[i]
                retired.extend(old[k] for k in ('message_id', 'edit_message_id') if old.get(k))
                db._save_task_action_state(f'learning:task:{i}', {**old, 'status': 'needs_replan'})
            for index, item in enumerate(sessions):
                old = current_states[item.task_id] or {}
                retired.extend(old[k] for k in ('message_id', 'edit_message_id') if old.get(k))
                start, stop = item.start.astimezone(cfg.tz), item.end.astimezone(cfg.tz)
                minutes = int((stop - start).total_seconds() / 60)
                db._save_task_action_state(f'learning:task:{item.task_id}', {
                    'status': 'queued', 'token': uuid4().hex, 'nightly_batch': day.isoformat(),
                    'nightly_slot': {'start': start.isoformat(), 'end': stop.isoformat()},
                    'nightly_overview': summary if index == 0 else '',
                    'nightly_reason': item.reason, 'snapshot': task_fields(by_id[item.task_id]),
                    'requested_minutes': minutes, 'duration_source': 'nightly_ai',
                    'duration_estimate': {'estimated_minutes': item.estimated_minutes}})
            db._save_task_action_state('learning:proposal-queue', {**queue,
                'active_task_id': None if queue.get('active_task_id') in expired_unselected else queue.get('active_task_id'),
                'retire_message_ids': list(dict.fromkeys(retired))})
            saved = {**current_receipt, 'status': 'complete', 'task_ids': [s.task_id for s in sessions],
                'windows': [{'start': a.isoformat(), 'end': b.isoformat()} for a, b in windows],
                'budget_minutes': budget, 'sessions': [s.model_dump(mode='json') for s in sessions]}
            db._save_task_action_state(key, saved)
            return saved
        saved = db._run_task_action(save)
        if not saved:
            return {'already_running': True, 'date': day.isoformat()}
    except (AIError, ValueError, CalendarError) as error:
        db.mutate_task_action_state(key, lambda old: {**old, 'status': 'failed'}
            if old and old.get('claim') == claim and old.get('status') == 'planning' else old)
        raise CalendarError('Tomorrow planning was not saved: ' + str(error)) from None
    result = await planner.tick(now) if saved['task_ids'] else {}
    return {**result, 'nightly_task_ids': saved['task_ids'], 'date': day.isoformat(),
            'target_date': target.isoformat(), 'free_windows': saved['windows']}
