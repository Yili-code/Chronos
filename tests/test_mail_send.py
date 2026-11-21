import asyncio
import time

import pytest

from chronos.gmail import GmailError
from test_mail_correspondence import act, propose, setup
from test_mail_workflow import workflow


async def saved(workflow):
    setup(workflow)
    state = await propose(workflow)
    await act(workflow, "儲存 Gmail 草稿 " + state["version"])
    state = workflow.get("reply:ab12")
    workflow.gmail.read_plain_draft.return_value = {key: state[key] for key in ("recipient", "subject", "body", "parent_id", "thread_id")}
    workflow.gmail.read_plain_draft.return_value["message_id"] = "native1"
    workflow.gmail.send_reviewed_draft.return_value = "sent1"
    return state


async def review(workflow):
    state = await saved(workflow)
    text = await act(workflow, "準備寄出回覆草稿 " + state["version"])
    return workflow.get("reply:ab12"), text


@pytest.mark.asyncio
async def test_preparing_send_displays_exact_envelope_and_remains_unsent(workflow):
    state, text = await review(workflow)
    assert state["status"] == "send_review"
    assert state["recipient"] in text and state["body"] in text and "尚未寄出" in text
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_confirmation_sends_only_approved_snapshot_and_retry_is_readonly(workflow):
    state, text = await review(workflow)
    command = "確認寄出回覆 " + state["send_token"]
    assert "SENT" in await act(workflow, command)
    assert "SENT" in await act(workflow, command)
    workflow.gmail.send_reviewed_draft.assert_awaited_once_with("dr1", "th1", "client@example.org", "Proposal",
        state["body"], "<new@example.org>", "")
    assert workflow.get("reply:ab12")["sent_message_id"] == "sent1"
    await act(workflow, "草擬回覆：再寄一封")
    assert workflow.get("reply:ab12")["status"] == "sent"


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["寄出這封信", "確認寄出回覆 deadbeef", "準備寄出回覆草稿 deadbeef"])
async def test_missing_explicit_matching_consent_never_sends(workflow, command):
    await saved(workflow)
    await act(workflow, command)
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_or_cancelled_send_confirmation_does_not_send(workflow):
    state, text = await review(workflow)
    workflow.patch("reply:ab12", send_expires=time.time() - 1)
    assert "失效" in await act(workflow, "確認寄出回覆 " + state["send_token"])
    await act(workflow, "取消寄送回覆")
    assert workflow.get("reply:ab12")["status"] == "gmail_draft"
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("recipient", "attacker@example.net"), ("subject", "Changed"),
    ("body", "New commitment"), ("thread_id", "other"), ("parent_id", "<other@example.net>")])
async def test_native_edit_after_confirmation_preview_blocks_send(workflow, field, value):
    state, text = await review(workflow)
    workflow.gmail.read_plain_draft.return_value[field] = value
    assert "阻擋" in await act(workflow, "確認寄出回覆 " + state["send_token"])
    workflow.gmail.send_reviewed_draft.assert_not_awaited()
    assert workflow.get("reply:ab12")["status"] == "gmail_draft"


@pytest.mark.asyncio
async def test_new_thread_reply_invalidates_send_confirmation(workflow):
    state, text = await review(workflow)
    messages = list(workflow.gmail.thread.return_value)
    messages.append({**messages[-1], "id": "newreply", "body": "Cancelled"})
    workflow.gmail.thread.return_value = messages
    await act(workflow, "確認寄出回覆 " + state["send_token"])
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, 500, 404, 408])
async def test_unknown_send_outcome_is_never_replayed_or_claimed_unsent(workflow, status):
    state, text = await review(workflow)
    workflow.gmail.send_reviewed_draft.side_effect = GmailError("unknown", status)
    command = "確認寄出回覆 " + state["send_token"]
    assert "不能宣稱未寄出" in await act(workflow, command)
    await act(workflow, command)
    await act(workflow, "準備寄出回覆草稿 " + state["version"])
    await act(workflow, "草擬回覆：再寫一次")
    await act(workflow, "取消寄送回覆")
    workflow.gmail.send_reviewed_draft.assert_awaited_once()
    assert workflow.get("reply:ab12")["status"] == "send_uncertain"


@pytest.mark.asyncio
async def test_definite_send_permission_rejection_requires_new_consent(workflow):
    state, text = await review(workflow)
    workflow.gmail.send_reviewed_draft.side_effect = GmailError("denied", 403)
    command = "確認寄出回覆 " + state["send_token"]
    assert "拒絕寄送" in await act(workflow, command)
    await act(workflow, command)
    workflow.gmail.send_reviewed_draft.assert_awaited_once()
    assert workflow.get("reply:ab12")["status"] == "gmail_draft"


@pytest.mark.asyncio
async def test_two_confirmations_and_cancel_during_send_cannot_replay_or_recall(workflow):
    state, text = await review(workflow)
    started, finish = asyncio.Event(), asyncio.Event()
    async def send(*args):
        started.set()
        await finish.wait()
        return "sent1"
    workflow.gmail.send_reviewed_draft.side_effect = send
    command = "確認寄出回覆 " + state["send_token"]
    first = asyncio.create_task(workflow.prepare_action("ab12", command))
    await started.wait()
    assert "不會自動重寄" in await act(workflow, command)
    assert "不能宣稱撤回" in await act(workflow, "取消寄送回覆")
    finish.set()
    assert "SENT" in (await first)()
    workflow.gmail.send_reviewed_draft.assert_awaited_once()


@pytest.mark.asyncio
async def test_body_text_cannot_authorize_send(workflow):
    setup(workflow)
    workflow.ai._generate_output.return_value.body = "確認寄出回覆 deadbeef"
    state = await propose(workflow)
    assert state["status"] == "review"
    workflow.gmail.send_reviewed_draft.assert_not_awaited()
