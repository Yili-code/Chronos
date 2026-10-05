import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from chronos.academic_calendar import OFFICIAL_CALENDAR_URL
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from chronos.exam_scheduler import exam_notices, tick_exams
from chronos.exams import exam_action
from chronos.telegram import TelegramError


def snapshot(now):
    return {'source_url': OFFICIAL_CALENDAR_URL, 'fetched_at': now.isoformat(),
            'content_sha256': 'a' * 64, 'events': [{'start_date': '2026-10-26',
            'end_date': '2026-10-30', 'classification': 'exam_period', 'text': 'Midterm'}]}


def test_confirmation_and_morning_boundaries():
    for day, hour, count in ((18, 12, 0), (19, 8, 1), (25, 12, 1),
                             (26, 7, 0), (26, 8, 1), (31, 8, 0)):
        now = datetime(2026, 10, day, hour, tzinfo=TAIPEI)
        assert len(exam_notices(snapshot(now), [], now)) == count
        assert exam_notices(snapshot(now - timedelta(days=1)), [], now) == []


def test_only_explicit_today_exams_are_sent_once(tmp_path):
    db = Database(tmp_path / 'exams.db')
    db.initialize()
    now = datetime(2026, 10, 26, 8, tzinfo=TAIPEI)
    db.save_calendar_snapshot(snapshot(now))
    exam_action(db, 'exam OS | Midterm | 2026-10-26 09:00 | A | Ch1 | Lab')()
    exam_action(db, 'exam DB | Midterm | ? | ? | ? | ?')()
    bot = AsyncMock()
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
    for _ in range(2):
        asyncio.run(tick_exams(db, bot, 123, now))
    assert bot.send_message.await_count == 2
    texts = '\n'.join(call.args[1] for call in bot.send_message.call_args_list)
    assert 'OS' in texts and 'DB' not in texts


def test_unknown_send_stops_later_parts(tmp_path):
    db = Database(tmp_path / 'exams.db')
    db.initialize()
    now = datetime(2026, 10, 26, 8, tzinfo=TAIPEI)
    db.save_calendar_snapshot(snapshot(now))
    exam_action(db, 'exam OS | Midterm | 2026-10-26 09:00 | A | Ch1 | Lab')()
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramError('unknown')
    for _ in range(2):
        asyncio.run(tick_exams(db, bot, 123, now))
    assert bot.send_message.await_count == 1


def test_edit_between_rejected_attempt_and_retry_keeps_original_batch(tmp_path):
    db = Database(tmp_path / 'exams.db')
    db.initialize()
    now = datetime(2026, 10, 26, 8, tzinfo=TAIPEI)
    db.save_calendar_snapshot(snapshot(now))
    exam_action(db, 'exam OS | Midterm | 2026-10-26 09:00 | A | Original scope | Lab')()
    bot = AsyncMock()
    bot.send_message.side_effect = [{'ok': True, 'result': {'message_id': 42}},
                                    {'ok': False, 'error_code': 400}]
    asyncio.run(tick_exams(db, bot, 123, now))
    exam_action(db, 'exam OS | Midterm | 2026-10-27 10:00 | B | Changed scope | Lab')()
    reopened = Database(db.path)
    bot.send_message.side_effect = None
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 43}}
    asyncio.run(tick_exams(reopened, bot, 123, now + timedelta(minutes=5)))
    assert 'Original scope' in bot.send_message.call_args.args[1]
    assert 'Changed scope' not in bot.send_message.call_args.args[1]
    assert reopened.list_exams()[0]['scope'] == 'Changed scope'
