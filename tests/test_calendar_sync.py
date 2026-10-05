import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import httpx

from chronos.calendar_sync import sync_calendar, fetch_calendar
from chronos.calendar_snapshot import make_snapshot
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from test_firestore import database as firestore_fake

HTML = '<table><tr><th>辦理事項</th></tr><tr><td>115年</td><td>十月</td><td>（9）國慶日(補假)</td></tr></table>'
NOW = datetime(2026, 10, 5, 6, tzinfo=TAIPEI)


@pytest.fixture(params=['sqlite', 'firestore'])
def db(request, tmp_path, monkeypatch):
    value = firestore_fake(monkeypatch) if request.param == 'firestore' else Database(tmp_path / 'calendar.db')
    value.initialize()
    return value


def test_refresh_once_each_day(db):
    fetch = AsyncMock(return_value=HTML)
    assert asyncio.run(sync_calendar(db, NOW, fetch=fetch))['calendar_sync'] == 'updated'
    assert asyncio.run(sync_calendar(db, NOW + timedelta(minutes=1), fetch=fetch))['calendar_sync'] == 'current'
    assert fetch.await_count == 1
    assert asyncio.run(sync_calendar(db, NOW + timedelta(days=1), fetch=fetch))['calendar_sync'] == 'updated'
    assert fetch.await_count == 2


def test_failure_keeps_evidence_and_caps_daily_attempts(db):
    old = make_snapshot(HTML, NOW - timedelta(days=1))
    db.save_calendar_snapshot(old)
    fetch = AsyncMock(return_value='<html>Broken source</html>')
    for minute in (0, 1, 29, 30, 31, 60, 61, 120):
        asyncio.run(sync_calendar(db, NOW + timedelta(minutes=minute), fetch=fetch))
    assert fetch.await_count == 3
    assert db.get_calendar_snapshot() == old
    asyncio.run(sync_calendar(db, NOW + timedelta(days=1), fetch=fetch))
    assert fetch.await_count == 4


def test_concurrent_ticks_share_a_durable_claim(db):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def fetch():
            entered.set()
            await release.wait()
            return HTML
        first = asyncio.create_task(sync_calendar(db, NOW, fetch=fetch))
        await entered.wait()
        second = await sync_calendar(db, NOW, fetch=fetch)
        release.set()
        await first
        assert second['calendar_sync'] == 'waiting'
    asyncio.run(run())


@pytest.mark.parametrize('status,headers,body', [
    (302, {'location': 'https://example.com/'}, b''),
    (200, {'content-type': 'application/pdf'}, b'%PDF'),
    (200, {'content-type': 'text/html'}, b'x' * 1_000_001),
], ids=['redirect', 'wrong-type', 'oversized'])
def test_fetch_rejects_redirects_wrong_types_and_large_bodies(monkeypatch, status, headers, body):
    real_client = httpx.AsyncClient
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(status, headers=headers, content=body)
    def client(**kwargs):
        return real_client(**kwargs, transport=httpx.MockTransport(respond))
    monkeypatch.setattr('chronos.calendar_sync.httpx.AsyncClient', client)
    with pytest.raises((httpx.HTTPError, ValueError)):
        asyncio.run(fetch_calendar())
    assert len(requests) == 1
    assert requests[0].url.host == 'academic.ntou.edu.tw'
