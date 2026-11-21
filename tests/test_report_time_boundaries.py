from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from chronos.evening_agenda import render_agenda
from chronos.executive_reports import intervals, occupied_minutes
from chronos.google_calendar import CalendarError
from chronos.study_reflow import bounds


@pytest.mark.parametrize('start,end,expected',[
    ('2026-03-08T01:30:00-05:00','2026-03-08T03:30:00-04:00',60),
    ('2026-11-01T01:30:00-04:00','2026-11-01T01:15:00-05:00',45),
])
def test_dst_uses_elapsed_minutes(start,end,expected):
    tz=ZoneInfo('America/New_York')
    event={'start':{'dateTime':start},'end':{'dateTime':end},'summary':'DST meeting'}
    day=datetime.fromisoformat(start).date()
    a=datetime.combine(day,datetime.min.time(),tz)
    assert occupied_minutes(intervals([event],a,a+timedelta(days=1),tz))==expected
    assert f'{expected} minutes' in '\n'.join(render_agenda([event],day,tz))


@pytest.mark.parametrize('end',['2026-11-03T11:00:00','2026-11-03T09:00:00+08:00','invalid'])
def test_invalid_provider_event_does_not_become_local_time(end):
    with pytest.raises(CalendarError):
        bounds({'start':{'dateTime':'2026-11-03T10:00:00+08:00'},'end':{'dateTime':end}},ZoneInfo('Asia/Taipei'))


def test_unicode_agenda_parts_respect_utf16_bound():
    a=datetime.fromisoformat('2026-11-03T10:00:00+08:00')
    events=[{'start':{'dateTime':a.isoformat()},'end':{'dateTime':(a+timedelta(hours=1)).isoformat()},
             'summary':'🧠'*500} for _ in range(30)]
    parts=render_agenda(events,a.date(),a.tzinfo)
    assert all(len(part.encode('utf-16-le'))//2<4096 for part in parts)
    assert sum(part.count('🧠') for part in parts)==15000
