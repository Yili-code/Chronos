from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import pytest
from chronos.db import Database
from test_note_record import note
from test_firestore import database


@pytest.fixture(params=["sqlite", "firestore_fake"])
def repo(request, tmp_path, monkeypatch):
    if request.param == "firestore_fake":
        return database(monkeypatch)
    result = Database(tmp_path / "notes.db")
    result.initialize()
    return result


def test_repository_contract(repo):
    first = note()
    second = note(content_fingerprint="c" * 64, course="Databases",
                  created_at=datetime(2026, 10, 2, 12, tzinfo=timezone(timedelta(hours=8))))
    repo.save_study_note(first)
    repo.save_study_note(second)
    assert repo.save_study_note(note(markdown="retry")) == first
    assert repo.get_study_note("a" * 64) == first
    assert repo.get_study_note("b" * 64) is None
    assert repo.list_study_notes(limit=1) == [second]
    assert repo.list_study_notes(course="OS") == [first]
    assert repo.list_study_notes(course="' OR 1=1 --") == []
    with pytest.raises(ValueError):
        repo.list_study_notes(limit=101)


def test_sqlite_concurrent_retries_preserve_one_record(tmp_path):
    path = tmp_path / "notes.db"
    db = Database(path)
    db.initialize()
    def save(index):
        return Database(path).save_study_note(note(markdown=f"attempt {index}"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(save, range(8)))
    assert all(result == results[0] for result in results)
    assert db.list_study_notes() == [results[0]]
