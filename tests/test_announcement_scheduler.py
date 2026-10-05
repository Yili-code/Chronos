import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
import pytest
from chronos.announcements import AnnouncementObservationStore
from chronos.announcement_scheduler import tick_announcements
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from chronos.telegram import TelegramError
from test_firestore import database as firestore_fake
from test_announcements import payload


@pytest.mark.parametrize('backend',['sqlite','firestore'])
@pytest.mark.parametrize('uncertain',[False,True])
def test_canonical_history_and_bounded_delivery(backend, uncertain, tmp_path, monkeypatch):
    db = firestore_fake(monkeypatch) if backend=='firestore' else Database(tmp_path/'db.sqlite3')
    db.initialize()
    now = datetime.now(TAIPEI)
    store = AnnouncementObservationStore(tmp_path/'observations.sqlite3')
    data = payload()
    data['announcements'][0]['content'] = 'Synthetic paragraph.\n'*400
    store.put(data, now)
    record = store.snapshots()[0]
    key = db.save_announcement(record)
    db.save_announcement({**record,'first_observed_at':(now+timedelta(hours=1)).isoformat()})
    assert len(db.list_announcements()) == 1
    assert db.list_announcements()[0][1]['first_observed_at'] == now.isoformat()
    bot = AsyncMock()
    bot.send_message.return_value = {'ok':True,'result':{'message_id':10}}
    if uncertain:
        bot.send_message.side_effect = TelegramError('synthetic transport failure')
    for _ in range(20):
        asyncio.run(tick_announcements(db,bot,123,now,limit=1))
    if uncertain:
        assert bot.send_message.await_count == 1
        assert db.get_study_delivery(f'announcement:{key}:part:0')['status']=='uncertain'
    else:
        count = bot.send_message.await_count
        assert count > 1
        asyncio.run(tick_announcements(db,bot,123,now))
        assert bot.send_message.await_count == count
        assert all(len(call.args[1].encode('utf-16-le'))//2 < 4096 for call in bot.send_message.call_args_list)
    with pytest.raises(ValueError):
        db.save_announcement({**record,'fingerprint':'wrong'})
