"""Explicit course-specific decisions; never expand one teacher's scope."""
from datetime import date

from .course_tracking import COURSE_SCHEDULE


def classday_action(db, command):
    usage = ('用法：/classday YYYY-MM-DD 課程代碼 class|off|auto\n'
             'class＝照課表詢問；off＝當日不詢問；auto＝恢復校曆判斷。\n' +
             '\n'.join(f'{slot.key}：{slot.name}' for slot in COURSE_SCHEDULE))
    parts = command.split()
    if len(parts) != 4:
        return lambda: usage
    _, day, course, decision = parts
    try:
        parsed = date.fromisoformat(day)
        slot = next(slot for slot in COURSE_SCHEDULE if slot.key == course)
        if parsed.isoformat() != day or parsed.weekday() != slot.weekday or decision not in {'class', 'off', 'auto'}:
            raise ValueError('unsupported decision')
    except (ValueError, StopIteration):
        return lambda: usage + '\n日期必須是該課原課表日；改期補課尚不由此指令處理。'
    def save():
        db.save_course_day_decision(f'{course}:{day}', decision)
        return f'已保存 {day} {slot.name}：{decision}。只影響這堂課，不撤回已送訊息。'
    return save
