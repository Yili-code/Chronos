import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from chronos.telegram import TelegramClient
from chronos.message_lifecycle import KEY, requires_response
from test_mail_workflow import workflow
from test_system import system, observe_message_lifecycle


@pytest.mark.parametrize('text', [
    '已記錄 資料庫系統（11/05）：今天無課或沒有進度。填寫進度代辦已完成，其他代辦保持不變。',
    'Added task #12: Networking', '操作已完成。', '已取消。', '今天沒有新郵件。',
    'Daily briefing · 11/05', '檔案已保存。', 'Calendar 已更新。', '這封郵件已有任務。',
    '原信摘要：網路協定比較。', 'Reminder: report deadline tomorrow',
])
def test_passive_output_expires_by_default_without_text_allowlist(text):
    assert not requires_response(text)


@pytest.mark.parametrize('text', ['請直接回覆今天的課堂進度。', '請補充開始與結束時間。',
    'Which task should I update?', '下次要先做哪個？', 'Reply with the revised title.', '請重新選擇後重試。'])
def test_pending_plain_replies_remain_available(text):
    assert requires_response(text)


def test_required_actions_and_optional_controls_have_different_lifetimes():
    keyboard = lambda data: {'inline_keyboard': [[{'text': 'Action', 'callback_data': data}]]}
    assert requires_response('Choose', {'force_reply': True})
    assert requires_response('預覽', keyboard('clear:confirm'))
    assert requires_response('進度', keyboard('learn:done:1:abc'))
    assert requires_response('郵件', keyboard('mail:task:ab12'))
    for action in ('help:index', 'task:undo:abc', 'learn:adjustbooked:1:abc', 'task:edit:1', 'taskreview:open'):
        assert not requires_response('已完成', keyboard(action))
    assert not requires_response('請確認？', keyboard('anything:confirm'), explicit=False)
    assert requires_response('Status', explicit=True)


@pytest.mark.asyncio
async def test_send_returns_before_expiry_and_flush_deletes_after_delivery(monkeypatch):
    bot = TelegramClient('test-token')
    order = []
    gate = asyncio.Event()
    async def delay(seconds):
        assert 0 <= seconds <= 7
        order.append('timer')
        await gate.wait()
    monkeypatch.setattr('chronos.message_lifecycle.asyncio.sleep', delay)
    async def request(method, payload):
        order.append(method)
        return {'ok': True, 'result': {'message_id': 42}} if method == 'sendMessage' else {'ok': True}
    bot.request = AsyncMock(side_effect=request)
    result = await bot.send_message(123, '已記錄，今天無課。')
    assert result['result']['message_id'] == 42
    assert order == ['sendMessage']
    # Caller can commit its receipt/binding while the seven-second timer is pending.
    order.append('receipt saved')
    gate.set()
    await bot.flush_expirations()
    assert order == ['sendMessage', 'receipt saved', 'timer', 'deleteMessage']
    assert not bot.expiry.read()


@pytest.mark.asyncio
async def test_durable_restart_recovers_pending_id_without_resending(workflow, monkeypatch):
    monkeypatch.setattr('chronos.message_lifecycle.asyncio.sleep', AsyncMock())
    bot = TelegramClient('test-token', expiry_store=lambda: workflow.db)
    bot.request = AsyncMock(side_effect=[{'ok': True, 'result': {'message_id': 42}}, {'ok': False, 'error_code': 500}])
    await bot.send_message(123, '已完成')
    await bot.flush_expirations()
    assert workflow.db.get_task_action_state(KEY)
    fresh = TelegramClient('test-token', expiry_store=lambda: workflow.db)
    fresh.request = AsyncMock(return_value={'ok': True})
    await fresh.flush_expirations()
    fresh.request.assert_awaited_once_with('deleteMessage', {'chat_id': 123, 'message_id': 42})
    assert not workflow.db.get_task_action_state(KEY)


@pytest.mark.asyncio
async def test_prompts_and_failed_sends_never_enter_expiry_queue(monkeypatch):
    timer = AsyncMock()
    monkeypatch.setattr('chronos.message_lifecycle.asyncio.sleep', timer)
    bot = TelegramClient('test-token')
    bot.request = AsyncMock(return_value={'ok': True, 'result': {'message_id': 42}})
    await bot.send_message(123, '請回覆進度', reply_markup={'force_reply': True})
    bot.request.return_value = {'ok': False, 'error_code': 500}
    await bot.send_message(123, '已完成')
    await bot.flush_expirations()
    timer.assert_not_awaited()
    assert not bot.expiry.read()


@pytest.mark.asyncio
async def test_editing_a_notice_into_a_prompt_cancels_pending_expiry(monkeypatch):
    gate = asyncio.Event()
    async def delay(seconds):
        await gate.wait()
    monkeypatch.setattr('chronos.message_lifecycle.asyncio.sleep', delay)
    bot = TelegramClient('test-token')
    bot.request = AsyncMock(return_value={'ok': True})
    result = {'ok': True, 'result': {'message_id': 42}}
    await bot.expiry.track(result, 123, '已完成')
    await bot.expiry.track(result, 123, '請補充日期', {'force_reply': True})
    gate.set()
    await bot.flush_expirations()
    bot.request.assert_not_awaited()


def test_screenshot_course_ack_uses_global_expiry_after_receipt_commit(system, monkeypatch):
    from chronos import main
    client, tasks, bot, config = system
    observe_message_lifecycle(bot)
    text = '已記錄 資料庫系統（11/05）：今天無課或沒有進度。填寫進度代辦已完成，其他代辦保持不變。'
    monkeypatch.setattr(main, 'prepare_message', AsyncMock(return_value=lambda: text))
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
    async def request(method, payload):
        assert method == 'deleteMessage'
        assert main.db.get_update(99001)['delivered']
        return {'ok': True}
    bot.request.side_effect = request
    payload = {'update_id': 99001, 'message': {'message_id': 40, 'chat': {'id': 123}, 'text': '今天無課'}}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    assert client.post('/telegram/webhook', json=payload, headers=headers).status_code == 200
    bot.request.assert_awaited_once_with('deleteMessage', {'chat_id': 123, 'message_id': 42})
    assert 0 <= bot.expiry.sleep.await_args.args[0] <= 7
    assert client.post('/telegram/webhook', json=payload, headers=headers).status_code == 200
    bot.send_message.assert_awaited_once()
    assert not tasks.list_open()


def test_explicit_pending_question_is_retained_even_without_question_words(system, monkeypatch):
    from chronos import main
    client, tasks, bot, config = system
    observe_message_lifecycle(bot)
    monkeypatch.setattr(main, 'prepare_message', AsyncMock(return_value=lambda: {'messages': [
        {'text': '補充資料中', 'requires_response': True}]}))
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 43}}
    payload = {'update_id': 99002, 'message': {'message_id': 40, 'chat': {'id': 123}, 'text': '處理'}}
    assert client.post('/telegram/webhook', json=payload, headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'}).status_code == 200
    bot.request.assert_not_awaited()
    assert not bot.expiry.read()


@pytest.mark.asyncio
async def test_document_message_uses_the_same_expiry_policy(monkeypatch):
    import httpx
    calls=[]
    original=httpx.AsyncClient
    def handle(request):
        method=request.url.path.rsplit('/',1)[-1]
        calls.append(method)
        return httpx.Response(200,json={'ok':True,'result':{'message_id':44}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    monkeypatch.setattr('chronos.message_lifecycle.asyncio.sleep',AsyncMock())
    bot=TelegramClient('test-token')
    await bot.send_document(123,'note.md',b'canonical note remains stored')
    await bot.flush_expirations()
    assert calls==['sendDocument','deleteMessage']


def test_health_reads_never_delete_or_drain_telegram_messages(system):
    client, tasks, bot, config = system
    bot.expiry.mutate(lambda old: {'123:49': {'chat_id':123,'message_id':49,'delete_at':0}})
    assert client.get('/health').status_code == 200
    bot.request.assert_not_awaited()
    assert '123:49' in bot.expiry.read()


@pytest.mark.asyncio
async def test_cleanup_store_outage_does_not_turn_success_into_send_failure(monkeypatch):
    class Unavailable:
        def get_task_action_state(self, key):
            raise RuntimeError('unavailable')
        def mutate_task_action_state(self, key, change):
            raise RuntimeError('unavailable')
    monkeypatch.setattr('chronos.message_lifecycle.asyncio.sleep', AsyncMock())
    bot = TelegramClient('test-token', expiry_store=lambda: Unavailable())
    bot.request = AsyncMock(side_effect=[{'ok':True,'result':{'message_id':50}}, {'ok':True}])
    result = await bot.send_message(123, '已記錄')
    assert result['ok']
    await bot.flush_expirations()
    assert bot.request.await_count == 2
    assert bot.request.await_args.args == ('deleteMessage',{'chat_id':123,'message_id':50})


@pytest.mark.asyncio
async def test_legacy_absolute_deadline_is_not_restarted_after_upgrade(monkeypatch):
    timer = AsyncMock()
    monkeypatch.setattr('chronos.message_lifecycle.asyncio.sleep', timer)
    bot = TelegramClient('test-token')
    bot.request = AsyncMock(return_value={'ok': True})
    await bot.track_delivery({'ok':True,'result':{'message_id':51}},123,requires_reply=False,deadline=0)
    await bot.flush_expirations()
    timer.assert_awaited_once_with(0)
    bot.request.assert_awaited_once_with('deleteMessage',{'chat_id':123,'message_id':51})


def test_owner_message_expires_after_success_and_retry_keeps_original_deadline(system, monkeypatch):
    from types import SimpleNamespace
    from chronos import main, message_lifecycle

    client, tasks, bot, _ = system
    task = tasks.create('Finish this task')
    now = 1794434400
    clock = SimpleNamespace(time=lambda: now)
    monkeypatch.setattr(message_lifecycle, 'time', clock)
    bot.expiry.sleep = AsyncMock()

    async def delete(method, payload):
        assert method == 'deleteMessage'
        assert payload == {'chat_id': 123, 'message_id': 40}
        assert main.db.get_update(99003)['delivered']
        assert main.db.get_task(task['id'])['status'] == 'done'
        if now > message['date']:
            return {'ok': False, 'error_code': 400, 'description': 'message to delete not found'}
        return {'ok': True}

    bot.request.side_effect = delete
    message = {'message_id': 40, 'chat': {'id': 123, 'type': 'private'}, 'date': now, 'text': '/done 1'}
    payload = {'update_id': 99003, 'message': message}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    assert client.post('/telegram/webhook', json=payload, headers=headers).status_code == 200
    bot.expiry.sleep.assert_awaited_once_with(7)
    now += 8
    assert client.post('/telegram/webhook', json=payload, headers=headers).status_code == 200
    assert bot.expiry.sleep.await_args.args == (0,)
    bot.send_message.assert_awaited_once()
    assert not bot.expiry.read()


@pytest.mark.parametrize(('kind', 'chat_type', 'secret', 'status'), [
    ('message', 'group', 'test-hook', 200),
    ('edited_message', 'private', 'test-hook', 200),
    ('message', 'private', 'wrong', 403),
    ('message', 'private', 'test-hook', 502),
])
def test_owner_cleanup_ignores_nonprivate_edits_and_unsuccessful_webhooks(system, kind, chat_type, secret, status):
    client, _, bot, _ = system
    if status == 502:
        bot.send_message.return_value = {'ok': False}
    payload = {'update_id': 99004, kind: {'message_id': 41, 'chat': {'id': 123, 'type': chat_type},
               'date': 1794434400, 'text': '/tasks'}}
    assert client.post('/telegram/webhook', json=payload,
                       headers={'X-Telegram-Bot-Api-Secret-Token': secret}).status_code == status
    bot.request.assert_not_awaited()
    assert not bot.expiry.read()
