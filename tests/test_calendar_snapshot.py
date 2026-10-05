from datetime import datetime, timedelta
import pytest
from chronos.calendar_snapshot import make_snapshot, is_current
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from test_firestore import database as firestore_fake

HTML = '<table><tr><th>辦理事項</th></tr><tr><td>115年</td><td>十月</td><td>（9）國慶日(補假)</td></tr></table>'
NOW = datetime(2026,10,5,23,59,tzinfo=TAIPEI)

@pytest.mark.parametrize('backend',['sqlite','firestore'])
def test_persistence_and_old_fetch_cannot_overwrite(backend,tmp_path,monkeypatch):
    db = firestore_fake(monkeypatch) if backend == 'firestore' else Database(tmp_path/'db.sqlite3')
    db.initialize()
    assert db.get_calendar_snapshot() is None
    snapshot = make_snapshot(HTML,NOW)
    db.save_calendar_snapshot(snapshot)
    db.save_calendar_snapshot(make_snapshot(HTML,NOW-timedelta(days=1)))
    assert db.get_calendar_snapshot() == snapshot
    assert is_current(db.get_calendar_snapshot(), NOW)
    assert not is_current(snapshot,NOW+timedelta(minutes=1))
    assert not is_current(snapshot,NOW-timedelta(minutes=1))
    with pytest.raises(ValueError):
        db.save_calendar_snapshot({**snapshot,'source_url':'https://example.com'})
    assert db.get_calendar_snapshot() == snapshot

def test_failed_parse_cannot_become_a_successful_snapshot():
    with pytest.raises(ValueError): make_snapshot('<html>Unavailable</html>',NOW)
    assert not is_current(None,NOW)
