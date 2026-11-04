from copy import deepcopy

import pytest

from chronos.google_calendar import CalendarError, GoogleCalendar
from chronos.gmail import GmailError
from chronos.meeting_invitations import MeetingInvitations
from test_meeting_coordination import action, configure, proposal
from test_mail_workflow import workflow


async def held(workflow):
    service, calendar = configure(workflow)
    state = await proposal(workflow)
    await action(workflow, "暫留會面時段 " + state["version"])
    event = {**deepcopy(calendar.insert.call_args.args[0]), "etag": "hold-v1", "organizer": {"self": True}}
    calendar.event.return_value = event
    calendar.agenda.return_value = [{**deepcopy(event), "_calendar_id": workflow.settings.calendar_id}]
    return service, calendar, service.state("ab12"), event


async def prepare(workflow, state):
    return await action(workflow, "準備會面邀請 " + state["version"] + "：client@example.org；地點：台北")


@pytest.mark.asyncio
async def test_hold_conversion_reviews_then_patches_same_event_once(workflow):
    service, calendar, state, event = await held(workflow)
    await prepare(workflow, state)
    reviewed = service.state("ab12")
    assert reviewed["status"] == "invite_review"
    calendar.invite_existing.assert_not_awaited()
    calendar.invite_existing.side_effect = lambda identifier, body, etag: {**deepcopy(body), "id": identifier,
        "etag": "invite-v2", "organizer": {"self": True}}
    text = await action(workflow, "確認會面邀請 " + reviewed["invite_token"])
    assert "尚未全部接受" in text
    assert service.state("ab12")["status"] == "invite_pending"
    args = calendar.invite_existing.call_args.args
    assert args[0] == state["event_id"] and args[2] == "hold-v1"
    assert "id" not in args[1] and args[1]["transparency"] == "opaque"
    await action(workflow, "確認會面邀請 " + reviewed["invite_token"])
    calendar.invite_existing.assert_awaited_once()
    calendar.insert.assert_awaited_once()
    calendar.invite.assert_not_awaited()
    calendar.delete_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"description": "private negotiation floor"}, {"location": "private home"},
    {"attachments": [{"fileUrl": "https://example.org/private"}]}, {"conferenceData": {"conferenceId": "private"}},
    {"recurrence": ["RRULE:FREQ=DAILY"]}, {"attendees": [{"email": "other@example.org"}]},
    {"organizer": {"self": False}}, {"visibility": "public"}, {"locked": True},
    {"extendedProperties": {"shared": {"private": "secret"}}}, {"unknownField": "private"},
])
async def test_changed_or_private_hold_content_is_not_published(workflow, change):
    service, calendar, state, event = await held(workflow)
    calendar.event.return_value = {**event, **change}
    await prepare(workflow, state)
    assert service.state("ab12")["status"] == "held"
    calendar.invite_existing.assert_not_awaited()
    calendar.delete_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"id": "different", "start": {"dateTime": "2026-11-04T15:05:00+08:00"}, "end": {"dateTime": "2026-11-04T16:00:00+08:00"}},
    {"id": "different", "start": {"date": "2026-11-04"}, "end": {"date": "2026-11-05"}},
    {"id": "different", "start": {"date": "2026-11-05"}, "end": {"date": "2026-11-04"}},
    {"id": "different", "start": {}, "end": {}},
])
async def test_own_hold_exclusion_never_hides_other_busy_or_invalid_intervals(workflow, extra):
    service, calendar, state, event = await held(workflow)
    calendar.agenda.return_value.append({**extra, "_calendar_id": "other"})
    try:
        await prepare(workflow, state)
    except GmailError as exc:
        assert "verification failed" in str(exc)
    assert service.state("ab12")["status"] == "held"
    calendar.invite_existing.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_id_on_another_calendar_is_not_excluded(workflow):
    service, calendar, state, event = await held(workflow)
    calendar.agenda.return_value.append({**event, "_calendar_id": "other"})
    await prepare(workflow, state)
    assert service.state("ab12")["status"] == "held"


@pytest.mark.asyncio
async def test_conversion_cancellation_restores_private_hold(workflow):
    service, calendar, state, event = await held(workflow)
    await prepare(workflow, state)
    reviewed = service.state("ab12")
    await action(workflow, "取消會面邀請準備 " + reviewed["invite_token"])
    assert service.state("ab12")["status"] == "held"
    assert service.state("ab12")["event_id"] == state["event_id"]
    calendar.invite_existing.assert_not_awaited()
    calendar.delete_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403, 412, 429])
async def test_definite_conversion_rejection_keeps_original_hold(workflow, status):
    service, calendar, state, event = await held(workflow)
    await prepare(workflow, state)
    reviewed = service.state("ab12")
    calendar.invite_existing.side_effect = CalendarError("rejected", status)
    await action(workflow, "確認會面邀請 " + reviewed["invite_token"])
    assert service.state("ab12")["status"] == "held"
    await action(workflow, "確認會面邀請 " + reviewed["invite_token"])
    calendar.invite_existing.assert_awaited_once()


@pytest.mark.asyncio
async def test_changed_etag_after_review_prevents_conversion(workflow):
    service, calendar, state, event = await held(workflow)
    await prepare(workflow, state)
    reviewed = service.state("ab12")
    calendar.event.return_value = {**event, "etag": "changed"}
    await action(workflow, "確認會面邀請 " + reviewed["invite_token"])
    calendar.invite_existing.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_conversion_does_not_replay_even_if_old_hold_is_read(workflow):
    service, calendar, state, event = await held(workflow)
    await prepare(workflow, state)
    reviewed = service.state("ab12")
    calendar.invite_existing.side_effect = CalendarError("timeout")
    await action(workflow, "確認會面邀請 " + reviewed["invite_token"])
    assert service.state("ab12")["status"] == "invite_uncertain"
    await action(workflow, "查看會面邀請")
    assert service.state("ab12")["status"] == "invite_unverified"
    await action(workflow, "確認會面邀請 " + reviewed["invite_token"])
    calendar.invite_existing.assert_awaited_once()
    calendar.invite.assert_not_awaited()


@pytest.mark.asyncio
async def test_conversion_transport_notifies_all_and_uses_etag(workflow):
    from unittest.mock import AsyncMock
    calendar = GoogleCalendar(workflow.settings)
    calendar.request = AsyncMock(return_value={"id": "event"})
    await calendar.invite_existing("event", {"summary": "reviewed"}, "v1")
    call = calendar.request.call_args
    assert call.args[0] == "PATCH" and call.args[1].endswith("/event")
    assert call.kwargs["params"] == {"sendUpdates": "all"}
    assert call.kwargs["etag"] == "v1"
    with pytest.raises(CalendarError):
        await calendar.invite_existing("event", {}, "")
    calendar.request.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_hold_without_calendar_binding_cannot_convert(workflow):
    service, calendar, state, event = await held(workflow)
    workflow.db.mutate_mail_state(workflow.key("meeting:ab12"), lambda old: {k: v for k, v in old.items() if k not in {"calendar_account", "calendar_id"}})
    await prepare(workflow, state)
    assert service.state("ab12")["status"] == "held"
    calendar.invite_existing.assert_not_awaited()


@pytest.mark.asyncio
async def test_recheck_other_busy_event_after_review_prevents_patch(workflow):
    service, calendar, state, event = await held(workflow)
    await prepare(workflow, state)
    reviewed = service.state("ab12")
    calendar.agenda.return_value.append({**deepcopy(event), "id": "new-busy", "_calendar_id": "other"})
    await action(workflow, "確認會面邀請 " + reviewed["invite_token"])
    calendar.invite_existing.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_conversion_can_reconcile_verified_invitation_without_replay(workflow):
    service, calendar, state, event = await held(workflow)
    await prepare(workflow, state)
    reviewed = service.state("ab12")
    captured = {}
    def uncertain(identifier, body, etag):
        captured.update(deepcopy(body))
        raise CalendarError("timeout")
    calendar.invite_existing.side_effect = uncertain
    await action(workflow, "確認會面邀請 " + reviewed["invite_token"])
    calendar.event.return_value = {**captured, "id": state["event_id"], "etag": "v2", "organizer": {"self": True}}
    await action(workflow, "查看會面邀請")
    assert service.state("ab12")["status"] == "invite_pending"
    calendar.invite_existing.assert_awaited_once()


@pytest.mark.asyncio
async def test_conversion_confirmation_expires_without_touching_private_hold(workflow):
    service, calendar, state, event = await held(workflow)
    await prepare(workflow, state)
    reviewed = service.state("ab12")
    workflow.patch("meeting:ab12", invite_expires=0)
    await action(workflow, "確認會面邀請 " + reviewed["invite_token"])
    calendar.invite_existing.assert_not_awaited()
    assert service.state("ab12")["status"] == "invite_review"


@pytest.mark.asyncio
async def test_simultaneous_conversion_confirms_only_patch_once(workflow):
    import asyncio
    service, calendar, state, event = await held(workflow)
    await prepare(workflow, state)
    reviewed = service.state("ab12")
    started, release = asyncio.Event(), asyncio.Event()
    async def patch(identifier, body, etag):
        started.set()
        await release.wait()
        return {**deepcopy(body), "id": identifier, "etag": "v2", "organizer": {"self": True}}
    calendar.invite_existing.side_effect = patch
    first = asyncio.create_task(action(workflow, "確認會面邀請 " + reviewed["invite_token"]))
    await started.wait()
    await action(workflow, "確認會面邀請 " + reviewed["invite_token"])
    release.set()
    await first
    calendar.invite_existing.assert_awaited_once()
    assert service.state("ab12")["status"] == "invite_pending"
