from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
from zoneinfo import ZoneInfo

from chronos.booked_options import adjustment_card, select_slot
from chronos.settings import Settings


import pytest

@pytest.mark.asyncio
async def test_card_excludes_meeting_and_binds_each_button_to_displayed_time():
    tz = ZoneInfo("Asia/Taipei")
    now = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    cfg = Settings(_env_file=None, telegram_chat_id=123, calendar_account="owner@example.com",
                   learning_daily_minutes=720)
    planner = SimpleNamespace(settings=cfg, db=Mock(), calendar=AsyncMock(), get=Mock(), revise=AsyncMock())
    task = {"id": 42, "title": "Read"}
    state = dict(status="scheduled", minutes=60, token="a"*32, event_id="book",
                 start=(now+timedelta(days=2,hours=15)).isoformat(),
                 end=(now+timedelta(days=2,hours=16)).isoformat())
    planner.get.return_value = state
    planner.db.list_open_tasks.return_value = [task]
    planner.db.get_task_action_state.return_value = None
    start = now + timedelta(hours=9)
    end = start + timedelta(hours=2)
    planner.calendar.agenda.return_value = [dict(id="other", summary="Meeting",
        start={"dateTime": start.isoformat()}, end={"dateTime": end.isoformat()})]
    card = await adjustment_card(planner, task, state, now)
    message = card["messages"][0]
    assert "Meeting" in message["text"]
    binding = message["learning_edit"]
    assert len(binding["choices"]) == len(message["reply_markup"]["inline_keyboard"]) == 3
    choices = [datetime.strptime(x, "%Y-%m-%d %H:%M").replace(tzinfo=tz) for x in binding["choices"]]
    assert all(x >= end or x+timedelta(hours=1) <= start for x in choices)
    assert all(a+timedelta(hours=1) <= b for a,b in zip(choices,choices[1:]))
    planner.db.get_task_action_state.return_value = dict(binding, prompt_message_id=9,
        expires_at=(now+timedelta(days=1)).isoformat())
    await select_slot(planner,123,8,42,state["token"],1,1)
    planner.revise.assert_not_awaited()
    await select_slot(planner,123,9,42,state["token"],1,2)
    assert planner.revise.await_args.args[2] == binding["choices"][1]
