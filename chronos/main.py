import base64
from html import escape
import hmac
import json
import logging
import re
from datetime import datetime
from contextlib import asynccontextmanager
from collections.abc import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .db import create_database
from .ai import AIError, ExternalAI
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
from .web import PAGE
from .task_timing import display_time, task_sort_key, timing_details
from .study_scheduler import tick_study, notify_study_failures
from .gmail import GmailClient, GmailError
from .mail_workflow import MailWorkflow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chronos")

db = create_database(settings)
tasks = TaskService(db, settings.tz)
ai = ExternalAI(settings)
telegram = TelegramClient(settings.telegram_bot_token)
gmail = GmailClient(settings)
scheduler = AsyncIOScheduler(timezone=settings.tz)

HELP_TEXT = (
    '<b>指令說明</b>\n'
    '參數請替換成實際內容；[ ] 表示選填，不需輸入括號。\n'
    '\n<b>任務</b>\n'
    '/tasks — 查看未完成任務\n'
    '/add 任務內容 [#標籤] — 原文新增，不使用 AI、不設定期限\n'
    '/done 順位 — 完成任務\n'
    '/edit 順位 修改內容 — 修改任務\n'
    '/edit #固定ID 修改內容 — 指定固定 ID 修改\n'
    '順位是 /tasks 最新清單中的序號，會隨清單變動。\n'
    '固定 ID 可見於任務卡片的編輯按鈕（Edit #編號）。\n'
    '範例：<code>/edit 1 移除期限</code>\n'
    '範例：<code>/edit #42 移除期限</code>\n'
    '\n也可直接回覆任務卡片，輸入修改內容。\n'
    '編輯已送出的訊息不會重新執行指令。\n'
    '\n/clear — 確認後刪除所有任務（含已完成），無法復原\n'
    '\n<b>郵件</b>\n'
    '回覆郵件卡片：刪除／保留／已讀\n'
    '建立任務範例：<code>新增任務：回覆會議邀請</code>\n'
    '不會自動寄送郵件。\n'
    '\n<b>課程</b>\n'
    '/classday 日期 課程代碼 class|off|auto\n'
    '日期格式：YYYY-MM-DD；須為該課原課表日。\n'
    'class＝上課；off＝不上課；auto＝恢復自動判定。\n'
    '輸入 /classday 可查課程代碼；也支援自然語言：\n'
    '<code>/classday 10/07 軟體工程不上課</code>\n'
    '/study_budget — 查看已記錄的學習 AI 用量，不產生內容\n'
    '\n<b>作業</b>\n'
    '/prepare 作業ID — 請求產生可編輯草稿\n'
    '/draft 作業ID [頁碼] — 閱讀已存草稿，不重新生成\n'
    '作業 ID 請取自作業通知的固定編號，輸入時不加 #。\n'
    '範例：<code>/prepare 42</code>、<code>/draft 42 1</code>\n'
    '\n<b>筆記</b>\n'
    '/notes [課程] — 列出已存筆記；省略課程則列出最近筆記\n'
    '/note 筆記編號 [段落編號] — 閱讀筆記\n'
    '/export 筆記編號 — 下載 Markdown 原文\n'
    '請從 /notes 複製完整筆記編號。\n'
    '頁碼與段落編號皆從 1 起算，省略時預設為 1。'
)

CLEAR_CONFIRM_TEXT = "Delete all tasks? This cannot be undone."
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
        lines.extend(["", f"<b>{label}</b>", f"Before: {escape(old)}", f"After: {escape(new)}"])
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
    return f"Deleted {deleted} {noun}.\n\nNo open tasks."


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
    db.initialize()
    if settings.enable_gmail:
        mail_workflow().require_config()
    if settings.enable_internal_scheduler:
        scheduler.add_job(send_daily_tasks, "cron", hour=8, minute=0, id="daily_tasks", replace_existing=True)
        if settings.enable_gmail:
            scheduler.add_job(send_daily_mail, "cron", hour=8, minute=0, timezone="Asia/Taipei",
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


async def run_study_tick() -> dict:
    if not settings.enable_study_tracking:
        return {"enabled": False}
    if not settings.telegram_chat_id or not telegram.enabled:
        raise HTTPException(status_code=503, detail="Study delivery is not configured")
    now = datetime.now(settings.tz)
    from .study_poll_queue import enqueue_scheduled_polls
    collection_slots = enqueue_scheduled_polls(db, now)
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
    return {**result, **assignment_result, **announcement_result, **notices, **calendar_sync_result, **calendar_result,
            'collection_slots_ensured': collection_slots}


@app.post("/internal/study")
async def trigger_study(x_chronos_scheduler_secret: str | None = Header(default=None)) -> dict:
    if not settings.scheduler_secret:
        raise HTTPException(status_code=503, detail="Scheduler endpoint is not configured")
    if not hmac.compare_digest(x_chronos_scheduler_secret or "", settings.scheduler_secret):
        raise HTTPException(status_code=403, detail="Invalid scheduler credential")
    return await run_study_tick()


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


class TelegramReplyReference(BaseModel):
    model_config = ConfigDict(strict=True)
    message_id: int = Field(gt=0)


class TelegramMessage(BaseModel):
    model_config = ConfigDict(strict=True)
    chat: TelegramChat
    text: str | None = None
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


@app.post("/internal/mail/backlog")
async def trigger_mail_backlog(x_chronos_scheduler_secret: str | None = Header(default=None)) -> dict:
    if not settings.scheduler_secret or not hmac.compare_digest(x_chronos_scheduler_secret or "", settings.scheduler_secret):
        raise HTTPException(status_code=403, detail="Invalid scheduler credential")
    try:
        return await mail_workflow().daily(backlog=True, request_key="bootstrap")
    except GmailError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


@app.post("/internal/daily")
async def trigger_daily_tasks(x_chronos_scheduler_secret: str | None = Header(default=None)) -> dict:
    if not settings.scheduler_secret:
        raise HTTPException(status_code=503, detail="Scheduler endpoint is not configured")
    if not hmac.compare_digest(x_chronos_scheduler_secret or "", settings.scheduler_secret):
        raise HTTPException(status_code=403, detail="Invalid scheduler credential")
    await send_daily_tasks()
    await send_daily_mail()
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
    return tasks.create(parsed.title, parsed.due_at, parsed.project, parsed.timing)


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
    source_message = update.edited_message or update.message or (update.callback_query.message if update.callback_query else None)
    if source_message is None:
        return {"ok": True}
    chat_id = source_message.chat.id
    if settings.telegram_chat_id and chat_id != settings.telegram_chat_id:
        raise HTTPException(status_code=403, detail="Unauthorized chat")
    update_id = update.update_id
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
            reply = (f"本批還有 {result['waiting']} 封，請先封存、刪除或標為已讀，再傳 /mail_next。"
                     if result.get("waiting") else "批次已送出，請查看上方郵件卡片。")
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
        receipt = db.get_update(update_id)
        if receipt is None:
            if update.edited_message is not None:
                action = lambda: "編輯舊訊息不會重新執行郵件動作，請傳送新指令。"
            elif mail_callback:
                match = re.fullmatch(r"mail:(trash|task|keep|read|(?:confirm|edit|cancel)_[a-f0-9]{8}):([A-Za-z0-9_-]{1,32})", update.callback_query.data or "")
                binding = mail.binding(chat_id, source_message.message_id)
                if not match or not binding or binding["mail_id"] != match.group(2):
                    raise HTTPException(status_code=403, detail="Mail callback is not bound to this card")
                try:
                    action = await mail.prepare_action(match.group(2), match.group(1))
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
            result = await telegram.request("deleteMessage", {"chat_id": chat_id, "message_id": card_id})
            missing = result.get("error_code") == 400 and "message to delete not found" in result.get("description", "").lower()
            if not result.get("ok") and not missing:
                raise HTTPException(status_code=502, detail="Mail action completed; Telegram card deletion pending")
        if not receipt["delivered"]:
            if receipt["reply"] != "已移到垃圾桶。":
                markup = None
                is_proposal = receipt["reply"].startswith("Add this to Tasks?\n\n")
                is_edit = receipt["reply"] == "Reply to this message with the new task title."
                identifier = (mail.binding(chat_id, source_message.message_id)["mail_id"] if mail_callback else mail_binding["mail_id"])
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
                    mail.patch(f"telegram:{chat_id}:{message_id}", mail_id=identifier)
            db.mark_update_delivered(update_id)
        if update.callback_query:
            result = await telegram.answer_callback_query(update.callback_query.id)
            if not result.get("ok"):
                raise HTTPException(status_code=502, detail="Mail callback acknowledgement pending")
        return {"ok": True}
    if update.callback_query and (update.callback_query.data or "").startswith("pdf:"):
        from .selection_buttons import apply_callback
        try:
            text, markup = apply_callback(db, chat_id, update.callback_query.data, message_id=source_message.message_id)
        except ValueError:
            await telegram.request("answerCallbackQuery", {"callback_query_id": update.callback_query.id,
                                   "text": "Invalid selection, no file selected, or this list has expired."})
            return {"ok": True}
        if source_message.message_id is None:
            raise HTTPException(status_code=422, detail="Selection message id required")
        result = await telegram.request("editMessageText", {"chat_id": chat_id, "message_id": source_message.message_id,
                                         "text": text, "reply_markup": markup})
        # Duplicate callbacks may result in an unchanged-message rejection;
        # state transitions remain revision-idempotent regardless of rendering.
        if not result.get("ok") and "message is not modified" not in str(result.get("description", "")).lower():
            raise HTTPException(status_code=502, detail="Selection display update failed")
        await telegram.answer_callback_query(update.callback_query.id)
        return {"ok": True}
    export_match = re.fullmatch(r"/export\s+([0-9a-f]{64})", (source_message.text or "").strip())
    if update.edited_message is None and update.callback_query is None and source_message.reply_to_message is None and export_match:
        from .note_delivery import export_note
        status = await export_note(db, telegram, chat_id=chat_id, update_id=update_id,
                                   fingerprint=export_match.group(1), now=datetime.now(settings.tz))
        if status != "not_found":
            if status in {"retry", "sending"}:
                raise HTTPException(status_code=503, detail="Document delivery pending")
            return {"ok": True, "document_status": status}
    receipt = db.get_update(update_id)
    if update.callback_query:
        if receipt is None:
            data = update.callback_query.data
            if data == "clear:confirm":
                action = clear_tasks_reply
            elif data == "clear:cancel":
                action = lambda: "Clear cancelled."
            elif data and re.fullmatch(r"edit:(?:retry|cancel):\d+", data):
                action = await prepare_pending_edit(data)
            elif data and re.fullmatch(r"task:edit:\d+", data):
                selected = tasks.get_open_by_id(int(data.rsplit(":", 1)[1]))
                if selected is None:
                    action = lambda: "This task is no longer open. Use /tasks to select another task."
                else:
                    card = task_card(selected, "Reply to this message with your changes.\n\n" +
                                     "\n".join(format_task_block(selected, settings.tz)))
                    card["reply_markup"] = {"force_reply": True, "selective": True}
                    action = lambda: message_bundle(card)
            else:
                action = lambda: "This action is no longer available."
            receipt = db.process_update(update_id, action)
    else:
        text = (source_message.text or "").strip()
        if not text:
            return {"ok": True}
        if receipt is None:
            # Network work happens before acquiring the persistence transaction.
            bound_task = (db.task_for_message(chat_id, source_message.reply_to_message.message_id)
                          if source_message.reply_to_message is not None else None)
            if update.edited_message is not None:
                action = lambda: (
                    "Editing a sent message does not change tasks or replay commands. "
                    "No task was changed. Send /edit 1 your changes as a new message, "
                    "or use /tasks → Edit and reply to the task card."
                )
            elif bound_task is not None:
                instruction = re.sub(r"^/edit\s*", "", text, flags=re.IGNORECASE).strip()
                action = await prepare_message(f"/edit #{bound_task} {instruction}", update_id=update_id)
            elif source_message.reply_to_message is not None:
                if source_message.message_id is None:
                    raise HTTPException(status_code=422, detail="Reply message id is required")
                received_at = datetime.now(settings.tz)
                received_date = received_at.date()
                review_summary = None
                session = db.get_course_session_by_prompt(source_message.reply_to_message.message_id)
                if (session is not None and session.class_date == received_date
                        and session.survey_task_id is not None
                        and session.status.value not in {"answered", "missed"}):
                    from .course_tracking import progress_is_unknown
                    if not progress_is_unknown(text):
                        try:
                            review_summary = await ai.summarize_progress(text)
                        except (AIError, ValueError):
                            pass
                action = lambda: db.record_course_reply(
                    source_message.reply_to_message.message_id, source_message.message_id, text,
                    local_date=received_date, update_id=update_id, received_at=received_at,
                    review_summary=review_summary,
                )
            else:
                action = await prepare_message(text, update_id=update_id)
            receipt = db.process_update(update_id, action)
    if not receipt["delivered"]:
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
    return {"ok": True}


def _legacy_parse_mode(reply: str) -> str | None:
    return "HTML" if (
        reply == HELP_TEXT or "<b>Tasks</b>" in reply or reply.startswith("Completed:")
        or reply.startswith("<b>")
    ) else None


async def prepare_pending_edit(data: str) -> Callable[[], str | dict]:
    match = re.fullmatch(r"edit:(retry|cancel):(\d+)", data)
    if not match:
        return lambda: "This saved edit is no longer available."
    action_name, pending_id_text = match.groups()
    pending_id = int(pending_id_text)
    pending = db.get_pending_task_edit(pending_id)
    if pending is None:
        return lambda: "This saved edit is no longer available. No task was changed."
    position = int(pending["position"])
    if action_name == "cancel":
        def cancel() -> str:
            db.delete_pending_task_edit(pending_id)
            return f"The saved edit for Task {position} was cancelled. The task was not changed."
        return cancel

    current_items = tasks.list_open()
    current = next((item for item in current_items if item["id"] == pending["task_id"]), None)
    if current is None:
        def stale() -> str:
            db.delete_pending_task_edit(pending_id)
            return "The target task is no longer open. The saved command was cancelled."
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
                    f"{reason}\nNo changes were made to Task {current_position}. "
                    "Your command is still saved; use the button below to retry it later."
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
                                   "No changes were applied. Retry the saved edit to use its latest values.",
                                   "reply_markup": pending_edit_keyboard(pending_id, current_position)})
        task = tasks.edit(current["id"], parsed.title, parsed.due_at, parsed.project, parsed.timing)
        if task is None:
            db.delete_pending_task_edit(pending_id)
            return message_bundle({
                "text": "The target task is no longer open. No changes were applied.",
                "parse_mode": None,
            })
        if tag_alias:
            tasks.save_project_alias(*tag_alias, existing_aliases=aliases)
        db.delete_pending_task_edit(pending_id)
        return message_bundle(
            {
                "text": format_update_summary(
                    current_position, current, task, tag_alias=tag_alias, aliases=aliases
                ),
                "parse_mode": "HTML",
                "task_id": task["id"],
                "reply_markup": task_edit_keyboard([task]),
            },
            {
                "text": format_updated_tasks(before, task, aliases=aliases, tag_alias=tag_alias),
                "parse_mode": "HTML",
            },
        )
    return retry


async def handle_message(text: str) -> str:
    output = (await prepare_message(text))()
    if isinstance(output, str):
        return output
    return "\n\n".join(message["text"] for message in output["messages"])


async def prepare_message(text: str, *, update_id: int | None = None) -> Callable[[], str | dict]:
    """Resolve external input first; the returned action performs no async work."""
    normalized = text.strip()
    command = normalized[1:].strip() if normalized.startswith("/") else None
    if command is not None and (command == 'announcements' or command.startswith('announcements ')):
        from .announcement_commands import announcements_query
        return lambda: announcements_query(db, command)
    if command is not None and (command == 'announcement' or command.startswith('announcement ')):
        from .announcement_commands import announcement_query
        return lambda: announcement_query(db, command)
    if command is not None and (command == 'draft' or command.startswith('draft ')):
        from .preparation_commands import draft_query
        return lambda: draft_query(db, command)
    if command is not None and (command == 'prepare' or command.startswith('prepare ')):
        from .preparation_commands import prepare_action
        return prepare_action(db, command, datetime.now(settings.tz))
    if command == 'study_budget':
        from .ai_budget import budget_report
        return lambda: budget_report(db, datetime.now(settings.tz))
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
    if command == "export" or (command is not None and command.startswith("export ")):
        return lambda: (
            "No exportable note was found, or the ID is invalid. "
            "Usage: /export full-note-id. Use /notes to find an ID first."
        )
    if command is not None and (command == "notes" or command.startswith("notes ") or command == "note" or command.startswith("note ")):
        from .note_commands import note_command
        return lambda: note_command(db, command) or (
            "Usage: /notes [course] or /note full-note-id [section]."
        )
    if command in {"start", "help"}:
        return lambda: HELP_TEXT
    if command == "tasks":
        def list_tasks() -> dict:
            items = tasks.list_open()
            return message_bundle({"text": task_list_text(items), "parse_mode": "HTML"})
        return list_tasks
    if command == "clear":
        return lambda: CLEAR_CONFIRM_TEXT
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
                f"Completed: {escape(task['title'])}\n\n"
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
                        f"{reason}\nNo changes were made to Task {position}. "
                        "Please try again later."
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
                        f"{reason}\nNo changes were made to Task {position}. "
                        "Your command has been saved; use the button below to retry it."
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
                                       "No changes were applied. Use /tasks and send your changes again."})
            task = tasks.edit(task_id, parsed.title, parsed.due_at, parsed.project, parsed.timing)
            if task is None:
                return message_bundle({
                    "text": f"Task {position} is no longer open. No changes were applied.",
                    "parse_mode": None,
                })
            if tag_alias:
                tasks.save_project_alias(*tag_alias, existing_aliases=aliases)
            return message_bundle(
                {
                    "text": format_update_summary(
                        position, current, task, tag_alias=tag_alias, aliases=aliases
                    ),
                    "parse_mode": "HTML",
                    "task_id": task_id,
                    "reply_markup": task_edit_keyboard([task]),
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
    elif command is not None or re.fullmatch(r"(?:代辦|清單|完成\s*#?\d+|延期\s*#?\d+.*)", normalized):
        return lambda: "Unknown command. Use /help to see available commands."
    else:
        try:
            parsed = await ai.parse(normalized)
        except (AIError, ValueError) as error:
            return lambda reply=str(error): reply
    aliases = tasks.project_aliases()
    def create() -> dict:
        task = tasks.create(parsed.title, parsed.due_at, parsed.project, parsed.timing)
        details = "\n".join(format_task_block(
            task, settings.tz, project_aliases=aliases
        ))
        return message_bundle(task_card(task, f"<b>Created</b>\n{details}"))
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
