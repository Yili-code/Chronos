from datetime import datetime, timezone
from dataclasses import replace
from unittest.mock import AsyncMock
import pytest
from chronos import summary_companion
from chronos.course_tracking import COURSE_SCHEDULE, new_session, ProgressStatus
from chronos.selection_store import SelectionStore
from chronos.study_materials import MaterialSelection, PdfMaterial
from test_note_repository import repo


@pytest.mark.asyncio
async def test_pass_uses_saved_session_and_skips_finished_work(repo, monkeypatch):
    now = datetime.now(timezone.utc)
    session = replace(new_session(COURSE_SCHEDULE[0], now.date(), 1), status=ProgressStatus.ANSWERED, reported_progress="Chapter 1")
    repo.create_course_session(session)
    selection = MaterialSelection(session.session_id, "42", "Chapter 1", (PdfMaterial("1", "42", "a.pdf", None),)).choose("1", selected=True).confirm()
    SelectionStore(repo).create("pick", 123, selection)
    generate = AsyncMock(return_value="sent")
    monkeypatch.setattr(summary_companion, "generate_local_summary", generate)
    options = dict(owner_chat_id=123, course_mapping={session.course_key: "42"}, model="test", prompt_version="v1", now=now)
    assert (await summary_companion.run_summary_pass(repo, None, None, None, **options))["processed"] == 0
    result = await summary_companion.run_summary_pass(repo, None, None, None, enabled=True, **options)
    assert result["outcomes"] == {"pick": "sent"}
    assert generate.call_args.kwargs["course"] == session.course_name
    assert (await summary_companion.run_summary_pass(repo, None, None, None, enabled=True, **options))["processed"] == 0
    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_terminal_failure_notice_is_sent_once(repo):
    now = datetime.now(timezone.utc)
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 4}}
    for _ in range(2):
        await summary_companion.notify_summary_problem(repo, bot, "pick", 123, "uncertain", now)
    assert bot.send_message.await_count == 1
    assert "結果不明" in bot.send_message.call_args.args[1]
