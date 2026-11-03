import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from chronos.google_calendar import CalendarError
from chronos.meeting_coordination import Candidate, MeetingCoordination
from test_mail_correspondence import setup, thread
from test_mail_workflow import workflow


NOW = datetime.fromisoformat("2026-11-03T12:00:00+08:00")


def configure(workflow):
    setup(workflow)
    calendar = AsyncMock()
    calendar.configured = Mock(return_value=True)
    calendar.busy.return_value = []
    calendar.insert.side_effect = lambda body: {**body, "etag": "v1"}
    workflow.calendar = calendar
    workflow.ai._generate_output.return_value = Candidate(title="合作討論", start="2026-11-04T15:00:00+08:00", timezone="Asia/Taipei", minutes=30)
    return MeetingCoordination(workflow, calendar), calendar


async def action(workflow, text):
    return (await workflow.prepare_action("ab12", text, NOW))()


async def proposal(workflow):
    await action(workflow, "協調會面：明天下午三點，台北，30 分鐘")
    return workflow.get("meeting:ab12")


@pytest.mark.asyncio
async def test_candidate_checks_calendar_but_never_claims_attendee_consent(workflow):
    service, calendar = configure(workflow)
    text = await action(workflow, "協調會面：明天下午三點，台北，30 分鐘")
    assert "尚未聯繫" in text and "未確認對方" in text
    calendar.verify_owner.assert_awaited_once()
    calendar.busy.assert_awaited_once()
    calendar.insert.assert_not_awaited()
    assert service.state("ab12")["status"] == "review"


@pytest.mark.asyncio
async def test_owner_confirmation_creates_one_private_tentative_hold_without_attendees(workflow):
    service, calendar = configure(workflow)
    state = await proposal(workflow)
    text = await action(workflow, "暫留會面時段 " + state["version"])
    assert "對方尚未確認" in text and "沒有寄出邀請" in text
    body = calendar.insert.call_args.args[0]
    assert body["visibility"] == "private" and body["status"] == "tentative"
    assert "attendees" not in body and "conferenceData" not in body
    assert body["id"] == state["event_id"]
    await action(workflow, "暫留會面時段 " + state["version"])
    calendar.insert.assert_awaited_once()
    assert service.state("ab12")["status"] == "held"


@pytest.mark.asyncio
async def test_changed_thread_cancellation_prevents_stale_hold(workflow):
    service, calendar = configure(workflow)
    state = await proposal(workflow)
    workflow.gmail.thread.return_value = thread() + [{**thread()[-1], "id": "cancel", "received_at": 3, "body": "Cancelled"}]
    assert "過時" in await action(workflow, "暫留會面時段 " + state["version"])
    calendar.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_conflict_before_confirmation_prevents_write(workflow):
    service, calendar = configure(workflow)
    state = await proposal(workflow)
    calendar.busy.return_value = [(datetime.fromisoformat(state["start"]), datetime.fromisoformat(state["end"]))]
    assert "衝突" in await action(workflow, "暫留會面時段 " + state["version"])
    calendar.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_insert_requires_read_reconciliation_without_replay(workflow):
    service, calendar = configure(workflow)
    state = await proposal(workflow)
    calendar.insert.side_effect = CalendarError("timeout")
    assert "不確定" in await action(workflow, "暫留會面時段 " + state["version"])
    await action(workflow, "暫留會面時段 " + state["version"])
    calendar.insert.assert_awaited_once()
    calendar.event.return_value = {"id": state["event_id"], "status": "tentative", "summary": state["title"],
        "start": {"dateTime": state["start"]}, "end": {"dateTime": state["end"]},
        "extendedProperties": {"private": {"chronosMeeting": state["version"], "chronosAccount": workflow.namespace}}}
    assert "私人暫留" in await action(workflow, "查看會面狀態")
    assert service.state("ab12")["status"] == "held"


@pytest.mark.asyncio
async def test_incomplete_insert_response_is_uncertain(workflow):
    service, calendar = configure(workflow)
    state = await proposal(workflow)
    calendar.insert.side_effect = None
    calendar.insert.return_value = {"id": state["event_id"]}
    await action(workflow, "暫留會面時段 " + state["version"])
    assert service.state("ab12")["status"] == "uncertain"


@pytest.mark.asyncio
async def test_ambiguous_dst_time_requires_explicit_offset(workflow):
    service, calendar = configure(workflow)
    workflow.ai._generate_output.return_value = Candidate(title="Discussion", start="2026-11-01T01:30:00-04:00", timezone="America/New_York", minutes=30)
    text = (await service.prepare("ab12", "協調會面：紐約十一月一日一點半，30 分鐘", datetime.fromisoformat("2026-10-30T12:00:00+08:00")))()
    assert "UTC offset" in text
    calendar.busy.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_or_conflicting_constraints_invalidate_old_candidate(workflow):
    service, calendar = configure(workflow)
    state = await proposal(workflow)
    workflow.ai._generate_output.return_value = Candidate(title="", start="", timezone="", minutes=0, questions=["你希望哪一天？"])
    assert "需要補充" in await action(workflow, "協調會面：某天下午，不知道哪天")
    await action(workflow, "暫留會面時段 " + state["version"])
    calendar.insert.assert_not_awaited()
    assert service.state("ab12")["status"] == "needs_information"


@pytest.mark.asyncio
async def test_opt_out_blocks_meeting_generation_and_no_invitation_is_created(workflow):
    service, calendar = configure(workflow)
    workflow.patch("contacts", items={"one": {"emails": ["client@example.org"], "do_not_contact": True}})
    assert "不主動聯絡" in await action(workflow, "協調會面：明天下午三點，30 分鐘")
    workflow.ai._generate_output.assert_not_awaited()
    calendar.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_requires_matching_owned_unchanged_event_and_etag(workflow):
    service, calendar = configure(workflow)
    state = await proposal(workflow)
    await action(workflow, "暫留會面時段 " + state["version"])
    calendar.event.return_value = {**calendar.insert.call_args.args[0], "etag": "v1"}
    assert "已取消" in await action(workflow, "取消會面暫留 " + state["version"])
    calendar.delete_event.assert_awaited_once_with(state["event_id"], "v1")
    assert service.state("ab12")["status"] == "cancelled"


@pytest.mark.asyncio
async def test_changed_calendar_event_is_not_deleted(workflow):
    service, calendar = configure(workflow)
    state = await proposal(workflow)
    await action(workflow, "暫留會面時段 " + state["version"])
    calendar.event.return_value = {**calendar.insert.call_args.args[0], "etag": "v2", "attendees": [{"email": "other@example.org"}]}
    assert "外部修改" in await action(workflow, "取消會面暫留 " + state["version"])
    calendar.delete_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_holds_and_prepared_cancel_cannot_overwrite_inflight_claim(workflow):
    service, calendar = configure(workflow)
    state = await proposal(workflow)
    cancel = await workflow.prepare_action("ab12", "取消會面暫留 " + state["version"], NOW)
    started, finish = asyncio.Event(), asyncio.Event()
    async def insert(body):
        started.set()
        await finish.wait()
        return {**body, "etag": "v1"}
    calendar.insert.side_effect = insert
    saving = asyncio.create_task(workflow.prepare_action("ab12", "暫留會面時段 " + state["version"], NOW))
    await started.wait()
    assert "取消未執行" in cancel()
    await action(workflow, "暫留會面時段 " + state["version"])
    finish.set()
    (await saving)()
    calendar.insert.assert_awaited_once()
    assert service.state("ab12")["status"] == "held"


@pytest.mark.asyncio
async def test_missing_calendar_permission_returns_safe_boundary(workflow):
    service, calendar = configure(workflow)
    calendar.configured.return_value = False
    assert "尚未完成授權" in await action(workflow, "協調會面：明天下午")
    calendar.verify_owner.assert_not_awaited()
    calendar.insert.assert_not_awaited()
