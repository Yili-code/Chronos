from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import pytest
from chronos.db import Database
from chronos.summary_jobs import SummaryJobs
from test_note_record import note
from test_note_repository import repo

NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
KEY = "a" * 64


def test_claim_unknown_and_late_completion(repo):
    jobs = SummaryJobs(repo)
    claim = jobs.claim(KEY, NOW)
    assert claim
    assert jobs.claim(KEY, NOW) is None
    assert jobs.claim(KEY, NOW + timedelta(minutes=11)) is None
    assert jobs.claim(KEY, NOW + timedelta(days=1)) is None
    with pytest.raises(ValueError):
        jobs.finish(KEY, claim, NOW, outcome="completed")
    repo.save_study_note(note())
    assert jobs.finish(KEY, claim, NOW, outcome="completed")["status"] == "completed"
    assert jobs.claim(KEY, NOW + timedelta(days=2)) is None


def test_explicit_rejection_is_bounded(repo):
    jobs = SummaryJobs(repo)
    for attempt in range(3):
        current = NOW + timedelta(minutes=5 * attempt)
        claim = jobs.claim(KEY, current)
        assert claim
        with pytest.raises(ValueError):
            jobs.finish(KEY, "wrong", current, outcome="rejected")
        state = jobs.finish(KEY, claim, current, outcome="rejected")
        assert state["attempt_count"] == attempt + 1
        assert jobs.claim(KEY, current) is None
    assert state["status"] == "failed"
    assert jobs.claim(KEY, NOW + timedelta(days=1)) is None


def test_concurrent_claims_have_one_winner(tmp_path):
    path = tmp_path / "jobs.db"
    Database(path).initialize()
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: SummaryJobs(Database(path)).claim(KEY, NOW), range(8)))
    assert sum(claim is not None for claim in claims) == 1
