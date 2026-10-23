"""Validated natural-language routing; models choose data, never executable commands."""
from datetime import date, datetime, timedelta
from html import escape
import re
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .ai import AIError
from .google_calendar import CalendarError
from .gmail import GmailError
from .task_actions import task_fields


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action: Literal[
        "task_create", "task_edit", "task_complete", "task_list", "task_review", "undo", "clear_tasks", "event_create",
        "plan_list", "plan_request", "plan_edit", "plan_feedback", "backward", "backward_progress",
        "replan", "agenda", "free", "info", "reminder_create", "reminder_list", "reminder_cancel",
        "classday", "calendar_view", "calendar_edit", "ai_usage", "announcements", "announcement",
        "mail_next", "cancel", "clarify", "unsupported",
    ]
    task_ids: list[Annotated[int, Field(strict=True, ge=1)]] = Field(default_factory=list, max_length=100)
    instruction: str = Field(default="", max_length=4000)
    question: str | None = Field(default=None, max_length=250)
    start: datetime | None = None
    end: datetime | None = None
    reference: str | None = Field(default=None, max_length=64, pattern=r"^[a-f0-9]+$")
    page: int = Field(default=1, ge=1, le=999999)
    minutes: int | None = Field(default=None, ge=1, le=1440)
    outcome: Literal["done", "partial", "skipped"] | None = None
    operation: Literal["enable", "disable", "reset", "term"] | None = None
    day: date | None = None
    end_day: date | None = None
    title: str | None = Field(default=None, min_length=1, max_length=150)
    content: str | None = Field(default=None, max_length=2000)
    event_category: Literal["contest", "meeting", "social", "other"] | None = None
    edit_field: Literal["title", "content"] | None = None


TARGET_ACTIONS = {"task_edit", "task_complete", "plan_request", "plan_edit", "plan_feedback"}


def explicit_event_minutes(text):
    """Preserve explicit numeric duration even if the classifier omits it."""
    matches = re.findall(r"(?<![\d:])(\d+(?:\.\d+)?)\s*(hours?|hrs?|h\b|小時|小时|minutes?|mins?|min\b|分鐘|分钟)", text, re.I)
    if not matches:
        return None
    total = sum(float(value) * (60 if unit.lower().startswith('h') or unit in {'小時', '小时'} else 1)
                for value, unit in matches)
    return total if total == int(total) else -1


async def prepare(app, text, *, update_id=None, chat_id=None, bound_task_id=None):
    """Run external interpretation before the caller's task/receipt transaction."""
    now = datetime.now(app.settings.tz)
    key = f"intent:clarify:{chat_id}"
    persistent = chat_id is not None and update_id is not None
    pending = app.db.get_task_action_state(key) if persistent else None
    if pending and pending.get("expires_at", "") <= now.isoformat():
        pending = None
    items = app.tasks.list_open()
    by_id = {item["id"]: item for item in items}
    if bound_task_id is not None and bound_task_id not in by_id:
        return lambda: "這項任務已結束，請從 /task 選擇其他任務。"
    context = {"bound_task_id": bound_task_id, "clarification": pending}
    reminder_items = list((app.db.get_task_action_state(app.reminders.KEY) or {}).values()) if persistent else []
    reminder_items = [item for item in reminder_items if item.get("chat_id") == chat_id
                      and item.get("status") in {"active", "pending"}]
    bulletins = app.db.list_announcements() if persistent else []
    try:
        intent = await app.ai.classify_intent(text, [
            {"id": item["id"], "position": position, **task_fields(item)}
            for position, item in enumerate(items, 1)
        ], now, context=context,
            reminders=[{k: item.get(k) for k in ("token", "title", "start")} for item in reminder_items],
            announcements=[{"id": token, "title": record["announcement"]["title"]}
                           for token, record in bulletins])
        # Also validate tool substitutes and malformed provider return values.
        intent = Intent.model_validate(intent)
    except (AIError, ValueError) as error:
        return lambda reply=str(error): "目前無法理解這個要求，沒有變更任何內容。\n" + escape(reply)

    instruction = intent.instruction or text
    # Keep the owner's field choice across a clarification, outside model discretion.
    content_choice = bool(re.search(r"(?:修改|更改|改|更新).{0,6}(?:內容|内容)|\b(?:edit|change|update)\s+(?:the\s+)?content\b", text, re.I))
    field_answer = bool(re.fullmatch(r"\s*(?:(?:修改|更改|改|更新)(?:他|它|這個任務|这个任务)?的?(?:內容|内容)|(?:edit|change|update)\s+(?:the\s+)?content)[。.!！]?\s*", text, re.I))
    if field_answer and pending and len(pending.get("candidate_ids", [])) == 1:
        intent.action = "task_edit"
        intent.task_ids = pending["candidate_ids"]
        intent.question = None
        intent.edit_field = "content"
        instruction = pending["instruction"] + "\nOnly change content; preserve title."
    elif intent.action == "task_edit" and (content_choice or intent.edit_field == "content"
            or re.search(r"剩下|還剩|还剩|\bremaining\b", text, re.I)
            or (pending and pending.get("edit_field") == "content" and intent.task_ids == pending.get("candidate_ids"))):
        intent.edit_field = "content"
        instruction += "\nOnly change content; preserve title."
    ids = list(dict.fromkeys(intent.task_ids))
    if bound_task_id is not None and intent.action in TARGET_ACTIONS:
        if ids and ids != [bound_task_id]:
            return lambda: "回覆的任務與指定目標不同，請重新選擇任務。沒有變更。"
        ids = [bound_task_id]

    def clarify(question, candidates=None):
        candidates = (candidates or [])[:8]
        lines = [escape(question)] + [f"{n}. {escape(by_id[i]['title'][:160])} (#{i})"
                              for n, i in enumerate(candidates, 1) if i in by_id]
        state = {"instruction": instruction, "action": intent.action, "edit_field": intent.edit_field, "candidate_ids": candidates,
                 "snapshots": {str(i): task_fields(by_id[i]) for i in candidates if i in by_id},
                 "expires_at": (now + timedelta(minutes=10)).isoformat(), "token": uuid4().hex}
        def save():
            if persistent:
                app.db.mutate_task_action_state(key, lambda old: state)
            return app.message_bundle({"text": "\n".join(lines), "parse_mode": "HTML"})
        return save

    if any(i not in by_id for i in ids):
        return clarify("找不到指定的未完成任務，請重新指定名稱。")
    if intent.action in TARGET_ACTIONS and len(ids) != 1:
        return clarify("要操作哪一項任務？", ids or list(by_id))
    if intent.action == "clarify" or intent.question:
        return clarify(intent.question or "請補充要處理的事項。", ids)
    if intent.action == "unsupported":
        return lambda: "目前無法處理這個要求。可以查行程、新增或調整任務。"
    if ids and pending:
        for task_id in ids:
            snapshot = pending.get("snapshots", {}).get(str(task_id))
            if snapshot is not None and task_fields(by_id[task_id]) != snapshot:
                return clarify("這項任務剛有變更，請重新說明要做的操作。", [task_id])

    task_id = ids[0] if len(ids) == 1 else None
    selected = by_id.get(task_id)
    # Fixed commands below are internal adapters to existing validated handlers.
    # Neither the model nor the user can supply a command name here.
    simple = {"task_list": "/task", "task_review": "/reviewtasks", "undo": "/undo", "clear_tasks": "/clear",
              "plan_list": "/plan", "free": "/free", "info": "/info",
              "calendar_view": "/calendar", "ai_usage": "/ai_usage"}
    try:
        if intent.action in simple:
            action = await app.prepare_message(simple[intent.action], update_id=update_id, chat_id=chat_id)
        elif intent.action == "task_create":
            action = await app.prepare_message(instruction, update_id=update_id, chat_id=chat_id, create_only=True)
        elif intent.action == "task_edit":
            action = await app.prepare_message(f"/edit #{task_id} {instruction}", update_id=update_id)
        elif intent.action == "event_create":
            if not persistent:
                return lambda: "請在 Telegram 對話新增活動。"
            if not intent.title or not intent.event_category or not intent.start:
                return clarify("請補充活動名稱、分類及開始時間。")
            stated_minutes = explicit_event_minutes(text)
            if stated_minutes is not None:
                if not 1 <= stated_minutes <= 1440 or (intent.minutes is not None and intent.minutes != stated_minutes):
                    return clarify("活動時長與解析結果不一致，請確認開始時間及時長。")
                intent.minutes = int(stated_minutes)
            end = intent.end
            if end is None and intent.minutes is not None:
                end = intent.start + timedelta(minutes=intent.minutes)
            if (intent.start.utcoffset() is None or end is None or end.utcoffset() is None
                    or not timedelta(0) < end - intent.start <= timedelta(days=1)
                    or intent.start <= now
                    or (intent.minutes is not None and end - intent.start != timedelta(minutes=intent.minutes))):
                return clarify("請提供未來活動的開始與結束時間，或明確時長。")
            from .free_booking import preview_explicit_event
            result = preview_explicit_event(app.db, app.settings, chat_id, update_id,
                intent.title, intent.start, end, intent.event_category,
                '\n'.join(dict.fromkeys(filter(None, [intent.content, instruction, text]))), now)
            action = lambda: result
        elif intent.action == "task_complete":
            def action():
                current = app.tasks.list_open()
                if app.edit_conflict(current, selected):
                    return "任務已變更，請重新確認。沒有標記完成。"
                aliases = app.tasks.project_aliases()
                if not app.tasks.complete(task_id):
                    return "這項任務已結束。"
                return f"Completed {escape(selected['title'])}\n\n" + app.format_tasks(
                    [item for item in current if item["id"] != task_id], app.settings.tz, project_aliases=aliases)
        elif intent.action in {"plan_request", "plan_edit", "plan_feedback"}:
            if not app.settings.enable_learning_calendar:
                return lambda: "目前未啟用排程。"
            if intent.action == "plan_request":
                action = await app.prepare_message(f"/plan {task_id}", update_id=update_id)
            elif intent.action == "plan_edit":
                action = await app.prepare_message(f"/planedit {task_id} {instruction}", update_id=update_id)
            elif intent.minutes is None or intent.outcome is None:
                return clarify("這次花了幾分鐘？完成、部分完成，還是略過？", ids)
            else:
                action = await app.learning_calendar().prepare_feedback(selected, intent.outcome, intent.minutes, instruction)
        elif intent.action == "agenda":
            start, end = intent.start, intent.end
            if (not start or not end or start.utcoffset() is None or end.utcoffset() is None
                    or not timedelta(0) < end - start <= timedelta(days=7)):
                return clarify("想查哪一天、哪個時段的行程？")
            start, end = start.astimezone(app.settings.tz), end.astimezone(app.settings.tz)
            await app.google_calendar.verify_owner()
            events = await app.google_calendar.agenda(start, end)
            from .evening_agenda import render_window_agenda
            parts = render_window_agenda(events, start, end, app.settings.tz, db=app.db)
            result = app.message_bundle(*({"text": part, "parse_mode": "HTML"} for part in parts))
            action = lambda: result
        elif intent.action in {"replan", "backward", "backward_progress"}:
            if not persistent or not app.settings.enable_learning_calendar:
                return lambda: "請在已啟用排程的 Telegram 對話操作。"
            if intent.action == "replan":
                result = await app.calendar_replan.preview(app.learning_calendar(), chat_id, instruction, update_id)
            else:
                planner = app.BackwardPlanner(app.db, app.google_calendar, app.ai, app.settings)
                if intent.action == "backward":
                    result = await planner.preview(chat_id, instruction, update_id)
                else:
                    state = (app.db.get_task_action_state(f"backward:goal:{task_id}") if task_id
                             else planner.session(chat_id))
                    result = planner.progress(state["token"], chat_id) if state and state.get("token") else "尚無對應計畫。"
            action = lambda: result
        elif intent.action in {"reminder_create", "reminder_list", "reminder_cancel"}:
            if not persistent:
                return lambda: "請在 Telegram 對話管理提醒。"
            if intent.action == "reminder_cancel":
                if not intent.reference or intent.reference not in {item["token"] for item in reminder_items}:
                    return clarify("要取消哪一個提醒？請提供名稱或時間。")
                action = lambda: app.reminders.cancel(app.db, intent.reference, chat_id)
            else:
                result = await app.reminders.command(app.db, app.ai, app.google_calendar, app.settings,
                    "/reminders" if intent.action == "reminder_list" else "/remind " + instruction, chat_id, update_id)
                action = lambda: result
        elif intent.action == "classday":
            action = await app.prepare_message("/classday " + instruction, update_id=update_id)
        elif intent.action == "calendar_edit":
            if not intent.operation or not intent.day or (intent.operation == "term" and not intent.end_day):
                return clarify("要修改哪一天的掃描設定？")
            command = f"/calendar {intent.operation} {intent.day.isoformat()}"
            if intent.operation == "term":
                command += " " + intent.end_day.isoformat()
            action = await app.prepare_message(command, update_id=update_id)
        elif intent.action == "announcements":
            action = await app.prepare_message(f"/announcements {intent.page}", update_id=update_id)
        elif intent.action == "announcement":
            if not intent.reference or intent.reference not in {token for token, _ in bulletins}:
                return clarify("想看哪一則公告？請提供標題。")
            action = await app.prepare_message(f"/announcement {intent.reference} {intent.page}", update_id=update_id)
        elif intent.action == "mail_next":
            if not persistent or not app.settings.enable_gmail:
                return lambda: "目前未啟用郵件整理。"
            result = await app.mail_workflow().daily(backlog=True, request_key=str(update_id))
            reply = (f"還有 {result['waiting']} 封待處理，請先處理郵件卡片。"
                     if result.get("waiting") else "下一批已送出，請查看郵件卡片。")
            action = lambda: reply
        elif intent.action == "cancel":
            action = lambda: "已取消這次詢問。"
        else:
            return lambda: "請補充要處理的事項。沒有變更。"
    except (AIError, CalendarError, GmailError, ValueError) as error:
        return lambda reply=str(error): "未能完成這個要求，請稍後重試。\n" + escape(reply)

    def execute():
        def transaction():
            saved = app.db.get_task_action_state(key) if persistent else None
            # Recheck the task after external work; list positions never become mutation IDs.
            if selected and intent.action in {"task_edit", "task_complete", "plan_request"}:
                if app.edit_conflict(app.tasks.list_open(), selected):
                    return "任務已變更，請重新確認要做的操作。"
            result = action()
            if saved and pending and saved.get("token") == pending.get("token"):
                app.db._save_task_action_state(key, {})
            return result
        return app.db._run_task_action(transaction) if persistent else transaction()
    return execute
