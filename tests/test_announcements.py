from copy import deepcopy
import pytest
from chronos.announcements import validate_announcements
from chronos.announcements import AnnouncementObservationStore
from datetime import datetime, timedelta
from chronos.course_tracking import TAIPEI


def payload():
    return {'status': 'observed_partial', 'complete_course': False, 'announcements': [{
        'course_id': '188571', 'source_id': None, 'identity_status': 'not_exposed',
        'title': 'Synthetic notice', 'published_at': '2026-10-05T12:00:00+08:00',
        'content_status': 'observed_redacted', 'content': 'Read chapter 3.'}]}


def test_identical_versions_deduplicate_but_edits_are_distinct():
    data = payload()
    original = validate_announcements(data)[0]
    data['announcements'].append(deepcopy(data['announcements'][0]))
    assert validate_announcements(data) == [original]
    data['announcements'][1]['content'] = 'Read chapter 4.'
    items = validate_announcements(data)
    assert len(items) == 2
    assert items[0].fingerprint != items[1].fingerprint
    assert validate_announcements(payload())[0].fingerprint == original.fingerprint


@pytest.mark.parametrize('field,value', [('source_id','123'), ('course_id','999'),
    ('published_at','2026-02-30T12:00:00+08:00'), ('content','https://example.test/private'),
    ('title',''), ('content','a'*20001)])
def test_invalid_or_secret_bearing_observation_rejected(field, value):
    data = payload()
    data['announcements'][0][field] = value
    with pytest.raises(ValueError):
        validate_announcements(data)


def test_unknown_cannot_claim_records_or_complete_coverage():
    data = payload()
    data['status'] = 'unknown'
    with pytest.raises(ValueError):
        validate_announcements(data)
    data['announcements'] = []
    assert validate_announcements(data) == []
    data['complete_course'] = True
    with pytest.raises(ValueError):
        validate_announcements(data)


def test_versions_survive_restart_and_partial_absence(tmp_path):
    path = tmp_path / 'announcements.db'
    now = datetime.now(TAIPEI)
    store = AnnouncementObservationStore(path)
    assert store.put(payload(), now)['inserted'] == 1
    restarted = AnnouncementObservationStore(path)
    assert restarted.put(payload(), now + timedelta(hours=1))['inserted'] == 0
    original = restarted.snapshots()
    assert original[0]['first_observed_at'] == now.isoformat()
    assert restarted.put({'status':'unknown', 'complete_course':False, 'announcements':[]}, now)['inserted'] == 0
    assert restarted.snapshots() == original
    changed = payload()
    changed['announcements'][0]['content'] = 'Changed requirement'
    assert restarted.put(changed, now + timedelta(hours=2))['inserted'] == 1
    assert len(restarted.snapshots()) == 2


@pytest.mark.parametrize('status',[[],{},None,True,1])
def test_status_must_be_a_string(status):
    data = payload()
    data['status'] = status
    with pytest.raises(ValueError):
        validate_announcements(data)


def test_invalid_second_record_does_not_partially_persist(tmp_path):
    store = AnnouncementObservationStore(tmp_path/'atomic.sqlite3')
    data = payload()
    data['announcements'].append(deepcopy(data['announcements'][0]))
    data['announcements'][1]['content'] = 'invalid \ud800 unicode'
    with pytest.raises(ValueError):
        store.put(data,datetime.now(TAIPEI))
    assert store.snapshots() == []
