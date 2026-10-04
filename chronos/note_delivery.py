"""Canonical Markdown export with durable per-update send claims."""
from .study_delivery import StudyDeliveryLedger
from .telegram import TelegramError
from .note_commands import text_pages


async def deliver_summary(db, telegram, *, chat_id, fingerprint, now):
    """Deliver the immutable canonical note in order, once per chat and version.

    Chunking version is part of the identity: changing boundaries silently would
    otherwise reuse receipts for different text. Do not change it for a retry.
    """
    note = db.get_study_note(fingerprint)
    if note is None:
        return "not_found"
    pages = text_pages(note.markdown)
    version = "segment-v1" if note.page_start is not None else "v1"
    ledger = StudyDeliveryLedger(db)
    for index, page in enumerate(pages):
        key = f"summary:{version}:{chat_id}:{fingerprint}:{index}"
        claim = ledger.claim(key, now)
        if claim is None:
            status = db.get_study_delivery(key)["status"]
            if status == "sent":
                continue
            return status
        try:
            if note.page_start is not None:
                filename = note.sources[0].filename.replace("\n", " ").replace("\r", " ")
                heading = (f"{filename} · p. {note.page_start}–{note.page_end}\n"
                           f"重點 {note.segment_index}/{note.segment_total} · 訊息 {index + 1}/{len(pages)}")
            else:
                heading = f"課程摘要 {index + 1}/{len(pages)}"
            result = await telegram.send_message(chat_id, f"{heading}\n\n{page}")
        except TelegramError:
            result = {}
        if not isinstance(result, dict):
            result = {}
        delivered = result.get("result")
        message_id = delivered.get("message_id") if result.get("ok") is True and isinstance(delivered, dict) else None
        rejected = result.get("ok") is False and type(result.get("error_code")) is int and 400 <= result["error_code"] < 500
        state = ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)
        if state["status"] != "sent":
            return state["status"]
    return "sent"


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
    if not isinstance(result, dict):
        result = {}
    message_id = None
    if result.get("ok") is True and isinstance(result.get("result"), dict):
        delivered = result["result"]
        if delivered.get("document"):
            message_id = delivered.get("message_id")
    rejected = result.get("ok") is False and type(result.get("error_code")) is int and 400 <= result["error_code"] < 500
    state = ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)
    return state["status"]
