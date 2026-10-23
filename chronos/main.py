from html import escape
import hmac
import json
import logging
import re
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from collections.abc import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .database_metrics import snapshot as database_metrics_snapshot
from .job_health import tracked_job, KEY as JOB_HEALTH_KEY
from .db import create_database
from .ai import AIError, ExternalAI
from .ai_usage import AIUsageRecorder, format_ai_usage
from .settings import settings
from .tasks import (
    TaskService,
    format_task_block,
    format_tasks,
    parse_deterministic_edit,
    parse_literal_task,
    project_display_name,
)
from .telegram import TelegramClient
from .runtime_health import (
    RuntimeConfigurationError,
    configured_components,
    readiness_snapshot,
    validate_runtime_security,
)
from .task_actions import UndoConflict
from .deadline_reminders import send_deadline_reminders
from .free_time import free_slot_offer
from .reply_policy import is_no_change_reply
from . import reminders
from . import calendar_replan
from .backward_planning import BackwardPlanner
from .free_booking import bind_free_message, preview_free_event, free_booking_callback
from . import task_review
from . import course_habits
from . import assignment_access
from . import preferences
from .evening_agenda import send_evening_agenda, tomorrow_agenda
from .task_timing import display_time, task_sort_key, timing_details
from .study_scheduler import tick_study, notify_study_failures
from .gmail import GmailClient, GmailError
from .mail_workflow import MailWorkflow
from .google_calendar import GoogleCalendar, CalendarError
from .learning_calendar import LearningCalendar, study_window

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chronos")

db = create_database(settings)
tasks = TaskService(db, settings.tz)
ai_usage = AIUsageRecorder(db, settings.tz)
ai = ExternalAI(settings, usage=ai_usage, preference_source=lambda:
                preferences.read(db, settings.telegram_chat_id) if settings.telegram_chat_id is not None else {})
telegram = TelegramClient(settings.telegram_bot_token)
gmail = GmailClient(settings)
google_calendar = GoogleCalendar(settings)
scheduler = AsyncIOScheduler(timezone=settings.tz)

HELP_TEXT = (
    "<b>Chronos</b>\n\n"
    "「報告改到星期五交」\n"
    "「今晚幫我空出來」\n"
    "「明天下午有什麼安排？」"
)
HELP_SECTIONS = {
    "tasks": "<b>任務</b>\n直接說「新增買牛奶」「報告做完了」，或回覆任務卡說「延到明天」。",
    "study": "<b>計畫</b>\n直接說「幫我安排報告的時間」「今晚幫我空出來」。行程調整會先供你確認。",
    "backward": "<b>期限規劃</b>\n直接說「幫我拆解下週五要交的報告，每天最多90分鐘」。確認後才建立任務與行程。",
    "assignments": "<b>作業</b>\n已追蹤作業會出現在 /task。不代寫或提交作業。",
}
# Empty markup also clears category buttons when returning from an old help card.
HELP_KEYBOARD = {"inline_keyboard": []}
HELP_BACK_KEYBOARD = {
    "inline_keyboard": [[{"text": "返回", "callback_data": "help:index"}]]
}

CLEAR_CONFIRM_TEXT = "Delete every task? This cannot be undone."
CLEAR_KEYBOARD = {
    "inline_keyboard": [
        [{"text": "Delete all tasks", "callback_data": "clear:confirm"}],
        [{"text": "Cancel", "callback_data": "clear:cancel"}],
    ]
}


def task_list_text(items: list[dict] | None = None) -> str:
    return format_tasks(
        tasks.list_open() if items is None else items,
        settings.tz,
        project_aliases=tasks.project_aliases(),
    )


def message_bundle(*messages: dict) -> dict:
    return {"messages": list(messages)}


def record_course_progress_reply(
    prompt_id: int, message_id: int, text: str, *, local_date
) -> str | dict:
    """Record course progress and return only its acknowledgement."""
    remembered = course_habits.remember_reply(db, prompt_id, text)
    return remembered or db.record_course_reply(prompt_id, message_id, text, local_date=local_date)


def task_edit_keyboard(items: list[dict]) -> dict:
    return {"inline_keyboard": [[{"text": f"Edit #{task['id']} · {task['title'][:40]}",
                                  "callback_data": f"task:edit:{task['id']}"}] for task in items[:50]]}


def task_card(task: dict, text: str) -> dict:
    if (task.get("timing") or {}).get("uncertain"):
        text += "\n\nIs this the deadline, planned work time, or course/event date? Reply with the intended role."
    return {"text": text, "parse_mode": "HTML", "task_id": task["id"],
            "reply_markup": task_edit_keyboard([task])}


def edit_conflict(items: list[dict], expected: dict) -> bool:
    latest = next((item for item in items if item["id"] == expected["id"]), None)
    return latest is not None and any(latest.get(key) != expected.get(key)
                                      for key in ("title", "due_at", "project", "timing"))


def pending_edit_keyboard(update_id: int, position: int) -> dict:
    return {
        "inline_keyboard": [
            [{"text": f"Retry editing Task {position}", "callback_data": f"edit:retry:{update_id}"}],
            [{"text": f"Cancel saved edit for Task {position}", "callback_data": f"edit:cancel:{update_id}"}],
        ]
    }


def format_update_summary(
    position: int,
    before: dict,
    after: dict,
    *,
    tag_alias: tuple[str, str] | None = None,
    aliases: dict[str, str] | None = None,
) -> str:
    aliases = dict(aliases or {})
    if tag_alias:
        aliases[tag_alias[0]] = tag_alias[1]
    lines = [f"<b>Updated · Task {position}</b>"]
    changes: list[tuple[str, str, str]] = []
    if before["title"] != after["title"]:
        changes.append(("Title", before["title"], after["title"]))
    old_times = dict(timing_details(before, settings.tz))
    new_times = dict(timing_details(after, settings.tz))
    for label in dict.fromkeys([*old_times, *new_times]):
        if old_times.get(label) != new_times.get(label):
            changes.append((label, old_times.get(label, "None"), new_times.get(label, "None")))
    if before.get("project") != after.get("project"):
        old_tag = _display_tag(before.get("project"), aliases)
        new_tag = _display_tag(after.get("project"), aliases)
        changes.append(("Tag", old_tag, new_tag))
    for label, old, new in changes:
        lines.extend(["", f"<b>{label}</b>", f"<b>Before</b> {escape(old)}", f"<b>After</b> {escape(new)}"])
    if tag_alias:
        project, alias = tag_alias
        lines.extend([
            "",
            "<b>Tag display preference</b>",
            f"{escape(project)} is now shown as #{escape(alias)}.",
        ])
    if not changes and not tag_alias:
        lines[0] = f"<b>No changes · Task {position}</b>"
        lines.extend(["", "No field values changed."])
    return "\n".join(lines)


def _display_due(value: str | None) -> str:
    return display_time(value, settings.tz)


def _display_tag(value: str | None, aliases: dict[str, str]) -> str:
    return f"#{project_display_name(value, aliases)}" if value else "None"


def clear_tasks_reply() -> str:
    deleted = tasks.clear()
    noun = "task" if deleted == 1 else "tasks"
    # The clear operation runs inside the same Firestore transaction as the
    # update receipt. Firestore forbids reads after the first write, and the
    # post-clear state is already known without another query.
    return f"Deleted {deleted} {noun}.\n\nNo open tasks. All clear."


@tracked_job(lambda: db)
async def send_daily_tasks() -> None:
    if settings.telegram_chat_id and telegram.enabled:
        result = await telegram.send_message(
            settings.telegram_chat_id,
            format_tasks(
                tasks.list_open(), settings.tz, project_aliases=tasks.project_aliases()
            ),
            parse_mode="HTML",
        )
        if not result.get("ok"):
            code = result.get("error_code", "unknown")
            raise RuntimeError(f"Telegram daily delivery failed with code {code}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_runtime_security(settings)
    db.initialize()
    if settings.enable_gmail:
        mail_workflow().require_config()
    if settings.enable_internal_scheduler:
        scheduler.add_job(run_reminder_tick, "interval", minutes=1, id="personal_reminders", replace_existing=True, max_instances=1)
        scheduler.add_job(send_daily_tasks, "cron", hour=8, minute=0, id="daily_tasks", replace_existing=True)
        scheduler.add_job(run_deadline_reminders, "interval", minutes=5, id="deadline_reminders", replace_existing=True, max_instances=1)
        if settings.enable_learning_calendar:
            scheduler.add_job(run_nightly_planning, "cron", hour=21, minute=0, timezone="Asia/Taipei",
                              id="nightly_planning", replace_existing=True, max_instances=1)
            scheduler.add_job(run_evening_agenda, "cron", hour=21, minute=0, timezone="Asia/Taipei",
                              id="evening_agenda", replace_existing=True)
        if settings.enable_gmail:
            scheduler.add_job(send_daily_mail, "cron", hour=7, minute=0, timezone="Asia/Taipei",
                              id="daily_mail", replace_existing=True)
        if settings.enable_study_tracking:
            scheduler.add_job(run_study_tick, "cron", second=0, id="study_tracking", replace_existing=True)
        scheduler.start()
    if settings.public_base_url and telegram.enabled:
        url = f"{settings.public_base_url.rstrip('/')}/telegram/webhook"
        try:
            result = await telegram.set_webhook(url, settings.telegram_webhook_secret)
            if not result.get("ok"):
                raise RuntimeError(f"Telegram webhook registration failed with code {result.get('error_code', 'unknown')}")
        except Exception:
            logger.exception("Telegram webhook setup failed")
    yield
    if settings.enable_internal_scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Chronos", lifespan=lifespan)


def mail_workflow():
    return MailWorkflow(db, gmail, telegram, ai, settings)


def learning_calendar():
    return LearningCalendar(db, google_calendar, telegram, settings, ai=ai)


async def deliver_learning_reply(chat_id, update_id, receipt):
    if receipt["delivered"]:
        return
    if any(m.get('task_review') for m in receipt.get('messages', [])):
        messages = receipt['messages']
        for count, part in enumerate(messages[receipt.get('delivered_count', 0):], receipt.get('delivered_count', 0) + 1):
            result = await telegram.send_message(chat_id, part['text'],
                **{k: part[k] for k in ('parse_mode', 'reply_markup') if k in part})
            if not result.get('ok') or (part.get('task_review') and result.get('result', {}).get('message_id') is None):
                raise HTTPException(status_code=502, detail='Task suggestion delivery pending')
            if part.get('task_review'):
                task_review.bind(db, chat_id, result['result']['message_id'], part)
            db.mark_update_message_delivered(update_id, count)
        db.mark_update_delivered(update_id)
        return
    if any(m.get('backward_plan') for m in receipt.get('messages', [])):
        for count, part in enumerate(receipt['messages'][receipt.get('delivered_count', 0):], receipt.get('delivered_count', 0)+1):
            result = await telegram.send_message(chat_id, part['text'],
                **{k: part[k] for k in ('parse_mode', 'reply_markup') if k in part})
            if not result.get('ok') or result.get('result', {}).get('message_id') is None:
                raise HTTPException(status_code=502, detail='Backward preview delivery pending')
            BackwardPlanner(db, google_calendar, ai, settings).bind(chat_id, result['result']['message_id'], part)
            db.mark_update_message_delivered(update_id, count)
        db.mark_update_delivered(update_id)
        return
    message = (receipt.get("messages") or [{"text": receipt["reply"]}])[0]
    options = {key: message[key] for key in ("parse_mode", "reply_markup") if key in message}
    result = await telegram.send_message(chat_id, message["text"], **options)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail="Planning reply pending")
    if message.get("free_booking") and result.get("result", {}).get("message_id") is not None:
        bind_free_message(db, chat_id, result["result"]["message_id"], message)
    if result.get("result", {}).get("message_id") is not None:
        calendar_replan.bind(db, chat_id, result["result"]["message_id"], message)
    if message.get("learning_edit"):
        message_id = result.get("result", {}).get("message_id")
        if message_id is None:
            raise HTTPException(status_code=502, detail="Planning prompt ID missing")
        db.mutate_task_action_state(f"learning:edit-message:{chat_id}:{message_id}", lambda old: message["learning_edit"])
        db.mutate_task_action_state(f"learning:edit-chat:{chat_id}", lambda old: {**message["learning_edit"],
            "expires_at": (datetime.now(settings.tz) + timedelta(hours=2)).isoformat()})
    if message.get("learning_menu"):
        message_id = result.get("result", {}).get("message_id")
        if message_id is None:
            raise HTTPException(status_code=502, detail="Planning menu ID missing")
        menu = message["learning_menu"]
        previous = learning_calendar().get(menu["task_id"]) or {}
        saved = learning_calendar().update(menu["task_id"], lambda old: {**old, "edit_message_id": message_id}
            if old and old.get("token") == menu["token"] and old["status"] == "editing" else old)
        if saved and saved.get("edit_message_id") == message_id:
            learning_calendar().retire(previous.get("edit_message_id"))
    if message.get("learning_proposal"):
        message_id = result.get("result", {}).get("message_id")
        if message_id is None:
            raise HTTPException(status_code=502, detail="Execution plan ID missing")
        proposal = message["learning_proposal"]
        previous = learning_calendar().get(proposal["task_id"]) or {}
        saved = learning_calendar().update(proposal["task_id"], lambda old: {**old, "message_id": message_id}
            if old and old.get("token") == proposal["token"] and old["status"] == "proposed" else old)
        if saved and saved.get("message_id") == message_id:
            learning_calendar().retire(previous.get("message_id"), previous.get("edit_message_id"))
    db.mark_update_delivered(update_id)


async def delete_telegram_messages(chat_id: int, *message_ids: int | None) -> None:
    """Delete resolved interaction cards; repeated cleanup is safe."""
    for message_id in dict.fromkeys(message_id for message_id in message_ids if message_id is not None):
        result = await telegram.request("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        missing = (result.get("error_code") == 400
                   and "message to delete not found" in result.get("description", "").lower())
        if not result.get("ok") and not missing:
            raise HTTPException(status_code=502, detail="Mail interaction cleanup pending")


@tracked_job(lambda: db)
async def send_daily_mail():
    if not settings.enable_gmail:
        return {"enabled": False}
    try:
        return await mail_workflow().daily()
    except GmailError as error:
        logger.warning("Mail digest: %s", error)
        raise HTTPException(status_code=503, detail=str(error)) from None


@app.get("/internal/mail/status")
async def mail_status(x_chronos_scheduler_secret: str | None = Header(default=None)) -> dict:
    if not settings.scheduler_secret:
        raise HTTPException(status_code=503, detail="Scheduler endpoint is not configured")
    if not hmac.compare_digest(x_chronos_scheduler_secret or "", settings.scheduler_secret):
        raise HTTPException(status_code=403, detail="Invalid scheduler credential")
    try:
        mail_workflow().require_config()
        account = await gmail.verify_account()
        chat = await telegram.request("getChat", {"chat_id": settings.telegram_chat_id})
        if not chat.get("ok") or chat.get("result", {}).get("id") != settings.telegram_chat_id:
            raise GmailError("Telegram owner chat could not be verified")
        return {"enabled": True, "gmail_account": account, "telegram_connected": True,
                "send_enabled": False, "timezone": "Asia/Taipei"}
    except GmailError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


@tracked_job(lambda: db)
async def run_study_tick() -> dict:
    await reminders.tick(db, telegram, settings)
    if not settings.enable_study_tracking:
        return {"enabled": False}
    if not settings.telegram_chat_id or not telegram.enabled:
        raise HTTPException(status_code=503, detail="Planning delivery is not configured")
    now = datetime.now(settings.tz)
    from .calendar_sync import sync_calendar
    from .calendar_scheduler import tick_calendar
    calendar_sync_result = await sync_calendar(db, now)
    calendar_result = await tick_calendar(db, telegram, settings.telegram_chat_id, now,
                                          sync_result=calendar_sync_result)
    result = await tick_study(db, telegram, settings.telegram_chat_id, now)
    from .assignment_scheduler import tick_assignments
    assignment_result = await tick_assignments(db, telegram, settings.telegram_chat_id, now)
    from .announcement_scheduler import tick_announcements
    announcement_result = await tick_announcements(db, telegram, settings.telegram_chat_id, now)
    notices = await notify_study_failures(db, telegram, settings.telegram_chat_id, now)
    return {**result, **assignment_result, **announcement_result, **notices,
            **calendar_sync_result, **calendar_result}


@app.post("/internal/study")
async def trigger_study(x_chronos_scheduler_secret: str | None = Header(default=None)) -> dict:
    if not settings.scheduler_secret:
        raise HTTPException(status_code=503, detail="Scheduler endpoint is not configured")
    if not hmac.compare_digest(x_chronos_scheduler_secret or "", settings.scheduler_secret):
        raise HTTPException(status_code=403, detail="Invalid scheduler credential")
    return await run_study_tick()


class TelegramChat(BaseModel):
    model_config = ConfigDict(strict=True)
    id: int = Field(ge=-(2**63), le=2**63 - 1)


class TelegramReplyReference(BaseModel):
    model_config = ConfigDict(strict=True)
    message_id: int = Field(gt=0)


class TelegramPhoto(BaseModel):
    model_config = ConfigDict(strict=True)
    file_id: str = Field(min_length=1, max_length=1024)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class TelegramMessage(BaseModel):
    model_config = ConfigDict(strict=True)
    chat: TelegramChat
    text: str | None = None
    caption: str | None = None
    photo: list[TelegramPhoto] = Field(default_factory=list, max_length=20)
    message_id: int | None = Field(default=None, gt=0)
    reply_to_message: TelegramReplyReference | None = None


class TelegramCallbackQuery(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str = Field(min_length=1, max_length=128)
    message: TelegramMessage
    data: str | None = None


class TelegramUpdate(BaseModel):
    model_config = ConfigDict(strict=True)
    update_id: int = Field(ge=0, le=2**63 - 1)
    message: TelegramMessage | None = None
    edited_message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/live")
async def live() -> dict:
    return {"status": "live"}


@app.get("/ready")
def ready() -> dict:
    try:
        return readiness_snapshot(db, settings)
    except (RuntimeConfigurationError, OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Service is not ready") from None


@app.get("/internal/status")
async def internal_status(x_chronos_scheduler_secret: str | None = Header(default=None)) -> dict:
    if not settings.scheduler_secret:
        raise HTTPException(status_code=503, detail="Scheduler endpoint is not configured")
    if not hmac.compare_digest(x_chronos_scheduler_secret or "", settings.scheduler_secret):
        raise HTTPException(status_code=403, detail="Invalid scheduler credential")
    snapshot = configured_components(settings)
    try:
        await run_in_threadpool(readiness_snapshot, db, settings)
    except (RuntimeConfigurationError, OSError, RuntimeError, ValueError):
        snapshot["readiness"] = "failed"
    else:
        snapshot["readiness"] = "ready"
    if telegram.enabled:
        try:
            webhook = await telegram.request("getWebhookInfo", {})
        except Exception:
            snapshot["telegram_webhook"] = "unknown"
        else:
            configured_url = f"{settings.public_base_url.rstrip('/')}/telegram/webhook"
            actual_url = webhook.get("result", {}).get("url") if webhook.get("ok") else None
            snapshot["telegram_webhook"] = "configured" if configured_url and actual_url == configured_url else "mismatch"
    snapshot["database_operations"] = database_metrics_snapshot()
    snapshot["jobs"] = await run_in_threadpool(db.get_task_action_state, JOB_HEALTH_KEY) or {}
    snapshot["reminders"] = await run_in_threadpool(reminders.health_snapshot, db)
    return snapshot


@app.post("/internal/mail/backlog")
async def trigger_mail_backlog(x_chronos_scheduler_secret: str | None = Header(default=None)) -> dict:
    if not settings.scheduler_secret or not hmac.compare_digest(x_chronos_scheduler_secret or "", settings.scheduler_secret):
        raise HTTPException(status_code=403, detail="Invalid scheduler credential")
    try:
        return await mail_workflow().daily(backlog=True, request_key="bootstrap")
    except GmailError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


@app.post("/internal/mail/daily")
async def trigger_daily_mail(x_chronos_scheduler_secret: str | None = Header(default=None)) -> dict:
    if not settings.scheduler_secret:
        raise HTTPException(status_code=503, detail="Scheduler endpoint is not configured")
    if not hmac.compare_digest(x_chronos_scheduler_secret or "", settings.scheduler_secret):
        raise HTTPException(status_code=403, detail="Invalid scheduler credential")
    return await send_daily_mail()


@tracked_job(lambda: db)
async def run_reminder_tick():
    return await reminders.tick(db, telegram, settings)


@tracked_job(lambda: db)
async def run_deadline_reminders():
    await reminders.tick(db, telegram, settings)
    error = None
    try:
        result = await send_deadline_reminders(db, telegram, settings)
    except RuntimeError as failure:
        if not settings.enable_learning_calendar:
            raise
        result, error = {}, failure
    if settings.enable_learning_calendar:
        result["learning"] = await learning_calendar().tick()
    if error:
        raise error
    return result


@app.post("/internal/deadlines")
async def trigger_deadline_reminders(x_chronos_scheduler_secret: str | None = Header(default=None)) -> dict:
    if not settings.scheduler_secret or not hmac.compare_digest(x_chronos_scheduler_secret or "", settings.scheduler_secret):
        raise HTTPException(status_code=403, detail="Invalid scheduler credential")
    try:
        return await run_deadline_reminders()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="Deadline reminder delivery requires retry or inspection") from error


def require_learning_scheduler_secret(value):
    if not settings.scheduler_secret or not hmac.compare_digest(value or "", settings.scheduler_secret):
        raise HTTPException(status_code=403, detail="Invalid scheduler credential")


@tracked_job(lambda: db)
async def run_evening_agenda():
    return await send_evening_agenda(db, google_calendar, telegram, settings)


@app.post("/internal/agenda/tomorrow")
async def trigger_evening_agenda(x_chronos_scheduler_secret: str | None = Header(default=None)):
    require_learning_scheduler_secret(x_chronos_scheduler_secret)
    try:
        return await run_evening_agenda()
    except (CalendarError, RuntimeError):
        raise HTTPException(status_code=503, detail="Tomorrow agenda delivery requires retry or inspection") from None


@tracked_job(lambda: db)
async def run_nightly_planning():
    return await learning_calendar().nightly_proposals()


@app.post("/internal/learning/nightly")
async def trigger_nightly_planning(x_chronos_scheduler_secret: str | None = Header(default=None)):
    require_learning_scheduler_secret(x_chronos_scheduler_secret)
    try:
        return await run_nightly_planning()
    except (CalendarError, RuntimeError):
        raise HTTPException(status_code=503, detail="Nightly planning requires retry or inspection") from None


@app.get("/internal/learning/status")
async def learning_status(x_chronos_scheduler_secret: str | None = Header(default=None)):
    require_learning_scheduler_secret(x_chronos_scheduler_secret)
    if not settings.enable_learning_calendar:
        return {"enabled": False}
    try:
        await google_calendar.verify_owner()
        now = datetime.now(settings.tz)
        await google_calendar.busy(now, now + timedelta(days=1))
    except CalendarError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    return {"enabled": True, "calendar_read_verified": True, "approval_required": True,
        "start_hour": settings.learning_start_hour, "end_hour": settings.learning_end_hour,
        "default_minutes": settings.learning_minutes, "daily_class_and_study_minutes": settings.learning_daily_minutes}


@app.post("/internal/learning")
async def learning_tick(x_chronos_scheduler_secret: str | None = Header(default=None)):
    require_learning_scheduler_secret(x_chronos_scheduler_secret)
    await reminders.tick(db, telegram, settings)
    try:
        return await learning_calendar().tick()
    except CalendarError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


@app.post("/internal/daily")
async def trigger_daily_tasks(x_chronos_scheduler_secret: str | None = Header(default=None)) -> dict:
    if not settings.scheduler_secret:
        raise HTTPException(status_code=503, detail="Scheduler endpoint is not configured")
    if not hmac.compare_digest(x_chronos_scheduler_secret or "", settings.scheduler_secret):
        raise HTTPException(status_code=403, detail="Invalid scheduler credential")
    await send_daily_tasks()
    return {"ok": True}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)) -> dict:
    if settings.telegram_webhook_secret and not hmac.compare_digest(
        x_telegram_bot_api_secret_token or "", settings.telegram_webhook_secret
    ):
        raise HTTPException(status_code=403, detail="Invalid webhook credential")
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Webhook body must be valid JSON") from None
    try:
        update = TelegramUpdate.model_validate(payload)
    except ValidationError:
        raise HTTPException(
            status_code=422,
            detail="Invalid webhook shape: check update_id, message or callback_query, chat, and text",
        ) from None
    source_message = update.edited_message or update.message or (update.callback_query.message if update.callback_query else None)
    if source_message is None:
        return {"ok": True}
    chat_id = source_message.chat.id
    if settings.telegram_chat_id and chat_id != settings.telegram_chat_id:
        raise HTTPException(status_code=403, detail="Unauthorized chat")
    update_id = update.update_id
    if update.callback_query and update.callback_query.data == 'assignments:refresh':
        receipt = db.get_update(update_id)
        if receipt is None:
            receipt = db.process_update(update_id, lambda: assignment_access.request_refresh(db, settings, chat_id, update_id))
        await deliver_learning_reply(chat_id, update_id, receipt)
        await telegram.answer_callback_query(update.callback_query.id)
        for message in receipt.get('messages', []):
            if message.get('assignment_refresh'):
                await assignment_access.dispatch(db, settings, telegram, message['assignment_refresh'])
        return {'ok': True}
    review_callback = re.fullmatch(r'taskreview:(open|(?:apply|cancel):[a-f0-9]{32})',
        update.callback_query.data or '') if update.callback_query else None
    if review_callback:
        receipt = db.get_update(update_id)
        if receipt is None:
            if review_callback[1] == 'open':
                action = await task_review.prepare(db, tasks, ai, settings, chat_id)
            else:
                operation, token = review_callback[1].split(':')
                action = lambda: task_review.respond(db, tasks, settings, chat_id, source_message.message_id, token, operation)
            receipt = db.process_update(update_id, action)
        await deliver_learning_reply(chat_id, update_id, receipt)
        await telegram.answer_callback_query(update.callback_query.id)
        if review_callback[1] != 'open':
            state = db.get_task_action_state(task_review.KEY + review_callback[1].split(':')[1])
            if state and state.get('chat_id') == chat_id and state.get('message_id') == source_message.message_id and state.get('status') in {'applied', 'cancelled'}:
                await delete_telegram_messages(chat_id, source_message.message_id)
        return {'ok': True}
    backward = BackwardPlanner(db, google_calendar, ai, settings)
    backward_match = re.fullmatch(r'backward:(allow|edit|cancel):([a-f0-9]{32})', update.callback_query.data or '') if update.callback_query else None
    backward_command = re.fullmatch(r'/backward(?:@[A-Za-z0-9_]+)?(?:\s+(.+))?', source_message.text or '', re.DOTALL) if update.message else None
    backward_progress = re.fullmatch(r'/backward(?:@[A-Za-z0-9_]+)?\s+progress(?:\s+#?(\d+))?', source_message.text or '') if update.message else None
    backward_session = backward.session(chat_id)
    backward_state = db.get_task_action_state('backward:plan:'+backward_session['token']) if backward_session.get('token') else None
    backward_reply = (update.message and not update.callback_query and backward_state
        and backward_state['status'] in {'clarifying', 'editing'}
        and backward_state['expires_at'] > datetime.now(settings.tz).isoformat()
        and (not source_message.reply_to_message
             or source_message.reply_to_message.message_id == backward_state.get('message_id'))
        and (not (source_message.text or '').startswith('/') or source_message.text == '/cancel'))
    if backward_match or backward_command or backward_progress or backward_reply:
        receipt = db.get_update(update_id)
        if receipt is None:
            try:
                if backward_match:
                    reply = await backward.callback(chat_id, source_message.message_id, backward_match[2], backward_match[1])
                elif backward_progress:
                    linked = db.get_task_action_state('backward:goal:'+backward_progress[1]) if backward_progress[1] else backward_session
                    reply = backward.progress(linked['token'], chat_id) if linked else 'No matching plan. Use /backward #<task-id>.'
                elif backward_reply and source_message.text in {'/cancel', '取消'}:
                    cancelled = db.mutate_task_action_state('backward:plan:'+backward_state['token'],
                        lambda old: {**old, 'status': 'cancelled'} if old['status'] in {'editing', 'clarifying'} else old)
                    reply = ('Cancelled. No tasks/events created.' if cancelled['status'] == 'cancelled'
                             else 'This plan changed. Review /backward progress; approved work is preserved.')
                else:
                    instruction = backward_command[1] if backward_command else source_message.text
                    reply = await backward.preview(chat_id, instruction, update_id) if instruction else 'Use /info backward for planning instructions.'
            except (AIError, CalendarError, ValueError):
                reply = 'Planning or Calendar availability could not be verified. No feasible plan is claimed. Retry /backward; if already approved, use Confirm schedule to recover the recorded writes.'
            receipt = db.process_update(update_id, lambda: reply)
        await deliver_learning_reply(chat_id, update_id, receipt)
        if backward_match:
            await telegram.answer_callback_query(update.callback_query.id)
        return {'ok': True}
    reminder_callback = re.fullmatch(r"reminder:(allow|cancel):([a-f0-9]{16})", update.callback_query.data or "") if update.callback_query else None
    reminder_text = (source_message.text or "").strip()
    if reminder_callback or (update.message and not source_message.reply_to_message and reminder_text.startswith("/") and reminders.matches(reminder_text)):
        receipt = db.get_update(update_id)
        if receipt is None:
            try:
                if reminder_callback:
                    reply = reminders.confirm(db, reminder_callback[2], reminder_callback[1], chat_id)
                else:
                    reply = await reminders.command(db, ai, google_calendar, settings,
                                                    reminder_text, chat_id, update_id)
            except (AIError, CalendarError):
                reply = "Reminder could not be prepared or checked for overlaps. Nothing added; please try again."
            receipt = db.process_update(update_id, lambda: reply)
        await deliver_learning_reply(chat_id, update_id, receipt)
        if reminder_callback:
            await telegram.answer_callback_query(update.callback_query.id)
        return {"ok": True}
    replan_callback = re.fullmatch(r"replan:(allow|edit|cancel):([a-f0-9]{32})", update.callback_query.data or "") if update.callback_query else None
    replan_session = db.get_task_action_state(calendar_replan.key(chat_id))
    replan_command = re.fullmatch(r"/replan(?:@[A-Za-z0-9_]+)?(?:\s+(.+))?", source_message.text or "", re.DOTALL) if update.message else None
    replan_edit = (not update.callback_query and update.message and replan_session
        and replan_session.get("status") == "editing" and not source_message.reply_to_message
        and replan_session.get("expires_at", "") > datetime.now(settings.tz).isoformat()
        and (not (source_message.text or "").startswith("/") or source_message.text == "/cancel"))
    if replan_callback or replan_command or replan_edit:
        receipt = db.get_update(update_id)
        if receipt is None:
            try:
                if not settings.enable_learning_calendar:
                    reply = "Task planning is unavailable. Nothing changed."
                elif replan_callback:
                    reply = await calendar_replan.callback(learning_calendar(), chat_id, source_message.message_id,
                                                           replan_callback[2], replan_callback[1])
                elif replan_edit and source_message.text in {"/cancel", "取消"}:
                    reply = await calendar_replan.callback(learning_calendar(), chat_id, replan_session.get("message_id"),
                                                           replan_session["token"], "cancel")
                else:
                    instruction = replan_command[1] if replan_command else source_message.text
                    reply = (await calendar_replan.preview(learning_calendar(), chat_id, instruction, update_id)
                             if instruction else "Use /replan 清空今天現在到下午兩點的行程")
            except CalendarError:
                reply = "Calendar could not confirm the arrangement. Nothing further was changed. If you pressed Allow, tap it again to recover its result."
            receipt = db.process_update(update_id, lambda: reply)
        await deliver_learning_reply(chat_id, update_id, receipt)
        if update.callback_query:
            await telegram.answer_callback_query(update.callback_query.id)
        return {"ok": True}
    free_callback = re.fullmatch(r"free:(allow|cancel):([a-f0-9]{32})", update.callback_query.data or "") if update.callback_query else None
    free_source = (db.get_task_action_state(f"free:message:{chat_id}:{source_message.reply_to_message.message_id}")
                   if source_message.reply_to_message and not update.callback_query else None)
    if free_callback or (free_source and update.message and not (source_message.text or "").startswith("/")):
        receipt = db.get_update(update_id)
        if receipt is None:
            try:
                if free_callback:
                    reply = await free_booking_callback(db, google_calendar, settings, chat_id,
                        source_message.message_id, free_callback[2], free_callback[1])
                else:
                    reply = await preview_free_event(db, ai, settings, chat_id,
                        source_message.reply_to_message.message_id, source_message.text or "", update_id)
            except CalendarError:
                reply = "Calendar could not confirm the result. Tap Allow again to check; no duplicate event will be created."
            receipt = db.process_update(update_id, lambda: reply)
        await deliver_learning_reply(chat_id, update_id, receipt)
        if free_callback:
            current = db.get_task_action_state("free:booking:" + free_callback[2])
            if (current and current.get("chat_id") == chat_id
                    and current.get("message_id") == source_message.message_id):
                if current.get("status") == "booked":
                    await delete_telegram_messages(chat_id, source_message.message_id)
                elif current.get("status") in {"cancelled", "expired", "conflict"}:
                    await telegram.request("editMessageReplyMarkup", {"chat_id": chat_id,
                        "message_id": source_message.message_id, "reply_markup": {"inline_keyboard": []}})
            await telegram.answer_callback_query(update.callback_query.id)
        return {"ok": True}
    if update.callback_query and (update.callback_query.data or "").startswith("learn:"):
        matched = re.fullmatch(r"learn:(approve|edit|earlier|later|longer|shorter|postpone|more|lots|shuffle|manual|reject|retry|done|partial|skipped):(\d+):([a-f0-9]{32})", update.callback_query.data)
        if not matched or source_message.message_id is None:
            raise HTTPException(status_code=400, detail="Invalid planning callback")
        receipt = db.get_update(update_id)
        if receipt is None:
            if not settings.enable_learning_calendar:
                reply = "Task planning is unavailable. No event was created."
            else:
                try:
                    reply = await learning_calendar().callback(matched[1], int(matched[2]), matched[3], source_message.message_id)
                except CalendarError as error:
                    reply = str(error) + " The execution plan remains saved; check /plan."
            receipt = db.process_update(update_id, lambda: reply)
        await deliver_learning_reply(chat_id, update_id, receipt)
        await telegram.answer_callback_query(update.callback_query.id)
        if settings.enable_learning_calendar:
            # Deliver the next view before removing the card that was acted on.
            deleted = await telegram.request("deleteMessage", {
                "chat_id": chat_id, "message_id": source_message.message_id,
            })
            if not deleted.get("ok"):
                logger.warning("Could not remove the handled study notification")
            try:
                await learning_calendar().tick()
            except CalendarError:
                logger.warning("Study continuation proposal deferred to the scheduler")
        return {"ok": True}
    study_edit = (db.get_task_action_state(f"learning:edit-message:{chat_id}:{source_message.reply_to_message.message_id}")
                  if source_message.reply_to_message else None)
    instruction = (source_message.text or "").strip()
    pending_key = f"learning:edit-chat:{chat_id}"
    pending = db.get_task_action_state(pending_key)
    if (not study_edit and pending and not source_message.reply_to_message and not update.callback_query
            and (not instruction.startswith("/") or instruction == "/cancel")):
        if pending.get("expires_at", "") > datetime.now(settings.tz).isoformat():
            study_edit = pending
    if study_edit:
        receipt = db.get_update(update_id)
        if receipt is None:
            if update.edited_message:
                reply = "Send a new reply to adjust the execution plan. Editing an old message does not replay it."
            elif instruction in {"/cancel", "取消"}:
                planner = learning_calendar()
                state = planner.get(study_edit["task_id"])
                reply = (planner.edit_menu(study_edit["task_id"], state) if state and state["status"] == "editing"
                    and state["token"] == study_edit["token"] else "This manual adjustment has ended. Open Adjust plan on the current execution plan.")
                db.mutate_task_action_state(pending_key, lambda old: {} if (old or {}).get("token") == study_edit["token"] else old)
            else:
                try:
                    reply = (learning_calendar().partial_reply(study_edit["task_id"], study_edit["token"], instruction)
                        if study_edit.get("mode") == "feedback" else
                        await learning_calendar().revise(study_edit["task_id"], study_edit["token"], instruction))
                except CalendarError as error:
                    reply = str(error)
                state = learning_calendar().get(study_edit["task_id"])
                if not state or state.get("token") != study_edit["token"] or state["status"] != ("awaiting_feedback" if study_edit.get("mode") == "feedback" else "editing"):
                    db.mutate_task_action_state(pending_key, lambda old: {} if (old or {}).get("token") == study_edit["token"] else old)
                else:
                    db.mutate_task_action_state(pending_key, lambda old: {**study_edit, "task_id": study_edit["task_id"],
                        "token": study_edit["token"], "expires_at": (datetime.now(settings.tz) + timedelta(hours=2)).isoformat()})
            receipt = db.process_update(update_id, lambda: reply)
        await deliver_learning_reply(chat_id, update_id, receipt)
        if settings.enable_learning_calendar:
            try:
                await learning_calendar().tick()
            except CalendarError:
                logger.warning("Revised study proposal deferred to the scheduler")
        return {"ok": True}
    # Route mail cards before generic task/study replies. Bind callbacks to the
    # actual delivered card so arbitrary mail IDs cannot be supplied by a client.
    mail = mail_workflow() if settings.enable_gmail else None
    if mail and update.message and (source_message.text or "").strip() == "/mail_next":
        receipt = db.get_update(update_id)
        if receipt is None:
            try:
                result = await mail.daily(backlog=True, request_key=str(update_id))
            except GmailError as error:
                raise HTTPException(status_code=503, detail=str(error)) from None
            reply = (f"本批仍有 {result['waiting']} 封待處理。請先封存、刪除或標為已讀，再傳 /mail_next。"
                     if result.get("waiting") else "下一批已送出。請查看上方郵件卡片。")
            receipt = db.process_update(update_id, lambda: reply)
        if not receipt["delivered"]:
            result = await telegram.send_message(chat_id, receipt["reply"])
            if not result.get("ok"):
                raise HTTPException(status_code=502, detail="Mail reply pending")
            db.mark_update_delivered(update_id)
        return {"ok": True}
    mail_binding = (mail.binding(chat_id, source_message.reply_to_message.message_id)
                    if mail and source_message.reply_to_message else None)
    mail_callback = bool(update.callback_query and (update.callback_query.data or "").startswith("mail:"))
    if mail_callback or mail_binding:
        if mail is None:
            raise HTTPException(status_code=503, detail="Mail integration is disabled")
        callback_match = (re.fullmatch(
            r"mail:(trash|task|keep|read|(?:confirm|edit|cancel)_[a-f0-9]{8}):([A-Za-z0-9_-]{1,32})",
            update.callback_query.data or "",
        ) if mail_callback else None)
        active_binding = (mail.binding(chat_id, source_message.message_id) if mail_callback else mail_binding)
        receipt = db.get_update(update_id)
        if receipt is None:
            if update.edited_message is not None:
                action = lambda: "編輯舊訊息不會重新執行郵件動作。請傳送一則新指令。"
            elif mail_callback:
                if not callback_match or not active_binding or active_binding["mail_id"] != callback_match.group(2):
                    raise HTTPException(status_code=403, detail="Mail callback is not bound to this card")
                try:
                    action = await mail.prepare_action(callback_match.group(2), callback_match.group(1))
                except GmailError as error:
                    raise HTTPException(status_code=503, detail=str(error)) from None
            else:
                try:
                    action = await mail.prepare_action(mail_binding["mail_id"], source_message.text or "")
                except GmailError as error:
                    raise HTTPException(status_code=503, detail=str(error)) from None
            receipt = db.process_update(update_id, action)
        if receipt["reply"] in {"已移到垃圾桶。", "已標為已讀。"} or receipt["reply"].startswith("已封存，可在 Gmail 所有郵件找到"):
            card_id = source_message.message_id if mail_callback else source_message.reply_to_message.message_id
            await delete_telegram_messages(chat_id, card_id)
        is_proposal = receipt["reply"].startswith("Create this task?\n\n")
        is_edit = receipt["reply"] == "Reply with the revised task title."
        task_resolved = (receipt["reply"].startswith("Added task #")
                         or receipt["reply"].startswith("This email already has task #"))
        callback_action = callback_match.group(1) if callback_match else None
        active_card_id = (source_message.message_id if mail_callback
                          else source_message.reply_to_message.message_id)
        origin_card_id = (active_binding or {}).get("origin_message_id")
        if callback_action == "task" and origin_card_id is None:
            origin_card_id = active_card_id
        if task_resolved:
            await delete_telegram_messages(chat_id, origin_card_id or active_card_id, active_card_id)
        elif callback_action and callback_action.startswith(("edit_", "cancel_")):
            await delete_telegram_messages(chat_id, active_card_id)
        elif is_proposal and not mail_callback and origin_card_id is not None:
            # The edited title has replaced the force-reply prompt.
            await delete_telegram_messages(chat_id, active_card_id)
        if not receipt["delivered"]:
            if receipt["reply"] != "已移到垃圾桶。":
                markup = None
                identifier = active_binding["mail_id"]
                if is_proposal:
                    import hashlib
                    title = receipt["reply"].split("\n\n", 1)[1]
                    token = hashlib.sha256(title.encode()).hexdigest()[:8]
                    markup = {"inline_keyboard": [[{"text": label, "callback_data": f"mail:{action}_{token}:{identifier}"}
                              for label, action in (("Yes", "confirm"), ("Edit", "edit"), ("Cancel", "cancel"))]]}
                elif is_edit:
                    markup = {"force_reply": True, "input_field_placeholder": "Enter a task title"}
                result = await telegram.send_message(chat_id, receipt["reply"], reply_markup=markup)
                if not result.get("ok"):
                    raise HTTPException(status_code=502, detail="Mail action reply pending")
                if is_proposal or is_edit:
                    message_id = result.get("result", {}).get("message_id")
                    if not message_id:
                        raise HTTPException(status_code=502, detail="Mail task prompt ID missing")
                    binding = {"mail_id": identifier}
                    if origin_card_id is not None:
                        binding["origin_message_id"] = origin_card_id
                    mail.patch(f"telegram:{chat_id}:{message_id}", **binding)
            db.mark_update_delivered(update_id)
        if update.callback_query:
            result = await telegram.answer_callback_query(update.callback_query.id)
            if not result.get("ok"):
                raise HTTPException(status_code=502, detail="Mail callback acknowledgement pending")
        return {"ok": True}
    receipt = db.get_update(update_id)
    plan_callback = False
    if update.callback_query:
        if receipt is None:
            data = update.callback_query.data
            if data and re.fullmatch(r"chabit:[a-f0-9]{16}:[01]:(?:accept|skip)", data):
                action = lambda: course_habits.respond(db, data, chat_id, source_message.message_id, datetime.now(settings.tz))
            elif data and re.fullmatch(r"image:\d+:\d+", data):
                from .image_deadlines import confirm_photo
                action = lambda: confirm_photo(db, tasks, data, chat_id, datetime.now(settings.tz))
            elif data == "clear:confirm":
                action = clear_tasks_reply
            elif data == "clear:cancel":
                action = lambda: "Understood. Nothing was deleted."
            elif data and re.fullmatch(r"edit:(?:retry|cancel):\d+", data):
                action = await prepare_pending_edit(data)
            elif data and re.fullmatch(r"task:undo:[a-f0-9]{32}", data):
                action = prepare_undo(data.rsplit(":", 1)[1])
            elif data and re.fullmatch(r"task:edit:\d+", data):
                selected = tasks.get_open_by_id(int(data.rsplit(":", 1)[1]))
                if selected is None:
                    action = lambda: "That task is no longer open. Use /tasks to select another."
                else:
                    card = task_card(selected, "Send the changes you want applied.\n\n" +
                                     "\n".join(format_task_block(selected, settings.tz)))
                    card["reply_markup"] = {"force_reply": True, "selective": True}
                    action = lambda: message_bundle(card)
            elif data and re.fullmatch(r"task:plan:\d+", data):
                selected = tasks.get_open_by_id(int(data.rsplit(":", 1)[1]))
                if selected is None:
                    action = lambda: "That task is no longer open. Use /tasks to select another."
                elif not settings.enable_learning_calendar:
                    action = lambda: "Task planning is unavailable. No calendar event was created."
                else:
                    plan_callback = True
                    action = lambda task_id=selected["id"]: learning_calendar().request_proposal(
                        task_id, immediate=True)
            elif data == "help:index":
                action = lambda: message_bundle({
                    "text": HELP_TEXT,
                    "parse_mode": "HTML",
                    "reply_markup": HELP_KEYBOARD,
                })
            elif data and re.fullmatch(r"help:(?:tasks|study|backward|assignments)", data):
                section = data.split(":", 1)[1]
                action = lambda section=section: message_bundle({
                    "text": HELP_SECTIONS[section],
                    "parse_mode": "HTML",
                    "reply_markup": HELP_BACK_KEYBOARD,
                })
            else:
                action = lambda: "That action is no longer available. Nothing changed."
            receipt = db.process_update(update_id, action)
    else:
        text = (source_message.text or "").strip()
        if not text and not source_message.photo:
            return {"ok": True}
        if receipt is None:
            # Network work happens before acquiring the persistence transaction.
            bound_task = (db.task_for_message(chat_id, source_message.reply_to_message.message_id)
                          if source_message.reply_to_message is not None else None)
            if update.edited_message is not None:
                action = lambda: (
                    "Editing a sent message does not replay its command. Nothing changed. "
                    "Send /edit 1 your changes as a new message, or use /tasks → Edit."
                )
            elif source_message.photo:
                from .image_deadlines import prepare_photo
                photo = max(source_message.photo, key=lambda item: item.width * item.height)
                proposal = await prepare_photo(ai, telegram, tasks, photo, source_message.caption or "",
                                               update_id, chat_id, datetime.now(settings.tz))
                action = lambda: proposal
            elif bound_task is not None:
                instruction = re.sub(r"^/edit\s*", "", text, flags=re.IGNORECASE).strip()
                if re.match(r"^/edit(?:\s|$)", text, re.IGNORECASE):
                    action = await prepare_message(f"/edit #{bound_task} {instruction}", update_id=update_id)
                else:
                    action = await prepare_message(text, update_id=update_id, chat_id=chat_id, bound_task_id=bound_task)
            elif source_message.reply_to_message is not None:
                if source_message.message_id is None:
                    raise HTTPException(status_code=422, detail="Reply message id is required")
                received_at = datetime.now(settings.tz)
                received_date = received_at.date()
                action = lambda: record_course_progress_reply(
                    source_message.reply_to_message.message_id, source_message.message_id, text,
                    local_date=received_date,
                )
            else:
                action = await prepare_message(text, update_id=update_id, chat_id=chat_id)
            receipt = db.process_update(update_id, action)
    help_callback = (
        update.callback_query is not None
        and re.fullmatch(
            r"help:(?:index|tasks|study|backward|assignments)",
            update.callback_query.data or "",
        )
    )
    if not receipt["delivered"] and help_callback:
        if source_message.message_id is None:
            raise HTTPException(status_code=422, detail="Help message id is required")
        message = (receipt.get("messages") or [{
            "text": receipt["reply"],
            "parse_mode": _legacy_parse_mode(receipt["reply"]),
        }])[0]
        payload = {
            "chat_id": chat_id,
            "message_id": source_message.message_id,
            "text": message["text"],
            "parse_mode": message.get("parse_mode"),
            "reply_markup": message.get("reply_markup"),
        }
        result = await telegram.request("editMessageText", payload)
        if (
            not result.get("ok")
            and "message is not modified" not in str(result.get("description", "")).lower()
        ):
            raise HTTPException(status_code=502, detail="Help display update failed")
        if receipt.get("messages"):
            db.mark_update_message_delivered(update_id, 1)
        else:
            db.mark_update_delivered(update_id)
    elif not receipt["delivered"] and any(
        any(message.get(key) for key in ("backward_plan", "learning_edit", "learning_menu", "learning_proposal"))
        for message in receipt.get("messages", [])
    ):
        await deliver_learning_reply(chat_id, update_id, receipt)
    elif not receipt["delivered"]:
        messages = receipt.get("messages") or [{
            "text": receipt["reply"],
            "parse_mode": _legacy_parse_mode(receipt["reply"]),
            "reply_markup": CLEAR_KEYBOARD if receipt["reply"] == CLEAR_CONFIRM_TEXT else None,
        }]
        delivered_count = receipt.get("delivered_count", 0)
        for index, message in enumerate(messages[delivered_count:], start=delivered_count + 1):
            send_options = {"parse_mode": message.get("parse_mode")}
            if message.get("reply_markup"):
                send_options["reply_markup"] = message["reply_markup"]
            result = await telegram.send_message(chat_id, message["text"], **send_options)
            if not result.get("ok"):
                raise HTTPException(status_code=502, detail="Telegram reply failed; waiting for retry")
            sent_id = result.get("result", {}).get("message_id")
            if sent_id is not None:
                task_review.bind(db, chat_id, sent_id, message)
                bind_free_message(db, chat_id, sent_id, message)
                calendar_replan.bind(db, chat_id, sent_id, message)
                BackwardPlanner(db, google_calendar, ai, settings).bind(chat_id, sent_id, message)
            if message.get("task_id") is not None and sent_id is not None:
                db.bind_task_message(chat_id, sent_id, message["task_id"])
            if receipt.get("messages"):
                db.mark_update_message_delivered(update_id, index)
            else:
                db.mark_update_delivered(update_id)
    if update.callback_query:
        if (update.callback_query.data or "").startswith("edit:") and source_message.message_id is not None:
            await telegram.request("editMessageReplyMarkup", {
                "chat_id": chat_id,
                "message_id": source_message.message_id,
                "reply_markup": {"inline_keyboard": []},
            })
        result = await telegram.answer_callback_query(update.callback_query.id)
        if not result.get("ok"):
            raise HTTPException(status_code=502, detail="Telegram callback acknowledgement failed")
        if plan_callback and settings.enable_learning_calendar:
            try:
                await learning_calendar().tick(force_proposal=True)
            except CalendarError:
                logger.warning("Deadline study proposal deferred; check /plan or the next scheduler tick")
    for message in receipt.get('messages', []):
        if message.get('assignment_refresh'):
            await assignment_access.dispatch(db, settings, telegram, message['assignment_refresh'])
    if settings.enable_learning_calendar and update.message and (
        receipt.get("reply", "").startswith("<b>Task created</b>") or (source_message.text or "").startswith(("/plan ", "/planedit ", "/taskupdate ", "/studyfeedback "))
    ):
        try:
            await learning_calendar().tick()
        except CalendarError:
            logger.warning("Learning calendar proposal deferred; check /plan or the next scheduler tick")
    return {"ok": True}


def _legacy_parse_mode(reply: str) -> str | None:
    return "HTML" if (
        reply == HELP_TEXT or "<b>Tasks</b>" in reply or reply.startswith(("Completed:", "Completed "))
        or reply.startswith("<b>")
    ) else None


async def prepare_pending_edit(data: str) -> Callable[[], str | dict]:
    match = re.fullmatch(r"edit:(retry|cancel):(\d+)", data)
    if not match:
        return lambda: "That saved edit is no longer available."
    action_name, pending_id_text = match.groups()
    pending_id = int(pending_id_text)
    pending = db.get_pending_task_edit(pending_id)
    if pending is None:
        return lambda: "That saved edit is no longer available. Nothing changed."
    position = int(pending["position"])
    if action_name == "cancel":
        def cancel() -> str:
            db.delete_pending_task_edit(pending_id)
            return f"Understood. The saved edit for Task {position} was cancelled; the task is unchanged."
        return cancel

    current_items = tasks.list_open()
    current = next((item for item in current_items if item["id"] == pending["task_id"]), None)
    if current is None:
        def stale() -> str:
            db.delete_pending_task_edit(pending_id)
            return "The target task is no longer open. I cancelled the saved command."
        return stale
    current_position = next(
        index for index, item in enumerate(current_items, start=1) if item["id"] == current["id"]
    )
    try:
        deterministic = parse_deterministic_edit(current, pending["instruction"])
    except ValueError as error:
        return lambda reply=str(error): reply
    tag_alias = deterministic.tag_alias if deterministic else None
    if deterministic:
        parsed = deterministic.task
    else:
        try:
            parsed = await ai.edit(current, pending["instruction"])
        except AIError as error:
            reason = escape(str(error))
            return lambda reason=reason: message_bundle({
                "text": (
                    f"<b>Update failed · Task {current_position}</b>\n"
                    f"{reason}\nTask {current_position} is unchanged. "
                    "The command remains saved; use the button below when you are ready to retry."
                ),
                "parse_mode": "HTML",
                "reply_markup": pending_edit_keyboard(pending_id, current_position),
            })
        except ValueError as error:
            return lambda reply=str(error): reply

    def retry() -> dict:
        before = tasks.list_open()
        aliases = tasks.project_aliases()
        if edit_conflict(before, current):
            return message_bundle({"text": "This task changed while the edit was being prepared. "
                                   "Nothing was applied. Retry the saved edit to use the latest values.",
                                   "reply_markup": pending_edit_keyboard(pending_id, current_position)})
        task = tasks.edit_with_undo(current["id"], parsed.title, parsed.due_at, parsed.project, parsed.timing, tag_alias)
        if task is None:
            db.delete_pending_task_edit(pending_id)
            return message_bundle({
                "text": "The target task is no longer open. Nothing was applied.",
                "parse_mode": None,
            })
        db.delete_pending_task_edit(pending_id)
        return message_bundle(
            {
                "text": format_update_summary(
                    current_position, current, task, tag_alias=tag_alias, aliases=aliases
                ),
                "parse_mode": "HTML",
                "task_id": task["id"],
                "reply_markup": edited_task_keyboard(task),
            },
            {
                "text": format_updated_tasks(before, task, aliases=aliases, tag_alias=tag_alias),
                "parse_mode": "HTML",
            },
        )
    return retry


def edited_task_keyboard(task):
    keyboard = task_edit_keyboard([task])
    if task.get("undo_token"):
        keyboard["inline_keyboard"].append([{
            "text": "Undo this edit", "callback_data": f"task:undo:{task['undo_token']}",
        }])
    return keyboard


def prepare_undo(token=None):
    def undo():
        before = tasks.list_open()
        aliases = tasks.project_aliases()
        try:
            restored = db.undo_task_edit(token)
        except UndoConflict as error:
            return str(error)
        for key, alias in restored.pop("restored_aliases").items():
            if alias is None:
                aliases.pop(key, None)
            else:
                aliases[key] = alias
        return message_bundle(
            task_card(restored, "<b>Edit undone</b>\n" + "\n".join(format_task_block(restored, settings.tz, project_aliases=aliases))),
            {"text": format_updated_tasks(before, restored, aliases=aliases), "parse_mode": "HTML"},
        )
    return undo


async def handle_message(text: str) -> str:
    output = (await prepare_message(text))()
    if isinstance(output, str):
        return output
    return "\n\n".join(message["text"] for message in output["messages"])


async def prepare_message(text: str, *, update_id: int | None = None, chat_id: int | None = None,
                          bound_task_id: int | None = None, create_only: bool = False) -> Callable[[], str | dict]:
    """Resolve external input first; the returned action performs no async work."""
    normalized = text.strip()
    if re.match(r'^/coursehabit(?:@[A-Za-z0-9_]+)?(?:\s|$)', normalized):
        return lambda: course_habits.command(db, normalized)
    if not create_only and is_no_change_reply(normalized):
        return lambda: "收到"
    if create_only:
        try:
            parsed = await ai.parse_many(normalized)
        except (AIError, ValueError) as error:
            return lambda reply=str(error): reply
        parsed = task_review.defer_inferred_course_tags(parsed, normalized)
        return prepare_created_tasks(parsed, chat_id=chat_id)
    normalized = re.sub(r"^(/[A-Za-z_]+)@[A-Za-z0-9_]+(?=\s|$)", r"\1", normalized)
    assignment_query = re.fullmatch(r'/assignments(?:\s+([a-z-]+))?(?:\s+([1-9][0-9]{0,5}))?', normalized)
    if assignment_query:
        course, page = assignment_query[1], int(assignment_query[2] or 1)
        if course and course not in assignment_access.COURSES:
            return lambda: '請使用目前課程代號，或直接說「資安課還有作業嗎？」。'
        return lambda: assignment_access.query(db, settings, course=course, page=page)
    if normalized == '/refreshassignments':
        return lambda: assignment_access.request_refresh(db, settings, chat_id, update_id)
    if bound_task_id is not None and not normalized.startswith("/"):
        import sys
        from .intents import prepare
        return await prepare(sys.modules[__name__], normalized, update_id=update_id,
                             chat_id=chat_id, bound_task_id=bound_task_id)
    if not normalized.startswith("/") and (re.fullmatch(
            r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2})\s+\d{1,2}:\d{2}(?:\s+\d{1,3}(?:\s*(?:minutes|min|分鐘))?)?", normalized, re.IGNORECASE)
            or study_window(normalized, datetime.now(settings.tz), settings.learning_minutes)):
        return lambda: "Open Adjust plan → Set manually on the current execution plan, then send this date/time again. No task was created."
    if re.fullmatch(r"/free(?:@[A-Za-z0-9_]+)?", normalized, re.IGNORECASE):
        try:
            offer = await free_slot_offer(db, google_calendar, settings)
        except CalendarError:
            return lambda: "Calendar availability is unavailable right now. Please try /free again shortly."
        return lambda: message_bundle(offer)
    if re.fullmatch(r"/(?:today|tomorrow)(?:@[A-Za-z0-9_]+)?", normalized, re.IGNORECASE):
        command = normalized.split("@")[0].lower()
        label = "Today" if command == "/today" else "Tomorrow"
        try:
            parts = await tomorrow_agenda(google_calendar, settings, day_offset=0 if command == "/today" else 1, db=db)
        except CalendarError:
            return lambda: f"{label}'s agenda is unavailable right now. Please try {command} again shortly."
        return lambda: message_bundle(*({"text": part, "parse_mode": "HTML"} for part in parts))
    schedule_question = re.fullmatch(
        r"(?:我)?(?:這|下)?(?:星期|週|周)([一二三四五六日天])(?:的)?(早上|上午|下午|晚上)?(?:有事嗎|有空嗎|有什麼(?:事|安排|行程)|有行程嗎)[？?]?",
        normalized,
    )
    if schedule_question:
        from .evening_agenda import render_window_agenda
        now = datetime.now(settings.tz)
        weekday = "一二三四五六日".index(schedule_question[1].replace("天", "日"))
        offset = (weekday - now.weekday()) % 7
        if normalized.lstrip("我").startswith("下"):
            offset = 7 - now.weekday() + weekday
        day = now.date() + timedelta(days=offset)
        hours = {"早上": (6, 12), "上午": (6, 12), "下午": (12, 18), "晚上": (18, 24)}
        begin, end = hours.get(schedule_question[2], (0, 24))
        start = datetime.combine(day, datetime.min.time(), settings.tz) + timedelta(hours=begin)
        stop = datetime.combine(day, datetime.min.time(), settings.tz) + timedelta(hours=end)
        try:
            await google_calendar.verify_owner()
            events = await google_calendar.agenda(start, stop)
        except CalendarError:
            return lambda: "目前無法讀取行事曆，尚不能確認這段時間是否有安排。沒有建立任務。"
        parts = render_window_agenda(events, start, stop, settings.tz, db=db)
        return lambda: message_bundle(*({"text": part, "parse_mode": "HTML"} for part in parts))
    if not normalized.startswith("/"):
        import sys
        from .intents import prepare
        return await prepare(sys.modules[__name__], normalized, update_id=update_id,
                             chat_id=chat_id, bound_task_id=bound_task_id)
    plan_edit = re.fullmatch(r"/planedit\s+(\d+)\s+(.+)", normalized, re.DOTALL)
    if plan_edit:
        state = learning_calendar().get(int(plan_edit[1]))
        if not state:
            return lambda: "No execution plan matches that task. Use /plan."
        try:
            reply = await learning_calendar().revise(int(plan_edit[1]), state["token"], plan_edit[2])
        except CalendarError as error:
            reply = str(error)
        return lambda: reply
    if normalized == "/plan":
        def plan_status():
            planner = learning_calendar()
            parts = [planner.status(), planner.status(booked_only=True)]
            return message_bundle(*({"text": part, "parse_mode": "HTML"} for part in parts if part))
        return plan_status
    replan = re.fullmatch(r"/plan\s+#?(\d+)", normalized)
    if replan:
        task_id = int(replan[1])
        def request_plan():
            if not settings.enable_learning_calendar:
                return "Task planning is unavailable."
            if tasks.get_open_by_id(task_id) is None:
                return "That task is not open. Use /tasks."
            return learning_calendar().request_proposal(task_id)
        return request_plan
    feedback = re.fullmatch(r"/(?:taskupdate|studyfeedback)\s+(\d+)\s+(\d+)\s+(done|partial|skipped)(?:\s+(.+))?", normalized, re.DOTALL)
    if feedback:
        task = tasks.get_open_by_id(int(feedback[1]))
        if not task:
            return lambda: "No open work session matches that task."
        return await learning_calendar().prepare_feedback(task, feedback[3], int(feedback[2]), feedback[4] or "")
    command = normalized[1:].strip() if normalized.startswith("/") else None
    if command is not None and (command == 'announcements' or command.startswith('announcements ')):
        from .announcement_commands import announcements_query
        return lambda: announcements_query(db, command)
    if command is not None and (command == 'announcement' or command.startswith('announcement ')):
        from .announcement_commands import announcement_query
        return lambda: announcement_query(db, command)
    if command is not None and (command == 'classday' or command.startswith('classday ')):
        from .course_day_commands import classday_action, save_classday_action
        parts = command.split()
        if command == 'classday' or (len(parts) == 4 and parts[-1] in {'class', 'off', 'auto'}):
            return classday_action(db, command)
        try:
            record = await ai.parse_classday(command.removeprefix('classday').strip(), datetime.now(settings.tz))
        except (AIError, ValueError) as error:
            return lambda reply=str(error): reply
        return save_classday_action(db, record)
    if command in {"start", "info"}:
        return lambda: message_bundle({
            "text": HELP_TEXT,
            "parse_mode": "HTML",
            "reply_markup": HELP_KEYBOARD,
        })
    if command is not None and (command == "calendar" or command.startswith("calendar ")):
        from .calendar_overrides import CalendarOverrides, apply_calendar_override, render_calendar_overrides
        suffix = command.removeprefix("calendar").strip()
        if not suffix:
            return lambda: message_bundle({
                "text": render_calendar_overrides(CalendarOverrides.from_record(db.get_calendar_overrides())),
                "parse_mode": "HTML",
            })
        try:
            apply_calendar_override(CalendarOverrides(), suffix)
        except ValueError as error:
            return lambda reply=str(error): reply
        def update_calendar():
            current = CalendarOverrides.from_record(db.get_calendar_overrides())
            updated = apply_calendar_override(current, suffix)
            db.save_calendar_overrides(updated.to_record())
            return message_bundle({"text": "掃描校曆已更新。\n\n" + render_calendar_overrides(updated),
                                   "parse_mode": "HTML"})
        return update_calendar
    help_section = re.fullmatch(r"info(?:@[A-Za-z0-9_]+)?\s+(tasks|study|planning|backward|assignments)", command or "", re.IGNORECASE)
    if help_section:
        section = help_section.group(1).lower()
        if section == "planning":
            section = "study"
        return lambda: message_bundle({
            "text": HELP_SECTIONS[section],
            "parse_mode": "HTML",
            "reply_markup": HELP_BACK_KEYBOARD,
        })
    if command == "ai_usage":
        def usage_report() -> str:
            now = datetime.now(settings.tz)
            return format_ai_usage(ai_usage.report(now), now.date().isoformat())
        return usage_report
    if command == 'reviewtasks':
        return await task_review.prepare(db, tasks, ai, settings, chat_id)
    if command in {"task", "tasks"}:
        def list_tasks() -> dict:
            items = tasks.list_open()
            return message_bundle({"text": task_list_text(items), "parse_mode": "HTML",
                                   **({'reply_markup': task_review.OPEN_KEYBOARD} if items else {})})
        return list_tasks
    if command == "undo":
        return prepare_undo()
    if command == "clear":
        return lambda: CLEAR_CONFIRM_TEXT
    if command == "done":
        return lambda: "要完成哪一項？直接說任務名稱，或回覆任務卡說「完成了」。\n\n" + task_list_text()
    completed = re.fullmatch(r"done\s+(\d+)", command or "", re.IGNORECASE)
    if completed:
        def complete() -> str:
            position = int(completed.group(1))
            before = tasks.list_open()
            aliases = tasks.project_aliases()
            task = tasks.complete_position(position)
            if task is None:
                return f"Task {position} not found.\n\n{task_list_text()}"
            remaining = [item for item in before if item["id"] != task["id"]]
            return (
                f"Completed {escape(task['title'])}\n\n"
                f"{format_tasks(remaining, settings.tz, project_aliases=aliases)}"
            )
        return complete
    edited = re.fullmatch(r"edit\s+(#?\d+)\s+(.+)", command or "", re.IGNORECASE | re.DOTALL)
    if edited:
        reference = edited.group(1)
        position = int(reference.lstrip("#"))
        items = tasks.list_open()
        if reference.startswith("#"):
            current = next((item for item in items if item["id"] == position), None)
        else:
            current = items[position - 1] if 1 <= position <= len(items) else None
        if current is None:
            return lambda: f"Task {reference} not found.\n\n{task_list_text()}"
        if reference.startswith("#"):
            position = next(i for i, item in enumerate(items, 1) if item["id"] == current["id"])
        instruction = edited.group(2).strip()
        try:
            deterministic = parse_deterministic_edit(current, instruction)
        except ValueError as error:
            return lambda reply=str(error): reply
        tag_alias = deterministic.tag_alias if deterministic else None
        if deterministic:
            parsed = deterministic.task
        else:
            try:
                parsed = await ai.edit(current, instruction)
            except AIError as error:
                reason = escape(str(error))
                if update_id is None:
                    return lambda reason=reason: (
                        f"{reason}\nTask {position} is unchanged. Please try again later."
                    )
                def save_failed_edit() -> dict:
                    db.save_pending_task_edit(
                        update_id,
                        current["id"],
                        position,
                        instruction,
                        datetime.now(settings.tz),
                    )
                    text = (
                        f"<b>Update failed · Task {position}</b>\n"
                        f"{reason}\nTask {position} is unchanged. "
                        "The command is saved; use the button below to retry."
                    )
                    return message_bundle({
                        "text": text,
                        "parse_mode": "HTML",
                        "reply_markup": pending_edit_keyboard(update_id, position),
                    })
                return save_failed_edit
            except ValueError as error:
                return lambda reply=str(error): reply
        task_id = current["id"]
        def edit() -> dict:
            before = tasks.list_open()
            aliases = tasks.project_aliases()
            if edit_conflict(before, current):
                return message_bundle({"text": "This task changed while the edit was being prepared. "
                                       "Nothing was applied. Use /tasks, then send the changes again."})
            task = tasks.edit_with_undo(task_id, parsed.title, parsed.due_at, parsed.project, parsed.timing, tag_alias)
            if task is None:
                return message_bundle({
                    "text": f"Task {position} is no longer open. Nothing was applied.",
                    "parse_mode": None,
                })
            return message_bundle(
                {
                    "text": format_update_summary(
                        position, current, task, tag_alias=tag_alias, aliases=aliases
                    ),
                    "parse_mode": "HTML",
                    "task_id": task_id,
                    "reply_markup": edited_task_keyboard(task),
                },
                {
                    "text": format_updated_tasks(before, task, aliases=aliases, tag_alias=tag_alias),
                    "parse_mode": "HTML",
                },
            )
        return edit
    if re.match(r"^edit(?:\s|$)", command or "", re.IGNORECASE):
        return lambda: (
            "Specify the task and the change: /edit 1 移除期限 or /edit #42 your changes. "
            "Use /tasks to choose a task, or reply to its task card with your changes. No task was changed."
        )
    literal = re.fullmatch(r"add(?:\s+(.*))?", command or "", re.IGNORECASE | re.DOTALL)
    if literal:
        try:
            parsed = parse_literal_task(literal.group(1) or "")
        except ValueError as error:
            return lambda reply=str(error): reply
    else:
        return lambda: "Unknown command. Use /info to see available commands."
    return prepare_created_task(parsed, chat_id=chat_id)


def prepare_created_task(parsed, *, chat_id=None):
    return prepare_created_tasks([parsed], chat_id=chat_id)


def prepare_created_tasks(parsed_tasks, *, chat_id=None):
    aliases = tasks.project_aliases()
    def create() -> dict:
        before = tasks.list_open()
        created = tasks.create_many(parsed_tasks)
        messages = []
        for task in created:
            details = "\n".join(format_task_block(task, settings.tz, project_aliases=aliases))
            messages.append(task_card(task, f"<b>Task created</b>\n{details}"))
        # Read once before writes, preserving the Firestore transaction contract.
        after = sorted([*before, *created], key=task_sort_key)
        suggestions = [suggestion for task in created if (suggestion := task_review.new_task_suggestion(
            db, task, [*before, *created], aliases, chat_id, datetime.now(settings.tz)))]
        return message_bundle(*messages,
            {"text": format_tasks(after, settings.tz, project_aliases=aliases), "parse_mode": "HTML"}, *suggestions)
    return create


def format_updated_tasks(
    before: list[dict],
    updated: dict,
    *,
    aliases: dict[str, str] | None = None,
    tag_alias: tuple[str, str] | None = None,
) -> str:
    """Render the known transaction outcome without a Firestore read-after-write."""
    after = [{**item, **updated} if item["id"] == updated["id"] else item for item in before]
    after.sort(key=task_sort_key)
    display_aliases = dict(aliases or {})
    if tag_alias:
        display_aliases[tag_alias[0]] = tag_alias[1]
    return format_tasks(after, settings.tz, project_aliases=display_aliases)
