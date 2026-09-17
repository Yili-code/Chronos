import base64
import hmac
import logging
import re
from contextlib import asynccontextmanager
from collections.abc import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .db import Database
from .ai import AIError, ExternalAI
from .projects import ProjectService, format_projects
from .settings import settings
from .tasks import TaskService, format_tasks
from .telegram import TelegramClient
from .web import PAGE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chronos")

db = Database(settings.database_path)
tasks = TaskService(db, settings.tz)
ai = ExternalAI(settings)
projects = ProjectService(settings.projects_root, settings.github_token)
telegram = TelegramClient(settings.telegram_bot_token)
scheduler = AsyncIOScheduler(timezone=settings.tz)


async def send_daily_tasks() -> None:
    if settings.telegram_chat_id and telegram.enabled:
        await telegram.send_message(settings.telegram_chat_id, format_tasks(tasks.list_open(), settings.tz))


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.initialize()
    scheduler.add_job(send_daily_tasks, "cron", hour=8, minute=0, id="daily_tasks", replace_existing=True)
    scheduler.start()
    if settings.public_base_url and telegram.enabled:
        url = f"{settings.public_base_url.rstrip('/')}/telegram/webhook"
        try:
            await telegram.set_webhook(url, settings.telegram_webhook_secret)
        except Exception:
            logger.exception("Telegram webhook 設定失敗")
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Chronos", lifespan=lifespan)


def require_web_auth(authorization: str | None = Header(default=None)) -> None:
    if not settings.web_password:
        return
    expected = base64.b64encode(f"{settings.web_username}:{settings.web_password}".encode()).decode()
    if not authorization or not hmac.compare_digest(authorization, f"Basic {expected}"):
        raise HTTPException(status_code=401, detail="需要驗證", headers={"WWW-Authenticate": "Basic"})


class NaturalTask(BaseModel):
    text: str


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


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
        raise HTTPException(status_code=404, detail="找不到未完成代辦")
    return {"ok": True}


@app.get("/api/projects", dependencies=[Depends(require_web_auth)])
async def list_projects() -> list[dict]:
    return await projects.scan()


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)) -> dict:
    if settings.telegram_webhook_secret and not hmac.compare_digest(
        x_telegram_bot_api_secret_token or "", settings.telegram_webhook_secret
    ):
        raise HTTPException(status_code=403, detail="無效 webhook")
    update = await request.json()
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return {"ok": True}
    if settings.telegram_chat_id and chat_id != settings.telegram_chat_id:
        raise HTTPException(status_code=403, detail="未授權 chat")
    update_id = update.get("update_id")
    if type(update_id) is not int:
        await telegram.send_message(chat_id, await handle_message(text))
        return {"ok": True}
    receipt = db.get_update(update_id)
    if receipt is None:
        # Network work happens before acquiring the SQLite write lock.
        action = await prepare_message(text)
        with db.transaction() as connection:
            receipt = db.get_update(update_id)
            if receipt is None:
                reply = action()
                connection.execute(
                    "INSERT INTO telegram_updates(update_id, reply) VALUES (?, ?)", (update_id, reply)
                )
                receipt = {"reply": reply, "delivered": False}
    if not receipt["delivered"]:
        result = await telegram.send_message(chat_id, receipt["reply"])
        if not result.get("ok"):
            raise HTTPException(status_code=502, detail="Telegram 回覆失敗，等待重試")
        db.mark_update_delivered(update_id)
    return {"ok": True}


async def handle_message(text: str) -> str:
    return (await prepare_message(text))()


async def prepare_message(text: str) -> Callable[[], str]:
    """Resolve external input first; the returned action performs no async work."""
    normalized = text.lstrip("/")
    if normalized in {"start", "help", "說明"}:
        return lambda: "指令：\n• 新增 明天 17:00 完成報告 #Chronos\n• 代辦\n• 完成 3\n• 延期 3 到明天 10:00\n• 專案"
    if normalized in {"代辦", "清單", "tasks"}:
        return lambda: format_tasks(tasks.list_open(), settings.tz)
    if normalized in {"專案", "狀態", "projects"}:
        reply = format_projects(await projects.scan())
        return lambda: reply
    completed = re.fullmatch(r"(?:完成|done)\s*#?(\d+)", normalized, re.IGNORECASE)
    if completed:
        def complete() -> str:
            ok = tasks.complete(int(completed.group(1)))
            return "已完成。" if ok else "找不到該未完成代辦。"
        return complete
    postponed = re.fullmatch(r"延期\s*#?(\d+)\s*(?:到|至)?\s*(.+)", normalized)
    if postponed:
        try:
            parsed = await ai.parse(f"{postponed.group(2)} 更新期限")
        except (AIError, ValueError) as error:
            return lambda reply=str(error): reply
        if not parsed.due_at:
            return lambda: "請指定日期或時間。"
        def postpone() -> str:
            ok = tasks.postpone(int(postponed.group(1)), parsed.due_at)
            return f"已延期至 {parsed.due_at:%m/%d %H:%M}。" if ok else "找不到該未完成代辦。"
        return postpone
    try:
        parsed = await ai.parse(normalized)
    except (AIError, ValueError) as error:
        return lambda reply=str(error): reply
    def create() -> str:
        task = tasks.create(parsed.title, parsed.due_at, parsed.project)
        due = f"，期限 {parsed.due_at:%m/%d %H:%M}" if parsed.due_at else ""
        return f"已新增 #{task['id']}：{task['title']}{due}。"
    return create
