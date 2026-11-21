from datetime import timedelta

import pytest

from chronos.mail_followups import MailFollowups
from test_mail_correspondence import setup, thread
from test_mail_followups import now
from test_mail_workflow import workflow


def outgoing(targets):
    return thread() + [{**thread()[0], "id": "sentnew", "received_at": 3, "to": targets}]


async def begin(workflow, targets, target=None):
    setup(workflow)
    workflow.gmail.thread.return_value = outgoing(targets)
    command = "追蹤回覆：1 天" + ("；對象：" + target if target else "")
    action = await workflow.prepare_action("ab12", command, now(workflow))
    return action()


@pytest.mark.asyncio
async def test_group_target_requires_explicit_selection(workflow):
    text = await begin(workflow, ["alice@example.org", "bob@example.org"])
    assert "指定" in text
    assert not MailFollowups(workflow).registry()


@pytest.mark.asyncio
async def test_unrelated_group_reply_cannot_resolve_selected_person(workflow):
    await begin(workflow, ["alice@example.org", "bob@example.org"], "alice@example.org")
    workflow.gmail.thread.return_value = outgoing(["alice@example.org", "bob@example.org"]) + [
        {**thread()[-1], "id": "bobreply", "sender": "bob@example.org", "received_at": 4}]
    service = MailFollowups(workflow)
    result = await service.refresh(now(workflow) + timedelta(days=2), notify=False)
    assert result["resolved"] == 0
    assert service.registry()["ab12"]["status"] == "waiting_other"
    assert service.registry()["ab12"]["target"] == "alice@example.org"
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_selected_person_reply_resolves_without_reminder(workflow):
    await begin(workflow, ["alice@example.org", "bob@example.org"], "alice@example.org")
    workflow.gmail.thread.return_value = outgoing(["alice@example.org", "bob@example.org"]) + [
        {**thread()[-1], "id": "alicereply", "sender": "alice@example.org", "received_at": 4}]
    assert (await MailFollowups(workflow).refresh(now(workflow) + timedelta(days=2)))["resolved"] == 1
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["outsider@example.org", "owner@example.com", "Alice", "alice@example.org,bob@example.org"])
async def test_target_must_be_one_visible_recipient(workflow, target):
    await begin(workflow, ["alice@example.org", "bob@example.org"], target)
    assert not MailFollowups(workflow).registry()


@pytest.mark.asyncio
async def test_outgoing_without_recipient_metadata_does_not_guess(workflow):
    await begin(workflow, [])
    assert not MailFollowups(workflow).registry()


@pytest.mark.asyncio
async def test_unrelated_auto_reply_does_not_pause_target(workflow):
    await begin(workflow, ["alice@example.org", "bob@example.org"], "alice@example.org")
    workflow.gmail.thread.return_value = outgoing(["alice@example.org", "bob@example.org"]) + [
        {**thread()[-1], "id": "auto", "sender": "bob@example.org", "received_at": 4, "automatic": True}]
    service = MailFollowups(workflow)
    assert (await service.refresh(now(workflow) + timedelta(hours=1)))["paused"] == 0
    assert service.registry()["ab12"]["status"] == "waiting_other"


@pytest.mark.asyncio
async def test_explicit_target_uses_history_after_another_person_replies(workflow):
    setup(workflow)
    workflow.gmail.thread.return_value = outgoing(["alice@example.org", "bob@example.org"]) + [
        {**thread()[-1], "id": "boblatest", "sender": "bob@example.org", "received_at": 4}]
    action = await workflow.prepare_action("ab12", "追蹤回覆：1 天；對象：alice@example.org", now(workflow))
    action()
    state = MailFollowups(workflow).registry()["ab12"]
    assert state["status"] == "waiting_other" and state["target"] == "alice@example.org"


@pytest.mark.asyncio
async def test_owner_reply_to_someone_else_does_not_close_wait(workflow):
    setup(workflow)
    workflow.gmail.thread.return_value = thread()
    (await workflow.prepare_action("ab12", "追蹤回覆：1 天", now(workflow)))()
    workflow.gmail.thread.return_value = thread() + [{**thread()[0], "id": "other", "to": ["other@example.org"], "received_at": 3}]
    service = MailFollowups(workflow)
    assert (await service.refresh(now(workflow) + timedelta(hours=1)))["resolved"] == 0
    assert service.registry()["ab12"]["status"] == "waiting_owner"


@pytest.mark.asyncio
async def test_owner_message_to_unrelated_recipient_does_not_reset_clock(workflow):
    await begin(workflow, ["alice@example.org"])
    service = MailFollowups(workflow)
    due = service.registry()["ab12"]["due_at"]
    workflow.gmail.thread.return_value = outgoing(["alice@example.org"]) + [{**thread()[0], "id": "other", "to": ["other@example.org"], "received_at": 4}]
    await service.refresh(now(workflow) + timedelta(hours=1))
    assert service.registry()["ab12"]["due_at"] == due


@pytest.mark.asyncio
async def test_existing_unfinished_target_is_not_silently_replaced(workflow):
    await begin(workflow, ["alice@example.org", "bob@example.org"], "alice@example.org")
    service = MailFollowups(workflow)
    state = service.registry()["ab12"]
    action = await workflow.prepare_action("ab12", "追蹤回覆：1 天；對象：bob@example.org", now(workflow))
    assert "先停止" in action()
    assert service.registry()["ab12"] == state


@pytest.mark.asyncio
async def test_legacy_unbound_wait_pauses_instead_of_inventing_target(workflow):
    await begin(workflow, ["alice@example.org"])
    service = MailFollowups(workflow)
    state = service.registry()["ab12"]
    workflow.patch("followups", **{"ab12": {k: v for k, v in state.items() if k != "target"}})
    assert (await service.refresh(now(workflow) + timedelta(days=2)))["paused"] == 1
    assert "核對特定對象" in service.describe(service.registry()["ab12"])
    workflow.telegram.send_message.assert_not_awaited()
