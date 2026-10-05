"""Pure browser-collection planning; this module never opens a tab or fetches."""
from dataclasses import dataclass
from datetime import datetime

from .assignment_bridge import TRACKED_COURSES
from .assignments import aware


POLL_HOURS = (8, 12, 18, 22)


@dataclass(frozen=True)
class PollRequest:
    key: str
    course_id: str
    reason: str
    due_at: datetime


def scheduled_requests(now, completed_keys=()):
    """Latest due slot today only: offline restarts don't replay obsolete slots.

    A completed key means collection was confirmed, not merely attempted.
    Claims, retries and authentication state belong to the executor, not here.
    """
    local = aware(now)
    eligible = [hour for hour in POLL_HOURS if hour <= local.hour]
    if not eligible:
        return []
    hour = eligible[-1]
    due = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    completed = set(completed_keys)
    requests = []
    for course in sorted(TRACKED_COURSES):
        key = f'poll:scheduled:{local.date()}:{hour:02d}:{course}'
        if key not in completed:
            requests.append(PollRequest(key,course,'scheduled',due))
    return requests


def reply_request(course_id, update_id, now):
    """One immediate request per correlated owner reply, independently of slots."""
    if not isinstance(course_id, str) or course_id not in TRACKED_COURSES or type(update_id) is not int or update_id < 0:
        raise ValueError('tracked course and Telegram update ID required')
    return PollRequest(f'poll:reply:{update_id}:{course_id}',course_id,'progress_reply',aware(now))
