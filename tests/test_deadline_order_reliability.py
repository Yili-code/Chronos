"""Deadline priority is chronological across offsets, with date-only semantics."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from chronos.task_timing import task_sort_key
from test_task_timing import service


def test_stored_deadline_priority_uses_instants_not_iso_strings(service):
    later = service.create('Later local clock, earlier string', datetime.fromisoformat('2026-10-25T08:30:00Z'))
    earlier = service.create('Earlier local clock, later string', datetime.fromisoformat('2026-10-25T09:00:00+08:00'))
    assert [t['id'] for t in service.list_open()] == [earlier['id'], later['id']]
    assert service.get_open_by_position(1)['id'] == earlier['id']
    assert service.get_open_by_position(1)['due_at'] == earlier['due_at']


def test_date_only_priority_uses_local_calendar_day_without_inventing_due_time(service):
    timed = service.create('Evening local deadline', datetime.fromisoformat('2026-10-24T18:00:00Z'))
    date_only = service.create('Date-only deadline', timing={'due_date': '2026-10-25'})
    no_deadline = service.create('Unscheduled')
    assert [t['id'] for t in service.list_open()] == [date_only['id'], timed['id'], no_deadline['id']]
    saved = service.get_open_by_id(date_only['id'])
    assert saved['due_at'] is None and saved['timing'] == {'due_date': '2026-10-25'}


def test_equal_instants_have_stable_id_order(service):
    first = service.create('First', datetime.fromisoformat('2026-10-25T09:00:00+08:00'))
    second = service.create('Same instant', datetime.fromisoformat('2026-10-25T01:00:00Z'))
    assert [t['id'] for t in service.list_open()] == [first['id'], second['id']]


def test_priority_across_daylight_saving_fall_back():
    earlier = {'id': 2, 'due_at': '2026-11-01T01:45:00-04:00'}
    later = {'id': 1, 'due_at': '2026-11-01T01:15:00-05:00'}
    assert sorted([later, earlier], key=task_sort_key) == [earlier, later]

@pytest.mark.parametrize('zone', ['America/New_York', 'Pacific/Kiritimati'])
def test_database_factory_passes_configured_timezone_to_priority(service, tmp_path, zone):
    from chronos.db import create_database
    from chronos.firestore_db import FirestoreDatabase
    from chronos.settings import Settings
    cfg = Settings(_env_file=None, timezone=zone, database_path=tmp_path/'configured.db',
        database_backend='firestore' if isinstance(service.db, FirestoreDatabase) else 'sqlite',
        firestore_project_id='test-project', firestore_collection_prefix='configured')
    db = create_database(cfg)
    db.initialize()
    tz = ZoneInfo(zone)
    assert db.tz == tz
    now = datetime.fromisoformat('2026-10-25T00:00:00Z')
    day = datetime.fromisoformat('2026-10-26T00:00:00').replace(tzinfo=tz)
    from datetime import timedelta
    timed = db.create_task('Previous local evening', day - timedelta(minutes=30), None, now, {})
    date_only = db.create_task('Next local date', None, None, now, {'due_date': '2026-10-26'})
    assert [t['id'] for t in db.list_open_tasks()] == [timed['id'], date_only['id']]
    assert [t['id'] for t in sorted(db.list_open_tasks(), key=lambda t: task_sort_key(t, tz))] == [timed['id'], date_only['id']]
