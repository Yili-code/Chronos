"""Exact placement of an existing task, with reviewed conflict moves."""
from datetime import datetime, timedelta
from hashlib import sha256
from html import escape
from uuid import uuid4

from .ai import AIError
from .google_calendar import CalendarError
from .learning_calendar import find_slot, overlaps
from .study_reflow import bounds, movable, apply, reconcile, ReflowReviewError
from .task_actions import task_fields
from .temporal_constraints import literal_weekday_conflict


def validate_target(planner, review):
    target = review['slot_target']
    task = planner.db.get_task(target['task_id'])
    if (not task or task.get('status') != 'open'
            or task_fields(task) != target['task_snapshot']):
        raise ReflowReviewError('The target task changed. Prepare a fresh arrangement.')
    current = planner.get(target['task_id'])
    if current != review['target_state']:
        # A scheduled target may already have been moved by this reviewed operation.
        move = next((m for m in review['calendar_moves'] if m['task_id'] == target['task_id']), None)
        expected = dict(review['target_state'] or {})
        if move:
            expected.update({k: move[k] for k in ('start', 'end', 'minutes')})
            expected['requested_minutes'] = move['minutes']
            if 'nightly_slot' in expected:
                expected['nightly_slot'] = {k: move[k] for k in ('start', 'end')}
        if not move or current != expected:
            raise ReflowReviewError('The target booking changed. Prepare a fresh arrangement.')


def card(review):
    target = review['slot_target']
    lines = ['<b>Existing task · exact start</b>', escape(target['title']),
             f"{target['start'][:16].replace('T', ' ')}–{target['end'][11:16]} · {target['minutes']} min"]
    for move in review['calendar_moves']:
        if move['task_id'] != target['task_id']:
            lines += ['', escape(move['title']),
                      f"{move['old_start'][:16]} → {move['start'][:16]}–{move['end'][11:16]}"]
    lines += ['', 'The existing task is reused. No duplicate task will be created.']
    if review['status'] in {'partial', 'creating'}:
        lines += ['Some changes may have succeeded. Allow checks and resumes this same arrangement.']
    else:
        lines += ['Nothing changes until Allow.']
    token = review['token']
    actions = ('allow',) if review['status'] in {'partial', 'creating'} else ('allow', 'cancel')
    return {'messages': [{'text': '\n'.join(lines), 'parse_mode': 'HTML', 'replan_token': token,
        'reply_markup': {'inline_keyboard': [[{'text': a.title(), 'callback_data': f'replan:{a}:{token}'} for a in actions]]}}]}


async def preview(planner, chat_id, task, instruction, update_id, now, *, start=None, minutes=None):
    from .calendar_replan import key, revoke_pending
    from .backward_planning import reserved_windows
    from .nightly_planning import state_window
    cfg, db = planner.settings, planner.db
    token = sha256(f'task-slot:{chat_id}:{update_id}'.encode()).hexdigest()[:32]
    previous = db.get_task_action_state(key(chat_id)) or {}
    if previous.get('token') == token:
        if previous.get('status') == 'done':
            return 'This exact task arrangement has already been applied.'
        return card(previous)
    if previous.get('status') in {'creating', 'partial'}:
        return 'A calendar change needs recovery. Use Allow on its existing card first.'
    if not revoke_pending(db, chat_id, previous):
        return 'The previous arrangement changed. Please retry.'
    conflict = literal_weekday_conflict(instruction, now)
    if conflict:
        return conflict
    saved = planner.get(task['id'])
    if saved and saved.get('status') not in {'scheduled', 'queued', 'proposed', 'editing', 'no_slot', 'needs_replan', 'rejected', 'cancelled'}:
        return 'This task has an active or unfinished booking that needs review. Nothing changed.'
    if start is None:
        edit = await planner.ai.parse_schedule_edit(instruction, task, saved or {}, now)
        if not edit.actionable or edit.timing != 'exact' or not edit.start:
            return '請指定這項既有任務的確切開始時間。沒有新增任務或變更行程。'
        try:
            start = datetime.fromisoformat(edit.start)
        except ValueError:
            return '請提供有效的開始時間。沒有變更。'
        minutes = minutes or edit.minutes
    if start.utcoffset() is None:
        return '請指定有時區的開始時間。沒有變更。'
    start = start.astimezone(cfg.tz)
    minutes = minutes or (saved or {}).get('minutes') or (task.get('timing') or {}).get('estimated_minutes')
    if minutes is None:
        try:
            estimate = await planner.ai.estimate_duration(task, {})
            minutes = estimate.estimated_minutes
        except AIError:
            return '無法估計這項任務的時長，請補充需要幾分鐘。沒有變更。'
    if not isinstance(minutes, int) or not 10 <= minutes <= 180:
        return '請指定 10 至 180 分鐘的工作時段。沒有變更。'
    end = start + timedelta(minutes=minutes)
    if not now < start < end <= now + timedelta(days=7):
        return '請選擇未來七天內的工作時段。沒有變更，也沒有改排到其他日期。'
    await planner.calendar.verify_owner()
    horizon = now + timedelta(days=7)
    events = await planner.calendar.agenda(now, horizon)
    tasks = {t['id']: t for t in db.list_open_tasks()}
    if task_fields(tasks.get(task['id'], {})) != task_fields(task):
        return 'The task changed while planning. Nothing changed.'
    states = {i: planner.get(i) for i in tasks}
    if states[task['id']] != saved:
        return 'The target booking changed while planning. Nothing changed.'
    target_event = None
    if saved and saved.get('status') == 'scheduled':
        target_event = await planner.calendar.event(saved['event_id'])
        if target_event:
            target_event = {**target_event, '_calendar_id': cfg.calendar_id}
        if not target_event or not movable(target_event, cfg, now, task_id=task["id"], study_state=saved):
            return 'The existing work session cannot be moved automatically. Nothing changed.'
        if all(e.get('id') != target_event['id'] or e.get('_calendar_id') != cfg.calendar_id for e in events):
            events.append(target_event)
    reservations = list(reserved_windows(db))
    for i, s in states.items():
        if i == task['id'] or not s or s.get('status') not in {'queued', 'planning', 'proposed', 'editing', 'creating'}:
            continue
        source = state_window(s)
        if source.get('start') and source.get('end'):
            reservations.append(tuple(datetime.fromisoformat(source[k]) for k in ('start', 'end')))
    selected, fixed, blocked = [], [], []
    for event in events:
        if event.get('status') == 'cancelled':
            continue
        window = bounds(event, cfg.tz)
        linked = next((i for i, s in states.items() if s and s.get('event_id') == event.get('id')
            and event.get('_calendar_id', '').casefold() in {cfg.calendar_id.casefold(), cfg.calendar_account.casefold(), 'primary'}), None)
        is_target = target_event and event.get('id') == target_event['id'] and linked == task['id']
        if not is_target and not overlaps(start, end, [window]):
            if event.get('transparency') != 'transparent':
                fixed.append(window)
            continue
        props = event.get('extendedProperties', {}).get('private', {})
        eligible = movable(event, cfg, now, allow_started=linked is not None and not is_target, task_id=linked, study_state=states.get(linked))
        if linked is not None or props.get('chronosTask'):
            s = states.get(linked) or {}
            eligible = (eligible and s.get('status') == 'scheduled' and not s.get('pending_reschedule')
                and props.get('chronosTask') == str(linked) and props.get('chronosToken') == s.get('token')
                and window == tuple(datetime.fromisoformat(s[k]) for k in ('start', 'end')))
        if not eligible:
            blocked.append(event.get('summary', 'Calendar activity'))
        elif is_target:
            target_event = event
        else:
            selected.append((event, window, linked))
    if blocked:
        return '無法騰出指定時段，以下活動不能自動移動：\n' + '\n'.join(blocked) + '\n沒有變更，也沒有新增任務。'
    if len(selected) > 5:
        return 'More than five activities conflict with that slot. Nothing changed.'
    budgets = { (now + timedelta(days=n)).date().isoformat(): dict(db.get_task_action_state(
        'learning:day:' + (now + timedelta(days=n)).date().isoformat()) or {}) for n in range(8)}
    for _, old, linked in selected:
        if linked is not None:
            budgets[old[0].date().isoformat()].pop(states[linked]['token'], None)
    if target_event:
        budgets.setdefault(saved['start'][:10], dict(db.get_task_action_state('learning:day:' + saved['start'][:10]) or {})).pop(saved['token'], None)
    policy = db.get_task_action_state('learning:course-calendar-policy')
    load = {day: sum(bucket.values()) for day, bucket in budgets.items()}
    if find_slot(task, min(now, start - timedelta(minutes=10)), fixed, reservations, cfg, minutes,
                 load, policy, window=(start, end), align_minutes=False) != (start, end):
        return '指定時段不符合課表、期限、用餐時段或每日額度，無法安排。沒有變更，也沒有改到其他時間。'
    load[start.date().isoformat()] = load.get(start.date().isoformat(), 0) + minutes
    moves = []
    for event, old, linked in sorted(selected, key=lambda row: row[1][0]):
        duration = (old[1] - old[0]).total_seconds() / 60
        if not duration.is_integer():
            return 'A conflicting activity has an unsupported duration. Nothing changed.'
        slot = find_slot(tasks.get(linked, {}), now, [*fixed, (start, end)], reservations, cfg,
                         int(duration), load, policy, window=(end, horizon))
        if not slot:
            return '無法為衝突活動找到符合期限與額度的新時段。沒有變更。'
        fixed.append(slot)
        if linked is not None:
            load[slot[0].date().isoformat()] = load.get(slot[0].date().isoformat(), 0) + int(duration)
        moves.append(move_record(event, old, slot, linked, states, tasks, int(duration)))
    target = {'task_id': task['id'], 'title': task['title'], 'task_snapshot': task_fields(task),
              'start': start.isoformat(), 'end': end.isoformat(), 'minutes': minutes, 'token': token}
    if target_event:
        moves.append(move_record(target_event, bounds(target_event, cfg.tz), (start, end), task['id'], states, tasks, minutes))
    review = {'task_slot': True, 'window_replan': True, 'token': token, 'chat_id': chat_id,
        'status': 'pending', 'start': start.isoformat(), 'end': end.isoformat(), 'minutes': 0,
        'calendar_moves': moves, 'revision_additions': [] if target_event else [target],
        'slot_target': target, 'target_state': saved, 'expires_at': (now + timedelta(minutes=10)).isoformat()}
    def save(old):
        if old and old.get('status') in {'creating', 'partial'}:
            return old
        validate_target(planner, review)
        return review
    stored = db.mutate_task_action_state(key(chat_id), save)
    if stored.get('token') != token:
        return 'Another calendar change is in progress. Nothing changed.'
    if cfg.learning_auto_accept:
        return await callback(planner, chat_id, None, token, 'allow', now)
    return card(stored)


def move_record(event, old, slot, linked, states, tasks, minutes):
    return {'event_id': event['id'], 'etag': event['etag'], 'title': event.get('summary', 'Activity')[:200],
        'task_id': linked, 'task_token': states[linked]['token'] if linked is not None else None,
        'task_snapshot': task_fields(tasks[linked]) if linked is not None else None,
        'old_start': old[0].isoformat(), 'old_end': old[1].isoformat(),
        'start': slot[0].isoformat(), 'end': slot[1].isoformat(), 'minutes': minutes}


async def callback(planner, chat_id, message_id, token, action, now):
    from .calendar_replan import key
    db = planner.db
    lease = uuid4().hex
    def claim(old):
        if not old or old.get('token') != token or old.get('message_id') != message_id:
            return old
        if old.get('status') == 'done' or (old.get('status') == 'creating' and old.get('lease_until', '') > now.isoformat()):
            return old
        if old.get('status') not in {'pending', 'partial', 'creating'}:
            return old
        if action != 'allow':
            return {**old, 'status': 'cancelled'} if old['status'] == 'pending' else old
        if old['status'] == 'pending' and old['expires_at'] <= now.isoformat():
            return {**old, 'status': 'expired'}
        return {**old, 'status': 'creating', 'lease': lease, 'lease_until': (now + timedelta(minutes=3)).isoformat()}
    review = db.mutate_task_action_state(key(chat_id), claim) or {}
    if review.get('token') != token or review.get('message_id') != message_id:
        return 'This arrangement is no longer active.'
    if review['status'] == 'done':
        return 'This task arrangement has already been applied.'
    if review['status'] in {'cancelled', 'expired'}:
        return 'Arrangement ' + review['status'] + '. No changes applied.'
    if review.get('lease') != lease:
        return 'This arrangement is already being applied. Please wait.'
    try:
        await planner.calendar.verify_owner()
        if review.get('phase') != 'booking':
            await apply(planner, {}, review, now)
            if review['revision_additions']:
                target = review['slot_target']
                def reserve():
                    live = db.get_task_action_state(key(chat_id))
                    if live.get('lease') != lease:
                        raise ReflowReviewError('Another operation owns this review.')
                    validate_target(planner, review)
                    state = {'status': 'creating', 'token': token, 'snapshot': target['task_snapshot'],
                        'start': target['start'], 'end': target['end'], 'minutes': target['minutes'],
                        'requested_minutes': target['minutes'], 'duration_source': 'owner',
                        'auto_accepted': planner.settings.learning_auto_accept, 'exact_slot': True}
                    db._save_task_action_state(f"learning:task:{target['task_id']}", state)
                    db._save_task_action_state(key(chat_id), {**live, 'phase': 'booking'})
                db._run_task_action(reserve)
        if review['revision_additions']:
            target = review['slot_target']
            task = db.get_task(target['task_id'])
            if not task or task.get('status') != 'open' or task_fields(task) != target['task_snapshot']:
                raise CalendarError('The target task changed; the exact booking needs review.')
            saved = planner.get(target['task_id'])
            if not saved or saved.get('token') != token or saved.get('status') not in {'creating', 'scheduled', 'needs_replan'}:
                raise CalendarError('The exact booking changed; inspect Calendar before retrying.')
            reply = await planner.book(db.get_task(target['task_id']), saved, now)
            if planner.get(target['task_id']).get('status') != 'scheduled':
                raise CalendarError(reply)
        db.mutate_task_action_state(key(chat_id), lambda old: {**old, 'status': 'done', 'lease_until': now.isoformat()}
            if old.get('lease') == lease else old)
    except (CalendarError, ReflowReviewError) as error:
        # Event markers and deterministic booking IDs allow a later Allow to reconcile.
        try:
            await reconcile(planner, review)
        except CalendarError:
            pass  # Keep the durable review and original failure for explicit recovery.
        stored = db.mutate_task_action_state(key(chat_id), lambda old, message=str(error): {**old, 'status': 'partial',
            'lease_until': now.isoformat(), 'error': message} if old.get('lease') == lease else old)
        result = card(stored)
        result['messages'][0]['text'] = 'Exact scheduling paused. No duplicate task was created.\n' + escape(str(error)) + '\n\n' + result['messages'][0]['text']
        return result
    target = review['slot_target']
    return (f"Scheduled existing task · {escape(target['title'])}\n{target['start'][:16].replace('T', ' ')}–{target['end'][11:16]}"
            + (f"\nRearranged {sum(m['task_id'] != target['task_id'] for m in review['calendar_moves'])} conflicting activities."
               if any(m['task_id'] != target['task_id'] for m in review['calendar_moves']) else ''))
