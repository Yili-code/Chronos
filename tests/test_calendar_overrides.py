from datetime import date

import pytest

from chronos.calendar_overrides import CalendarOverrides, apply_calendar_override
from chronos.db import Database


def test_default_term_includes_holidays_and_excludes_break():
    value = CalendarOverrides()

    assert value.allows(date(2026, 10, 25))
    assert value.allows(date(2026, 12, 25))
    assert not value.allows(date(2027, 2, 7))


def test_owner_override_takes_precedence_and_can_be_reset():
    value = CalendarOverrides()
    value = apply_calendar_override(value, "disable 2026-10-25")
    assert not value.allows(date(2026, 10, 25))
    value = apply_calendar_override(value, "reset 2026-10-25")
    assert value.allows(date(2026, 10, 25))
    value = apply_calendar_override(value, "enable 2027-02-07")
    assert value.allows(date(2027, 2, 7))


def test_rejects_invalid_or_conflicting_calendar_state():
    with pytest.raises(ValueError):
        CalendarOverrides.from_record({"term_start": "2027-01-01", "term_end": "2026-12-31", "enabled_dates": [], "disabled_dates": []})
    with pytest.raises(ValueError):
        apply_calendar_override(CalendarOverrides(), "disable not-a-date")


def test_sqlite_round_trip(tmp_path):
    db = Database(tmp_path / "chronos.db")
    db.initialize()
    record = apply_calendar_override(CalendarOverrides(), "disable 2026-10-25").to_record()

    assert db.save_calendar_overrides(record) == record
    assert db.get_calendar_overrides() == record


@pytest.mark.parametrize('backend', ['sqlite', 'firestore'])
def test_concurrent_prepared_commands_compose_and_receipts_deduplicate(backend, tmp_path, monkeypatch):
    import asyncio
    from chronos import main
    from test_firestore import database
    if backend == 'firestore':
        db = database(monkeypatch)
    else:
        db = Database(tmp_path / 'atomic.db')
        db.initialize()
    monkeypatch.setattr(main, 'db', db)
    one = asyncio.run(main.prepare_message('/calendar disable 2026-10-25'))
    two = asyncio.run(main.prepare_message('/calendar disable 2026-10-26'))
    for update_id, action in [(7001, one), (7002, two), (7001, one)]:
        db.process_update(update_id, action)
    assert db.get_calendar_overrides()['disabled_dates'] == ['2026-10-25', '2026-10-26']


def test_calendar_write_rolls_back_when_receipt_transaction_fails(tmp_path):
    db = Database(tmp_path / 'rollback.db')
    db.initialize()
    def crash():
        db.save_calendar_overrides(apply_calendar_override(CalendarOverrides(), 'disable 2026-10-25').to_record())
        raise RuntimeError('receipt failed')
    with pytest.raises(RuntimeError):
        db.process_update(9001, crash)
    assert db.get_calendar_overrides() is None
    assert db.get_update(9001) is None
