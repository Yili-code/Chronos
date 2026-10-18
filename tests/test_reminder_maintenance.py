from datetime import datetime, timedelta, timezone
from chronos.reminder_maintenance import compact


def test_only_old_completed_payloads_are_compacted():
    now = datetime.now(timezone.utc)
    def record(status, days):
        return {'status': status, 'title': 'private title', 'end': (now - timedelta(days=days)).isoformat(),
                'conflicts': ['old event'], 'token': status}
    records = {s: record(s, 31) for s in ['sent', 'cancelled', 'expired', 'active', 'uncertain', 'failed']}
    result = compact(records, now)
    for status in ['sent', 'cancelled', 'expired']:
        assert result[status]['archived']
        assert 'conflicts' not in result[status]
        assert result[status]['token'] == status
    for status in ['active', 'uncertain', 'failed']:
        assert result[status] == records[status]
    assert compact(result, now) == result
    assert compact({'recent': record('sent', 29)}, now)['recent']['title'] == 'private title'
