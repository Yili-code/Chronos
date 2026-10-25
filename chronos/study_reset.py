"""Owner-requested study rebuild with dependency planning and durable reconciliation."""
from datetime import datetime, timedelta, time
from hashlib import sha256
from html import escape
import re
import time as clock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .google_calendar import CalendarError
from .ai import AIError
from .study_reflow import bounds
from .task_actions import task_fields

KEY = 'learning:study-reset'
ACTIVE = {'planning', 'applying', 'partial'}
COMMAND_HELP = ('/studyreset — 刪除舊 study 行程，依前置順序重建所有未完成學習任務。\n'
                '/studyreset 包含過期未完成的行程\n/studyreset 只排未來\n'
                '範圍不明確時會先追問；明確時直接執行。')


def command_args(text):
    match = re.fullmatch(r'\s*/studyreset(?:@[A-Za-z0-9_]+)?(?:\s+([\s\S]*\S))?\s*', text, re.I)
    return (match[1] or '') if match else None


async def command(planner, chat_id, text, update_id, now=None):
    args = command_args(text)
    if args is None or args.casefold() in {'help', '?', '說明', '说明'}:
        return COMMAND_HELP
    return await run(planner, chat_id,
        'Rebuild all unfinished study sessions in prerequisite order, including unbooked study tasks.\n' + args,
        update_id, now)


class StudyChoice(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    task_id: int
    is_study: bool
    minutes: int = Field(ge=10, le=10080)


class Dependency(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    before: int
    after: int


class ExamAnchor(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    task_id: int
    event_id: str


class ResetOutput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    actionable: bool
    include_past: bool | None = None
    start: str | None = None
    end: str | None = None
    start_hour: int | None = Field(default=None, ge=0, le=23)
    end_hour: int | None = Field(default=None, ge=1, le=24)
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)), max_length=7)
    tasks: list[StudyChoice] = Field(default_factory=list, max_length=100)
    dependencies: list[Dependency] = Field(default_factory=list, max_length=500)
    exams: list[ExamAnchor] = Field(default_factory=list, max_length=100)
    question: str | None = None


def looks_reset(text):
    """A narrow fast route; the model handles further paraphrases."""
    study = re.search(r'\bstudy\b|\blearning\b|讀書|读书|學習|学习', text, re.I)
    bulk = re.search(r'全部|所有|整份|整個|整批|\ball\b|\bevery\b', text, re.I)
    rebuild = re.search(r'重排|重來|重来|重新|重建|打掉|\breset\b|\brebuild\b|\breschedul', text, re.I)
    negative = (re.search(r'(?:不要|別|别|不需要|不用|do not|don.t|never)[^，,。;；]{0,35}'
                         r'(?:重排|重來|重来|重新|重建|reset|rebuild|reschedul)', text, re.I)
                or re.search(r'^\s*(?:如何|怎麼|怎么|how\b)|只.*(?:問|问)', text, re.I))
    return bool(study and bulk and rebuild and not negative and not re.search(r'[「」『』"{}]', text))


def pending_reply(state, text):
    return bool(state and state.get('status') == 'question' and re.fullmatch(
        r'\s*(?:是|要|包含|全部|也要|包括過期的|包括过期的|不要過去的|不要过去的|只排未來|只排未来|'
        r'不含過去|不含过去|yes|no|include past|future only|取消|/?cancel)[。.!！\s]*', text, re.I))


def ordered(tasks, dependencies):
    edges = {(e.before, e.after) for e in dependencies}
    chapters = {}
    for i, task in tasks.items():
        m = re.search(r'^(?:study\s+|review\s+)?(.+?)\s*(?:chapter|ch\.?)\s*(\d+)\b', task['title'], re.I)
        if m:
            chapters.setdefault(m[1].strip().casefold(), []).append((int(m[2]), i))
    for group in chapters.values():
        group.sort()
        edges.update((a[1], b[1]) for a in group for b in group if a[0] < b[0])
    if any(a not in tasks or b not in tasks or a == b for a, b in edges):
        raise ValueError('A prerequisite does not match an unfinished study task.')
    result, remaining = [], set(tasks)
    while remaining:
        ready = [i for i in remaining if not any(b == i and a in remaining for a, b in edges)]
        if not ready:
            raise ValueError('The prerequisite order contains a cycle. Which unit should come first?')
        # Earliest deadlines first among ready nodes; subjects may interleave.
        i = min(ready, key=lambda i: (tasks[i].get('due_at') or
            (tasks[i].get('timing') or {}).get('due_date') or '9999', i))
        result.append(i)
        remaining.remove(i)
    return result, edges


def deletable(event, cfg):
    owner = cfg.calendar_account.casefold()
    return (writable(event, cfg)
        and event.get('creator', {}).get('email', '').casefold() == owner
        and (event.get('organizer', {}).get('self') or event.get('organizer', {}).get('email', '').casefold() == owner)
        and bool(event.get('etag')) and event.get('eventType', 'default') == 'default'
        and not any(event.get(k) for k in ('attendees', 'attendeesOmitted', 'recurrence', 'recurringEventId'))
        and bool(event.get('start', {}).get('dateTime')))


def writable(event, cfg):
    source = cfg.calendar_id.casefold()
    aliases = {source}
    if source in {'primary', cfg.calendar_account.casefold()}:
        aliases.update({'primary', cfg.calendar_account.casefold()})
    return event.get('_calendar_id', '').casefold() in aliases


def constraints(planner):
    from .preferences import read
    cfg = planner.settings
    return {'hours': [cfg.learning_start_hour, cfg.learning_end_hour],
            'daily_minutes': cfg.learning_daily_minutes,
            'preparation_minutes': cfg.learning_class_preparation_minutes,
            'course_policy': planner.db.get_task_action_state('learning:course-calendar-policy'),
            'preferences': read(planner.db, cfg.telegram_chat_id)}


def schedule(planner, tasks, choices, edges, order, fixed, removed, start, stop, now, output):
    from .learning_calendar import find_slot
    from .backward_planning import reserved_windows
    cfg, db = planner.settings, planner.db
    begin_hour = max(cfg.learning_start_hour, output.start_hour if output.start_hour is not None else cfg.learning_start_hour)
    end_hour = min(cfg.learning_end_hour, output.end_hour if output.end_hour is not None else cfg.learning_end_hour)
    if begin_hour >= end_hour or not output.weekdays or any(day not in range(7) for day in output.weekdays):
        raise ValueError('The requested study hours or weekdays leave no available window.')
    cfg = cfg.model_copy(update={'learning_start_hour': begin_hour, 'learning_end_hour': end_hour})
    budgets = {}
    day = start.date()
    while day <= stop.date():
        bucket = dict(db.get_task_action_state('learning:day:' + day.isoformat()) or {})
        for event in removed:
            bucket.pop(event.get('extendedProperties', {}).get('private', {}).get('chronosToken'), None)
        budgets[day.isoformat()] = sum(bucket.values())
        day += timedelta(days=1)
    policy = db.get_task_action_state('learning:course-calendar-policy')
    from .nightly_planning import class_blocks
    reserved = reserved_windows(db)
    fixed = [*fixed, *reserved]
    for a, b in reserved:
        if a.date().isoformat() in budgets:
            budgets[a.date().isoformat()] += (b - a).total_seconds() / 60
    day = start.date()
    while day <= stop.date():
        fixed.extend(class_blocks(day, cfg, policy))
        day += timedelta(days=1)
    selected = set(tasks)
    for task in db.list_open_tasks():
        state = planner.get(task['id']) or {}
        if task['id'] not in selected and state.get('status') in {'queued', 'planning', 'proposed', 'editing', 'creating'}:
            if state.get('start') and state.get('end'):
                a, b = tuple(datetime.fromisoformat(state[k]) for k in ('start', 'end'))
                fixed.append((a, b))
                if a.date().isoformat() in budgets:
                    budgets[a.date().isoformat()] += (b - a).total_seconds() / 60
    for factor in (1, .75, .5, .25, .1, 0):
        busy, load, ends, sessions = list(fixed), dict(budgets), {}, []
        failed = False
        for i in order:
            cursor = max([start, *(ends[a] for a, b in edges if b == i)])
            total = max(10, int(choices[i] * factor))
            remaining = total
            while remaining:
                minutes = min(180, remaining)
                if 0 < remaining - minutes < 10:
                    minutes -= 10
                slot, day = None, cursor.date()
                while day <= stop.date():
                    if day.weekday() not in output.weekdays:
                        day += timedelta(days=1)
                        continue
                    a = max(cursor, datetime.combine(day, time(), cfg.tz))
                    b = min(stop, datetime.combine(day + timedelta(days=1), time(), cfg.tz))
                    if a < b:
                        task = tasks[i]
                        if not task.get('due_at') and not (task.get('timing') or {}).get('due_date'):
                            task = {**task, 'due_at': stop.isoformat()}
                        slot = find_slot(task, now, busy, [], cfg, minutes, load, policy, window=(a, b))
                    if slot:
                        break
                    day += timedelta(days=1)
                if not slot:
                    failed = True
                    break
                sessions.append({'task_id': i, 'title': tasks[i]['title'], 'start': slot[0].isoformat(),
                    'end': slot[1].isoformat(), 'minutes': minutes, 'original_minutes': choices[i]})
                if len(sessions) > 200:
                    failed = True
                    break
                busy.append(slot)
                load[slot[0].date().isoformat()] += minutes
                cursor = slot[1]
                remaining -= minutes
            if failed:
                break
            ends[i] = cursor
        if not failed:
            return sessions, factor
    raise ValueError('Even compressed study sessions cannot fit before the deadlines. Which deadline or daily limit may change?')


def save(planner, state):
    return planner.db.mutate_task_action_state(KEY, lambda old: state if old and old.get('token') == state['token'] else old)


def result(state):
    label = 'Study' if state.get('event_type', 'study') == 'study' else escape(state['event_type'])
    if state.get('status') == 'completed':
        lines = [label + ' 行程已全部重建，前置順序與其他類型活動已保留。']
        if state['factor'] < 1:
            lines.append('已壓縮準備時間（每項至少 10 分鐘）；下列列出原預估與排定時長。')
        lines += [f"{escape(s['title'][:200])}{'…' if len(s['title'])>200 else ''}：{s['start'][:16].replace('T', ' ')}–{s['end'][11:16]}（{s['minutes']} 分鐘；原估 {s['original_minutes']} 分鐘）" for s in state['sessions']]
        from .sequence_replan import question_reply
        reply = question_reply('\n'.join(lines))
        if isinstance(reply,dict):
            for message in reply['messages']:
                message['event_reset_result'] = True
        return reply
    retry = '/studyreset' if state.get('event_type', 'study') == 'study' else '/eventreset ' + state['event_type']
    return (label + ' 重建暫停；已確認刪除 ' + str(len(state.get('deleted', []))) +
            ' 個舊行程、建立 ' + str(len(state.get('inserted', []))) +
            ' 個新時段。請輸入 ' + escape(retry) + ' 恢復核對。\n' + escape(state.get('error', '')))


async def run(planner, chat_id, instruction, update_id, now=None):
    now = now or datetime.now(planner.settings.tz)
    db, cfg = planner.db, planner.settings
    from .calendar_replan import key as review_key
    review = db.get_task_action_state(review_key(chat_id)) or {}
    if review.get('status') == 'creating':
        return '先完成現有 Calendar 操作的恢復核對，再重建 study；本次尚未變更行程。'
    prior = db.get_task_action_state(KEY)
    if prior and prior.get('status') in ACTIVE and prior.get('event_type', 'study') != 'study':
        return '另一類型的重建尚未結束，請先使用 /eventreset ' + escape(prior['event_type']) + ' 恢復核對。'
    token = sha256(f'study-reset:{chat_id}:{update_id}'.encode()).hexdigest()[:32]
    if prior and prior.get('chat_id') != chat_id and prior.get('status') in ACTIVE | {'question'}:
        return 'Another study rebuild needs resolution first.'
    if prior and prior.get('status') == 'completed' and prior.get('token') == token:
        return result(prior)
    if prior and prior.get('status') == 'question' and instruction.strip().lower() in {'cancel', '/cancel', '取消'}:
        db.mutate_task_action_state(KEY, lambda old: {**old, 'status': 'cancelled'}
            if old and old.get('token') == prior['token'] and old.get('status') == 'question' else old)
        return '已取消，行程未變更。'
    lease = uuid4().hex
    deadline = clock.monotonic() + 540
    tick = db.mutate_task_action_state('learning:tick', lambda old: {'claim': lease,
        'until': (now + timedelta(minutes=10)).isoformat()} if not old or old['until'] <= now.isoformat() else old)
    if tick['claim'] != lease:
        return '排程正在處理其他操作，請稍後再試；尚未執行這次重建。'
    try:
        if prior and prior.get('status') in {'applying', 'partial'}:
            state = prior
        else:
            state = {'status': 'planning', 'token': token, 'chat_id': chat_id, 'instruction': instruction, 'event_type': 'study',
                     'updated_at': now.isoformat()}
            db.mutate_task_action_state(KEY, lambda old: state)
            tasks = {t['id']: t for t in db.list_open_tasks()}
            if len(tasks) > 100:
                raise ValueError('More than 100 unfinished tasks; narrow the study scope first.')
            await planner.calendar.verify_owner()
            horizon = now + timedelta(days=30)
            agenda = await planner.calendar.agenda(now, horizon)
            context = prior if prior and prior.get('status') == 'question' and prior.get('event_type','study') == 'study' else None
            output = ResetOutput.model_validate(await planner.ai.parse_study_reset(instruction,
                [{'id': i, **task_fields(t), 'effort': {k: (planner.get(i) or {}).get(k)
                    for k in ('minutes', 'requested_minutes', 'duration_estimate', 'progress_note')}} for i, t in tasks.items()],
                [{'id': e['id'], 'title': e.get('summary', ''), 'start': e.get('start'), 'end': e.get('end')}
                for e in agenda], {'clarification': context, 'constraints': constraints(planner)}, now))
            if not output.actionable or output.question:
                raise ValueError(output.question or '要重建哪段期間的 study？是否也移除過期但未完成的行程？')
            ids = [c.task_id for c in output.tasks]
            if len(ids) != len(set(ids)) or set(ids) != set(tasks):
                raise ValueError('Study task classification is incomplete. Please clarify the study scope.')
            choices = {c.task_id: c.minutes for c in output.tasks if c.is_study}
            for i in choices:
                explicit = (tasks[i].get('timing') or {}).get('estimated_minutes')
                if explicit is not None:
                    choices[i] = max(10, explicit)
            if any(i not in choices and re.search(r'^study\b|\b(?:chapter|ch\.?)\s*\d+|讀書|读书|複習|复习', t['title'], re.I)
                   for i, t in tasks.items()):
                raise ValueError('An explicitly named study unit was excluded. Please clarify the study task group.')
            tasks = {i: tasks[i] for i in choices}
            if not tasks:
                raise ValueError('沒有找到可重排的未完成學習任務。請提供課程或任務名稱。')
            if len(tasks) > 30:
                raise ValueError('At most 30 study tasks can be rebuilt together. Please narrow the period.')
            start = datetime.fromisoformat(output.start) if output.start else now
            stop = datetime.fromisoformat(output.end) if output.end else horizon
            if start.tzinfo is None or stop.tzinfo is None or not now <= start < stop <= horizon:
                raise ValueError('Please choose a study window within the next 30 days.')
            snapshots = {str(i): {'task': task_fields(t), 'state': planner.get(i)} for i, t in tasks.items()}
            removed, historical = [], False
            for i in tasks:
                old = planner.get(i) or {}
                if old.get('status') in {'creating', 'backward_managed', 'reset_pending'} or str(old.get('delivery', '')).endswith(('_uncertain', '_sending')):
                    raise ValueError('A selected task has an unresolved Calendar operation. Resolve it before rebuilding.')
                events = await planner.calendar.task_events(i)
                if old.get('event_id') and old.get('status') in {'scheduled', 'awaiting_feedback'} and old['event_id'] not in {e['id'] for e in events}:
                    raise ValueError('A tracked study session could not be verified. Refresh its state before rebuilding.')
                for event in events:
                    props = event.get('extendedProperties', {}).get('private', {})
                    if props.get('chronosTask') != str(i) or not props.get('chronosToken'):
                        raise ValueError('Study event ownership could not be verified.')
                    a, b = bounds(event, cfg.tz)
                    # Old completed sessions are history, not unfinished work.
                    history = db.get_task_action_state('learning:history:' + props['chronosToken']) or {}
                    if history.get('feedback', {}).get('outcome') == 'done':
                        continue
                    if a < now < b:
                        raise ValueError('A study session is in progress. Finish or stop it before rebuilding.')
                    if b <= now:
                        historical = True
                        if output.include_past is False:
                            continue
                    if not deletable(event, cfg):
                        raise ValueError('A study event is shared, recurring or no longer owned; clarify its treatment first.')
                    removed.append(event)
                    if len(removed) > 100:
                        raise ValueError('More than 100 old study events; narrow the task group before rebuilding.')
            if historical and output.include_past is None:
                raise ValueError('是否也刪除已過期、但尚未確認完成的 study 行程，再將未完成工作排到未來？請回答「包含」或「只排未來」。')
            # Independently preserve obvious same-subject exam deadlines if the model omits an anchor.
            for i, task in tasks.items():
                subject = task.get('project') or re.sub(r'^(?:study|review|prepare|準備|复习|複習)\s*', '', task['title'], flags=re.I).split()[0]
                exams = [e for e in agenda if re.search(r'\b(?:midterm|exam|final)\b|期中|期末|考試|考试', e.get('summary', ''), re.I)
                         and re.search(r'(?<!\w)' + re.escape(subject) + r'(?!\w)', e.get('summary', ''), re.I)]
                if not any(a.task_id == i for a in output.exams):
                    if len(exams) > 1:
                        raise ValueError('同一科目有多場考試；請確認各學習單位應在哪一場考試前完成。')
                    if exams:
                        output.exams.append(ExamAnchor(task_id=i, event_id=exams[0]['id']))
            for anchor in output.exams:
                matches = [e for e in agenda if e['id'] == anchor.event_id]
                event = matches[0] if len(matches) == 1 else None
                if anchor.task_id not in tasks or not event or event['id'] in {e['id'] for e in removed}:
                    raise ValueError('The fixed exam could not be matched uniquely.')
                cutoff = bounds(event, cfg.tz)[0]
                task = dict(tasks[anchor.task_id])
                due = task.get('due_at') or (task.get('timing') or {}).get('due_date')
                if not due or due > cutoff.isoformat():
                    task['due_at'] = cutoff.isoformat()
                tasks[anchor.task_id] = task
            order, edges = ordered(tasks, output.dependencies)
            removed_ids = {e['id'] for e in removed}
            if any(e['id'] not in removed_ids and not e.get('extendedProperties', {}).get('private', {}).get('chronosTask')
                   and re.search(r'^study\b|讀書|读书|複習|复习', e.get('summary', ''), re.I) for e in agenda):
                raise ValueError('有尚未連結任務的 study 行程，請先確認其對應的學習任務，再整批重建。')
            fixed = [bounds(e, cfg.tz) for e in agenda if e.get('transparency') != 'transparent' and
                     not (e['id'] in removed_ids and writable(e, cfg))]
            sessions, factor = schedule(planner, tasks, choices, edges, order, fixed, removed, start, stop, now, output)
            for n, session in enumerate(sessions):
                session['event_id'] = sha256(f"reset:{token}:{n}".encode()).hexdigest()
                session['token'] = sha256(f"reset-token:{token}:{n}".encode()).hexdigest()[:32]
            state = {**state, 'status': 'applying', 'snapshots': snapshots, 'removed': removed,
                     'sessions': sessions, 'factor': factor, 'deleted': [], 'inserted': [],
                     'constraints': constraints(planner),
                     'exams': [e for e in agenda if e['id'] in {a.event_id for a in output.exams}]}
            save(planner, state)
        return await execute(planner, state, now, lease, deadline)
    except (ValueError, AIError) as error:
        state = db.get_task_action_state(KEY) or {}
        if state.get('status') in {'applying', 'partial'}:
            state = {**state, 'status': 'partial', 'error': str(error)}
            save(planner, state)
            return result(state)
        if state.get('status') == 'planning':
            save(planner, {**state, 'status': 'question', 'question': str(error),
                           'previous': prior.get('instruction') if prior else None})
        return escape(str(error)) + '\n尚未刪除或建立行程。'
    except CalendarError as error:
        state = db.get_task_action_state(KEY) or {}
        if state.get('status') == 'planning':
            save(planner, {**state, 'status': 'question', 'question': str(error)})
            return 'Calendar 暫時無法核對，尚未變更行程。'
        state = {**state, 'status': 'partial', 'error': str(error)}
        save(planner, state)
        return result(state)
    finally:
        db.mutate_task_action_state('learning:tick', lambda old: {**old, 'until': now.isoformat()}
            if old and old.get('claim') == lease else old)


async def execute(planner, state, now, lease, deadline):
    """Persist progress after readback; retry always checks deterministic identities."""
    db, cfg = planner.db, planner.settings
    await planner.calendar.verify_owner()

    def current():
        tick = db.get_task_action_state('learning:tick') or {}
        if tick.get('claim') != lease or clock.monotonic() >= deadline:
            raise CalendarError('The rebuild lease changed or expired; stop and reconcile.')
        if constraints(planner) != state['constraints']:
            raise CalendarError('Scheduling hours, capacity or preferences changed; inspect the saved rebuild.')
        for i, original in state['snapshots'].items():
            if original.get('event'):
                booking = original.get('booking')
                if booking and db.get_task_action_state('free:booking:' + booking['token']) != booking:
                    raise CalendarError('Another operation changed an event booking; rebuilding paused.')
                continue
            task = db.get_task(int(i))
            if not task or task.get('status') != 'open' or task_fields(task) != original['task']:
                raise CalendarError('A selected task changed during rebuilding; inspect the saved plan.')
            saved = planner.get(int(i))
            if saved != original['state'] and not (saved and saved.get('reset_token') == state['token']):
                raise CalendarError('Another operation changed a selected plan; rebuilding paused.')

    current()
    from .learning_calendar import overlaps
    from .backward_planning import reserved_windows
    # Refresh every replacement conflict before any deletion; old selected events are the only exclusions.
    selected = {e['id'] for e in state['removed']}
    sessions = state['sessions']
    agenda = await planner.calendar.agenda(now, max(datetime.fromisoformat(s['end']) for s in [*sessions,
        *({'end': bounds(e, cfg.tz)[1].isoformat()} for e in state['exams'])]))
    busy = [bounds(e, cfg.tz) for e in agenda if e.get('transparency') != 'transparent' and
        not ((e['id'] in selected or e['id'] in state['inserted'] or
              e['id'] in {s['event_id'] for s in sessions}) and
             writable(e, cfg))]
    busy += reserved_windows(db)
    if any(overlaps(datetime.fromisoformat(s['start']), datetime.fromisoformat(s['end']), busy) for s in sessions):
        raise CalendarError('A replacement slot became busy. Rebuilding paused before further changes.')
    for exam in state['exams']:
        actual = next((e for e in agenda if e['id'] == exam['id'] and
            (e.get('_calendar_id') == exam.get('_calendar_id') or writable(e,cfg) and writable(exam,cfg))), None)
        if not actual or {k:v for k,v in actual.items() if k!='_calendar_id'} != {k:v for k,v in exam.items() if k!='_calendar_id'}:
            raise CalendarError('A fixed event changed. Inspect its timing before rebuilding.')
    for session in sessions:
        actual = await planner.calendar.event(session['event_id'])
        if actual is not None and not matches_session(actual, session, state, cfg):
            raise CalendarError('A replacement event changed or has a conflicting identity. Rebuilding paused.')
    from .learning_calendar import class_windows
    days = {s['start'][:10] for s in sessions if not state['snapshots'][str(s['task_id'])].get('event')}
    for day in days:
        bucket = dict(db.get_task_action_state('learning:day:' + day) or {})
        for event in state['removed']:
            bucket.pop(event.get('extendedProperties', {}).get('private', {}).get('chronosToken'), None)
        for s in sessions:
            bucket.pop(s['token'], None)
        load = sum(bucket.values()) + sum(s['minutes'] for s in sessions if s['start'][:10] == day
            and not state['snapshots'][str(s['task_id'])].get('event'))
        load += sum((b-a).total_seconds()/60 for a,b in class_windows(datetime.fromisoformat(day).date(), cfg.tz,
            state['constraints']['course_policy']))
        load += sum((b-a).total_seconds()/60 for a,b in reserved_windows(db) if a.date().isoformat() == day)
        for task in db.list_open_tasks():
            saved = planner.get(task['id']) or {}
            if str(task['id']) not in state['snapshots'] and saved.get('status') in {'queued','planning','proposed','editing','creating'}:
                if saved.get('start', '')[:10] == day and saved.get('end'):
                    load += (datetime.fromisoformat(saved['end']) - datetime.fromisoformat(saved['start'])).total_seconds()/60
        if load > cfg.learning_daily_minutes:
            raise CalendarError('The daily task capacity changed. Rebuilding paused before further changes.')
    # Check the complete old set before the first deletion, including versions.
    for event in state['removed']:
        actual = await planner.calendar.event(event['id'])
        if actual and actual.get('status') != 'cancelled' and actual.get('etag') != event['etag']:
            raise CalendarError('An old selected event changed. Deletion paused.')
    for event in state['removed']:
        if event['id'] in state['deleted']:
            continue
        current()
        actual = await planner.calendar.event(event['id'])
        if actual and actual.get('status') != 'cancelled':
            if actual.get('etag') != event['etag']:
                raise CalendarError('An old selected event changed. Deletion paused.')
            await planner.calendar.delete_event(event['id'], event['etag'])
            actual = await planner.calendar.event(event['id'])
            if actual and actual.get('status') != 'cancelled':
                raise CalendarError('Deletion was not confirmed; rebuilding paused.')
        state['deleted'].append(event['id'])
        save(planner, state)
    for session in state['sessions']:
        if session['event_id'] in state['inserted']:
            continue
        current()
        actual = await planner.calendar.event(session['event_id'])
        if actual is None:
            a, b = (datetime.fromisoformat(session[k]) for k in ('start', 'end'))
            if a <= now:
                raise CalendarError('A replacement slot has passed. Inspect the saved rebuild before continuing.')
            if overlaps(a, b, [*await planner.calendar.busy(a, b), *reserved_windows(db)]):
                raise CalendarError('A replacement slot is now busy. Existing progress was retained.')
            body = {'id': session['event_id'], 'summary': session['title'], 'colorId': '10',
                'start': {'dateTime': session['start'], 'timeZone': cfg.timezone},
                'end': {'dateTime': session['end'], 'timeZone': cfg.timezone},
                'description': 'Chronos ' + state.get('event_type','study') + ' rebuild under owner authorization.',
                'visibility': 'private', 'transparency': 'opaque',
                'extendedProperties': {'private': {'chronosTask': str(session['task_id']),
                    'chronosToken': session['token'], 'chronosReset': state['token']}}}
            original = state['snapshots'][str(session['task_id'])]
            if original.get('event') or session.get('template'):
                source = original.get('event') or session['template']
                body = {**{k: source[k] for k in ('summary','description','location','colorId','visibility','transparency','reminders','extendedProperties') if k in source},
                        **{k: body[k] for k in ('id','start','end')}}
                body['summary'] = session['title']
                props = dict(source.get('extendedProperties', {}).get('private', {}))
                props.update(chronosReset=state['token'], chronosEventCategory=state['event_type'])
                if not original.get('event'):
                    props.update(chronosTask=str(session['task_id']),chronosToken=session['token'])
                body['extendedProperties'] = {**source.get('extendedProperties', {}), 'private': props}
            elif state.get('event_type', 'study') != 'study':
                from .free_booking import EVENT_COLORS
                body['colorId'] = EVENT_COLORS.get(state['event_type'],'8')
                body['extendedProperties']['private']['chronosEventCategory'] = state['event_type']
            await planner.calendar.insert(body)
            actual = await planner.calendar.event(session['event_id'])
        if not matches_session(actual, session, state, cfg):
            raise CalendarError('A replacement event could not be verified; rebuilding paused.')
        state['inserted'].append(session['event_id'])
        save(planner, state)
    def finish():
        current()
        buckets = {}
        replacements = {}
        def bucket(day):
            if day not in buckets:
                buckets[day] = dict(db.get_task_action_state('learning:day:' + day) or {})
            return buckets[day]
        for event in state['removed']:
            bucket(bounds(event, cfg.tz)[0].date().isoformat()).pop(
                event.get('extendedProperties', {}).get('private', {}).get('chronosToken'), None)
        queue = db.get_task_action_state('learning:proposal-queue') or {}
        booking_updates = {}
        for i, original in state['snapshots'].items():
            sessions = [s for s in state['sessions'] if str(s['task_id']) == i]
            first, *later = sessions
            if original.get('event'):
                booking = original.get('booking')
                if booking:
                    booking_updates[booking['token']] = {**booking, 'event_id': first['event_id'],
                        'start': first['start'], 'end': first['end'], 'reset_token': state['token']}
                continue
            old = original['state'] or {}
            replacement = {**first, 'status': 'scheduled', 'snapshot': original['task'],
                'reset_token': state['token'], 'reset_remaining_sessions': later,
                'history': old.get('history', []), 'auto_accepted': True}
            replacements[i] = replacement
            for session in sessions:
                bucket(session['start'][:10])[session['token']] = session['minutes']
            queue['retire_message_ids'] = list(dict.fromkeys([*queue.get('retire_message_ids', []),
                *(old[k] for k in ('message_id', 'edit_message_id') if old.get(k))]))
        selected = {int(i) for i in state['snapshots']}
        if queue.get('active_task_id') in selected:
            queue['active_task_id'] = None
        queue['requested_task_ids'] = [i for i in queue.get('requested_task_ids', []) if i not in selected]
        # Read all records before writes for the strict Firestore transaction contract.
        for i, replacement in replacements.items():
            db._save_task_action_state('study-reset:history:' + state['token'] + ':' + i, state['snapshots'][i])
            db._save_task_action_state('learning:task:' + i, replacement)
        for i, original in state['snapshots'].items():
            if original.get('event'):
                db._save_task_action_state('event-reset:history:' + state['token'] + ':' + i, original)
        for token, booking in booking_updates.items():
            db._save_task_action_state('free:booking:' + token, booking)
        for day, values in buckets.items():
            db._save_task_action_state('learning:day:' + day, values)
        db._save_task_action_state('learning:proposal-queue', queue)
        db._save_task_action_state(KEY, {**state, 'status': 'completed'})
    db._run_task_action(finish)
    return result({**state, 'status': 'completed'})


def matches_session(actual, session, state, cfg):
    props = (actual or {}).get('extendedProperties', {}).get('private', {})
    original = state.get('snapshots', {}).get(str(session['task_id']), {})
    if original.get('event') or session.get('template'):
        source = original.get('event') or session['template']
        kept = {k: source[k] for k in ('description','location','colorId','visibility','transparency','reminders') if k in source}
        expected = dict(source.get('extendedProperties',{}).get('private',{}))
        expected.update(chronosReset=state['token'],chronosEventCategory=state['event_type'])
        if not original.get('event'):
            expected.update(chronosTask=str(session['task_id']),chronosToken=session['token'])
        return bool(actual and actual.get('status') != 'cancelled' and props.get('chronosReset') == state['token']
            and props.get('chronosEventCategory') == state['event_type'] and actual.get('summary') == session['title']
            and all(actual.get(k) == v for k,v in kept.items())
            and all(props.get(k) == v for k,v in expected.items())
            and actual.get('extendedProperties',{}).get('shared') == source.get('extendedProperties',{}).get('shared')
            and tuple(v.isoformat() for v in bounds(actual, cfg.tz)) == (session['start'], session['end']))
    return bool(actual and actual.get('status') != 'cancelled' and props.get('chronosReset') == state['token']
        and props.get('chronosTask') == str(session['task_id']) and props.get('chronosToken') == session['token']
        and actual.get('summary') == session['title']
        and tuple(v.isoformat() for v in bounds(actual, cfg.tz)) == (session['start'], session['end']))


async def advance_sessions(planner, task, state, now):
    """Advance booked segments without inferring progress or completion from elapsed time."""
    later = state.get('reset_remaining_sessions', [])
    if not later or state.get('status') != 'scheduled' or state['end'] > now.isoformat():
        return state
    event = await planner.calendar.event(state['event_id'])
    operation = {'token': state['reset_token']}
    if task_fields(task) != state['snapshot'] or not matches_session(event, state, operation, planner.settings):
        raise CalendarError('A rebuilt study segment changed; inspect the plan before advancing.')
    first, *remaining = later
    next_event = await planner.calendar.event(first['event_id'])
    if not matches_session(next_event, first, operation, planner.settings):
        raise CalendarError('The next rebuilt study segment changed; inspect the plan before advancing.')
    def move(old):
        if old != state:
            return old
        return {**old, **first, 'reset_remaining_sessions': remaining,
                'elapsed_segments': [*old.get('elapsed_segments', []),
                    {k: old[k] for k in ('event_id', 'token', 'start', 'end', 'minutes')} ]}
    return planner.update(task['id'], move)
