from datetime import date

import pytest

from chronos.course_tracking import COURSE_SCHEDULE, ProgressStatus, new_session
from chronos.course_tracking_store import (
    InMemoryCourseSessionStore,
    session_from_firestore,
    session_to_firestore,
)


def test_in_memory_store_is_idempotent_by_session_id():
    store = InMemoryCourseSessionStore()
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 101)
    assert store.create(session) == session
    assert store.create(session) == session
    assert store.get(session.session_id) == session


def test_store_round_trip_contains_no_secret_material():
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 101)
    data = session_to_firestore(session)
    assert data["status"] == ProgressStatus.PENDING.value
    assert "cookie" not in str(data).lower()
    assert session_from_firestore(data) == session


def test_save_requires_existing_session():
    store = InMemoryCourseSessionStore()
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 101)
    with pytest.raises(KeyError):
        store.save(session)
