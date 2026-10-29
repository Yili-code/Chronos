"""Explicit whole-afternoon allocation is a focus request, not completion."""
import re
from datetime import timedelta
from .task_actions import task_fields


def request(text, now):
    match = re.fullmatch(
        r'\s*(今天|今日|明天)?下午(?:的)?(?:時間|时间)?(?:全部|全都|都|全)(?:用來|用来|拿來|拿来|留給|留给)(?:完成|處理|处理|做)?\s*(task|任務|任务)\s*(#?)([1-9]\d*)[。.!！]?\s*',
        text, re.I)
    if not match:
        return None
    day = now + timedelta(days=1 if match[1] == '明天' else 0)
    start = max(now, day.replace(hour=13, minute=0, second=0, microsecond=0))
    end = day.replace(hour=18, minute=0, second=0, microsecond=0)
    return {'reference': int(match[4]), 'stable_id': bool(match[3]), 'start': start, 'end': end}


def validate(planner, review):
    target = review.get('focus_task')
    if target:
        task = planner.db.get_task(target['id'])
        if not task or task.get('status') != 'open' or task_fields(task) != target['snapshot']:
            from .study_reflow import ReflowReviewError
            raise ReflowReviewError('專注時段對應的任務已變更，請重新確認。沒有調整行程。')
