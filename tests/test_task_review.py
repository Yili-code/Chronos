import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import httpx
import json

from chronos import main, task_review
from chronos.ai import AIError, ExternalAI, TaskReviewItem, TaskReviewOutput
from chronos.tasks import ParsedTask
from test_intents import store, run, classify
from test_system import system
from test_ai import config, mock_provider


def output(task, title='Study network firewalls', course='security'):
    return TaskReviewOutput(items=[TaskReviewItem(task_id=task['id'], title=title,
        course_key=course, reason='主題可能屬於資訊安全課程，請確認。')])


def preview(store, monkeypatch, task, **kwargs):
    model = AsyncMock(return_value=output(task, **kwargs))
    monkeypatch.setattr(main.ai, 'review_tasks', model)
    result = run('/reviewtasks')
    message = result['messages'][1]
    task_review.bind(store, 123, 50, message)
    return message['task_review'], result, model


@pytest.mark.parametrize(('title', 'course'), [
    ('Learn Information Security Management', 'security'),
    ('Learn secure network architecture', 'security'),
    ('Learn secure transport protocol', 'security'),
    ('Study transport security protocols', 'security'),
    ('Learn network firewall', 'security'),
    ('Learn Intrusion Prevention System', 'security'),
    ('Operating System lecture 1', 'operating-systems'),
    ('Learn computer architecture ch3', 'computer-architecture'),
    ('Complete competitive programming assignment', 'competitive-programming'),
    ('Participate in AtCoder contest', None), ('Study systems', None),
    ('Study database systems and software engineering', None),
])
def test_course_inference_is_specific(title, course):
    assert task_review.infer_course({'title': title}) == course


def test_review_changes_nothing_until_bound_confirmation(store, monkeypatch):
    task = main.tasks.create('Learn network firewall', timing={'content': 'Read chapter 4', 'due_date': '2026-12-01'})
    token, result, model = preview(store, monkeypatch, task)
    assert main.tasks.list_open()[0]['title'] == task['title']
    assert main.tasks.list_open()[0]['project'] is None
    assert '#ispm' in result['messages'][1]['text']
    assert '<b>Before</b>' in result['messages'][1]['text']
    main.ai.parse.assert_not_awaited()
    task_review.respond(store, main.tasks, main.settings, 123, 50, token, 'apply')
    updated = main.tasks.list_open()[0]
    assert updated['title'] == 'Study network firewalls' and updated['project'] == 'ispm'
    assert updated['timing'] == task['timing']
    assert '重複' in task_review.respond(store, main.tasks, main.settings, 123, 50, token, 'apply')
    store.undo_task_edit()
    restored = main.tasks.list_open()[0]
    assert restored['title'] == task['title'] and restored['project'] is None


@pytest.mark.parametrize('case', ['cancel', 'expired', 'wrong_chat', 'wrong_message', 'changed', 'completed'])
def test_no_mutation_without_current_owner_confirmation(store, monkeypatch, case):
    task = main.tasks.create('Learn network firewall')
    token, _, _ = preview(store, monkeypatch, task)
    now = datetime.now(main.settings.tz)
    if case == 'changed': main.tasks.edit(task['id'], 'Changed task', None, None, {})
    if case == 'completed': main.tasks.complete(task['id'])
    before = main.tasks.list_open()
    task_review.respond(store, main.tasks, main.settings, 999 if case == 'wrong_chat' else 123,
        999 if case == 'wrong_message' else 50, token, 'cancel' if case == 'cancel' else 'apply',
        now + timedelta(hours=1) if case == 'expired' else now)
    assert main.tasks.list_open() == before


def test_existing_tag_is_preserved_even_when_model_proposes_another_course(store, monkeypatch):
    task = main.tasks.create('Learn network firewall', project='custom')
    token, _, _ = preview(store, monkeypatch, task, course='computer-architecture')
    task_review.respond(store, main.tasks, main.settings, 123, 50, token, 'apply')
    assert main.tasks.list_open()[0]['project'] == 'custom'


def test_new_task_suggests_a_tag_but_does_not_apply_it(store, monkeypatch):
    monkeypatch.setattr(main.ai, 'parse', AsyncMock(return_value=ParsedTask('Study network firewalls')))
    classify(monkeypatch, action='task_create')
    result = run('讀防火牆')
    assert result['messages'][-1]['task_review']
    assert main.tasks.list_open()[0]['project'] is None


def test_review_reuses_known_course_tag(store, monkeypatch):
    main.tasks.create('Known security topic', project='security')
    task = main.tasks.create('Learn network firewall')
    assert task_review.course_project('security', main.tasks.list_open(), {}) == 'security'


@pytest.mark.parametrize(('text', 'expected'), [('讀作業系統第一堂課', None), ('讀作業系統 #os', 'os')])
def test_parser_does_not_save_inferred_course_tags(monkeypatch, text, expected):
    ai = ExternalAI(main.settings)
    monkeypatch.setattr(ai, '_generate', AsyncMock(return_value=ParsedTask('Study Operating Systems — Lecture 1', project='os')))
    parsed = asyncio.run(ai.parse(text))
    assert parsed.project == expected


def test_personal_contest_never_gets_a_course_tag_from_review(store, monkeypatch):
    task = main.tasks.create('Participate in AtCoder contest')
    token, _, _ = preview(store, monkeypatch, task, title='Participate in the AtCoder contest', course='competitive-programming')
    task_review.respond(store, main.tasks, main.settings, 123, 50, token, 'apply')
    assert main.tasks.list_open()[0]['project'] is None


def test_review_provider_contract_uses_real_structured_output(monkeypatch):
    def handler(request):
        payload = json.loads(request.content)
        prompt = payload['systemInstruction']['parts'][0]['text']
        data = json.loads(payload['contents'][0]['parts'][0]['text'])
        assert data['courses']['security'] == '資訊安全實務與管理'
        assert data['tasks'][0]['id'] == 7
        assert 'Use Study' in prompt and 'never apply changes' in prompt
        assert 'course_key' in str(payload['generationConfig']['responseJsonSchema'])
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps({
            'items': [{'task_id': 7, 'title': 'Study network firewalls', 'course_key': 'security',
                       'reason': '可能屬於資安課程，請確認。'}]})}]}}]})
    mock_provider(monkeypatch, handler)
    result = asyncio.run(ExternalAI(config()).review_tasks([{'id': 7, 'title': 'Learn network firewall'}]))
    assert result.items[0].course_key == 'security'


def test_course_scope_control_tasks_are_not_renamed(store, monkeypatch):
    main.tasks.create('Check operating system class scope')
    model = AsyncMock()
    monkeypatch.setattr(main.ai, 'review_tasks', model)
    assert '課程範圍' in run('/reviewtasks')['reply']
    model.assert_not_awaited()


def test_task_list_remains_read_only(store, monkeypatch):
    main.tasks.create('Learn network firewall')
    model = AsyncMock()
    monkeypatch.setattr(main.ai, 'review_tasks', model)
    result = run('/tasks')
    assert result['messages'][0]['reply_markup'] == task_review.OPEN_KEYBOARD
    model.assert_not_awaited()
    assert main.tasks.list_open()[0]['project'] is None


@pytest.mark.parametrize('case', ['provider_failure', 'unknown_id', 'duplicate_id', 'missing_id'])
def test_bad_review_never_applies_changes(store, monkeypatch, case):
    task = main.tasks.create('Learn network firewall')
    reviewed = output(task)
    if case == 'unknown_id': reviewed.items[0].task_id += 100
    if case == 'duplicate_id': reviewed.items.append(reviewed.items[0])
    if case == 'missing_id': main.tasks.create('Another task')
    model = AsyncMock(side_effect=AIError('Unavailable')) if case == 'provider_failure' else AsyncMock(return_value=reviewed)
    monkeypatch.setattr(main.ai, 'review_tasks', model)
    assert '沒有變更' in run('/reviewtasks')['reply']
    assert all(t.get('project') is None for t in main.tasks.list_open())


def test_webhook_preview_binding_confirmation_and_replay(system, monkeypatch):
    client, tasks, bot, cfg = system
    task = tasks.create('Learn network firewall')
    monkeypatch.setattr(main.ai, 'review_tasks', AsyncMock(return_value=output(task)))
    bot.send_message.side_effect = [
        {'ok': True, 'result': {'message_id': 49}},
        {'ok': True, 'result': {'message_id': 50}},
        {'ok': True, 'result': {'message_id': 51}},
    ]
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    assert client.post('/telegram/webhook', headers=headers, json={'update_id': 40,
        'message': {'message_id': 48, 'chat': {'id': 123}, 'text': '/reviewtasks'}}).status_code == 200
    token = main.db.get_update(40)['messages'][1]['task_review']
    assert tasks.list_open()[0]['project'] is None
    payload = {'update_id': 41, 'callback_query': {'id': 'approval', 'data': 'taskreview:apply:' + token,
        'message': {'message_id': 50, 'chat': {'id': 123}, 'text': 'preview'}}}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert tasks.list_open()[0]['project'] == 'ispm'
    assert tasks.list_open()[0]['title'] == 'Study network firewalls'
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert bot.send_message.await_count == 3
    assert bot.send_message.await_args.kwargs['reply_markup']['inline_keyboard'][0][0]['callback_data'].startswith('task:undo:')


def test_review_button_resumes_failed_card_delivery_without_regenerating(system, monkeypatch):
    client, tasks, bot, cfg = system
    task = tasks.create('Learn network firewall')
    model = AsyncMock(return_value=output(task))
    monkeypatch.setattr(main.ai, 'review_tasks', model)
    bot.send_message.side_effect = [
        {'ok': True, 'result': {'message_id': 49}}, {'ok': False},
        {'ok': True, 'result': {'message_id': 50}},
    ]
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'test-hook'}
    payload = {'update_id': 42, 'callback_query': {'id': 'review', 'data': 'taskreview:open',
        'message': {'message_id': 48, 'chat': {'id': 123}, 'text': 'tasks'}}}
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 502
    assert main.db.get_update(42)['delivered_count'] == 1
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    assert client.post('/telegram/webhook', headers=headers, json=payload).status_code == 200
    model.assert_awaited_once()
    assert bot.send_message.await_count == 3
    assert tasks.list_open()[0]['project'] is None
    token = main.db.get_update(42)['messages'][1]['task_review']
    assert main.db.get_task_action_state(task_review.KEY + token)['message_id'] == 50


def test_review_rechecks_snapshots_after_generation(store, monkeypatch):
    task = main.tasks.create('Learn network firewall')
    monkeypatch.setattr(main.ai, 'review_tasks', AsyncMock(return_value=output(task)))
    action = asyncio.run(task_review.prepare(store, main.tasks, main.ai, main.settings, 123))
    main.tasks.edit(task['id'], 'New wording', None, None, {})
    result = store.process_update(80, action)
    assert '清單剛有變更' in result['reply']
    assert main.tasks.list_open()[0]['title'] == 'New wording'
