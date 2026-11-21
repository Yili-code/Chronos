"""Provider contract failures must never become an empty availability window."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chronos.google_calendar import CalendarError, GoogleCalendar
from chronos.settings import Settings

NOW = datetime.fromisoformat('2026-11-03T10:00:00+08:00')


@pytest.mark.parametrize('payload', [
    None, [], {'calendars': []}, {'calendars': {'primary': None}},
    {'calendars': {'primary': {}}}, {'calendars': {'primary': {'busy': None}}},
    {'calendars': {'primary': {'busy': {}}}},
    {'calendars': {'primary': {'busy': [{'start': NOW.isoformat()}]}}},
    {'calendars': {'primary': {'busy': [{'start': '2026-11-03T10:00:00', 'end': '2026-11-03T11:00:00'}]}}},
    {'calendars': {'primary': {'busy': [{'start': NOW.isoformat(), 'end': NOW.isoformat()}]}}},
    {'calendars': {'primary': {'busy': [{'start': (NOW+timedelta(hours=1)).isoformat(), 'end': NOW.isoformat()}]}}},
    {'calendars': {'primary': {'busy': [{'start': 'invalid', 'end': NOW.isoformat()}]}}},
], ids=['null-root','array-root','array-calendars','null-calendar','missing-busy','null-busy',
        'object-busy','missing-end','naive','zero-length','reversed','invalid-time'])
def test_incomplete_availability_fails_closed(payload):
    calendar = GoogleCalendar(Settings(_env_file=None, calendar_account='owner@example.com'))
    calendar.request = AsyncMock(return_value=payload)
    with pytest.raises(CalendarError):
        asyncio.run(calendar.busy(NOW, NOW+timedelta(hours=2)))


def test_agenda_repeated_page_token_fails_without_partial_results():
    calendar = GoogleCalendar(Settings(_env_file=None, calendar_account='owner@example.com'))
    # Stop even on the unfixed implementation, rather than hanging the test.
    calendar.request = AsyncMock(side_effect=[{'items': [], 'nextPageToken': 'loop'}]*3 +
                                [RuntimeError('unbounded pagination')])
    with pytest.raises(CalendarError):
        asyncio.run(calendar.agenda(NOW, NOW+timedelta(days=7)))
    assert calendar.request.await_count == 2


@pytest.mark.parametrize('start,end', [(NOW.replace(tzinfo=None), NOW), (NOW, NOW),
                                     (NOW+timedelta(hours=1), NOW)])
def test_invalid_query_range_never_calls_provider(start, end):
    calendar = GoogleCalendar(Settings(_env_file=None))
    calendar.request = AsyncMock(return_value={'calendars': {'primary': {'busy': []}}})
    with pytest.raises(CalendarError):
        asyncio.run(calendar.busy(start, end))
    calendar.request.assert_not_awaited()
