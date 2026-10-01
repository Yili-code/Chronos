import base64
import hmac
import json
import logging
import re
from contextlib import asynccontextmanager
from collections.abc import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .db import create_database
from .ai import AIError, ExternalAI
from .settings import settings
from .tasks import TaskService, format_task, format_tasks
from .telegram import TelegramClient
from .web import PAGE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chronos")

db = create_database(settings)
tasks = TaskService(db, settings.tz)
ai = ExternalAI(settings)
telegram = TelegramClient(settings.telegram_bot_token)
scheduler = AsyncIOScheduler(timezone=settings.tz)

HELP_TEXT = (
    "<b>Chronos</b>\n"
    "Send a task in Chinese or English. I will store a concise English version:\n"
    "• Finish the report tomorrow at 17:00\n"
    "• Attend Friday's meeting at 10:00 #Chronos\n"
    "• Buy milk\n"
    "(Dates, times, and tags are optional.)\n\n"
    "Commands:\n"
    "/help — Show this guide\n"
    "/tasks — List open tasks\n"
    "/done 1 — Complete task 1\n"
    "/reschedule 1 tomorrow at 10:00 — Change task 1's due time\n"
    "/edit 1 move it to Friday and rename it — Edit task 1\n"
    "/clear — Delete all tasks after confirmation"
)

CLEAR_CONFIRM_TEXT = "Delete all tasks? This cannot be undone."
CLEAR_KEYBOARD = {
    "inline_keyboard": [
        [{"text": "Delete all tasks", "callback_data": "clear:confirm"}],
        [{"text": "Cancel", "callback_data": "clear:cancel"}],
    ]
}


async def send_daily_tasks() -> None:
    if settings.telegram_chat_id and telegram.enabled:
        result = await telegram.send_message(settings.telegram_chat_id, format_tasks(tasks.list_open(), settings.tz))
        if not result.get("ok"):
            code = result.get("error_code", "unknown")
            raise RuntimeError(f"Telegram daily delivery failed with code {code}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.initialize()
    if settings.enable_internal_scheduler:
        scheduler.add_job(send_daily_tasks, "cron", hour=8, minute=0, id="daily_tasks", replace_existing=True)
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


def require_web_auth(authorization: str | None = Header(default=None)) -> None:
    if not settings.web_password:
        return
    expected = base64.b64encode(f"{settings.web_username}:{settings.web_password}".encode()).decode()
    if not authorization or not hmac.compare_digest(authorization, f"Basic {expected}"):
        raise HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Basic"})


class NaturalTask(BaseModel):
    text: str


class TelegramChat(BaseModel):
    model_config = ConfigDict(strict=True)
    id: int = Field(ge=-(2**63), le=2**63 - 1)


class TelegramMessage(BaseModel):
    model_config = ConfigDict(strict=True)
    chat: TelegramChat
    text: str | None = None


class TelegramCallbackQuery(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str = Field(min_length=1, max_length=128)
    message: TelegramMessage
    data: str | None = None


class TelegramUpdate(BaseModel):
    model_config = ConfigDict(strict=True)
    update_id: int = Field(ge=0, le=2**63 - 1)
    message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/internal/daily")
async def trigger_daily_tasks(x_chronos_scheduler_secret: str | None = Header(default=None)) -> dict:
    if not settings.scheduler_secret:
        raise HTTPException(status_code=503, detail="Scheduler endpoint is not configured")
    if not hmac.compare_digest(x_chronos_scheduler_secret or "", settings.scheduler_secret):
        raise HTTPException(status_code=403, detail="Invalid scheduler credential")
    await send_daily_tasks()
    return {"ok": True}


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_web_auth)])
async def index() -> str:
    return PAGE


@app.get("/api/tasks", dependencies=[Depends(require_web_auth)])
async def list_tasks() -> list[dict]:
    return tasks.list_open()


@app.post("/api/tasks/natural", dependencies=[Depends(require_web_auth)])
async def create_natural_task(body: NaturalTask) -> dict:
    try:
        parsed = await ai.parse(body.text)
    except AIError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return tasks.create(parsed.title, parsed.due_at, parsed.project)


@app.post("/api/tasks/{task_id}/complete", dependencies=[Depends(require_web_auth)])
async def complete_task(task_id: int) -> dict:
    if not tasks.complete(task_id):
        raise HTTPException(status_code=404, detail="Open task not found")
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
    source_message = update.message or (update.callback_query.message if update.callback_query else None)
    if source_message is None:
        return {"ok": True}
    chat_id = source_message.chat.id
    if settings.telegram_chat_id and chat_id != settings.telegram_chat_id:
        raise HTTPException(status_code=403, detail="Unauthorized chat")
    update_id = update.update_id
    receipt = db.get_update(update_id)
    if update.callback_query:
        if receipt is None:
            data = update.callback_query.data
            if data == "clear:confirm":
                def action() -> str:
                    deleted = tasks.clear()
                    noun = "task" if deleted == 1 else "tasks"
                    return f"Deleted {deleted} {noun}.\n\n{format_tasks(tasks.list_open(), settings.tz)}"
            elif data == "clear:cancel":
                action = lambda: "Clear cancelled."
            else:
                action = lambda: "This action is no longer available."
            receipt = db.process_update(update_id, action)
    else:
        text = (source_message.text or "").strip()
        if not text:
            return {"ok": True}
        if receipt is None:
            # Network work happens before acquiring the persistence transaction.
            action = await prepare_message(text)
            receipt = db.process_update(update_id, action)
    if not receipt["delivered"]:
        parse_mode = "HTML" if receipt["reply"] == HELP_TEXT else None
        reply_markup = CLEAR_KEYBOARD if receipt["reply"] == CLEAR_CONFIRM_TEXT else None
        send_options = {"parse_mode": parse_mode}
        if reply_markup:
            send_options["reply_markup"] = reply_markup
        result = await telegram.send_message(chat_id, receipt["reply"], **send_options)
        if not result.get("ok"):
            raise HTTPException(status_code=502, detail="Telegram reply failed; waiting for retry")
        db.mark_update_delivered(update_id)
    if update.callback_query:
        result = await telegram.answer_callback_query(update.callback_query.id)
        if not result.get("ok"):
            raise HTTPException(status_code=502, detail="Telegram callback acknowledgement failed")
    return {"ok": True}


async def handle_message(text: str) -> str:
    return (await prepare_message(text))()


async def prepare_message(text: str) -> Callable[[], str]:
    """Resolve external input first; the returned action performs no async work."""
    normalized = text.strip()
    command = normalized[1:].strip() if normalized.startswith("/") else None
    if command in {"start", "help"}:
        return lambda: HELP_TEXT
    if command == "tasks":
        return lambda: format_tasks(tasks.list_open(), settings.tz)
    if command == "clear":
        return lambda: CLEAR_CONFIRM_TEXT
    completed = re.fullmatch(r"done\s+(\d+)", command or "", re.IGNORECASE)
    if completed:
        def complete() -> str:
            position = int(completed.group(1))
            task = tasks.complete_position(position)
            if task is None:
                return f"Task {position} not found.\n\n{format_tasks(tasks.list_open(), settings.tz)}"
            return f"Completed: {task['title']}\n\n{format_tasks(tasks.list_open(), settings.tz)}"
        return complete
    rescheduled = re.fullmatch(r"reschedule\s+(\d+)\s+(.+)", command or "", re.IGNORECASE)
    if rescheduled:
        position = int(rescheduled.group(1))
        if tasks.get_open_by_position(position) is None:
            return lambda: f"Task {position} not found.\n\n{format_tasks(tasks.list_open(), settings.tz)}"
        try:
            parsed = await ai.parse(f"Reschedule to {rescheduled.group(2)}")
        except (AIError, ValueError) as error:
            return lambda reply=str(error): reply
        if not parsed.due_at:
            return lambda: "Please include a date or time."
        def reschedule() -> str:
            task = tasks.reschedule_position(position, parsed.due_at)
            if task is None:
                return f"Task {position} not found.\n\n{format_tasks(tasks.list_open(), settings.tz)}"
            return f"Rescheduled: {format_task(task, settings.tz)}\n\n{format_tasks(tasks.list_open(), settings.tz)}"
        return reschedule
    edited = re.fullmatch(r"edit\s+(\d+)\s+(.+)", command or "", re.IGNORECASE)
    if edited:
        position = int(edited.group(1))
        current = tasks.get_open_by_position(position)
        if current is None:
            return lambda: f"Task {position} not found.\n\n{format_tasks(tasks.list_open(), settings.tz)}"
        try:
            parsed = await ai.edit(current, edited.group(2))
        except (AIError, ValueError) as error:
            return lambda reply=str(error): reply
        task_id = current["id"]
        def edit() -> str:
            task = tasks.edit(task_id, parsed.title, parsed.due_at, parsed.project)
            if task is None:
                return f"Task {position} is no longer open.\n\n{format_tasks(tasks.list_open(), settings.tz)}"
            return f"Updated: {format_task(task, settings.tz)}\n\n{format_tasks(tasks.list_open(), settings.tz)}"
        return edit
    if command is not None or re.fullmatch(r"(?:代辦|清單|完成\s*#?\d+|延期\s*#?\d+.*)", normalized):
        return lambda: "Unknown command. Use /help to see available commands."
    try:
        parsed = await ai.parse(normalized)
    except (AIError, ValueError) as error:
        return lambda reply=str(error): reply
    def create() -> str:
        task = tasks.create(parsed.title, parsed.due_at, parsed.project)
        return f"Created: {format_task(task, settings.tz)}"
    return create
