"""Whole-day study corrections using the existing versioned review gate."""
from datetime import datetime, timedelta
from hashlib import sha256
from html import escape
import re
from uuid import uuid4

from .ai import StudyRevisionOutput
from .google_calendar import CalendarError
from .task_actions import task_fields
from .study_reflow import bounds, movable, ReflowReviewError


def is_correction(text):
    return bool(re.search(r'chapter|\bch\s*\d|章', text, re.I) and
                re.search(r'min|hour|分鐘|小时|小時|未完成|沒.*完|not finish', text, re.I))


def chapter_scope(instruction, tasks):
    """Verify literal course/chapter references independently of model omissions."""
    matched = {}
    for i, task in tasks.items():
        match = re.fullmatch(r'(?:Study|Review)?\s*(.+?)\s+(?:Chapter|Ch\.?)\s*(\d+)\s*', task['title'], re.I)
        if match and re.search(r'(?<!\w)' + re.escape(match[1].strip()) + r'(?!\w)', instruction, re.I):
            matched[i] = int(match[2])
    named = {int(n) for n in re.findall(r'\bch(?:apter)?\.?\s*(\d+)', instruction, re.I)}
    return matched, named


def recent_context(db, now):
    day = (now.date() + timedelta(days=1)).isoformat()
    record = db.get_task_action_state('evening:agenda:' + day) or {}
    if record.get('status') == 'sent':
        return {'day': day, 'parts': record.get('parts', [])}
    return None


async def preview(planner, chat_id, instruction, update_id, now=None):
    from .calendar_replan import key, card
    from .learning_calendar import find_slot
    from .nightly_planning import day_bounds, state_window
    from .backward_planning import reserved_windows
    now = now or datetime.now(planner.settings.tz)
    db, cfg = planner.db, planner.settings
    old_review = db.get_task_action_state(key(chat_id))
    token = sha256(f'study-revision:{chat_id}:{update_id}'.encode()).hexdigest()[:32]
    if old_review and old_review.get('status') == 'creating':
        return 'A calendar change needs recovery. Use Allow on its existing card first.'
    if old_review and old_review.get('token') == token:
        return card(old_review)
    tasks = {t['id']: t for t in db.list_open_tasks()}
    states = {i: planner.get(i) for i in tasks}
    context = recent_context(db, now)
    if (old_review and old_review.get('study_revision') and old_review.get('status') == 'editing'
            and old_review.get('expires_at', '') > now.isoformat()):
        context = {'day': old_review['start'][:10], 'previous_sessions': [*old_review['calendar_moves'], *old_review['revision_additions']]}
    explicit_day = re.search(
        r'\d{4}-\d{1,2}-\d{1,2}|\b\d{1,2}/\d{1,2}\b|'
        r'\b(?:today|tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|'
        r'今天|明天|後天|后天|今晚|(?:星期|週|周)[一二三四五六日天]|\d{1,2}月\d{1,2}[日號号]', instruction, re.I)
    if not explicit_day and not context:
        return 'Which study day should I revise? Please give a date. Nothing changed.'
    stated = re.search(r'(?:at\s+least|至少)\s*(?:各|each\s*)?(\d+)\s*(?:min(?:ute)?s?|分鐘|分钟)', instruction, re.I)
    if stated and not 10 <= int(stated[1]) <= 180:
        return 'Each chapter session supports 10 to 180 minutes. Please specify a duration within that limit. Nothing changed.'
    output = StudyRevisionOutput.model_validate(await planner.ai.parse_study_revision(
        instruction, [{'id': i, **task_fields(t)} for i, t in tasks.items()], context, now))
    if not output.actionable or not output.day or not output.items:
        return escape(output.question or 'Which study day and chapters should I revise? Nothing changed.')
    try:
        day = datetime.strptime(output.day, '%Y-%m-%d').date()
    except ValueError:
        return 'Please specify the study date. Nothing changed.'
    if not now.date() <= day <= now.date() + timedelta(days=6):
        return 'Choose a study day within the next seven days. Nothing changed.'
    minimums = {i.task_id: i.minimum_minutes for i in output.items}
    if len(minimums) != len(output.items) or any(i not in tasks for i in minimums):
        return 'The requested chapters could not be matched uniquely. Nothing changed.'
    if stated and any(m < int(stated[1]) for m in minimums.values()):
        return 'The parsed duration is below your stated minimum. Please restate the chapter duration. Nothing changed.'
    scope, named = chapter_scope(instruction, tasks)
    if scope and named - set(scope.values()):
        return 'A named unfinished chapter has no matching open task. Please specify its task name. Nothing changed.'
    required = set(scope) if re.search(r'per\s+chapter|each\s+chapter|每(?:一)?章', instruction, re.I) else {i for i, n in scope.items() if n in named}
    if required - set(minimums):
        if stated:
            minimums.update({i: int(stated[1]) for i in required})
        else:
            return 'The parsed plan omitted a named chapter. Please confirm the chapters and minimum duration. Nothing changed.'
    if any(not 10 <= m <= 180 for m in minimums.values()):
        return 'Use chapter sessions between 10 and 180 minutes. Nothing changed.'
    begin, end = day_bounds(day, cfg)
    await planner.calendar.verify_owner()
    events = await planner.calendar.agenda(min(now, begin), end)
    keep_others = bool(re.search(
        r'其他.{0,12}(?:照舊|照旧|不動|不动|不改|維持|维持)|'
        r'(?:keep|leave).{0,30}(?:other|rest).{0,35}(?:unchanged|as is)|'
        r'(?:other|rest).{0,20}(?:unchanged|as is)', instruction, re.I))
    selected, fixed, additions = {}, [], []
    for event in events:
        if event.get('status') == 'cancelled':
            continue
        a, b = bounds(event, cfg.tz)
        props = event.get('extendedProperties', {}).get('private', {})
        linked = next((i for i, s in states.items() if s and s.get('event_id') == event.get('id')
                       and props.get('chronosTask') == str(i) and props.get('chronosToken') == s.get('token')
                       and event.get('_calendar_id', '').casefold() in {cfg.calendar_id.casefold(), cfg.calendar_account.casefold(), 'primary'}), None)
        state = states.get(linked) or {}
        # Retain all other tracked sessions on the revised day, without changing their duration.
        relevant = linked is not None and a.date() == day and b > now and (not keep_others or linked in minimums)
        if relevant:
            if (state.get('status') != 'scheduled' or not movable(event, cfg, now)
                    or (a.isoformat(), b.isoformat()) != (state.get('start'), state.get('end'))):
                return 'A study booking has started or changed. Refresh the agenda before revising. Nothing changed.'
            if linked in selected:
                return 'Duplicate study bookings need inspection. Nothing changed.'
            selected[linked] = (event, a, b)
        elif event.get('transparency') != 'transparent':
            fixed.append((a, b))
    for i in minimums:
        if i in selected:
            continue
        state = states[i] or {}
        # Do not replace pending approvals, future bookings, or uncertain external writes.
        if (state.get('status') not in {None, 'needs_replan', 'finished', 'scheduled', 'awaiting_feedback'}
                or (state.get('delivery') or '').endswith(('_uncertain', '_sending'))
                or (state.get('status') in {'scheduled', 'awaiting_feedback'} and
                    (not state.get('end') or datetime.fromisoformat(state['end']) > now))):
            return f"Resolve the existing plan for {escape(tasks[i]['title'])} first. Nothing changed."
        additions.append(i)
    target_ids = set(selected) | set(additions)
    if len(target_ids) > 20:
        return 'At most twenty study tasks can be revised together. Nothing changed.'
    for i, state in states.items():
        if i in target_ids or not state or state.get('status') not in {'queued', 'planning', 'proposed', 'editing', 'creating'}:
            continue
        source = state_window(state)
        if source.get('start') and source.get('end'):
            fixed.append(tuple(datetime.fromisoformat(source[k]) for k in ('start', 'end')))
    fixed += reserved_windows(db)
    bucket = dict(db.get_task_action_state('learning:day:' + day.isoformat()) or {})
    for i in selected:
        bucket.pop(states[i]['token'], None)
    budgets = {day.isoformat(): sum(bucket.values())}
    policy = db.get_task_action_state('learning:course-calendar-policy')
    moves, pending = [], []
    # Numeric title order keeps Chapter 1/2 before Chapter 10, within deadline priority.
    def ordering(i):
        return (tasks[i].get('due_at') or (tasks[i].get('timing') or {}).get('due_date') or '9999',
                re.sub(r'\d+', lambda m: m[0].zfill(8), tasks[i]['title'].casefold()), i)
    for i in sorted(target_ids, key=ordering):
        old = selected.get(i)
        original_minutes = int((old[2] - old[1]).total_seconds()/60) if old else (states[i] or {}).get('minutes', 0)
        minutes = max(original_minutes, minimums.get(i, 0))
        slot = find_slot(tasks[i], now, fixed, [], cfg, minutes, budgets, policy, window=(begin, end))
        if not slot:
            return (f"The revised plan needs at least {minutes} minutes for {escape(tasks[i]['title'])}, "
                    'but it does not fit the remaining time, deadline or daily limit. '
                    'Which task may move to another day? Nothing changed.')
        fixed.append(slot)
        budgets[day.isoformat()] += minutes
        common = {'task_id': i, 'task_snapshot': task_fields(tasks[i]), 'title': tasks[i]['title'],
                  'start': slot[0].isoformat(), 'end': slot[1].isoformat(), 'minutes': minutes}
        if old:
            moves.append({**common, 'event_id': old[0]['id'], 'etag': old[0]['etag'],
                          'task_token': states[i]['token'], 'old_start': old[1].isoformat(), 'old_end': old[2].isoformat()})
        else:
            pending.append({**common, 'token': uuid4().hex})
    state = {'status': 'pending', 'token': token, 'chat_id': chat_id, 'start': begin.isoformat(), 'end': end.isoformat(),
             'destination_start': begin.isoformat(), 'calendar_moves': moves, 'study_revision': True,
             'revision_additions': pending, 'revision_states': {str(i): states[i] for i in target_ids},
             'window_replan': True, 'minutes': 0, 'expires_at': (now + timedelta(hours=2)).isoformat()}
    def save(old):
        return old if old and old.get('status') == 'creating' else state
    saved = db.mutate_task_action_state(key(chat_id), save)
    return card(saved) if saved.get('token') == token else 'Another calendar change is in progress.'


def validate_additions(planner, state):
    for item in state.get('revision_additions', []):
        task = planner.db.get_task(item['task_id'])
        current = planner.get(item['task_id'])
        if (not task or task.get('status') != 'open' or task_fields(task) != item['task_snapshot']
                or current != state['revision_states'][str(item['task_id'])]):
            raise ReflowReviewError('An unfinished task changed. Prepare a fresh proposal.')


def finish(planner, state):
    """Queue new exact sessions once; keep the original session as history."""
    db = planner.db
    def save():
        from .calendar_replan import key
        review = db.get_task_action_state(key(state['chat_id']))
        if review.get('revision_queued'):
            return
        validate_additions(planner, state)
        queue = db.get_task_action_state('learning:proposal-queue') or {}
        old_states = {item['task_id']: planner.get(item['task_id']) for item in state['revision_additions']}
        for item in state['revision_additions']:
            old = old_states[item['task_id']]
            if old and old.get('token'):
                db._save_task_action_state('learning:history:' + old['token'], old)
            db._save_task_action_state('learning:task:' + str(item['task_id']), {
                'status': 'queued', 'token': item['token'], 'snapshot': item['task_snapshot'],
                'requested_minutes': item['minutes'], 'duration_source': 'nightly_ai',
                'nightly_slot': {k: item[k] for k in ('start', 'end')},
                'duration_estimate': {'estimated_minutes': item['minutes']},
                'progress_note': 'Reported unfinished; completion and time spent are unknown.'})
        ids = [item['task_id'] for item in state['revision_additions']]
        db._save_task_action_state('learning:proposal-queue', {**queue, 'requested_task_ids': list(dict.fromkeys([*queue.get('requested_task_ids', []), *ids]))})
        db._save_task_action_state(key(state['chat_id']), {**review, 'revision_queued': True})
    db._run_task_action(save)
