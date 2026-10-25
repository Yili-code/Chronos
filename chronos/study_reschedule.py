"""Explicit owner edits of existing study bookings, independent of proposal turns."""
from datetime import datetime, timedelta
from hashlib import sha256
import re
from uuid import uuid4

from .ai import AIError
from .google_calendar import CalendarError
from .study_reflow import bounds
from .task_actions import task_fields
from .temporal_constraints import literal_weekday_conflict


def verified_move(event, event_id, task_id, token, pending, tz):
    props = (event or {}).get('extendedProperties', {}).get('private', {})
    try:
        clocks = [datetime.fromisoformat(event[k]['dateTime'].replace('Z', '+00:00')) for k in ('start', 'end')]
        return bool(event.get('id') == event_id and event.get('status') != 'cancelled'
                    and all(value.utcoffset() is not None for value in clocks)
                    and props.get('chronosTask') == str(task_id) and props.get('chronosToken') == token
                    and props.get('chronosReschedule') == pending['marker']
                    and tuple(value.astimezone(tz) for value in clocks) == tuple(datetime.fromisoformat(pending[k]) for k in ('start', 'end')))
    except (KeyError, ValueError, TypeError):
        return False


def receipt_key(update_id):
    return f'learning:edit-receipt:{update_id}'


def success_reply(pending):
    return f"Work block updated\n{pending['start']} · {pending['minutes']} minutes" + (
        '\n' + pending['progress_note'] if pending['progress_note'] else '')


async def reschedule(planner, task_id, token, instruction, now, *, update_id=None):
    from .learning_calendar import find_slot
    state = planner.get(task_id)
    task = next((t for t in planner.db.list_open_tasks() if t['id'] == task_id), None)
    if not task or state.get('token') != token or state['status'] != 'scheduled':
        return 'The booking changed. Use /plan and try again.'
    await planner.calendar.verify_owner()
    event = await planner.calendar.event(state['event_id'])
    props = (event or {}).get('extendedProperties', {}).get('private', {})
    if (not event or event.get('status') == 'cancelled' or props.get('chronosTask') != str(task_id)
            or props.get('chronosToken') != token or not event.get('etag')):
        raise CalendarError('The booked event is missing or its ownership cannot be verified. Nothing changed.')
    pending = state.get('pending_reschedule')
    if pending:
        if props.get('chronosReschedule') == pending['marker']:
            if not verified_move(event, state['event_id'], task_id, token, pending, planner.settings.tz):
                raise CalendarError('The moved event changed again. Check Calendar before retrying.')
            reply = 'Previous calendar change recovered. Review /plan, then send any further edit again.'
            record(planner, task_id, token, pending, update_id=update_id, reply=reply)
            return reply
        if event['etag'] != pending['etag']:
            raise CalendarError('The booked event changed during an uncertain edit. Check Calendar before retrying.')
        if (pending.get('request_digest') != sha256(instruction.strip().encode()).hexdigest()
                and instruction.strip().casefold() not in {'retry', '重試', '重试'}):
            return 'An earlier calendar edit needs reconciliation. Repeat its original request or send retry to check it first; this new request has not been applied.'
    elif bounds(event, planner.settings.tz) != tuple(datetime.fromisoformat(state[k]) for k in ('start', 'end')):
        raise CalendarError('The event was edited outside Chronos. Check Calendar before changing it here.')
    window = None
    if not pending:
        conflict = literal_weekday_conflict(instruction, now.astimezone(planner.settings.tz))
        if conflict:
            return conflict
        note = state.get('progress_note', '')
        match = re.fullmatch(r'\s*(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2})\s+(\d{1,2}:\d{2})(?:\s+(\d{1,3}))?\s*', instruction)
        try:
            if match:
                day = match[1] if '-' in match[1] else f'{now.year}-{int(match[1].split("/")[0]):02d}-{int(match[1].split("/")[1]):02d}'
                start = datetime.fromisoformat(day + 'T' + match[2].zfill(5)).replace(tzinfo=planner.settings.tz)
                minutes = int(match[3]) if match[3] else state['minutes']
            else:
                if planner.ai is None:
                    return 'Use /planedit ID MM/DD HH:MM minutes. The booking is unchanged.'
                edit = await planner.ai.parse_schedule_edit(instruction, task, state, now)
                if not edit.actionable:
                    return 'Please specify a time, a clear time window or a duration. The booking is unchanged.'
                if edit.timing == 'window':
                    window = (datetime.fromisoformat(edit.start), datetime.fromisoformat(edit.window_end))
                    if any(v.tzinfo is None for v in window) or not timedelta(0) < window[1] - window[0] <= timedelta(days=7):
                        raise ValueError
                    start = window[0]
                elif edit.timing == 'soon':
                    window = (now, now + timedelta(days=7))
                    start = now
                else:
                    start = datetime.fromisoformat(edit.start if edit.timing == 'exact' else state['start'])
                minutes = edit.minutes if edit.minutes is not None else state['minutes']
                note = edit.progress_note or note
            if start.tzinfo is None or not 10 <= minutes <= 180:
                raise ValueError
            start = start.astimezone(planner.settings.tz)
            end = start + timedelta(minutes=minutes)
        except (ValueError, TypeError, OverflowError, AIError):
            return 'Could not interpret the time or duration. The booking is unchanged.'
        pending = {'marker': uuid4().hex, 'etag': event['etag'], 'start': start.isoformat(),
                   'end': end.isoformat(), 'minutes': minutes, 'progress_note': note,
                   'request_digest': sha256(instruction.strip().encode()).hexdigest(),
                   'update_id': update_id,
                   'snapshot': task_fields(task)}
    start, end = (datetime.fromisoformat(pending[k]) for k in ('start', 'end'))
    if pending['snapshot'] != task_fields(task):
        raise CalendarError('The task changed during this edit. Check /plan before retrying.')
    agenda = await planner.calendar.agenda(*(window or (start, end)))
    own_ids = {planner.settings.calendar_id.casefold()}
    if planner.settings.calendar_id.casefold() in {'primary', planner.settings.calendar_account.casefold()}:
        own_ids.update({'primary', planner.settings.calendar_account.casefold()})
    busy = [bounds(e, planner.settings.tz) for e in agenda if e.get('transparency') != 'transparent'
            and not (e.get('id') == state['event_id'] and e.get('_calendar_id', '').casefold() in own_ids)]
    reservations = [(datetime.fromisoformat(s['start']), datetime.fromisoformat(s['end']))
                    for t in planner.db.list_open_tasks() if t['id'] != task_id
                    for s in [planner.get(t['id'])] if s and s.get('start') and s['status'] == 'creating']
    budget = {}
    for offset in range(8 if window else 1):
        day = (start + timedelta(days=offset)).astimezone(planner.settings.tz).date().isoformat()
        bucket = planner.db.get_task_action_state('learning:day:' + day) or {}
        budget[day] = sum(v for k, v in bucket.items() if k != token)
    policy = planner.db.get_task_action_state('learning:course-calendar-policy')
    slot = find_slot(task, now, busy, reservations, planner.settings, pending['minutes'], budget, policy, window=window or (start, end), align_minutes=window is not None)
    if window and slot:
        start, end = slot
        pending.update(start=start.isoformat(), end=end.isoformat())
    if not slot or slot != (start, end):
        return 'That time conflicts with your calendar, deadline or study limits. The booking is unchanged.'
    def reserve():
        current_task = planner.db.get_task(task_id)
        current = planner.get(task_id)
        if (not current_task or current_task['status'] != 'open'
                or task_fields(current_task) != pending['snapshot'] or current != state):
            return False
        planner.db._save_task_action_state(f'learning:task:{task_id}', {**current, 'pending_reschedule': pending})
        return True
    if not planner.db._run_task_action(reserve):
        return 'The booking changed. Try again from /plan.'
    await planner.calendar.move_event(state['event_id'], {
        'start': {'dateTime': pending['start'], 'timeZone': planner.settings.timezone},
        'end': {'dateTime': pending['end'], 'timeZone': planner.settings.timezone},
        'extendedProperties': {**event.get('extendedProperties', {}),
                               'private': {**props, 'chronosReschedule': pending['marker']}}}, pending['etag'])
    verified = await planner.calendar.event(state['event_id'])
    if not verified_move(verified, state['event_id'], task_id, token, pending, planner.settings.tz):
        raise CalendarError('The calendar change could not be verified. Check /plan and retry to reconcile; no success is recorded.')
    reply = success_reply(pending)
    record(planner, task_id, token, pending, update_id=update_id, reply=reply)
    return reply


def record(planner, task_id, token, pending, *, update_id=None, reply=None):
    def save():
        state = planner.get(task_id)
        task = planner.db.get_task(task_id)
        if (not task or task['status'] != 'open' or task_fields(task) != pending['snapshot']
                or state.get('token') != token or state['status'] != 'scheduled'
                or state.get('pending_reschedule') != pending):
            raise CalendarError('Calendar moved, but the task changed. Reconciliation is required.')
        days = {state['start'][:10], pending['start'][:10]}
        buckets = {d: planner.db.get_task_action_state('learning:day:' + d) or {} for d in days}
        buckets[state['start'][:10]].pop(token, None)
        buckets[pending['start'][:10]][token] = pending['minutes']
        for day, bucket in buckets.items():
            planner.db._save_task_action_state('learning:day:' + day, bucket)
        updated = {**state, **{k: pending[k] for k in ('start', 'end', 'minutes', 'progress_note')}}
        updated.pop('pending_reschedule', None)
        planner.db._save_task_action_state(f'learning:task:{task_id}', updated)
        replies = {}
        if pending.get('update_id') is not None:
            replies[pending['update_id']] = success_reply(pending)
        if update_id is not None:
            replies[update_id] = reply or success_reply(pending)
        for request_id, text in replies.items():
            planner.db._save_task_action_state(receipt_key(request_id), {'reply': text})
    planner.db._run_task_action(save)
