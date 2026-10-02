"""Canonical Markdown export with durable per-update send claims."""
from .study_delivery import StudyDeliveryLedger
from .telegram import TelegramError


async def export_note(db, telegram, *, chat_id, update_id, fingerprint, now):
    note = db.get_study_note(fingerprint)
    if note is None:
        return "not_found"
    key = f"export:{chat_id}:{update_id}"
    ledger = StudyDeliveryLedger(db)
    claim = ledger.claim(key, now)
    if claim is None:
        return db.get_study_delivery(key)["status"]
    filename, content = note.export()
    try:
        result = await telegram.send_document(chat_id, filename, content)
    except TelegramError:
        result = {}
    message_id = None
    if result.get("ok") is True and isinstance(result.get("result"), dict):
        delivered = result["result"]
        if delivered.get("document"):
            message_id = delivered.get("message_id")
    rejected = result.get("ok") is False and type(result.get("error_code")) is int and 400 <= result["error_code"] < 500
    state = ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)
    return state["status"]
