import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi.testclient import TestClient

from chronos import main
from chronos.ai import AIError
from chronos.db import Database
from chronos.settings import Settings
from chronos.tasks import ParsedTask, TaskService, format_tasks
from chronos.telegram import TelegramClient, TelegramError


@pytest.fixture
def system(tmp_path, monkeypatch):
    config = Settings(_env_file=None, database_path=tmp_path / 'test.db',
                      telegram_chat_id=123,
                      telegram_webhook_secret='test-hook', scheduler_secret='test-scheduler',
                      web_password='test-password')
    db = Database(config.database_path)
    service = TaskService(db, config.tz)
    bot = TelegramClient('test-token')
    monkeypatch.setattr(bot, 'send_message', AsyncMock(return_value={'ok': True}))
    monkeypatch.setattr(bot, 'request', AsyncMock(return_value={'ok': True}))
    monkeypatch.setattr(bot, 'answer_callback_query', AsyncMock(return_value={'ok': True}))
    monkeypatch.setattr(bot, 'set_webhook', AsyncMock(return_value={'ok': True}))
    monkeypatch.setattr(main, 'settings', config)
    monkeypatch.setattr(main, 'db', db)
    monkeypatch.setattr(main, 'tasks', service)
    monkeypatch.setattr(main, 'telegram', bot)
    monkeypatch.setattr(main, 'scheduler', AsyncIOScheduler(timezone=config.tz))
    monkeypatch.setattr(main.ai, 'parse', AsyncMock(return_value=ParsedTask('Test task')))
    with TestClient(main.app, raise_server_exceptions=False) as client:
        yield client, service, bot, config


def test_course_reply_is_correlated_and_receipt_deduplicated(system):
    from chronos.course_tracking import COURSE_SCHEDULE, new_session
    client, service, bot, config = system
    session = new_session(COURSE_SCHEDULE[0], datetime.now(config.tz).date(), 501)
    main.db.create_course_session(session)
    payload = {'update_id': 900, 'message': {'message_id': 502,
               'chat': {'id': 123}, 'text': 'Chapter 4',
               'reply_to_message': {'message_id': 501}}}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    for _ in range(2):
        assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert main.db.get_course_session(session.session_id).reported_progress == 'Chapter 4'
    assert main.db.get_update(900)['delivered']
    assert bot.send_message.await_count == 1
    main.ai.parse.assert_not_awaited()
    assert service.list_open() == []
    polls = main.db.list_study_polls()
    assert len(polls) == 1
    assert polls[0][0] == 'poll:reply:900:189717'
    assert polls[0][1]['status'] == 'queued'


def test_unknown_reply_never_creates_task(system):
    client, service, bot, config = system
    response = client.post('/telegram/webhook',
        headers={'X-Telegram-Bot-Api-Secret-Token': 'test-hook'},
        json={'update_id': 901, 'message': {'message_id': 503,
              'chat': {'id': 123}, 'text': 'Chapter 4',
              'reply_to_message': {'message_id': 999}}})
    assert response.status_code == 200
    main.ai.parse.assert_not_awaited()
    assert service.list_open() == []


def test_course_reply_preserves_evidence_and_stores_english_review_label(system, monkeypatch):
    from chronos.course_tracking import COURSE_SCHEDULE, new_session
    client, service, bot, config = system
    today = datetime.now(config.tz).date()
    session = new_session(COURSE_SCHEDULE[1], today, 601)
    main.db.create_course_session(session, create_tasks=True)
    summary = AsyncMock(return_value='Chapter 2 to around page 43')
    monkeypatch.setattr(main.ai, 'summarize_progress', summary)
    payload = {'update_id': 902, 'message': {'message_id': 602,
               'chat': {'id': 123}, 'text': '第二章到43頁左右',
               'reply_to_message': {'message_id': 601}}}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    review, = service.list_open()
    assert review['title'] == f'複習計算機結構 {today:%m/%d}：Chapter 2 to around page 43'
    assert main.db.get_course_session(session.session_id).reported_progress == '第二章到43頁左右'
    summary.assert_awaited_once_with('第二章到43頁左右')


def test_study_scheduler_endpoint_requires_secret_and_is_disabled_by_default(system):
    client, service, bot, config = system
    assert client.post('/internal/study').status_code == 403
    assert client.post('/internal/study', headers={'X-Chronos-Scheduler-Secret': 'wrong'}).status_code == 403
    response = client.post('/internal/study', headers={'X-Chronos-Scheduler-Secret': 'test-scheduler'})
    assert response.status_code == 200
    assert response.json() == {'enabled': False}
    assert main.db.list_study_polls() == []
    bot.send_message.assert_not_awaited()


def test_study_endpoint_refreshes_calendar_before_course_policy(system, monkeypatch):
    from chronos.academic_calendar import OFFICIAL_CALENDAR_URL
    from chronos.course_tracking import TAIPEI
    client, service, bot, config = system
    config.enable_study_tracking = True
    now = datetime(2026, 10, 5, 12, 10, tzinfo=TAIPEI)
    class Clock:
        @staticmethod
        def now(tz):
            return now
    monkeypatch.setattr(main, 'datetime', Clock)
    async def refresh(db, clock):
        db.save_calendar_snapshot({'source_url': OFFICIAL_CALENDAR_URL,
            'fetched_at': clock.isoformat(), 'content_sha256': 'a' * 64,
            'events': [{'start_date': '2026-10-05', 'end_date': '2026-10-05',
                        'classification': 'no_class', 'text': 'Official closure'}]})
        return {'calendar_sync': 'updated'}
    monkeypatch.setattr('chronos.calendar_sync.sync_calendar', refresh)
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
    response = client.post('/internal/study', headers={'X-Chronos-Scheduler-Secret': 'test-scheduler'})
    assert response.status_code == 200
    assert response.json()['calendar_sync'] == 'updated'
    assert response.json()['course_prompts_suppressed'] is True
    assert response.json()['holiday_notices_sent'] == 1
    assert response.json()['collection_slots_ensured'] == 7
    assert len(main.db.list_study_polls()) == 7
    assert all(state['status']=='queued' for _,state in main.db.list_study_polls())
    repeated = client.post('/internal/study', headers={'X-Chronos-Scheduler-Secret': 'test-scheduler'})
    assert repeated.status_code == 200
    assert len(main.db.list_study_polls()) == 7
    assert main.db.get_course_session('security:2026-10-05') is None
    assert bot.send_message.await_count == 1


def test_web_auth_and_task_lifecycle(system):
    client, service, bot, config = system
    assert client.get('/health').json() == {'status': 'ok'}
    for path in ['/', '/api/tasks']:
        assert client.get(path).status_code == 401
        assert client.get(path, auth=('chronos', 'wrong')).status_code == 401
    assert client.post('/api/tasks/natural', json={'text': '工作'}).status_code == 401
    assert client.post('/api/tasks/1/complete').status_code == 401
    client.auth = ('chronos', 'test-password')
    homepage = client.get('/').text
    assert 'Chronos' in homepage
    assert 'Open tasks' in homepage
    assert 'No open tasks.' in homepage
    assert '>Add</button>' in homepage
    assert '>Complete</button>' in homepage
    assert '<h2>代辦</h2>' not in homepage
    assert '開發專案' not in homepage
    assert client.get('/api/projects').status_code == 404
    assert client.get('/api/tasks').json() == []
    created = client.post('/api/tasks/natural', json={'text': '新增工作'})
    assert created.status_code == 200
    task_id = created.json()['id']
    assert client.get('/api/tasks').json()[0]['title'] == 'Test task'
    assert client.post(f'/api/tasks/{task_id}/complete').status_code == 200
    assert client.get('/api/tasks').json() == []
    assert client.post(f'/api/tasks/{task_id}/complete').status_code == 404
    assert client.post('/api/tasks/natural', json={}).status_code == 422
    main.ai.parse.side_effect = AIError('Test failure')
    assert client.post('/api/tasks/natural', json={'text': '工作'}).status_code == 503
    assert service.list_open() == []
    main.ai.parse.side_effect = ValueError('Empty')
    assert client.post('/api/tasks/natural', json={'text': ''}).status_code == 422


def test_webhook_auth_and_commands(system):
    client, service, bot, config = system
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    update_id = 0
    def send(text, chat_id=123):
        nonlocal update_id
        update_id += 1
        return client.post('/telegram/webhook', headers=headers,
                           json={'update_id': update_id, 'message': {'chat': {'id': chat_id}, 'text': text}})
    assert client.post('/telegram/webhook', json={}).status_code == 403
    assert send('新增工作', 456).status_code == 403
    assert bot.send_message.await_count == 0
    assert client.post('/telegram/webhook', headers=headers, json={'update_id': 0}).status_code == 200
    assert send('/start').status_code == 200
    help_text = bot.send_message.call_args.args[1]
    assert help_text == main.HELP_TEXT
    assert help_text == (
        '<b>Tasks</b>\n'
        '/tasks — list open tasks\n'
        '/add title [#tag] — add exact text without AI or a deadline\n'
        '/done x — complete\n'
        '/edit x ... — edit\n'
        '/edit #id ... — edit a fixed task ID\n'
        '/clear — delete all tasks\n'
        '\n<b>Study</b>\n'
        '/classday ... — confirm a course-specific instruction day\n'
        '/study_budget — inspect AI usage\n'
        '\n<b>Assignments</b>\n'
        '/prepare y — request an editable assignment draft\n'
        '/draft y [page] — read a saved draft without generating\n'
        '\n<b>Notes</b>\n'
        '/notes {course} — list saved study notes\n'
        '/note x {page} — read a saved note\n'
        '/export x — download canonical Markdown'
    )
    assert '/reschedule' not in help_text
    assert '/postpone' not in help_text
    assert '/deadline' not in help_text
    assert '/exam' not in help_text
    assert '/assignment' not in help_text
    assert bot.send_message.call_args.kwargs == {'parse_mode': 'HTML'}
    assert send('/help').status_code == 200
    assert bot.send_message.call_args.args[1] == help_text
    assert bot.send_message.call_args.kwargs == {'parse_mode': 'HTML'}
    assert send('代辦').status_code == 200
    assert bot.send_message.call_args.args[1] == 'Unknown command. Use /help to see available commands.'
    assert bot.send_message.call_args.kwargs == {'parse_mode': None}
    assert service.list_open() == []
    assert send('新增工作').status_code == 200
    assert bot.send_message.call_args.args[1] == '<b>Created</b>\n<b>Test task</b>'
    assert bot.send_message.call_args.kwargs['parse_mode'] == 'HTML'
    assert bot.send_message.call_args.kwargs['reply_markup']['inline_keyboard'][0][0]['callback_data'] == 'task:edit:1'
    assert send('/tasks').status_code == 200
    assert bot.send_message.call_args.args[1] == '<b>Tasks</b>\n\n1. <b>Test task</b>'
    assert bot.send_message.call_args.kwargs['parse_mode'] == 'HTML'
    assert 'reply_markup' not in bot.send_message.call_args.kwargs
    main.ai.edit = AsyncMock(return_value=ParsedTask(
        'Test task', datetime(2026, 9, 20, 10, tzinfo=config.tz)))
    assert send('/edit 1 改到週日').status_code == 200
    assert service.list_open()[0]['due_at'].startswith('2026-09-20T10:00')
    assert bot.send_message.await_args_list[-2].args[1].startswith('<b>Updated · Task 1</b>')
    assert bot.send_message.await_args_list[-1].args[1].startswith('<b>Tasks</b>')
    assert send('/done 1').status_code == 200
    assert bot.send_message.call_args.args[1] == 'Completed: Test task\n\nNo open tasks.'
    assert service.list_open() == []
    assert send('/done 3').status_code == 200
    assert bot.send_message.call_args.args[1] == 'Task 3 not found.\n\nNo open tasks.'
    parse_count = main.ai.parse.await_count
    assert send('/reschedule 4 tomorrow').status_code == 200
    assert bot.send_message.call_args.args[1] == 'Unknown command. Use /help to see available commands.'
    assert main.ai.parse.await_count == parse_count
    service.create('Draft roadmap')
    main.ai.edit = AsyncMock(return_value=ParsedTask(
        'Finalize roadmap', datetime(2026, 10, 3, 18, tzinfo=config.tz), 'Chronos'))
    assert send('/edit 1 改成完成 roadmap 並移到 10/03 18:00').status_code == 200
    assert bot.send_message.await_args_list[-2].args[1].startswith('<b>Updated · Task 1</b>')
    assert bot.send_message.await_args_list[-1].args[1] == (
        '<b>Tasks</b>\n\n'
        '1. 🚨 <b>Finalize roadmap</b>\n'
        '<b>Due:</b> 2026-10-03 18:00\n'
        '<b>Tag:</b> #Chronos')


def test_daily_reminder_schedule(system):
    client, service, bot, config = system
    job = main.scheduler.get_job('daily_tasks')
    assert str(job.trigger.timezone) == 'Asia/Taipei'
    assert job.next_run_time.hour == 8 and job.next_run_time.minute == 0
    service.create('每日提醒測試')
    asyncio.run(main.send_daily_tasks())
    assert bot.send_message.call_args.args[0] == 123
    assert '每日提醒測試' in bot.send_message.call_args.args[1]
    assert bot.send_message.call_args.kwargs == {'parse_mode': 'HTML'}
    bot.send_message.reset_mock()
    config.telegram_chat_id = None
    asyncio.run(main.send_daily_tasks())
    bot.send_message.assert_not_awaited()


def test_failed_edit_is_saved_and_can_be_retried_by_stable_task_id(system):
    client, service, bot, config = system
    original = service.create('Original task')
    main.ai.edit = AsyncMock(side_effect=AIError('provider unavailable'))
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    payload = {'update_id': 70, 'message': {
        'chat': {'id': 123}, 'text': '/edit 1 translate and shorten the title',
    }}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    error_message = bot.send_message.await_args_list[-1]
    assert error_message.args[1] == (
        '<b>Update failed · Task 1</b>\n'
        'provider unavailable\nNo changes were made to Task 1. '
        'Your command has been saved; use the button below to retry it.'
    )
    assert error_message.kwargs['parse_mode'] == 'HTML'
    assert error_message.kwargs['reply_markup']['inline_keyboard'][0][0] == {
        'text': 'Retry editing Task 1', 'callback_data': 'edit:retry:70',
    }
    assert main.db.get_pending_task_edit(70)['task_id'] == original['id']

    main.ai.edit = AsyncMock(return_value=ParsedTask('Short title'))
    callback = {'update_id': 71, 'callback_query': {
        'id': 'retry-70', 'data': 'edit:retry:70',
        'message': {'message_id': 701, 'chat': {'id': 123}},
    }}
    assert client.post('/telegram/webhook', headers=headers, json=callback).status_code == 200
    assert service.list_open()[0]['title'] == 'Short title'
    assert main.db.get_pending_task_edit(70) is None
    assert bot.send_message.await_args_list[-2].args[1].startswith('<b>Updated · Task 1</b>')
    assert bot.send_message.await_args_list[-1].args[1].startswith('<b>Tasks</b>')
    bot.request.assert_awaited_with('editMessageReplyMarkup', {
        'chat_id': 123, 'message_id': 701, 'reply_markup': {'inline_keyboard': []},
    })


def test_deterministic_tag_alias_edit_skips_ai(system):
    client, service, bot, _ = system
    service.create('Review chapter 2', project='computer-architecture')
    main.ai.edit = AsyncMock()
    response = client.post('/telegram/webhook', headers={
        'X-Telegram-Bot-Api-Secret-Token': 'test-hook',
    }, json={'update_id': 72, 'message': {
        'chat': {'id': 123},
        'text': '/edit 1 未來 computer architecture 標籤改用 CA (存入記憶)',
    }})
    assert response.status_code == 200
    main.ai.edit.assert_not_awaited()
    assert service.project_aliases() == {'computer-architecture': 'CA'}
    assert '<b>Tag:</b> #CA' in bot.send_message.await_args_list[-1].args[1]


def test_second_update_message_retries_without_resending_first(system, monkeypatch):
    client, service, bot, _ = system
    service.create('Original task')
    main.ai.edit = AsyncMock(return_value=ParsedTask('Renamed task'))
    edit = Mock(wraps=service.edit)
    monkeypatch.setattr(service, 'edit', edit)
    bot.send_message.side_effect = [
        {'ok': True}, {'ok': False, 'error_code': 500}, {'ok': True},
    ]
    payload = {'update_id': 73, 'message': {
        'chat': {'id': 123}, 'text': '/edit 1 rename it',
    }}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 502
    assert main.db.get_update(73)['delivered_count'] == 1
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert edit.call_count == 1
    assert bot.send_message.await_count == 3
    assert main.db.get_update(73)['delivered'] is True


def test_daily_reminder_reports_delivery_failure(system):
    _, service, bot, _ = system
    service.create('無法投遞測試')
    bot.send_message.return_value = {'ok': False, 'error_code': 403, 'description': 'Forbidden'}
    with pytest.raises(RuntimeError, match='code 403') as error:
        asyncio.run(main.send_daily_tasks())
    assert 'test-token' not in str(error.value)


def test_cloud_scheduler_endpoint(system):
    client, service, bot, _ = system
    service.create('Cloud Scheduler 測試')
    assert client.post('/internal/daily').status_code == 403
    assert client.post('/internal/daily', headers={
        'X-Chronos-Scheduler-Secret': 'wrong'}).status_code == 403
    response = client.post('/internal/daily', headers={
        'X-Chronos-Scheduler-Secret': 'test-scheduler'})
    assert response.status_code == 200
    assert 'Cloud Scheduler 測試' in bot.send_message.call_args.args[1]


def test_persistence_order_and_reschedule(tmp_path):
    db = Database(tmp_path / 'nested' / 'persist.db')
    db.initialize()
    tz = main.settings.tz
    service = TaskService(db, tz)
    no_due = service.create('無期限', project='Chronos')
    later = service.create('較晚', datetime(2026, 9, 20, 10, tzinfo=tz))
    earlier = service.create('較早', datetime(2026, 9, 19, 10, tzinfo=tz))
    db.initialize()
    reopened = TaskService(Database(db.path), tz)
    assert [x['id'] for x in reopened.list_open()] == [earlier['id'], later['id'], no_due['id']]
    formatted = format_tasks(reopened.list_open(), tz, now=datetime(2026, 9, 14, tzinfo=tz))
    assert formatted.startswith('<b>Tasks</b>\n\n1. <b>較早</b>\n<b>Due:</b> 2026-09-19 10:00')
    assert '#Chronos' in formatted
    assert reopened.complete(earlier['id'])
    assert not reopened.postpone(earlier['id'], datetime.now(tz))
    assert not reopened.complete(999)


def test_telegram_transport(monkeypatch):
    original = httpx.AsyncClient
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    bot = TelegramClient('test-token')
    assert asyncio.run(bot.send_message(123, '測試'))['ok']
    assert requests[-1].url.path.endswith('/sendMessage')
    assert b'parse_mode' not in requests[-1].content
    assert asyncio.run(bot.send_message(123, '<b>測試</b>', parse_mode='HTML'))['ok']
    assert json.loads(requests[-1].content)['parse_mode'] == 'HTML'
    keyboard = {'inline_keyboard': [[{'text': 'Delete all tasks', 'callback_data': 'clear:confirm'}]]}
    assert asyncio.run(bot.send_message(123, 'Confirm', reply_markup=keyboard))['ok']
    assert json.loads(requests[-1].content)['reply_markup'] == keyboard
    assert asyncio.run(bot.answer_callback_query('callback-1'))['ok']
    assert requests[-1].url.path.endswith('/answerCallbackQuery')
    assert asyncio.run(bot.set_webhook('https://example.invalid/telegram/webhook', 'test-secret'))['ok']
    webhook_payload = json.loads(requests[-1].content)
    assert webhook_payload['secret_token'] == 'test-secret'
    assert webhook_payload['allowed_updates'] == ['message', 'edited_message', 'callback_query']
    assert not asyncio.run(TelegramClient('').send_message(123, '測試'))['ok']


def test_telegram_errors_do_not_expose_token(monkeypatch):
    original = httpx.AsyncClient

    def rejected(_request):
        return httpx.Response(403, json={'ok': False, 'error_code': 403, 'description': 'Forbidden'})

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(rejected), **kw))
    result = asyncio.run(TelegramClient('secret-token').send_message(123, '測試'))
    assert result == {'ok': False, 'error_code': 403, 'description': 'Forbidden'}
    assert 'secret-token' not in repr(result)

    def failed(_request):
        raise httpx.ConnectError('connection failed')

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(failed), **kw))
    with pytest.raises(TelegramError) as error:
        asyncio.run(TelegramClient('secret-token').send_message(123, '測試'))
    assert 'secret-token' not in str(error.value)


def test_webhook_duplicate_update(system):
    client, service, bot, _ = system
    payload = {'update_id': 12345, 'message': {'chat': {'id': 123}, 'text': '新增工作'}}
    for _ in range(2):
        assert client.post('/telegram/webhook', json=payload, headers={
            'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}).status_code == 200
    assert len(service.list_open()) == 1
    assert bot.send_message.await_count == 1
    assert main.ai.parse.await_count == 1


def test_duplicate_update_survives_database_reopen(system, monkeypatch):
    client, service, bot, config = system
    payload = {'update_id': 45, 'message': {'chat': {'id': 123}, 'text': '新增工作'}}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    assert client.post('/telegram/webhook', json=payload, headers=headers).status_code == 200
    reopened = Database(config.database_path)
    reopened.initialize()
    monkeypatch.setattr(main, 'db', reopened)
    monkeypatch.setattr(main, 'tasks', TaskService(reopened, config.tz))
    assert client.post('/telegram/webhook', json=payload, headers=headers).status_code == 200
    assert len(service.list_open()) == 1
    assert bot.send_message.await_count == 1


@pytest.mark.parametrize('failure', ['exception', 'rejected'])
def test_reply_retry_does_not_repeat_task_change(system, failure):
    client, service, bot, _ = system
    payload = {'update_id': 46, 'message': {'chat': {'id': 123}, 'text': '新增工作'}}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    if failure == 'exception':
        bot.send_message.side_effect = httpx.ReadTimeout('timeout')
    else:
        bot.send_message.return_value = {'ok': False}
    assert client.post('/telegram/webhook', json=payload, headers=headers).status_code >= 500
    assert len(service.list_open()) == 1
    first_reply = bot.send_message.call_args.args[1]
    bot.send_message.side_effect = None
    bot.send_message.return_value = {'ok': True}
    assert client.post('/telegram/webhook', json=payload, headers=headers).status_code == 200
    assert len(service.list_open()) == 1
    assert main.ai.parse.await_count == 1
    assert bot.send_message.call_args.args[1] == first_reply


def test_update_receipt_and_mutation_roll_back_together(system):
    client, service, _, _ = system
    payload = {'update_id': 47, 'message': {'chat': {'id': 123}, 'text': '新增工作'}}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    with main.db.connect() as connection:
        connection.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON telegram_updates "
                           "BEGIN SELECT RAISE(ABORT, 'test disk failure'); END")
    assert client.post('/telegram/webhook', json=payload, headers=headers).status_code == 500
    assert service.list_open() == []
    assert main.db.get_update(47) is None
    with main.db.connect() as connection:
        connection.execute('DROP TRIGGER fail_receipt')
    assert client.post('/telegram/webhook', json=payload, headers=headers).status_code == 200
    assert len(service.list_open()) == 1
def test_concurrent_duplicate_updates(system, monkeypatch):
    _, service, _, _ = system
    async def run():
        both_started = asyncio.Event()
        calls = 0
        async def parse(text):
            nonlocal calls
            calls += 1
            if calls == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=2)
            return ParsedTask('並行測試')
        monkeypatch.setattr(main.ai, 'parse', parse)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
            async def send():
                return await client.post('/telegram/webhook', json={
                    'update_id': 48, 'message': {'chat': {'id': 123}, 'text': '新增工作'}},
                    headers={'X-Telegram-Bot-Api-Secret-Token': 'test-hook'})
            responses = await asyncio.gather(send(), send())
        assert all(response.status_code == 200 for response in responses)
    asyncio.run(run())
    assert len(service.list_open()) == 1


@pytest.mark.parametrize('command', ['done', 'edit'])
def test_duplicate_other_mutations(system, monkeypatch, command):
    client, service, bot, config = system
    service.create('Original task')
    method = {'done': 'complete_position', 'edit': 'edit'}[command]
    action = Mock(wraps=getattr(service, method))
    monkeypatch.setattr(service, method, action)
    if command == 'done':
        text = '/done 1'
    else:
        text = '/edit 1 rename it'
        main.ai.edit = AsyncMock(return_value=ParsedTask('Renamed task'))
    payload = {'update_id': 49, 'message': {'chat': {'id': 123}, 'text': text}}
    for _ in range(2):
        assert client.post('/telegram/webhook', json=payload, headers={
            'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}).status_code == 200
    assert action.call_count == 1
    assert bot.send_message.await_count == (2 if command == 'edit' else 1)


def test_clear_requires_button_confirmation_and_deletes_every_task(system):
    client, service, bot, _ = system
    completed = service.create('Completed')
    service.create('Open')
    assert service.complete(completed['id'])
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    assert client.post('/telegram/webhook', headers=headers, json={
        'update_id': 60, 'message': {'chat': {'id': 123}, 'text': '/clear'}}).status_code == 200
    assert len(service.list_open()) == 1
    assert bot.send_message.call_args.args[1] == main.CLEAR_CONFIRM_TEXT
    assert bot.send_message.call_args.kwargs['reply_markup'] == main.CLEAR_KEYBOARD

    callback = {'update_id': 61, 'callback_query': {
        'id': 'clear-1', 'data': 'clear:confirm', 'message': {'chat': {'id': 123}}}}
    assert client.post('/telegram/webhook', headers=headers, json=callback).status_code == 200
    assert service.list_open() == []
    assert bot.send_message.call_args.args[1] == 'Deleted 2 tasks.\n\nNo open tasks.'
    bot.answer_callback_query.assert_awaited_with('clear-1')
    for _ in range(2):
        assert client.post('/telegram/webhook', headers=headers, json=callback).status_code == 200
    assert bot.send_message.await_count == 2


def test_clear_can_be_cancelled(system):
    client, service, bot, _ = system
    service.create('Keep me')
    response = client.post('/telegram/webhook', headers={
        'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}, json={'update_id': 62, 'callback_query': {
            'id': 'cancel-1', 'data': 'clear:cancel', 'message': {'chat': {'id': 123}}}})
    assert response.status_code == 200
    assert len(service.list_open()) == 1
    assert bot.send_message.call_args.args[1] == 'Clear cancelled.'


@pytest.mark.parametrize('payload', [
    [], None, 'secret-input', 1, {}, {'update_id': True}, {'update_id': '1'},
    {'update_id': 1.0}, {'update_id': -1}, {'update_id': 2**63},
    {'update_id': 1, 'message': []}, {'update_id': 1, 'message': 'wrong'},
    {'update_id': 1, 'message': {}}, {'update_id': 1, 'message': {'chat': []}},
    {'update_id': 1, 'message': {'chat': {'id': '123'}, 'text': '工作'}},
    {'update_id': 1, 'message': {'chat': {'id': True}, 'text': '工作'}},
    {'update_id': 1, 'message': {'chat': {'id': 2**63}, 'text': '工作'}},
    {'update_id': 1, 'message': {'chat': {'id': 123}, 'text': []}},
    {'update_id': 1, 'message': {'chat': {'id': 123}, 'text': 123}},
    {'update_id': 1, 'callback_query': {'data': 'ignored'}},
])
def test_webhook_invalid_shape(system, payload):
    client, service, bot, _ = system
    response = client.post('/telegram/webhook', content=json.dumps(payload), headers={
        'Content-Type': 'application/json',
        'X-Telegram-Bot-Api-Secret-Token': 'test-hook'})
    assert response.status_code == 422
    assert 'secret-input' not in response.text
    assert service.list_open() == []
    main.ai.parse.assert_not_awaited()
    bot.send_message.assert_not_awaited()


@pytest.mark.parametrize('body', [b'{broken', b'', b'\xff'])
def test_webhook_invalid_json(system, body):
    client, service, bot, _ = system
    response = client.post('/telegram/webhook', content=body, headers={
        'Content-Type': 'application/json', 'X-Telegram-Bot-Api-Secret-Token': 'test-hook'})
    assert response.status_code == 400
    assert service.list_open() == []
    main.ai.parse.assert_not_awaited()
    bot.send_message.assert_not_awaited()


@pytest.mark.parametrize('payload', [
    {'update_id': 2, 'message': {'chat': {'id': 123}, 'photo': []}},
    {'update_id': 3, 'message': {'chat': {'id': 123}, 'text': '  '}},
])
def test_webhook_non_text_updates_are_ignored(system, payload):
    client, service, bot, _ = system
    assert client.post('/telegram/webhook', json=payload, headers={
        'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}).status_code == 200
    assert service.list_open() == []
    main.ai.parse.assert_not_awaited()
    bot.send_message.assert_not_awaited()


def test_webhook_checks_secret_before_parsing_and_preserves_retry_id(system):
    client, service, _, _ = system
    assert client.post('/telegram/webhook', content=b'{broken').status_code == 403
    payload = {'update_id': 123, 'message': {'chat': {'id': 123}, 'text': []}}
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    assert client.post('/telegram/webhook', json=payload, headers=headers).status_code == 422
    assert main.db.get_update(123) is None
    payload['message']['text'] = '新增工作'
    assert client.post('/telegram/webhook', json=payload, headers=headers).status_code == 200
    assert len(service.list_open()) == 1
