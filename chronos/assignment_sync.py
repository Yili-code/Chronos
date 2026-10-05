"""Import validated local observations without AI, source writes or task resurrection."""
from datetime import datetime, timedelta

from .assignment_bridge import TRACKED_COURSES, validate_assignment_observation
from .assignments import aware


def sync_assignment_observations(db, store, now, *, limit=20):
    now = aware(now)
    if not 1 <= limit <= 100:
        raise ValueError('bounded import batch required')
    counts = {'created': 0, 'existing': 0, 'invalid': 0, 'stale': 0}
    for snapshot in store.snapshots():
        if counts['created'] >= limit:
            break
        try:
            observed = aware(datetime.fromisoformat(snapshot['observed_at']))
            if observed > now or now - observed > timedelta(days=1):
                counts['stale'] += 1
                continue
            source = snapshot['assignment']
            payload = {'status': 'observed', 'assignment': {
                name: source[name] for name in ('course_id', 'source_id', 'title', 'description', 'deadline')}}
            payload['assignment'].update(submission_status=snapshot['submission_status'],
                attachments_status=snapshot['attachments_status'], attachments=snapshot.get('attachments', []))
            item = validate_assignment_observation(payload, tracked_courses=TRACKED_COURSES,
                                                   observed_at=observed)['assignment']
        except (ValueError, TypeError, KeyError):
            counts['invalid'] += 1
            continue
        previous = db.get_assignment(item.key)
        if previous is not None:
            # Preserve owner edits and cleared/completed task history. Source
            # revisions need a separate conflict-aware update path.
            counts['existing'] += 1
            continue
        db.create_assignment(item)
        counts['created'] += 1
    return counts
