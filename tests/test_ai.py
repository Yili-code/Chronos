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
        "title": "Submit report", "due_at": "2026-09-18T17:00:00+08:00", "project": "Chronos"
    })}]}}]})


def test_external_parse(monkeypatch):
    def handler(request):
        assert str(request.url) == "https://generativelanguage.example/v1beta/models/test-model:generateContent"
        assert request.headers["x-goog-api-key"] == "test-secret"
        payload = json.loads(request.content)
        assert payload["contents"][0]["parts"][0]["text"] == "明天五點交報告 #Chronos"
        assert payload["generationConfig"]["responseMimeType"] == "application/json"
        prompt = payload["systemInstruction"]["parts"][0]["text"]
        assert "concise, natural English action phrase" in prompt
        assert "lowercase kebab-case" in prompt
        return valid_response()
    mock_provider(monkeypatch, handler)
    parsed = asyncio.run(ExternalAI(config()).parse("明天五點交報告 #Chronos"))
    assert parsed.title == "Submit report"
    assert parsed.project == "Chronos"
    assert parsed.due_at == datetime.fromisoformat("2026-09-18T17:00:00+08:00")


def test_external_edit_returns_complete_final_task(monkeypatch):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["contents"][0]["parts"][0]["text"] == "改成星期五，移除專案"
        prompt = payload["systemInstruction"]["parts"][0]["text"]
        assert '"title": "Review proposal"' in prompt
        assert '"project": "Chronos"' in prompt
        assert "Preserve every field the instruction does not change" in prompt
        assert "must set that field to null" in prompt
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps({
            "title": "Review proposal", "due_at": "2026-09-18T09:00:00+08:00", "project": None
        })}]}}]})
    mock_provider(monkeypatch, handler)
    parsed = asyncio.run(ExternalAI(config()).edit({
        "title": "Review proposal", "due_at": None, "project": "Chronos"
    }, "改成星期五，移除專案"))
    assert parsed.title == "Review proposal"
    assert parsed.project is None
    assert parsed.due_at == datetime.fromisoformat("2026-09-18T09:00:00+08:00")


def test_external_classday_parse_supports_natural_language(monkeypatch):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["contents"][0]["parts"][0]["text"] == "明天軟體工程不上課"
        prompt = payload["systemInstruction"]["parts"][0]["text"]
        assert "course-day decision" in prompt
        assert set(payload["generationConfig"]["responseJsonSchema"]["required"]) == {
            "day", "course_key", "decision"
        }
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps({
            "day": "2026-10-07", "course_key": "software-engineering", "decision": "off",
        }, ensure_ascii=False)}]}}]})
    mock_provider(monkeypatch, handler)
    parsed = asyncio.run(ExternalAI(config()).parse_classday(
        "明天軟體工程不上課",
        datetime.fromisoformat("2026-10-06T12:00:00+08:00"),
    ))
    assert parsed == {
        "day": "2026-10-07", "course_key": "software-engineering", "decision": "off",
    }


def test_external_classday_parse_rejects_incomplete_output(monkeypatch):
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": json.dumps({
            "day": "2026-10-07", "course_key": "software-engineering",
        })}]}}]}))
    with pytest.raises(AIError, match="No class-day decision was saved"):
        asyncio.run(ExternalAI(config()).parse_classday("明天軟體工程不上課"))


def test_progress_summary_is_concise_english_and_preserves_numbers(monkeypatch):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["contents"][0]["parts"][0]["text"] == "第二章到43頁左右"
        prompt = payload["systemInstruction"]["parts"][0]["text"]
        assert "Preserve chapter numbers, page numbers" in prompt
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{
            "text": json.dumps({"summary": "Chapter 2 to around page 43"})
        }]}}]})
    mock_provider(monkeypatch, handler)
    assert asyncio.run(ExternalAI(config()).summarize_progress("第二章到43頁左右")) == (
        "Chapter 2 to around page 43"
    )


@pytest.mark.parametrize("content", ["not json", '{}',
    '{"title":"  ","due_at":null,"project":null}',
    '{"title":"test","due_at":"2026-09-18T17:00:00","project":null}',
    '{"title":"Deploy app","due_at":null,"project":"部署"}',
    '{"title":"Review budget","due_at":null,"project":"personal finance"}'])
def test_invalid_output(monkeypatch, content):
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": content}]}}]}))
    with pytest.raises(AIError, match="invalid response"):
        asyncio.run(ExternalAI(config()).parse("新增工作"))


def test_missing_configuration():
    settings = config()
    settings.gemini_api_key = ""
    with pytest.raises(AIError, match="not configured"):
        asyncio.run(ExternalAI(settings).parse("新增工作"))


@pytest.mark.parametrize(("status", "message", "attempts"), [
    (400, "rejected", 1),
    (401, "authentication failed", 1),
    (403, "authentication failed", 1),
    (404, "model is unavailable", 1),
    (429, "rate-limited", 3),
    (500, "temporarily busy", 3),
    (503, "temporarily busy", 3),
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
    assert parsed.title == "Submit report"


def test_timeout(monkeypatch):
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)
    skip_retry_wait(monkeypatch)
    mock_provider(monkeypatch, handler)
    with pytest.raises(AIError, match="timed out"):
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
    with pytest.raises(AIError, match="could not be reached") as error:
        asyncio.run(ExternalAI(config()).parse("新增工作"))
    assert calls == 3
    assert "test-secret" not in str(error.value)
