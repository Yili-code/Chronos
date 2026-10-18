"""Explicit owner edits of existing study bookings, independent of proposal turns."""
from datetime import datetime, timedelta
import re
from uuid import uuid4

from .ai import AIError
from .google_calendar import CalendarError
from .study_reflow import bounds
from .task_actions import task_fields


async def reschedule(planner, task_id, token, instruction, now):
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
            if bounds(event, planner.settings.tz) != tuple(datetime.fromisoformat(pending[k]) for k in ('start', 'end')):
                raise CalendarError('The moved event changed again. Check Calendar before retrying.')
            record(planner, task_id, token, pending)
            return 'Previous calendar change recovered. Review /plan, then send any further edit again.'
        if event['etag'] != pending['etag']:
            raise CalendarError('The booked event changed during an uncertain edit. Check Calendar before retrying.')
    elif bounds(event, planner.settings.tz) != tuple(datetime.fromisoformat(state[k]) for k in ('start', 'end')):
        raise CalendarError('The event was edited outside Chronos. Check Calendar before changing it here.')
    if not pending:
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
                if not edit.actionable or edit.timing not in {'exact', 'keep'}:
                    return 'Provide an exact start time for this booked event. The booking is unchanged.'
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
                   'snapshot': task_fields(task)}
    start, end = (datetime.fromisoformat(pending[k]) for k in ('start', 'end'))
    if pending['snapshot'] != task_fields(task):
        raise CalendarError('The task changed during this edit. Check /plan before retrying.')
    agenda = await planner.calendar.agenda(start, end)
    own_ids = {planner.settings.calendar_id.casefold()}
    if planner.settings.calendar_id.casefold() in {'primary', planner.settings.calendar_account.casefold()}:
        own_ids.update({'primary', planner.settings.calendar_account.casefold()})
    busy = [bounds(e, planner.settings.tz) for e in agenda if e.get('transparency') != 'transparent'
            and not (e.get('id') == state['event_id'] and e.get('_calendar_id', '').casefold() in own_ids)]
    reservations = [(datetime.fromisoformat(s['start']), datetime.fromisoformat(s['end']))
                    for t in planner.db.list_open_tasks() if t['id'] != task_id
                    for s in [planner.get(t['id'])] if s and s.get('start') and s['status'] == 'creating']
    bucket = planner.db.get_task_action_state('learning:day:' + start.date().isoformat()) or {}
    budget = {start.date().isoformat(): sum(v for k, v in bucket.items() if k != token)}
    policy = planner.db.get_task_action_state('learning:course-calendar-policy')
    slot = find_slot(task, now, busy, reservations, planner.settings, pending['minutes'], budget, policy, window=(start, end))
    if slot != (start, end):
        return 'That time conflicts with your calendar, deadline or study limits. The booking is unchanged.'
    saved = planner.update(task_id, lambda old: {**old, 'pending_reschedule': pending}
                           if old.get('token') == token and old['status'] == 'scheduled' else old)
    if saved.get('pending_reschedule') != pending:
        return 'The booking changed. Try again from /plan.'
    await planner.calendar.move_event(state['event_id'], {
        'start': {'dateTime': pending['start'], 'timeZone': planner.settings.timezone},
        'end': {'dateTime': pending['end'], 'timeZone': planner.settings.timezone},
        'extendedProperties': {**event.get('extendedProperties', {}),
                               'private': {**props, 'chronosReschedule': pending['marker']}}}, pending['etag'])
    record(planner, task_id, token, pending)
    return f"Study booking updated\n{pending['start']} · {pending['minutes']} minutes" + (
        '\n' + pending['progress_note'] if pending['progress_note'] else '')


def record(planner, task_id, token, pending):
    def save():
        state = planner.get(task_id)
        if state.get('token') != token or state['status'] != 'scheduled':
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
    planner.db._run_task_action(save)
