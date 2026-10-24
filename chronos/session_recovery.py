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
    lines = [f"<b>Let’s decide the next step, Sir.</b>\n{escape(task['title'])}",
             "Recorded as not started. The task remains open."]
    count = recovery.get("missed_count", 1)
    if count >= 2:
        lines.append(f"Not started in {count} reported sessions. Consider a smaller first step or a different time; rescheduling alone may not help.")
    from .nightly_planning import deadline
    due = deadline(task, planner.settings)
    if due:
        label = "Deadline has passed" if due <= (now or datetime.fromisoformat(recovery["reported_at"])) else "Deadline"
        lines.append(f"{label}: {due:%m/%d %H:%M}. Delay reduces the remaining time; the deadline is unchanged.")
    if stage == "reason":
        lines.append("What got in the way? Choose one, or describe it in your own words.")
        values = [("r" + key, value) for key, value in REASONS.items()] + [("rwrite", "Other reason")]
    elif stage == "stop_confirm":
        lines.append("Stop arranging this task? It will stay in your task list, uncompleted. Its deadline and past Calendar event will be retained.")
        values = [("rconfirm", "Confirm stop planning"), ("rback", "Keep considering")]
    else:
        lines.append("Obstacle: " + escape(recovery.get("reason", "Not specified")))
        if recovery.get('reason') in {'Too much planned', 'Task felt too large'}:
            lines.append("A shorter first session may be more realistic. It will not reduce the recorded task scope.")
        elif recovery.get('reason') == 'No longer needed':
            lines.append("If this is no longer useful, consider stopping further planning rather than moving it again.")
        lines.append("Choose another time, a shorter first work session, or pause. " +
            ("I’ll arrange the session and report the result; exceptions will need your decision." if planner.settings.learning_auto_accept
             else "A new Calendar block requires a separate confirmation."))
        values = [("ragain", "Find another time")]
        if state.get('minutes', 30) > 15:
            values.append(("rshort", "Try a shorter session"))
        values.extend([("rdefer", "Defer for now"), ("rstop", "Stop planning")])
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
                return {'messages': [{'text': 'What prevented you from starting? Reply here. /cancel leaves the decision open.',
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
        return planner.request_proposals([task['id']], minutes=minutes)[0]
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
