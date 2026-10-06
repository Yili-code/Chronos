"""Atomic local accounting, not a claim about provider billing or quota resets."""
from datetime import datetime
from .course_tracking import TAIPEI


class BudgetExceeded(RuntimeError):
    pass


class DailyAIBudget:
    def __init__(self, db, *, request_limit, token_limit):
        if any(type(value) is not int or value < 0 for value in (request_limit, token_limit)):
            raise ValueError('budget limits must be nonnegative integers')
        self.db = db
        self.request_limit = request_limit
        self.token_limit = token_limit

    def reserve(self, now: datetime, estimated_tokens: int):
        if now.utcoffset() is None or type(estimated_tokens) is not int or estimated_tokens < 1:
            raise ValueError('aware clock and positive token estimate required')
        day = now.astimezone(TAIPEI).date().isoformat()
        def transition(previous):
            state = previous or {'requests': 0, 'estimated_tokens': 0}
            if (state['requests'] + 1 > self.request_limit or
                    state['estimated_tokens'] + estimated_tokens > self.token_limit):
                raise BudgetExceeded('configured daily AI budget exhausted or disabled')
            return {'requests': state['requests'] + 1,
                    'estimated_tokens': state['estimated_tokens'] + estimated_tokens,
                    'request_limit': self.request_limit, 'token_limit': self.token_limit}
        state = self.db.mutate_ai_budget(day, transition)
        return {**state, 'day': day, 'near_limit':
                state['requests'] * 5 >= self.request_limit * 4 or
                state['estimated_tokens'] * 5 >= self.token_limit * 4}


async def notify_budget(db, telegram, chat_id, status, now):
    from .study_delivery import StudyDeliveryLedger
    from .study_scheduler import delivery_outcome
    from .telegram import TelegramError
    messages = {'near_limit': 'Study AI 已接近設定的每日上限；不會切換付費模型。',
                'exhausted': 'Study AI 已達設定的每日上限或尚未配置額度，已停止送出請求。請檢查額度設定；不會切換付費模型。'}
    if status not in messages:
        raise ValueError('invalid budget notice')
    ledger = StudyDeliveryLedger(db)
    key = f'ai-budget:{now.astimezone(TAIPEI).date()}:{status}'
    claim = ledger.claim(key, now)
    if claim is None:
        return
    try:
        response = await telegram.send_message(chat_id, messages[status])
    except TelegramError:
        ledger.finish(key, claim, now)
        return
    message_id, rejected = delivery_outcome(response)
    ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)


def budget_report(db, now):
    if now.utcoffset() is None:
        raise ValueError('aware clock required')
    day = now.astimezone(TAIPEI).date().isoformat()
    state = db.get_ai_budget(day)
    if state is None:
        return 'Study AI：今日尚無用量紀錄。'
    return (f"Study AI：{state['requests']}/{state.get('request_limit', '?')} requests · "
            f"~{state['estimated_tokens']}/{state.get('token_limit', '?')} tokens")
