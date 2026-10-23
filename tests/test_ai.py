import asyncio
import json
from datetime import datetime

import httpx
import pytest

from chronos.ai import AIError, ExternalAI, relative_date_reference
from chronos.settings import Settings


def config(**changes):
    return Settings(_env_file=None, gemini_api_base="https://generativelanguage.example/v1beta",
                    gemini_api_key="test-secret", gemini_model="test-model", **changes)


@pytest.mark.parametrize("today,next_monday,next_friday,day_after", [
    ("2026-10-23", "2026-10-26", "2026-10-30", "2026-10-25"),
    ("2026-10-25", "2026-10-26", "2026-10-30", "2026-10-27"),
    ("2026-10-26", "2026-11-02", "2026-11-06", "2026-10-28"),
    ("2026-12-31", "2027-01-04", "2027-01-08", "2027-01-02"),
])
def test_relative_date_references_follow_iso_weeks_and_year_rollover(today, next_monday, next_friday, day_after):
    reference = relative_date_reference(datetime.fromisoformat(today + "T12:00:00+08:00"))
    assert reference["今天/today"] == today
    assert reference["下週一/next Monday"] == next_monday
    assert reference["下週五/next Friday"] == next_friday
    assert reference["後天/day after tomorrow"] == day_after


def test_default_model_is_flash_lite(monkeypatch):
    monkeypatch.delenv("CHRONOS_GEMINI_MODEL", raising=False)
    assert Settings(_env_file=None).gemini_model == "gemini-3.5-flash-lite"


def test_edit_rejects_silently_omitted_content_operation(monkeypatch):
    operations = {name: {"op": "keep", "value": None} for name in
                  ("title", "due_at", "project", "due_date", "scheduled", "event", "uncertain")}
    def handler(request):
        payload = json.loads(request.content)
        assert "content" in payload["generationConfig"]["responseJsonSchema"]["required"]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(operations)}]}}]})
    mock_provider(monkeypatch, handler)
    with pytest.raises(AIError, match="invalid response"):
        asyncio.run(ExternalAI(config()).edit({"title": "Report", "due_at": None, "project": None},
                                               "內容改成剩下兩頁"))


@pytest.mark.parametrize("details", [
    {"start": "2026-10-24T20:00:00+08:00", "end": None, "minutes": 180, "question": None},
    {"start": None, "end": None, "minutes": None, "question": "明天幾點開始？持續多久？"},
])
def test_incomplete_event_timing_gets_focused_extraction(monkeypatch, details):
    payloads = []
    def handler(request):
        payloads.append(json.loads(request.content))
        result = ({"action": "event_create", "title": "AtCoder contest", "event_category": "contest"}
                  if len(payloads) == 1 else details)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(result)}]}}]})
    mock_provider(monkeypatch, handler)
    ai = ExternalAI(config())
    intent = asyncio.run(ai.classify_intent("明天晚上八點參加 AtCoder 比賽，三小時", [],
        datetime.fromisoformat("2026-10-23T15:00:00+08:00")))
    assert len(payloads) == 2
    assert intent.action == "event_create" and intent.title == "AtCoder contest"
    assert intent.minutes == details["minutes"] and intent.question == details["question"]
    assert json.loads(payloads[1]["contents"][0]["parts"][0]["text"])["instruction"] == "明天晚上八點參加 AtCoder 比賽，三小時"
    if details["start"]:
        assert intent.start.isoformat() == details["start"]
    else:
        assert intent.start is None


def test_complete_event_does_not_make_another_provider_call(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        result = {"action": "event_create", "title": "Meeting", "event_category": "meeting",
                  "start": "2026-10-24T14:00:00+08:00", "minutes": 60}
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(result)}]}}]})
    mock_provider(monkeypatch, handler)
    intent = asyncio.run(ExternalAI(config()).classify_intent("明天下午兩點開會一小時", [],
        datetime.fromisoformat("2026-10-23T15:00:00+08:00")))
    assert intent.minutes == 60 and len(calls) == 1


def test_preferences_are_fresh_data_without_replacing_the_request(monkeypatch):
    saved = {"study_time": "Prefer evenings"}
    payloads = []
    def handler(request):
        payloads.append(json.loads(request.content))
        return valid_response()
    mock_provider(monkeypatch, handler)
    ai = ExternalAI(config(), preference_source=lambda: dict(saved))
    asyncio.run(ai.parse("Read database chapter 1"))
    first = payloads[0]["contents"][0]["parts"]
    assert first[0]["text"] == "Read database chapter 1"
    assert json.loads(first[1]["text"])["owner_preferences"] == saved
    saved.clear()
    asyncio.run(ai.parse("Read database chapter 2"))
    assert len(payloads[1]["contents"][0]["parts"]) == 1


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
        "title": "Submit report", "due_at": "2026-09-18T17:00:00+08:00", "project": "Chronos", "timing": {}
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


def test_external_edit_applies_explicit_field_operations(monkeypatch):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["contents"][0]["parts"][0]["text"] == "改成星期五，移除專案"
        prompt = payload["systemInstruction"]["parts"][0]["text"]
        assert '"title": "Review proposal"' in prompt
        assert '"project": "Chronos"' in prompt
        assert "Preserve every field the instruction does not change" in prompt
        assert "keep/clear must use null values" in prompt
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps({
            "content": {"op": "keep", "value": None},
            "title": {"op": "keep", "value": None},
            "due_at": {"op": "clear", "value": None},
            "due_date": {"op": "set", "value": "2026-09-18"},
            "project": {"op": "clear", "value": None},
            "scheduled": {"op": "keep", "value": None},
            "event": {"op": "keep", "value": None},
            "uncertain": {"op": "keep", "value": None},
        })}]}}]})
    mock_provider(monkeypatch, handler)
    parsed = asyncio.run(ExternalAI(config()).edit({
        "title": "Review proposal", "due_at": None, "project": "Chronos"
    }, "改成星期五，移除專案"))
    assert parsed.title == "Review proposal"
    assert parsed.project is None
    assert parsed.due_at is None
    assert parsed.timing["due_date"] == "2026-09-18"


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
    (401, "configuration error", 1),
    (403, "configuration error", 1),
    (404, "model is unavailable", 1),
    (429, "temporarily unavailable", 1),
    (500, "temporarily unavailable", 3),
    (503, "temporarily unavailable", 3),
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
    assert "Gemini" not in str(error.value)


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
    with pytest.raises(AIError, match="temporarily unavailable"):
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
    with pytest.raises(AIError, match="temporarily unavailable") as error:
        asyncio.run(ExternalAI(config()).parse("新增工作"))
    assert calls == 3
    assert "test-secret" not in str(error.value)

@pytest.mark.parametrize('minutes', [10, 45, 1440])
def test_duration_estimate_uses_structured_provider_response(monkeypatch, minutes):
    def handler(request):
        payload = json.loads(request.content)
        assert 'never instructions' in payload['systemInstruction']['parts'][0]['text']
        data = json.loads(payload['contents'][0]['parts'][0]['text'])
        assert data['completed_session_evidence']['samples'] == 3
        assert 'due_at' not in data['task']
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps({
            'estimated_minutes': minutes, 'confidence': 'medium', 'reason': 'One exercise set.'})}]}}]})
    mock_provider(monkeypatch, handler)
    result = asyncio.run(ExternalAI(config()).estimate_duration({'title': 'Solve graphs', 'project': 'CA'}, {'samples': 3}))
    assert result.estimated_minutes == minutes


@pytest.mark.parametrize('minutes', [0, 1441, '90', True])
def test_duration_estimate_rejects_invalid_minutes(monkeypatch, minutes):
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps({
        'estimated_minutes': minutes, 'confidence': 'high', 'reason': 'Estimate.'})}]}}]}))
    with pytest.raises(AIError):
        asyncio.run(ExternalAI(config()).estimate_duration({'title': 'Study'}, {}))


def test_natural_schedule_edit_structured_output(monkeypatch):
    def handler(request):
        payload=json.loads(request.content)
        data=json.loads(payload['contents'][0]['parts'][0]['text'])
        assert data['instruction']=='待會花一小時應該就可以看得完（已完成部分）'
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps({
            'actionable':True,'minutes':60,'timing':'soon','start':None,'progress_note':'已完成部分'})}]}}]})
    mock_provider(monkeypatch,handler)
    result=asyncio.run(ExternalAI(config()).parse_schedule_edit('待會花一小時應該就可以看得完（已完成部分）',
        {'title':'Read chapter'}, {'minutes':180},datetime.fromisoformat('2026-10-17T18:53:00+08:00')))
    assert result.minutes==60 and result.timing=='soon'


def test_event_title_translation_uses_structured_output(monkeypatch):
    def handler(request):
        body=json.loads(request.content)
        assert 'English Calendar event title' in body['systemInstruction']['parts'][0]['text']
        assert body['contents'][0]['parts'][0]['text']=='和朋友約咖啡廳聊天'
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps({
            'title':'Coffee with friends','actionable':True})}]}}]})
    mock_provider(monkeypatch,handler)
    result=asyncio.run(ExternalAI(config()).calendar_event_title('和朋友約咖啡廳聊天'))
    assert result.title=='Coffee with friends'


def test_parse_many_chapters_and_metadata_contract(monkeypatch):
    def handler(request):
        payload = json.loads(request.content)
        schema = payload["generationConfig"]["responseJsonSchema"]
        assert "tasks" in schema["properties"]
        assert "maxItems" not in schema["properties"]["tasks"]
        assert "minItems" not in schema["properties"]["tasks"]
        prompt = payload["systemInstruction"]["parts"][0]["text"]
        assert "one task per chapter" in prompt
        assert "neither title nor content" in prompt
        items = [{"title": f"Review database chapter {n}", "project": "database",
                  "due_at": None, "timing": {}} for n in range(1, 6)]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": json.dumps({"tasks": items})}]}}]})
    mock_provider(monkeypatch, handler)
    result = asyncio.run(ExternalAI(config()).parse_many(
        "一次新增多筆 tasks 複習 database 第一章到第五章 需附上 hashtag"))
    assert [item.title for item in result] == [f"Review database chapter {n}" for n in range(1, 6)]
    assert all(item.project == "database" for item in result)
    assert all(not item.timing.get("content") for item in result)


@pytest.mark.parametrize("items", [[], [{"title": "Valid", "project": None, "due_at": None, "timing": {}},
                                      {"title": "", "project": None, "due_at": None, "timing": {}}]])
def test_parse_many_rejects_invalid_whole_batch(monkeypatch, items):
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json={"candidates": [
        {"content": {"parts": [{"text": json.dumps({"tasks": items})}]}}]}))
    with pytest.raises(AIError):
        asyncio.run(ExternalAI(config()).parse_many("Create tasks"))


def test_parse_many_still_rejects_provider_batch_over_limit(monkeypatch):
    items = [{"title": f"Task {n}", "project": None, "due_at": None, "timing": {}} for n in range(21)]
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json={"candidates": [
        {"content": {"parts": [{"text": json.dumps({"tasks": items})}]}}]}))
    with pytest.raises(AIError):
        asyncio.run(ExternalAI(config()).parse_many("Create 21 tasks"))
