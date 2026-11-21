import asyncio
from datetime import timedelta

import pytest

from chronos import mail_followups as module
from chronos.mail_followups import MailFollowups
from test_mail_followups import track, now, outgoing
from test_mail_workflow import workflow


@pytest.mark.asyncio
async def test_followup_provider_wait_is_bounded(workflow, monkeypatch):
    await track(workflow)
    monkeypatch.setattr(module, "FOLLOWUP_POLL_SECONDS", 0.01, raising=False)
    async def never(*args):
        await asyncio.Event().wait()
    workflow.gmail.thread.side_effect = never
    result = await asyncio.wait_for(MailFollowups(workflow).refresh(now(workflow)), timeout=1)
    assert result["errors"] == 1
    assert MailFollowups(workflow).registry()["ab12"]["read_unverified"] is True
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_cursor_resumes_with_other_thread_after_slow_first_thread(workflow, monkeypatch):
    await track(workflow)
    service = MailFollowups(workflow)
    first = service.registry()["ab12"]
    second = {**first, "thread_id": "th2", "version": "other-version", "baseline_id": "other-sentnew", "latest_id": "other-sentnew"}
    workflow.patch("followups", ac12=second)
    messages = [{**m, "id": "ac12" if m["id"] == "ab12" else "other-" + m["id"], "thread_id": "th2"} for m in outgoing()]
    workflow.patch("message:ac12", mail=messages[1])
    calls = []
    async def remote(thread_id):
        calls.append(thread_id)
        if thread_id == "th1":
            await asyncio.Event().wait()
        return messages
    workflow.gmail.thread.side_effect = remote
    monkeypatch.setattr(module, "FOLLOWUP_POLL_SECONDS", 0.05)
    result = await service.refresh(now(workflow))
    assert result["checked"] == 1 and result["deferred"] == 1
    assert workflow.get("followup-health")["cursor"] == 1
    calls.clear()
    await service.refresh(now(workflow))
    assert calls[0] == "th2"
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_timed_out_owner_delivery_is_not_replayed(workflow, monkeypatch):
    await track(workflow)
    service = MailFollowups(workflow)
    state = service.registry()["ab12"]
    monkeypatch.setattr(module, "FOLLOWUP_POLL_SECONDS", 0.01)
    async def never(*args, **kwargs):
        await asyncio.Event().wait()
    workflow.telegram.send_message.side_effect = never
    result = await service.refresh(now(workflow) + timedelta(days=2))
    assert result["errors"] == 1
    assert service.registry()["ab12"]["reason"] == "delivery_unverified"
    assert workflow.get("followup-notification:ab12:" + state["version"])["delivery"] == "sending"
    await service.refresh(now(workflow) + timedelta(days=3))
    workflow.telegram.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_only_check_does_not_reset_scheduler_cursor(workflow):
    await track(workflow)
    workflow.patch("followup-health", cursor=7)
    result = await MailFollowups(workflow).refresh(now(workflow), only="ab12", notify=False)
    assert result["checked"] == 1
    assert workflow.get("followup-health")["cursor"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("update", [{"status": "resolved"}, {"due_at": 9999999999}, {"baseline_id": "newer"}])
async def test_stale_read_failure_does_not_taint_updated_wait(workflow, update):
    from chronos.gmail import GmailError
    await track(workflow)
    service = MailFollowups(workflow)
    state = service.registry()["ab12"]
    async def failed(*args):
        service.update("ab12", state["version"], **update)
        raise GmailError("read failed")
    workflow.gmail.thread.side_effect = failed
    assert (await service.refresh(now(workflow)))["errors"] == 1
    current = service.registry()["ab12"]
    assert not current.get("read_unverified")
    assert all(current[k] == v for k, v in update.items())


@pytest.mark.asyncio
async def test_timeout_read_recovers_on_next_verified_poll(workflow, monkeypatch):
    await track(workflow)
    service = MailFollowups(workflow)
    monkeypatch.setattr(module, "FOLLOWUP_POLL_SECONDS", 0.01)
    async def never(*args):
        await asyncio.Event().wait()
    workflow.gmail.thread.side_effect = never
    await service.refresh(now(workflow))
    assert service.registry()["ab12"]["read_unverified"]
    workflow.gmail.thread.side_effect = None
    workflow.gmail.thread.return_value = outgoing()
    monkeypatch.setattr(module, "FOLLOWUP_POLL_SECONDS", 45)
    await service.refresh(now(workflow))
    assert service.registry()["ab12"]["read_unverified"] is False
    assert service.registry()["ab12"]["status"] == "waiting_other"
