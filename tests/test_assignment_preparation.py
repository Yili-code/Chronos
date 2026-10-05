from datetime import datetime
from types import SimpleNamespace
import json

import httpx
import pytest
from pydantic import ValidationError

from chronos.assignment_preparation import PreparationDraft, generate_preparation, render_preparation
from chronos.assignments import Assignment
from chronos.course_tracking import TAIPEI
from chronos.gemini_summary import GeminiSummary


def payload(kind='report'):
    return dict(kind=kind, requirements=['Explain algorithm'], plan=['Outline'], draft='Editable draft',
                supporting_points=['Discuss complexity'], test_plan=['Check empty input'], edge_cases=['Empty input'],
                verification_needed=['Verify references'], owner_checklist=['Review and test'], assumptions=[])


def test_required_sections_and_honest_rendering():
    draft = PreparationDraft(**payload())
    assert '未提交、未執行' in render_preparation(draft)
    with pytest.raises(ValidationError):
        PreparationDraft(**{**payload('code'), 'test_plan': []})
    with pytest.raises(ValidationError):
        PreparationDraft(**{**payload(), 'supporting_points': []})


@pytest.mark.asyncio
async def test_preparation_uses_bounded_provider_without_submission():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP',
            'content': {'parts': [{'text': json.dumps(payload())}]}}]})
    config = SimpleNamespace(gemini_api_key='test', gemini_model='test-model', ai_timeout=5,
        gemini_api_base='https://generativelanguage.googleapis.com/v1beta')
    provider = GeminiSummary(config, free_tier_confirmed=True, transport=httpx.MockTransport(handler))
    item = Assignment('course', 'source', 'Report', 'Explain algorithm', datetime.now(TAIPEI))
    result = await generate_preparation(provider, item)
    assert result.kind == 'report'
    assert len(calls) == 1
    assert calls[0].url.host == 'generativelanguage.googleapis.com'
    assert json.loads(calls[0].content)['generationConfig']['maxOutputTokens'] == 8192
