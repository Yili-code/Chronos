"""Authorized Lec0 pages 1-6 workflow test, isolated from production notes."""
import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chronos.db import Database
from chronos.gemini_summary import GeminiSummary, PROMPT_VERSION
from chronos.pdf_store import PdfStore
from chronos.pdf_validation import isolated_pdf_page_count
from chronos.settings import settings
from chronos.study_materials import MaterialSelection, PdfMaterial
from chronos.summary_pipeline import PdfInput, generate_selected_summary
from chronos.telegram import TelegramClient


def checkpoint_matches(checkpoint, *, model, prompt_version):
    return (checkpoint.get('model') == model
            and checkpoint.get('prompt_version') == prompt_version
            and checkpoint.get('pages') == [1, 6]
            and checkpoint.get('test_only') is True)


class TestTelegram(TelegramClient):
    async def send_message(self, chat_id, text, **kwargs):
        return await super().send_message(chat_id, '【流程測試・非正式課程筆記】\n' + text, **kwargs)


async def main():
    lite = sys.argv[1:] in (['--execute-authorized-lite-first-six'], ['--resume-authorized-lite-first-six'])
    resume = sys.argv[1:] in (['--resume-authorized-lec0-first-six'], ['--resume-authorized-lite-first-six'])
    if not lite and not resume and sys.argv[1:] != ['--execute-authorized-lec0-first-six']:
        print('disabled')
        return
    if not all((settings.gemini_api_key, settings.telegram_bot_token, settings.telegram_chat_id)):
        print('configuration_required')
        return
    config = settings.model_copy(update={'gemini_model': 'gemini-3.1-flash-lite'}) if lite else settings
    directory = Path('.study-data/lec0-lite-first-six-workflow-v4' if lite else '.study-data/lec0-first-six-workflow-v1')
    directory.mkdir(parents=True, exist_ok=True)
    if resume:
        if not (directory / 'attempt.json').exists() or not (directory / 'notes.sqlite3').exists():
            print('missing_checkpoint')
            return
        checkpoint = json.loads((directory / 'attempt.json').read_text(encoding='utf-8'))
        if not checkpoint_matches(checkpoint, model=config.gemini_model, prompt_version=PROMPT_VERSION):
            print('checkpoint_version_unverified_no_requests_sent')
            return
    else:
        with (directory / 'attempt.json').open('x', encoding='utf-8') as output:
            json.dump({'started_at': datetime.now(timezone.utc).isoformat(), 'pages': [1, 6], 'test_only': True,
                       'model': config.gemini_model, 'prompt_version': PROMPT_VERSION}, output)
    db = Database(directory / 'notes.sqlite3')
    db.initialize()
    store = PdfStore(Path('.study-data/pdfs'))
    row = next(r for r in store.catalog('192072') if r['source_id'] == '9714342')
    data = store.read('192072', row['source_id'], expected_sha256=row['sha256'])
    count = isolated_pdf_page_count(data)
    item = PdfMaterial(row['source_id'], '192072', row['filename'], None, row['sha256'])
    selection = MaterialSelection('explicit-chat-workflow-test-first-six-v1', '192072',
        '流程測試：僅講義前六頁；非正式課程筆記，實際上課進度未知。', (item,)).choose(item.source_id, selected=True).confirm()
    generator = GeminiSummary(config.model_copy(update={'ai_timeout': 45}), free_tier_confirmed=True)
    bot = TestTelegram(settings.telegram_bot_token)
    report = {'test_only': True, 'segments': [], 'duplicate_suppressed': False, 'production_database_written': False}
    args = dict(selection=selection, pdfs={item.source_id: PdfInput(data, count)},
        course='流程測試 — OS', class_date=datetime.now(settings.tz).date(), chat_id=settings.telegram_chat_id,
        model=config.gemini_model, prompt_version=PROMPT_VERSION)
    for ordinal, (start, end) in enumerate(((1, 3), (4, 6)), 1):
        result = await generate_selected_summary(db, generator, bot, **args,
            now=datetime.now(timezone.utc), segment=(start, end, ordinal, 2))
        report['segments'].append({'start': start, 'end': end, 'status': result})
        report['saved_notes'] = len(db.list_study_notes())
        (directory / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report['segments'][-1]), flush=True)
        if result != 'sent':
            return
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('duplicate network operation')
    generator.generate = forbidden
    bot.send_message = forbidden
    results = []
    for ordinal, (start, end) in enumerate(((1, 3), (4, 6)), 1):
        results.append(await generate_selected_summary(db, generator, bot, **args,
            now=datetime.now(timezone.utc), segment=(start, end, ordinal, 2)))
    report['duplicate_suppressed'] = results == ['sent', 'sent'] and not calls
    (directory / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(main())
    except FileExistsError:
        print('existing_attempt_requires_review_no_retry')
    except Exception:
        print('workflow_failed_inspect_safe_checkpoint')
