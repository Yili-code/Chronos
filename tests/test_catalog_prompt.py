from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from chronos.catalog_prompt import prompt_observed_catalogs
from chronos.course_tracking import COURSE_SCHEDULE, new_session, ProgressStatus
from chronos.material_bridge import MaterialObservationStore
from test_note_repository import repo


@pytest.mark.asyncio
async def test_observed_catalog_prompts_once_without_selecting(repo, tmp_path):
    now = datetime.now(timezone.utc)
    session = replace(new_session(COURSE_SCHEDULE[0], now.date(), 1), status=ProgressStatus.ANSWERED, reported_progress="chapter")
    repo.create_course_session(session)
    observations = MaterialObservationStore(tmp_path / "catalog.db")
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 8}}
    kwargs = dict(owner_chat_id=123, course_mapping={session.course_key: "1"})
    assert await prompt_observed_catalogs(repo, bot, observations, now=now, **kwargs) == {}
    observations.put({"status": "observed", "materials": [{"course_id": "1", "activity_id": "2", "source_id": "3", "filename": "a.pdf", "uploaded_at": None}]})
    now = datetime.now(timezone.utc)
    result = await prompt_observed_catalogs(repo, bot, observations, now=now, **kwargs)
    assert list(result.values()) == ["sent"]
    state = repo.get_material_selection(next(iter(result)))
    assert state["selection"]["selected_ids"] == []
    assert "非完整" in bot.send_message.call_args.args[1]
    assert await prompt_observed_catalogs(repo, bot, observations, now=now, **kwargs) == {}
    assert bot.send_message.await_count == 1
