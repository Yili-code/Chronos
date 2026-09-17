import asyncio
import subprocess
from datetime import datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi.testclient import TestClient

from chronos import main
from chronos.ai import AIError
from chronos.db import Database
from chronos.projects import GitReadError, ProjectService, format_projects
from chronos.settings import Settings
from chronos.tasks import ParsedTask, TaskService, format_tasks
from chronos.telegram import TelegramClient


@pytest.fixture
def system(tmp_path, monkeypatch):
    config = Settings(_env_file=None, database_path=tmp_path / 'test.db',
                      projects_root=tmp_path, telegram_chat_id=123,
                      telegram_webhook_secret='test-hook', web_password='test-password')
    db = Database(config.database_path)
    service = TaskService(db, config.tz)
    bot = TelegramClient('test-token')
    monkeypatch.setattr(bot, 'send_message', AsyncMock(return_value={'ok': True}))
    monkeypatch.setattr(bot, 'set_webhook', AsyncMock(return_value={'ok': True}))
    monkeypatch.setattr(main, 'settings', config)
    monkeypatch.setattr(main, 'db', db)
    monkeypatch.setattr(main, 'tasks', service)
    monkeypatch.setattr(main, 'telegram', bot)
    monkeypatch.setattr(main, 'projects', ProjectService(tmp_path))
    monkeypatch.setattr(main, 'scheduler', AsyncIOScheduler(timezone=config.tz))
    monkeypatch.setattr(main.ai, 'parse', AsyncMock(return_value=ParsedTask('測試工作')))
    with TestClient(main.app, raise_server_exceptions=False) as client:
        yield client, service, bot, config


def test_web_auth_and_task_lifecycle(system):
    client, service, bot, config = system
    assert client.get('/health').json() == {'status': 'ok'}
    for path in ['/', '/api/tasks', '/api/projects']:
        assert client.get(path).status_code == 401
        assert client.get(path, auth=('chronos', 'wrong')).status_code == 401
    assert client.post('/api/tasks/natural', json={'text': '工作'}).status_code == 401
    assert client.post('/api/tasks/1/complete').status_code == 401
    client.auth = ('chronos', 'test-password')
    assert 'Chronos' in client.get('/').text
    assert client.get('/api/tasks').json() == []
    assert client.get('/api/projects').json() == []
    created = client.post('/api/tasks/natural', json={'text': '新增工作'})
    assert created.status_code == 200
    task_id = created.json()['id']
    assert client.get('/api/tasks').json()[0]['title'] == '測試工作'
    assert client.post(f'/api/tasks/{task_id}/complete').status_code == 200
    assert client.get('/api/tasks').json() == []
    assert client.post(f'/api/tasks/{task_id}/complete').status_code == 404
    assert client.post('/api/tasks/natural', json={}).status_code == 422
    main.ai.parse.side_effect = AIError('測試失敗')
    assert client.post('/api/tasks/natural', json={'text': '工作'}).status_code == 503
    assert service.list_open() == []
    main.ai.parse.side_effect = ValueError('空白')
    assert client.post('/api/tasks/natural', json={'text': ''}).status_code == 422


def test_webhook_auth_and_commands(system):
    client, service, bot, config = system
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    def send(text, chat_id=123):
        return client.post('/telegram/webhook', headers=headers,
                           json={'message': {'chat': {'id': chat_id}, 'text': text}})
    assert client.post('/telegram/webhook', json={}).status_code == 403
    assert send('新增工作', 456).status_code == 403
    assert bot.send_message.await_count == 0
    assert client.post('/telegram/webhook', headers=headers, json={}).status_code == 200
    assert send('/start').status_code == 200
    assert '指令' in bot.send_message.call_args.args[1]
    assert send('新增工作').status_code == 200
    task_id = service.list_open()[0]['id']
    assert send('代辦').status_code == 200
    assert '測試工作' in bot.send_message.call_args.args[1]
    main.ai.parse.return_value = ParsedTask('更新期限', datetime(2026, 9, 20, 10, tzinfo=config.tz))
    assert send(f'延期 {task_id} 到週日').status_code == 200
    assert service.list_open()[0]['due_at'].startswith('2026-09-20T10:00')
    assert send(f'完成 {task_id}').status_code == 200
    assert service.list_open() == []
    assert send('專案').status_code == 200
    assert '找不到' in bot.send_message.call_args.args[1]


def test_daily_reminder_schedule(system):
    client, service, bot, config = system
    job = main.scheduler.get_job('daily_tasks')
    assert str(job.trigger.timezone) == 'Asia/Taipei'
    assert job.next_run_time.hour == 8 and job.next_run_time.minute == 0
    service.create('每日提醒測試')
    asyncio.run(main.send_daily_tasks())
    assert bot.send_message.call_args.args[0] == 123
    assert '每日提醒測試' in bot.send_message.call_args.args[1]
    bot.send_message.reset_mock()
    config.telegram_chat_id = None
    asyncio.run(main.send_daily_tasks())
    bot.send_message.assert_not_awaited()


def test_persistence_order_and_postpone(tmp_path):
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
    assert '#Chronos' in format_tasks(reopened.list_open(), tz)
    assert reopened.complete(earlier['id'])
    assert not reopened.postpone(earlier['id'], datetime.now(tz))
    assert not reopened.complete(999)


def test_real_git_scan(tmp_path):
    repo = tmp_path / 'sample'
    repo.mkdir()
    def git(*args):
        subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
    git('init', '-b', 'main')
    git('config', 'user.name', 'Chronos Test')
    git('config', 'user.email', 'test@example.invalid')
    (repo / 'example.txt').write_text('first', encoding='utf-8')
    git('add', '.')
    git('commit', '-m', 'initial')
    service = ProjectService(tmp_path)
    clean = asyncio.run(service.scan())[0]
    assert clean['branch'] == 'main' and not clean['dirty']
    assert 'initial' in clean['last_commit']
    (repo / 'example.txt').write_text('changed', encoding='utf-8')
    dirty = asyncio.run(service.scan())[0]
    assert dirty['dirty'] and dirty['change_count'] == 1
    assert '1 項未 commit' in format_projects([dirty])
    assert asyncio.run(ProjectService(tmp_path / 'missing').scan()) == []


@pytest.mark.parametrize('status', [200, 403, 404, 429, 500])
def test_github_responses(tmp_path, monkeypatch, status):
    original = httpx.AsyncClient
    def handler(request):
        assert request.headers['Authorization'] == 'Bearer test-token'
        return httpx.Response(status, json={'open_issues_count': 3, 'default_branch': 'main'})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    result = asyncio.run(ProjectService(tmp_path, 'test-token')._github_status('owner/repo'))
    assert result.get('open_issues') == 3 if status == 200 else result['error'] == status


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
    assert asyncio.run(bot.set_webhook('https://example.invalid/telegram/webhook', 'test-secret'))['ok']
    assert b'test-secret' in requests[-1].content
    assert not asyncio.run(TelegramClient('').send_message(123, '測試'))['ok']


@pytest.mark.xfail(strict=True, reason='已知問題：相同 update_id 重送會重複新增')
def test_webhook_duplicate_update(system):
    client, service, _, _ = system
    payload = {'update_id': 12345, 'message': {'chat': {'id': 123}, 'text': '新增工作'}}
    for _ in range(2):
        assert client.post('/telegram/webhook', json=payload, headers={
            'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}).status_code == 200
    assert len(service.list_open()) == 1


@pytest.mark.xfail(strict=True, reason='已知問題：非物件 webhook 輸入導致 500')
def test_webhook_invalid_shape(system):
    client, _, _, _ = system
    response = client.post('/telegram/webhook', json=[], headers={
        'X-Telegram-Bot-Api-Secret-Token': 'test-hook'})
    assert response.status_code in (400, 422)


@pytest.mark.xfail(strict=True, reason='已知問題：無法識別名稱含句點的 GitHub repo')
def test_github_dotted_repository():
    assert ProjectService._github_repo('https://github.com/owner/my.repo.git') == 'owner/my.repo'


@pytest.mark.xfail(strict=True, reason='已知問題：GitHub 網路失敗會中斷掃描')
def test_github_offline_is_reported(tmp_path, monkeypatch):
    original = httpx.AsyncClient
    def handler(request):
        raise httpx.ConnectError('offline', request=request)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    result = asyncio.run(ProjectService(tmp_path, 'test')._github_status('owner/repo'))
    assert result.get('error')


def test_failed_git_is_not_clean(tmp_path, monkeypatch):
    service = ProjectService(tmp_path)
    monkeypatch.setattr(service, '_git', AsyncMock(side_effect=GitReadError('讀取被拒絕')))
    result = asyncio.run(service._inspect(tmp_path))
    assert result['error'] == '讀取被拒絕'
    assert result['dirty'] is None and result['change_count'] is None
    assert '乾淨' not in format_projects([result])
    assert '狀態未知' in format_projects([result])


def test_unborn_repository_and_invalid_repository(tmp_path):
    good = tmp_path / 'good'
    good.mkdir()
    subprocess.run(['git', '-C', str(good), 'init', '-b', 'main'], check=True, capture_output=True)
    bad = tmp_path / 'bad'
    bad.mkdir()
    (bad / '.git').mkdir()
    projects = asyncio.run(ProjectService(tmp_path).scan())
    assert len(projects) == 2
    by_name = {p['name']: p for p in projects}
    assert by_name['good']['dirty'] is False
    assert by_name['good']['last_commit'] == '尚無 commit'
    assert by_name['good']['origin'] == ''
    assert by_name['bad']['dirty'] is None
    assert by_name['bad']['error']


def test_git_ownership_error_and_missing_binary(tmp_path, monkeypatch):
    process = AsyncMock()
    process.returncode = 128
    process.communicate.return_value = (b'', b'fatal: detected dubious ownership')
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    service = ProjectService(tmp_path)
    with pytest.raises(GitReadError, match='擁有者'):
        asyncio.run(service._git(tmp_path, 'status', '--porcelain'))
    spawn.side_effect = FileNotFoundError()
    with pytest.raises(GitReadError, match='無法執行'):
        asyncio.run(service._git(tmp_path, 'status', '--porcelain'))
