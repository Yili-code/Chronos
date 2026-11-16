"""Review and book multiple blocks for one unscheduled task."""
from datetime import datetime, timedelta
from hashlib import sha256
from html import escape
import time
from uuid import uuid4

from .backward_planning import reserved_windows
from .google_calendar import CalendarError
from .learning_calendar import find_slot
from .study_reset import constraints, execute
from .task_actions import task_fields

MIN_BLOCK = 15


def allocate(task, now, window, busy, settings, minutes, budgets, policy):
    """Fill early openings while retaining enough minutes for a final block."""
    blocks = []
    remaining = minutes
    begin, limit = window
    while remaining >= MIN_BLOCK:
        first = find_slot(task, now, busy, [], settings, MIN_BLOCK, budgets, policy, window=(begin, limit))
        if not first:
            break
        start, stop = first
        for length in range(remaining, MIN_BLOCK - 1, -1):
            if 0 < remaining - length < MIN_BLOCK:
                continue
            end = start + timedelta(minutes=length)
            if end <= limit and find_slot(task, now, busy, [], settings, length, budgets, policy,
                                          window=(start, end)) == (start, end):
                stop = end
                break
        length = int((stop - start).total_seconds() / 60)
        blocks.append((start, stop, length))
        day = start.date().isoformat()
        budgets[day] = budgets.get(day, 0) + length
        begin = stop
        remaining -= length
    return blocks, remaining


def card(planner, task, state, text, actions):
    buttons = planner.buttons(task["id"], state, actions)
    return {"messages": [{"text": text, "parse_mode": "HTML",
                          "reply_markup": {"inline_keyboard": [[button] for button in buttons]},
                          "learning_proposal": {"task_id": task["id"], "token": state["token"],
                                                "status": "no_slot"}}]}


def block_lines(sessions):
    lines = []
    for session in sessions:
        start, end = (datetime.fromisoformat(session[k]) for k in ("start", "end"))
        lines.append(f"• {start:%m/%d %H:%M}–{end:%H:%M}（{session['minutes']} 分鐘）")
    return lines


def budgets_for(db, begin, end):
    days = [(begin.date() + timedelta(days=offset)).isoformat()
            for offset in range((end.date() - begin.date()).days + 1)]
    return {day: sum((db.get_task_action_state("learning:day:" + day) or {}).values()) for day in days}


async def preview(planner, task, state, now):
    search = state.get("availability_search")
    if state["status"] != "no_slot" or state["minutes"] < 2 * MIN_BLOCK or not search:
        return "目前沒有可分次安排的時段。請查看 /plan。"
    begin, end = (datetime.fromisoformat(search[k]) for k in ("start", "end"))
    begin = max(now, begin)
    alternatives = [("retry", "Replan"), ("postpone", "Defer")]
    if begin >= end:
        return card(planner, task, state, "原搜尋時段已過。請重新安排。", alternatives)
    await planner.calendar.verify_owner()
    busy = [*await planner.calendar.busy(begin, end), *reserved_windows(planner.db)]
    budget = budgets_for(planner.db, begin, end)
    cfg = constraints(planner)
    blocks, missing = allocate(task, now, (begin, end), busy, planner.settings, state["minutes"],
                               budget, cfg["course_policy"])
    if missing or len(blocks) < 2:
        text = (f"原時段內可分配 {state['minutes'] - missing}／{state['minutes']} 分鐘。"
                "\n請調整時長或指定更大的時段。" if missing else "目前已有連續空檔，可以重新安排。")
        return card(planner, task, state, text, alternatives)
    token = uuid4().hex
    sessions = [{"task_id": task["id"], "title": task["title"], "start": start.isoformat(),
                 "end": stop.isoformat(), "minutes": length, "original_minutes": length,
                 "token": sha256(f"split-token:{token}:{index}".encode()).hexdigest()[:32],
                 "event_id": sha256(f"chronos-split:{token}:{index}".encode()).hexdigest()}
                for index, (start, stop, length) in enumerate(blocks)]
    draft = {"token": token, "sessions": sessions, "constraints": cfg, "snapshot": task_fields(task),
             "expires_at": (now + timedelta(minutes=15)).isoformat()}
    saved = planner.update(task["id"], lambda old: {**old, "split_draft": draft} if old == state else old)
    if ((saved or {}).get("split_draft") or {}).get("token") != token:
        return "原安排已更新。請使用最新卡片。"
    text = "\n".join([f"<b>分次完成</b>｜{escape(task['title'])}", *block_lines(sessions),
                      f"合計 {state['minutes']} 分鐘。確認後才加入 Calendar。"])
    return card(planner, task, state, text, [("splitconfirm", "確認分次安排"), *alternatives])


async def confirm(planner, task, state, now):
    draft = state.get("split_draft")
    operation_key = state.get("split_operation")
    if state["status"] not in {"no_slot", "creating"} or not (draft or operation_key):
        return "分次安排已失效，請查看 /plan。"
    if not operation_key and (draft["expires_at"] <= now.isoformat()
                              or any(datetime.fromisoformat(s["start"]) <= now for s in draft["sessions"])):
        return "這份分次安排已過期。請重新安排。"
    db = planner.db
    claim = uuid4().hex
    lease = db.mutate_task_action_state("learning:tick", lambda old: {
        "claim": claim, "until": (now + timedelta(minutes=10)).isoformat()}
        if not old or old["until"] <= now.isoformat() else old)
    if lease["claim"] != claim:
        return "排程正在處理，請稍後再點一次。"
    try:
        if not operation_key:
            slots = draft["sessions"]
            ranges = [tuple(datetime.fromisoformat(s[k]) for k in ("start", "end")) for s in slots]
            await planner.calendar.verify_owner()
            busy = [*await planner.calendar.busy(ranges[0][0], ranges[-1][1]), *reserved_windows(db)]
            if constraints(planner) != draft["constraints"]:
                return "空檔或排程限制已變更。請重新安排，再確認新的分次時段。"
            initial_budget = budgets_for(db, ranges[0][0], ranges[-1][1])
            budget = dict(initial_budget)
            for session, (begin, end) in zip(slots, ranges):
                minutes = session["minutes"]
                if find_slot(task, now, busy, [], planner.settings, minutes, budget,
                             draft["constraints"]["course_policy"], window=(begin, end)) != (begin, end):
                    return "可安排的時間已變更。請重新安排，再確認新的分次時段。"
                day = begin.date().isoformat()
                budget[day] += minutes
            operation_key = "learning:split:" + draft["token"]

            def reserve():
                fresh = planner.get(task["id"])
                live = db.get_task(task["id"])
                if (fresh != state or not live or live["status"] != "open" or task_fields(live) != draft["snapshot"]
                        or budgets_for(db, ranges[0][0], ranges[-1][1]) != initial_budget):
                    return None
                creating = {**fresh, "status": "creating", "split_operation": operation_key}
                operation = {**draft, "status": "applying", "removed": [], "inserted": [], "deleted": [],
                             "exams": [], "factor": 1,
                             "snapshots": {str(task["id"]): {"task": draft["snapshot"], "state": creating}}}
                db._save_task_action_state(f"learning:task:{task['id']}", creating)
                db._save_task_action_state(operation_key, operation)
                return operation

            operation = db._run_task_action(reserve)
            if not operation:
                return "原任務或安排已更新。請使用最新卡片。"
        else:
            operation = db.get_task_action_state(operation_key)
        try:
            await execute(planner, operation, now, claim, time.monotonic() + 90, state_key=operation_key)
        except CalendarError:
            return "分次排程尚未完成。請確認時段仍可使用，再按一次「確認分次安排」；已建立的時段會先核對。"
        return "\n".join(["已安排分次完成", task["title"], *block_lines(operation["sessions"])])
    finally:
        db.mutate_task_action_state("learning:tick", lambda old: {**old, "until": now.isoformat()}
                                    if old and old.get("claim") == claim else old)
