from zoneinfo import ZoneInfo

from chronos.reply_voice import creation_messages


def test_batch_groups_long_tasks_and_preserves_every_button_and_uncertainty():
    tasks = [{"id": n, "title": "A & <B> " + "&" * 2000, "due_at": None,
              "timing": {"uncertain": "<" * 2000, "content": ">" * 2000}} for n in range(1, 21)]
    def keyboard(items):
        return {"inline_keyboard": [[{"text": f"Edit #{t['id']}", "callback_data": f"task:edit:{t['id']}"}] for t in items]}
    messages = creation_messages(tasks, ZoneInfo("Asia/Taipei"), {}, None, keyboard)
    assert 1 < len(messages) <= 20
    assert all(len(m["text"]) < 4096 and "task_id" not in m and m["task_created"] for m in messages)
    callbacks = [row[0]["callback_data"] for m in messages for row in m["reply_markup"]["inline_keyboard"]]
    assert callbacks == [f"task:edit:{n}" for n in range(1, 21)]
    assert sum(m["text"].count("Please clarify") for m in messages) == 20
    assert all("&amp; &lt;B&gt;" in m["text"] for m in messages)

    assert all(len(task["title"]) > 2000 for task in tasks)
    assert all(len(task["timing"]["content"]) == 2000 for task in tasks)
