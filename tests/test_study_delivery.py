from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

from chronos.db import Database
from chronos.study_delivery import StudyDeliveryLedger

NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)


def ledger(tmp_path):
    db = Database(tmp_path / "delivery.db")
    db.initialize()
    return StudyDeliveryLedger(db)


def test_concurrent_claims_allow_only_one_sender(tmp_path):
    book = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: book.claim("course:date:prompt", NOW), range(8)))
    assert sum(claim is not None for claim in claims) == 1


def test_crash_recovery_does_not_resend_uncertain_message(tmp_path):
    book = ledger(tmp_path)
    claim = book.claim("prompt", NOW)
    assert book.claim("prompt", NOW + timedelta(minutes=3)) is None
    assert book.claim("prompt", NOW + timedelta(days=1)) is None
    # A delayed successful response from the original sender may reconcile it.
    assert book.finish("prompt", claim, NOW, message_id=123)["status"] == "sent"
    assert book.claim("prompt", NOW + timedelta(days=2)) is None


def test_explicit_rejections_have_bounded_durable_retry(tmp_path):
    book = ledger(tmp_path)
    for attempt in range(3):
        now = NOW + timedelta(minutes=5 * attempt)
        claim = book.claim("prompt", now)
        assert claim is not None
        state = book.finish("prompt", claim, now, definitely_rejected=True)
        assert state["attempt_count"] == attempt + 1
        assert book.claim("prompt", now + timedelta(seconds=1)) is None
    assert state["status"] == "failed"
    assert book.claim("prompt", NOW + timedelta(days=1)) is None
