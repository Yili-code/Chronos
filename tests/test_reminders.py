import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from chronos.ai import ReminderOutput
from chronos import reminders, main
from test_deadline_reminders import store, NOW, bot
from test_system import system
from chronos.settings import Settings

CFG = Settings(_env_file=None, telegram_chat_id=123)


@pytest.mark.parametrize('age', [None, 4, 5])
def test_interrupted_send_never_resends(store, age):
    _, item = create(store)
    now = NOW + timedelta(hours=1)
    def interrupt(old):
        record = {**old[item['token']], 'status': 'sending', 'claim': 'interrupted'}
        if age is not None:
            record['claimed_at'] = (now - timedelta(minutes=age)).isoformat()
        return {**old, item['token']: record}
    store.mutate_task_action_state(reminders.KEY, interrupt)
    telegram = bot()
    assert asyncio.run(reminders.tick(store, telegram, CFG, now)) == {'sent': 0}
    assert store.get_task_action_state(reminders.KEY)[item['token']]['status'] == ('sending' if age == 4 else 'uncertain')
    asyncio.run(reminders.tick(store, telegram, CFG, now + timedelta(minutes=5)))
    assert store.get_task_action_state(reminders.KEY)[item['token']]['status'] == 'uncertain'
    telegram.send_message.assert_not_awaited()


def create(db, busy=False, uid=10, kind='call', minutes=None):
    ai = AsyncMock()
    ai.parse_reminder.return_value = ReminderOutput(actionable=True, title='Call with Kai',
        start=(NOW+timedelta(hours=1)).isoformat(), kind=kind, minutes=minutes)
    cal = AsyncMock()
    cal.busy.return_value = [(NOW, NOW+timedelta(hours=2))] if busy else []
    reply = asyncio.run(reminders.command(db, ai, cal, CFG, '今天下午一點通電話', 123, uid, NOW))
    return reply, next(reversed(db.get_task_action_state(reminders.KEY).values()))


@pytest.mark.parametrize('kind,minutes,expected', [('call',None,30),('meeting',None,30),('call',60,60),('other',None,1)])
def test_duration_and_no_task(store,kind,minutes,expected):
    reply,item=create(store,kind=kind,minutes=minutes)
    from datetime import datetime
    assert datetime.fromisoformat(item['end'])-datetime.fromisoformat(item['start'])==timedelta(minutes=expected)
    assert item['status']=='active'
    assert store.list_open_tasks()==[]
    assert 'Your reminder is set, Sir.' in reply['messages'][0]['text']


def test_notification_only_wording_preserves_reminder_without_creating_todo(store):
    text = '後天上午九點通知我出門，不建待辦'
    start = (NOW + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    ai = AsyncMock()
    ai.parse_reminder.return_value = ReminderOutput(actionable=True, title='Leave home',
        start=start.isoformat(), kind='other', minutes=None)
    calendar = AsyncMock()
    calendar.busy.return_value = []
    asyncio.run(reminders.command(store, ai, calendar, CFG, text, 123, 701, NOW))
    ai.parse_reminder.assert_awaited_once_with(text, NOW)
    items = list(store.get_task_action_state(reminders.KEY).values())
    assert len(items) == 1 and items[0]['start'] == start.isoformat()
    assert items[0]['status'] == 'active'
    assert store.list_open_tasks() == []


def test_overlap_requires_confirmation_and_duplicate_is_safe(store):
    reply,item=create(store,True)
    assert item['status']=='pending'
    assert 'Calendar' in reply['messages'][0]['text']
    assert 'no longer' in reminders.confirm(store,item['token'],'allow',999,NOW)
    telegram=bot()
    assert asyncio.run(reminders.tick(store,telegram,CFG,NOW+timedelta(hours=1)))=={'sent':0}
    assert 'added' in reminders.confirm(store,item['token'],'allow',123,NOW)
    assert asyncio.run(reminders.tick(store,telegram,CFG,NOW+timedelta(hours=1)))=={'sent':1}
    assert asyncio.run(reminders.tick(store,telegram,CFG,NOW+timedelta(hours=1)))=={'sent':0}
    telegram.send_message.assert_awaited_once()


def test_reminder_overlap_cancel_and_expiry(store):
    _,first=create(store)
    _,second=create(store,uid=11)
    assert second['status']=='pending'
    assert 'expired' in reminders.confirm(store,second['token'],'allow',123,NOW+timedelta(minutes=31))
    assert 'cancelled' in reminders.cancel(store,first['token'],123)
    assert asyncio.run(reminders.tick(store,bot(),CFG,NOW+timedelta(hours=2)))=={'sent':0}


def test_failed_calendar_does_not_save(store):
    from chronos.google_calendar import CalendarError
    ai=AsyncMock();ai.parse_reminder.return_value=ReminderOutput(actionable=True,title='Meeting',start=(NOW+timedelta(hours=1)).isoformat(),kind='meeting')
    cal=AsyncMock();cal.busy.side_effect=CalendarError('Unavailable')
    with pytest.raises(CalendarError):
        asyncio.run(reminders.command(store,ai,cal,CFG,'meeting',123,10,NOW))
    assert not store.get_task_action_state(reminders.KEY)


@pytest.mark.parametrize('elapsed', [30, 60])
def test_list_hides_expired_previews_without_writing(store, elapsed):
    _, active = create(store)
    _, pending = create(store, uid=11)
    before = store.get_task_action_state(reminders.KEY)
    ai, calendar = AsyncMock(), AsyncMock()
    reply = asyncio.run(reminders.command(store, ai, calendar, CFG, '/reminders', 123, 12,
        NOW + timedelta(minutes=elapsed)))
    assert active['token'] in reply
    assert pending['token'] not in reply
    assert store.get_task_action_state(reminders.KEY) == before
    ai.parse_reminder.assert_not_awaited()
    calendar.verify_owner.assert_not_awaited()
    assert 'expired' in reminders.confirm(store, pending['token'], 'allow', 123,
        NOW + timedelta(minutes=elapsed))


def test_list_shows_unexpired_preview(store):
    _, pending = create(store, busy=True)
    reply = asyncio.run(reminders.command(store, AsyncMock(), AsyncMock(), CFG,
        '/reminders', 123, 12, NOW + timedelta(minutes=29)))
    assert pending['token'] in reply


def test_unknown_delivery_never_repeats(store):
    from chronos.telegram import TelegramError
    create(store)
    telegram=bot();telegram.send_message.side_effect=TelegramError('Unknown')
    with pytest.raises(RuntimeError):
        asyncio.run(reminders.tick(store,telegram,CFG,NOW+timedelta(hours=1)))
    asyncio.run(reminders.tick(store,telegram,CFG,NOW+timedelta(hours=2)))
    telegram.send_message.assert_awaited_once()


@pytest.mark.parametrize('failure', ['transport', 'rejected'])
def test_failed_reminder_does_not_block_later_delivery(store, failure):
    from chronos.telegram import TelegramError
    _, first = create(store)
    _, second = create(store, uid=11)
    reminders.confirm(store, second['token'], 'allow', 123, NOW)
    telegram = bot()
    telegram.send_message.side_effect = [
        TelegramError('Unknown') if failure == 'transport' else {'ok': False, 'error_code': 400},
        {'ok': True},
    ]
    with pytest.raises(RuntimeError, match='1 reminder deliveries'):
        asyncio.run(reminders.tick(store, telegram, CFG, NOW + timedelta(hours=1)))
    saved = store.get_task_action_state(reminders.KEY)
    assert saved[first['token']]['status'] == ('uncertain' if failure == 'transport' else 'failed')
    assert saved[second['token']]['status'] == 'sent'
    assert telegram.send_message.await_count == 2
    telegram.send_message.side_effect = None
    asyncio.run(reminders.tick(store, telegram, CFG, NOW + timedelta(hours=2)))
    assert telegram.send_message.await_count == 2


def test_webhook_natural_reminder_and_redelivery(system,monkeypatch):
    from datetime import datetime
    client,tasks,telegram,cfg=system
    ai=AsyncMock(return_value=ReminderOutput(actionable=True,title='Call with Kai',start=(datetime.now(cfg.tz)+timedelta(hours=2)).isoformat(),kind='call'))
    monkeypatch.setattr(main.ai,'parse_reminder',ai)
    monkeypatch.setattr(main.google_calendar,'verify_owner',AsyncMock())
    monkeypatch.setattr(main.google_calendar,'busy',AsyncMock(return_value=[]))
    data={'update_id':810,'message':{'message_id':810,'chat':{'id':123},'text':'今天晚上和 Kai 通電話'}}
    for _ in range(2):
        response=client.post('/telegram/webhook',headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'},json=data)
        assert response.status_code==200,response.text
    assert tasks.list_open()==[]
    ai.assert_awaited_once()
    assert len(main.db.get_task_action_state(reminders.KEY))==1


def test_reminder_uses_shared_provider_transport(monkeypatch):
    import httpx, json
    from chronos.ai import ExternalAI
    from test_ai import config, mock_provider
    def handler(request):
        payload=json.loads(request.content)
        assert 'kind' in payload['generationConfig']['responseJsonSchema']['properties']
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps({
            'actionable':True,'title':'Call with Kai','start':(NOW+timedelta(hours=1)).isoformat(),
            'kind':'call','minutes':None})}]}}]})
    mock_provider(monkeypatch,handler)
    parsed=asyncio.run(ExternalAI(config()).parse_reminder('今天下午一點通電話',NOW))
    assert parsed.kind=='call' and parsed.minutes is None


def test_rate_limit_waits_before_retrying(store):
    _, item = create(store)
    now = NOW + timedelta(hours=1)
    telegram = bot()
    telegram.send_message.return_value = {'ok': False, 'error_code': 429, 'parameters': {'retry_after': 120}}
    with pytest.raises(RuntimeError):
        asyncio.run(reminders.tick(store, telegram, CFG, now))
    assert asyncio.run(reminders.tick(store, telegram, CFG, now + timedelta(seconds=60))) == {'sent': 0}
    assert telegram.send_message.await_count == 1
    telegram.send_message.return_value = {'ok': True}
    assert asyncio.run(reminders.tick(store, telegram, CFG, now + timedelta(seconds=120))) == {'sent': 1}
    assert reminders.health_snapshot(store) == {'counts': {'sent': 1}, 'attention_required': 0}


def test_idle_tick_does_not_write(store):
    from unittest.mock import patch
    with patch.object(store, 'mutate_task_action_state', wraps=store.mutate_task_action_state) as mutate:
        assert asyncio.run(reminders.tick(store, bot(), CFG, NOW)) == {'sent': 0}
        mutate.assert_not_called()
