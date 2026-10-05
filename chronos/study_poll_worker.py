"""Bounded collection orchestration; browser permissions belong to its connector."""
from dataclasses import dataclass
from typing import Protocol

from .study_adapter import SessionStatus
from .study_poll_queue import next_poll, finish_poll


@dataclass(frozen=True)
class CollectionEvidence:
    # True means the connector verified coverage AND persisted observations.
    # Merely finding zero rows, or saving a partial DOM, is not complete.
    announcements: bool = False
    materials: bool = False
    assignments: bool = False
    videos: bool = False
    outcome: str = 'partial'


class CourseCollector(Protocol):
    async def collect(self, course_id: str) -> CollectionEvidence:
        """Read and persist course observations; no submission or video playback."""


async def run_poll_once(db, session_adapter, collector: CourseCollector, now, *, enabled=False):
    if not enabled:
        return {'collection':'disabled'}
    job = next_poll(db,now)
    if job is None:
        return {'collection':'idle'}
    outcome='unknown'
    try:
        session=session_adapter.session()
        if session.status is SessionStatus.REAUTH_REQUIRED:
            outcome='reauth_required'
        elif session.status is SessionStatus.READY:
            evidence=await collector.collect(job['course_id'])
            if not isinstance(evidence,CollectionEvidence):
                outcome='adapter_failed'
            elif evidence.outcome in ('reauth_required','unknown','adapter_failed'):
                outcome=evidence.outcome
            elif evidence.outcome in ('partial','completed'):
                coverage=(evidence.announcements,evidence.materials,evidence.assignments,evidence.videos)
                if any(type(value) is not bool for value in coverage):
                    outcome='adapter_failed'
                else:
                    outcome='completed' if evidence.outcome=='completed' and all(coverage) else 'partial'
            else:
                outcome='adapter_failed'
    except Exception:
        # Never log an exception that could contain a provider URL or cookie.
        outcome='unknown'
    finish_poll(db,job['key'],job['claim'],outcome)
    return {'collection':outcome}
