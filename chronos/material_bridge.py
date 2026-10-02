"""Strict metadata-only browser handoff. No raw URLs or download credentials."""
import re


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
