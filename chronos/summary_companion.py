"""One bounded local worker pass over durable confirmed selections."""
from .selection_store import decode
from .local_summary import generate_local_summary
from .course_tracking import ProgressStatus
from .study_delivery import StudyDeliveryLedger
from .telegram import TelegramError


async def notify_summary_problem(db, telegram, key, chat_id, status, now):
    messages = {
        "uncertain": "摘要處理結果不明，已停止自動重跑，避免重複生成或傳送。請保留目前選擇並檢查處理紀錄。",
        "failed": "摘要生成或傳送已達重試上限，未確認完成。請稍後檢查設定與服務狀態。",
        "context_mismatch": "選檔與課程進度資料不一致，已停止處理。請重新確認課程與進度。",
    }
    if status not in messages:
        return
    ledger = StudyDeliveryLedger(db)
    notice_key = f"notice:summary:{chat_id}:{key}:{status}"
    claim = ledger.claim(notice_key, now)
    if claim is None:
        return
    try:
        result = await telegram.send_message(chat_id, messages[status])
    except TelegramError:
        result = {}
    if not isinstance(result, dict):
        result = {}
    delivered = result.get("result")
    message_id = delivered.get("message_id") if result.get("ok") is True and isinstance(delivered, dict) else None
    rejected = result.get("ok") is False and type(result.get("error_code")) is int and 400 <= result["error_code"] < 500
    ledger.finish(notice_key, claim, now, message_id=message_id, definitely_rejected=rejected)


async def run_summary_pass(db, generator, telegram, pdf_store, *, owner_chat_id,
                           course_mapping, model, prompt_version, now, enabled=False, limit=5):
    if not enabled:
        return {"enabled": False, "processed": 0}
    if not owner_chat_id or not 1 <= limit <= 20:
        raise ValueError("owner and bounded batch required")
    outcomes = {}
    pending = sorted(db.list_material_selections(), key=lambda item: (item[1].get("processed_at", ""), item[0]))
    for key, state in pending:
        if len(outcomes) >= limit:
            break
        if state["chat_id"] != owner_chat_id or not state["selection"]["confirmed"]:
            continue
        if state.get("processing_status") in {"sent", "uncertain", "failed", "context_mismatch"}:
            await notify_summary_problem(db, telegram, key, owner_chat_id, state["processing_status"], now)
            continue
        selection = decode(state["selection"])
        session = db.get_course_session(selection.session_id)
        if (session is None or session.status is not ProgressStatus.ANSWERED
                or session.reported_progress != selection.reported_progress
                or course_mapping.get(session.course_key) != selection.course_id):
            outcome = "context_mismatch"
        else:
            outcome = await generate_local_summary(db, generator, telegram, pdf_store,
                selection=selection, selection_key=key, course=session.course_name,
                class_date=session.class_date, chat_id=owner_chat_id,
                model=model, prompt_version=prompt_version, now=now)
        def record(previous):
            # Never overwrite a terminal result produced by a concurrent pass.
            if previous.get("processing_status") == "sent":
                return previous
            return {**previous, "processing_status": outcome, "processed_at": now.isoformat()}
        db.mutate_material_selection(key, record)
        await notify_summary_problem(db, telegram, key, owner_chat_id, outcome, now)
        outcomes[key] = outcome
    return {"enabled": True, "processed": len(outcomes), "outcomes": outcomes}
