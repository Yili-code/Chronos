"""Bounded preparation worker; canonical draft always precedes delivery."""
from datetime import datetime, timedelta
from uuid import uuid4

from .ai_budget import BudgetExceeded
from .assignment_preparation import PreparationDraft, generate_preparation, render_preparation
from .assignments import from_record
from .study_delivery import StudyDeliveryLedger
from .study_scheduler import delivery_outcome
from .telegram import TelegramError


async def run_preparation_pass(db, provider, telegram, chat_id, now, *, limit=3):
    if now.utcoffset() is None or not 1 <= limit <= 10:
        raise ValueError('aware clock and bounded batch required')
    ledger = StudyDeliveryLedger(db)
    processed = 0
    for key, state in db.list_preparations():
        if processed >= limit:
            break
        if state['status'] not in {'queued', 'running', 'ready'}:
            continue
        processed += 1
        if state['status'] != 'ready':
            token = uuid4().hex
            def claim(previous):
                if previous['status'] == 'running':
                    if now >= datetime.fromisoformat(previous['claimed_at']) + timedelta(minutes=10):
                        return {**previous, 'status': 'uncertain', 'last_error': 'interrupted_generation'}
                    return previous
                if previous['status'] != 'queued':
                    return previous
                return {**previous, 'status': 'running', 'claim': token,
                        'claimed_at': now.isoformat(), 'attempt_count': previous['attempt_count'] + 1}
            state = db.mutate_preparation(key, claim)
            if state['status'] != 'running' or state.get('claim') != token:
                continue
            item = from_record(state['assignment'])
            current = db.get_assignment(item.key)
            if not current or not current['task_exists'] or current['assignment'].status == 'done':
                db.mutate_preparation(key, lambda old: {**old, 'status': 'cancelled', 'last_error': 'assignment_closed'})
                continue
            try:
                draft = await generate_preparation(provider, item)
            except BudgetExceeded:
                outcome, error = 'failed', 'budget_exhausted'
            except Exception:
                # No automatic retry: a model request might have completed.
                outcome, error = 'uncertain', 'generation_unconfirmed'
            else:
                outcome, error = 'ready', None
            def finish(previous):
                if previous.get('claim') != token or previous['status'] != 'running':
                    return previous
                return {**previous, 'status': outcome, 'last_error': error,
                        'draft': draft.model_dump() if outcome == 'ready' else None}
            state = db.mutate_preparation(key, finish)
        if state['status'] != 'ready':
            continue
        text = f"作業 #{state['task_id']}\n" + render_preparation(PreparationDraft.model_validate(state['draft']))
        complete = True
        for index, offset in enumerate(range(0, len(text), 1500)):
            delivery_key = f'preparation:{key}:{index}'
            previous = db.get_study_delivery(delivery_key)
            if previous and previous['status'] == 'sent':
                continue
            claim = ledger.claim(delivery_key, now)
            if claim is None:
                complete = False
                break
            try:
                result = await telegram.send_message(chat_id, text[offset:offset + 1500])
            except TelegramError:
                ledger.finish(delivery_key, claim, now)
                complete = False
                break
            message_id, rejected = delivery_outcome(result)
            receipt = ledger.finish(delivery_key, claim, now, message_id=message_id, definitely_rejected=rejected)
            if receipt['status'] != 'sent':
                complete = False
                break
        if complete:
            db.mutate_preparation(key, lambda previous: {**previous, 'status': 'sent', 'sent_at': now.isoformat()})
    return {'preparations_processed': processed}
