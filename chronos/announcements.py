"""Content-version observations, explicitly not stable upstream bulletin IDs."""
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
import sqlite3
from pathlib import Path

from .assignment_bridge import TRACKED_COURSES
from .assignments import aware


@dataclass(frozen=True)
class AnnouncementObservation:
    course_id: str
    title: str
    published_at: str
    content: str

    @property
    def fingerprint(self):
        # An identical rendered version has the same fingerprint after restart.
        # An edited title/body is a different observation, not proven new source.
        return sha256(json.dumps([self.course_id, self.title, self.published_at,
                                  self.content], ensure_ascii=False).encode()).hexdigest()


def validate_announcements(payload, tracked_courses=TRACKED_COURSES):
    if not isinstance(payload, dict) or set(payload) != {'status', 'complete_course', 'announcements'}:
        raise ValueError('invalid announcement envelope')
    if payload['complete_course'] is not False:
        raise ValueError('complete bulletin coverage is not supported')
    status, rows = payload['status'], payload['announcements']
    if status not in {'unknown', 'unsupported_page', 'observed_partial'} or not isinstance(rows, list):
        raise ValueError('invalid announcement status')
    if status != 'observed_partial':
        if rows:
            raise ValueError('uncertain observation contains records')
        return []
    if not 1 <= len(rows) <= 100:
        raise ValueError('invalid announcement count')
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'course_id', 'source_id', 'identity_status',
                'title', 'published_at', 'content_status', 'content'}:
            raise ValueError('unexpected announcement fields')
        if (not isinstance(row['course_id'], str) or row['course_id'] not in tracked_courses
                or row['source_id'] is not None or row['identity_status'] != 'not_exposed'
                or row['content_status'] != 'observed_redacted'):
            raise ValueError('unsupported announcement identity')
        for field, limit in [('title', 500), ('content', 20000)]:
            text = row[field]
            if not isinstance(text, str) or len(text) > limit or '\0' in text:
                raise ValueError('invalid announcement text')
            if re.search(r'https?://', text, re.I):
                raise ValueError('unredacted announcement URL')
        if not row['title'].strip():
            raise ValueError('missing title')
        date = row['published_at']
        if not isinstance(date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00\+08:00', date):
            raise ValueError('invalid publication date')
        datetime.fromisoformat(date)
        item = AnnouncementObservation(row['course_id'], row['title'], date, row['content'])
        result[item.fingerprint] = item
    return list(result.values())


class AnnouncementObservationStore:
    """Append-only versions; absence from a partial page never means deletion."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def put(self, payload, observed_at):
        observed_at = aware(observed_at).isoformat()
        items = validate_announcements(payload)
        if payload['status'] != 'observed_partial':
            return {'status': payload['status'], 'inserted': 0}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        inserted = 0
        with sqlite3.connect(self.path) as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('CREATE TABLE IF NOT EXISTS announcement_observations '
                '(fingerprint TEXT PRIMARY KEY, record_json TEXT NOT NULL, first_observed_at TEXT NOT NULL)')
            for item in items:
                cursor = connection.execute('INSERT OR IGNORE INTO announcement_observations VALUES (?, ?, ?)',
                    (item.fingerprint, json.dumps(asdict(item), ensure_ascii=False), observed_at))
                inserted += cursor.rowcount
        return {'status': 'observed_partial', 'inserted': inserted}

    def snapshots(self):
        if not self.path.exists():
            return []
        with sqlite3.connect(self.path) as connection:
            return [{'fingerprint': key, 'announcement': json.loads(record), 'first_observed_at': observed}
                    for key, record, observed in connection.execute(
                        'SELECT fingerprint, record_json, first_observed_at FROM announcement_observations '
                        'ORDER BY first_observed_at, fingerprint')]
