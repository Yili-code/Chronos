"""Global Telegram lifetime: retain pending interaction, expire passive output."""
import asyncio
from functools import wraps
from html import unescape
import logging
import re
import time

KEY = 'telegram:message-expiry'
SECONDS = 7

# These controls offer optional navigation or reversal after an outcome, rather
# than requesting a decision needed to complete the current operation.
OPTIONAL = ('help:', 'task:undo:', 'learn:adjustbooked:', 'task:edit:', 'task:plan:', 'taskreview:open')
REQUEST = re.compile(
    r'請(?:直接|重新|先|再)?(?:回覆|選擇|提供|補充|確認|告訴|輸入|簡述|說明|重試|重送)|'
    r'(?:回覆|回复)[「：:]\s*|尚需.*(?:資料|資訊)|還需要你|等待你|'
    r'^(?:reply|choose|select|provide|enter|tell me|send a new|send the|retry|tap .*again)\b|'
    r'\b(?:please|you (?:must|need to))\s+(?:reply|choose|select|provide|enter|confirm|retry|send)\b', re.I | re.M)
QUESTION = re.compile(r'[?？]\s*$', re.M)


def requires_response(text, markup=None, explicit=None, delete_after_seconds=None):
    if explicit is not None:
        return bool(explicit)
    if delete_after_seconds == SECONDS:
        return False
    markup = markup or {}
    if markup.get('force_reply'):
        return True
    buttons = [button for row in markup.get('inline_keyboard', []) for button in row]
    if any(button.get('callback_data') and not button['callback_data'].startswith(OPTIONAL) for button in buttons):
        return True
    if markup.get('keyboard'):
        return True
    plain = unescape(re.sub(r'<[^>]*>', '', text))
    return bool(REQUEST.search(plain) or QUESTION.search(plain))


class MessageExpiry:
    def __init__(self, client, store=None):
        self.client, self.store = client, store
        self.memory = {}
        self.tasks = set()
        self.sleep = None

    def read(self):
        return (self.store().get_task_action_state(KEY) or {}) if self.store else dict(self.memory)

    def mutate(self, change):
        if self.store:
            return self.store().mutate_task_action_state(KEY, lambda old: change(old or {}))
        self.memory = change(dict(self.memory))
        return self.memory

    async def track(self, result, chat_id, text='', markup=None, *, requires_reply=None, delete_after_seconds=None, deadline=None):
        message_id = (result.get('result') or {}).get('message_id') if result.get('ok') else None
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            return
        key = f'{chat_id}:{message_id}'
        if requires_response(text, markup, requires_reply, delete_after_seconds):
            self.mutate(lambda old: {k:v for k,v in old.items() if k != key})
            return
        entry = {'chat_id': chat_id, 'message_id': message_id, 'delete_at': deadline if deadline is not None else time.time() + SECONDS}
        self.mutate(lambda old: {**old, key: entry})
        task = asyncio.create_task(self.expire(key, entry))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def expire(self, key, entry):
        try:
            await (self.sleep or asyncio.sleep)(max(0, entry['delete_at'] - time.time()))
            if self.read().get(key) != entry:
                return  # A newer edit can turn this notice into a pending prompt.
            result = await self.client.request('deleteMessage', {'chat_id': entry['chat_id'], 'message_id': entry['message_id']})
            missing = result.get('error_code') == 400 and 'message to delete not found' in result.get('description','').lower()
            if result.get('ok') or missing or (result.get('error_code') == 400 and any(v in result.get('description','').lower() for v in ("can't be deleted", 'cannot be deleted'))):
                self.mutate(lambda old: {k:v for k,v in old.items() if k != key or v != entry})
        except asyncio.CancelledError:
            raise  # Persisted deadline survives worker/request interruption.
        except Exception:
            logging.getLogger(__name__).warning('Telegram expiry pending; a later drain will retry')

    async def drain(self, *, wait=True):
        current_loop = asyncio.get_running_loop()
        running = [t for t in self.tasks if not t.done() and t.get_loop() is current_loop]
        if running and wait:
            await asyncio.gather(*running, return_exceptions=True)
            return  # A failed deletion retries on the next request/job, not a tight loop.
        pending = self.read()
        due = [(key,entry) for key,entry in pending.items() if wait or entry['delete_at'] <= time.time()]
        if due:
            await asyncio.gather(*(self.expire(key,entry) for key,entry in due), return_exceptions=True)


def drain_messages(provider):
    """Keep scheduled-job CPU active until already-sent notices have expired."""
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            try:
                return await function(*args, **kwargs)
            finally:
                try:
                    await provider().flush_expirations()
                except Exception:
                    logging.getLogger(__name__).warning('Telegram cleanup pending after job')
        return wrapped
    return decorate
