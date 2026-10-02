"""One bounded local worker pass over durable confirmed selections."""
from .selection_store import decode
from .local_summary import generate_local_summary
from .course_tracking import ProgressStatus


async def run_summary_pass(db, generator, telegram, pdf_store, *, owner_chat_id,
                           course_mapping, model, prompt_version, now, enabled=False, limit=5):
    if not enabled:
        return {"enabled": False, "processed": 0}
    if not owner_chat_id or not 1 <= limit <= 20:
        raise ValueError("owner and bounded batch required")
    outcomes = {}
    for key, state in db.list_material_selections():
        if len(outcomes) >= limit:
            break
        if state["chat_id"] != owner_chat_id or not state["selection"]["confirmed"]:
            continue
        if state.get("processing_status") in {"sent", "uncertain", "failed", "context_mismatch"}:
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
        outcomes[key] = outcome
    return {"enabled": True, "processed": len(outcomes), "outcomes": outcomes}
