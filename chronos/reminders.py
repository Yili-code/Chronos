"""One-time notifications, without task completion or Calendar writes."""
import re
from datetime import datetime, timedelta
from hashlib import sha256
from html import escape
from uuid import uuid4

from .telegram import TelegramError

KEY = 'reminders:v1'


def matches(text):
    if text.startswith('/'):
        return bool(re.match(r'^/(?:remind|reminders|cancelreminder)(?:\s|$)', text))
    return bool(re.search(r'提醒|通電話|打電話|\b(?:meeting|remind me|call with)\b', text, re.I))


def line(item, settings):
    start = datetime.fromisoformat(item['start']).astimezone(settings.tz)
    end = datetime.fromisoformat(item['end']).astimezone(settings.tz)
    return f"{escape(item['title'])}\n{start:%m/%d %H:%M}–{end:%H:%M}"


async def command(db, ai, calendar, settings, text, chat_id, update_id, now=None):
    now = now or datetime.now(settings.tz)
    if text.strip() == '/reminders':
        items = (db.get_task_action_state(KEY) or {}).values()
        return '\n\n'.join(f"{item['token']} · {item['status']} · {line(item, settings)}" for item in items
                           if item['status'] in {'active', 'pending', 'sending', 'uncertain'}) or 'No upcoming reminders.'
    if text.startswith('/cancelreminder '):
        return cancel(db, text.split(maxsplit=1)[1].strip(), chat_id)
    output = await ai.parse_reminder(re.sub(r'^/remind\s*', '', text), now)
    if not output.actionable or not output.start:
        return 'Please specify an unambiguous date and time, including AM/PM. No reminder added.'
    try:
        start = datetime.fromisoformat(output.start)
        if start.utcoffset() is None or start <= now:
            raise ValueError()
    except ValueError:
        return 'Choose a future date and time with a timezone. No reminder added.'
    minutes = output.minutes or (30 if output.kind in {'call', 'meeting'} else 1)
    end = start + timedelta(minutes=minutes)
    await calendar.verify_owner()
    busy = await calendar.busy(start, end)
    token = sha256(f'{chat_id}:{update_id}'.encode()).hexdigest()[:16]
    # Shared transaction serializes reminder creation and overlap checks on both backends.
    def save(old):
        items = dict(old or {})
        if token in items:
            return items
        conflicts = [f"Calendar · {a.astimezone(settings.tz):%m/%d %H:%M}–{b.astimezone(settings.tz):%H:%M}"
                     for a, b in busy if start < b and a < end]
        conflicts += [line(x, settings) for x in items.values()
                      if x['status'] == 'active' and start < datetime.fromisoformat(x['end'])
                      and datetime.fromisoformat(x['start']) < end]
        items[token] = dict(token=token, title=output.title, start=start.isoformat(), end=end.isoformat(),
                            chat_id=chat_id, status='pending' if conflicts else 'active',
                            conflicts=conflicts, expires_at=(now + timedelta(minutes=30)).isoformat())
        return items
    item = db.mutate_task_action_state(KEY, save)[token]
    if item['status'] == 'pending':
        return {'messages': [{'text': '<b>Time overlap — add anyway?</b>\n' + line(item, settings)
                 + '\n\n' + '\n'.join(item['conflicts']), 'parse_mode': 'HTML',
                 'reply_markup': {'inline_keyboard': [[
                     {'text': 'Add anyway', 'callback_data': 'reminder:allow:' + token},
                     {'text': 'Cancel', 'callback_data': 'reminder:cancel:' + token}]]}}]}
    return {'messages': [{'text': '<b>Reminder added</b>\n' + line(item, settings), 'parse_mode': 'HTML'}]}


def cancel(db, token, chat_id):
    return confirm(db, token, 'cancel', chat_id)


def confirm(db, token, action, chat_id, now=None):
    now = now or datetime.now().astimezone()
    reply = 'This reminder is no longer pending.'
    def change(old):
        nonlocal reply
        items = dict(old or {})
        item = items.get(token)
        if not item or item['chat_id'] != chat_id:
            return items
        if action == 'cancel' and item['status'] in {'pending', 'active'}:
            items[token] = {**item, 'status': 'cancelled'}
            reply = 'Reminder cancelled.'
        elif action == 'allow' and item['status'] == 'pending':
            expired = now >= datetime.fromisoformat(item['expires_at']) or now >= datetime.fromisoformat(item['start'])
            items[token] = {**item, 'status': 'expired' if expired else 'active'}
            reply = 'Preview expired. Send a new reminder.' if expired else 'Reminder added. No completion update needed.'
        return items
    db.mutate_task_action_state(KEY, change)
    return reply


async def tick(db, telegram, settings, now=None):
    if not settings.telegram_chat_id or not telegram.enabled:
        return {'sent': 0}
    now = now or datetime.now(settings.tz)
    sent = 0
    failed = 0
    for token, item in (db.get_task_action_state(KEY) or {}).items():
        if item['status'] != 'active' or datetime.fromisoformat(item['start']) > now:
            continue
        claim = uuid4().hex
        def reserve(old):
            items = dict(old or {})
            current = items.get(token)
            if current and current['status'] == 'active':
                items[token] = {**current, 'status': 'sending', 'claim': claim}
            return items
        saved = db.mutate_task_action_state(KEY, reserve)[token]
        if saved.get('claim') != claim:
            continue
        status = 'uncertain'
        try:
            result = await telegram.send_message(item['chat_id'], '<b>Reminder</b>\n' + line(item, settings), parse_mode='HTML')
            if result.get('ok'):
                status = 'sent'
                sent += 1
            elif 400 <= result.get('error_code', 500) < 500:
                status = 'active'
        except TelegramError:
            pass
        db.mutate_task_action_state(KEY, lambda old: {**old, token: {**old[token], 'status': status}})
        if status != 'sent':
            failed += 1
    if failed:
        raise RuntimeError(f'{failed} reminder deliveries pending or uncertain; inspect reminder state before retrying')
    return {'sent': sent}
