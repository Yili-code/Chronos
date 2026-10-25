"""Deadline decomposition using existing task transactions and Calendar transport.

Drafts never create tasks/events. Approved plans are a resumable saga: reserve
capacity, atomically create each linked task, reconcile deterministic event IDs.
"""
import math
import re
from datetime import datetime, timedelta, time, date
from hashlib import sha256
from html import escape
from uuid import uuid4
from zoneinfo import ZoneInfo

from .ai import BackwardOutput
from .google_calendar import CalendarError
from .learning_calendar import class_windows, short_task_windows, overlaps
from .task_actions import task_fields
from .task_timing import display_time

TZ = ZoneInfo("Asia/Taipei")
INDEX = "backward:reservations"


def key(token):
    return "backward:plan:" + token


def reserved_windows(db):
    return [(datetime.fromisoformat(s['start']), datetime.fromisoformat(s['end']))
            for s in (db.get_task_action_state(INDEX) or {}).get('slots', [])]


def deadline_boundary(value):
    if len(value) == 10:
        # Conservative planning boundary, NOT an assertion of submission time.
        return datetime.combine(date.fromisoformat(value), time.min, TZ)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('Deadline time must include a timezone.')
    return parsed.astimezone(TZ)


def explicit_deadline(text, now):
    """Resolve common explicit relative dates without delegating week arithmetic."""
    matches = list(re.finditer(r'(\d{4}-\d{2}-\d{2})|(今天|明天|後天)|(下|這|本)(?:週|周|星期)([一二三四五六日天])', text))
    if len(matches) != 1:
        return None  # Multiple dates need the AI role classifier / clarification.
    match = matches[0]
    if match[1]:
        day = date.fromisoformat(match[1])
    elif match[2]:
        day = now.date()+timedelta(days={'今天': 0, '明天': 1, '後天': 2}[match[2]])
    else:
        weekday = '一二三四五六日'.index(match[4].replace('天', '日'))
        day = now.date()-timedelta(days=now.weekday())+timedelta(days=weekday+(7 if match[3] == '下' else 0))
    # Only the immediately adjacent HH:MM supplies an exact deadline time.
    clock = re.match(r'\s*(\d{1,2}):(\d{2})(?!\d)', text[match.end():])
    if clock:
        # This shortcut only resolves unqualified local 24-hour clocks. Keep
        # the structured parser's result when meridiem or zone conversion is
        # needed, rather than silently treating 5 PM or 17:00 UTC as Taipei.
        suffix = text[match.end() + clock.end():]
        if (re.search(r'(?<![a-z])(?:[ap]\.?m\.?|utc|gmt|[ecmp][sd]t|[a-z_]+/[a-z_]+)(?![a-z])', text, re.I)
                or re.match(r'\s*[+-]\d{1,2}(?::?\d{2})?(?!\d)', suffix)):
            return None
        return datetime.combine(day, time(int(clock[1]), int(clock[2])), TZ).isoformat()
    if re.search(r'\d{1,2}:\d{2}|\d+\s*點|上午|下午|晚上|AM|PM', text, re.I):
        return None
    return day.isoformat()


def ordered_steps(steps):
    remaining, ordered = set(range(len(steps))), []
    while remaining:
        ready = sorted(i for i in remaining if set(steps[i]['depends_on']) <= set(ordered))
        if not ready:
            raise ValueError('Step dependencies contain a cycle or an invalid reference.')
        for i in ready:
            if i in steps[i]['depends_on']:
                raise ValueError('A step cannot depend on itself.')
            ordered.append(i)
            remaining.remove(i)
    return ordered


def verified_event(event, event_id, state, slot):
    title = ('Buffer · ' + state['goal']) if slot['step'] == -1 else state['steps'][slot['step']]['title']
    return bool(event and event.get('id') == event_id and event.get('status') != 'cancelled'
                and event.get('extendedProperties', {}).get('private', {}).get('chronosBackward') == state['token']
                and event.get('summary') == title
                and event.get('start', {}).get('dateTime') and event.get('end', {}).get('dateTime')
                and datetime.fromisoformat(event['start']['dateTime']) == datetime.fromisoformat(slot['start'])
                and datetime.fromisoformat(event['end']['dateTime']) == datetime.fromisoformat(slot['end']))


def allocate(steps, now, boundary, busy, settings, policy, budgets, daily_cap):
    """Latest-first serial topological schedule in 15-minute working units.

    Serializing independent steps is deliberately conservative for one person.
    Fragmented steps may span days. A final reserved buffer consumes real capacity.
    """
    order = ordered_steps(steps)
    total = sum(math.ceil(s['minutes'] / 15) * 15 for s in steps)
    buffer = max(30, math.ceil(total * .2 / 15) * 15)
    jobs = [(i, math.ceil(steps[i]['minutes'] / 15)) for i in order] + [(-1, buffer // 15)]
    units, loads = [], dict(budgets)
    cursor = boundary.replace(second=0, microsecond=0)
    cursor -= timedelta(minutes=cursor.minute % 15)
    earliest = now + timedelta(minutes=10)
    missing = 0
    for step, count in reversed(jobs):
        for _ in range(count):
            found = False
            while cursor - timedelta(minutes=15) >= earliest:
                end, start = cursor, cursor - timedelta(minutes=15)
                cursor = start
                day = start.date()
                classes = class_windows(day, TZ, policy)
                opening = datetime.combine(day, time(settings.learning_start_hour), TZ)
                closing = datetime.combine(day, time(settings.learning_end_hour % 24), TZ)
                if settings.learning_end_hour == 24:
                    closing += timedelta(days=1)
                load = loads.get(day.isoformat(), 0) + sum((b-a).total_seconds()/60 for a,b in classes)
                if (start < opening or end > closing or load + 15 > daily_cap
                        or overlaps(start, end, [*busy, *classes, *short_task_windows(day, TZ, 15)])):
                    continue
                units.append({'step': step, 'start': start.isoformat(), 'end': end.isoformat()})
                loads[day.isoformat()] = loads.get(day.isoformat(), 0) + 15
                found = True
                break
            if not found:
                missing += 15
    slots = []
    for unit in reversed(units):
        if (slots and slots[-1]['step'] == unit['step'] and slots[-1]['end'] == unit['start']
                and (datetime.fromisoformat(unit['end'])-datetime.fromisoformat(slots[-1]['start'])).total_seconds() <= 10800):
            slots[-1]['end'] = unit['end']
        else:
            slots.append(dict(unit))
    return slots, buffer, missing


class BackwardPlanner:
    def __init__(self, db, calendar, ai, settings):
        self.db, self.calendar, self.ai, self.settings = db, calendar, ai, settings

    def session(self, chat):
        return self.db.get_task_action_state(f'backward:chat:{chat}') or {}

    def bind(self, chat, message_id, message):
        token = message.get('backward_plan')
        if token:
            self.db.mutate_task_action_state(key(token), lambda old: {**old, 'message_id': message_id}
                if old and old['chat_id'] == chat else old)

    def progress(self, token, chat):
        state = self.db.get_task_action_state(key(token))
        if not state or state['chat_id'] != chat:
            return 'No matching plan. Use /backward #<task-id>.'
        lines = [f"Backward plan · {state['goal']}", f"Status: {state['status']}"]
        completed = 0
        for i, step in enumerate(state['steps']):
            task_id = state.get('children', {}).get(str(i))
            task = self.db.get_task(task_id) if task_id else None
            done = bool(task and task['status'] == 'done')
            completed += int(done)
            lines.append(f"{'Done' if done else 'Open' if task else 'Not created'} · #{task_id or '—'} {step['title']}")
            slots = [(n, s) for n, s in enumerate(state.get('slots', [])) if s['step'] == i]
            if slots:
                lines.append(f"  Blocks recorded: {sum(str(n) in state.get('events', {}) for n, _ in slots)}/{len(slots)}")
        lines.append(f"Progress: {completed}/{len(state['steps'])}. Goal #{state.get('parent_id', '—')} stays separate.")
        lines.append('Complete steps with /done using the current /tasks list position. Goal completion is explicit.')
        return '\n'.join(lines)

    async def preview(self, chat, instruction, update_id, now=None):
        now = (now or datetime.now(TZ)).astimezone(TZ)
        if not self.settings.enable_learning_calendar:
            return 'Task planning is unavailable. Enable the existing learning Calendar connection first.'
        session = self.session(chat)
        old = self.db.get_task_action_state(key(session['token'])) if session.get('token') else None
        if old and old['status'] in {'creating', 'partial'}:
            return 'An approved plan needs recovery. Tap Confirm schedule on its card before starting another plan.'
        token = sha256(f'backward:{chat}:{update_id}'.encode()).hexdigest()[:32]
        saved = self.db.get_task_action_state(key(token))
        if saved:
            return self.render(saved)
        existing = None
        reference = re.match(r'^#(\d+)(?:\s+|$)', instruction)
        if reference:
            existing = self.db.get_task(int(reference[1]))
            if not existing or existing['status'] != 'open':
                return 'That goal is not open. Use /tasks and its stable #ID.'
            linked = self.db.get_task_action_state(f"backward:goal:{existing['id']}")
            if linked:
                return self.progress(linked['token'], chat) + '\nExisting work is preserved; automatic replanning is not supported.'
            state = self.db.get_task_action_state(f"learning:task:{existing['id']}") or {}
            if state.get('status') in {'creating', 'scheduled', 'awaiting_feedback'}:
                return 'This goal already has scheduled work. Review /plan first; existing bookings will not be replaced.'
        # Resume clarification/edit context without creating a second goal.
        if old and old['status'] in {'clarifying', 'editing'}:
            instruction = old['instruction'] + '\nAdditional user information: ' + instruction
            existing = self.db.get_task(old['parent_id']) if old.get('parent_id') else existing
        candidates = self.db.list_open_tasks()
        evidence = None
        if existing:
            learning = self.db.get_task_action_state(f"learning:task:{existing['id']}") or {}
            evidence = {**existing, 'recorded_progress': {k: learning[k] for k in ('progress', 'progress_note', 'feedback') if k in learning}}
        output = BackwardOutput.model_validate(await self.ai.backward_plan(instruction, evidence, now,
            [{'id': t['id'], **task_fields(t)} for t in candidates]))
        if not existing and output.existing_task_id is not None:
            existing = next((t for t in candidates if t['id'] == output.existing_task_id), None)
            if existing is None:
                raise ValueError('The selected goal is not open.')
        if not existing:
            matches = [t for t in candidates if t['title'].casefold().strip() == output.goal.casefold().strip()]
            if len(matches) == 1:
                existing = matches[0]
            elif matches:
                return 'Several goals match. Select ' + ', '.join(f"/backward #{t['id']}" for t in matches)
        if existing:
            linked = self.db.get_task_action_state(f"backward:goal:{existing['id']}")
            if linked:
                return self.progress(linked['token'], chat) + '\nExisting work is preserved; automatic replanning is not supported.'
            learning = self.db.get_task_action_state(f"learning:task:{existing['id']}") or {}
            if learning.get('status') in {'creating', 'scheduled', 'awaiting_feedback'}:
                return 'This goal already has scheduled work. Review /plan first; existing bookings will not be replaced.'
        deadline = output.deadline
        if not existing:
            deadline = explicit_deadline(instruction, now) or deadline
        if existing and reference and not instruction[reference.end():].strip():
            deadline = existing.get('due_at') or (existing.get('timing') or {}).get('due_date')
        steps = [s.model_dump() for s in output.steps]
        state = {'token': token, 'chat_id': chat, 'goal': existing['title'] if existing else output.goal,
                 'instruction': instruction[:4000], 'deadline': deadline, 'steps': steps,
                 'parent_id': existing['id'] if existing else None, 'snapshot': task_fields(existing) if existing else None,
                 'assumptions': output.assumptions, 'progress': output.progress, 'status': 'draft',
                 'daily_cap': min(output.daily_minutes or self.settings.learning_daily_minutes, self.settings.learning_daily_minutes),
                 'expires_at': (now+timedelta(hours=2)).isoformat(), 'children': {}, 'events': {}}
        question = output.question or ('What is the deadline (date or exact time)?' if not deadline else None)
        if not steps and not question:
            question = 'What result do you need to deliver?'
        if question:
            state.update(status='clarifying', question=question)
        else:
            boundary = deadline_boundary(deadline)
            if boundary <= now:
                return 'The planning deadline has passed. Supply a future date/time; nothing created.'
            if boundary > now+timedelta(days=60):
                return 'Planning supports the next 60 days. Choose a nearer milestone first; nothing created.'
            ordered_steps(steps)
            await self.calendar.verify_owner()
            busy = await self.calendar.busy(now, boundary)
            policy = self.db.get_task_action_state('learning:course-calendar-policy')
            budgets = self.budgets(now, boundary)
            slots, buffer, missing = allocate(steps, now, boundary, [*busy, *reserved_windows(self.db)],
                self.settings, policy, budgets, state['daily_cap'])
            state.update(slots=slots, buffer=buffer, missing=missing,
                         status='insufficient' if missing else 'draft', policy=policy)
        def save():
            previous = self.session(chat)
            previous_state = self.db.get_task_action_state(key(previous['token'])) if previous.get('token') else None
            if previous_state and previous_state['status'] in {'creating', 'partial'}:
                return False
            if previous_state and previous_state['status'] in {'draft', 'insufficient', 'editing', 'clarifying'}:
                self.db._save_task_action_state(key(previous['token']), {**previous_state, 'status': 'superseded'})
            self.db._save_task_action_state(key(token), state)
            self.db._save_task_action_state(f'backward:chat:{chat}', {'token': token})
            return True
        return self.render(state) if self.db._run_task_action(save) else 'Another plan is being created. Retry its confirmation.'

    def budgets(self, now, boundary):
        return {(now.date()+timedelta(days=i)).isoformat(): sum((self.db.get_task_action_state(
            'learning:day:'+(now.date()+timedelta(days=i)).isoformat()) or {}).values())
            for i in range((boundary.date()-now.date()).days+1)}

    def render(self, state):
        lines = [f"<b>Backward plan</b> · {escape(state['goal'])}"]
        if state['status'] == 'clarifying':
            lines += [escape(state['question']), 'Reply here, or send the missing information. /cancel discards this draft.']
        else:
            lines += [f"Deadline · {escape(state['deadline'])} · Asia/Taipei"]
            if len(state['deadline']) == 10:
                lines += ['Date only: submission time unknown. Work finishes by the previous day; this does not assert a due time.']
            lines += [f"User input · {escape(state['instruction'][:500])}",
                      f"Reported progress · {escape(state['progress'])}",
                      f"AI estimated work · {sum(math.ceil(s['minutes']/15)*15 for s in state['steps'])} min + {state['buffer']} min reserved buffer",
                      f"Hours · {self.settings.learning_start_hour:02d}:00–{self.settings.learning_end_hour:02d}:00; daily cap {state['daily_cap']} min including classes and approved study; meals excluded."]
            if state.get('parent_id'):
                previous = state['snapshot'].get('due_at') or state['snapshot'].get('timing', {}).get('due_date')
                lines += [f"Existing goal #{state['parent_id']} · recorded deadline {escape(previous or 'none')}"]
                if previous != state['deadline']:
                    lines += ['Confirmation also updates this goal deadline to the previewed deadline.']
            for i, step in enumerate(state['steps']):
                dependencies = ', '.join(str(d+1) for d in step['depends_on']) or 'none'
                lines += [f"\n{i+1}. {escape(step['title'])} · ~{math.ceil(step['minutes']/15)*15} min · after {dependencies}",
                          f"Output: {escape(step['output'])}"]
                if not state['missing']:
                    for slot in state['slots']:
                        if slot['step'] == i:
                            lines += [self.slot_text(slot)]
            if state['missing']:
                lines += [f"\nInsufficient capacity · short by {state['missing']} min (including buffer).",
                          'Adjust scope/effort, explicitly change daily capacity within the configured limit, or negotiate a later deadline. No feasible schedule is claimed.']
            else:
                lines += ['\nReserved buffer:'] + [self.slot_text(s) for s in state['slots'] if s['step'] == -1]
            lines += ['\nAI assumptions (not verified requirements):'] + [escape(a) for a in state['assumptions']]
            lines += ['No course slides were searched. Confirm requirements in TronClass when needed.',
                      'No tasks/events created yet. Confirmation rechecks Calendar. Preview expires in 2 hours.']
        buttons = []
        if state['status'] == 'draft':
            buttons.append({'text': 'Confirm schedule', 'callback_data': 'backward:allow:'+state['token']})
        buttons += [{'text': 'Adjust plan', 'callback_data': 'backward:edit:'+state['token']},
                    {'text': 'Cancel', 'callback_data': 'backward:cancel:'+state['token']}]
        # At most eight steps, but fragmented schedules can exceed a Telegram message.
        text = '\n'.join(lines)
        if len(text) > 3900:
            # Preserve full schedule in multiple messages; controls belong to the first card.
            chunks, current = [], ''
            for line in lines:
                if len(current)+len(line)+1 > 3700:
                    chunks.append(current)
                    current = ''
                current += ('\n' if current else '') + line
            chunks.append(current)
        else:
            chunks = [text]
        return {'messages': [{'text': chunk, 'parse_mode': 'HTML',
            **({'backward_plan': state['token'], 'reply_markup': {'inline_keyboard': [buttons]}} if i == len(chunks)-1 else {})}
            for i, chunk in enumerate(chunks)]}

    @staticmethod
    def slot_text(slot):
        return display_time(slot['start'], TZ) + '–' + datetime.fromisoformat(slot['end']).strftime('%H:%M')

    async def callback(self, chat, message_id, token, action, now=None):
        now = (now or datetime.now(TZ)).astimezone(TZ)
        state = self.db.get_task_action_state(key(token))
        if not state or state['chat_id'] != chat or state.get('message_id') != message_id:
            return 'This button is not bound to this plan card.'
        if state['status'] == 'created':
            return self.progress(token, chat)
        if state['status'] in {'cancelled', 'superseded', 'conflict'}:
            return 'This preview is no longer active. Use /backward #<goal-id> or describe the goal again.'
        if action in {'cancel', 'edit'}:
            def change(old):
                if old['status'] in {'creating', 'partial', 'created'}:
                    return old
                return {**old, 'status': 'cancelled' if action == 'cancel' else 'editing'}
            updated = self.db.mutate_task_action_state(key(token), change)
            if updated['status'] in {'creating', 'partial', 'created'}:
                return 'Approved writes must be recovered first. Tap Confirm schedule; existing work will be preserved.'
            return 'Cancelled. No tasks/events created.' if action == 'cancel' else 'Send the changes (deadline, progress, scope or daily minutes). A revised preview requires confirmation.'
        if state['status'] not in {'draft', 'creating', 'partial'} or (state['status'] == 'draft' and state['expires_at'] <= now.isoformat()):
            return 'This plan cannot be approved or has expired. Adjust it to prepare a new preview.'
        claim = uuid4().hex
        def lock():
            current = self.db.get_task_action_state(key(token))
            lock_state = self.db.get_task_action_state('backward:writer') or {}
            if current['status'] not in {'draft', 'creating', 'partial'}:
                return False
            if lock_state.get('until', '') > now.isoformat():
                return False
            self.db._save_task_action_state('backward:writer', {'claim': claim, 'until': (now+timedelta(minutes=10)).isoformat()})
            return True
        if not self.db._run_task_action(lock):
            return 'Another confirmation is running. Tap Confirm schedule again to recover its result.'
        try:
            return await self.create(state, now)
        except Exception as error:
            # A failed task transaction may leave earlier transactions committed.
            # If the state store itself remains unavailable, propagate so Telegram
            # retries the webhook; otherwise give an accurate durable receipt.
            fresh = self.db.get_task_action_state(key(token))
            if fresh['status'] in {'creating', 'partial'}:
                self.db.mutate_task_action_state(key(token), lambda old, error_name=type(error).__name__: {**old, 'status': 'partial',
                    'last_error_type': error_name, 'last_error_at': now.isoformat()})
            fresh = self.db.get_task_action_state(key(token))
            return (f"Planning could not verify or finish the plan. Recorded: {len(fresh['children'])}/{len(fresh['steps'])} step tasks, "
                    f"{len(fresh['events'])}/{len(fresh['slots'])} blocks. A last write may be uncertain; "
                    'tap Confirm schedule to reconcile and retry. No complete-plan claim is made.')
        finally:
            self.db.mutate_task_action_state('backward:writer', lambda old: {} if old.get('claim') == claim else old)

    async def create(self, state, now):
        token = state['token']
        await self.calendar.verify_owner()
        # Reconcile all IDs before considering conflicts. Never duplicate an uncertain insert.
        known = {}
        for i, slot in enumerate(state['slots']):
            event_id = sha256(f'chronos-backward:{token}:{i}'.encode()).hexdigest()
            event = await self.calendar.event(event_id)
            if event is None and str(i) in state.get('events', {}):
                raise CalendarError('A previously verified event was removed. Inspect Calendar before recovery; it will not be recreated automatically.')
            if event:
                if not verified_event(event, event_id, state, slot):
                    raise CalendarError('An approved event changed. Inspect Calendar before recovery.')
                known[str(i)] = event_id
        boundary = deadline_boundary(state['deadline'])
        busy = await self.calendar.busy(now, max(now+timedelta(minutes=1), boundary))
        policy = self.db.get_task_action_state('learning:course-calendar-policy')
        if state['status'] in {'creating', 'partial'}:
            current = self.db.get_task(state['parent_id']) if state.get('parent_id') else None
            if current and state.get('snapshot') and (current['status'] != 'open' or task_fields(current) != state['snapshot']):
                raise CalendarError('The approved goal changed. Review existing work before recovery.')
        for i, slot in enumerate(state['slots']):
            if str(i) in known:
                continue
            start, end = (datetime.fromisoformat(slot[k]) for k in ('start', 'end'))
            opening = datetime.combine(start.date(), time(self.settings.learning_start_hour), TZ)
            closing = datetime.combine(start.date(), time(self.settings.learning_end_hour % 24), TZ)
            if self.settings.learning_end_hour == 24:
                closing += timedelta(days=1)
            if (start <= now or start < opening or end > closing or end > boundary
                    or overlaps(start, end, [*busy, *class_windows(start.date(), TZ, policy), *short_task_windows(start.date(), TZ, 15)])):
                if state['status'] == 'draft':
                    self.db.mutate_task_action_state(key(token), lambda old: {**old, 'status': 'conflict'})
                raise CalendarError('Slots changed. No remaining blocks were inserted.')
        if state['status'] == 'draft':
            # All database reads precede writes for real Firestore transactions.
            def reserve():
                fresh = self.db.get_task_action_state(key(token))
                index = self.db.get_task_action_state(INDEX) or {'slots': []}
                tasks = self.db.list_open_tasks()
                learning = {t['id']: self.db.get_task_action_state(f"learning:task:{t['id']}") or {} for t in tasks}
                parent = self.db.get_task(state['parent_id']) if state.get('parent_id') else None
                link = self.db.get_task_action_state(f"backward:goal:{state['parent_id']}") if parent else None
                if (fresh['status'] != 'draft' or link or any(s.get('status') == 'creating' for s in learning.values())
                        or (not state.get('parent_id') and any(t['title'].casefold().strip() == state['goal'].casefold().strip() for t in tasks))
                        or (state.get('parent_id') and (not parent or parent['status'] != 'open' or task_fields(parent) != state['snapshot']))
                        or (parent and learning.get(parent['id'], {}).get('status') in {'scheduled', 'awaiting_feedback'})):
                    return False
                buckets = {}
                added = {}
                for slot in state['slots']:
                    start, end = (datetime.fromisoformat(slot[k]) for k in ('start', 'end'))
                    if overlaps(start, end, [(datetime.fromisoformat(s['start']), datetime.fromisoformat(s['end'])) for s in index['slots']]):
                        return False
                    day = start.date().isoformat()
                    added[day] = added.get(day, 0) + int((end-start).total_seconds()/60)
                for day, minutes in added.items():
                    bucket = self.db.get_task_action_state('learning:day:'+day) or {}
                    class_load = sum((b-a).total_seconds()/60 for a,b in class_windows(date.fromisoformat(day), TZ, policy))
                    if sum(bucket.values())+class_load+minutes > min(state['daily_cap'], self.settings.learning_daily_minutes):
                        return False
                    buckets[day] = {**bucket, 'backward:'+token: minutes}
                if parent:
                    previous = parent.get('due_at') or parent.get('timing', {}).get('due_date')
                    if previous != state['deadline']:
                        timing = {k: v for k, v in (parent.get('timing') or {}).items() if k != 'due_date'}
                        due = deadline_boundary(state['deadline']) if len(state['deadline']) > 10 else None
                        if due is None:
                            timing['due_date'] = state['deadline']
                        updated = self.db.edit_task_with_undo(parent['id'], parent['title'], due, parent.get('project'), timing)
                        fresh['snapshot'] = task_fields(updated)
                self.db._save_task_action_state(key(token), {**fresh, 'status': 'creating'})
                self.db._save_task_action_state(INDEX, {'slots': [s for s in index['slots'] if s['end'] > now.isoformat()] +
                    [{**s, 'token': token} for s in state['slots']]})
                for day, bucket in buckets.items():
                    self.db._save_task_action_state('learning:day:'+day, bucket)
                if parent:
                    self.db._save_task_action_state(f"learning:task:{parent['id']}", {'status': 'backward_managed', 'token': token})
                    self.db._save_task_action_state(f"backward:goal:{parent['id']}", {'token': token})
                return True
            if not self.db._run_task_action(reserve):
                return 'The goal, daily budget or reservations changed. Nothing created; adjust the preview and confirm again.'
        # Each task + its relation + scheduler suppression is one transaction.
        for step_id in ['parent', *map(str, range(len(state['steps'])))]:
            def ensure_task():
                fresh = self.db.get_task_action_state(key(token))
                if step_id == 'parent' and fresh.get('parent_id'):
                    return
                if step_id in fresh['children']:
                    return
                if step_id == 'parent':
                    title = fresh['goal']
                    due = deadline_boundary(fresh['deadline']) if len(fresh['deadline']) > 10 else None
                    timing = {'due_date': fresh['deadline']} if due is None else {}
                else:
                    step = fresh['steps'][int(step_id)]
                    title = step['title']
                    due = max(datetime.fromisoformat(s['end']) for s in fresh['slots'] if s['step'] == int(step_id))
                    timing = {'source_text': f"Backward goal #{fresh['parent_id']}: {step['output']}"}
                created = self.db.create_task(title, due, None, now, timing)
                if step_id == 'parent':
                    fresh['parent_id'] = created['id']
                    fresh['snapshot'] = task_fields(created)
                else:
                    fresh['children'] = {**fresh['children'], step_id: created['id']}
                self.db._save_task_action_state(key(token), fresh)
                self.db._save_task_action_state(f"learning:task:{created['id']}", {'status': 'backward_managed', 'token': token})
                self.db._save_task_action_state(f"backward:goal:{created['id']}", {'token': token, 'parent_id': fresh['parent_id']})
            self.db._run_task_action(ensure_task)
        state = self.db.get_task_action_state(key(token))
        for i, slot in enumerate(state['slots']):
            event_id = sha256(f'chronos-backward:{token}:{i}'.encode()).hexdigest()
            if str(i) not in known:
                # Recheck immediately before each write; external Calendar changes
                # cannot be locked transactionally with our database.
                start, end = (datetime.fromisoformat(slot[k]) for k in ('start', 'end'))
                if overlaps(start, end, await self.calendar.busy(start, end)):
                    raise CalendarError('New conflict during creation.')
                await self.calendar.insert({'id': event_id, 'summary': ('Buffer · '+state['goal']) if slot['step'] == -1 else state['steps'][slot['step']]['title'],
                    'description': f"Chronos backward plan · Goal #{state['parent_id']} · " +
                        ('Reserved contingency buffer' if slot['step'] == -1 else f"Task #{state['children'][str(slot['step'])]}: " + state['steps'][slot['step']]['output']),
                    'colorId': '10', 'visibility': 'private', 'transparency': 'opaque',
                    'start': {'dateTime': slot['start'], 'timeZone': 'Asia/Taipei'},
                    'end': {'dateTime': slot['end'], 'timeZone': 'Asia/Taipei'},
                    'extendedProperties': {'private': {'chronosBackward': token}}})
                event = await self.calendar.event(event_id)
                if not verified_event(event, event_id, state, slot):
                    raise CalendarError('Calendar insertion needs reconciliation.')
            self.db.mutate_task_action_state(key(token), lambda old, i=i, event_id=event_id:
                {**old, 'events': {**old['events'], str(i): event_id}})
        self.db.mutate_task_action_state(key(token), lambda old: {**old, 'status': 'created'})
        return 'Plan created.\n' + self.progress(token, state['chat_id'])
