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
from .calendar_ownership import owned_single_event


def verified_move(event, event_id, task_id, token, pending, cfg):
    props = (event or {}).get('extendedProperties', {}).get('private', {})
    try:
        clocks = [datetime.fromisoformat(event[k]['dateTime'].replace('Z', '+00:00')) for k in ('start', 'end')]
        return bool(event.get('id') == event_id and owned_single_event(event, cfg)
                    and all(value.utcoffset() is not None for value in clocks)
                    and props.get('chronosTask') == str(task_id) and props.get('chronosToken') == token
                    and props.get('chronosReschedule') == pending['marker']
                    and tuple(value.astimezone(cfg.tz) for value in clocks) == tuple(datetime.fromisoformat(pending[k]) for k in ('start', 'end')))
    except (KeyError, ValueError, TypeError):
        return False


def receipt_key(update_id):
    return f'learning:edit-receipt:{update_id}'


def success_reply(pending):
    return f"Work block updated\n{pending['start']} · {pending['minutes']} minutes" + (
        '\n' + pending['progress_note'] if pending['progress_note'] else '')


def drift_review(planner, task_id, state, task, saved, live, event, minutes, now):
    review_id = uuid4().hex
    start, end = live[0], live[0] + timedelta(minutes=minutes)
    if end >= live[1] or end <= now:
        return "保留 Calendar 的開始時間時，只能縮短時段，且新的結束時間必須在現在之後。"
    review = {"id": review_id, "etag": event["etag"], "snapshot": task_fields(task),
              "live": [value.isoformat() for value in live], "minutes": minutes,
              "expires_at": (now + timedelta(minutes=15)).isoformat()}
    changed = planner.update(task_id, lambda old: {**old, "drift_review": review} if old == state else old)
    if ((changed or {}).get("drift_review") or {}).get("id") != review_id:
        return "安排已更新。請重新提出時間調整。"

    def label(pair):
        return f"{pair[0]:%m/%d %H:%M}–{pair[1]:%m/%d %H:%M}"

    return {"messages": [{
        "text": (f"{task['title']}\nChronos 紀錄：{label(saved)}\nCalendar 目前：{label(live)}\n\n"
                 f"保留 Calendar 的開始時間，改為 {label((start, end))}（{minutes} 分鐘）？"),
        "reply_markup": {"inline_keyboard": [[{"text": "確認調整", "callback_data": f"bd:{task_id}:{review_id}"}]]},
    }]}


async def reschedule(planner, task_id, token, instruction, now, *, update_id=None, review_id=None):
    from .learning_calendar import find_slot
    state = planner.get(task_id)
    task = next((t for t in planner.db.list_open_tasks() if t['id'] == task_id), None)
    if not task or not state or state.get('token') != token or state['status'] != 'scheduled':
        return 'The booking changed. Use /plan and try again.'
    review = state.get("drift_review") if review_id else None
    if review_id and (not review or review["id"] != review_id):
        return "這項確認已失效。請重新提出時間調整。"
    await planner.calendar.verify_owner()
    event = await planner.calendar.event(state['event_id'])
    props = (event or {}).get('extendedProperties', {}).get('private', {})
    if (not event or event.get('id') != state['event_id'] or event.get('status') == 'cancelled' or props.get('chronosTask') != str(task_id)
            or props.get('chronosToken') != token or not owned_single_event(event, planner.settings)):
        raise CalendarError('The booked event is missing or no longer a verified owner-only single event. Check Calendar before retrying.')
    pending = state.get('pending_reschedule')
    if pending:
        if review_id and pending.get("review_id") != review_id:
            return "另一項行程修改正在核對。請查看 /plan。"
        if props.get('chronosReschedule') == pending['marker']:
            if not verified_move(event, state['event_id'], task_id, token, pending, planner.settings):
                raise CalendarError('The moved event changed again. Check Calendar before retrying.')
            reply = 'Previous calendar change recovered. Review /plan, then send any further edit again.'
            record(planner, task_id, token, pending, update_id=update_id, reply=reply)
            return reply
        if event['etag'] != pending['etag']:
            raise CalendarError('The booked event changed during an uncertain edit. Check Calendar before retrying.')
        if (pending.get('request_digest') != sha256(instruction.strip().encode()).hexdigest()
                and instruction.strip().casefold() not in {'retry', '重試', '重试'}):
            return 'An earlier calendar edit needs reconciliation. Repeat its original request or send retry to check it first; this new request has not been applied.'
    window = None
    if not pending:
        live = bounds(event, planner.settings.tz)
        saved = tuple(datetime.fromisoformat(state[k]).astimezone(planner.settings.tz) for k in ('start', 'end'))
        conflict = literal_weekday_conflict(instruction, now.astimezone(planner.settings.tz))
        if conflict:
            return conflict
        note = state.get('progress_note', '')
        match = re.fullmatch(r'\s*(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2})\s+(\d{1,2}:\d{2})(?:\s+(\d{1,3}))?\s*', instruction)
        try:
            if review:
                if (datetime.fromisoformat(review["expires_at"]) <= now or review["etag"] != event.get("etag")
                        or review["live"] != [value.isoformat() for value in live]
                        or review["snapshot"] != task_fields(task)):
                    return "時段已變更或確認已過期。請重新提出時間調整。"
                start, minutes = live[0], review["minutes"]
            elif live != saved:
                if not match and planner.ai is not None and event.get("etag"):
                    live_state = {**state, "start": live[0].isoformat(), "end": live[1].isoformat(),
                                  "minutes": int((live[1] - live[0]).total_seconds() / 60)}
                    edit = await planner.ai.parse_schedule_edit(instruction, task, live_state, now)
                    if edit.actionable and edit.timing == "keep" and edit.minutes is not None and not edit.progress_note:
                        return drift_review(planner, task_id, state, task, saved, live, event, edit.minutes, now)
                return "Calendar 時段與 Chronos 紀錄不同。請先指定時長，並說明是否保留 Calendar 的開始時間。"
            elif match:
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
        if review:
            pending["review_id"] = review_id
    start, end = (datetime.fromisoformat(pending[k]) for k in ('start', 'end'))
    if pending['snapshot'] != task_fields(task):
        raise CalendarError('The task changed during this edit. Check /plan before retrying.')
    if pending.get("review_id"):
        # A verified shrink occupies a strict subset of the live event. Keeping
        # its start cannot introduce any new booking conflict.
        live_start, live_end = bounds(event, planner.settings.tz)
        if start != live_start or not max(now, start) < end < live_end:
            return "Calendar 時段已變更，或縮短後的結束時間已過。請重新提出時間調整。"
    else:
        agenda = await planner.calendar.agenda(*(window or (start, end)))
        busy, reservations, budget, policy = scheduling_inputs(planner, task_id, state, agenda, start, 8 if window else 1)
        slot = find_slot(task, now, busy, reservations, planner.settings, pending['minutes'], budget, policy, window=window or (start, end), align_minutes=window is not None)
        if window and slot:
            start, end = slot
            pending.update(start=start.isoformat(), end=end.isoformat())
        if not slot or slot != (start, end):
            return 'That time conflicts with your calendar, deadline or study limits. The booking is unchanged.'
    def reserve():
        current_task = planner.db.get_task(task_id)
        current = planner.get(task_id)
        request = planner.db.get_task_action_state(receipt_key(update_id)) if update_id is not None else None
        if (not current_task or current_task['status'] != 'open'
                or task_fields(current_task) != pending['snapshot'] or current != state
                or (request and (request.get('task_id') != task_id or request.get('token') != token))):
            return False
        planner.db._save_task_action_state(f'learning:task:{task_id}', {**current, 'pending_reschedule': pending})
        if update_id is not None:
            planner.db._save_task_action_state(receipt_key(update_id), {'task_id': task_id, 'token': token})
        return True
    if not planner.db._run_task_action(reserve):
        return 'The booking changed. Try again from /plan.'
    try:
        await planner.calendar.move_event(state['event_id'], {
            'start': {'dateTime': pending['start'], 'timeZone': planner.settings.timezone},
            'end': {'dateTime': pending['end'], 'timeZone': planner.settings.timezone},
            'extendedProperties': {**event.get('extendedProperties', {}),
                                   'private': {**props, 'chronosReschedule': pending['marker']}}}, pending['etag'])
    except CalendarError as error:
        if not pending.get("review_id") or error.status != 412:
            raise
        planner.update(task_id, lambda old: {k: v for k, v in old.items() if k not in {"pending_reschedule", "drift_review"}}
                       if old.get("token") == token and old.get("pending_reschedule") == pending else old)
        return "Calendar 行程剛更新。請重新提出時間調整。"
    verified = await planner.calendar.event(state['event_id'])
    if not verified_move(verified, state['event_id'], task_id, token, pending, planner.settings):
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
        updated.pop('drift_review', None)
        planner.db._save_task_action_state(f'learning:task:{task_id}', updated)
        replies = {}
        if pending.get('update_id') is not None:
            replies[pending['update_id']] = success_reply(pending)
        if update_id is not None:
            replies[update_id] = reply or success_reply(pending)
        for request_id, text in replies.items():
            planner.db._save_task_action_state(receipt_key(request_id), {'reply': text})
    planner.db._run_task_action(save)


def scheduling_inputs(planner, task_id, state, agenda, start, days):
    token = state['token']
    own_ids = {planner.settings.calendar_id.casefold()}
    if planner.settings.calendar_id.casefold() in {'primary', planner.settings.calendar_account.casefold()}:
        own_ids.update({'primary', planner.settings.calendar_account.casefold()})
    busy = [bounds(e, planner.settings.tz) for e in agenda if e.get('transparency') != 'transparent'
            and not (e.get('id') == state['event_id'] and e.get('_calendar_id', '').casefold() in own_ids)]
    reservations = [(datetime.fromisoformat(s['start']), datetime.fromisoformat(s['end']))
                    for t in planner.db.list_open_tasks() if t['id'] != task_id
                    for s in [planner.get(t['id'])] if s and s.get('start') and s['status'] == 'creating']
    budget = {}
    for offset in range(days):
        day = (start + timedelta(days=offset)).astimezone(planner.settings.tz).date().isoformat()
        bucket = planner.db.get_task_action_state('learning:day:' + day) or {}
        budget[day] = sum(v for k, v in bucket.items() if k != token)
    policy = planner.db.get_task_action_state('learning:course-calendar-policy')
    return busy, reservations, budget, policy
