"""Deterministic presentation; never invent actions or rewrite user content."""
from .tasks import format_task_block

def creation_messages(created, tz, aliases, task_card, keyboard):
    if len(created) == 1:
        task = created[0]
        details = "\n".join(format_task_block(task, tz, project_aliases=aliases))
        return [{**task_card(task, f"<b>I have added it, Sir.</b>\n{details}"), "task_created": True}]
    # Bounded groups retain separate stable-ID edit buttons. Do not bind an
    # ambiguous multi-task message to one arbitrary task.
    messages, group, blocks = [], [], []
    def flush():
        if not group:
            return
        messages.append({"text": f"<b>I have added {len(created)} tasks, Sir.</b>\n\n" + "\n\n".join(blocks),
                         "parse_mode": "HTML", "task_created": True,
                         "reply_markup": keyboard(group)})
        group.clear()
        blocks.clear()
    for task in created:
        # Acknowledgements are previews; the persisted task remains complete.
        def preview(value, limit):
            return value if len(value) <= limit else value[:limit] + "…"
        timing = dict(task.get("timing") or {})
        for key in ("content", "uncertain"):
            if timing.get(key):
                timing[key] = preview(timing[key], 160)
        shown = {**task, "title": preview(task["title"], 200), "timing": timing,
                 "project": preview(task["project"], 50) if task.get("project") else None}
        shown_aliases = {key: preview(value, 50) for key, value in aliases.items()}
        block = "\n".join(format_task_block(shown, tz, project_aliases=shown_aliases))
        block = f"#{task['id']} · " + block
        if (task.get("timing") or {}).get("uncertain"):
            block += "\nPlease clarify the time role using this task’s Edit button, Sir: deadline, work session, or event."
        if sum(map(len, blocks)) + len(block) > 3000:
            flush()
        group.append(task)
        blocks.append(block)
    flush()
    return messages
