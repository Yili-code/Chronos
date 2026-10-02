"""Durable send claims. Uncertain outcomes are never automatically resent."""

from datetime import datetime, timedelta
from uuid import uuid4


class StudyDeliveryLedger:
    def __init__(self, db):
        self.db = db

    def claim(self, key: str, now: datetime) -> str | None:
        if now.utcoffset() is None:
            raise ValueError("delivery clock must be timezone-aware")
        token = uuid4().hex

        def transition(previous):
            if previous:
                if previous["status"] == "sending":
                    if now >= datetime.fromisoformat(previous["claimed_at"]) + timedelta(minutes=2):
                        return {**previous, "status": "uncertain", "last_error": "interrupted_send"}
                    return previous
                if previous["status"] != "retry" or now < datetime.fromisoformat(previous["next_retry_at"]):
                    return previous
            return {
                "status": "sending", "claim": token, "claimed_at": now.isoformat(),
                "attempt_count": (previous["attempt_count"] if previous else 0) + 1,
                "last_error": None, "next_retry_at": None, "message_id": None,
            }

        state = self.db.mutate_study_delivery(key, transition)
        return token if state["status"] == "sending" and state["claim"] == token else None

    def finish(self, key: str, claim: str, now: datetime, *, message_id: int | None = None,
               definitely_rejected: bool = False) -> dict:
        """Only explicit rejection permits retry; timeout/missing response does not."""
        def transition(previous):
            if not previous or previous.get("claim") != claim:
                raise ValueError("delivery claim mismatch")
            if previous["status"] not in {"sending", "uncertain"}:
                return previous
            if type(message_id) is int and message_id > 0:
                return {**previous, "status": "sent", "message_id": message_id,
                        "last_error": None, "next_retry_at": None}
            if definitely_rejected:
                retry = previous["attempt_count"] < 3
                return {**previous, "status": "retry" if retry else "failed",
                        "last_error": "telegram_rejected",
                        "next_retry_at": (now + timedelta(minutes=5)).isoformat() if retry else None}
            return {**previous, "status": "uncertain", "last_error": "delivery_unknown",
                    "next_retry_at": None}

        return self.db.mutate_study_delivery(key, transition)
