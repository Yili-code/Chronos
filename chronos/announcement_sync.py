"""Import explicitly saved bulletin versions without fetching or generation."""
from datetime import datetime
from .announcements import canonical_announcement
from .assignments import aware


def sync_announcements(db, store, now, *, limit=20):
    now = aware(now)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('bounded import batch required')
    existing = {key for key, _ in db.list_announcements()}
    counts = {'imported':0, 'existing':0, 'invalid':0}
    for snapshot in store.snapshots():
        if counts['imported'] >= limit:
            break
        try:
            key, record = canonical_announcement(snapshot)
            if datetime.fromisoformat(record['first_observed_at']) > now:
                raise ValueError('future observation')
        except (ValueError, TypeError, KeyError):
            counts['invalid'] += 1
            continue
        if key in existing:
            counts['existing'] += 1
            continue
        db.save_announcement(record)
        existing.add(key)
        counts['imported'] += 1
    return counts
