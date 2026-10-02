import hashlib
from io import BytesIO
from pypdf import PdfWriter
from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from chronos.db import Database
from chronos.study_materials import MaterialSelection, PdfMaterial
from chronos.summary_pipeline import PdfInput, generate_selected_summary


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
async def test_pipeline_persists_before_delivery_and_never_regenerates(tmp_path, valid):
    db = Database(tmp_path / "pipeline.db")
    db.initialize()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    data = output.getvalue()
    item = PdfMaterial("a", "course", "lecture.pdf", None, hashlib.sha256(data).hexdigest())
    selected = MaterialSelection("session", "course", "chapter one", (item,)).choose("a", selected=True).confirm()
    point = {"text": "概念" * (270 if valid else 1), "citations": [{"source_id": "a", "page": 1}]}
    generator = AsyncMock()
    generator.generate.return_value = {"scope": [point], "concepts": [point], "relationships": [point],
                                      "exam_inferences": [], "uncertainties": []}
    bot = AsyncMock()
    key = selected.generation_key(model="test-model", prompt_version="v1")
    async def send(*args, **kwargs):
        assert db.get_study_note(key) is not None
        return {"ok": True, "result": {"message_id": 9}}
    bot.send_message.side_effect = send
    now = datetime.now(timezone.utc)
    args = dict(selection=selected, pdfs={"a": PdfInput(data, 1)}, course="OS",
                class_date=now.date(), chat_id=123, model="test-model", prompt_version="v1", now=now)
    first = await generate_selected_summary(db, generator, bot, **args)
    assert first == ("sent" if valid else "uncertain")
    repeated = await generate_selected_summary(db, generator, bot, **args)
    assert repeated == ("sent" if valid else "uncertain")
    assert generator.generate.await_count == 1
    assert bot.send_message.await_count == (1 if valid else 0)
