import asyncio
from datetime import datetime
from unittest.mock import AsyncMock
import pytest
from chronos.db import Database
from chronos.course_tracking import TAIPEI
from chronos.study_adapter import BrowserSessionAdapter, FakeBrowserConnector, ConnectorSignal
from chronos.study_poll_plan import reply_request
from chronos.study_poll_queue import enqueue_poll
from chronos.study_poll_worker import CollectionEvidence,run_poll_once


@pytest.mark.parametrize('signal,evidence,expected',[
    (ConnectorSignal.CAS_REDIRECT,None,'reauth_required'),
    (ConnectorSignal.TIMEOUT,None,'unknown'),
    (ConnectorSignal.AUTHENTICATED_PAGE,CollectionEvidence(outcome='completed'),'partial'),
    (ConnectorSignal.AUTHENTICATED_PAGE,CollectionEvidence(True,True,True,True,'completed'),'completed'),
    (ConnectorSignal.AUTHENTICATED_PAGE,{'status':'success'},'adapter_failed'),
    (ConnectorSignal.AUTHENTICATED_PAGE,CollectionEvidence(outcome='reauth_required'),'reauth_required'),
])
def test_worker_requires_explicit_complete_coverage(tmp_path,signal,evidence,expected):
    db=Database(tmp_path/'jobs.db'); db.initialize()
    now=datetime(2026,10,5,12,tzinfo=TAIPEI)
    enqueue_poll(db,reply_request('188571',1,now))
    adapter=BrowserSessionAdapter(FakeBrowserConnector(signal))
    collector=AsyncMock()
    collector.collect.return_value=evidence
    assert asyncio.run(run_poll_once(db,adapter,collector,now))=={'collection':'disabled'}
    assert db.list_study_polls()[0][1]['status']=='queued'
    assert asyncio.run(run_poll_once(db,adapter,collector,now,enabled=True))=={'collection':expected}
    assert collector.collect.await_count==(1 if signal is ConnectorSignal.AUTHENTICATED_PAGE else 0)
    assert db.list_study_polls()[0][1]['status']==expected
    assert asyncio.run(run_poll_once(db,adapter,collector,now,enabled=True))=={'collection':'idle'}


def test_transport_exception_is_not_exposed_or_retried(tmp_path):
    db=Database(tmp_path/'jobs.db'); db.initialize()
    now=datetime(2026,10,5,12,tzinfo=TAIPEI)
    enqueue_poll(db,reply_request('188571',1,now))
    adapter=BrowserSessionAdapter(FakeBrowserConnector(ConnectorSignal.AUTHENTICATED_PAGE))
    collector=AsyncMock()
    collector.collect.side_effect=RuntimeError('SYNTHETIC_PRIVATE_VALUE')
    result=asyncio.run(run_poll_once(db,adapter,collector,now,enabled=True))
    assert result=={'collection':'unknown'}
    assert 'SYNTHETIC_PRIVATE_VALUE' not in str(db.list_study_polls())
