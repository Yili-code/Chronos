"""Resolve explicit selections against locally persisted browser PDF bytes."""
import asyncio
from dataclasses import replace
from .pdf_validation import isolated_pdf_page_count
from .summary_pipeline import PdfInput, generate_selected_summary


def load_selected_pdfs(selection, pdf_store):
    if not selection.confirmed or not selection.selected_ids:
        raise ValueError("explicit selection confirmation required")
    indexed = {row["source_id"]: row for row in pdf_store.catalog(selection.course_id)}
    catalog = []
    inputs = {}
    for item in selection.catalog:
        if item.source_id not in selection.selected_ids:
            catalog.append(item)
            continue
        stored = indexed.get(item.source_id)
        if stored is None or stored["filename"] != item.filename:
            raise ValueError("selected attachment unavailable or renamed")
        if item.sha256 is not None and item.sha256 != stored["sha256"]:
            raise ValueError("selected attachment content changed")
        data = pdf_store.read(selection.course_id, item.source_id, expected_sha256=stored["sha256"])
        inputs[item.source_id] = PdfInput(data, isolated_pdf_page_count(data))
        catalog.append(replace(item, sha256=stored["sha256"]))
    # Fill verified content identities without changing the user's selected set.
    return replace(selection, catalog=tuple(catalog)), inputs


async def generate_local_summary(db, generator, telegram, pdf_store, *, selection,
                                 course, class_date, chat_id, model, prompt_version, now):
    if not selection.confirmed:
        return "selection_required"
    try:
        verified, pdfs = await asyncio.to_thread(load_selected_pdfs, selection, pdf_store)
    except (ValueError, OSError):
        return "deferred_attachment"
    return await generate_selected_summary(db, generator, telegram, selection=verified,
        pdfs=pdfs, course=course, class_date=class_date, chat_id=chat_id,
        model=model, prompt_version=prompt_version, now=now)
