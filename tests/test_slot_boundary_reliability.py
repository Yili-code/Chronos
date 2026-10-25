"""Availability search respects lower bounds at sub-minute and timezone edges."""
from datetime import datetime, timedelta
import random

import pytest

from chronos.learning_calendar import find_slot
from chronos.settings import Settings

CFG = Settings(_env_file=None, learning_daily_minutes=600)
NOW = datetime.fromisoformat('2026-10-19T09:00:00+08:00')
NO_CLASSES = {'first_day': '2020-01-01', 'last_regular_day': '2020-01-02', 'excluded_dates': []}


@pytest.mark.parametrize('second,microsecond', [(1, 0), (30, 0), (59, 999999), (0, 1)])
def test_slot_never_rounds_before_requested_window(second, microsecond):
    opening = NOW.replace(hour=14, minute=15, second=second, microsecond=microsecond)
    window = opening, opening + timedelta(hours=2)
    slot = find_slot({}, NOW, [], [], CFG, 30, course_policy=NO_CLASSES, window=window)
    assert slot is not None
    assert slot[0] >= opening
    assert slot[1] <= window[1]
    assert slot[0] == NOW.replace(hour=14, minute=30)


def test_slot_never_rounds_below_minimum_lead_time():
    now = NOW.replace(minute=5, second=30)
    slot = find_slot({}, now, [], [], CFG, 30, course_policy=NO_CLASSES)
    assert slot[0] >= now + timedelta(minutes=10)
    assert slot[0] == NOW.replace(minute=30)


def test_foreign_offset_window_still_searches_the_local_starting_day():
    opening = datetime.fromisoformat('2026-10-20T00:00:00+14:00')
    slot = find_slot({}, NOW, [], [], CFG, 30, course_policy=NO_CLASSES,
                     window=(opening, opening + timedelta(hours=2)))
    assert slot is not None
    assert slot[0] == NOW.replace(hour=18)


def test_seeded_slot_search_invariants():
    rng = random.Random(20261025)
    for _ in range(300):
        now = NOW + timedelta(days=rng.randrange(27), minutes=rng.randrange(720), seconds=rng.randrange(60))
        opening = now + timedelta(minutes=rng.randrange(1, 600))
        closing = opening + timedelta(minutes=rng.randrange(30, 300))
        minutes = rng.choice([10, 30, 45, 60, 90, 180])
        busy = [(opening + timedelta(minutes=30), opening + timedelta(minutes=60))]
        task = {'due_at': (closing - timedelta(minutes=10)).isoformat()}
        slot = find_slot(task, now, busy, [], CFG, minutes, course_policy=NO_CLASSES, window=(opening, closing))
        if slot:
            start, end = slot
            assert start >= max(opening, now + timedelta(minutes=10))
            assert end <= closing - timedelta(minutes=10)
            assert end - start == timedelta(minutes=minutes)
            assert end <= busy[0][0] or start >= busy[0][1]
            assert start.minute % 15 == start.second == start.microsecond == 0


def test_slot_search_matches_exhaustive_feasibility_oracle():
    from datetime import time
    from zoneinfo import ZoneInfo
    rng = random.Random(28720261025)
    for case in range(1200):
        tz = ZoneInfo(rng.choice(['Asia/Taipei', 'UTC', 'America/New_York']))
        cfg = CFG.model_copy(update={'timezone': str(tz), 'learning_daily_minutes': rng.choice([60, 120, 240, 600]),
                                      'learning_start_hour': rng.choice([6, 9, 13]), 'learning_end_hour': rng.choice([18, 21, 24])})
        now = (NOW + timedelta(days=rng.randrange(45), minutes=rng.randrange(1200), seconds=rng.randrange(60))).astimezone(tz)
        opening = now + timedelta(minutes=rng.randrange(1, 1400))
        closing = opening + timedelta(minutes=rng.randrange(30, 3000))
        # Equivalent timezone representations must not change the feasible set.
        from datetime import timezone
        offset = timezone(timedelta(hours=rng.choice([-12, -4, 0, 8, 14])))
        window = (opening.astimezone(offset), closing.astimezone(offset))
        minutes = rng.choice([10, 30, 45, 60, 90, 180])
        busy = [(opening + timedelta(minutes=30), opening + timedelta(minutes=rng.randrange(45, 300)))]
        days = [opening.date() + timedelta(days=i) for i in range(4)]
        budgets = {d.isoformat(): rng.choice([0, 30, 60]) for d in days}
        task = {'due_at': (closing - timedelta(minutes=rng.randrange(0, 90))).astimezone(offset).isoformat()}
        due = datetime.fromisoformat(task['due_at'])
        feasible = []
        # Enumerate all local grid points independently, then apply constraints.
        for day in days:
            for minute in range(0, 24 * 60, 15):
                a = datetime.combine(day, time.min, tz) + timedelta(minutes=minute)
                b = a + timedelta(minutes=minutes)
                if not (a >= opening and a >= now + timedelta(minutes=10) and b <= closing and b <= due): continue
                if not (a >= datetime.combine(day, time(cfg.learning_start_hour), tz)
                        and b <= datetime.combine(day + timedelta(days=1) if cfg.learning_end_hour == 24 else day,
                                                  time(cfg.learning_end_hour % 24), tz)): continue
                if budgets.get(day.isoformat(), 0) + minutes > cfg.learning_daily_minutes: continue
                blocked = busy + ([] if minutes <= 10 else [
                    (datetime.combine(day, time(h), tz), datetime.combine(day, time(h + 1), tz)) for h in (12, 17)])
                if all(b <= x or a >= y for x, y in blocked): feasible.append((a, b))
        expected = min(feasible) if feasible else None
        actual = find_slot(task, now, busy, [], cfg, minutes, budgets, NO_CLASSES, window=window)
        assert actual == expected, {'case': case, 'now': now, 'window': window, 'minutes': minutes, 'expected': expected, 'actual': actual}
