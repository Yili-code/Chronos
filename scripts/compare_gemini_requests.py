"""Three explicitly authorized requests; no retries or raw response persistence."""
import asyncio
import base64
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
import time
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chronos.settings import settings
from chronos.gemini_summary import provider_summary_schema, SYSTEM_PROMPT
from chronos.pdf_store import PdfStore
from chronos.pdf_validation import isolated_pdf_page_count
from chronos.study_notes import SummaryDraft


async def main():
    if sys.argv[1:] != ['--execute-authorized-three-requests']:
        print('disabled')
        return
    if not settings.gemini_api_key or settings.gemini_api_base != 'https://generativelanguage.googleapis.com/v1beta':
        print('configuration_required')
        return
    store = PdfStore(Path('.study-data/pdfs'))
    row = next(r for r in store.catalog('192072') if r['source_id'] == '9714342')
    data = store.read('192072', '9714342', expected_sha256=row['sha256'])
    sliced = isolated_pdf_page_count(data, page_range=(1, 3))
    directory = Path('.study-data/gemini-controlled-comparison-v1')
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'attempt.json').open('x', encoding='utf-8') as output:
        json.dump({'started_at': datetime.now(timezone.utc).isoformat(), 'request_limit': 3,
                   'model': settings.gemini_model}, output)
    synthetic = {'text': 'Source a, physical page 1: An operating system manages hardware resources and provides services to applications. Extract only supported key points; do not invent exam predictions.'}
    pdf_parts = [{'text': 'Source a, physical pages 1 to 3. Actual class progress is unknown.'},
                 {'inlineData': {'mimeType': 'application/pdf', 'data': base64.b64encode(sliced).decode('ascii')}}]
    reports = []
    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
        for name, parts, schema in [('text', [synthetic], False), ('text_schema', [synthetic], True), ('pdf_schema', pdf_parts, True)]:
            body = {'systemInstruction': {'parts': [{'text': SYSTEM_PROMPT}]},
                    'contents': [{'role': 'user', 'parts': parts}]}
            if schema:
                body['generationConfig'] = {'responseMimeType': 'application/json', 'responseJsonSchema': provider_summary_schema()}
            report = {'case': name, 'http_status': None, 'category': 'unknown'}
            start = time.monotonic()
            try:
                response = await client.post(f'{settings.gemini_api_base}/models/{settings.gemini_model}:generateContent',
                    headers={'x-goog-api-key': settings.gemini_api_key}, json=body)
                report['http_status'] = response.status_code
                report['category'] = 'success' if response.is_success else 'http_error'
                if response.is_success:
                    candidate = response.json()['candidates'][0]
                    report['complete'] = candidate.get('finishReason') == 'STOP'
                    if schema:
                        text = ''.join(p.get('text', '') for p in candidate['content']['parts'] if not p.get('thought'))
                        draft = SummaryDraft.model_validate_json(text)
                        draft.validate_sources({'a': 3 if name == 'pdf_schema' else 1})
                        report['structure_and_citations_valid'] = True
                else:
                    # Only a known enum is retained, never a free-text error message.
                    status = response.json().get('error', {}).get('status')
                    if status in {'UNAVAILABLE', 'RESOURCE_EXHAUSTED', 'INVALID_ARGUMENT', 'PERMISSION_DENIED', 'NOT_FOUND', 'INTERNAL'}:
                        report['provider_status'] = status
            except httpx.TimeoutException:
                report['category'] = 'timeout'
            except httpx.HTTPError:
                report['category'] = 'transport'
            except Exception:
                report['category'] = 'response_validation_failed'
            report['elapsed_seconds'] = round(time.monotonic() - start, 2)
            reports.append(report)
            (directory / 'results.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
            print(json.dumps(report), flush=True)


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(main())
    except FileExistsError:
        print('existing_attempt_no_requests_sent')
    except Exception:
        print('preflight_or_checkpoint_failed')
