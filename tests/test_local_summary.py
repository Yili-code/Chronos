import base64
from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from chronos.db import Database
from chronos.pdf_store import PdfStore
from chronos.local_summary import generate_local_summary, load_selected_pdfs
from chronos.study_materials import MaterialSelection, PdfMaterial
from test_pdf_validation import make_pdf
from chronos.selection_store import SelectionStore


def setup(tmp_path):
    db = Database(tmp_path / "notes.db")
    db.initialize()
    store = PdfStore(tmp_path / "pdfs")
    selection = MaterialSelection("session", "2", "lecture", (
        PdfMaterial("1", "2", "lecture.pdf", None),
        PdfMaterial("9", "2", "not-selected.pdf", None))).choose("1", selected=True).confirm()
    return db, store, selection


@pytest.mark.asyncio
async def test_missing_selected_attachment_never_calls_model(tmp_path):
    db, store, selection = setup(tmp_path)
    generator, bot = AsyncMock(), AsyncMock()
    now = datetime.now(timezone.utc)
    SelectionStore(db).create("pick", 123, selection)
    result = await generate_local_summary(db, generator, bot, store, selection=selection,
        course="OS", class_date=now.date(), chat_id=123, model="test", prompt_version="v1", now=now, selection_key="pick")
    assert result == "deferred_attachment"
    generator.generate.assert_not_awaited()
    bot.send_message.assert_not_awaited()


def test_index_loads_only_confirmed_sources_and_detects_changes(tmp_path):
    _, store, selection = setup(tmp_path)
    data = make_pdf()
    metadata = [{"source_id": "1", "course_id": "2", "activity_id": "3", "filename": "lecture.pdf", "uploaded_at": None}]
    receipt = store.accept({"status": "downloaded", "source_id": "1", "byte_count": len(data),
                           "data_base64": base64.b64encode(data).decode()}, metadata)
    verified, inputs = load_selected_pdfs(selection, store)
    assert set(inputs) == {"1"}
    assert inputs["1"].page_count == 2
    assert verified.selected_ids == selection.selected_ids
    assert verified.catalog[0].sha256 == receipt["sha256"]
    changed = make_pdf(pages=3)
    store.accept({"status": "downloaded", "source_id": "1", "byte_count": len(changed),
                  "data_base64": base64.b64encode(changed).decode()}, metadata)
    with pytest.raises(ValueError, match="content changed"):
        load_selected_pdfs(verified, store)
