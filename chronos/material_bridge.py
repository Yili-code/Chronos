"""Strict metadata-only browser handoff. No raw URLs or download credentials."""
import re
from threading import Lock
from copy import deepcopy
from pathlib import Path
import json
import sqlite3
from datetime import datetime, timezone


class MaterialObservationStore:
    """Activity snapshots with optional durable storage, never a complete catalog."""
    def __init__(self, path: Path | None = None):
        self._lock = Lock()
        self._activities = {}
        self.path = Path(path) if path is not None else None

    def put(self, payload: dict) -> None:
        observation = validate_material_observation(payload)
        if observation['status'] != 'observed':
            return
        identities = {(row['course_id'], row['activity_id']) for row in observation['materials']}
        if len(identities) != 1:
            raise ValueError('one activity per observation is required')
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            course, activity = next(iter(identities))
            with sqlite3.connect(self.path) as connection:
                connection.execute("CREATE TABLE IF NOT EXISTS activity_snapshots (course_id TEXT, activity_id TEXT, observed_at TEXT, rows_json TEXT, PRIMARY KEY(course_id, activity_id))")
                connection.execute("INSERT INTO activity_snapshots VALUES (?, ?, ?, ?) ON CONFLICT(course_id, activity_id) DO UPDATE SET observed_at=excluded.observed_at, rows_json=excluded.rows_json",
                    (course, activity, datetime.now(timezone.utc).isoformat(), json.dumps(observation['materials'])))
            return
        with self._lock:
            self._activities[next(iter(identities))] = observation['materials']

    def course_materials(self, course_id: str) -> list[dict]:
        if self.path is not None:
            snapshots = self.course_snapshots(course_id)
            by_source = {}
            for snapshot in snapshots:
                for row in snapshot['materials']:
                    by_source[row['source_id']] = row
            return list(by_source.values())
        with self._lock:
            by_source = {}
            for (course, _activity), rows in self._activities.items():
                if course == course_id:
                    for row in rows:
                        by_source[row['source_id']] = row
            return deepcopy(list(by_source.values()))

    def course_snapshots(self, course_id: str) -> list[dict]:
        """Observation time is not upload time or proof of completeness."""
        if self.path is None or not self.path.exists():
            return []
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute("SELECT activity_id, observed_at, rows_json FROM activity_snapshots WHERE course_id=? ORDER BY observed_at, activity_id", (course_id,)).fetchall()
        result = []
        for activity, observed_at, encoded in rows:
            observation = validate_material_observation({'status': 'observed', 'materials': json.loads(encoded)})
            result.append({'activity_id': activity, 'observed_at': observed_at, 'materials': observation['materials'], 'complete_course': False})
        return result


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
