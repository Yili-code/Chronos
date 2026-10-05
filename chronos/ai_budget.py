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
                    'estimated_tokens': state['estimated_tokens'] + estimated_tokens}
        state = self.db.mutate_ai_budget(day, transition)
        return {**state, 'day': day, 'near_limit':
                state['requests'] * 5 >= self.request_limit * 4 or
                state['estimated_tokens'] * 5 >= self.token_limit * 4}
