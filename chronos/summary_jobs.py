"""Durable generation claims, separate from Telegram delivery receipts."""
import re
import secrets
from datetime import datetime, timedelta
from uuid import uuid4


class SummaryJobs:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def _validate(key, now):
        if not re.fullmatch(r"[0-9a-f]{64}", key) or now.utcoffset() is None:
            raise ValueError("fingerprint and aware clock required")

    def claim(self, key: str, now: datetime) -> str | None:
        self._validate(key, now)
        token = uuid4().hex
        def transition(previous):
            if previous:
                if previous["status"] == "running":
                    if now >= datetime.fromisoformat(previous["claimed_at"]) + timedelta(minutes=10):
                        return {**previous, "status": "uncertain", "last_error": "interrupted_generation"}
                    return previous
                if previous["status"] != "retry" or now < datetime.fromisoformat(previous["next_retry_at"]):
                    return previous
            return {"status": "running", "claim": token, "claimed_at": now.isoformat(),
                    "attempt_count": (previous["attempt_count"] if previous else 0) + 1,
                    "next_retry_at": None, "last_error": None}
        state = self.db.mutate_summary_job(key, transition)
        return token if state["status"] == "running" and state["claim"] == token else None

    def finish(self, key: str, claim: str, now: datetime, *, outcome: str) -> dict:
        self._validate(key, now)
        if outcome not in {"completed", "rejected", "uncertain", "unavailable"}:
            raise ValueError("unsupported generation outcome")
        # Call completed only after canonical persistence, never on model response alone.
        if outcome == "completed" and self.db.get_study_note(key) is None:
            raise ValueError("canonical note must exist before completion")
        def transition(previous):
            if not previous or previous["claim"] != claim:
                raise ValueError("generation claim mismatch")
            if previous["status"] not in {"running", "uncertain"}:
                return previous
            if outcome == "completed":
                return {**previous, "status": "completed", "last_error": None, "next_retry_at": None}
            if outcome in {"rejected", "unavailable"}:
                retry = previous["attempt_count"] < 3
                delay = (60 * 2 ** (previous["attempt_count"] - 1) + secrets.randbelow(16)
                         if outcome == "unavailable" else 300)
                return {**previous, "status": "retry" if retry else "failed",
                        "last_error": "generation_unavailable" if outcome == "unavailable" else "generation_rejected",
                        "next_retry_at": (now + timedelta(seconds=delay)).isoformat() if retry else None}
            return {**previous, "status": "uncertain", "last_error": "generation_unknown", "next_retry_at": None}
        return self.db.mutate_summary_job(key, transition)
