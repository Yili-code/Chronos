"""Persist the agenda actually sent; titles are data, never routing authority."""
import re
from .task_actions import task_fields


def snapshot(events, db, now, settings):
    entries = []
    for event in events:
        props = event.get('extendedProperties', {}).get('private', {})
        task_id = str(props.get('chronosTask', ''))
        task = db.get_task(int(task_id)) if re.fullmatch(r'[1-9][0-9]{0,18}', task_id) else None
        state = db.get_task_action_state('learning:task:' + task_id) if task else None
        owned = (state and state.get('status') == 'scheduled' and event.get('id') and props.get('chronosToken')
                 and state.get('event_id') == event.get('id')
                 and state.get('token') == props.get('chronosToken')
                 and event.get('_calendar_id', 'primary') in {'primary', settings.calendar_id, settings.calendar_account})
        entries.append({'title': event.get('summary') or 'Untitled event',
                        'start': event.get('start', {}), 'end': event.get('end', {}),
                        'task_id': task['id'] if owned and task.get('status') == 'open' else None,
                        'snapshot': task_fields(task) if owned else None,
                        'plan': {key: state.get(key) for key in ('status', 'token', 'event_id', 'start', 'end')} if owned else None})
    return {'date': now.date().isoformat(), 'entries': entries}


def bind(db, chat_id, message_id, message):
    if message.get('today_agenda') is not None:
        db.mutate_task_action_state(f'agenda:message:{chat_id}:{message_id}', lambda old: message['today_agenda'])


def current(db, context):
    for entry in context['entries']:
        if entry.get('task_id') is None:
            continue
        task = db.get_task(entry['task_id'])
        if not task or task.get('status') != 'open' or task_fields(task) != entry['snapshot']:
            return False
        state = db.get_task_action_state('learning:task:' + str(entry['task_id'])) or {}
        if {key: state.get(key) for key in entry['plan']} != entry['plan']:
            return False
    return True


STALE = '這份行程的任務或安排已變更，請重新輸入 /today 確認。沒有變更。'


async def prepare(app, context, text, *, chat_id, update_id):
    from .intents import prepare as prepare_intent
    if text.startswith('/'):
        return await app.prepare_message(text, update_id=update_id, chat_id=chat_id)
    if context.get('date') != app.datetime.now(app.settings.tz).date().isoformat():
        return lambda: '這份 today 行程已過期，請重新輸入 /today。沒有變更。'
    if not current(app.db, context):
        return lambda: STALE
    changing = re.search(r'取消|刪|删|改|移|挪|提前|延後|完成|做完|\b(cancel|delete|move|change|complete|done)\b', text, re.I)
    names = [entry for entry in context['entries'] if entry['title'].casefold() in text.casefold()]
    ordinals = set(re.findall(r'第([0-9一二三四五六七八九十]+)[個項筆]', text))
    multiple = (len(names) > 1 or len(ordinals) > 1 or re.search(
        r'全部|全都|都標|都完成|這[兩二三四五六七八九十0-9幾]+[個項筆]|\b(all|both)\b', text, re.I))
    if changing and multiple and len(context['entries']) > 1:
        return lambda: '請一次指定一項行程；清單可能包含未連結任務的 Calendar 活動，尚未變更任何項目。'
    match = re.search(r'第([0-9一二三四五六七八九十]+)[個項筆]', text)
    bound = None
    selected = None
    if match:
        value = match[1]
        position = int(value) if value.isdecimal() else {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
                                                       '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}.get(value, 0)
        if not 1 <= position <= len(context['entries']):
            return lambda: '這份行程沒有該順位，請重新指定行程名稱。沒有變更。'
        selected = context['entries'][position - 1]
    else:
        if len(names) == 1:
            selected = names[0]
    if selected is not None:
        bound = selected.get('task_id')
        if bound is None and changing:
            return lambda: ('這一列是未連結任務的 Calendar 活動，目前不支援從 /today 直接修改、取消或標記完成。'
                            '請到 Calendar 操作；沒有變更。')
    elif changing and len(context['entries']) > 1:
        return lambda: '要調整哪一項行程？請提供名稱或這份清單中的順位，以及希望的新時間或時段。沒有變更。'
    return await prepare_intent(app, text, update_id=update_id, chat_id=chat_id,
                                bound_task_id=bound, agenda_context=context)
