"""Privacy-preserving aggregate AI usage accounting."""

from datetime import datetime


class AIUsageRecorder:
    def __init__(self, db, tz):
        self.db = db
        self.tz = tz

    def record(
        self,
        operation: str,
        *,
        now: datetime,
        attempts: int,
        status: str,
        input_chars: int,
        prompt_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> dict:
        if not operation or attempts < 0 or input_chars < 0:
            raise ValueError("valid aggregate AI usage is required")
        day = now.astimezone(self.tz).date().isoformat()

        def transition(previous):
            state = previous or {
                "requests": 0,
                "attempts": 0,
                "input_chars": 0,
                "prompt_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "statuses": {},
            }
            statuses = dict(state.get("statuses", {}))
            statuses[status] = statuses.get(status, 0) + 1
            return {
                **state,
                "requests": state.get("requests", 0) + 1,
                "attempts": state.get("attempts", 0) + attempts,
                "input_chars": state.get("input_chars", 0) + input_chars,
                "prompt_tokens": state.get("prompt_tokens", 0) + (prompt_tokens or 0),
                "output_tokens": state.get("output_tokens", 0) + (output_tokens or 0),
                "total_tokens": state.get("total_tokens", 0) + (total_tokens or 0),
                "statuses": statuses,
            }

        return self.db.mutate_ai_usage(day, operation, transition)

    def report(self, now: datetime) -> dict[str, dict]:
        day = now.astimezone(self.tz).date().isoformat()
        return self.db.list_ai_usage(day)


def format_ai_usage(records: dict[str, dict], day: str) -> str:
    if not records:
        return f"<b>AI usage · {day}</b>\nNo AI requests recorded."
    lines = [f"<b>AI usage · {day}</b>"]
    for operation, state in sorted(records.items()):
        tokens = state.get("total_tokens", 0)
        token_text = str(tokens) if tokens else "not reported"
        lines.append(
            f"\n<b>{operation}</b>\n"
            f"Requests: {state.get('requests', 0)} · Attempts: {state.get('attempts', 0)} · "
            f"Tokens: {token_text}"
        )
    return "\n".join(lines)
