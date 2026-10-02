"""Bridge observed activity metadata to explicitly labelled selection prompts."""
import hashlib
from datetime import datetime, timedelta
from .study_materials import MaterialSelection, PdfMaterial, course_catalog
from .selection_delivery import send_selection


async def prompt_observed_catalogs(db, telegram, observations, *, owner_chat_id, course_mapping, now, limit=5):
    if not owner_chat_id or now.utcoffset() is None or not 1 <= limit <= 20:
        raise ValueError("owner, aware time and bounded batch required")
    results = {}
    for session in db.list_answered_course_sessions():
        if len(results) >= limit:
            break
        course_id = course_mapping.get(session.course_key)
        if not course_id or not session.reported_progress:
            continue
        key = hashlib.sha256(f"catalog-v1:{owner_chat_id}:{session.session_id}".encode()).hexdigest()[:32]
        if db.get_material_selection(key) is not None:
            receipt = db.get_study_delivery(f"selection:{owner_chat_id}:{key}")
            if receipt and receipt["status"] in {"sent", "uncertain", "failed"}:
                continue
        snapshots = observations.course_snapshots(course_id)
        if not snapshots or any(not timedelta(0) <= now - datetime.fromisoformat(s["observed_at"]) <= timedelta(hours=24) for s in snapshots):
            continue
        rows = observations.course_materials(course_id)
        catalog = course_catalog(course_id, tuple(PdfMaterial(row["source_id"], course_id, row["filename"], None) for row in rows))
        if not catalog:
            continue
        selection = MaterialSelection(session.session_id, course_id, session.reported_progress, catalog)
        results[key] = await send_selection(db, telegram, key=key, chat_id=owner_chat_id, selection=selection, now=now)
    return results
