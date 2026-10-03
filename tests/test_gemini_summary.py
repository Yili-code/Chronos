import json
from types import SimpleNamespace
import httpx
import pytest
from chronos.gemini_summary import GeminiSummary, PROMPT_VERSION
from chronos.summary_pipeline import PdfInput, GenerationRejected
from test_study_notes import payload


def adapter(handler, **options):
    settings = SimpleNamespace(gemini_api_key="synthetic-test-key", gemini_model="test-model",
        gemini_api_base="https://generativelanguage.googleapis.com/v1beta", ai_timeout=5)
    return GeminiSummary(settings, transport=httpx.MockTransport(handler), **options)


def args():
    return dict(progress="chapter", pdfs={"a": PdfInput(b"synthetic-pdf", 2)},
                model="test-model", prompt_version=PROMPT_VERSION)


@pytest.mark.asyncio
async def test_pdf_request_and_validated_response():
    requests = []
    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["contents"][0]["parts"][2]["inlineData"]["mimeType"] == "application/pdf"
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(payload())}]}}]})
    assert await adapter(handler, free_tier_confirmed=True).generate(**args()) == payload()
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_default_gate_prevents_network():
    def handler(request):
        pytest.fail("network must not start")
    with pytest.raises(ValueError):
        await adapter(handler).generate(**args())


@pytest.mark.asyncio
@pytest.mark.parametrize("status,error", [(429, GenerationRejected), (503, RuntimeError), (200, RuntimeError)])
async def test_failure_is_generic_and_never_retried_in_adapter(status, error):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"sensitive_detail": "do-not-echo"})
    with pytest.raises(error) as failure:
        await adapter(handler, free_tier_confirmed=True).generate(**args())
    assert "do-not-echo" not in str(failure.value)
    assert len(calls) == 1

@pytest.mark.asyncio
@pytest.mark.parametrize('status,category',[(400,'invalid_request'),(403,'permission'),(429,'quota_or_rate_limit')])
async def test_rejection_preserves_only_safe_classification(status,category):
    with pytest.raises(GenerationRejected) as failure:
        await adapter(lambda request:httpx.Response(status,json={'error':{'message':'private-token'}}),free_tier_confirmed=True).generate(**args())
    assert failure.value.status_code == status
    assert failure.value.category == category
    assert 'private-token' not in repr(vars(failure.value))
