"""Strict metadata-only browser handoff. No raw URLs or download credentials."""
import re
from threading import Lock
from copy import deepcopy


class MaterialObservationStore:
    """In-memory snapshots by activity; unknown observations never erase data."""
    def __init__(self):
        self._lock = Lock()
        self._activities = {}

    def put(self, payload: dict) -> None:
        observation = validate_material_observation(payload)
        if observation['status'] != 'observed':
            return
        identities = {(row['course_id'], row['activity_id']) for row in observation['materials']}
        if len(identities) != 1:
            raise ValueError('one activity per observation is required')
        with self._lock:
            self._activities[next(iter(identities))] = observation['materials']

    def course_materials(self, course_id: str) -> list[dict]:
        with self._lock:
            by_source = {}
            for (course, _activity), rows in self._activities.items():
                if course == course_id:
                    for row in rows:
                        by_source[row['source_id']] = row
            return deepcopy(list(by_source.values()))


def validate_material_observation(payload: dict) -> dict:
    if set(payload) != {'status', 'materials'}:
        raise ValueError('unexpected metadata fields')
    if not isinstance(payload['status'], str) or payload['status'] not in {'observed', 'unknown', 'unsupported_page'}:
        raise ValueError('invalid observation status')
    rows = payload['materials']
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError('invalid material count')
    if (payload['status'] == 'observed') != bool(rows):
        raise ValueError('inconsistent material status')
    cleaned = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'source_id', 'course_id', 'activity_id', 'filename', 'uploaded_at'}:
            raise ValueError('unexpected material fields')
        for key in ('source_id', 'course_id', 'activity_id'):
            if not isinstance(row[key], str) or not re.fullmatch(r'[0-9]{1,20}', row[key]):
                raise ValueError('invalid material identity')
        name = row['filename']
        if not isinstance(name, str) or not 1 <= len(name) <= 255 or not name.lower().endswith('.pdf'):
            raise ValueError('invalid PDF filename')
        if any(c in name for c in '\r\n/\\\x00') or row['uploaded_at'] is not None:
            raise ValueError('unsupported metadata value')
        identity = (row['course_id'], row['source_id'])
        if identity in seen:
            raise ValueError('duplicate material identity')
        seen.add(identity)
        cleaned.append(dict(row))
    return {'status': payload['status'], 'materials': cleaned}
