"""Durable outcome state for the daily TronClass scanner."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import re

from .assignments import aware


@dataclass(frozen=True)
class AssignmentScanState:
    initialized: bool = False
    failure_fingerprint: str | None = None
    consecutive_failures: int = 0
    failure_notified: bool = False
    last_success_at: str | None = None
    baseline_keys: tuple[str, ...] = ()
    failure_sequence: int = 0

    @classmethod
    def from_record(cls, value: dict | None) -> "AssignmentScanState":
        if value is None:
            return cls()
        allowed = {"initialized", "failure_fingerprint", "consecutive_failures", "failure_notified", "last_success_at"}
        if not allowed <= set(value) or set(value) - allowed - {"baseline_keys", "failure_sequence"} or type(value["initialized"]) is not bool or type(value["failure_notified"]) is not bool:
            raise ValueError("invalid assignment scan state")
        fingerprint = value["failure_fingerprint"]
        if fingerprint is not None and (not isinstance(fingerprint, str) or not fingerprint or len(fingerprint) > 100):
            raise ValueError("invalid assignment scan failure")
        if type(value["consecutive_failures"]) is not int or not 0 <= value["consecutive_failures"] <= 1000:
            raise ValueError("invalid assignment scan count")
        if (value["consecutive_failures"] == 0) != (fingerprint is None):
            raise ValueError("inconsistent assignment scan failure")
        timestamp = value["last_success_at"]
        if timestamp is not None:
            aware(datetime.fromisoformat(timestamp))
        baseline = value.get("baseline_keys", [])
        sequence = value.get("failure_sequence", 0)
        if (not isinstance(baseline, list) or len(baseline) > 5000
                or not all(isinstance(key, str) and re.fullmatch(r"[a-f0-9]{64}", key) for key in baseline)
                or baseline != sorted(set(baseline)) or type(sequence) is not int or sequence < 0):
            raise ValueError("invalid assignment scan baseline")
        return cls(**{**value, "baseline_keys": tuple(baseline), "failure_sequence": sequence})

    def to_record(self) -> dict:
        return {"initialized": self.initialized, "failure_fingerprint": self.failure_fingerprint,
                "consecutive_failures": self.consecutive_failures, "failure_notified": self.failure_notified,
                "last_success_at": self.last_success_at, "baseline_keys": list(self.baseline_keys),
                "failure_sequence": self.failure_sequence}


def record_success(previous: AssignmentScanState, now: datetime, *, baseline_keys=None) -> AssignmentScanState:
    return replace(previous, initialized=True, failure_fingerprint=None, consecutive_failures=0,
                   failure_notified=False, last_success_at=aware(now).isoformat(),
                   baseline_keys=tuple(sorted(baseline_keys)) if baseline_keys is not None else previous.baseline_keys)


def record_failure(previous: AssignmentScanState, fingerprint: str) -> AssignmentScanState:
    if not fingerprint or len(fingerprint) > 100:
        raise ValueError("invalid assignment scan failure")
    same = previous.failure_fingerprint == fingerprint
    count = min(previous.consecutive_failures + 1, 1000) if same else 1
    return replace(previous, failure_fingerprint=fingerprint, consecutive_failures=count,
                   failure_notified=previous.failure_notified if same else False,
                   failure_sequence=previous.failure_sequence + int(not same))


def mark_failure_notified(previous: AssignmentScanState) -> AssignmentScanState:
    if previous.consecutive_failures < 2:
        raise ValueError("first scanner failure must not notify")
    return replace(previous, failure_notified=True)
