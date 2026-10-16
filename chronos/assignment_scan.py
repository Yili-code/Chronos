"""Daily assignment-scan orchestration with bounded, durable notifications."""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from .assignment_scan_state import AssignmentScanState, mark_failure_notified, record_failure, record_success
from .assignment_scheduler import tick_assignments
from .assignments import aware
from .assignment_bridge import TRACKED_COURSES
from .calendar_overrides import CalendarOverrides
from .study_delivery import StudyDeliveryLedger
from .study_scheduler import delivery_outcome
from .telegram import TelegramError
from .tronclass_collector import CollectorFailure, collect_assignments


def _eligible_first_scan(item, now: datetime) -> bool:
    return item.submission_status != "submitted" and (item.deadline is None or item.deadline >= now)


async def run_assignment_scan(db, telegram, chat_id: int, now: datetime, username: str, password: str, *, excluded_course_ids: set[str] | None = None,
                              collect=collect_assignments) -> dict:
    now = aware(now)
    token = uuid4().hex
    def claim(previous):
        if previous and datetime.fromisoformat(previous["expires_at"]) > now:
            return previous
        return {"token": token, "expires_at": (now + timedelta(minutes=10)).isoformat()}
    claimed = db.mutate_task_action_state("assignment-scan-lease", claim)
    if claimed["token"] != token:
        return {"assignment_scan": "already_running"}
    try:
        return await _scan(db, telegram, chat_id, now, username, password,
                           excluded_course_ids=excluded_course_ids, collect=collect)
    finally:
        db.mutate_task_action_state("assignment-scan-lease", lambda current:
            {"token": token, "expires_at": now.isoformat()}
            if not current or current.get("token") == token else current)


async def _scan(db, telegram, chat_id, now, username, password, *, excluded_course_ids, collect):
    """Scan once using the configured course scope on every scheduled run."""
    now = aware(now)
    calendar = CalendarOverrides.from_record(db.get_calendar_overrides())
    if not calendar.allows(now.date()):
        return {"assignment_scan": "paused_by_calendar"}
    before = AssignmentScanState.from_record(db.get_assignment_scan_state())
    try:
        incoming = collect(username, password, now, excluded_course_ids=excluded_course_ids)
    except CollectorFailure as error:
        failed = record_failure(before, str(error))
        db.save_assignment_scan_state(failed.to_record())
        if failed.consecutive_failures < 2 or failed.failure_notified:
            return {"assignment_scan": "failure_recorded", "consecutive_failures": failed.consecutive_failures}
        key = "assignment-scan:failure:" + sha256(f"{failed.failure_sequence}:{failed.failure_fingerprint}".encode()).hexdigest()
        ledger = StudyDeliveryLedger(db)
        claim = ledger.claim(key, now)
        if claim is None:
            return {"assignment_scan": "failure_notice_pending", "consecutive_failures": failed.consecutive_failures}
        try:
            result = await telegram.send_message(chat_id, "TronClass 作業掃描連續兩次失敗，尚未判定有無新作業。下次會自動重試。")
        except TelegramError:
            ledger.finish(key, claim, now)
            return {"assignment_scan": "failure_notice_pending", "consecutive_failures": failed.consecutive_failures}
        message_id, rejected = delivery_outcome(result)
        delivery = ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)
        if delivery["status"] != "sent":
            return {"assignment_scan": "failure_notice_pending", "consecutive_failures": failed.consecutive_failures}
        db.save_assignment_scan_state(mark_failure_notified(failed).to_record())
        return {"assignment_scan": "failure_notified", "consecutive_failures": failed.consecutive_failures}

    created = updated = existing = baseline_skipped = 0
    baseline_keys = set(before.baseline_keys)
    for item in incoming:
        if item.course_id not in TRACKED_COURSES:
            baseline_skipped += 1
            continue
        previous = db.get_assignment(item.key)
        if previous is None and (item.key in baseline_keys or not _eligible_first_scan(item, now)):
            baseline_keys.add(item.key)
            baseline_skipped += 1
            continue
        if previous is None:
            db.create_assignment(item)
            created += 1
        elif db.refresh_assignment(item):
            updated += 1
        else:
            existing += 1
    db.save_assignment_scan_state(record_success(before, now, baseline_keys=baseline_keys).to_record())
    delivery = await tick_assignments(db, telegram, chat_id, now)
    status_sent = False
    if created == 0:
        key = f"assignment-scan:{now.date().isoformat()}:no-new"
        ledger = StudyDeliveryLedger(db)
        claim = ledger.claim(key, now)
        if claim:
            try:
                result = await telegram.send_message(chat_id, "TronClass 掃描完成：沒有新作業。")
            except TelegramError:
                ledger.finish(key, claim, now)
            else:
                message_id, rejected = delivery_outcome(result)
                status_sent = ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)["status"] == "sent"
    return {"assignment_scan": "completed", "created": created, "updated": updated, "existing": existing,
            "baseline_skipped": baseline_skipped, "no_new_status_sent": status_sent, **delivery}
