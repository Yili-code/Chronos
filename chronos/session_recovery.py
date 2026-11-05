"""Owner-confirmed recovery after a work session; never infer an outcome."""
from datetime import datetime, timedelta
from html import escape
from uuid import uuid4

from .task_actions import task_fields

REASONS = {"busy": "An interruption", "forgot": "Forgot", "load": "Too much planned",
           "large": "Task felt too large", "irrelevant": "No longer needed", "private": "Prefer not to say"}
ACTIONS = {"recover", "rbusy", "rforgot", "rload", "rlarge", "rirrelevant", "rprivate",
           "rwrite", "ragain", "rshort", "rdefer", "rstop", "rconfirm", "rback"}


def card(planner, task, state, now=None):
    recovery = state["recovery"]
    stage = recovery["stage"]
    lines = [f"<b>下一步安排</b>\n{escape(task['title'])}",
             "這次尚未開始，任務仍保留。"]
    count = recovery.get("missed_count", 1)
    if count >= 2:
        lines.append(f"已回報 {count} 次尚未開始。可縮小第一步或換個時段，避免只是不斷延後。")
    from .nightly_planning import deadline
    due = deadline(task, planner.settings)
    if due:
        label = "期限已過" if due <= (now or datetime.fromisoformat(recovery["reported_at"])) else "期限"
        lines.append(f"{label}: {due:%m/%d %H:%M}. 延後會減少剩餘時間，原期限不變。")
    if stage == "reason":
        lines.append("這次未開始的原因？可選擇下方項目或自行說明。")
        values = [("r" + key, {"busy": "臨時有事", "forgot": "忘記了", "load": "安排太多", "large": "任務太大", "irrelevant": "已不需要", "private": "不方便說明"}[key]) for key in REASONS] + [("rwrite", "其他原因")]
    elif stage == "stop_confirm":
        lines.append("停止安排這個任務？任務會保留為未完成，原期限與過去的 Calendar 行程也會保留。")
        values = [("rconfirm", "確認停止安排"), ("rback", "返回選擇")]
    else:
        reason = recovery.get("reason", "未指定")
        labels = {"An interruption": "臨時有事", "Forgot": "忘記了", "Too much planned": "安排太多",
                  "Task felt too large": "任務太大", "No longer needed": "已不需要", "Prefer not to say": "不方便說明"}
        lines.append("未開始原因：" + escape(labels.get(reason, reason)))
        if reason == "Forgot":
            lines.append("可以重新安排到方便開始的時間；這次回報不會自動新增提醒。")
        if recovery.get('reason') in {'Too much planned', 'Task felt too large'}:
            lines.append("可以先安排較短的時段，任務範圍仍完整保留。")
        elif recovery.get('reason') == 'No longer needed':
            lines.append("如果已經不需要，可以停止安排，任務仍會保留。")
        lines.append("請選擇重新安排、縮短時段或暫緩。 " +
            ("我會安排時段並回報結果；遇到衝突再請你決定。" if planner.settings.learning_auto_accept
             else "新的 Calendar 時段會先請你確認。"))
        values = [("ragain", "重新安排")]
        if state.get('minutes', 30) > 15:
            values.append(("rshort", "縮短時段"))
        values.extend([("rdefer", "暫緩安排"), ("rstop", "停止安排")])
    return {"messages": [{"text": "\n\n".join(lines), "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [planner.buttons(task['id'], state, values[i:i+2]) for i in range(0, len(values), 2)]},
        "learning_recovery": {"task_id": task['id'], "token": state['token'], "stage": stage}}]}


def bind(planner, message_id, message):
    ref = message.get("learning_recovery")
    if ref:
        planner.update(ref['task_id'], lambda old: {**old, 'message_id': message_id}
            if old and old.get('token') == ref['token'] and old.get('status') == 'awaiting_resolution'
            and old.get('recovery', {}).get('stage') == ref['stage'] else old)


def respond(planner, task, token, action, now, reason=None):
    def apply():
        state = planner.get(task['id'])
        live = next((t for t in planner.db.list_open_tasks() if t['id'] == task['id']), None)
        if (not state or state.get('token') != token or state.get('status') != 'awaiting_resolution'
                or not live or task_fields(live) != state.get('snapshot')):
            return "This follow-up is no longer current. Review /plan; nothing was changed."
        recovery = dict(state['recovery'])
        stage = recovery['stage']
        if action == 'recover':
            return card(planner, live, state, now)
        if stage == 'reason':
            if action == 'rwrite' and reason is None:
                return {'messages': [{'text': 'What got in the way? Tell me here, and we’ll work out the next step.',
                    'reply_markup': {'force_reply': True},
                    'learning_edit': {'task_id': task['id'], 'token': token, 'mode': 'recovery'}}]}
            if reason is None:
                reason_value = REASONS.get(action[1:])
            else:
                reason_value = reason.strip()[:1000]
            if not reason_value:
                return card(planner, live, state, now)
            recovery.update(stage='decision', reason=reason_value)
        elif stage == 'stop_confirm':
            if action == 'rback':
                recovery['stage'] = 'decision'
            elif action == 'rconfirm':
                recovery.update(stage='settled', decision='stop_planning', decided_at=now.isoformat())
            else:
                return card(planner, live, state, now)
        elif stage == 'decision':
            if action == 'rstop':
                recovery['stage'] = 'stop_confirm'
            elif action in {'ragain', 'rshort', 'rdefer'}:
                if action == 'rshort' and state.get('minutes', 30) <= 15:
                    return card(planner, live, state, now)
                recovery.update(stage='settled', decision=action, decided_at=now.isoformat())
            else:
                return card(planner, live, state, now)
        else:
            return "This decision has already been recorded. Review /plan."
        changed = {**state, 'recovery': recovery}
        if recovery['stage'] == 'settled':
            changed.update(status='postponed' if action in {'rdefer', 'rconfirm'} else 'needs_replan', token=uuid4().hex)
        planner.db._save_task_action_state('learning:task:' + str(task['id']), changed)
        planner.db._save_task_action_state('learning:history:' + token, {**changed, 'token': token})
        if recovery['stage'] != 'settled':
            return card(planner, live, changed, now)
        if changed['status'] == 'postponed':
            return ("Planning paused. The task remains open; its deadline is unchanged. "
                    f"Use /plan {task['id']} when ready. No Calendar event was changed.")
        return {'queue_recovery': True, 'short': action == 'rshort', 'minutes': state.get('minutes', 30)}
    result = planner.db._run_task_action(apply)
    if isinstance(result, dict) and result.get('queue_recovery'):
        minutes = max(15, min(180, result['minutes'] // 2)) if result['short'] else None
        reply, accepted = planner.request_proposals([task['id']], minutes=minutes)
        if not accepted:
            return reply
        opening = 'I’ll look for a shorter session' if result['short'] else 'I’ll find another time'
        return opening + (' and let you know when it’s scheduled.' if planner.settings.learning_auto_accept
                          else ' and show you a new time for confirmation.')
    return result


async def unanswered(planner, task, state, now):
    """One durable reminder after 24 hours; uncertainty is never auto-retried."""
    since = state.get('feedback_requested_at') or state.get('end')
    if (state.get('delivery') != 'feedback_sent' or state.get('outcome_reminder_count', 0)
            or not since or now < datetime.fromisoformat(since) + timedelta(hours=24)
            or not planner.settings.learning_start_hour <= now.hour < planner.settings.learning_end_hour):
        return
    changed = planner.update(task['id'], lambda old: {**old, 'outcome_reminder_count': 1, 'delivery': 'feedback_pending'}
        if old and old.get('token') == state['token'] and old.get('status') == 'awaiting_feedback'
        and old.get('delivery') == 'feedback_sent' and not old.get('outcome_reminder_count') else old)
    if changed and changed.get('delivery') == 'feedback_pending':
        await planner.deliver_feedback(task, changed)
