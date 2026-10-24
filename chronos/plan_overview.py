"""Read-only, bounded Telegram views for execution planning."""
from html import escape
from datetime import datetime

PAGE_SIZE = 6
PENDING = {"queued", "needs_replan", "cancelled", "no_slot"}
LABELS = {
    "planning": "Preparing a proposal", "queued": "To arrange",
    "proposed": "Awaiting approval", "creating": "Applying approved plan",
    "editing": "Awaiting your direction", "scheduled": "Scheduled",
    "awaiting_feedback": "Awaiting outcome", "awaiting_resolution": "Awaiting recovery decision", "no_slot": "No time block found",
    "needs_replan": "To arrange", "postponed": "Deferred",
    "backward_managed": "Deadline plan", "rejected": "Skipped",
    "cancelled": "Schedule removed", "finished": "Completed",
}


def view(planner, section="summary", page=0):
    items = [(task, planner.get(task["id"])) for task in planner.db.list_open_tasks()]
    items = [(task, state) for task, state in items if state]
    rows = []
    lines = []
    if not planner.calendar.configured():
        lines.append("Google Calendar authorization is still required.")
    if section == "summary":
        pending = [(task, state) for task, state in items if state["status"] in PENDING]
        deferred = sum(state["status"] == "postponed" for _, state in items)
        scheduled = sum(state["status"] == "scheduled" for _, state in items)
        attention = sum(state["status"] in {"proposed", "editing", "awaiting_feedback", "awaiting_resolution"}
                        or state.get("delivery", "").endswith(("_uncertain", "_sending"))
                        for _, state in items)
        if not items:
            lines.append("No task plans yet. Select a task from /task to arrange it.")
        else:
            lines.append(f"{len(pending)} to arrange · {deferred} deferred · {scheduled} scheduled")
            if attention:
                lines.append(f"{attention} awaiting your response or review. Open details to check them.")
            if pending:
                lines.extend(["", "Which task would you like to arrange?"])
                for task, _ in pending[:2]:
                    rows.append([{"text": short(task["title"]),
                                  "callback_data": f"task:plan:{task['id']}"}])
            rows.append([{"text": "View all plans", "callback_data": "planview:all:0"}])
            if scheduled:
                rows.append([{"text": "Scheduled tasks", "callback_data": "planview:scheduled:0"}])
    else:
        selected = [(task, state) for task, state in items
                    if section != "scheduled" or state["status"] == "scheduled"]
        page = min(max(page, 0), max(0, (len(selected) - 1) // PAGE_SIZE))
        lines.append("<b>Scheduled tasks</b>" if section == "scheduled" else "<b>All task plans</b>")
        for task, state in selected[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
            label = LABELS.get(state["status"], "Review required")
            if state.get("delivery", "").endswith(("_uncertain", "_sending")):
                label += " · delivery needs review"
            lines.extend(["", f"<b>{escape(short(task['title'], 140))}</b>", escape(label)])
            if state.get("start") and state.get("end"):
                start = datetime.fromisoformat(state["start"]).astimezone(planner.settings.tz)
                end = datetime.fromisoformat(state["end"]).astimezone(planner.settings.tz)
                lines.append(f"{start:%m/%d %H:%M}–{end:%m/%d %H:%M}")
            if state['status'] == 'awaiting_resolution':
                rows.append([{'text': 'Review next step', 'callback_data': f"learn:recover:{task['id']}:{state['token']}"}])
            if state["status"] in PENDING | {"postponed"}:
                rows.append([{"text": "Arrange: " + short(task["title"], 45),
                              "callback_data": f"task:plan:{task['id']}"}])
        if not selected:
            lines.append("No task plans in this view.")
        navigation = []
        if page:
            navigation.append({"text": "Previous", "callback_data": f"planview:{section}:{page - 1}"})
        if (page + 1) * PAGE_SIZE < len(selected):
            navigation.append({"text": "Next", "callback_data": f"planview:{section}:{page + 1}"})
        if navigation:
            rows.append(navigation)
        rows.append([{"text": "Back to overview", "callback_data": "planview:summary:0"}])
    return {"text": "\n".join(lines), "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": rows}}


def short(text, limit=60):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"
