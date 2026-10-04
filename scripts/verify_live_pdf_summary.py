"""Explicit one-shot first-segment probe; no Telegram or production writes."""
import asyncio
import json
import logging
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.disable(logging.CRITICAL)
from chronos.settings import settings
from chronos.pdf_store import PdfStore
from chronos.pdf_validation import isolated_pdf_page_count
from chronos.summary_pipeline import PdfInput, GenerationRejected, GenerationUnavailable
from chronos.gemini_summary import GeminiSummary, PROMPT_VERSION, ProviderUncertain
from chronos.study_notes import SummaryDraft, render_note


async def main():
    retry = sys.argv[1:] == ['--execute-free-tier-confirmed', '--retry-rejected-once']
    approved_retry = sys.argv[1:] == ['--execute-free-tier-confirmed', '--user-approved-uncertain-retry-once']
    if not retry and not approved_retry and sys.argv[1:] != ['--execute-free-tier-confirmed']:
        print('probe=disabled')
        return
    directory=Path('.study-data/live-segment-probe-v2')
    if retry or approved_retry:
        previous=json.loads((directory/'result.json').read_text(encoding='utf-8'))
        expected = 'outcome_uncertain' if approved_retry else 'provider_rejected'
        if previous.get('status') != expected:
            print('probe=retry_not_allowed')
            return
        directory=directory/('approved-retry-1' if approved_retry else 'retry-1')
    store=PdfStore(Path('.study-data/pdfs'))
    rows={r['source_id']:r for r in store.catalog('192072')}
    pdfs={}
    for identity in ('9714342',):
        row=rows[identity]
        data=store.read('192072',identity,expected_sha256=row['sha256'])
        end=min(3,isolated_pdf_page_count(data))
        sliced=isolated_pdf_page_count(data,page_range=(1,end))
        pdfs[identity]=PdfInput(sliced,end)
    if not settings.gemini_api_key:
        print('probe=configuration_required')
        return
    directory.mkdir(parents=True,exist_ok=True)
    # Exclusive checkpoint prevents silent re-generation after uncertain outcomes.
    with (directory/'attempt.json').open('x',encoding='utf-8') as output:
        json.dump({'status':'started','model':settings.gemini_model,'sources':list(pdfs)},output)
    try:
        generator=GeminiSummary(settings.model_copy(update={'ai_timeout':45}),free_tier_confirmed=True)
        raw=await generator.generate(progress='Integration test only: extract key points from this page segment. Actual class progress is not supplied; do not infer it.',
            pdfs=pdfs,model=settings.gemini_model,prompt_version=PROMPT_VERSION)
        draft=SummaryDraft.model_validate(raw)
        markdown=render_note(draft,filenames={i:rows[i]['filename'] for i in pdfs},page_counts={i:p.page_count for i,p in pdfs.items()})
        count=len(re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff]',markdown))
        (directory/'draft.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2),encoding='utf-8')
        (directory/'draft.md').write_text(markdown,encoding='utf-8')
        report={'status':'structurally_valid','chinese_characters':count,'page_start':1,'page_end':end,
            'semantic_review':'pending','production_note_created':False}
    except GenerationUnavailable:
        report={'status':'provider_unavailable','category':'server_response','http_status':503}
    except GenerationRejected as error:
        report={'status':'provider_rejected','http_status':getattr(error,'status_code',None),
                'category':getattr(error,'category','unknown')}
    except ProviderUncertain as error:
        report={'status':'outcome_uncertain','category':error.category,'http_status':error.status_code}
    except Exception:
        report={'status':'outcome_uncertain'}
    (directory/'result.json').write_text(json.dumps(report),encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':
    try:
        asyncio.run(main())
    except FileExistsError:
        print('probe=existing_attempt_requires_review')
    except Exception:
        print('probe=preflight_failed')
