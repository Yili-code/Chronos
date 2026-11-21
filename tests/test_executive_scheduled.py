import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from chronos.executive_reports import scheduled
from chronos.settings import Settings
from chronos.telegram import TelegramError
from test_deadline_reminders import store, NOW

CFG=Settings(_env_file=None,telegram_chat_id=123)


@pytest.mark.parametrize('mode',['repeat','monday','uncertain','reject','overlap','restart','calendar_failure','disabled'])
def test_durable_report_delivery(store,mode):
    cal,bot=AsyncMock(),AsyncMock()
    cal.agenda.return_value=[]
    bot.enabled=mode!='disabled'
    bot.send_message.return_value={'ok':True}
    now=NOW+timedelta(days=3) if mode=='monday' else NOW  # 2026-10-19 Monday
    if mode=='uncertain':bot.send_message.side_effect=TelegramError('timeout')
    if mode=='reject':bot.send_message.side_effect=[{'ok':False,'error_code':429}]+[{'ok':True}]*20
    if mode=='restart':bot.send_message.side_effect=RuntimeError('simulated process crash')
    if mode=='calendar_failure':
        from chronos.google_calendar import CalendarError
        cal.agenda.side_effect=CalendarError('unavailable')
        with pytest.raises(CalendarError):asyncio.run(scheduled(store,cal,bot,CFG,now=now))
        bot.send_message.assert_not_awaited()
        return
    if mode=='overlap':
        async def send(*args,**kwargs):
            with pytest.raises(RuntimeError):await scheduled(store,cal,bot,CFG,now=now)
            return {'ok':True}
        bot.send_message.side_effect=send
    if mode in {'uncertain','reject','restart'}:
        with pytest.raises(RuntimeError):asyncio.run(scheduled(store,cal,bot,CFG,now=now))
        first=bot.send_message.await_count
        if mode in {'uncertain','restart'}:
            with pytest.raises(RuntimeError):asyncio.run(scheduled(store,cal,bot,CFG,now=now))
            assert bot.send_message.await_count==first
            return
    result=asyncio.run(scheduled(store,cal,bot,CFG,now=now))
    total=bot.send_message.await_count
    assert asyncio.run(scheduled(store,cal,bot,CFG,now=now))=={'sent':0}
    assert bot.send_message.await_count==total
    assert cal.agenda.await_count==(0 if mode=='disabled' else 2 if mode=='monday' else 1)
    if mode!='disabled':assert result['sent']>0
