"""Deterministic presentation; never invent actions or rewrite user content."""
from .tasks import format_task_block

def creation_messages(created, tz, aliases, task_card, keyboard):
    if len(created) == 1:
        task = created[0]
        timing = dict(task.get('timing') or {})
        if (timing.get('content') or '').strip().casefold() == task['title'].strip().casefold():
            timing.pop('content', None)
        task = {**task, 'timing': timing}
        labels = {'Content': '內容', 'Due': '期限', 'Scheduled': '預計處理',
                  'Course / event': '課程／活動', 'Time needs clarification': '待確認時間'}
        details = "\n".join(format_task_block(task, tz, project_aliases=aliases))
        for label, translated in labels.items():
            details = details.replace(f'<b>{label}</b>', f'<b>{translated}</b>')
        return [{**task_card(task, f"<b>已加入待辦</b>\n{details}"), "task_created": True}]
    # Bounded groups retain separate stable-ID edit buttons. Do not bind an
    # ambiguous multi-task message to one arbitrary task.
    messages, group, blocks = [], [], []
    def flush():
        if not group:
            return
        messages.append({"text": f"<b>已加入 {len(created)} 項待辦</b>\n\n" + "\n\n".join(blocks),
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
            block += "\n請透過這項任務的修改按鈕，補充這是期限、工作日期或活動日期。"
        if sum(map(len, blocks)) + len(block) > 3000:
            flush()
        group.append(task)
        blocks.append(block)
    flush()
    return messages
