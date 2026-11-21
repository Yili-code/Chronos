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
    from .study_constraints import literal_window,chapter_refs,minimum,rest_minutes
    try:
        owner_window=literal_window(instruction,now)
    except ValueError as error:
        return str(error)
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
    if not output.actionable or not (output.day or owner_window) or not output.items:
        return escape(output.question or 'Which study day and chapters should I revise? Nothing changed.')
    try:
        day = owner_window[0].date() if owner_window else datetime.strptime(output.day, '%Y-%m-%d').date()
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
    try:
        db_chapters=chapter_refs(instruction,tasks)
    except ValueError as error:
        return str(error)
    literal_min=minimum(instruction)
    if db_chapters:
        if literal_min is None and db_chapters-set(minimums):
            return '請補充每個 DB 章節至少需要多久，這次沒有變更行程。'
        for i in db_chapters:
            minimums[i]=max(minimums.get(i,0),literal_min or 0)
    exact={}
    sheet=re.search(r'(\d+)\s*(?:分鐘|分钟|minutes?)\s*(?:的)?\s*(?:DB|資料庫).{0,12}(?:考古|大抄|past|cheat)',instruction,re.I)
    if sheet:
        candidates=[i for i in minimums if i not in db_chapters and re.search(r'考古|大抄|past|cheat|midterm|exam',tasks[i]['title'],re.I)]
        if len(candidates)!=1:
            return '90 分鐘的 DB 考古準備對應哪一項任務？請補充名稱，避免漏排或排錯。'
        exact[candidates[0]]=int(sheet[1]);minimums[candidates[0]]=int(sheet[1])
    if any(not 10<=m<=180 for m in minimums.values()):return '每項工作需為 10 至 180 分鐘，請調整時長。'
    if not owner_window and (output.window_start or output.window_end):
        try:
            a,b=(datetime.fromisoformat(v) for v in (output.window_start,output.window_end))
            if a.utcoffset() is None or b.utcoffset() is None or not now<=a<b or a.date()!=day or b-a>timedelta(days=1):raise ValueError
            owner_window=(a.astimezone(cfg.tz),b.astimezone(cfg.tz))
        except (ValueError,TypeError):return '請確認安排的開始與結束時間，這次沒有變更行程。'
    try:
        breaks=rest_minutes(instruction)
    except ValueError as error:
        return str(error)
    if not 0<=breaks<=60:return '請指定 0 至 60 分鐘的休息長度。'
    begin,end=owner_window or day_bounds(day,cfg)
    allocation_cfg=cfg.model_copy(update={'learning_start_hour':0,'learning_end_hour':24}) if owner_window else cfg

    await planner.calendar.verify_owner()
    events = await planner.calendar.agenda(min(now,day_bounds(day,cfg)[0]),datetime.combine(day,datetime.min.time(),cfg.tz)+timedelta(days=1)) if owner_window else await planner.calendar.agenda(min(now, begin), end)
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
        relevant = linked is not None and a.date() == day and b > now and ((linked in minimums) if owner_window else (not keep_others or linked in minimums))
        if relevant:
            if (state.get('status') != 'scheduled' or not movable(event, cfg, now, task_id=linked, study_state=state)
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
    if owner_window:
        from .nightly_planning import class_blocks
        fixed+=class_blocks(day,cfg,policy)
    moves, pending = [], []
    # Numeric title order keeps Chapter 1/2 before Chapter 10, within deadline priority.
    def ordering(i):
        return (tasks[i].get('due_at') or (tasks[i].get('timing') or {}).get('due_date') or '9999',
                re.sub(r'\d+', lambda m: m[0].zfill(8), tasks[i]['title'].casefold()), i)
    ordered=sorted(target_ids,key=lambda i:((0 if i in db_chapters else 1),ordering(i))) if owner_window and db_chapters else sorted(target_ids,key=ordering)
    required_minutes={i:exact.get(i,max(int((selected[i][2]-selected[i][1]).total_seconds()/60) if i in selected else (states[i] or {}).get('minutes',0),minimums.get(i,0))) for i in ordered}
    if owner_window:
        available=int((end-max(now,begin)).total_seconds()//60)
        work=sum(required_minutes.values());rest=breaks*max(0,len(ordered)-1)
        from .learning_calendar import class_windows
        previous_load=budgets[day.isoformat()]+sum(int((b-a).total_seconds()/60) for a,b in class_windows(day,cfg.tz,policy))
        if work+rest>available or previous_load+work>cfg.learning_daily_minutes:
            shorter=max(0,(available-work)//max(1,len(ordered)-1))
            option=f'每次休息最多約 {shorter} 分鐘才可能放得下，仍需核對行事曆。' if shorter>0 else '至少需要將一項工作移到別的時段，或延後截止時間。'
            capacity=f'指定範圍有 {available} 分鐘；工作至少 {work} 分鐘，加上每次 {breaks} 分鐘休息共 {rest} 分鐘，合計 {work+rest} 分鐘。'
            if work+rest>available:capacity+=f' 時間差 {work+rest-available} 分鐘。'
            if previous_load+work>cfg.learning_daily_minutes:
                capacity+=f'\n今天已有課程與已排學習 {previous_load} 分鐘，加上本次工作後是 {previous_load+work} 分鐘，超過目前每日上限 {cfg.learning_daily_minutes} 分鐘。'
                option='只調整休息仍不足以解決每日上限，請指定哪項安排可移到別天，或明確調整上限。'
            return capacity+'\n'+option+' 要調整休息、任務或截止時間？這次沒有變更行程。'
    cursor=begin
    rest_slots=[]
    for index,i in enumerate(ordered):
        old = selected.get(i)
        original_minutes = int((old[2] - old[1]).total_seconds()/60) if old else (states[i] or {}).get('minutes', 0)
        minutes = required_minutes[i]
        gap=breaks if index<len(ordered)-1 else 0
        search_cfg=allocation_cfg.model_copy(update={'learning_daily_minutes':allocation_cfg.learning_daily_minutes+gap})
        block=find_slot(tasks[i],now,fixed,[],search_cfg,minutes+gap,budgets,policy,
            window=(cursor if owner_window or breaks else begin,end),align_minutes=not bool(owner_window),lead_minutes=0 if owner_window else 10)
        slot=(block[0],block[0]+timedelta(minutes=minutes)) if block else None
        if not slot and owner_window:
            return f"{escape(tasks[i]['title'])} 需要 {minutes} 分鐘，但無法在 {begin:%H:%M}–{end:%H:%M} 內同時避開行程、課前準備、期限與每日上限。要將哪項工作移到別的時段，或調整截止時間？這次沒有變更行程。"
        if not slot:
            return (f"The revised plan needs at least {minutes} minutes for {escape(tasks[i]['title'])}, "
                    'but it does not fit the remaining time, deadline or daily limit. '
                    'Which task may move to another day? Nothing changed.')
        fixed.append(block)
        if gap:rest_slots.append({'start':slot[1].isoformat(),'end':block[1].isoformat(),'title':'短休息'})
        if owner_window or breaks:cursor=block[1]
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
             'owner_window': {'start':begin.isoformat(),'end':end.isoformat()} if owner_window else None,
             'revision_breaks':rest_slots,'break_minutes':breaks,
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
        active_state=planner.get(queue['active_task_id']) if state.get('owner_window') and queue.get('active_task_id') else None
        old_states = {item['task_id']: planner.get(item['task_id']) for item in state['revision_additions']}
        hold_index=db.get_task_action_state('planning:holds') or {}
        for item in state['revision_additions']:
            old = old_states[item['task_id']]
            if old and old.get('token'):
                db._save_task_action_state('learning:history:' + old['token'], old)
            db._save_task_action_state('learning:task:' + str(item['task_id']), {
                'status': 'queued', 'token': item['token'], 'snapshot': item['task_snapshot'],
                'requested_minutes': item['minutes'], 'duration_source': 'nightly_ai',
                'nightly_slot': {k: item[k] for k in ('start', 'end')},
                'duration_estimate': {'estimated_minutes': item['minutes']},
                **({'owner_window':state['owner_window'],'protected_breaks':state.get('revision_breaks',[])} if state.get('owner_window') else {}),
                'progress_note': 'Reported unfinished; completion and time spent are unknown.'})
        ids = [item['task_id'] for item in state['revision_additions']]
        requested=list(dict.fromkeys([*ids,*queue.get('requested_task_ids',[])] if state.get('owner_window') else [*queue.get('requested_task_ids',[]),*ids]))
        if state.get('owner_window') and ids and (active_state or {}).get('status')!='creating':queue={**queue,'active_task_id':ids[0]}
        db._save_task_action_state('learning:proposal-queue', {**queue, 'requested_task_ids': requested})
        if state.get('revision_breaks'):
            kept=[s for s in hold_index.get('slots',[]) if s['end']>datetime.now(planner.settings.tz).isoformat() and s.get('token')!=state['token']]
            db._save_task_action_state('planning:holds',{'slots':kept+[{**s,'token':state['token'],'chat_id':state['chat_id']} for s in state['revision_breaks']]})
        db._save_task_action_state(key(state['chat_id']), {**review, 'revision_queued': True})
    db._run_task_action(save)
