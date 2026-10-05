import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
import pytest
from chronos.announcements import AnnouncementObservationStore
from chronos.announcement_sync import sync_announcements
from chronos.run_announcement_sync import run
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from test_firestore import database as firestore_fake
from test_announcements import payload


@pytest.mark.parametrize('backend',['sqlite','firestore'])
def test_import_replay_and_bounded_catchup(backend,tmp_path,monkeypatch):
    db = firestore_fake(monkeypatch) if backend=='firestore' else Database(tmp_path/'db.sqlite3')
    db.initialize()
    now = datetime.now(TAIPEI)
    store = AnnouncementObservationStore(tmp_path/'source.sqlite3')
    for index in range(3):
        data = payload()
        data['announcements'][0]['title'] = f'Notice {index}'
        store.put(data,now-timedelta(days=3))
    for count in (1,2,3):
        assert sync_announcements(db,store,now,limit=1)['imported']==1
        assert len(db.list_announcements())==count
    assert sync_announcements(db,store,now)['imported']==0
    future=payload()
    store.put(future,now+timedelta(days=1))
    assert sync_announcements(db,store,now)['invalid']==1


def test_disabled_launcher_does_not_require_configuration(capsys):
    assert asyncio.run(run(SimpleNamespace(enable=False)))==0
    assert capsys.readouterr().out.strip()=='announcement_sync=disabled'
