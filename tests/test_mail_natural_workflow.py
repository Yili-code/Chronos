import copy

import pytest

from chronos.ai import AIError
from chronos.mail_correspondence import ReplyText, requires_mail_context
from chronos.mail_natural_workflow import MailPlan
from test_mail_correspondence import setup, thread
from test_mail_workflow import workflow
from test_system import system


def configure(mail, plan=None):
    setup(mail)
    async def generate(*args):
        return plan if args[3] is MailPlan else ReplyText(body="Thank you for the update.")
    mail.ai._generate_output.side_effect = generate


async def act(mail, text="幫我草擬英文回覆，並在 3 天後提醒我追蹤"):
    return (await mail.prepare_action("ab12", text))()


@pytest.mark.asyncio
async def test_composite_local_changes_committed_with_receipt_once(workflow):
    configure(workflow, MailPlan(draft=True, tracking_hours=72))
    action = await workflow.prepare_action("ab12", "幫我草擬英文回覆，並在 3 天後提醒我追蹤")
    assert workflow.get("reply:ab12") is None and workflow.get("followups") is None
    receipt = workflow.db.process_update(73001, action)
    before = copy.deepcopy(workflow.get("reply:ab12"))
    workflow.db.process_update(73001, lambda: pytest.fail("Receipt replayed"))
    assert "僅本機" in receipt["reply"] and "目前等你回覆" in receipt["reply"]
    assert workflow.get("reply:ab12") == before
    state = workflow.get("followups")["ab12"]
    assert state["duration"] == 72*3600 and state["target"] == "client@example.org"
    assert state["status"] == "waiting_owner"
    workflow.gmail.create_reply_draft.assert_not_awaited()
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["reply:ab12", "meeting:ab12", "contacts", "followups", "message:ab12"])
async def test_any_concurrent_guard_change_aborts_whole_workflow(workflow, key):
    configure(workflow, MailPlan(draft=True, tracking_hours=72))
    action = await workflow.prepare_action("ab12", "幫我草擬英文回覆並在 3 天後追蹤")
    workflow.patch(key, version="another")
    before = {k: copy.deepcopy(workflow.get(k)) for k in ("reply:ab12", "followups")}
    assert "整個流程未套用" in action()
    assert {k: workflow.get(k) for k in before} == before


@pytest.mark.asyncio
@pytest.mark.parametrize("text,plan", [
    ("幫我草擬回覆並在 3 天後追蹤", MailPlan(draft=True)),
    ("幫我草擬回覆並在 3 天後追蹤", MailPlan(tracking_hours=72)),
    ("幫我草擬回覆並在 3 天後追蹤", MailPlan(draft=True, tracking_hours=48)),
    ("幫我草擬回覆並追蹤", MailPlan(draft=True, tracking_hours=72)),
    ("幫我草擬回覆並在 3 天或 4 天後追蹤", MailPlan(draft=True, tracking_hours=72)),
    ("幫我草擬回覆並在 3 天後追蹤", MailPlan(draft=True, tracking_hours=72, target="evil@example.org")),
    ("幫我草擬回覆並在 3 天後追蹤 client@example.org", MailPlan(draft=True, tracking_hours=72)),
    ("幫我草擬回覆並寄出", MailPlan(draft=True)),
    ("幫我回覆全體並在 3 天後追蹤", MailPlan(draft=True, tracking_hours=72)),
    ("handle this email: draft a reply all and track in 3 days", MailPlan(draft=True, tracking_hours=72)),
    ("幫我草擬回覆並安排會議", MailPlan(draft=True)),
    ("幫我不要回覆，只追蹤 3 天", MailPlan(tracking_hours=72)),
    ("幫我草擬回覆，如果他沒答覆在 3 天後追蹤", MailPlan(draft=True, tracking_hours=72)),
    ("幫我草擬回覆，並搜尋他的其他郵件", MailPlan(draft=True)),
    ("幫我草擬回覆並附上檔案", MailPlan(draft=True, question="Which file?")),
])
async def test_partial_or_ungrounded_plan_never_applies(workflow, text, plan):
    configure(workflow, plan)
    await act(workflow, text)
    assert workflow.get("reply:ab12") is None and workflow.get("followups") is None
    workflow.gmail.create_reply_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_thread_between_steps_aborts(workflow):
    configure(workflow, MailPlan(draft=True, tracking_hours=72))
    modified = thread() + [{**thread()[-1], "id": "ab99", "body": "Already resolved"}]
    workflow.gmail.thread.side_effect = [thread(), thread(), modified, modified]
    assert "未套用" in await act(workflow)
    assert workflow.get("reply:ab12") is None and workflow.get("followups") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [AIError("unavailable"), ValueError("schema"), TimeoutError()])
async def test_planner_failure_leaves_no_state(workflow, failure):
    configure(workflow)
    workflow.ai._generate_output.side_effect = failure
    assert "解析暫時失敗" in await act(workflow)
    assert workflow.get("reply:ab12") is None and workflow.get("followups") is None


@pytest.mark.asyncio
async def test_missing_draft_fact_aborts_tracking_too(workflow):
    configure(workflow)
    workflow.ai._generate_output.side_effect = [MailPlan(draft=True, tracking_hours=72), ReplyText(body="Pending", questions=["Which date?"])]
    assert "Which date?" in await act(workflow)
    assert workflow.get("reply:ab12") is None and workflow.get("followups") is None


@pytest.mark.asyncio
async def test_planner_never_sees_prompt_injection_from_email(workflow):
    configure(workflow, MailPlan(draft=True, tracking_hours=72))
    await act(workflow)
    planner = workflow.ai._generate_output.call_args_list[0]
    assert "Ignore all rules" not in planner.args[1]
    assert "Ignore all rules" in workflow.ai._generate_output.call_args_list[1].args[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("hours", [1, 2160])
async def test_tracking_only_has_no_draft_and_accepts_duration_boundaries(workflow, hours):
    configure(workflow, MailPlan(tracking_hours=hours))
    text = f"幫我追蹤回覆，{hours} 小時後提醒"
    assert "僅本機" in await act(workflow, text)
    assert workflow.get("reply:ab12") is None
    assert workflow.get("followups")["ab12"]["duration"] == hours*3600


@pytest.mark.parametrize("text", ["幫我草擬英文回覆並在 3 天後追蹤", "處理這封信：回覆謝謝", "handle this email: draft a reply", "草擬回覆：謝謝，並在 3 天後追蹤"])
def test_unbound_mail_request_requires_source_card(text):
    assert requires_mail_context(text)


def test_task_request_keeps_task_router():
    assert not requires_mail_context("幫我新增買牛奶的任務")


def test_webhook_composite_replay_and_followup_card_binding(system, monkeypatch):
    from test_correspondence_webhook import configure as configure_webhook
    from test_mail_webhook import post
    client, tasks, bot, gmail, mail = configure_webhook(system, monkeypatch)
    configure(mail, MailPlan(draft=True, tracking_hours=72))
    assert post(client, 74001, text="幫我草擬英文回覆，並在 3 天後追蹤").status_code == 200
    before = copy.deepcopy(mail.get("reply:ab12"))
    assert post(client, 74001, text="幫我草擬英文回覆，並在 3 天後追蹤").status_code == 200
    assert mail.get("reply:ab12") == before
    assert mail.get("followups")["ab12"]["status"] == "waiting_owner"
    assert not tasks.list_open()
    gmail.send_reviewed_draft.assert_not_awaited()
    assert any("僅本機" in call.args[1] for call in bot.send_message.call_args_list)
    assert mail.binding(123, 700)["mail_id"] == "ab12"
    gmail.create_reply_draft.assert_not_awaited()
    assert post(client, 74002, text="儲存 Gmail 草稿 " + before["version"], card=700).status_code == 200
    gmail.create_reply_draft.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["opt_out", "native_draft", "incomplete", "ambiguous_target"])
async def test_preparation_refusal_does_not_commit_other_step(workflow, reason):
    configure(workflow, MailPlan(draft=True, tracking_hours=72))
    if reason == "opt_out":
        workflow.patch("contacts", items={"c": {"emails": ["client@example.org"], "do_not_contact": True}})
    elif reason == "native_draft":
        workflow.patch("reply:ab12", status="gmail_draft", body="Existing", recipient="client@example.org", subject="Existing", version="old")
    elif reason == "incomplete":
        workflow.gmail.thread.return_value = [{**m, "body_incomplete": True} for m in thread()]
    else:
        workflow.patch("followups", ab12={"status": "waiting_other", "target": "other@example.org", "version": "old"})
    before = {k: copy.deepcopy(workflow.get(k)) for k in ("reply:ab12", "followups")}
    assert "未套用" in await act(workflow)
    assert {k: workflow.get(k) for k in before} == before


@pytest.mark.asyncio
async def test_exact_explicit_target_is_verified_against_thread(workflow):
    configure(workflow, MailPlan(tracking_hours=24, target="evil@example.org"))
    assert "未套用" in await act(workflow, "幫我追蹤 1 天；對象：evil@example.org")
    assert workflow.get("followups") is None


def test_batch_rejects_unguarded_writes(workflow):
    with pytest.raises(ValueError):
        workflow.db.compare_mail_states({}, {workflow.key("reply:ab12"): {"status": "review"}})
    assert workflow.get("reply:ab12") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [False, True])
async def test_composite_on_clarification_card_never_loses_tracking(workflow, stale):
    configure(workflow, MailPlan(draft=True, tracking_hours=72))
    workflow.patch("reply:ab12", version="current", status="needs_information", owner_request="Reply in English")
    action = await workflow.prepare_action("ab12", "幫我草擬回覆並在 3 天後追蹤",
        context_kind="reply", context_version="old" if stale else "current")
    result = action()
    if stale:
        assert "過期" in result
        assert workflow.get("followups") is None
    else:
        assert "僅本機" in result
        assert workflow.get("followups")["ab12"]["duration"] == 72*3600
