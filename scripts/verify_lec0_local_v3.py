"""Explicit bounded local-only v3 review; no Telegram imports or delivery."""
import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chronos.settings import settings
from chronos.gemini_summary import GeminiSummary, PROMPT_VERSION, ProviderRejected, ProviderUncertain
from chronos.summary_pipeline import PdfInput, GenerationUnavailable
from chronos.pdf_store import PdfStore
from chronos.pdf_validation import isolated_pdf_page_count
from chronos.study_notes import SummaryDraft, render_note


async def main():
    quality_v5 = sys.argv[1:] == ['--execute-authorized-lite-quality-v5']
    quality_second = sys.argv[1:] == ['--execute-authorized-lite-quality-v4-second']
    quality = quality_second or sys.argv[1:] == ['--execute-authorized-lite-quality-v4']
    lite = sys.argv[1:] == ['--execute-authorized-31-lite']
    second = sys.argv[1:] == ['--execute-authorized-37-second-once']
    alternate = second or sys.argv[1:] == ['--execute-authorized-37-once']
    if quality or quality_v5:
        lite = True
    if (not lite and not alternate and sys.argv[1:] != ['--execute-authorized-local-v3']) or PROMPT_VERSION != ('study-segment-v5' if quality_v5 else 'study-segment-v4' if quality else 'study-segment-v3'):
        print('disabled')
        return
    config = settings.model_copy(update={'ai_timeout':45, **({'gemini_model':'gemini-3.1-flash-lite'} if lite else {'gemini_model':'gemini-3.7-flash'} if alternate else {})})
    limit = 1 if lite or alternate else 3
    directory = Path('.study-data/lec0-lite-quality-v5' if quality_v5 else '.study-data/lec0-lite-quality-v4-second' if quality_second else '.study-data/lec0-lite-quality-v4' if quality else '.study-data/lec0-local-v3-31-lite' if lite else '.study-data/lec0-local-v3-37-second-once' if second else
                     '.study-data/lec0-local-v3-37-once' if alternate else '.study-data/lec0-local-v3')
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'attempt.json').open('x', encoding='utf-8') as output:
        json.dump({'started_at': datetime.now(timezone.utc).isoformat(), 'model': config.gemini_model,
                   'prompt_version': PROMPT_VERSION, 'local_only': True, 'max_attempts_per_segment': limit}, output)
    store = PdfStore(Path('.study-data/pdfs'))
    row = next(r for r in store.catalog('192072') if r['source_id'] == '9714342')
    data = store.read('192072', row['source_id'], expected_sha256=row['sha256'])
    generator = GeminiSummary(config, free_tier_confirmed=True)
    reports = []
    for start, end in (((1,3),(7,9),(10,12),(13,14)) if quality_v5 else ((4,6),) if quality_second else ((1,3),) if quality else ((4,6),) if second else ((1,3),) if alternate else ((1,3), (4,6))):
        sliced = isolated_pdf_page_count(data, page_range=(start,end))
        for attempt in range(1,limit+1):
            report = {'start':start, 'end':end, 'attempt':attempt}
            reports.append(report)
            report['status'] = 'started'
            (directory/'results.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
            try:
                raw = await generator.generate(progress='Explicit local workflow test only; actual class progress unknown.',
                    pdfs={row['source_id']:PdfInput(sliced,end-start+1,original_page_start=start)}, model=config.gemini_model, prompt_version=PROMPT_VERSION)
                draft = SummaryDraft.model_validate(raw)
                draft.validate_sources({row['source_id']:end-start+1})
                for point in (*draft.scope,*draft.concepts,*draft.relationships,*draft.exam_inferences):
                    for citation in point.citations:
                        citation.page += start-1
                markdown = render_note(draft, filenames={row['source_id']:row['filename']}, page_counts={row['source_id']:end}, segment_only=True)
                (directory/f'pages-{start}-{end}.json').write_text(draft.model_dump_json(indent=2),encoding='utf-8')
                (directory/f'pages-{start}-{end}.md').write_text('流程測試，非正式課程筆記\n\n'+markdown,encoding='utf-8')
                report.update(status='saved', semantic_review='pending')
            except GenerationUnavailable:
                report.update(status='unavailable', http_status=503)
            except ProviderRejected as error:
                report.update(status='rejected', http_status=error.status_code, category=error.category)
            except ProviderUncertain as error:
                report.update(status='uncertain', http_status=error.status_code, category=error.category)
            except Exception:
                report.update(status='validation_or_persistence_failed')
            (directory/'results.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
            print(json.dumps(report),flush=True)
            if report['status'] == 'saved':
                break
            if report['status'] != 'unavailable' or attempt == limit:
                break
            delay = 60 * 2**(attempt-1) + secrets.randbelow(16)
            print(json.dumps({'waiting_seconds':delay,'next_attempt':attempt+1}),flush=True)
            await asyncio.sleep(delay)


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(main())
    except FileExistsError:
        print('existing_attempt_no_requests_sent')
    except Exception:
        print('preflight_or_checkpoint_failed')
