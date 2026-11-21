"""Calendar protocol integration with an isolated HTTP transport and real client."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from chronos.google_calendar import CalendarError, GoogleCalendar
from test_learning_calendar import NOW, setup
from test_booked_edit_reliability import booked


@pytest.mark.parametrize('mode', ['success', 'no_effect', 'lost_response', 'readback_unavailable', 'rejected_version'])
def test_booked_edit_real_transport_acknowledgement_and_retry(setup, mode):
    planner, task, _, event, original = booked(setup)
    requests, patches = [], []
    unavailable = False
    def handler(request):
        nonlocal unavailable
        requests.append((request.method, request.url.path))
        if request.url.host == 'oauth2.googleapis.com':
            return httpx.Response(200, json={'access_token': 'synthetic-test-token', 'expires_in': 3600})
        assert request.headers['Authorization'] == 'Bearer synthetic-test-token'
        if request.url.path.endswith('/calendars/primary'):
            return httpx.Response(200, json={'id': 'owner@example.com'})
        if request.method == 'GET' and request.url.path.endswith('/events'):
            assert request.url.params['singleEvents'] == 'true'
            return httpx.Response(200, json={'items': [deepcopy(event)]})
        if request.method == 'GET' and request.url.path.endswith('/events/existing'):
            if unavailable:
                unavailable = False
                return httpx.Response(503, json={'error': 'synthetic temporary failure'})
            return httpx.Response(200, json=deepcopy(event))
        assert request.method == 'PATCH' and request.url.path.endswith('/events/existing')
        assert request.headers['If-Match'] == 'v1'
        assert request.url.params['sendUpdates'] == 'none'
        body = json.loads(request.content)
        patches.append(body)
        if mode == 'rejected_version': return httpx.Response(412, json={'error': 'version changed'})
        if mode != 'no_effect':
            event.update(deepcopy(body))
            event['etag'] = 'v2'
        if mode == 'lost_response': raise httpx.ReadTimeout('synthetic lost PATCH response', request=request)
        if mode == 'readback_unavailable': unavailable = True
        return httpx.Response(200, json=deepcopy(event))
    cfg = planner.settings.model_copy(update={'calendar_client_id': 'synthetic-client',
        'calendar_client_secret': 'synthetic-secret', 'calendar_refresh_token': 'synthetic-refresh'})
    planner.calendar = GoogleCalendar(cfg, transport=httpx.MockTransport(handler))
    if mode == 'success':
        reply = asyncio.run(planner.revise(task['id'], 'booked', '10/19 14:30 30', NOW))
        assert 'updated' in reply
    else:
        with pytest.raises(CalendarError):
            asyncio.run(planner.revise(task['id'], 'booked', '10/19 14:30 30', NOW))
        saved = planner.get(task['id'])
        assert saved['start'] == original['start']
        assert saved.get('pending_reschedule')
        assert planner.db.get_task_action_state('learning:day:2026-10-19') == {'booked': 60}
        if mode in {'lost_response', 'readback_unavailable'}:
            reply = asyncio.run(planner.revise(task['id'], 'booked', '10/19 14:30 30', NOW))
            assert 'recovered' in reply
    assert len(patches) == 1
    assert ('POST', '/token') in requests
    if mode in {'success', 'lost_response', 'readback_unavailable'}:
        assert planner.get(task['id'])['start'] == event['start']['dateTime'] == '2026-10-19T14:30:00+08:00'
        assert planner.db.get_task_action_state('learning:day:2026-10-19') == {'booked': 30}
    else:
        assert event['start']['dateTime'] == original['start']
