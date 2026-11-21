import pytest

from chronos.mail_correspondence import ReplyText
from chronos.mail_natural_workflow import MailPlan
from test_mail_correspondence import setup
from test_mail_workflow import workflow


@pytest.mark.asyncio
@pytest.mark.parametrize('request_text',['幫我回覆這封信','請幫我回覆這封郵件','幫我起草回覆這封信','Reply to this email'])
async def test_context_only_reply_reads_thread_before_content_clarification(workflow,request_text):
    setup(workflow)
    schemas=[]
    async def generate(*args):
        schemas.append(args[3]);assert args[3] is not MailPlan
        assert 'Earlier proposal' in args[1]
        return ReplyText(body='Thank you. I confirm receipt.')
    workflow.ai._generate_output.side_effect=generate
    (await workflow.prepare_action('ab12',request_text))()
    assert workflow.get('reply:ab12')['status']=='review' and schemas==[ReplyText]
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_grounded_owner_questions_are_not_silenced_by_deterministic_draft_intent(workflow):
    setup(workflow)
    workflow.ai._generate_output.return_value=ReplyText(body='Pending decision',questions=['可以承諾的交期為何？'])
    output=(await workflow.prepare_action('ab12','幫我回覆這封信'))()
    assert '交期' in str(output) and workflow.get('reply:ab12') is None
    assert workflow.get('followups') is None
