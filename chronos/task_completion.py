"""Whole-task reports and optional measurements, separate from booked sessions."""
from datetime import datetime, timedelta
from html import escape
import re
from uuid import uuid4

from .google_calendar import CalendarError
from .task_actions import task_fields
from .study_feedback import extract, NUMBER
from .task_timing import display_time


def display_title(title):
    title = re.sub(r'database[–—_-]systems', 'Database systems', title, flags=re.I)
    match = re.fullmatch(r'(?:Study |Review )?(?:Database systems|DB|資料庫)\s*(?:Chapter|ch\.?|第)\s*(\d+)\s*章?', title, re.I)
    return f'資料庫第 {int(match[1])} 章' if match else title


def explicit_done(text):
    return (bool(re.search(r'複習完|复习完|讀完|读完|念完|看完|完整完成|全部完成|做完|finished|completed', text, re.I))
        and not re.search(r'還沒|还没|尚未|沒有|没有|部分|一半|半章|只看|只讀|只读|未完成|未讀完|未读完|還剩|还剩|not |haven.t', text, re.I))


def chapter_matches(text, items):
    pattern = r'(?:database[–—_ -]*systems?|database|db|資料庫)\s*(?:chapter|ch\.?|第)\s*(\d+)\s*章?'
    match = re.search(pattern, text, re.I)
    if not match:
        return []
    return [item['id'] for item in items if (found := re.fullmatch(r'(?:Study |Review |複習\s*)?' + pattern, item['title'], re.I)) and int(found[1]) == int(match[1])]


def reported_minutes(text):
    minutes = extract(text).get('minutes')
    return int(minutes) if minutes is not None and 0 < minutes <= 720 and minutes == int(minutes) else None


def duration_answer(text):
    minutes = reported_minutes(text)
    if minutes is None or not 0 < minutes <= 720 or minutes != int(minutes):
        return None
    rest = re.sub(rf'{NUMBER}\s*(?:個|个)?半?(?:小時|小时|hours?|hrs?|分鐘|分钟|分)半?|半(?:個|个)?(?:小時|小时)', '', text, flags=re.I)
    rest = re.sub(r'可能|大概|大約|大约|差不多|應該|应该|約|左右|吧|花了|用了|讀了|读了|專心|专心|實際|实际|完整完成|全部完成|完成了|完成|我|about|around|roughly|done', '', rest, flags=re.I)
    return int(minutes) if not re.sub(r'[\s，,。.!！？?、;；]', '', rest) else None


def profile_with_time(profile, task, minutes):
    if minutes is None:
        return profile
    subject = task.get('project') or 'general'
    previous = profile.get(subject, {})
    samples = previous.get('samples', 0)
    return {**profile, subject: {'samples': samples + 1,
        'average_minutes': (previous.get('average_minutes', minutes) * min(samples, 4) + minutes) / (min(samples, 4) + 1)}}


def complete(planner, task, minutes=None, reason='', chat_id=None):
    if minutes is not None and (type(minutes) is not int or not 0 < minutes <= 720):
        return '這次大概投入多久？可以用分鐘或小時說，不確定也沒關係。'
    now = datetime.now(planner.settings.tz)
    def record():
        state = planner.get(task['id']) or {}
        live = planner.db.get_task(task['id'])
        profile = planner.db.get_task_action_state('learning:profile') or {}
        prior_history = planner.db.get_task_action_state('learning:history:' + state['token']) if state.get('token') else None
        if not live or live.get('status') != 'open':
            return '這項任務已經記錄完成，不會重複記錄。'
        if task_fields(live) != task_fields(task):
            return '任務內容剛有變更，請確認最新內容後再回報。'
        if state.get('status') in {'creating', 'partial', 'backward_managed'}:
            return '這項任務的安排還在處理中，請先確認目前的安排，再回報完成。'
        token = (state.get('token') if not prior_history else None) or uuid4().hex
        recorded = {**state, 'token': token, 'status': 'finished',
            'snapshot': task_fields(live), 'feedback': {'scope': 'task', 'outcome': 'done',
                'actual_minutes': minutes, 'approximate': bool(re.search(r'可能|大概|約|about|roughly', reason, re.I)),
                'reason': reason[:1000], 'recorded_at': now.isoformat()}}
        future = False
        try:
            future = state.get('status') == 'scheduled' and bool(state.get('event_id')) and datetime.fromisoformat(state['start']) > now
        except (KeyError, ValueError, TypeError):
            pass
        if future:
            recorded['completion_cleanup'] = {'status': 'offered', 'event_id': state['event_id'], 'start': state['start']}
        planner.db.complete_task(task['id'], now)
        planner.db._save_task_action_state('learning:profile', profile_with_time(profile, task, minutes))
        planner.db._save_task_action_state(f"learning:task:{task['id']}", recorded)
        planner.db._save_task_action_state('learning:history:' + token, recorded)
        if minutes is None and chat_id is not None:
            planner.db._save_task_action_state(f'completion:recent:{chat_id}', {'task_id': task['id'], 'token': token,
                'expires_at': (now + timedelta(minutes=10)).isoformat()})
        text = f"{escape(display_title(task['title']))}已記錄完成。"
        if minutes is not None:
            text += f' 這次投入約 {minutes} 分鐘。'
        else:
            text += '\n這次大概投入多久？沒記時間也沒關係。'
        message = {'text': text, 'parse_mode': 'HTML'}
        if future:
            message['text'] += f"\n原訂 {display_time(state['start'], planner.settings.tz)} 的時段仍保留，要取消嗎？"
            message['reply_markup'] = {'inline_keyboard': [[{'text': '取消原訂時段', 'callback_data': f"learn:completecancel:{task['id']}:{token}"}]]}
        return {'messages': [message]}
    return planner.db._run_task_action(record)


def followup(planner, text, chat_id, now, pending=None):
    pending = pending or planner.db.get_task_action_state(f'completion:recent:{chat_id}')
    if not pending or pending.get('expires_at', '') <= now.isoformat():
        return None
    minutes = duration_answer(text)
    unknown = bool(re.fullmatch(r'\s*(?:沒記時間|没记时间|不知道|不確定|不确定|忘了|沒計時|没计时)[。.!！]?\s*', text))
    if minutes is None and not unknown:
        return None
    def record():
        current = planner.db.get_task_action_state(f'completion:recent:{chat_id}')
        task = planner.db.get_task(pending['task_id'])
        state = planner.get(pending['task_id']) or {}
        profile = planner.db.get_task_action_state('learning:profile') or {}
        if current != pending or not task or task.get('status') != 'done' or state.get('token') != pending['token'] or state.get('status') != 'finished' or state.get('feedback', {}).get('scope') != 'task':
            return '這次完成紀錄已變更，請查看最新狀態。'
        if state['feedback'].get('actual_minutes') is not None:
            return '這次投入時間已記下，不會重複計入。'
        updated = {**state, 'feedback': {**state['feedback'], 'actual_minutes': minutes,
            'approximate': bool(re.search(r'可能|大概|約|about|roughly', text, re.I))}}
        planner.db._save_task_action_state('learning:profile', profile_with_time(profile, task, minutes))
        planner.db._save_task_action_state(f"learning:task:{task['id']}", updated)
        planner.db._save_task_action_state('learning:history:' + pending['token'], updated)
        planner.db._save_task_action_state(f'completion:recent:{chat_id}', {})
        return f'已補記這次投入約 {minutes} 分鐘。' if minutes is not None else '好，完成狀態已記下，投入時間先留空。'
    return lambda: planner.db._run_task_action(record)


async def cancel_future(planner, task_id, token):
    now = datetime.now(planner.settings.tz)
    claim = uuid4().hex
    def acquire():
        state = planner.get(task_id) or {}
        task = planner.db.get_task(task_id)
        cleanup = state.get('completion_cleanup') or {}
        if not task or task.get('status') != 'done' or state.get('status') != 'finished' or state.get('token') != token:
            return None
        if cleanup.get('status') == 'cancelled':
            return 'cancelled'
        if cleanup.get('status') == 'removing' and cleanup.get('lease_until', '') > now.isoformat():
            return None
        if cleanup.get('status') not in {'offered', 'retry', 'removing'}:
            return None
        updated = {**state, 'completion_cleanup': {**cleanup, 'status': 'removing', 'claim': claim,
            'lease_until': (now + timedelta(minutes=2)).isoformat()}}
        planner.db._save_task_action_state(f'learning:task:{task_id}', updated)
        return updated
    state = planner.db._run_task_action(acquire)
    if state == 'cancelled':
        return '原訂時段已取消，不會重複刪除。'
    if not state:
        return '這個取消要求已變更或正在處理，請稍後確認。'
    try:
        await planner.calendar.verify_owner()
        event = await planner.calendar.event(state['completion_cleanup']['event_id'])
        if event and event.get('status') != 'cancelled':
            marker = event.get('extendedProperties', {}).get('private', {})
            start = datetime.fromisoformat(event['start']['dateTime'].replace('Z', '+00:00'))
            if (marker.get('chronosToken') != token or marker.get('chronosTask') != str(task_id)
                    or not event.get('etag') or event.get('recurringEventId') or event.get('recurrence')
                    or event.get('attendees') or event.get('eventType', 'default') != 'default'
                    or start.utcoffset() is None or start <= datetime.now(planner.settings.tz)
                    or start != datetime.fromisoformat(state['completion_cleanup']['start'])):
                raise CalendarError('Booking changed')
            await planner.calendar.delete_event(state['completion_cleanup']['event_id'], event['etag'])
    except (CalendarError, KeyError, ValueError, TypeError):
        planner.update(task_id, lambda old: {**old, 'completion_cleanup': {**old['completion_cleanup'], 'status': 'retry'}}
            if old and old.get('completion_cleanup', {}).get('claim') == claim else old)
        return '完成紀錄已保留，但還無法確認原訂時段是否取消。稍後可再按一次，我會先核對，不會盲目重複刪除。'
    def finish():
        current = planner.get(task_id) or {}
        task = planner.db.get_task(task_id)
        day_key = 'learning:day:' + datetime.fromisoformat(state['completion_cleanup']['start']).astimezone(planner.settings.tz).date().isoformat()
        budget = planner.db.get_task_action_state(day_key) or {}
        if current.get('token') != token or not task or task.get('status') != 'done' or current.get('completion_cleanup', {}).get('claim') != claim:
            return '原訂行程已核對，紀錄狀態另有變更，請查看最新安排。'
        updated = {**current, 'completion_cleanup': {**current['completion_cleanup'], 'status': 'cancelled'}}
        planner.db._save_task_action_state(day_key, {k: v for k, v in budget.items() if k != token})
        planner.db._save_task_action_state(f'learning:task:{task_id}', updated)
        planner.db._save_task_action_state('learning:history:' + token, updated)
        return '原訂時段已取消，完成紀錄與投入時間都已保留。'
    return planner.db._run_task_action(finish)
