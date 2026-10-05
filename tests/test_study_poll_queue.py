from datetime import datetime,timedelta
import pytest
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from chronos.study_poll_plan import scheduled_requests
from chronos.study_poll_queue import enqueue_scheduled_polls,claim_poll,finish_poll
from test_firestore import database as firestore_fake


@pytest.mark.parametrize('backend',['sqlite','firestore'])
def test_durable_claim_and_explicit_results(backend,tmp_path,monkeypatch):
    db=firestore_fake(monkeypatch) if backend=='firestore' else Database(tmp_path/'poll.db')
    db.initialize()
    now=datetime(2026,10,5,12,tzinfo=TAIPEI)
    for _ in range(2):
        enqueue_scheduled_polls(db,now)
    assert len(db.list_study_polls())==7
    keys=[job.key for job in scheduled_requests(now)]
    token=claim_poll(db,keys[0],now)
    assert token
    assert claim_poll(db,keys[0],now) is None
    assert finish_poll(db,keys[0],token,'partial')['status']=='partial'
    assert claim_poll(db,keys[0],now+timedelta(hours=1)) is None
    token2=claim_poll(db,keys[1],now)
    assert token2
    assert claim_poll(db,keys[1],now+timedelta(minutes=5)) is None
    assert dict(db.list_study_polls())[keys[1]]['status']=='unknown'
    with pytest.raises(ValueError):
        finish_poll(db,keys[1],'wrong','completed')
    enqueue_scheduled_polls(db,now)
    assert dict(db.list_study_polls())[keys[1]]['status']=='unknown'
