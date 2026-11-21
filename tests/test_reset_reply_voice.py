import asyncio
from datetime import datetime

import httpx
import pytest

from chronos import main, reset_conversation as flow, reset_replies as replies, study_reset as core
from chronos.ai import AIError
from chronos.google_calendar import CalendarError
from chronos.tasks import TaskService
from test_learning_calendar import setup, NOW
from test_study_reset import initialize as study_setup
from test_event_reset import initialize as event_setup


@pytest.mark.parametrize('error',[
    AIError('The AI service rejected the request. No study arrangement was changed.'),
    AIError('secret provider diagnostic'), CalendarError('private calendar diagnostic'),
])
def test_failure_reply_keeps_outcome_and_action_without_raw_diagnostics(error):
    text=replies.unchanged(error)
    assert '你的行程沒有變動' in text and '稍後再試' in text
    assert '\n\n' in text
    assert str(error) not in text


def test_partial_reply_never_claims_unchanged_and_hides_operational_error():
    text=core.result({'status':'partial','event_type':'meeting','deleted':['a','b'],'inserted':['c'],
                      'error':'Firestore private diagnostic <secret>'})
    assert '2 個舊行程' in text and '1 個新時段' in text
    assert '還沒有全部完成' in text and '核對已完成的部分' in text
    assert '沒有變動' not in text and 'Firestore' not in text and 'secret' not in text
    assert '重排所有會議行程' in text


def test_success_reply_groups_agenda_and_only_shows_changed_estimates():
    text=core.result({'status':'completed','event_type':'study','factor':0.5,'sessions':[
        {'title':'Second <unit>','start':'2026-10-27T10:00:00+08:00','end':'2026-10-27T11:00:00+08:00','minutes':60,'original_minutes':60},
        {'title':'First unit','start':'2026-10-26T09:00:00+08:00','end':'2026-10-26T09:30:00+08:00','minutes':30,'original_minutes':60},
    ]})
    assert text.startswith('已重新安排好你的讀書行程。')
    assert '10/26（一）' in text and '10/27（二）' in text
    assert text.index('First unit') < text.index('Second &lt;unit&gt;')
    assert text.count('原估 60 分鐘')==1
    assert '部分讀書時間已縮短' in text


@pytest.mark.parametrize('kind',['study','meeting'])
def test_duplicate_webhook_waits_for_original_reset_result_instead_of_saving_busy(setup,monkeypatch,kind):
    initialized=study_setup(setup) if kind=='study' else event_setup(setup)
    planner=initialized[0]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW.astimezone(tz) if tz else NOW
    for module in (main,core,flow):monkeypatch.setattr(module,'datetime',Clock)
    monkeypatch.setattr(main,'settings',planner.settings)
    monkeypatch.setattr(main,'db',planner.db)
    monkeypatch.setattr(main,'tasks',TaskService(planner.db,planner.settings.tz))
    monkeypatch.setattr(main,'learning_calendar',lambda:planner)
    monkeypatch.setattr(main,'telegram',planner.telegram)
    parser=planner.ai.parse_study_reset if kind=='study' else planner.ai.parse_event_reset
    original=parser.side_effect
    output=parser.return_value
    async def check():
        entered=asyncio.Event();release=asyncio.Event()
        async def pause(*args,**kwargs):
            entered.set();await release.wait()
            return await original(*args,**kwargs) if original else output
        parser.side_effect=pause
        text='重排所有讀書行程' if kind=='study' else '重排所有會議行程'
        payload={'update_id':501,'message':{'message_id':1,'chat':{'id':123},'text':text}}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
            first=asyncio.create_task(client.post('/telegram/webhook',json=payload))
            await asyncio.wait_for(entered.wait(),5)
            try:
                duplicate=await client.post('/telegram/webhook',json=payload)
                assert duplicate.status_code==503
                assert planner.db.get_update(501) is None
                planner.telegram.send_message.assert_not_awaited()
            finally:release.set()
            assert (await first).status_code==200
            receipt=planner.db.get_update(501)
            assert '已重新安排好' in receipt['reply'] and '尚待確認' not in receipt['reply']
            calls=planner.calendar.insert.await_count
            assert (await client.post('/telegram/webhook',json=payload)).status_code==200
            assert planner.calendar.insert.await_count==calls
        assert parser.await_count==1
    asyncio.run(check())

@pytest.mark.parametrize('kind',['study','meeting'])
def test_request_bound_lease_protects_duplicate_before_journal_is_saved(setup,kind):
    from hashlib import sha256
    from chronos import event_reset
    planner,*_=setup
    source='study-reset:123:501' if kind=='study' else 'event-reset:meeting:123:501'
    token=sha256(source.encode()).hexdigest()[:32]
    from datetime import timedelta
    planner.db.mutate_task_action_state('learning:tick',lambda old:{'claim':'other-instance',
        'reset_token':token,'until':(NOW+timedelta(minutes=1)).isoformat()})
    with pytest.raises(replies.ResetInProgress):
        asyncio.run(core.run(planner,123,'重排所有讀書行程',501,NOW) if kind=='study' else
                    event_reset.run(planner,123,'meeting','重排所有會議行程',501,NOW))
    assert planner.db.get_task_action_state(core.KEY) is None
    planner.calendar.delete_event.assert_not_awaited()
    planner.calendar.insert.assert_not_awaited()
