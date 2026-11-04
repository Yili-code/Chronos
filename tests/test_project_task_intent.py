from unittest.mock import AsyncMock
import pytest

from chronos import main, task_review
from chronos.tasks import ParsedTask
from chronos.ai import TaskReviewOutput, TaskReviewItem
from test_intents import store, classify, run
from test_system import system


@pytest.mark.parametrize('text,title',[
    ('優化 Mnemosyne','Mnemosyne 優化'),('改善 Kairos','Kairos 改善'),
    ('重構 Chronos','Chronos 重構'),('Optimize Mnemosyne','Mnemosyne optimization'),
    ('Improve the payment API','Payment API improvements'),
    ('優化 Mnemosyne 網站的標題','Mnemosyne 網站標題優化'),
])
def test_named_work_cannot_review_unrelated_tasks(store,monkeypatch,text,title):
    other=main.tasks.create('InnoServe 準備')
    before=store.get_task(other['id'])
    classify(monkeypatch,action='task_review',task_ids=[other['id']],instruction='優化所有任務標題')
    parse=AsyncMock(return_value=[ParsedTask(title)])
    monkeypatch.setattr(main.ai,'parse_many',parse)
    review=AsyncMock(side_effect=AssertionError('Unrelated task review must not run'))
    monkeypatch.setattr(main.ai,'review_tasks',review)
    reply=run(text)
    assert len(main.tasks.list_open())==2
    assert store.get_task(other['id'])==before
    assert not any(m.get('task_review') for m in reply.get('messages',[]))
    assert title in reply['reply']
    assert parse.await_args.args[0]==text
    review.assert_not_awaited()
    store.process_update(1,lambda:pytest.fail('Receipt replay must not run the mutation'))
    assert len(main.tasks.list_open())==2
    assert parse.await_count==1


@pytest.mark.parametrize('text',[
    '優化任務用詞','根據課程建議標籤','Improve my task titles',
])
def test_explicit_task_wording_review_remains_available(store,monkeypatch,text):
    task=main.tasks.create('InnoServe 準備')
    classify(monkeypatch,action='task_review')
    review=AsyncMock(return_value=TaskReviewOutput(items=[TaskReviewItem(task_id=task['id'],
        title=task['title'],course_key=None,reason='Wording is already clear')]))
    monkeypatch.setattr(main.ai,'review_tasks',review)
    run(text)
    review.assert_awaited_once()
    main.ai.parse_many.assert_not_awaited()
    assert len(main.tasks.list_open())==1


@pytest.mark.parametrize('text',[
    '優化任務','優化 Mnemosyne？','不要優化 Mnemosyne','如果我說優化 Mnemosyne',
    'Optimize task 1','Optimize my tasks','優化「Mnemosyne」這句話的意思',
    '不要優化任務標題','如果我說優化任務用詞','優化任務標題？',
])
def test_ambiguous_nonoperative_or_referenced_work_cannot_trigger_global_review(store,monkeypatch,text):
    task=main.tasks.create('InnoServe 準備')
    before=store.get_task(task['id'])
    classify(monkeypatch,action='task_review',task_ids=[task['id']])
    review=AsyncMock()
    monkeypatch.setattr(main.ai,'review_tasks',review)
    run(text)
    main.ai.parse_many.assert_not_awaited()
    review.assert_not_awaited()
    assert main.tasks.list_open()==[before]


def test_bound_project_request_requires_clarification_instead_of_global_review(store,monkeypatch):
    task=main.tasks.create('InnoServe 準備')
    classify(monkeypatch,action='task_review')
    review=AsyncMock()
    monkeypatch.setattr(main.ai,'review_tasks',review)
    run('優化 Mnemosyne',bound_task_id=task['id'])
    main.ai.parse_many.assert_not_awaited()
    review.assert_not_awaited()
    assert len(main.tasks.list_open())==1


def test_webhook_creates_named_project_work_and_replay_preserves_unrelated_task(system,monkeypatch):
    from chronos.intents import Intent
    client,tasks,bot,_=system
    old=tasks.create('InnoServe 準備')
    before=main.db.get_task(old['id'])
    monkeypatch.setattr(main.ai,'classify_intent',AsyncMock(return_value=Intent(action='task_review',task_ids=[old['id']])))
    parse=AsyncMock(return_value=[ParsedTask('Mnemosyne 優化')])
    monkeypatch.setattr(main.ai,'parse_many',parse)
    review=AsyncMock(side_effect=AssertionError('Unrelated review must not run'))
    monkeypatch.setattr(main.ai,'review_tasks',review)
    payload={'update_id':987901,'message':{'message_id':31,'chat':{'id':123},'text':'優化 Mnemosyne'}}
    headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'}
    assert client.post('/telegram/webhook',json=payload,headers=headers).status_code==200
    assert client.post('/telegram/webhook',json=payload,headers=headers).status_code==200
    assert len(tasks.list_open())==2 and main.db.get_task(old['id'])==before
    assert parse.await_count==1
    review.assert_not_awaited()
    receipt=main.db.get_update(987901)
    assert not any(m.get('task_review') for m in receipt.get('messages',[]))
