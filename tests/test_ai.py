import asyncio
import json
from datetime import datetime

import httpx
import pytest

from chronos.ai import AIError, ExternalAI
from chronos.settings import Settings


def config(**changes):
    return Settings(_env_file=None, gemini_api_base="https://generativelanguage.example/v1beta",
                    gemini_api_key="test-secret", gemini_model="test-model", **changes)


def mock_provider(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(handler), **kw))


def skip_retry_wait(monkeypatch):
    async def no_wait(_seconds):
        return None
    monkeypatch.setattr("chronos.ai.asyncio.sleep", no_wait)


def valid_response():
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps({
        "title": "交報告", "due_at": "2026-09-18T17:00:00+08:00", "project": "Chronos"
    })}]}}]})


def test_external_parse(monkeypatch):
    def handler(request):
        assert str(request.url) == "https://generativelanguage.example/v1beta/models/test-model:generateContent"
        assert request.headers["x-goog-api-key"] == "test-secret"
        payload = json.loads(request.content)
        assert payload["contents"][0]["parts"][0]["text"] == "明天五點交報告 #Chronos"
        assert payload["generationConfig"]["responseMimeType"] == "application/json"
        return valid_response()
    mock_provider(monkeypatch, handler)
    parsed = asyncio.run(ExternalAI(config()).parse("明天五點交報告 #Chronos"))
    assert parsed.title == "交報告"
    assert parsed.project == "Chronos"
    assert parsed.due_at == datetime.fromisoformat("2026-09-18T17:00:00+08:00")


@pytest.mark.parametrize("content", ["not json", '{}',
    '{"title":"  ","due_at":null,"project":null}',
    '{"title":"test","due_at":"2026-09-18T17:00:00","project":null}'])
def test_invalid_output(monkeypatch, content):
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": content}]}}]}))
    with pytest.raises(AIError, match="格式無效"):
        asyncio.run(ExternalAI(config()).parse("新增工作"))


def test_missing_configuration():
    settings = config()
    settings.gemini_api_key = ""
    with pytest.raises(AIError, match="尚未設定"):
        asyncio.run(ExternalAI(settings).parse("新增工作"))


@pytest.mark.parametrize(("status", "message", "attempts"), [
    (400, "拒絕請求", 1),
    (401, "驗證失敗", 1),
    (403, "驗證失敗", 1),
    (404, "模型不可用", 1),
    (429, "請求受限", 3),
    (500, "暫時繁忙", 3),
    (503, "暫時繁忙", 3),
])
def test_provider_error_is_classified_and_safe(monkeypatch, status, message, attempts):
    calls = 0
    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="test-secret")
    skip_retry_wait(monkeypatch)
    mock_provider(monkeypatch, handler)
    with pytest.raises(AIError, match=message) as error:
        asyncio.run(ExternalAI(config()).parse("新增工作"))
    assert calls == attempts
    assert "test-secret" not in str(error.value)


def test_transient_provider_error_recovers(monkeypatch):
    calls = 0
    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(503) if calls < 3 else valid_response()
    skip_retry_wait(monkeypatch)
    mock_provider(monkeypatch, handler)
    parsed = asyncio.run(ExternalAI(config()).parse("明天五點交報告 #Chronos"))
    assert calls == 3
    assert parsed.title == "交報告"


def test_timeout(monkeypatch):
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)
    skip_retry_wait(monkeypatch)
    mock_provider(monkeypatch, handler)
    with pytest.raises(AIError, match="逾時"):
        asyncio.run(ExternalAI(config()).parse("新增工作"))
    assert calls == 3


def test_transport_error_is_retried(monkeypatch):
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("test-secret", request=request)
    skip_retry_wait(monkeypatch)
    mock_provider(monkeypatch, handler)
    with pytest.raises(AIError, match="網路連線失敗") as error:
        asyncio.run(ExternalAI(config()).parse("新增工作"))
    assert calls == 3
    assert "test-secret" not in str(error.value)
