"""Explicit course habits and durable, individually approved task suggestions."""
from datetime import datetime, timedelta
from hashlib import sha256
from html import escape
import re

from .course_tracking import COURSE_SCHEDULE

PREFIX = 'course-habit:'
DEFAULTS = {'competitive-programming': ['學習本週上課內容', '完成至少六題程式設計題目']}


def preference(db, course_key):
    saved = db.get_task_action_state(PREFIX + course_key)
    return saved['items'] if saved is not None else DEFAULTS.get(course_key, [])


def parse_items(text):
    """Only explicit recurring statements with exactly two numbered items qualify."""
    match = re.fullmatch(r'(?:通常(?:都是|是)?|每週(?:都是|是)?|以後(?:都是|是)?|記住)\s*[:：]?\s*1[.、．]\s*(.+?)\s*2[.、．]\s*(.+)', text.strip(), re.S)
    if not match:
        return None
    items = [item.strip().rstrip('。') for item in match.groups()]
    return items if (all(1 <= len(item) <= 200 for item in items)
                     and not re.search(r'(?:^|\s)3[.、．]', items[1])) else None


def remember_reply(db, prompt_id, text):
    items = parse_items(text)
    if items is None:
        return None
    session = db.get_course_session_by_prompt(prompt_id)
    if session is None:
        return None
    db._save_task_action_state(PREFIX + session.course_key, {'items': items})
    return f'已記住 {session.course_name} 的兩項課後偏好。之後課後會先提出建議，接受後才加入待辦；這次沒有新增任務。'


def command(db, text):
    parts = [part.strip() for part in text.split('|')]
    head = parts[0].split()
    courses = {slot.key: slot for slot in COURSE_SCHEDULE}
    if len(head) != 2 or head[1] not in courses:
        return '用法：/coursehabit 課程代碼 | 第一項 | 第二項；停用：/coursehabit 課程代碼 | off。\n課程代碼：' + ', '.join(courses)
    key = head[1]
    if len(parts) == 1:
        items = preference(db, key)
        return courses[key].name + '\n' + ('\n'.join(f'{i}. {value}' for i, value in enumerate(items, 1)) or '已停用課後任務建議。')
    items = [] if parts[1:] == ['off'] else parts[1:]
    if items and (len(items) != 2 or not all(1 <= len(item) <= 200 for item in items)):
        return '請提供兩項各 1–200 字的偏好，或使用 off 停用。'
    db._save_task_action_state(PREFIX + key, {'items': items})
    return '已更新這門課的課後偏好；只影響之後的建議，沒有新增任務。'


def token_for(session):
    return sha256(session.session_id.encode()).hexdigest()[:16]


def prepare(db, session, chat_id, now):
    """Commit the immutable proposal before sending; retries reuse its contents."""
    token = token_for(session)
    def save():
        saved = db.get_task_action_state('course-proposal:' + token)
        items = preference(db, session.course_key)
        if saved:
            return saved
        if not items:
            return None
        state = {'chat_id': chat_id, 'session_id': session.session_id,
                 'course_name': session.course_name, 'date': session.class_date.isoformat(),
                 'items': [{'title': item, 'status': 'pending'} for item in items],
                 'expires': (now + timedelta(days=7)).isoformat()}
        db._save_task_action_state('course-proposal:' + token, state)
        return state
    state = db._run_task_action(save)
    if not state:
        return None
    text = f"<b>{escape(state['course_name'])} · {state['date']}</b>\n課後任務建議（接受後才加入待辦）：\n"
    text += '\n'.join(f"{i}. {escape(item['title'])}" for i, item in enumerate(state['items'], 1))
    buttons = [[{'text': f'接受 {i+1}', 'callback_data': f'chabit:{token}:{i}:accept'},
                {'text': f'略過 {i+1}', 'callback_data': f'chabit:{token}:{i}:skip'}] for i in range(len(state['items']))]
    return text, {'inline_keyboard': buttons}


def respond(db, data, chat_id, message_id, now):
    _, token, raw_index, action = data.split(':')
    state = db.get_task_action_state('course-proposal:' + token)
    if not state or state['chat_id'] != chat_id:
        return '這張建議無效。沒有新增任務。'
    delivery = db.get_study_delivery(state['session_id'] + ':prompt')
    index = int(raw_index)
    if (not delivery or delivery.get('message_id') != message_id or index >= len(state['items'])
            or now > datetime.fromisoformat(state['expires'])):
        return '這張建議無效或已過期。沒有新增任務。'
    item = state['items'][index]
    if item['status'] != 'pending':
        return '這項建議已處理，沒有重複新增。'
    if action == 'accept':
        task = db.create_task(f"{state['course_name']}（{state['date']}）：{item['title']}", None, None, now)
        item = {**item, 'status': 'accepted', 'task_id': task['id']}
        reply = f"已新增 #{task['id']}：{task['title']}"
    elif action == 'skip':
        item = {**item, 'status': 'skipped'}
        reply = '已略過這項建議，課後偏好仍保留。'
    else:
        return '無效操作。沒有新增任務。'
    items = list(state['items'])
    items[index] = item
    db._save_task_action_state('course-proposal:' + token, {**state, 'items': items})
    return reply
