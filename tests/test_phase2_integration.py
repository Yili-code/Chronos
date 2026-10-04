"""Local Phase 2 vertical slice. External browser/provider delivery stays mocked."""
import asyncio
import base64
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from chronos.pdf_validation import isolated_pdf_page_count

from chronos import main
from chronos.catalog_prompt import prompt_observed_catalogs
from chronos.course_tracking import COURSE_SCHEDULE, new_session, ProgressStatus
from chronos.gemini_summary import GeminiSummary, PROMPT_VERSION
from chronos.material_bridge import MaterialObservationStore
from chronos.pdf_store import PdfStore
from chronos.summary_companion import run_summary_pass
from test_pdf_validation import make_pdf
from test_system import system


@pytest.mark.parametrize("pages,unavailable", [(2, False), (6, True)])
def test_catalog_to_confirmed_summary_and_export(system, tmp_path, monkeypatch, pages, unavailable):
    client, _, bot, config = system
    now = datetime.now(timezone.utc)
    session = replace(new_session(COURSE_SCHEDULE[-1], now.date(), 500),
                      status=ProgressStatus.ANSWERED, reported_progress="Chapter 1")
    main.db.create_course_session(session)
    metadata = {"status": "observed", "materials": [{"source_id": "1", "course_id": "42",
        "activity_id": "3", "filename": "lecture.pdf", "uploaded_at": None}]}
    observations = MaterialObservationStore(tmp_path / "catalog.sqlite3")
    observations.put(metadata)
    pdf_store = PdfStore(tmp_path / "pdfs")
    pdf = make_pdf(pages=pages)
    pdf_store.accept({"status": "downloaded", "source_id": "1", "byte_count": len(pdf),
                      "data_base64": base64.b64encode(pdf).decode()}, metadata["materials"])
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 501}}
    mapping = {session.course_key: "42"}
    prompts = asyncio.run(prompt_observed_catalogs(main.db, bot, observations,
        owner_chat_id=123, course_mapping=mapping, now=datetime.now(timezone.utc)))
    key = next(iter(prompts))
    assert main.db.get_material_selection(key)["selection"]["selected_ids"] == []
    monkeypatch.setattr(bot, "request", AsyncMock(return_value={"ok": True}))
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-hook"}
    for update_id, action in [(901, "0:s0"), (902, "1:done")]:
        response = client.post("/telegram/webhook", headers=headers, json={"update_id": update_id,
            "callback_query": {"id": str(update_id), "data": f"pdf:{key}:{action}",
                "message": {"message_id": 501, "chat": {"id": 123}}}})
        assert response.status_code == 200
    assert main.db.get_material_selection(key)["selection"]["confirmed"]
    provider_requests = []
    def provider(request):
        provider_requests.append(request)
        body = json.loads(request.content)
        parts = body["contents"][0]["parts"]
        sent_pdf = base64.b64decode(parts[2]["inlineData"]["data"])
        assert isolated_pdf_page_count(sent_pdf) == min(3, pages)
        assert json.loads(parts[1]["text"])["physical_page_count"] == min(3, pages)
        if unavailable and len(provider_requests) == 1:
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        point = {"text": "概念" * 270, "citations": [{"source_id": "1", "page": 2}]}
        draft = {"scope": [point], "concepts": [point], "relationships": [point],
                 "exam_inferences": [], "uncertainties": []}
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP",
            "content": {"parts": [{"text": json.dumps(draft)}]}}]})
    settings = SimpleNamespace(gemini_api_key="synthetic", gemini_model="test-model",
        gemini_api_base="https://generativelanguage.googleapis.com/v1beta", ai_timeout=5)
    generator = GeminiSummary(settings, free_tier_confirmed=True, transport=httpx.MockTransport(provider))
    options = dict(owner_chat_id=123, course_mapping=mapping, model="test-model",
                   prompt_version=PROMPT_VERSION, now=datetime.now(timezone.utc), enabled=True)
    result = asyncio.run(run_summary_pass(main.db, generator, bot, pdf_store, **options))
    if unavailable:
        assert result['outcomes'] == {key: 'retry'}
        assert main.db.list_study_notes() == []
        # Early polling must not trigger another provider request.
        asyncio.run(run_summary_pass(main.db, generator, bot, pdf_store, **options))
        assert len(provider_requests) == 1
        options['now'] += timedelta(minutes=2)
        result = asyncio.run(run_summary_pass(main.db, generator, bot, pdf_store, **options))
    assert result["outcomes"] == {key: "sent"}
    assert asyncio.run(run_summary_pass(main.db, generator, bot, pdf_store, **options))["processed"] == 0
    assert len(provider_requests) == (3 if unavailable else 1)
    notes = main.db.list_study_notes()
    assert len(notes) == (2 if pages == 6 else 1)
    notes.sort(key=lambda note: note.page_start)
    note = notes[0]
    assert note.sources[0].page_count == pages
    assert "lecture.pdf, p. 2" in note.markdown
    if pages == 6:
        assert notes[1].page_start == 4 and notes[1].page_end == 6
        assert "lecture.pdf, p. 5" in notes[1].markdown
    sender = AsyncMock(return_value={"ok": True, "result": {"message_id": 700, "document": {"file_id": "fake"}}})
    monkeypatch.setattr(bot, "send_document", sender)
    for _ in range(2):
        response = client.post("/telegram/webhook", headers=headers, json={"update_id": 903,
            "message": {"message_id": 600, "chat": {"id": 123}, "text": f"/export {note.content_fingerprint}"}})
        assert response.status_code == 200
    sender.assert_awaited_once_with(123, *note.export())
