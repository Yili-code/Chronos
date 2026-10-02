"""Deliver and bind a selection prompt without automatically choosing files."""
from .selection_store import SelectionStore
from .selection_buttons import selection_view
from .study_delivery import StudyDeliveryLedger
from .telegram import TelegramError


async def send_selection(db, telegram, *, key, chat_id, selection, now):
    store = SelectionStore(db)
    state = store.create(key, chat_id, selection)
    ledger = StudyDeliveryLedger(db)
    delivery_key = f"selection:{chat_id}:{key}"
    claim = ledger.claim(delivery_key, now)
    if claim is None:
        receipt = db.get_study_delivery(delivery_key)
        if receipt["status"] == "sent":
            store.bind_message(key, chat_id, receipt["message_id"])
        return receipt["status"]
    text, markup = selection_view(key, state)
    try:
        result = await telegram.send_message(chat_id, text, reply_markup=markup)
    except TelegramError:
        result = {}
    if not isinstance(result, dict):
        result = {}
    delivered = result.get("result")
    message_id = delivered.get("message_id") if result.get("ok") is True and isinstance(delivered, dict) else None
    rejected = result.get("ok") is False and type(result.get("error_code")) is int and 400 <= result["error_code"] < 500
    receipt = ledger.finish(delivery_key, claim, now, message_id=message_id, definitely_rejected=rejected)
    if receipt["status"] == "sent":
        store.bind_message(key, chat_id, receipt["message_id"])
    return receipt["status"]
