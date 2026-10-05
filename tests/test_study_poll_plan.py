from datetime import datetime, timezone
import pytest
from chronos.course_tracking import TAIPEI
from chronos.study_poll_plan import scheduled_requests, reply_request


@pytest.mark.parametrize('hour,slot',[(0,None),(7,None),(8,8),(11,8),(12,12),(17,12),
                                    (18,18),(21,18),(22,22),(23,22)])
def test_four_local_slots_without_catchup_bursts(hour,slot):
    now=datetime(2026,10,5,hour,59,tzinfo=TAIPEI)
    jobs=scheduled_requests(now)
    if slot is None:
        assert jobs==[]
    else:
        assert len(jobs)==7
        assert all(job.due_at.hour==slot and job.due_at.minute==0 for job in jobs)
        assert len({job.key for job in jobs})==7
        assert scheduled_requests(now,[job.key for job in jobs])==[]


def test_timezone_day_rollover_and_partial_completion():
    utc=datetime(2026,10,5,4,tzinfo=timezone.utc)
    jobs=scheduled_requests(utc)
    assert all(job.due_at.hour==12 for job in jobs)
    assert len(scheduled_requests(utc,[jobs[0].key]))==6
    # Midnight Taipei does not backfill yesterday's 22:00 slot.
    assert scheduled_requests(datetime(2026,10,5,16,tzinfo=timezone.utc))==[]
    with pytest.raises(ValueError):
        scheduled_requests(datetime(2026,10,5,12))


def test_correlated_reply_is_immediate_and_replay_stable():
    now=datetime(2026,10,5,1,tzinfo=TAIPEI)
    job=reply_request('188571',123,now)
    assert job==reply_request('188571',123,now)
    assert job.due_at==now
    assert job.reason=='progress_reply'
    assert job.key!=reply_request('188571',124,now).key
    for course,update in [('unknown',123),('188571',True),('188571',-1)]:
        with pytest.raises(ValueError):
            reply_request(course,update,now)
