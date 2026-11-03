import pytest

from test_meeting_coordination import configure, proposal, action
from test_mail_workflow import workflow
from test_system import system


async def prepared(workflow):
    service, calendar = configure(workflow)
    state = await proposal(workflow)
    text = await action(workflow, "準備會面邀請 " + state["version"] + "：client@example.org,bob@example.org；地點：台北辦公室")
    return service, calendar, service.state("ab12"), text


@pytest.mark.asyncio
async def test_invitation_preparation_is_explicit_and_unsent(workflow):
    service, calendar, state, text = await prepared(workflow)
    assert state["status"] == "invite_review"
    assert "bob@example.org" in text and "尚未" in text
    calendar.invite.assert_not_awaited()
    calendar.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_organizer_confirmed_does_not_mean_everyone_accepted(workflow):
    service, calendar, state, text = await prepared(workflow)
    calendar.event.return_value = None
    calendar.invite.side_effect = lambda body: {**body, "status": "confirmed", "organizer": {"self": True}, "etag": "v1"}
    text = await action(workflow, "確認會面邀請 " + state["invite_token"])
    assert "尚未全部接受" in text
    assert service.state("ab12")["status"] == "invite_pending"
    calendar.invite.assert_awaited_once()
    assert [a["email"] for a in calendar.invite.call_args.args[0]["attendees"]] == ["client@example.org", "bob@example.org"]


@pytest.mark.asyncio
async def test_stale_invitation_approval_never_submits(workflow):
    service, calendar, state, text = await prepared(workflow)
    await action(workflow, "確認會面邀請 deadbeef")
    calendar.invite.assert_not_awaited()


async def submitted(workflow):
    service, calendar, state, text = await prepared(workflow)
    calendar.event.return_value = None
    calendar.invite.side_effect = lambda body: {**body, "status": "confirmed", "organizer": {"self": True}, "etag": "v1"}
    await action(workflow, "確認會面邀請 " + state["invite_token"])
    body = calendar.invite.call_args.args[0]
    calendar.event.return_value = {**body, "status": "confirmed", "organizer": {"self": True}, "etag": "v1"}
    calendar.cancel_invitation.return_value = {}
    return service, calendar, service.state("ab12")


@pytest.mark.asyncio
@pytest.mark.parametrize("responses,expected", [
    (["accepted", "accepted"], "invite_accepted"),
    (["accepted", "needsAction"], "invite_pending"),
    (["accepted", "tentative"], "invite_pending"),
    (["accepted", "declined"], "invite_declined"),
    (["accepted", None], "invite_unverified"),
])
async def test_rsvp_is_verified_for_every_invitee(workflow, responses, expected):
    service, calendar, state = await submitted(workflow)
    for attendee, response in zip(calendar.event.return_value["attendees"], responses):
        attendee["responseStatus"] = response
    await action(workflow, "查看會面狀態")
    assert service.state("ab12")["status"] == expected
    calendar.invite.assert_awaited_once()


@pytest.mark.asyncio
async def test_omitted_guests_and_foreign_edits_are_not_accepted(workflow):
    service, calendar, state = await submitted(workflow)
    calendar.event.return_value["attendeesOmitted"] = True
    await action(workflow, "查看會面邀請")
    assert service.state("ab12")["status"] == "invite_unverified"
    calendar.invite.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_create_is_never_replayed_and_can_be_reconciled(workflow):
    from chronos.google_calendar import CalendarError
    service, calendar, state, text = await prepared(workflow)
    calendar.event.return_value = None
    calendar.invite.side_effect = CalendarError("timeout")
    command = "確認會面邀請 " + state["invite_token"]
    await action(workflow, command)
    await action(workflow, command)
    assert service.state("ab12")["status"] == "invite_uncertain"
    calendar.invite.assert_awaited_once()
    body = calendar.invite.call_args.args[0]
    calendar.event.return_value = {**body, "organizer": {"self": True}, "etag": "v1"}
    await action(workflow, "查看會面邀請")
    assert service.state("ab12")["status"] == "invite_pending"


@pytest.mark.asyncio
async def test_cancel_requires_its_own_review_and_provider_ack(workflow):
    service, calendar, state = await submitted(workflow)
    await action(workflow, "準備取消會面邀請 " + state["version"])
    pending = service.state("ab12")
    calendar.cancel_invitation.assert_not_awaited()
    await action(workflow, "確認取消會面邀請 " + pending["cancel_token"])
    assert service.state("ab12")["status"] == "invite_cancelled"
    calendar.cancel_invitation.assert_awaited_once_with(state["event_id"], "v1")


@pytest.mark.asyncio
async def test_source_change_and_opt_out_prevent_invitation(workflow):
    service, calendar, state, text = await prepared(workflow)
    workflow.patch("contacts", items={"client": {"emails": ["bob@example.org"], "do_not_contact": True}})
    calendar.event.return_value = None
    await action(workflow, "確認會面邀請 " + state["invite_token"])
    calendar.invite.assert_not_awaited()


@pytest.mark.asyncio
async def test_abandon_preparation_cannot_send_from_old_consent(workflow):
    service, calendar, state, text = await prepared(workflow)
    await action(workflow, "取消會面邀請準備 " + state["invite_token"])
    assert service.state("ab12")["status"] == "review"
    await action(workflow, "確認會面邀請 " + state["invite_token"])
    calendar.invite.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [(403, "review"), (429, "review"), (500, "invite_uncertain"), (None, "invite_uncertain")])
async def test_provider_failure_does_not_claim_invitation_success(workflow, status, expected):
    from chronos.google_calendar import CalendarError
    service, calendar, state, text = await prepared(workflow)
    calendar.event.return_value = None
    calendar.invite.side_effect = CalendarError("provider failure", status)
    await action(workflow, "確認會面邀請 " + state["invite_token"])
    assert service.state("ab12")["status"] == expected
    calendar.invite.assert_awaited_once()


@pytest.mark.asyncio
async def test_changed_event_guests_block_cancel(workflow):
    service, calendar, state = await submitted(workflow)
    calendar.event.return_value["attendees"].append({"email": "outsider@example.org", "responseStatus": "accepted"})
    await action(workflow, "準備取消會面邀請 " + state["version"])
    calendar.cancel_invitation.assert_not_awaited()
    assert service.state("ab12")["status"] == "invite_pending"


@pytest.mark.asyncio
async def test_ambiguous_cancellation_is_not_replayed(workflow):
    from chronos.google_calendar import CalendarError
    service, calendar, state = await submitted(workflow)
    await action(workflow, "準備取消會面邀請 " + state["version"])
    pending = service.state("ab12")
    calendar.cancel_invitation.side_effect = CalendarError("timeout")
    command = "確認取消會面邀請 " + pending["cancel_token"]
    await action(workflow, command)
    await action(workflow, command)
    await action(workflow, "查看會面邀請")
    assert service.state("ab12")["status"] == "invite_cancel_uncertain"
    calendar.cancel_invitation.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_confirmations_submit_one_invitation(workflow):
    import asyncio
    service, calendar, state, text = await prepared(workflow)
    calendar.event.return_value = None
    started, finish = asyncio.Event(), asyncio.Event()
    async def invite(body):
        started.set()
        await finish.wait()
        return {**body, "organizer": {"self": True}, "etag": "v1"}
    calendar.invite.side_effect = invite
    command = "確認會面邀請 " + state["invite_token"]
    first = asyncio.create_task(action(workflow, command))
    await started.wait()
    await action(workflow, command)
    await action(workflow, "查看會面邀請")
    assert service.state("ab12")["status"] == "inviting"
    finish.set()
    await first
    calendar.invite.assert_awaited_once()
    assert service.state("ab12")["status"] == "invite_pending"


@pytest.mark.asyncio
async def test_confirmed_guests_with_tentative_organizer_remain_pending(workflow):
    service, calendar, state = await submitted(workflow)
    calendar.event.return_value["status"] = "tentative"
    for attendee in calendar.event.return_value["attendees"]:
        attendee["responseStatus"] = "accepted"
    await action(workflow, "查看會面邀請")
    assert service.state("ab12")["status"] == "invite_pending"


@pytest.mark.asyncio
async def test_calendar_transport_explicitly_notifies_and_uses_etag():
    from unittest.mock import AsyncMock
    from chronos.google_calendar import GoogleCalendar
    from chronos.settings import Settings
    calendar = GoogleCalendar(Settings(_env_file=None))
    calendar.request = AsyncMock(return_value={})
    body = {"id": "owned"}
    await calendar.invite(body)
    assert calendar.request.call_args.kwargs == {"body": body, "params": {"sendUpdates": "all"}}
    await calendar.cancel_invitation("owned", "etag1")
    assert calendar.request.call_args.kwargs == {"params": {"sendUpdates": "all"}, "etag": "etag1"}


@pytest.mark.asyncio
async def test_opt_out_revoked_during_final_read_blocks_atomic_claim(workflow):
    service, calendar, state, text = await prepared(workflow)
    async def changed(*args):
        workflow.patch("contacts", items={"client": {"emails": ["bob@example.org"], "do_not_contact": True}})
        return None
    calendar.event.side_effect = changed
    await action(workflow, "確認會面邀請 " + state["invite_token"])
    calendar.invite.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_invitation_consent_is_not_submitted(workflow):
    service, calendar, state, text = await prepared(workflow)
    workflow.patch("meeting:ab12", invite_expires=0)
    await action(workflow, "確認會面邀請 " + state["invite_token"])
    calendar.invite.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_cancellation_without_ack_stays_unverified(workflow):
    service, calendar, state = await submitted(workflow)
    await action(workflow, "準備取消會面邀請 " + state["version"])
    pending = service.state("ab12")
    calendar.cancel_invitation.return_value = {"unexpected": "data"}
    await action(workflow, "確認取消會面邀請 " + pending["cancel_token"])
    assert service.state("ab12")["status"] == "invite_cancel_uncertain"


@pytest.mark.asyncio
async def test_private_hold_is_not_silently_converted_to_external_invitation(workflow):
    service, calendar = configure(workflow)
    state = await proposal(workflow)
    await action(workflow, "暫留會面時段 " + state["version"])
    await action(workflow, "準備會面邀請 " + state["version"] + "：client@example.org；地點：台北")
    assert service.state("ab12")["status"] == "held"
    calendar.invite.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_source_does_not_generate_meeting_candidate(workflow):
    from test_mail_correspondence import thread
    service, calendar = configure(workflow)
    messages = thread()
    messages[-1]["sender_ambiguous"] = True
    workflow.gmail.thread.return_value = messages
    await action(workflow, "協調會面：明天下午三點，台北，30 分鐘")
    assert service.state("ab12")["status"] == "needs_information"
    workflow.ai._generate_output.assert_not_awaited()
    calendar.invite.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_calendar_target_invalidates_old_invitation_consent(workflow):
    service, calendar, state, text = await prepared(workflow)
    workflow.settings.calendar_id = "another-calendar"
    await action(workflow, "確認會面邀請 " + state["invite_token"])
    calendar.invite.assert_not_awaited()


def test_invitation_receipt_and_owner_binding_prevent_replay(system, monkeypatch):
    from test_secretary_integration import integrated
    from test_mail_webhook import post
    client, tasks, bot, gmail, mail, calendar, events = integrated(system, monkeypatch)
    calendar.event.side_effect = None
    calendar.event.return_value = None
    calendar.invite.side_effect = lambda body: {**body, "organizer": {"self": True}, "etag": "v1"}
    assert post(client, 29901, text="協調會面：明天下午三點，台北，30 分鐘").status_code == 200
    state = mail.get("meeting:ab12")
    assert post(client, 29902, text="準備會面邀請 " + state["version"] + "：client@example.org；地點：台北", card=700).status_code == 200
    state = mail.get("meeting:ab12")
    assert mail.binding(123, 701)["mail_id"] == "ab12"
    command = "確認會面邀請 " + state["invite_token"]
    assert post(client, 29903, text=command, card=701, chat=456).status_code == 403
    calendar.invite.assert_not_awaited()
    for _ in range(2):
        assert post(client, 29904, text=command, card=701).status_code == 200
    calendar.invite.assert_awaited_once()
    assert mail.get("meeting:ab12")["status"] == "invite_pending"
    assert not tasks.list_open()
    gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_conversion_to_recurring_event_blocks_series_cancellation(workflow):
    service, calendar, state = await submitted(workflow)
    calendar.event.return_value["recurrence"] = ["RRULE:FREQ=DAILY"]
    await action(workflow, "準備取消會面邀請 " + state["version"])
    assert service.state("ab12")["status"] == "invite_pending"
    calendar.cancel_invitation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"recurringEventId": "series"}, {"privateCopy": True},
    {"eventType": "outOfOffice"}, {"guestsCanInviteOthers": True}, {"etag": None},
])
async def test_changed_event_scope_or_permissions_blocks_cancel(workflow, change):
    service, calendar, state = await submitted(workflow)
    calendar.event.return_value.update(change)
    await action(workflow, "準備取消會面邀請 " + state["version"])
    assert service.state("ab12")["status"] == "invite_pending"
    calendar.cancel_invitation.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_rsvp_value_remains_unverified(workflow):
    service, calendar, state = await submitted(workflow)
    calendar.event.return_value["attendees"][0]["responseStatus"] = {"fake": "accepted"}
    await action(workflow, "查看會面邀請")
    assert service.state("ab12")["status"] == "invite_unverified"


@pytest.mark.asyncio
async def test_fake_organizer_attendee_cannot_hide_unapproved_guest(workflow):
    service, calendar, state = await submitted(workflow)
    calendar.event.return_value["attendees"].append({"email": "attacker@example.org", "organizer": True})
    await action(workflow, "準備取消會面邀請 " + state["version"])
    assert service.state("ab12")["status"] == "invite_pending"
    calendar.cancel_invitation.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_attendee_domains_are_canonicalized_before_review(workflow):
    service, calendar = configure(workflow)
    state = await proposal(workflow)
    await action(workflow, "準備會面邀請 " + state["version"] + "：Client@EXAMPLE.ORG；地點：台北")
    assert service.state("ab12")["invitees"] == ["Client@example.org"]
