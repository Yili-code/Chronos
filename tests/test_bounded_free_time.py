import asyncio
from datetime import datetime,timedelta
from unittest.mock import AsyncMock
from chronos.free_time import bounded_free_message,free_slot_offer
from chronos.planning_holds import save
from chronos import main
from test_intents import store

NOW=datetime.fromisoformat('2026-10-18T14:00:00+08:00')


def test_bounded_free_time_merges_busy_events_and_respects_local_holds(store):
    calendar=AsyncMock()
    calendar.busy.return_value=[(NOW,NOW+timedelta(minutes=30)),(NOW+timedelta(hours=1),NOW+timedelta(hours=1,minutes=30))]
    save(store,'approved',[{'start':(NOW+timedelta(minutes=30)).isoformat(),'end':(NOW+timedelta(minutes=45)).isoformat(),'title':'Reserved','chat_id':123}],NOW)
    text=asyncio.run(bounded_free_message(store,calendar,main.settings,NOW,NOW+timedelta(hours=2),NOW))
    assert '共 45 分鐘' in text and '14:45–15:00' in text and '15:30–16:00' in text
    calendar.insert.assert_not_awaited();calendar.move_event.assert_not_awaited()


def test_past_free_query_never_rolls_to_another_day(store):
    calendar=AsyncMock()
    text=asyncio.run(bounded_free_message(store,calendar,main.settings,NOW-timedelta(hours=2),NOW-timedelta(hours=1),NOW))
    assert '已結束' in text
    calendar.busy.assert_not_awaited()


def test_legacy_two_hour_offer_also_respects_approved_holds(store):
    calendar=AsyncMock();calendar.busy.return_value=[]
    now=NOW.replace(hour=13)
    save(store,'approved',[{'start':now.isoformat(),'end':now.replace(hour=16,minute=30).isoformat(),'title':'Reserved','chat_id':123}],now)
    offer=asyncio.run(free_slot_offer(store,calendar,main.settings,now))
    assert offer['free_slot']['start']==now.replace(hour=18).isoformat()
    assert offer['free_slot']['end']==now.replace(hour=20).isoformat()
