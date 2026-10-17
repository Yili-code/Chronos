import asyncio
from datetime import datetime,timedelta
from unittest.mock import AsyncMock

from chronos.free_time import next_two_hours
from chronos.settings import Settings
from chronos import main
from chronos.google_calendar import CalendarError
from test_system import system

NOW=datetime.fromisoformat('2026-10-18T11:00:00+08:00')
CONFIG=Settings(_env_file=None)


def test_next_two_hours_skips_lunch_and_busy_events():
    busy=[(NOW.replace(hour=13),NOW.replace(hour=14,minute=12,second=20))]
    slot=next_two_hours(NOW,busy,CONFIG)
    assert slot==(NOW.replace(hour=14,minute=13),NOW.replace(hour=16,minute=13))


def test_dinner_cannot_be_crossed_and_boundaries_are_allowed():
    assert next_two_hours(NOW.replace(hour=15),[],CONFIG)[1].hour==17
    assert next_two_hours(NOW.replace(hour=16),[],CONFIG)[0].hour==18


def test_night_cutoff_and_whole_week_blocked():
    assert next_two_hours(NOW.replace(hour=20),[],CONFIG)[0].date()>NOW.date()
    assert next_two_hours(NOW,[(NOW,NOW+timedelta(days=7))],CONFIG) is None


def test_fixed_classes_and_calendar_policy_are_respected():
    monday=NOW.replace(day=19,hour=9)
    assert next_two_hours(monday,[],CONFIG)[0].hour==13
    policy={'first_day':'2026-09-07','last_regular_day':'2026-12-18','excluded_dates':['2026-10-19']}
    assert next_two_hours(monday,[],CONFIG,policy)[0]==monday


def test_free2h_command_is_read_only(system,monkeypatch):
    client,tasks,bot,config=system
    calendar=AsyncMock();calendar.busy.return_value=[]
    monkeypatch.setattr(main,'google_calendar',calendar)
    reply=asyncio.run(main.prepare_message('/free2h'))()
    assert 'Next free 2 hours' in reply['messages'][0]['text']
    assert tasks.list_open()==[]
    main.ai.parse.assert_not_awaited()
    calendar.insert.assert_not_awaited()


def test_free2h_calendar_failure_does_not_report_false_availability(system,monkeypatch):
    calendar=AsyncMock();calendar.busy.side_effect=CalendarError('offline')
    monkeypatch.setattr(main,'google_calendar',calendar)
    assert 'unavailable' in asyncio.run(main.prepare_message('/free2h'))()
