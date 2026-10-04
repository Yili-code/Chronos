import json
from types import SimpleNamespace
import httpx
import pytest
from chronos.gemini_summary import GeminiSummary, PROMPT_VERSION
from chronos.summary_pipeline import PdfInput, GenerationRejected
from test_study_notes import payload

def test_provider_schema_preserves_shape_while_local_bounds_remain_strict():
    from chronos.gemini_summary import provider_summary_schema
    from chronos.study_notes import SummaryDraft
    from pydantic import ValidationError
    schema=provider_summary_schema()
    assert set(schema['required'])==set(SummaryDraft.model_fields)
    point=schema['properties']['concepts']['items']
    assert set(point['required'])=={'text','citations'}
    assert set(point['properties']['citations']['items']['required'])=={'source_id','page'}
    inference = schema['properties']['exam_inferences']['items']
    assert 'evidence' in inference['required']
    assert set(inference['properties']['evidence']['items']['required']) == {'source_id', 'page', 'quote'}
    assert '$ref' not in json.dumps(schema)
    assert 'maxLength' not in json.dumps(schema)
    invalid=payload()
    invalid['scope']=[]
    with pytest.raises(ValidationError):
        SummaryDraft.model_validate(invalid)


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["timeout", "transport", "server_response", "invalid_output"])
async def test_uncertain_diagnostics_never_expose_provider_content(category):
    from chronos.gemini_summary import ProviderUncertain
    def handler(request):
        if category == "timeout":
            raise httpx.ReadTimeout("private-token", request=request)
        if category == "transport":
            raise httpx.ConnectError("private-token", request=request)
        return httpx.Response(502 if category == "server_response" else 200,
                              json={"error": "private-token"})
    with pytest.raises(ProviderUncertain) as failure:
        await adapter(handler, free_tier_confirmed=True).generate(**args())
    assert failure.value.category == category
    assert "private-token" not in repr(vars(failure.value))
    assert failure.value.status_code == (502 if category == "server_response" else None)


@pytest.mark.asyncio
async def test_explicit_503_is_retryable_without_inline_retry():
    from chronos.summary_pipeline import GenerationUnavailable
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={"private": "not retained"})
    with pytest.raises(GenerationUnavailable):
        await adapter(handler, free_tier_confirmed=True).generate(**args())
    assert len(calls) == 1


def adapter(handler, **options):
    settings = SimpleNamespace(gemini_api_key="synthetic-test-key", gemini_model="test-model",
        gemini_api_base="https://generativelanguage.googleapis.com/v1beta", ai_timeout=5)
    return GeminiSummary(settings, transport=httpx.MockTransport(handler), **options)


def args():
    return dict(progress="chapter", pdfs={"a": PdfInput(b"synthetic-pdf", 2)},
                model="test-model", prompt_version=PROMPT_VERSION)


@pytest.mark.asyncio
async def test_segment_request_exposes_original_physical_pages():
    from chronos.summary_pipeline import GenerationUnavailable
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(503, json={})
    options = args()
    options['pdfs'] = {'a': PdfInput(b'synthetic-pdf', 2, original_page_start=13)}
    with pytest.raises(GenerationUnavailable):
        await adapter(handler, free_tier_confirmed=True).generate(**options)
    metadata = json.loads(captured[0]['contents'][0]['parts'][1]['text'])
    assert metadata['original_page_start'] == 13
    assert metadata['original_page_end'] == 14
    assert metadata['physical_page_count'] == 2


@pytest.mark.asyncio
async def test_pdf_request_and_validated_response():
    requests = []
    expected = payload()
    for inference in expected['exam_inferences']:
        inference['evidence'] = [{'source_id': 'a', 'page': 2,
                                  'quote': 'Compare resources shared by processes and threads.'}]
    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["contents"][0]["parts"][2]["inlineData"]["mimeType"] == "application/pdf"
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(expected)}]}}]})
    assert await adapter(handler, free_tier_confirmed=True).generate(**args()) == expected
    assert len(requests) == 2
    review = json.loads(requests[1].content)
    assert json.loads(review['contents'][0]['parts'][-1]['text'])['untrusted_draft'] == expected
    assert review['contents'][0]['parts'][2] == json.loads(requests[0].content)['contents'][0]['parts'][2]


@pytest.mark.asyncio
async def test_only_reviewed_draft_is_returned():
    initial = payload()
    corrected = payload()
    corrected['exam_inferences'] = []
    corrected['uncertainties'] = ['僅限本段提供的頁面。']
    responses = [initial, corrected]
    def handler(request):
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content':
            {'parts': [{'text': json.dumps(responses.pop(0))}]}}]})
    assert await adapter(handler, free_tier_confirmed=True).generate(**args()) == corrected
    assert responses == []


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 429, 503])
async def test_failed_review_never_returns_initial_draft_or_retries(status):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content':
                {'parts': [{'text': json.dumps(payload())}]}}]})
        return httpx.Response(status, json={'error': 'private-response-must-not-leak'})
    with pytest.raises((RuntimeError, GenerationRejected)) as failure:
        await adapter(handler, free_tier_confirmed=True).generate(**args())
    assert len(calls) == 2
    assert 'private-response' not in str(failure.value)


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
