from datetime import datetime
from zoneinfo import ZoneInfo

from chronos.assignment_scan_state import AssignmentScanState, mark_failure_notified, record_failure, record_success


TZ = ZoneInfo("Asia/Taipei")


def test_first_failure_never_notifies_and_same_second_failure_can_notify():
    state = record_failure(AssignmentScanState(), "courses_shape")
    assert state.consecutive_failures == 1
    assert not state.failure_notified

    state = record_failure(state, "courses_shape")
    assert state.consecutive_failures == 2
    assert mark_failure_notified(state).failure_notified


def test_different_failure_restarts_counter_and_success_resets_it():
    state = record_failure(AssignmentScanState(), "courses_shape")
    state = record_failure(state, "homework_shape")
    assert state.consecutive_failures == 1
    state = record_success(state, datetime(2026, 10, 12, 12, tzinfo=TZ))
    assert state.initialized
    assert state.failure_fingerprint is None
    assert state.consecutive_failures == 0


def test_old_production_state_migrates_without_resetting_failure_or_success():
    old = {"initialized": True, "failure_fingerprint": None, "consecutive_failures": 0,
           "failure_notified": False, "last_success_at": "2026-10-16T06:00:00+08:00"}
    state = AssignmentScanState.from_record(old)
    assert state.initialized and state.baseline_keys == ()
    first = record_failure(state, "transport")
    second = record_failure(first, "transport")
    assert second.failure_sequence == first.failure_sequence
    other = record_failure(second, "shape")
    assert other.failure_sequence == first.failure_sequence + 1
