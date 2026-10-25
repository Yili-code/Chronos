"""Ordered existing-event rescheduling; no event creation or implicit completion."""
from datetime import datetime, timedelta, time
from hashlib import sha256
from html import escape
import re
from uuid import uuid4

from .ai import SequenceReplanOutput
from .google_calendar import CalendarError
from .learning_calendar import find_slot
from .nightly_planning import deadline, state_window
from .preferences import read as read_preferences
from .study_reflow import bounds
from .task_actions import task_fields

UNITS = {'chapter': r'chapters?|ch\.?|章', 'lecture': r'lectures?|講',
         'part': r'parts?|部分', 'unit': r'units?|單元', 'session': r'sessions?|節'}


def explicit_group(text, *, prefix=False):
    """Extract a complete bounded group, never time constraints or preferences."""
    units = '|'.join(unit + 's?' for unit in UNITS)
    pattern = (r'\s*(?:reschedule|replan|reorder)\s+(.{1,100}?)\s+(' + units
        + r')\s+(\d{1,3})\s*(?:[-–—~]|to|through)\s*(\d{1,3})\s+in\s+(?:order|sequence)\b')
    match = re.match(pattern,text,re.I) if prefix else re.fullmatch(pattern+r'\s*[.!]?\s*',text,re.I)
    if not match:
        return None
    first, last = int(match[3]), int(match[4])
    if not 1 <= first <= last <= 999 or last - first >= 20:
        return None
    return SequenceReplanOutput(actionable=True,scope=match[1].strip(),
        unit=match[2].lower().removesuffix('s'),ordinals=list(range(first,last+1)))


def looks_ordered(text):
    return bool(re.search(r'\b(?:reschedule|replan|reorder)\b|重排|重新安排|改排', text, re.I)
        and re.search(r'in\s+(?:order|sequence)|sequential|依序|按順序|按顺序', text, re.I)
        and re.search(r'chapter|lecture|part|unit|session|章|單元|講', text, re.I))


def looks_choice_answer(text):
    if len(text) > 200 or text.startswith('/'):
        return False
    if re.search(r'\bref:[\w-]+|\b(?:earlier|later|latest|earliest)\b', text, re.I):
        return True
    if not re.search(r'\d{1,2}[/.-]\d{1,2}|\d{1,2}[號号日]',text):
        return False
    return bool(re.search(r'choose|select|use|take|那個|那个|那筆|那笔|選|选',text,re.I)
        or re.fullmatch(r'\s*(?:(?:\d{4}[-/])?\d{1,2}[/.-]\d{1,2}|\d{1,2}[號号日])(?:\s*\([^)]*\))?(?:\s+\d{1,2}:\d{2}(?:\s*[-–—]\s*\d{1,2}:\d{2})?)?\s*', text))


def has_clarification(state):
    # Clarification retains intent, never approval or cached availability.
    return bool(state and state.get('ordered_sequence') and state.get('status') == 'clarifying'
                and isinstance(state.get('parsed'), dict))


def choice_evidence(text, event, candidates, tz=None):
    value = event.get('start',{}).get('dateTime') or event.get('start',{}).get('date')
    if not value:
        return False
    start = datetime.fromisoformat(value.replace('Z','+00:00'))
    if tz is not None:
        start = start.astimezone(tz)
    year = re.search(r'(?<!\d)(\d{4})[-/]\d{1,2}[-/]\d{1,2}', text)
    if year and int(year[1]) != start.year:
        return False
    dates = [start.strftime('%Y-%m-%d'), f'{start.month}/{start.day}',f'{start.month:02d}/{start.day:02d}']
    if any(re.search(r'(?<![0-9])'+re.escape(date)+r'(?![0-9])',text) for date in dates):
        times = re.findall(r'(?<![0-9])([0-2]?[0-9]:[0-5][0-9])(?![0-9])',text)
        if times and start.strftime('%H:%M') != times[0].zfill(5):
            return False
        if len(times) > 1:
            end_value = event.get('end',{}).get('dateTime')
            if not end_value:
                return False
            end = datetime.fromisoformat(end_value.replace('Z','+00:00'))
            if tz is not None:
                end = end.astimezone(tz)
            return end.strftime('%H:%M') == times[1].zfill(5)
        return True
    def candidate_start(e):
        value = e.get('start',{}).get('dateTime') or e.get('start',{}).get('date')
        parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.astimezone(tz) if tz is not None else parsed
    if re.search(rf'(?<![0-9]){start.day}[號号日]',text) and sum(candidate_start(e).day == start.day for e in candidates) == 1:
        return True
    values = [(candidate_start(e),e['id']) for e in candidates]
    if re.search(r'\b(?:later|latest)\b|較晚|较晚',text,re.I):
        return event['id'] == max(values)[1]
    if re.search(r'\b(?:earlier|earliest)\b|較早|较早',text,re.I):
        return event['id'] == min(values)[1]
    return event['id'] in text or ('ref:' + event['id'][-16:]) in text


def literal_ordinals(text, unit):
    match = re.search(r'(?:' + UNITS[unit] + r')\s*(\d+)\s*(?:[-–—~至到]|to|through)\s*(\d+)', text, re.I)
    if not match:
        match = re.search(r'第?\s*(\d+)\s*(?:[-–—~至到]|to|through)\s*(\d+)\s*(?:' + UNITS[unit] + ')', text, re.I)
    if match:
        a, b = map(int, match.groups())
        return list(range(a, b + 1)) if 1 <= a <= b <= 999 and b-a < 20 else []
    return None


def ordinal(title, scope, unit):
    matches_scope = re.search(r'(?<!\w)' + re.escape(scope) + r'(?!\w)', title, re.I) if scope.isascii() else scope.casefold() in title.casefold()
    if not matches_scope:
        return None
    match = re.search(r'(?<!\w)(?:' + UNITS[unit] + r')\s*(\d+)(?!\d)', title, re.I)
    if not match:
        match = re.search(r'第?\s*(\d+)\s*(?:' + UNITS[unit] + r')', title, re.I)
    return int(match[1]) if match else None


def eligible(event, cfg):
    aliases = {cfg.calendar_id.casefold(), cfg.calendar_account.casefold(), 'primary'}
    return (event.get('_calendar_id', cfg.calendar_id).casefold() in aliases
        and event.get('creator', {}).get('email', '').casefold() == cfg.calendar_account.casefold()
        and (event.get('organizer', {}).get('self') or event.get('organizer', {}).get('email', '').casefold() == cfg.calendar_account.casefold())
        and event.get('etag') and event.get('status') != 'cancelled'
        and event.get('eventType', 'default') == 'default' and not event.get('recurrence') and not event.get('recurringEventId')
        and not event.get('attendees') and not event.get('attendeesOmitted') and event.get('start', {}).get('dateTime'))


def event_info(planner, event):
    props = event.get('extendedProperties', {}).get('private', {})
    raw = str(props.get('chronosTask', ''))
    task = planner.db.get_task(int(raw)) if raw.isdecimal() and len(raw) < 19 else None
    current = planner.get(task['id']) if task else None
    history = planner.db.get_task_action_state('learning:history:' + props['chronosToken']) if props.get('chronosToken') else None
    evidence = next((s for s in (history,current) if s and s.get('event_id') == event.get('id') and s.get('token') == props.get('chronosToken')), {})
    completed = bool(evidence and ((task and task.get('status') == 'done' and not evidence.get('successor_task_id'))
                     or evidence.get('feedback', {}).get('outcome') == 'done'))
    issue, issue_code = None, None
    if not completed and task:
        if evidence.get('successor_task_id'):
            issue = 'The remaining work has a separate session. Please choose that session rather than this past event.'
        elif (not current or current.get('event_id') != event.get('id') or current.get('token') != props.get('chronosToken')):
            issue = 'This event differs from the session currently tracked for the task. Please check /plan before moving it.'
            issue_code = 'stale_booking'
        elif current.get('status') in {'creating', 'planning'} or (
            (current.get('delivery') or '').endswith(('_uncertain', '_sending')) and not
            (current.get('status') == 'awaiting_feedback' and current.get('delivery') == 'feedback_uncertain')):
            # Unknown feedback-message delivery is not an uncertain Calendar
            # mutation. Active senders and booking uncertainty still block.
            issue = 'An earlier booking is still being checked. Please review /plan before requesting another change.'
    return {'task_id': task['id'] if task else None, 'task_snapshot': task_fields(task) if task else None,
            'task_status': task.get('status') if task else None, 'task_state': current,
            'completed': completed, 'issue': issue, 'issue_code': issue_code}


def effective_config(cfg, parsed):
    start = max(cfg.learning_start_hour, parsed.start_hour if parsed.start_hour is not None else cfg.learning_start_hour)
    end = min(cfg.learning_end_hour, parsed.end_hour if parsed.end_hour is not None else cfg.learning_end_hour)
    if start >= end or not parsed.weekdays:
        raise ValueError('Study preferences leave no supported working hours. Please specify an available daytime window.')
    return cfg.model_copy(update={'learning_start_hour': start, 'learning_end_hour': end})


def fixed_and_budgets(planner, moves, agenda, start, stop):
    from .backward_planning import reserved_windows
    ids = {m['event_id'] for m in moves}
    task_ids = {m['task_id'] for m in moves if m['task_id'] is not None}
    aliases = {planner.settings.calendar_id.casefold(), planner.settings.calendar_account.casefold(), 'primary'}
    fixed = [bounds(e, planner.settings.tz) for e in agenda if e.get('status') != 'cancelled'
             and e.get('transparency') != 'transparent' and not (e.get('id') in ids and e.get('_calendar_id', '').casefold() in aliases)]
    fixed += reserved_windows(planner.db)
    for task in planner.db.list_open_tasks():
        saved = planner.get(task['id']) or {}
        source = state_window(saved)
        if task['id'] not in task_ids and saved.get('status') in {'queued', 'planning', 'proposed', 'editing', 'creating'} and source.get('start') and source.get('end'):
            fixed.append(tuple(datetime.fromisoformat(source[k]) for k in ('start', 'end')))
    budgets = {}
    for offset in range((stop.date()-start.date()).days + 1):
        day = (start.date()+timedelta(days=offset)).isoformat()
        bucket = dict(planner.db.get_task_action_state('learning:day:' + day) or {})
        for move in moves:
            bucket.pop(move['budget_token'], None)
        budgets[day] = sum(bucket.values())
    return fixed, budgets


def choose_slot(task, minutes, old, cursor, stop, fixed, budgets, cfg, policy, weekdays, now, prefer_original=True):
    def exact(a, b):
        return a.weekday() in weekdays and find_slot(task, now, fixed, [], cfg, minutes, budgets, policy, window=(a, b)) == (a, b)
    if prefer_original and cursor <= old[0] and old[1] <= stop and exact(*old):
        return old
    day = cursor.date()
    while day <= stop.date():
        begin = max(cursor, datetime.combine(day, time.min, cfg.tz))
        end = min(stop, datetime.combine(day+timedelta(days=1), time.min, cfg.tz))
        if day.weekday() in weekdays and begin < end:
            slot = find_slot(task, now, fixed, [], cfg, minutes, budgets, policy, window=(begin, end))
            if slot:
                return slot
        day += timedelta(days=1)
    return None


def text_messages(lines):
    parts, current = [], ''
    for line in lines:
        if len(current) + len(line) + 1 > 3500:
            parts.append(current)
            current = ''
        current += ('\n' if current else '') + line
    if current:
        parts.append(current)
    return [{'text':part,'parse_mode':'HTML'} for part in parts]


def question_reply(text):
    return text if len(text) <= 3500 else {'messages':text_messages(text.split('\n'))}


def display_window(start, end, tz=None):
    a, b = (datetime.fromisoformat(value.replace('Z', '+00:00')) for value in (start, end))
    if tz is not None:
        a, b = a.astimezone(tz), b.astimezone(tz)
    # Include the year to keep saved reviews unambiguous across New Year.
    return f'{a:%Y/%m/%d (%a) %H:%M}' + (f'–{b:%H:%M}' if a.date() == b.date() else f'–{b:%Y/%m/%d %H:%M}')


def candidate_lines(candidates, tz):
    labels = []
    for event in candidates:
        start, end = (event.get(k, {}) for k in ('start', 'end'))
        if start.get('dateTime') and end.get('dateTime'):
            labels.append(display_window(start['dateTime'], end['dateTime'], tz))
        else:
            labels.append((start.get('date') or 'Date unavailable') + ' · All day')
    # Internal references are only needed when date/time cannot distinguish two
    # records. Keep the normal case free of identifiers and duplicated titles.
    return ['  • ' + label + (f" · ref:{event['id'][-16:]}" if labels.count(label) > 1 else '')
            for event, label in zip(candidates, labels)]


def card(state):
    lines = ['<b>Your revised study schedule</b>',
             'I have arranged the remaining sessions in order, using the earliest suitable times.',
             'Each session keeps its original duration. Please review before choosing Allow.']
    lines += [escape(note) for note in state.get('selection_notes', [])]
    for item in state.get('completed_events', []):
        lines += ['', '<b>' + escape(item['title'][:100]) + '</b> · Completed; unchanged']
    for move in state['calendar_moves']:
        lines += ['', '<b>' + escape(move['title'][:100]) + '</b>',
            'From: ' + display_window(move['old_start'], move['old_end']),
            'To: ' + display_window(move['start'], move['end']) + f" · {move['minutes']} min",
            escape(move['reason'])]
    if any(move.get('outcome_unknown') for move in state['calendar_moves']):
        lines += ['', 'Past sessions are still included where completion has not been confirmed.']
    lines += ['', 'Allow to apply these changes, or Edit to adjust your preferences.']
    token = state['token']
    messages = text_messages(lines)
    for message in messages:
        message['replan_preview_token'] = token
    messages[-1].update(replan_token=token,reply_markup={'inline_keyboard':[[{'text':a.title(),'callback_data':f'replan:{a}:{token}'} for a in ('allow','edit','cancel')]]})
    return {'messages':messages}


async def preview(planner, chat_id, instruction, update_id, now=None):
    from .calendar_replan import key
    now = now or datetime.now(planner.settings.tz)
    old = planner.db.get_task_action_state(key(chat_id)) or {}
    if old.get('status') in {'creating', 'paused'}:
        return 'An earlier change needs recovery. Use Allow on its card to inspect the remaining events first.'
    token = sha256(f'ordered:{chat_id}:{update_id}'.encode()).hexdigest()[:32]
    if old.get('token') == token:
        return question_reply(old['question']) if old.get('status') == 'clarifying' else card(old)
    prefs = read_preferences(planner.db, chat_id)
    previous = ({**old['parsed'], 'candidates': old.get('candidates',{}), 'original_instruction': old.get('original_instruction','')}
        if explicit_group(instruction,prefix=True) is None and (has_clarification(old) or
            (old.get('ordered_sequence') and old.get('status') == 'editing' and old.get('expires_at','') > now.isoformat())) else None)
    if previous and looks_ordered(instruction):
        # Shorthand can retain the group, but extraction never resolves cached
        # event candidates; discovery below owns that decision.
        previous = {**previous, 'candidates': {}}
    selection_reply = bool(previous and has_clarification(old) and looks_choice_answer(instruction))
    canonical = explicit_group(instruction) if not prefs else None
    parsed = (canonical if canonical is not None else SequenceReplanOutput.model_validate(old['parsed']) if selection_reply else
              SequenceReplanOutput.model_validate(await planner.ai.parse_sequence_replan(instruction, now, prefs, previous)))
    if selection_reply:
        parsed.choices = {}
    confirmed_choices = dict(old.get('confirmed_choices', {})) if previous and not looks_ordered(instruction) else {}
    if previous and old.get('status') == 'clarifying' and not looks_ordered(instruction) and parsed.actionable:
        parsed = SequenceReplanOutput.model_validate({**old['parsed'],'choices':parsed.choices})
    if not parsed.actionable or not parsed.scope:
        return escape(parsed.question or 'Which existing study group and chapter range should I reschedule in order?')
    if parsed.scope.casefold() not in instruction.casefold() and not (previous and parsed.scope == previous['scope']):
        return 'The group name was not in your request. Please specify the subject. Nothing changed.'
    literal = literal_ordinals(instruction, parsed.unit)
    if literal == []:
        return 'Use an ascending group of at most twenty items. Nothing changed.'
    requested = literal if literal is not None else parsed.ordinals
    if requested and (requested != sorted(set(requested)) or (literal is None and not previous and any(not re.search(r'(?<!\d)' + str(n) + r'(?!\d)', instruction) for n in requested))):
        return 'Please specify an unambiguous ascending chapter range. Nothing changed.'
    if not requested and not re.search(r'\ball\b|全部|所有', instruction, re.I):
        return 'Which chapter range should I reschedule? Nothing changed.'
    cfg = effective_config(planner.settings, parsed)
    start = max(now + timedelta(minutes=10), parsed.not_before.astimezone(cfg.tz) if parsed.not_before and parsed.not_before.utcoffset() is not None else now)
    stop = now + timedelta(days=30)
    if (parsed.not_before and parsed.not_before.utcoffset() is None) or (parsed.latest_end and parsed.latest_end.utcoffset() is None):
        return 'Please specify a timezone-aware future window. Nothing changed.'
    if parsed.latest_end:
        stop = min(stop, parsed.latest_end.astimezone(cfg.tz))
    if start >= stop:
        return 'The requested window has passed or is outside the thirty-day planning horizon. Nothing changed.'
    await planner.calendar.verify_owner()
    events = await planner.calendar.search_events(parsed.scope)
    # Known links can find events even if Calendar full-text search no longer indexes their description.
    seen = {e['id'] for e in events}
    for task in planner.db.list_open_tasks():
        number = ordinal(task['title'], parsed.scope, parsed.unit)
        saved = planner.get(task['id']) or {}
        if number is not None and (not requested or number in requested) and saved.get('event_id') and saved['event_id'] not in seen:
            event = await planner.calendar.event(saved['event_id'])
            if event:
                events.append({**event, '_calendar_id': cfg.calendar_id})
                seen.add(event['id'])
    matches = {}
    for event in events:
        number = ordinal(event.get('summary',''), parsed.scope, parsed.unit)
        if number is not None and event.get('status') != 'cancelled':
            matches.setdefault(number, {})[event['id']] = event
    requested = requested or sorted(matches)
    if not requested or len(requested) > 20:
        return 'No bounded group of matching events was found. Please narrow the subject/range; nothing changed.'
    parsed.ordinals = requested
    issues, moves, completed, ambiguous, selection_notes = [], [], [], {}, []
    for number in requested:
        candidates = list(matches.get(number, {}).values())
        current_candidates = [e for e in candidates if event_info(planner,e)['issue_code'] != 'stale_booking']
        if len(candidates) > 1 and previous and old.get('status') == 'clarifying':
            chosen = parsed.choices.get(str(number))
            explicit = [e for e in candidates if choice_evidence(instruction,e,candidates,cfg.tz)]
            if len(explicit) == 1 and (not chosen or chosen == explicit[0]['id']):
                if explicit[0] not in current_candidates and current_candidates:
                    ambiguous[str(number)] = [{k:e.get(k) for k in ('id','summary','start','end')} for e in current_candidates]
                    issues.append(f'{parsed.unit.title()} {number}: The selected event is an older task booking. It will stay unchanged. Please choose the current session:\n' + '\n'.join(candidate_lines(current_candidates,cfg.tz)))
                    continue
                candidates = explicit
                confirmed_choices[str(number)] = explicit[0]['id']
            elif not explicit and confirmed_choices.get(str(number)):
                retained = [e for e in candidates if e['id'] == confirmed_choices[str(number)]]
                if retained:
                    candidates = retained
        if len(candidates) > 1 and current_candidates and len(current_candidates) < len(candidates):
            candidates = current_candidates
            selection_notes.append(f'{parsed.unit.title()} {number}: older task bookings stay unchanged; the remaining matching session is reviewed below.')
        if len(candidates) != 1:
            if len(candidates) > 1:
                ambiguous[str(number)] = [{k:e.get(k) for k in ('id','summary','start','end')} for e in candidates]
            if not candidates:
                issues.append(f'{parsed.unit.title()} {number}: I could not find an event in your calendar. Please check its title or tell me which event to use.')
            else:
                issues.append(f'{parsed.unit.title()} {number} — I found {len(candidates)} matching events:\n' + '\n'.join(candidate_lines(candidates, cfg.tz)))
            continue
        event = candidates[0]
        info = event_info(planner, event)
        common = {'ordinal': number, 'event_id': event['id'], 'title': event.get('summary',''), 'etag': event.get('etag'), **info}
        if info['completed']:
            completed.append(common)
            continue
        if info['issue'] or not eligible(event, cfg):
            issues.append(f"{parsed.unit.title()} {number}: {info['issue'] or 'I cannot safely move this event. Please check whether it is shared, recurring, all-day, or owned by another account.'}")
            continue
        a,b = bounds(event, cfg.tz)
        seconds = (b-a).total_seconds()
        if seconds <= 0 or seconds % 60 or seconds > cfg.learning_daily_minutes*60:
            issues.append(f'{parsed.unit.title()} {number}: The full session exceeds the supported daily study limit. Please adjust that limit or split the session first.')
            continue
        moves.append({**common, 'old_start': a.isoformat(), 'old_end': b.isoformat(), 'minutes': int(seconds/60),
                      'budget_token': (info['task_state'] or {}).get('token') or 'sequence:' + event['id']})
    if issues:
        question = ('I can rearrange this group, Sir. I need to confirm ' + ('one detail' if len(issues) == 1 else 'a few details')
                    + ' first.\n\n' + '\n\n'.join(escape(i) for i in issues))
        if ambiguous:
            question += '\n\nWhich event should I move? Reply with the date and time' + (' for each item' if len(ambiguous) > 1 else '') + '.'
            if any('ref:' in line for line in issues):
                question += ' For identical times, include the displayed ref.'
            question += '\nThis selects the event; I will look for an earlier suitable time for it.'
        question += '\n\nYour calendar is unchanged. I will show the full schedule for your approval.'
        if issues:
            clarification = {'ordered_sequence':True,'status':'clarifying','token':token,'chat_id':chat_id,
                'parsed':parsed.model_dump(mode='json'),'candidates':ambiguous,'question':question,
                'confirmed_choices':confirmed_choices,
                'original_instruction':old.get('original_instruction') if previous and not looks_ordered(instruction) else instruction,
                'expires_at':(now+timedelta(minutes=10)).isoformat()}
            planner.db.mutate_task_action_state(key(chat_id),lambda current:current if current and current.get('status') in {'creating','paused'} else clarification)
        return question_reply(question)
    if not moves:
        return 'All matching chapters are confirmed completed. Their events were kept unchanged.'
    agenda = await planner.calendar.agenda(now, stop)
    fixed, budgets = fixed_and_budgets(planner, moves, agenda, now, stop)
    policy = planner.db.get_task_action_state('learning:course-calendar-policy')
    # Selecting an event identifies what to move, not a date to preserve.
    # Release all selected slots together and place predecessors as early as
    # constraints permit; retaining a late first slot delays the entire group.
    occupied, load = list(fixed), dict(budgets)
    cursor = start
    failed = None
    for move in moves:
        task = planner.db.get_task(move['task_id']) if move['task_id'] is not None else {}
        if task is None:
            return 'A linked task changed during planning. Please retry the group; no events changed.'
        due = deadline(task, cfg)
        limit = min(stop, due) if due else stop
        slot = choose_slot(task, move['minutes'], tuple(datetime.fromisoformat(move[k]) for k in ('old_start','old_end')),
                           cursor, limit, occupied, load, cfg, policy, parsed.weekdays, now, prefer_original=False)
        if not slot:
            failed = (move,limit)
            break
        move.update(start=slot[0].isoformat(), end=slot[1].isoformat(),
                    reason='This time still fits the session order.' if slot[0].isoformat() == move['old_start'] else 'Earliest suitable time after the preceding session.' if move != moves[0] else 'Earliest suitable time to begin the group.',
                    outcome_unknown=datetime.fromisoformat(move['old_end']) <= now)
        cursor = slot[1]
        occupied.append(slot)
        day = slot[0].date().isoformat()
        load[day] = load.get(day,0) + move['minutes']
    if failed:
        move,limit = failed
        return f"Cannot place {escape(move['title'])} after its predecessors within the deadline, preferences and available capacity (search ends {limit:%m/%d %H:%M}). Choose a later deadline or smaller group; no events changed."
    state = {'ordered_sequence': True, 'status': 'pending', 'token': token, 'chat_id': chat_id,
        'parsed': parsed.model_dump(mode='json'), 'preferences': prefs, 'policy': policy,
        'config': {k:getattr(cfg,k) for k in ('learning_start_hour','learning_end_hour','learning_daily_minutes')},
        'calendar_moves': moves, 'completed_events': completed, 'selection_notes': selection_notes, 'applied_event_ids': [], 'recorded_states': {},
        'expires_at': (now+timedelta(hours=2)).isoformat(), 'start': start.isoformat(), 'end': stop.isoformat()}
    saved = planner.db.mutate_task_action_state(key(chat_id), lambda current: current if current and current.get('status') in {'creating','paused'} else state)
    return card(saved) if saved.get('token') == token else 'Another calendar operation needs recovery first.'

class ReviewChanged(CalendarError):
    pass


def assert_task_current(planner, state, move):
    if move['task_id'] is None:
        return
    task = planner.db.get_task(move['task_id'])
    expected = state.get('recorded_states', {}).get(move['event_id'], move['task_state'])
    if (not task or task_fields(task) != move['task_snapshot'] or task.get('status') != move['task_status']
            or planner.get(move['task_id']) != expected):
        raise ReviewChanged(f"The task state changed for {move['title']}; refresh the group before continuing.")


def is_applied(event, state, move, tz):
    return bool(event and event.get('extendedProperties',{}).get('private',{}).get('chronosSequence') == state['token']
                and bounds(event,tz) == tuple(datetime.fromisoformat(move[k]) for k in ('start','end')))


async def preflight(planner, state, now):
    if read_preferences(planner.db, state['chat_id']) != state['preferences']:
        raise ReviewChanged('Owner preferences changed; prepare a fresh review.')
    if planner.db.get_task_action_state('learning:course-calendar-policy') != state['policy']:
        raise ReviewChanged('The course calendar policy changed; prepare a fresh review.')
    parsed = SequenceReplanOutput.model_validate(state['parsed'])
    cfg = effective_config(planner.settings, parsed)
    if {k:getattr(cfg,k) for k in state['config']} != state['config']:
        raise ReviewChanged('Working hours or daily capacity changed; prepare a fresh review.')
    current = {}
    for item in [*state['completed_events'], *state['calendar_moves']]:
        event = await planner.calendar.event(item['event_id'])
        if not event or event.get('status') == 'cancelled':
            raise ReviewChanged(f"{item['title']} was removed; inspect Calendar and refresh the group.")
        if item.get('completed'):
            if event.get('etag') != item['etag'] or not event_info(planner,event)['completed']:
                raise ReviewChanged('A completed chapter changed; refresh the group.')
            continue
        assert_task_current(planner,state,item)
        applied = is_applied(event,state,item,cfg.tz)
        if not applied and (event.get('etag') != item['etag'] or not eligible({**event,'_calendar_id':cfg.calendar_id},cfg)):
            raise ReviewChanged(f"{item['title']} changed in Calendar; refresh the group.")
        current[item['event_id']] = event
    start, stop = (datetime.fromisoformat(state[k]) for k in ('start','end'))
    agenda = await planner.calendar.agenda(min(now,start), stop)
    fixed, budgets = fixed_and_budgets(planner,state['calendar_moves'],agenda,now,stop)
    cursor = max(now + timedelta(minutes=10), start)
    for move in state['calendar_moves']:
        a,b = (datetime.fromisoformat(move[k]) for k in ('start','end'))
        task = planner.db.get_task(move['task_id']) if move['task_id'] is not None else {}
        # Previously verified writes are retained during recovery; their start may have elapsed.
        applied = is_applied(current[move['event_id']],state,move,cfg.tz)
        if not applied:
            if a < cursor or choose_slot(task,move['minutes'],(a,b),a,b,fixed,budgets,cfg,state['policy'],parsed.weekdays,now) != (a,b):
                raise ReviewChanged(f"The reviewed time for {move['title']} is now unavailable; refresh before continuing.")
        elif a.weekday() not in parsed.weekdays:
            raise ReviewChanged('A recovered session no longer meets the reviewed preference.')
        fixed.append((a,b))
        budgets[a.date().isoformat()] = budgets.get(a.date().isoformat(),0) + move['minutes']
        cursor = b
    return current


def record_applied(planner, state, move, lease):
    from .calendar_replan import key
    db = planner.db
    def save():
        review = db.get_task_action_state(key(state['chat_id'])) or {}
        if review.get('lease') != lease or review.get('token') != state['token']:
            raise CalendarError('Another recovery owns this operation; inspect the review card.')
        if move['event_id'] in review.get('applied_event_ids',[]):
            return review
        assert_task_current(planner,review,move)
        days = {move['old_start'][:10],move['start'][:10]}
        buckets = {day:dict(db.get_task_action_state('learning:day:'+day) or {}) for day in days}
        queue = db.get_task_action_state('learning:proposal-queue') or {}
        saved = planner.get(move['task_id']) if move['task_id'] is not None else None
        revised = None
        if saved is not None:
            revised = {**saved, 'status':'scheduled','start':move['start'],'end':move['end'],
                       'minutes':move['minutes'],'requested_minutes':move['minutes'],'snapshot':move['task_snapshot']}
            if 'nightly_slot' in revised:
                revised['nightly_slot'] = {k:move[k] for k in ('start','end')}
            for name in ('delivery','delivery_claim','message_id','edit_message_id','feedback_requested_at'):
                revised.pop(name,None)
        buckets[move['old_start'][:10]].pop(move['budget_token'],None)
        buckets[move['start'][:10]][move['budget_token']] = move['minutes']
        updated = {**review,'applied_event_ids':[*review.get('applied_event_ids',[]),move['event_id']],
                   'recorded_states':{**review.get('recorded_states',{}), **({move['event_id']:revised} if revised else {})}}
        if saved is not None:
            db._save_task_action_state('sequence:history:'+state['token']+':'+move['event_id'],saved)
            db._save_task_action_state('learning:task:'+str(move['task_id']),revised)
            retired = [saved.get(k) for k in ('message_id','edit_message_id') if saved.get(k)]
            db._save_task_action_state('learning:proposal-queue',{**queue,
                'active_task_id':None if queue.get('active_task_id') == move['task_id'] else queue.get('active_task_id'),
                'retire_message_ids':list(dict.fromkeys([*queue.get('retire_message_ids',[]),*retired]))})
        for day,bucket in buckets.items():
            db._save_task_action_state('learning:day:'+day,bucket)
        db._save_task_action_state(key(state['chat_id']),updated)
        return updated
    return db._run_task_action(save)


def outcome(state, unknown=(), problem=None):
    applied = set(state.get('applied_event_ids',[]))
    all_done = len(applied) == len(state['calendar_moves']) and not unknown and not problem
    lines = ['Ordered rescheduling completed.' if all_done else 'I have paused the changes; the whole group is not complete.']
    for item in state['calendar_moves']:
        status = 'Result unverified or changed' if item['event_id'] in unknown else 'Updated and verified' if item['event_id'] in applied else 'Not updated'
        lines += ['', f"{state['parsed']['unit'].title()} {item['ordinal']} · {status}", display_window(item['start'], item['end'])]
    if state['completed_events']:
        lines.append(f"{len(state['completed_events'])} confirmed completed chapter(s) kept unchanged.")
    if problem:
        lines.append(escape(problem))
    if not all_done:
        lines.append('Inspect the listed items in Calendar. Allow checks the remaining updates without repeating verified moves; use Edit for a fresh group review if a remaining event changed. Unknown results must be checked with Allow first.')
    return '\n'.join(lines)


async def callback(planner, chat_id, message_id, token, action, now=None):
    from .calendar_replan import key
    now = now or datetime.now(planner.settings.tz)
    lease = uuid4().hex
    def claim(old):
        if not old or old.get('token') != token or old.get('message_id') != message_id:
            return old
        if old.get('status') in {'done','cancelled','expired'}:
            return old
        if old.get('status') == 'creating' and old.get('lease_until','') > now.isoformat():
            return old
        recovering = old.get('status') in {'creating','paused'}
        if not recovering and old.get('expires_at','') <= now.isoformat():
            return {**old,'status':'expired'}
        if action != 'allow':
            if recovering:
                if action == 'edit' and not old.get('unknown_event_ids'):
                    return {**old,'status':'editing','partial_restart':True}
                return old
            return {**old,'status':'editing' if action == 'edit' else 'cancelled'}
        return {**old,'status':'creating','lease':lease,'lease_until':(now+timedelta(minutes=3)).isoformat()}
    state = planner.db.mutate_task_action_state(key(chat_id),claim)
    if not state or state.get('token') != token or state.get('message_id') != message_id:
        return 'This ordered review is no longer active. Request a fresh group review.'
    if state['status'] == 'done':
        return 'These ordered changes have already been applied.\n' + outcome(state)
    if state['status'] in {'cancelled','expired'}:
        return 'Ordered review '+state['status']+'. No new Calendar changes were made.'
    if state['status'] == 'editing':
        if state.get('partial_restart'):
            planner.db.mutate_task_action_state('sequence:batch:'+token,lambda old:old or state)
        return 'Send revised group constraints in one message. Scope and chapter order can be retained.'
    if state.get('lease') != lease:
        return 'An ordered update is in progress or needs recovery. Use Allow to inspect it; no second writer was started.'
    problem, unknown = None, []
    try:
        await planner.calendar.verify_owner()
        current = await preflight(planner,state,now)
        for move in state['calendar_moves']:
            event = current[move['event_id']]
            if not is_applied(event,state,move,planner.settings.tz):
                live = planner.db.get_task_action_state(key(chat_id)) or {}
                if live.get('lease') != lease:
                    raise CalendarError('Another operation now owns this review.')
                props = event.get('extendedProperties',{}).get('private',{})
                body = {'start':{'dateTime':move['start'],'timeZone':planner.settings.timezone},
                        'end':{'dateTime':move['end'],'timeZone':planner.settings.timezone},
                        'extendedProperties':{**event.get('extendedProperties',{}),'private':{**props,'chronosSequence':token}}}
                await planner.calendar.move_event(move['event_id'],body,move['etag'])
                verified = await planner.calendar.event(move['event_id'])
                if not is_applied(verified,state,move,planner.settings.tz):
                    raise CalendarError('A Calendar update could not be verified.')
            state = record_applied(planner,state,move,lease)
    except (CalendarError, ValueError) as error:
        problem = str(error)
        # Reconcile uncertain responses by reading markers, never by blindly repeating PATCH.
        verified_now = set()
        for move in state['calendar_moves']:
            try:
                event = await planner.calendar.event(move['event_id'])
                if is_applied(event,state,move,planner.settings.tz):
                    state = record_applied(planner,state,move,lease)
                    verified_now.add(move['event_id'])
                elif move['event_id'] in state.get('applied_event_ids',[]):
                    unknown.append(move['event_id'])
            except (CalendarError, ValueError):
                unknown.append(move['event_id'])
        if len(verified_now) == len(state['calendar_moves']) and not unknown:
            problem = None
    def release(old):
        if old.get('token') != token or old.get('lease') != lease:
            return old
        return {**old,'status':'done' if not problem else 'paused' if old.get('applied_event_ids') or unknown else 'pending',
                'lease_until':now.isoformat(),'unknown_event_ids':list(set(unknown))}
    state = planner.db.mutate_task_action_state(key(chat_id),release)
    return outcome(state,unknown,problem)
