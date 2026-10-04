from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
import pytest
from scripts import compare_study_free_models as comparison


@pytest.mark.asyncio
async def test_comparison_records_safe_failures_without_retry_or_default_change(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(comparison, 'PdfStore', lambda path: SimpleNamespace(read=lambda *a, **k: b'pdf'))
    monkeypatch.setattr(comparison, 'isolated_pdf_page_count',
                        lambda data, **kw: ['source'] * 3 if kw.get('extract_text') else b'slice')
    generator = SimpleNamespace(generate=AsyncMock(side_effect=[
        comparison.ProviderRejected(404), comparison.GenerationUnavailable('private-detail')]))
    monkeypatch.setattr(comparison, 'GeminiSummary', lambda *a, **kw: generator)
    previous = comparison.settings.gemini_model
    result = await comparison.run()
    assert result['production_changed'] is False
    assert comparison.settings.gemini_model == previous
    assert generator.generate.await_count == 2
    report = (tmp_path / '.study-data/free-model-comparison-v10/results.json').read_text()
    assert 'private-detail' not in report
    assert [row['http_status'] for row in json.loads(report)] == [404, 503]
    with pytest.raises(FileExistsError):
        await comparison.run()
    assert generator.generate.await_count == 2


@pytest.mark.asyncio
async def test_changed_prompt_does_not_start_comparison(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(comparison, 'PROMPT_VERSION', 'different')
    assert (await comparison.run())['status'] == 'prompt_changed_no_requests'
    assert not (tmp_path / '.study-data').exists()


@pytest.mark.asyncio
async def test_text_control_preserves_source_text_schema_and_original_body(monkeypatch):
    request = AsyncMock(return_value={'reviewed': True})
    monkeypatch.setattr(comparison.GeminiSummary, '_request', request)
    body = {'systemInstruction': {'parts': [{'text': 'same instructions'}]},
            'contents': [{'role': 'user', 'parts': [
                {'text': 'page-scoped source text'}, {'inlineData': {'data': 'synthetic'}}]}],
            'generationConfig': {'responseJsonSchema': {'type': 'object'}}}
    result = await comparison.TextOnlyControl(SimpleNamespace())._request(body, 'test-model')
    assert result == {'reviewed': True}
    sent = request.await_args.args[0]
    assert sent['contents'][0]['parts'] == [{'text': 'page-scoped source text'}]
    assert len(body['contents'][0]['parts']) == 2
    assert sent['generationConfig'] == body['generationConfig']
    assert sent['systemInstruction'] == body['systemInstruction']


@pytest.mark.asyncio
async def test_json_control_keeps_contract_and_does_not_mutate_original(monkeypatch):
    request = AsyncMock(return_value={'reviewed': True})
    monkeypatch.setattr(comparison.GeminiSummary, '_request', request)
    body = {'systemInstruction': {'parts': [{'text': 'rules'}]},
            'contents': [{'parts': [{'text': 'source'}, {'inlineData': {'data': 'pdf'}}]}],
            'generationConfig': {'responseMimeType': 'application/json',
                                 'responseJsonSchema': {'type': 'object'}}}
    await comparison.JsonModeControl(SimpleNamespace())._request(body, 'test-model')
    sent = request.await_args.args[0]
    assert sent['generationConfig'] == {'responseMimeType': 'application/json'}
    assert 'output contract' in sent['systemInstruction']['parts'][-1]['text']
    assert len(body['systemInstruction']['parts']) == 1
    assert 'responseJsonSchema' in body['generationConfig']
