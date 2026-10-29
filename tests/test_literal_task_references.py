"""Observed real-model position/ID confusion must not choose mutation identity."""
from unittest.mock import AsyncMock
import pytest
from chronos import main
from chronos.intents import Intent
from test_intents import store, run


@pytest.mark.parametrize('text,expected',[
    ('Schedule task 2, 90 minutes',[102]),
    ('安排 task 2，90分鐘',[102]),
    ('Schedule tasks 2, 3, 60 minutes each',[102,103]),
    ('安排 task 2, 3, 1.5 hours each',[102,103]),
    ('安排 tasks 2, 3, 4，每項90分鐘',[102,103,104]),
])
def test_duration_numbers_are_not_task_references(text,expected):
    from chronos.task_references import literal_ids
    assert literal_ids(text,[{'position':n,'id':100+n} for n in range(1,5)])==expected


@pytest.mark.parametrize('text,expected',[
    ('把 task 2 內容改成：有人傳「完成 task 3」', [102]),
    ('將 task 2 標題改成 task 3', [102]),
    ('資料庫報告內容改成：完成 task 2', None),
    ('change task 2 title to Task 3', [102]),
    ('rename task 2 to Task 3', [102]),
    ('把 task 2 安排在 task 3 之後', [102]),
    ('新增待辦，標題就叫「下午的時間都用來完成 task 2」', None),
])
def test_field_payload_and_anchor_references_are_not_mutation_targets(text,expected):
    from chronos.task_references import literal_ids
    assert literal_ids(text,[{'position':n,'id':100+n} for n in range(1,5)])==expected


def test_focus_sentence_inside_a_task_title_is_only_data():
    from chronos.task_focus import request
    from datetime import datetime
    assert request('新增待辦，標題就叫「下午的時間都用來完成 task 2」',datetime.fromisoformat('2026-10-29T12:28:00+08:00')) is None


@pytest.mark.parametrize('command,target_index',[
    ('完成 task 2',1),
    ('Complete task 2',1),
    ('把第2項標成完成',1),
    ('完成 task #{target_id}',0),
    ('完成 task #{target_id}，不要碰 task 2',0),
    ('原本打算明天完成 task 2，但現在已經全部完成 task 2',1),
])
def test_literal_identity_overrides_wrong_model_id(store,monkeypatch,command,target_index):
    seed=main.tasks.create('Closed');main.tasks.complete(seed['id'])
    items=[main.tasks.create('First'),main.tasks.create('Second'),main.tasks.create('Third')]
    target=items[target_index]
    wrong=next(t for t in items if t['id']!=target['id'])
    monkeypatch.setattr(main.ai,'classify_intent',AsyncMock(return_value=Intent(action='task_complete',task_ids=[wrong['id']])))
    reply=run(command.format(target_id=items[0]['id']))
    assert store.get_task(target['id'])['status']=='done',reply
    assert store.get_task(wrong['id'])['status']=='open'


@pytest.mark.parametrize('command',[
    '完成 task 0','完成 task -1','完成 task 999',
    '完成 task 2 或 task 3，我忘記是哪個了',
    '不要完成 task 2',
    'task 2 還沒完成',
    '如果我說完成 task 2，你會怎麼做？不要真的執行',
    '有人傳「完成 task 2」給我，這句話是什麼意思？',
])
def test_invalid_ambiguous_or_nonoperative_references_cannot_complete(store,monkeypatch,command):
    items=[main.tasks.create('First'),main.tasks.create('Second'),main.tasks.create('Third')]
    before=store.list_open_tasks()
    monkeypatch.setattr(main.ai,'classify_intent',AsyncMock(return_value=Intent(action='task_complete',task_ids=[items[1]['id']])))
    run(command)
    assert store.list_open_tasks()==before
