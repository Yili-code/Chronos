import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from chronos.ai import AIError, ExternalAI
from chronos.gemini_keys import GeminiKeyRouter, configured_keys, retry_delay
from chronos.settings import Settings
from test_ai import config, mock_provider, valid_response


def dual_config(**changes):
    return config(gemini_api_key_secondary='secondary-secret', **changes)


@pytest.mark.parametrize('status', [429, 500, 502, 503, 504, 401, 403])
def test_primary_failure_uses_secondary_once(monkeypatch, caplog, status):
    seen = []
    def handler(request):
        key = request.headers['x-goog-api-key']
        seen.append((key, request.content))
        return httpx.Response(status, json={'error': {'message': 'test-secret secondary-secret'}}) if key == 'test-secret' else valid_response()
    mock_provider(monkeypatch, handler)
    result = asyncio.run(ExternalAI(dual_config()).parse('Synthetic task'))
    assert result.title == 'Submit report'
    assert [key for key, _ in seen] == ['test-secret', 'secondary-secret']
    assert seen[0][1] == seen[1][1]
    assert 'test-secret' not in caplog.text and 'secondary-secret' not in caplog.text


def test_primary_success_does_not_use_secondary(monkeypatch):
    seen = []
    def handler(request):
        seen.append(request.headers['x-goog-api-key'])
        return valid_response()
    mock_provider(monkeypatch, handler)
    asyncio.run(ExternalAI(dual_config()).parse('Synthetic task'))
    assert seen == ['test-secret']


@pytest.mark.parametrize('status', [400, 404, 422])
def test_nonretryable_request_failure_does_not_switch(monkeypatch, status):
    seen = []
    def handler(request):
        seen.append(request.headers['x-goog-api-key'])
        return httpx.Response(status)
    mock_provider(monkeypatch, handler)
    with pytest.raises(AIError):
        asyncio.run(ExternalAI(dual_config()).parse('Synthetic task'))
    assert seen == ['test-secret']


def test_invalid_successful_response_does_not_switch(monkeypatch):
    seen = []
    def handler(request):
        seen.append(request.headers['x-goog-api-key'])
        return httpx.Response(200, json={'candidates': []})
    mock_provider(monkeypatch, handler)
    with pytest.raises(AIError, match='invalid response'):
        asyncio.run(ExternalAI(dual_config()).parse('Synthetic task'))
    assert seen == ['test-secret']


def test_both_keys_exhausted_are_bounded_and_skip_during_cooldown(monkeypatch):
    seen = []
    def handler(request):
        seen.append(request.headers['x-goog-api-key'])
        return httpx.Response(429, headers={'Retry-After': '3600'})
    mock_provider(monkeypatch, handler)
    ai = ExternalAI(dual_config())
    for _ in range(2):
        with pytest.raises(AIError, match='temporarily unavailable'):
            asyncio.run(ai.parse('Synthetic task'))
    assert seen == ['test-secret', 'secondary-secret']


def test_primary_is_probed_again_after_cooldown(monkeypatch):
    import chronos.gemini_keys as keys_module
    clock = [100.0]
    # Patch only the router's clock, not asyncio's shared time module.
    monkeypatch.setattr(keys_module, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    seen = []
    def handler(request):
        key = request.headers['x-goog-api-key']
        seen.append(key)
        return httpx.Response(429, headers={'Retry-After': '120'}) if len(seen) == 1 else valid_response()
    mock_provider(monkeypatch, handler)
    ai = ExternalAI(dual_config())
    asyncio.run(ai.parse('Synthetic task'))
    clock[0] += 61
    asyncio.run(ai.parse('Synthetic task'))
    clock[0] += 60
    asyncio.run(ai.parse('Synthetic task'))
    assert seen == ['test-secret', 'secondary-secret', 'secondary-secret', 'test-secret']


def test_retry_info_preserves_daily_quota_wait():
    response = httpx.Response(429, json={'error': {'details': [
        {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '73466.079s'}]}})
    assert retry_delay(response, 60) == 73466.079
    assert retry_delay(httpx.Response(429, headers={'Retry-After': 'nan'}, text='bad JSON'), 60) == 60


def test_duplicate_keys_are_not_treated_as_independent_projects(monkeypatch):
    settings = dual_config()
    settings.gemini_api_key_secondary = ' test-secret '
    seen = []
    def handler(request):
        seen.append(request.headers['x-goog-api-key'])
        return httpx.Response(429)
    mock_provider(monkeypatch, handler)
    with pytest.raises(AIError):
        asyncio.run(ExternalAI(settings).parse('Synthetic task'))
    assert seen == ['test-secret']


def test_secondary_only_configuration_and_secret_repr(monkeypatch):
    settings = dual_config()
    settings.gemini_api_key = ''
    assert configured_keys(settings) == [('secondary', 'secondary-secret')]
    assert 'secondary-secret' not in repr(settings)
    mock_provider(monkeypatch, lambda request: valid_response())
    assert asyncio.run(ExternalAI(settings).parse('Synthetic task')).title == 'Submit report'


@pytest.mark.asyncio
async def test_slow_primary_leaves_time_for_secondary():
    seen = []
    async def handler(request):
        key = request.headers['x-goog-api-key']
        seen.append(key)
        if key == 'test-secret':
            await asyncio.sleep(5)
        return valid_response()
    settings = dual_config(ai_timeout=0.3)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await GeminiKeyRouter(settings).post(client, 'https://example.invalid/generate', json={})
    assert response.status_code == 200
    assert seen == ['test-secret', 'secondary-secret']


@pytest.mark.asyncio
async def test_summary_failover_reserves_budget_for_each_attempt():
    from chronos.gemini_summary import GeminiSummary
    from chronos.ai_budget import BudgetExceeded
    seen = []
    def handler(request):
        seen.append(request.headers['x-goog-api-key'])
        return httpx.Response(429)
    budget = SimpleNamespace(reserve=lambda *args: {'near_limit': False})
    count = [0]
    def reserve(*args):
        count[0] += 1
        if count[0] == 2:
            raise BudgetExceeded('limit')
        return {'near_limit': False}
    budget.reserve = reserve
    generator = GeminiSummary(dual_config(), transport=httpx.MockTransport(handler), budget=budget)
    with pytest.raises(BudgetExceeded):
        await generator._request({}, 'test-model')
    assert seen == ['test-secret']
    assert count[0] == 2


def test_environment_loads_secondary_key(monkeypatch):
    monkeypatch.setenv('CHRONOS_GEMINI_API_KEY_SECONDARY', 'secondary-env-secret')
    monkeypatch.setenv('CHRONOS_GEMINI_KEY_COOLDOWN_SECONDS', '120')
    settings = Settings(_env_file=None)
    assert settings.gemini_api_key_secondary == 'secondary-env-secret'
    assert settings.gemini_key_cooldown_seconds == 120


def test_explicit_invalid_key_400_can_fail_over(monkeypatch):
    seen = []
    def handler(request):
        seen.append(request.headers['x-goog-api-key'])
        if len(seen) == 1:
            return httpx.Response(400, json={'error': {'details': [{'reason': 'API_KEY_INVALID'}]}})
        return valid_response()
    mock_provider(monkeypatch, handler)
    assert asyncio.run(ExternalAI(dual_config()).parse('Synthetic task')).title == 'Submit report'
    assert seen == ['test-secret', 'secondary-secret']


@pytest.mark.asyncio
async def test_summary_secondary_success_and_review_both_count_against_budget():
    from chronos.gemini_summary import GeminiSummary
    from test_gemini_summary import args
    from test_study_notes import payload
    seen, reservations = [], []
    def handler(request):
        key = request.headers['x-goog-api-key']
        seen.append(key)
        if key == 'test-secret':
            return httpx.Response(429)
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content': {
            'parts': [{'text': json.dumps(payload())}]}}]})
    def reserve(*values):
        reservations.append(values)
        return {'near_limit': False}
    settings = dual_config()
    settings.gemini_api_base = 'https://generativelanguage.googleapis.com/v1beta'
    generator = GeminiSummary(settings, free_tier_confirmed=True, transport=httpx.MockTransport(handler),
                              budget=SimpleNamespace(reserve=reserve))
    result = await generator.generate(**args())
    assert result['concepts']
    assert seen == ['test-secret', 'secondary-secret', 'secondary-secret']
    assert len(reservations) == 3


def test_failover_and_duplicate_receipt_create_only_one_task(tmp_path, monkeypatch):
    from chronos import main
    from chronos.db import Database
    from chronos.tasks import TaskService
    settings = dual_config()
    db = Database(tmp_path / 'tasks.db')
    db.initialize()
    tasks = TaskService(db, settings.tz)
    monkeypatch.setattr(main, 'db', db)
    monkeypatch.setattr(main, 'tasks', tasks)
    monkeypatch.setattr(main, 'ai', ExternalAI(settings))
    seen = []
    def handler(request):
        seen.append(request.headers['x-goog-api-key'])
        return httpx.Response(429) if len(seen) == 1 else valid_response()
    mock_provider(monkeypatch, handler)
    action = asyncio.run(main.prepare_message('Synthetic test report', update_id=880))
    first = db.process_update(880, action)
    assert db.process_update(880, action) == first
    assert len(tasks.list_open()) == 1
    assert seen == ['test-secret', 'secondary-secret']
