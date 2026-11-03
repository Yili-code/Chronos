import asyncio
from datetime import datetime, timedelta

import pytest

from chronos.gmail import GmailError
from chronos.mail_followups import MailFollowups
from test_mail_correspondence import act, setup, thread
from test_mail_workflow import workflow


def outgoing():
    messages = thread()
    messages[-1].update(sender="owner@example.com", labels=["SENT"])
    return messages


def now(workflow):
    return datetime(2026, 11, 3, 12, tzinfo=workflow.settings.tz)


async def track(workflow, owner_wait=False):
    setup(workflow)
    if not owner_wait:
        workflow.gmail.thread.return_value = outgoing()
    action = await workflow.prepare_action("ab12", "追蹤回覆：1 天", now(workflow))
    return action()


@pytest.mark.asyncio
async def test_direction_is_grounded_in_actual_latest_message(workflow):
    assert "等你回覆" in await track(workflow, owner_wait=True)
    assert "等對方回覆" in await track(workflow)
    workflow.ai._generate_output.assert_not_awaited()
    workflow.gmail.create_reply_draft.assert_not_awaited()
    assert not workflow.db.list_open_tasks()


@pytest.mark.asyncio
async def test_human_reply_resolves_wait_without_chasing(workflow):
    await track(workflow)
    workflow.gmail.thread.return_value = outgoing() + [{**thread()[-1], "id": "reply1", "received_at": 3}]
    result = await MailFollowups(workflow).refresh(now(workflow) + timedelta(days=2))
    assert result["resolved"] == 1 and result["reminded"] == 0
    assert MailFollowups(workflow).registry()["ab12"]["status"] == "resolved"
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_response_closes_waiting_owner(workflow):
    await track(workflow, owner_wait=True)
    workflow.gmail.thread.return_value = thread() + [{**outgoing()[-1], "id": "ownerreply", "received_at": 3}]
    assert (await MailFollowups(workflow).refresh(now(workflow) + timedelta(days=2)))["resolved"] == 1
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_reply_pauses_and_later_human_reply_resolves(workflow):
    await track(workflow)
    auto = {**thread()[-1], "id": "auto", "received_at": 3, "automatic": True}
    workflow.gmail.thread.return_value = outgoing() + [auto]
    service = MailFollowups(workflow)
    assert (await service.refresh(now(workflow) + timedelta(days=2)))["paused"] == 1
    assert service.registry()["ab12"]["status"] == "paused"
    workflow.gmail.thread.return_value = outgoing() + [auto, {**thread()[-1], "id": "human", "received_at": 4}]
    assert (await service.refresh(now(workflow) + timedelta(days=3)))["resolved"] == 1
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_due_reminder_is_owner_only_once_and_never_external_send(workflow):
    await track(workflow)
    service = MailFollowups(workflow)
    assert (await service.refresh(now(workflow) + timedelta(hours=23)))["reminded"] == 0
    assert (await service.refresh(now(workflow) + timedelta(days=2)))["reminded"] == 1
    assert (await service.refresh(now(workflow) + timedelta(days=3)))["reminded"] == 0
    workflow.telegram.send_message.assert_awaited_once()
    assert workflow.telegram.send_message.call_args.args[0] == 123
    assert "Proposal" in workflow.telegram.send_message.call_args.args[1]
    workflow.gmail.create_reply_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_prevents_future_reminders(workflow):
    await track(workflow)
    await act(workflow, "停止追蹤")
    assert (await MailFollowups(workflow).refresh(now(workflow) + timedelta(days=2)))["checked"] == 0
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_timeout_pauses_instead_of_reminding_from_stale_state(workflow):
    await track(workflow)
    workflow.gmail.thread.side_effect = GmailError("timeout")
    result = await MailFollowups(workflow).refresh(now(workflow) + timedelta(days=2))
    assert result["errors"] == 1
    assert MailFollowups(workflow).registry()["ab12"]["status"] == "paused"
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_notification_is_not_replayed(workflow):
    await track(workflow)
    workflow.telegram.send_message.return_value = {"ok": False, "error_code": 500}
    workflow.telegram.send_message.side_effect = None
    service = MailFollowups(workflow)
    assert (await service.refresh(now(workflow) + timedelta(days=2)))["errors"] == 1
    await service.refresh(now(workflow) + timedelta(days=3))
    workflow.telegram.send_message.assert_awaited_once()
    state = service.registry()["ab12"]
    assert workflow.get("followup-notification:ab12:" + state["version"])["delivery"] == "uncertain"


@pytest.mark.asyncio
async def test_concurrent_refresh_claims_one_notification(workflow):
    await track(workflow)
    service = MailFollowups(workflow)
    started, finish = asyncio.Event(), asyncio.Event()
    async def send(*args, **kwargs):
        started.set()
        await finish.wait()
        return {"ok": True, "result": {"message_id": 100}}
    workflow.telegram.send_message.side_effect = send
    first = asyncio.create_task(service.refresh(now(workflow) + timedelta(days=2)))
    await started.wait()
    await service.refresh(now(workflow) + timedelta(days=2))
    finish.set()
    await first
    workflow.telegram.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_parallel_track_preparations_do_not_overwrite(workflow):
    setup(workflow)
    actions = [await workflow.prepare_action("ab12", "追蹤回覆：1 天", now(workflow)) for _ in range(2)]
    assert "等你回覆" in actions[0]()
    assert "另一個操作" in actions[1]()


@pytest.mark.asyncio
async def test_no_deadline_means_tracking_without_automatic_reminder(workflow):
    setup(workflow)
    assert "尚未設定提醒" in await act(workflow, "追蹤回覆")
    await MailFollowups(workflow).refresh(now(workflow) + timedelta(days=10))
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_outgoing_message_resets_clock_instead_of_premature_chase(workflow):
    await track(workflow)
    workflow.gmail.thread.return_value = outgoing() + [{**outgoing()[-1], "id": "next", "received_at": 3}]
    service = MailFollowups(workflow)
    await service.refresh(now(workflow) + timedelta(hours=25))
    assert service.registry()["ab12"]["due_at"] == (now(workflow) + timedelta(hours=49)).timestamp()
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_reminder_registry_writes_are_receipt_transaction_compatible(workflow):
    setup(workflow)
    action = await workflow.prepare_action("ab12", "追蹤回覆：1 天", now(workflow))
    receipt = workflow.db.process_update(19001, action)
    assert "等你回覆" in receipt["reply"]
    assert MailFollowups(workflow).registry()["ab12"]["status"] == "waiting_owner"
