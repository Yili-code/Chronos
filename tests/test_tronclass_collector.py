from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from chronos.tronclass_collector import CollectorFailure, _configured_semester_id, _deadline, _plain_text


TZ = ZoneInfo("Asia/Taipei")


def test_collector_normalizes_html_and_preserves_taipei_deadline():
    assert _plain_text("<p>Hello&nbsp;<b>world</b></p>", limit=500) == "Hello world"
    assert _deadline("2026-10-13T12:00:00Z") == datetime(2026, 10, 13, 20, 0, tzinfo=TZ)


@pytest.mark.parametrize("value", ["tomorrow", "2026-10-13", 123])
def test_collector_rejects_ambiguous_deadline(value):
    with pytest.raises(CollectorFailure):
        _deadline(value)


def test_collector_requires_exactly_one_configured_semester():
    class Client:
        def get(self, *args, **kwargs):
            class Response:
                status_code = 200
                def json(self):
                    return {"semesters": [{"id": 1, "name": "1151"}, {"id": 2, "name": "1151"}]}
            return Response()
    with pytest.raises(CollectorFailure, match="configured_semester_shape"):
        _configured_semester_id(Client())


def test_deployed_semester_roster_and_explicit_unsubmitted_evidence(monkeypatch):
    from contextlib import contextmanager
    from chronos import tronclass_collector as collector
    calls = []
    class Response:
        status_code = 200
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload
    class Client:
        def get(self, url, **kwargs):
            calls.append(url)
            if '/my-semesters' in url:
                return Response({'semesters': [{'id': 1, 'name': '1142', 'is_active': True}, {'id': 2, 'name': '1151', 'is_active': False}]})
            if '/my-courses' in url:
                return Response({'courses': [{'id': 189684, 'semester': {'id': 2}}, {'id': 999, 'semester': {'id': 2}}]})
            return Response({'homework_activities': [
                {'id': 1, 'title': 'Current', 'submitted': False},
                {'id': 2, 'title': 'Done', 'submitted': True},
                {'id': 3, 'title': 'Unknown'},
            ]})
    @contextmanager
    def authenticated(*args): yield Client()
    monkeypatch.setattr(collector, 'authenticated_tronclass_client', authenticated)
    result = collector.collect_assignments('u', 'p', datetime(2026, 10, 16, 6, tzinfo=TZ))
    assert [item.source_id for item in result] == ['1']
    assert not any('/999/' in url for url in calls)


def test_course_semester_mismatch_fails_before_any_assignment_read(monkeypatch):
    from contextlib import contextmanager
    from chronos import tronclass_collector as collector
    calls = []
    class Client:
        def get(self, url, **kwargs):
            calls.append(url)
            payload = {'semesters': [{'id': 2, 'name': '1151'}]} if '/my-semesters' in url else {'courses': [{'id': 189684, 'semester': {'id': 1}}]}
            class Response:
                status_code = 200
                def json(self): return payload
            return Response()
    @contextmanager
    def authenticated(*args): yield Client()
    monkeypatch.setattr(collector, 'authenticated_tronclass_client', authenticated)
    with pytest.raises(CollectorFailure, match='course_semester_mismatch'):
        collector.collect_assignments('u', 'p', datetime(2026, 10, 16, 6, tzinfo=TZ))
    assert not any('/homework-activities' in url for url in calls)


def test_pagination_reads_remaining_rows_and_rejects_repeated_pages(monkeypatch):
    from chronos import tronclass_collector as collector
    first = [{'id': i} for i in range(1, 101)]
    pages = iter([{'items': first}, {'items': [{'id': 101}]}])
    monkeypatch.setattr(collector, '_json_get', lambda *a: next(pages))
    assert len(collector._paged_rows(None, '/api/example?scope=1', 'items')) == 101
    monkeypatch.setattr(collector, '_json_get', lambda *a: {'items': first})
    with pytest.raises(CollectorFailure, match='items_identity'):
        collector._paged_rows(None, '/api/example', 'items')
