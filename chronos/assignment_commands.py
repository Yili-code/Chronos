"""Explicit deadline input without AI date inference or a network request."""
from datetime import datetime
import re

from .course_tracking import TAIPEI


def deadline_action(db, command: str):
    match = re.fullmatch(r"deadline\s+([1-9]\d*)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", command)
    usage = "用法：/deadline 作業固定ID YYYY-MM-DD HH:MM（台北時間；不是 /tasks 的清單順位）。"
    if match is None:
        return lambda: usage
    try:
        deadline = datetime.strptime(f"{match[2]} {match[3]}", "%Y-%m-%d %H:%M").replace(tzinfo=TAIPEI)
    except ValueError:
        return lambda: usage
    def confirm():
        if not db.confirm_assignment_deadline(int(match[1]), deadline):
            return "找不到仍待完成的作業，未變更截止時間。"
        return f"作業 #{match[1]} 截止時間已確認：{deadline:%Y-%m-%d %H:%M}（台北時間）。"
    return confirm
