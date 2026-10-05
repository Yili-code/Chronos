"""Pure assignment lifecycle rules; no network, generation or submission.

An upstream source ID identifies an assignment independently of its title and
deadline. Adapter failures must be handled before calling these rules.
"""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256

from .course_tracking import TAIPEI


REMINDER_OFFSETS = (
    ("7d", timedelta(days=7)),
    ("3d", timedelta(days=3)),
    ("1d", timedelta(days=1)),
    ("3h", timedelta(hours=3)),
)


def aware(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("assignment timestamps must be timezone-aware")
    return value.astimezone(TAIPEI)


@dataclass(frozen=True)
class Assignment:
    course_id: str
    source_id: str
    title: str
    description: str
    discovered_at: datetime
    deadline: datetime | None = None
    deadline_origin: str | None = None
    deadline_revision: int = 0
    completed_at: datetime | None = None

    def __post_init__(self):
        if not all(value.strip() for value in (self.course_id, self.source_id, self.title)):
            raise ValueError("assignment identity and title are required")
        aware(self.discovered_at)
        if self.deadline is not None:
            aware(self.deadline)
            if self.deadline_origin not in {"source", "owner"}:
                raise ValueError("deadline requires explicit provenance")
        elif self.deadline_origin is not None:
            raise ValueError("missing deadline cannot have provenance")
        if self.completed_at is not None:
            aware(self.completed_at)
        if self.deadline_revision < 0:
            raise ValueError("invalid deadline revision")

    @property
    def key(self) -> str:
        # Length-prefixing avoids ambiguous concatenation and unsafe document IDs.
        identity = f"{len(self.course_id)}:{self.course_id}{len(self.source_id)}:{self.source_id}"
        return sha256(identity.encode()).hexdigest()

    @property
    def status(self) -> str:
        if self.completed_at is not None:
            return "done"
        return "deadline_pending" if self.deadline is None else "open"


def set_deadline(assignment: Assignment, deadline: datetime, *, origin: str) -> Assignment:
    """Only explicit source timestamps or owner confirmations may set deadlines."""
    deadline = aware(deadline)
    if origin not in {"source", "owner"}:
        raise ValueError("deadline cannot be inferred")
    if assignment.completed_at is not None:
        raise ValueError("completed assignments cannot be rescheduled")
    changed = assignment.deadline != deadline
    return replace(assignment, deadline=deadline, deadline_origin=origin,
                   deadline_revision=assignment.deadline_revision + int(changed))


def complete(assignment: Assignment, now: datetime) -> Assignment:
    now = aware(now)
    return assignment if assignment.completed_at is not None else replace(assignment, completed_at=now)


def due_reminder(assignment: Assignment, now: datetime, *, sent_keys: set[str]) -> str | None:
    """Return the current reminder window only, avoiding catch-up message bursts.

    A late discovery during the one-day window gets one one-day reminder, not
    all previous reminders. Completion and unknown deadlines always suppress
    reminders. The caller must claim the key durably before sending and recheck
    completion; this pure function alone does not provide delivery idempotency.
    """
    now = aware(now)
    if assignment.status != "open" or now < aware(assignment.discovered_at):
        return None
    remaining = aware(assignment.deadline) - now
    if remaining <= timedelta(0):
        return None
    eligible = [label for label, offset in REMINDER_OFFSETS if remaining <= offset]
    if not eligible:
        return None
    label = eligible[-1]
    key = f"assignment:{assignment.key}:deadline:{assignment.deadline_revision}:{label}"
    return None if key in sent_keys else key


def deadline_question(assignment: Assignment) -> str:
    if assignment.status != "deadline_pending":
        raise ValueError("only missing deadlines need confirmation")
    return f"作業「{assignment.title}」尚未提供明確截止時間，請確認日期與時間（Asia/Taipei）。"


def to_record(assignment: Assignment) -> dict:
    data = asdict(assignment)
    for field in ("discovered_at", "deadline", "completed_at"):
        data[field] = aware(data[field]).isoformat() if data[field] is not None else None
    return data


def from_record(data: dict) -> Assignment:
    data = dict(data)
    for field in ("discovered_at", "deadline", "completed_at"):
        data[field] = datetime.fromisoformat(data[field]) if data.get(field) is not None else None
    return Assignment(**data)
