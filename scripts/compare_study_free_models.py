"""Explicit, local-only fixed-input comparison; never deliver or change defaults."""
import argparse
import asyncio
import copy
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chronos.gemini_summary import GeminiSummary, PROMPT_VERSION, ProviderRejected, ProviderUncertain
from chronos.pdf_store import PdfStore
from chronos.pdf_validation import isolated_pdf_page_count
from chronos.settings import settings
from chronos.source_checks import validate_inference_evidence, validate_weekday_presence
from chronos.study_notes import SummaryDraft, render_note
from chronos.summary_pipeline import PdfInput, GenerationUnavailable


MODELS = ('gemini-2.5-flash', 'gemini-3.7-flash')
COHORTS = {'initial': MODELS, 'flash36': ('gemini-3.6-flash',),
           'flash36text': ('gemini-3.6-flash',), 'flash36json': ('gemini-3.6-flash',),
           'flash36stages': ('gemini-3.6-flash',)}
EXPECTED_HASH = 'fd31bde477c7c1e3b00e56139f1e37019b542268e7bb51fa9e4e1b888f1d3a14'


class ObservedSummary(GeminiSummary):
    """Record only request stages, safe outcomes and elapsed time, never content."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request_events = []

    async def _request(self, body, model):
        event = {'stage': 'draft' if not self.request_events else 'review', 'status': 'started'}
        self.request_events.append(event)
        started = time.monotonic()
        try:
            result = await super()._request(body, model)
            event['status'] = 'schema_validated'
            return result
        except ProviderRejected as error:
            event.update(status='rejected', http_status=error.status_code, category=error.category)
            raise
        except ProviderUncertain as error:
            event.update(status='uncertain', http_status=error.status_code, category=error.category)
            raise
        except GenerationUnavailable:
            event.update(status='unavailable', http_status=503)
            raise
        except Exception:
            event['status'] = 'unknown'
            raise
        finally:
            event['elapsed_seconds'] = round(time.monotonic() - started, 2)


class TextOnlyControl(ObservedSummary):
    """Diagnostic only: remove binary PDF, preserving prompt/schema/page text."""
    async def _request(self, body, model):
        body = copy.deepcopy(body)
        for content in body['contents']:
            content['parts'] = [part for part in content['parts'] if 'inlineData' not in part]
        return await super()._request(body, model)


class JsonModeControl(TextOnlyControl):
    """Isolate native schema enforcement; local validation remains unchanged."""
    async def _request(self, body, model):
        body = copy.deepcopy(body)
        schema = body['generationConfig'].pop('responseJsonSchema')
        body['systemInstruction']['parts'].append({'text':
            'Return JSON matching this output contract: ' + json.dumps(schema)})
        return await super()._request(body, model)


async def run(cohort='initial'):
    if cohort not in COHORTS:
        return {'status': 'unknown_cohort_no_requests'}
    if PROMPT_VERSION != 'study-segment-v10':
        return {'status': 'prompt_changed_no_requests'}
    models = COHORTS[cohort]
    directory = Path('.study-data/free-model-comparison-v10' + ('' if cohort == 'initial' else '-' + cohort))
    directory.mkdir(parents=True, exist_ok=True)
    # A previous or interrupted run cannot silently consume more requests.
    with (directory / 'attempt.json').open('x', encoding='utf-8') as output:
        json.dump({'started_at': datetime.now(timezone.utc).isoformat(),
            'models': models, 'prompt_version': PROMPT_VERSION,
            'source_sha256': EXPECTED_HASH, 'physical_pages': [10, 12],
            'max_requests_per_model': 2, 'automatic_retries': 0,
            'input_mode': 'extracted_text_only' if cohort in {'flash36text', 'flash36json'} else 'pdf_and_text',
            'native_schema': cohort != 'flash36json',
            'local_only': True}, output)
    store = PdfStore(Path('.study-data/pdfs'))
    data = store.read('192072', '9714342', expected_sha256=EXPECTED_HASH)
    sliced = isolated_pdf_page_count(data, page_range=(10, 12))
    pages = tuple(isolated_pdf_page_count(sliced, extract_text=True))
    pdf = PdfInput(sliced, 3, original_page_start=10, source_pages=pages)
    reports = []
    for model in models:
        started = time.monotonic()
        report = {'model': model, 'status': 'started'}
        reports.append(report)
        (directory / 'results.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
        stage = 'generation_and_review'
        generator = None
        try:
            config = settings.model_copy(update={'gemini_model': model, 'ai_timeout': 45})
            adapter = JsonModeControl if cohort == 'flash36json' else TextOnlyControl if cohort == 'flash36text' else ObservedSummary
            generator = adapter(config, free_tier_confirmed=True)
            raw = await generator.generate(
                progress='Explicit local workflow test only; actual class progress unknown.',
                pdfs={'9714342': pdf}, model=model, prompt_version=PROMPT_VERSION)
            stage = 'schema'
            draft = SummaryDraft.model_validate(raw)
            (directory / f'{model}-candidate.json').write_text(draft.model_dump_json(indent=2), encoding='utf-8')
            stage = 'source_bounds'
            draft.validate_sources({'9714342': 3})
            stage = 'inference_category'
            draft.validate_inference_categories()
            stage = 'weekday_presence'
            validate_weekday_presence(draft, {'9714342': pages})
            stage = 'inference_excerpt_presence'
            validate_inference_evidence(draft, {'9714342': pages})
            for point in (*draft.scope, *draft.concepts, *draft.relationships, *draft.exam_inferences):
                for citation in point.citations:
                    citation.page += 9
            for point in draft.exam_inferences:
                for excerpt in point.evidence:
                    excerpt.page += 9
            stage = 'local_save'
            markdown = render_note(draft, filenames={'9714342': 'Lec0_Course Info & CourseIntroduction_OS.pdf'},
                                   page_counts={'9714342': 14}, segment_only=True)
            (directory / f'{model}.md').write_text('流程對照測試，非正式筆記\n\n' + markdown, encoding='utf-8')
            report.update(status='saved', semantic_review='pending')
        except ProviderRejected as error:
            report.update(status='rejected', http_status=error.status_code, category=error.category)
        except ProviderUncertain as error:
            report.update(status='uncertain', http_status=error.status_code, category=error.category)
        except GenerationUnavailable:
            report.update(status='unavailable', http_status=503)
        except Exception:
            report.update(status='validation_or_save_failed', stage=stage)
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        report['requests'] = generator.request_events if generator is not None else []
        (directory / 'results.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)
    return {'status': 'comparison_finished', 'production_changed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--cohort', choices=tuple(COHORTS), default='initial')
    options = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if not options.execute:
        print('disabled')
    else:
        try:
            print(json.dumps(asyncio.run(run(options.cohort))))
        except FileExistsError:
            print('existing_attempt_no_requests_sent')
        except Exception:
            print('preflight_or_checkpoint_failed_no_blind_retry')
