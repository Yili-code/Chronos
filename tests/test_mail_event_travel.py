from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest

from chronos.mail_event_travel import transit_duration, travel_context


@pytest.mark.asyncio
async def test_google_routes_request_uses_arrival_in_utc(monkeypatch):
    import json

    arrival = datetime(2026, 11, 21, 14, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    client_type = httpx.AsyncClient

    def respond(request):
        assert request.url.host == "routes.googleapis.com"
        assert request.headers["X-Goog-Api-Key"] == "test"
        params = json.loads(request.content)
        assert params["arrivalTime"] == "2026-11-21T06:30:00Z"
        assert params["travelMode"] == "TRANSIT"
        assert params["origin"] == {"address": "奇浩聚會所"}
        return httpx.Response(200, json={"routes": [{"duration": "2701s"}]})

    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs))
    assert await transit_duration("test", "奇浩聚會所", "時代基金會", arrival) == timedelta(minutes=46)


@pytest.mark.asyncio
@pytest.mark.parametrize("prior", [False, True])
async def test_transit_origin_and_departure_warning(monkeypatch, prior):
    arrival = datetime(2026, 11, 21, 14, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    settings = SimpleNamespace(google_maps_routes_api_key="test", mail_event_default_origin="奇浩聚會所")
    routes = AsyncMock(return_value=timedelta(minutes=45))
    monkeypatch.setattr("chronos.mail_event_travel.transit_duration", routes)
    events = [({"location": "台北車站"}, arrival - timedelta(hours=1), arrival - timedelta(minutes=20))] if prior else []

    result, depart = await travel_context(settings, {"body": "地點：時代基金會"}, "時代基金會", events, arrival)

    routes.assert_awaited_once_with("test", "台北車站" if prior else "奇浩聚會所", "時代基金會", arrival)
    assert any("13:45 前出發" in row for row in result)
    assert depart == arrival - timedelta(minutes=45)
    assert any("轉場時間不足" in row for row in result) is prior
    assert await travel_context(settings, {"body": "地點未定"}, "未出現在原信的地址", events, arrival) == ([], None)
    routes.assert_awaited_once()
