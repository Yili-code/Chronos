import asyncio
import json
from datetime import datetime

import httpx
import pytest

from chronos.ai import AIError, ExternalAI
from chronos.settings import Settings


def config(**changes):
    return Settings(_env_file=None, ai_base_url="https://ai.example/v1",
                    ai_api_key="test-secret", ai_model="test-model", **changes)


def mock_provider(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(handler), **kw))


def test_external_parse(monkeypatch):
    def handler(request):
        assert str(request.url) == "https://ai.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-secret"
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["messages"][1]["content"] == "明天五點交報告 #Chronos"
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "title": "交報告", "due_at": "2026-09-18T17:00:00+08:00", "project": "Chronos"
        })}}]})
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
        "choices": [{"message": {"content": content}}]}))
    with pytest.raises(AIError, match="格式無效"):
        asyncio.run(ExternalAI(config()).parse("新增工作"))


def test_missing_configuration():
    settings = config()
    settings.ai_api_key = ""
    with pytest.raises(AIError, match="尚未設定"):
        asyncio.run(ExternalAI(settings).parse("新增工作"))


@pytest.mark.parametrize("status", [401, 429, 500])
def test_provider_error_is_safe(monkeypatch, status):
    mock_provider(monkeypatch, lambda request: httpx.Response(status, text="test-secret"))
    with pytest.raises(AIError, match="連線失敗") as error:
        asyncio.run(ExternalAI(config()).parse("新增工作"))
    assert "test-secret" not in str(error.value)


def test_timeout(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)
    mock_provider(monkeypatch, handler)
    with pytest.raises(AIError, match="逾時"):
        asyncio.run(ExternalAI(config()).parse("新增工作"))
