import asyncio
from datetime import datetime
import pytest
from chronos.announcement_commands import announcements_query, announcement_query
from chronos.announcements import AnnouncementObservationStore
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from test_firestore import database as firestore_fake
from test_announcements import payload


@pytest.mark.parametrize('backend',['sqlite','firestore'])
def test_saved_lookup_preserves_text_and_state(backend,tmp_path,monkeypatch):
    db = firestore_fake(monkeypatch) if backend=='firestore' else Database(tmp_path/'db.sqlite3')
    db.initialize()
    assert '不代表' in announcements_query(db,'announcements')
    store = AnnouncementObservationStore(tmp_path/'observations.sqlite3')
    data = payload()
    data['announcements'][0]['content'] = '中文😀 paragraph\n'*500
    store.put(data,datetime.now(TAIPEI))
    key = db.save_announcement(store.snapshots()[0])
    before = db.list_announcements()
    listing = announcements_query(db,'announcements')
    assert key in listing
    assert '2026-' not in listing
    chunks=[]
    page=1
    while True:
        answer=announcement_query(db,f'announcement {key} {page}')
        chunks.append(answer.rsplit('\n\n第 ',1)[0])
        assert len(answer.encode('utf-16-le'))//2 < 4096
        if '下一頁' not in answer:
            break
        page+=1
    assert ''.join(chunks).endswith(data['announcements'][0]['content'])
    assert db.list_announcements()==before
    assert '用法' in announcements_query(db,'announcements 0')
    assert '目前共' in announcement_query(db,f'announcement {key} 999')


def test_lookup_routes_do_not_call_generation(tmp_path,monkeypatch):
    from chronos import main
    db=Database(tmp_path/'route.sqlite3')
    db.initialize()
    monkeypatch.setattr(main,'db',db)
    action=asyncio.run(main.prepare_message('/announcements'))
    assert '尚無' in action()
    action=asyncio.run(main.prepare_message('/announcement '+'a'*64))
    assert '找不到' in action()
