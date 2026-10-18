"""Compact old completed reminder payloads while retaining deduplication keys."""
from datetime import datetime, timedelta

TERMINAL = {'sent', 'cancelled', 'expired'}
RETENTION = timedelta(days=30)


def compact(items, now):
    result = dict(items or {})
    for token, item in result.items():
        if item.get('status') not in TERMINAL or item.get('archived'):
            continue
        timestamp = item.get('updated_at') or item.get('end')
        if not timestamp or now - datetime.fromisoformat(timestamp) < RETENTION:
            continue
        result[token] = {key: value for key, value in item.items()
                         if key in {'token', 'status', 'chat_id', 'start', 'end', 'expires_at', 'updated_at'}}
        result[token]['title'] = 'Archived reminder'
        result[token]['archived'] = True
    return result
