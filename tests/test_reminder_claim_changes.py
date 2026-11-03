"""Revocation and postponement between a durable claim and delivery."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from chronos import reminders
from chronos.settings import Settings
from test_deadline_reminders import store, NOW, bot
from test_reminders import create


@pytest.mark.parametrize('change', ['cancel','postpone','wrong_chat'])
def test_claim_must_still_match_before_delivery(store, monkeypatch, change):
    _,item=create(store)
    original=store.mutate_task_action_state
    def mutate(key,transition):
        result=original(key,transition)
        if key==reminders.KEY and result[item['token']]['status']=='sending':
            def revise(old):
                state=dict(old[item['token']])
                if change=='cancel':state['status']='cancelled'
                if change=='postpone':state['start']=(NOW+timedelta(days=2)).isoformat()
                if change=='wrong_chat':state['chat_id']=999
                return {**old,item['token']:state}
            original(key,revise)
        return result
    monkeypatch.setattr(store,'mutate_task_action_state',mutate)
    telegram=bot()
    cfg=Settings(_env_file=None,telegram_chat_id=123)
    if change=='cancel':
        assert asyncio.run(reminders.tick(store,telegram,cfg,NOW+timedelta(hours=1)))=={'sent':0}
    else:
        with pytest.raises(RuntimeError,match='uncertain'):
            asyncio.run(reminders.tick(store,telegram,cfg,NOW+timedelta(hours=1)))
        assert store.get_task_action_state(reminders.KEY)[item['token']]['status']=='uncertain'
    telegram.send_message.assert_not_awaited()


def test_one_owner_cannot_receive_another_chat_reminder(store):
    _,item=create(store)
    store.mutate_task_action_state(reminders.KEY,lambda old:{**old,item['token']:{**item,'chat_id':999}})
    telegram=bot()
    assert asyncio.run(reminders.tick(store,telegram,Settings(_env_file=None,telegram_chat_id=123),
                                      NOW+timedelta(hours=1)))=={'sent':0}
    telegram.send_message.assert_not_awaited()
