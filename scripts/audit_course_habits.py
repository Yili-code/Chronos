"""Ten synthetic course replies; --live samples Gemini without remote database writes."""
import argparse
import asyncio
from datetime import date
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chronos.ai import ExternalAI
from chronos.course_habits import CLARIFY_TEXT, parse_items, preference, prepare, prepare_reply
from chronos.course_tracking import COURSE_SCHEDULE, TAIPEI, new_session
from chronos.db import Database
from chronos.settings import Settings
from datetime import datetime


CASES = [
    ('通常都是 1. 學習本週上課內容 2. 完成至少六題程式設計題目', 'set', 'numbered'),
    ('每次下課後都先複習本週內容，再完成至少六題程式題。', 'set', 'prose'),
    ('以後固定：一、複習本週內容；二、完成至少六題程式題。', 'set', 'Chinese numerals'),
    ('記住這堂課的固定安排：\n- 複習本週內容\n- 完成至少六題程式題', 'set', 'bullets'),
    ('After every class, review this week\'s material and solve at least six programming problems.', 'set', 'English'),
    ('以後第二項改成完成至少十題程式題，第一項不變。', 'set', 'partial update'),
    ('以後這堂課都照這樣做。', 'set', 'shown reference'),
    ('這門課以後不要再固定提出課後任務。', 'disable', 'disable'),
    ('今天複習了投影片，也做了六題，不用記住。', 'progress', 'one-time report'),
    ('以後都照今天這樣做。', 'clarify', 'missing reference'),
]


class ObservedAI:
    def __init__(self, ai):
        self.ai = ai
        self.action = None

    async def _generate_output(self, *args, **kwargs):
        result = await self.ai._generate_output(*args, **kwargs)
        self.action = result.action
        return result


async def audit(ai=None):
    rows = []
    for number, (text, expected, label) in enumerate(CASES, 1):
        baseline = 'set' if parse_items(text) else 'progress'
        row = {'case': number, 'label': label, 'reply': text, 'expected': expected,
               'before': baseline, 'before_correct': baseline == expected}
        if ai is not None:
            with TemporaryDirectory() as directory:
                db = Database(Path(directory) / 'audit.db')
                db.initialize()
                slot = COURSE_SCHEDULE[4 if number == 10 else 5]
                session = new_session(slot, date(2026, 10, 22), 81)
                db.create_course_session(session)
                if number == 7:
                    prepare(db, session, 123, datetime(2026, 10, 22, 21, 15, tzinfo=TAIPEI))
                before = list(preference(db, slot.key))
                observed = ObservedAI(ai)
                action = await prepare_reply(db, observed, 81, text)
                result = db.process_update(100 + number, action or (lambda: db.record_course_reply(
                    81, 90, text, local_date=session.class_date)))
                after = preference(db, slot.key)
                actual = observed.action or ('progress' if action is None else
                                            'clarify' if result['reply'] == CLARIFY_TEXT else 'unavailable')
                checks = actual == expected and not db.list_open_tasks()
                if expected in {'progress', 'clarify'}:
                    checks = checks and before == after
                elif expected == 'disable':
                    checks = checks and after == []
                else:
                    checks = checks and len(after) == 2
                    if number in {2, 3, 4}:
                        checks = checks and '複習' in after[0] and '至少' in after[1] and ('六' in after[1] or '6' in after[1])
                    if number == 5:
                        checks = checks and 'review' in after[0].lower() and 'at least six' in after[1].lower()
                    if number == 6:
                        checks = checks and after[0] == before[0] and ('十' in after[1] or '10' in after[1])
                    if number == 7:
                        checks = checks and after == before
                row.update(after=actual, after_correct=checks, saved_items=after,
                           response=result['reply'])
        rows.append(row)
    return {'cases': rows, 'before_correct': sum(row['before_correct'] for row in rows),
            'after_correct': sum(row.get('after_correct', False) for row in rows) if ai else None,
            'live_provider_sample': ai is not None, 'production_writes': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = asyncio.run(audit(ExternalAI(Settings()) if args.live else None))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if args.live and result['after_correct'] != len(CASES):
        raise SystemExit(1)
