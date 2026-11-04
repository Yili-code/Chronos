import copy

import pytest

from chronos.ai import AIError
from test_meeting_coordination import configure, proposal, NOW
from test_mail_workflow import workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [False, True])
@pytest.mark.parametrize("error", [AIError("Provider unavailable"), TimeoutError()])
async def test_provider_outage_preserves_existing_candidate_and_reports_temporary_failure(workflow, stage, error):
    service, calendar = configure(workflow)
    await proposal(workflow)
    before = copy.deepcopy(workflow.get("meeting:ab12"))
    workflow.ai._generate_output.side_effect = error
    action = await service.propose("ab12", "明天下午三點，台北，30 分鐘", NOW, stage=stage)
    result = action()
    text = result if isinstance(result, str) else result["messages"][0]["text"]
    assert "暫時" in text
    assert workflow.get("meeting:ab12") == before
    calendar.insert.assert_not_awaited()
    workflow.gmail.create_reply_draft.assert_not_awaited()
