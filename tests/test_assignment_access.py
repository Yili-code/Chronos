import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from chronos import main, assignment_access as access, cloud_assignment_scan
from chronos.ai import AIError
from chronos.assignments import Assignment
from chronos.assignment_scan import run_assignment_scan
from chronos.assignment_scan_state import AssignmentScanState, record_success
from chronos.settings import Settings
from chronos.tronclass_collector import CollectorFailure
from test_intents import store, run, classify
from test_system import system

NOW = datetime.fromisoformat('2026-10-23T15:00:00+08:00')
CFG = Settings(_env_file=None, telegram_chat_id=123, assignment_scan_job='chronos-assignment-scan',
    assignment_scan_project='sample-project')


def bot():
    result = AsyncMock()
    result.send_message.return_value = {'ok': True, 'result': {'message_id': 20}}
    return result


def item(course='189717', source='10'):
    return Assignment(course, source, 'Homework <one>', 'Read chapter', NOW,
        deadline=NOW + timedelta(days=2), deadline_origin='source')


def request(db, update=1, now=NOW):
    return access.request_refresh(db, CFG, 123, update, now)['messages'][0]['assignment_refresh']


def test_query_is_read_only_and_never_claims_empty_source(store):
    before = store.get_assignment_scan_state()
    result = access.query(store, CFG, now=NOW)['messages'][0]
    assert '尚無成功' in result['text'] and '不代表 TronClass' in result['text']
    assert result['reply_markup']['inline_keyboard'][0][0]['callback_data'] == 'assignments:refresh'
    assert store.get_assignment_scan_state() == before
    assert store.get_task_action_state(access.KEY) is None
    assert store.list_open_tasks() == []


def test_query_filters_courses_and_separates_new_and_pending(store):
    store.create_assignment(item())
    store.create_assignment(item('189684', '11'))
    store.save_assignment_scan_state(record_success(AssignmentScanState(), NOW - timedelta(hours=2)).to_record())
    store.mutate_task_action_state(access.OUTCOME, lambda old: {'assignment_scan': 'completed', 'created': 0})
    result = access.query(store, CFG, course='security', now=NOW)['messages'][0]['text']
    assert '新增 0' in result and '未結案</b> 1' in result and '超過一小時' in result
    assert 'Homework &lt;one&gt;' in result and 'Database Systems' not in result
    assert '189717' in result


def test_query_preserves_failure_and_last_success(store):
    previous = record_success(AssignmentScanState(), NOW - timedelta(hours=1)).to_record()
    previous.update(failure_fingerprint='offline', consecutive_failures=1)
    store.save_assignment_scan_state(previous)
    text = access.query(store, CFG, now=NOW)['messages'][0]['text']
    assert '最近一次檢查失敗' in text and '上次成功' in text


def test_refresh_requires_owner_and_configuration(store):
    assert isinstance(access.request_refresh(store, CFG, 456, 1, NOW), str)
    assert isinstance(access.request_refresh(store, CFG, None, 1, NOW), str)
    assert isinstance(access.request_refresh(store, Settings(_env_file=None, telegram_chat_id=123), 123, 1, NOW), str)
    assert store.get_task_action_state(access.KEY) is None


def test_refresh_coalesces_messages_and_dispatches_once(store):
    token = request(store)
    assert request(store, 2) == token
    launcher = AsyncMock()
    telegram = bot()
    asyncio.run(access.dispatch(store, CFG, telegram, token, NOW, launcher))
    asyncio.run(access.dispatch(store, CFG, telegram, token, NOW, launcher))
    launcher.assert_awaited_once_with(CFG)
    assert store.get_task_action_state(access.KEY)['status'] == 'running'


def test_ambiguous_launch_is_not_blindly_repeated(store):
    token = request(store)
    launcher = AsyncMock(side_effect=httpx.TransportError('Do not expose private error'))
    telegram = bot()
    asyncio.run(access.dispatch(store, CFG, telegram, token, NOW, launcher))
    asyncio.run(access.dispatch(store, CFG, telegram, token, NOW, launcher))
    assert store.get_task_action_state(access.KEY)['status'] == 'launch_uncertain'
    launcher.assert_awaited_once()
    assert '尚未確認' in access.query(store, CFG, now=NOW)['messages'][0]['text']


def test_rejected_launch_reports_failure_and_never_exposes_error(store):
    token = request(store)
    telegram = bot()
    launcher = AsyncMock(side_effect=RuntimeError('credential=private'))
    asyncio.run(access.dispatch(store, CFG, telegram, token, NOW, launcher))
    state = store.get_task_action_state(access.KEY)
    assert state['outcome']['assignment_scan'] == 'launch_failed'
    assert '未能啟動' in telegram.send_message.await_args.args[1]
    assert 'private' not in str(state)


@pytest.mark.parametrize('status', ['completed', 'failure_recorded', 'paused_by_calendar', 'runner_failed'])
def test_manual_semantic_result_is_delivered_once(store, status):
    request(store)
    telegram = bot()
    report = {'assignment_scan': status, 'created': 0}
    asyncio.run(access.finish(store, telegram, NOW, report))
    asyncio.run(access.finish(store, telegram, NOW, report))
    telegram.send_message.assert_awaited_once()
    text = telegram.send_message.await_args.args[1]
    assert ('檢查完成' in text) == (status == 'completed')
    if status != 'completed': assert '新增 0' not in text


def test_worker_completion_before_launch_response_is_preserved(store):
    token = request(store)
    telegram = bot()
    async def launching(settings):
        await access.finish(store, telegram, NOW, {'assignment_scan': 'completed', 'created': 2})
    asyncio.run(access.dispatch(store, CFG, telegram, token, NOW, launching))
    state = store.get_task_action_state(access.KEY)
    assert state['status'] == 'completed' and state['outcome']['created'] == 2
    assert '這次新增 2' in state['result_text'] and '本次沒有新增' not in state['result_text']


def test_busy_scan_does_not_falsely_complete_pending_request(store):
    request(store)
    telegram = bot()
    asyncio.run(access.finish(store, telegram, NOW, {'assignment_scan': 'already_running'}))
    assert store.get_task_action_state(access.KEY)['status'] == 'queued'
    telegram.send_message.assert_not_awaited()
    asyncio.run(access.finish(store, telegram, NOW, {'assignment_scan': 'completed', 'created': 0}))
    telegram.send_message.assert_awaited_once()


def test_interactive_zero_scan_uses_one_final_result_not_daily_zero_notice(store):
    request(store)
    telegram = bot()
    report = asyncio.run(run_assignment_scan(store, telegram, 123, NOW, 'test', 'test',
        collect=lambda *args, **kwargs: [], interactive=True))
    assert report['assignment_scan'] == 'completed'
    telegram.send_message.assert_not_awaited()
    asyncio.run(access.finish(store, telegram, NOW, report))
    telegram.send_message.assert_awaited_once()
    assert store.get_task_action_state(access.OUTCOME)['created'] == 0


def test_interactive_first_failure_answers_request_immediately(store):
    request(store)
    telegram = bot()
    def fail(*args, **kwargs): raise CollectorFailure('upstream_offline')
    report = asyncio.run(run_assignment_scan(store, telegram, 123, NOW, 'test', 'test', collect=fail, interactive=True))
    assert report['assignment_scan'] == 'failure_recorded'
    telegram.send_message.assert_not_awaited()
    asyncio.run(access.finish(store, telegram, NOW, report))
    assert '未完成' in telegram.send_message.await_args.args[1]


def test_natural_assignment_question_does_not_create_or_scan(store, monkeypatch):
    classify(monkeypatch, action='assignment_query', assignment_course='security')
    reply = run('資安課還有作業嗎？')
    assert 'TronClass' in reply['reply']
    main.ai.parse.assert_not_awaited()
    assert store.get_task_action_state(access.KEY) is None and store.list_open_tasks() == []


def test_webhook_acknowledges_before_cloud_launch_and_replays_safely(system, monkeypatch):
    client, tasks, telegram, cfg = system
    cfg.assignment_scan_job = 'chronos-assignment-scan'
    cfg.assignment_scan_project = 'sample-project'
    async def launch(settings):
        assert telegram.send_message.await_count == 1
        assert main.db.get_update(900)['delivered']
    launcher = AsyncMock(side_effect=launch)
    monkeypatch.setattr(access, 'launch', launcher)
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    payload = {'update_id': 900, 'message': {'chat': {'id': 123}, 'text': '/refreshassignments'}}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    launcher.assert_awaited_once()
    assert tasks.list_open() == []


def test_cloud_runner_reports_logical_failure_to_request(store, monkeypatch):
    request(store, now=datetime.now(CFG.tz))
    cfg = Settings(_env_file=None, telegram_chat_id=123, telegram_bot_token='test', tronclass_username='test', tronclass_password='test')
    telegram = bot()
    monkeypatch.setattr(cloud_assignment_scan, 'settings', cfg)
    monkeypatch.setattr(cloud_assignment_scan, 'create_database', lambda settings: store)
    monkeypatch.setattr(cloud_assignment_scan, 'TelegramClient', lambda token: telegram)
    scan = AsyncMock(return_value={'assignment_scan': 'failure_recorded', 'consecutive_failures': 1})
    monkeypatch.setattr(cloud_assignment_scan, 'run_assignment_scan', scan)
    asyncio.run(cloud_assignment_scan._main())
    assert scan.await_args.kwargs['interactive'] is True
    assert '未完成' in telegram.send_message.await_args.args[1]


def test_cloud_invoker_uses_fixed_job_and_no_school_credentials(monkeypatch):
    credentials = Mock(token='test-access-token')
    monkeypatch.setattr(access.google.auth, 'default', lambda **kwargs: (credentials, 'sample-project'))
    real = httpx.AsyncClient
    def handler(req):
        assert str(req.url) == 'https://run.googleapis.com/v2/projects/sample-project/locations/asia-east1/jobs/chronos-assignment-scan:run'
        assert req.headers['Authorization'] == 'Bearer test-access-token'
        assert req.content == b'{}'
        return httpx.Response(200, json={'name': 'operation'})
    monkeypatch.setattr(access.httpx, 'AsyncClient', lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    asyncio.run(access.launch(CFG))
    credentials.refresh.assert_called_once()
