"""Owner-reviewed calendar window moves, persisted before Calendar writes."""
from datetime import datetime, timedelta
from hashlib import sha256
from html import escape
import re

from .ai import AIError, ReplanOutput
from .google_calendar import CalendarError
from .learning_calendar import find_slot, overlaps
from .study_reflow import bounds, movable, apply, reconcile, ReflowReviewError
from .task_actions import task_fields
from .temporal_constraints import literal_weekday_conflict


def key(chat_id):
    return f'replan:chat:{chat_id}'


def revoke_pending(db, chat_id, previous):
    """New constraints revoke the old approval before any external parsing."""
    if not previous or previous.get('status') != 'pending':
        return True
    current = db.mutate_task_action_state(key(chat_id), lambda old:
        {**old, 'status': 'editing'} if old and old.get('token') == previous['token']
        and old.get('status') == 'pending' else old)
    return bool(current and current.get('token') == previous['token'] and current.get('status') == 'editing')


def card(state):
    if state.get('task_slot'):
        from .task_slot import card as slot_card
        return slot_card(state)
    if state.get('ordered_sequence'):
        from .sequence_replan import card as ordered_card
        return ordered_card(state)
    lines = ['<b>Your proposed calendar changes, Sir.</b>',
             f"Requested window: {escape(state['start'])} → {escape(state['end'])}"]
    blocked = state.get('blocked_events', [])
    if blocked:
        lines += ['', '<b>Still occupied</b>',
                  'These activities stay in place. Allow moves only the movable activities below.']
        lines += [escape(item['description']) for item in blocked]
    for move in state['calendar_moves']:
        if move.get('already_started'):
            lines += ['', 'Already started; progress is unknown. Original duration kept.']
        lines += ['', '<b>' + escape(move['title']) + '</b>',
                  f"{move['old_start'][:16].replace('T', ' ')}–{move['old_end'][11:16]}",
                  f"→ {move['start'][:16].replace('T', ' ')}–{move['end'][11:16]}"]
    for item in state.get('revision_additions', []):
        lines += ['', '<b>' + escape(item['title']) + '</b>',
                  f"New session: {item['start'][:16].replace('T', ' ')}–{item['end'][11:16]} · {item['minutes']} minutes"]
    if state.get('revision_additions'):
        lines += ['', 'Unfinished chapters remain open. New sessions enter the existing booking workflow after Allow.']
    lines += ['', 'Nothing changes until Allow.']
    token = state['token']
    return {'messages': [{'text': '\n'.join(lines), 'parse_mode': 'HTML', 'replan_token': token,
        'reply_markup': {'inline_keyboard': [[{'text': action.title(), 'callback_data': f'replan:{action}:{token}'}
                                              for action in ('allow', 'edit', 'cancel')]]}}]}


def bind(db, chat_id, message_id, message):
    token = message.get('replan_token') or message.get('replan_preview_token')
    if token:
        db.mutate_task_action_state(key(chat_id), lambda old: {**old,
            'preview_message_ids': list(dict.fromkeys([*old.get('preview_message_ids', []), message_id])),
            **({'message_id': message_id} if message.get('replan_token') else {})}
                                    if old and old.get('token') == token else old)


def explicit_window(instruction, now):
    """Resolve a complete, explicit same-day request without model ambiguity."""
    normalized = re.sub(r"\s+", "", instruction)
    if re.fullmatch(r"(?:幫我)?(?:清空|騰出|空出)(?:今天晚上|今晚)(?:的)?(?:所有)?(?:行程|事情|安排|活動)?[。！!]?", normalized):
        start = max(now, now.replace(hour=18, minute=0, second=0, microsecond=0))
        end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return ReplanOutput(actionable=True, start=start.isoformat(), end=end.isoformat(),
                            destination_start=end.isoformat())
    if re.fullmatch(
            r"(?:清空|騰出|空出)今天下午(?:的)?(?:所有)?(?:行程|事情|安排|活動)[。！!]?",
            normalized):
        start = max(now, now.replace(hour=13, minute=0, second=0, microsecond=0))
        end = now.replace(hour=18, minute=0, second=0, microsecond=0)
        return ReplanOutput(actionable=True, start=start.isoformat(), end=end.isoformat(),
                            destination_start=end.isoformat())
    match = re.fullmatch(
        r"(?:清空|騰出|空出)今天(?:從)?(?:現在|目前)到(?:今天)?"
        r"(上午|中午|下午|晚上)([0-9一二三四五六七八九十兩]{1,3})點"
        r"(半|[0-9一二三四五六七八九十兩]{1,3}分)?(?:之?前)?(?:的)?(?:所有)?(?:行程|事情|安排|活動)[。！!]?",
        normalized)
    if not match:
        return None
    def number(text):
        if text.isdigit():
            return int(text)
        digits = {'一': 1, '二': 2, '兩': 2, '三': 3, '四': 4, '五': 5,
                  '六': 6, '七': 7, '八': 8, '九': 9}
        if '十' in text:
            if not re.fullmatch(r'[一二三四五六七八九兩]?十[一二三四五六七八九]?', text):
                return -1
            left, right = text.split('十', 1)
            return (digits.get(left, 1) if left else 1) * 10 + digits.get(right, 0)
        return digits.get(text, -1)
    hour = number(match[2])
    minute = 30 if match[3] == '半' else number(match[3][:-1]) if match[3] else 0
    if not 1 <= hour <= 12 or not 0 <= minute < 60 or (match[1] == '中午' and hour != 12) or (match[1] == '晚上' and hour == 12):
        return ReplanOutput(actionable=False, start=None, end=None, destination_start=None)
    if match[1] in {'下午', '晚上'} and hour != 12:
        hour += 12
    elif match[1] == '上午' and hour == 12:
        hour = 0
    end = now.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()
    return ReplanOutput(actionable=True, start=now.isoformat(), end=end, destination_start=end)


async def preview(planner, chat_id, instruction, update_id, now=None):
    now = now or datetime.now(planner.settings.tz)
    db, config = planner.db, planner.settings
    previous = db.get_task_action_state(key(chat_id))
    token = sha256(f'replan:{chat_id}:{update_id}'.encode()).hexdigest()[:32]
    if previous and previous.get('status') == 'creating':
        return 'A calendar change needs recovery. Use Allow on its existing card before replanning.'
    if previous and previous.get('token') == token:
        return card(previous)
    if not revoke_pending(db, chat_id, previous):
        return 'The previous review changed. Check its current result before requesting a replacement.'
    conflict = literal_weekday_conflict(instruction, now.astimezone(config.tz))
    if conflict:
        return conflict
    try:
        output = explicit_window(instruction, now) or await planner.ai.parse_replan(instruction, now,
            {k: previous[k] for k in ('start', 'end', 'destination_start')} if previous and previous.get('status') == 'editing' else None)
        if not output.actionable:
            raise ValueError
        start, end, destination = [datetime.fromisoformat(v) for v in (output.start, output.end, output.destination_start)]
        if any(t.tzinfo is None for t in (start, end, destination)):
            raise ValueError
        start, end, destination = [t.astimezone(config.tz) for t in (start, end, destination)]
        if end <= now:
            return f'The cutoff {end:%H:%M} has passed; it is now {now:%H:%M}. Choose a future window. Nothing changed.'
        if not now - timedelta(minutes=2) <= start < end <= now + timedelta(days=7) or destination < end or destination >= now + timedelta(days=7):
            raise ValueError
    except AIError:
        return '目前無法解析改排要求，沒有調整行程。請稍後重試。'
    except (ValueError, TypeError, OverflowError):
        return '想空出哪個時段？例如「今天現在到下午三點」。沒有調整行程。'
    await planner.calendar.verify_owner()
    horizon = now + timedelta(days=7)
    events = await planner.calendar.agenda(now, horizon)
    tasks = {t['id']: t for t in db.list_open_tasks()}
    states = {i: planner.get(i) for i in tasks}
    selected, fixed, blocked = [], [], []
    for event in events:
        window = bounds(event, config.tz)
        if event.get('status') == 'cancelled':
            continue
        if not overlaps(start, end, [window]):
            if event.get('transparency') != 'transparent':
                fixed.append(window)
            continue
        linked = next((i for i, s in states.items() if s and s.get('event_id') == event['id']
                       and event.get('_calendar_id', '').casefold() in {config.calendar_id.casefold(), config.calendar_account.casefold(), 'primary'}), None)
        props = event.get('extendedProperties', {}).get('private', {})
        eligible = movable(event, config, now, allow_started=linked is not None)
        if linked is not None or props.get('chronosTask'):
            saved = states.get(linked) or {}
            eligible = (eligible and saved.get('status') == 'scheduled'
                and props.get('chronosTask') == str(linked) and props.get('chronosToken') == saved.get('token')
                and not saved.get('pending_reschedule')
                and window == tuple(datetime.fromisoformat(saved[k]) for k in ('start', 'end')))
        if not eligible:
            reasons = []
            if window[1] <= now:
                reasons.append('activity has ended')
            elif window[0] <= now and linked is None:
                reasons.append('already started and is not a verified study booking')
            if event.get('recurringEventId') or event.get('recurrence'):
                reasons.append('recurring activity')
            if event.get('attendees') or event.get('attendeesOmitted'):
                reasons.append('shared activity')
            if not event.get('start', {}).get('dateTime'):
                reasons.append('all-day activity')
            reason = ', '.join(reasons) or 'ownership, course constraints, or study records could not be verified'
            description = f"{event.get('summary', 'Untitled activity')} ({window[0]:%H:%M}–{window[1]:%H:%M}): {reason}"
            blocked.append({'event_id': event['id'], 'calendar_id': event.get('_calendar_id', ''),
                'etag': event.get('etag'), 'old_start': window[0].isoformat(), 'old_end': window[1].isoformat(),
                'description': description})
            if event.get('transparency') != 'transparent':
                fixed.append(window)
        else:
            selected.append((event, window, linked))
    if not selected:
        if blocked:
            return 'The window cannot be fully freed. These activities cannot be moved automatically:\n' + \
                '\n'.join(item['description'] for item in blocked) + '\nNothing changed.'
        return 'No activities need moving in this window. Nothing changed.'
    if len(selected) > 5:
        return 'At most five activities can be moved at once. Choose a smaller window. Nothing changed.'
    reservations = [(datetime.fromisoformat(s['start']), datetime.fromisoformat(s['end']))
                    for s in states.values() if s and s.get('start') and s.get('status') == 'creating']
    policy = db.get_task_action_state('learning:course-calendar-policy')
    budgets = {}
    for i in range(8):
        day = (now + timedelta(days=i)).date().isoformat()
        bucket = dict(db.get_task_action_state('learning:day:' + day) or {})
        for _, _, linked in selected:
            if linked is not None:
                bucket.pop(states[linked]['token'], None)
        budgets[day] = sum(bucket.values())
    moves = []
    for event, old, linked in sorted(selected, key=lambda row: row[1][0]):
        minutes = int((old[1] - old[0]).total_seconds() / 60)
        if old[1] - old[0] != timedelta(minutes=minutes):
            return 'An activity has an unsupported duration. Nothing changed.'
        task = tasks[linked] if linked is not None else {}
        slot = find_slot(task, now, fixed + [(start, end)], reservations, config, minutes,
                         budgets, policy, window=(destination, horizon))
        if not slot:
            return f"No slot meets the deadline, timetable, and daily limit: {event.get('summary', 'Activity')}. Nothing changed."
        fixed.append(slot)
        if linked is not None:
            day = slot[0].date().isoformat()
            budgets[day] = budgets.get(day, 0) + minutes
        moves.append({'event_id': event['id'], 'etag': event['etag'], 'title': event.get('summary', 'Activity')[:100],
            'task_id': linked, 'task_token': states[linked]['token'] if linked is not None else None,
            'task_snapshot': task_fields(task) if linked is not None else None,
            'old_start': old[0].isoformat(), 'old_end': old[1].isoformat(),
            'already_started': old[0] <= now,
            'start': slot[0].isoformat(), 'end': slot[1].isoformat()})
    state = {'status': 'pending', 'token': token, 'chat_id': chat_id, 'start': start.isoformat(),
             'end': end.isoformat(), 'destination_start': destination.isoformat(), 'calendar_moves': moves,
             'blocked_events': blocked,
             'window_replan': True, 'minutes': 0, 'expires_at': (now + timedelta(hours=2)).isoformat()}
    def save(old):
        return old if old and old.get('status') == 'creating' else state
    saved = db.mutate_task_action_state(key(chat_id), save)
    return card(saved) if saved.get('token') == token else 'Another calendar change is in progress. Retry later.'


async def callback(planner, chat_id, message_id, token, action, now=None):
    now = now or datetime.now(planner.settings.tz)
    db = planner.db
    existing = db.get_task_action_state(key(chat_id)) or {}
    if existing.get('task_slot') and existing.get('token') == token:
        from .task_slot import callback as slot_callback
        return await slot_callback(planner, chat_id, message_id, token, action, now)
    if existing.get('ordered_sequence') and existing.get('token') == token:
        from .sequence_replan import callback as ordered_callback
        return await ordered_callback(planner, chat_id, message_id, token, action, now)
    def claim(old):
        if not old or old.get('token') != token or old.get('message_id') != message_id:
            return old
        if old['status'] not in {'pending', 'creating', 'editing'}:
            return old
        if old['status'] == 'editing' and action == 'allow':
            return old  # Edit revokes approval until a replacement preview exists.
        if old['status'] == 'creating' and action != 'allow':
            return old
        if old['status'] != 'creating' and old['expires_at'] <= now.isoformat():
            return {**old, 'status': 'expired'}
        return {**old, 'recovering': old['status'] == 'creating',
                'status': {'allow': 'creating', 'edit': 'editing', 'cancel': 'cancelled'}[action]}
    state = db.mutate_task_action_state(key(chat_id), claim)
    if not state or state.get('token') != token or state.get('message_id') != message_id:
        return 'This proposal is no longer active. Use /replan.'
    if state['status'] == 'editing':
        return 'Use a fresh study-plan correction to revise this proposal.' if state.get('study_revision') else 'Enter revised constraints, e.g. "move to tomorrow or later". Use /cancel to cancel or /replan to choose another window.'
    if state['status'] == 'cancelled':
        return 'Cancelled. Original calendar events kept.'
    if state['status'] == 'expired':
        return 'This proposal expired. Use /replan to review a fresh arrangement.'
    if state['status'] == 'done':
        return 'These calendar changes have already been applied.'
    if action != 'allow':
        return 'Changes are being reconciled. Use Allow to check their result.'
    await planner.calendar.verify_owner()
    try:
        await apply(planner, {}, state, now)
        if state.get('study_revision'):
            from .study_revision import finish
            finish(planner, state)
    except ReflowReviewError:
        if not state.get('recovering'):
            db.mutate_task_action_state(key(chat_id), lambda old: {**old, 'status': 'pending'}
                                        if old.get('token') == token else old)
            return 'The arrangement changed before any writes. Use Edit for a fresh proposal, or Cancel. Nothing changed.'
        await reconcile(planner, state)
        return 'Recovery paused because an activity changed. Check Calendar before retrying Allow.'
    except CalendarError:
        await reconcile(planner, state)
        return 'Calendar changes paused. Some moves may already have succeeded. Tap Allow to recover; check Calendar if an activity changed externally.'
    db.mutate_task_action_state(key(chat_id), lambda old: {**old, 'status': 'done'} if old.get('token') == token else old)
    if state.get('study_revision'):
        lines = ['Calendar updated.' if state['calendar_moves'] else 'Study plan reviewed.']
        lines += [f"{m['title']}: {m['start'][11:16]}–{m['end'][11:16]} · {m['minutes']} minutes" for m in state['calendar_moves']]
        lines += [f"Queued unfinished task: {m['title']} · {m['minutes']} minutes. Booking will be checked again." for m in state['revision_additions']]
        return '\n'.join(lines)
    return 'Calendar updated.\n' + '\n'.join(f"{m['title']}: {m['old_start'][:16]} → {m['start'][:16]}" for m in state['calendar_moves'])
