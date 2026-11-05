import copy

import pytest

from chronos.ai import AIError
from chronos.mail_correspondence import ReplyText
from chronos.mail_natural_workflow import MailPlan
from chronos.mail_reply_decisions import ReplyDecision, requires_review
from test_mail_correspondence import act, setup
from test_mail_workflow import workflow


@pytest.mark.parametrize("text,expected", [
    ("答應價格，但價格尚未決定", True),
    ("承諾交付時間，但交付時間還沒確認", True),
    ("Confirm the amount, but it is still undecided", True),
    ("Agree to the price; delivery date is not yet decided", True),
    ("草擬婉拒，不要透露底價", False),
    ("Draft a proposal without promising a date", False),
    ("Do not promise an undecided price", False),
    ("我不確定日期，先問對方有空嗎", False),
])
def test_risk_screen_is_topic_independent_and_not_a_blanket_uncertainty_ban(text, expected):
    assert requires_review(text) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["草擬回覆：", "回信"])
async def test_conflicting_commitment_blocks_both_entrypoints_before_body_generation(workflow, prefix):
    setup(workflow)
    schemas = []
    async def generate(*args):
        schemas.append(args[3])
        if args[3] is MailPlan:
            return MailPlan(draft=True)
        assert args[3] is ReplyDecision
        return ReplyDecision(status="owner_decision_required", question="要承諾交期還是先保留？", reason="Same delivery fact is both promised and undecided")
    workflow.ai._generate_output.side_effect = generate
    text = await act(workflow, prefix + "承諾交付時間，但交付時間尚未決定")
    assert "要承諾交期" in text and workflow.get("reply:ab12") is None
    assert ReplyText not in schemas
    workflow.gmail.create_reply_draft.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment", [ReplyDecision(status="owner_decision_required", reason="Owner decision missing"),
    ReplyDecision(status="ready", question="仍要先確認哪個價格？", reason="Additional owner question exists"),
    AIError("Unavailable"), TimeoutError(), {"status": "unknown"}])
async def test_missing_or_failed_assessment_preserves_prior_draft(workflow, assessment):
    setup(workflow)
    await act(workflow, "草擬回覆：英文簡潔")
    before = copy.deepcopy(workflow.get("reply:ab12"))
    workflow.ai._generate_output.side_effect = assessment if isinstance(assessment, BaseException) else None
    workflow.ai._generate_output.return_value = assessment
    await act(workflow, "草擬回覆：承諾價格，但價格未定")
    assert workflow.get("reply:ab12") == before
    workflow.gmail.create_reply_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_different_decisions_do_not_block_a_consistent_draft(workflow):
    setup(workflow)
    workflow.ai._generate_output.side_effect = [ReplyDecision(status="ready", reason="Price is decided; no delivery date is promised"),
        ReplyText(body="價格已確認；交付日期尚未決定。")]
    await act(workflow, "草擬回覆：答應價格，交付日期尚未決定")
    assert workflow.get("reply:ab12")["status"] == "review"
    assert workflow.ai._generate_output.await_count == 2


@pytest.mark.asyncio
async def test_unverified_identity_is_rejected_before_additional_content_review(workflow):
    setup(workflow)
    messages = workflow.gmail.thread.return_value
    messages[-1]["sender_ambiguous"] = True
    await act(workflow, "草擬回覆：承諾價格，但價格未定")
    workflow.ai._generate_output.assert_not_awaited()
    assert workflow.get("reply:ab12") is None
