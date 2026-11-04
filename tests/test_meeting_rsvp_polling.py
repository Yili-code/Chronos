import pytest

from chronos.meeting_invitations import MeetingInvitations
from test_meeting_invitations import submitted
from test_mail_workflow import workflow


@pytest.mark.asyncio
async def test_invitation_is_indexed_before_provider_write(workflow):
    service, calendar, state = await submitted(workflow)
    index = workflow.get("meeting-invitations")
    assert index["ab12"]["version"] == state["version"]


@pytest.mark.asyncio
async def test_accepted_rsvp_notifies_owner_once_without_external_chasing(workflow):
    service, calendar, state = await submitted(workflow)
    for attendee in calendar.event.return_value["attendees"]:
        attendee["responseStatus"] = "accepted"
    poller = MeetingInvitations(workflow)
    first = await poller.refresh()
    second = await poller.refresh()
    assert first["notified"] == 1 and second["notified"] == 0
    workflow.telegram.send_message.assert_awaited_once()
    assert workflow.telegram.send_message.call_args.args[0] == 123
    calendar.invite.assert_awaited_once()
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_change_produces_no_owner_notification(workflow):
    service, calendar, state = await submitted(workflow)
    assert (await MeetingInvitations(workflow).refresh())["notified"] == 0
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_owner_notification_is_not_replayed(workflow):
    service, calendar, state = await submitted(workflow)
    for attendee in calendar.event.return_value["attendees"]:
        attendee["responseStatus"] = "accepted"
    workflow.telegram.send_message.side_effect = None
    workflow.telegram.send_message.return_value = {"ok": False, "error_code": 500}
    poller = MeetingInvitations(workflow)
    assert (await poller.refresh())["errors"] == 1
    await poller.refresh()
    workflow.telegram.send_message.assert_awaited_once()
    assert service.state("ab12")["rsvp_notice_error"] is True


@pytest.mark.asyncio
async def test_definite_notification_rejection_can_retry_without_reinviting(workflow):
    service, calendar, state = await submitted(workflow)
    for attendee in calendar.event.return_value["attendees"]:
        attendee["responseStatus"] = "accepted"
    workflow.telegram.send_message.side_effect = None
    workflow.telegram.send_message.return_value = {"ok": False, "error_code": 429}
    poller = MeetingInvitations(workflow)
    assert (await poller.refresh())["errors"] == 1
    workflow.telegram.send_message.return_value = {"ok": True, "result": {"message_id": 700}}
    assert (await poller.refresh())["notified"] == 1
    await poller.refresh()
    assert workflow.telegram.send_message.await_count == 2
    calendar.invite.assert_awaited_once()


@pytest.mark.asyncio
async def test_read_failure_suppresses_stale_acceptance_and_recovers(workflow):
    from chronos.google_calendar import CalendarError
    from chronos.meeting_invitations import preview
    service, calendar, state = await submitted(workflow)
    calendar.event.side_effect = CalendarError("timeout")
    poller = MeetingInvitations(workflow)
    assert (await poller.refresh())["errors"] == 1
    assert "未能驗證" in preview(service.state("ab12"))
    workflow.telegram.send_message.assert_not_awaited()
    calendar.event.side_effect = None
    for attendee in calendar.event.return_value["attendees"]:
        attendee["responseStatus"] = "accepted"
    assert (await poller.refresh())["notified"] == 1
    assert service.state("ab12")["rsvp_read_unverified"] is False


@pytest.mark.asyncio
async def test_concurrent_pollers_deliver_one_owner_update(workflow):
    import asyncio
    service, calendar, state = await submitted(workflow)
    for attendee in calendar.event.return_value["attendees"]:
        attendee["responseStatus"] = "accepted"
    started, finish = asyncio.Event(), asyncio.Event()
    async def send(*args, **kwargs):
        started.set()
        await finish.wait()
        return {"ok": True, "result": {"message_id": 700}}
    workflow.telegram.send_message.side_effect = send
    poller = MeetingInvitations(workflow)
    first = asyncio.create_task(poller.refresh())
    await started.wait()
    await poller.refresh()
    finish.set()
    await first
    workflow.telegram.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_observed_provider_cancellation_is_not_a_new_external_write(workflow):
    service, calendar, state = await submitted(workflow)
    calendar.event.return_value = {"id": state["event_id"], "status": "cancelled"}
    assert (await MeetingInvitations(workflow).refresh())["notified"] == 1
    assert service.state("ab12")["status"] == "invite_cancelled"
    calendar.cancel_invitation.assert_not_awaited()
    calendar.invite.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{"ok": True}, {"ok": True, "result": None},
    {"ok": True, "result": {"message_id": True}}, {"ok": True, "result": {"message_id": -1}}, ["invalid"]])
async def test_malformed_telegram_success_is_not_a_delivered_owner_update(workflow, response):
    service, calendar, state = await submitted(workflow)
    for attendee in calendar.event.return_value["attendees"]:
        attendee["responseStatus"] = "accepted"
    workflow.telegram.send_message.side_effect = None
    workflow.telegram.send_message.return_value = response
    result = await MeetingInvitations(workflow).refresh()
    assert result["errors"] == 1 and result["notified"] == 0
    await MeetingInvitations(workflow).refresh()
    workflow.telegram.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_index_is_present_when_provider_is_invoked(workflow):
    from test_meeting_invitations import prepared
    from test_meeting_coordination import action
    service, calendar, state, text = await prepared(workflow)
    calendar.event.return_value = None
    def invite(body):
        assert workflow.get("meeting-invitations")["ab12"]["version"] == state["version"]
        assert service.state("ab12")["status"] == "inviting"
        return {**body, "organizer": {"self": True}, "etag": "v1"}
    calendar.invite.side_effect = invite
    await action(workflow, "確認會面邀請 " + state["invite_token"])
    calendar.invite.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_index_after_registration_blocks_provider_write(workflow, monkeypatch):
    from test_meeting_invitations import prepared
    from test_meeting_coordination import action
    service, calendar, state, text = await prepared(workflow)
    calendar.event.return_value = None
    original = MeetingInvitations.register
    def removed(self, identifier, selected):
        result = original(self, identifier, selected)
        self.mail.db.mutate_mail_state(self.mail.key("meeting-invitations"), lambda old: {})
        return result
    monkeypatch.setattr(MeetingInvitations, "register", removed)
    await action(workflow, "確認會面邀請 " + state["invite_token"])
    calendar.invite.assert_not_awaited()


@pytest.mark.asyncio
async def test_each_real_rsvp_transition_has_a_distinct_notice_revision(workflow):
    service, calendar, state = await submitted(workflow)
    poller = MeetingInvitations(workflow)
    for status in ("accepted", "declined", "accepted"):
        for attendee in calendar.event.return_value["attendees"]:
            attendee["responseStatus"] = status
        assert (await poller.refresh())["notified"] == 1
    assert workflow.telegram.send_message.await_count == 3
    assert service.state("ab12")["rsvp_revision"] == 3
    calendar.invite.assert_awaited_once()


@pytest.mark.asyncio
async def test_atomic_notice_claim_rejects_a_concurrent_state_change(workflow, monkeypatch):
    service, calendar, state = await submitted(workflow)
    for attendee in calendar.event.return_value["attendees"]:
        attendee["responseStatus"] = "accepted"
    original = MeetingInvitations.reconcile
    async def changed(self, identifier, previous):
        current, verified = await original(self, identifier, previous)
        self.mail.patch("meeting:" + identifier, status="invite_cancelled")
        return current, verified
    monkeypatch.setattr(MeetingInvitations, "reconcile", changed)
    assert (await MeetingInvitations(workflow).refresh())["notified"] == 0
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_cancel_ack_clears_an_older_read_failure(workflow):
    from test_meeting_coordination import action
    service, calendar, state = await submitted(workflow)
    workflow.patch("meeting:ab12", rsvp_read_unverified=True)
    await action(workflow, "準備取消會面邀請 " + state["version"])
    pending = service.state("ab12")
    await action(workflow, "確認取消會面邀請 " + pending["cancel_token"])
    assert service.state("ab12")["status"] == "invite_cancelled"
    assert service.state("ab12")["rsvp_read_unverified"] is False


@pytest.mark.asyncio
async def test_rsvp_polling_runs_after_mail_digest_is_already_complete(workflow):
    from test_meeting_coordination import NOW
    service, calendar, state = await submitted(workflow)
    workflow.gmail.message_ids.return_value = ([], False)
    await workflow.daily(NOW)
    workflow.telegram.send_message.reset_mock()
    for attendee in calendar.event.return_value["attendees"]:
        attendee["responseStatus"] = "accepted"
    result = await workflow.daily(NOW)
    assert result == {"already_complete": True}
    workflow.telegram.send_message.assert_awaited_once()
    assert service.state("ab12")["status"] == "invite_accepted"
    calendar.invite.assert_awaited_once()


@pytest.mark.asyncio
async def test_slow_calendar_read_is_bounded_and_does_not_block_owner_mail(workflow, monkeypatch):
    import asyncio
    import time
    from chronos import meeting_invitations
    service, calendar, state = await submitted(workflow)
    async def slow(*args):
        await asyncio.Event().wait()
    calendar.event.side_effect = slow
    monkeypatch.setattr(meeting_invitations, "RSVP_POLL_SECONDS", 0.02)
    start = time.monotonic()
    result = await MeetingInvitations(workflow).refresh()
    assert time.monotonic() - start < 1
    assert result["errors"] == 1
    assert service.state("ab12")["rsvp_read_unverified"] is True
    workflow.telegram.send_message.assert_not_awaited()
    calendar.invite.assert_awaited_once()


@pytest.mark.asyncio
async def test_profile_is_verified_once_per_polling_run(workflow):
    service, calendar, state = await submitted(workflow)
    clone = {**state, "version": "abcdef12", "event_id": "other-event"}
    workflow.patch("meeting:cd34", **clone)
    index = workflow.get("meeting-invitations")
    workflow.patch("meeting-invitations", **{**index, "cd34": {"version": clone["version"], "event_id": clone["event_id"]}})
    calendar.verify_owner.reset_mock()
    await MeetingInvitations(workflow).refresh()
    calendar.verify_owner.assert_awaited_once()


@pytest.mark.asyncio
async def test_profile_failure_stops_redundant_calls_for_the_same_account(workflow):
    from chronos.google_calendar import CalendarError
    service, calendar, state = await submitted(workflow)
    clone = {**state, "version": "abcdef12", "event_id": "other-event"}
    workflow.patch("meeting:cd34", **clone)
    workflow.patch("meeting-invitations", cd34={"version": clone["version"], "event_id": clone["event_id"]})
    calendar.verify_owner.reset_mock()
    calendar.verify_owner.side_effect = CalendarError("account unavailable")
    result = await MeetingInvitations(workflow).refresh()
    calendar.verify_owner.assert_awaited_once()
    assert result["errors"] == 1 and result["deferred"] == 1
    workflow.telegram.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_older_failed_read_cannot_override_newer_cancel_evidence(workflow):
    from chronos.google_calendar import CalendarError
    service, calendar, state = await submitted(workflow)
    async def stale(*args):
        workflow.patch("meeting:ab12", status="invite_cancelled", rsvp_revision=1, rsvp_read_unverified=False)
        raise CalendarError("older request failed")
    calendar.event.side_effect = stale
    await MeetingInvitations(workflow).refresh()
    assert service.state("ab12")["rsvp_read_unverified"] is False
    assert service.state("ab12")["status"] == "invite_cancelled"


@pytest.mark.asyncio
async def test_sent_notice_journal_repairs_interrupted_completion_without_replay(workflow):
    service, calendar, state = await submitted(workflow)
    for attendee in calendar.event.return_value["attendees"]:
        attendee["responseStatus"] = "accepted"
    poller = MeetingInvitations(workflow)
    await poller.refresh()
    workflow.patch("meeting:ab12", last_notified_revision=0)
    await poller.refresh()
    assert service.state("ab12")["last_notified_revision"] == 1
    workflow.telegram.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_deferred_polling_resumes_from_the_next_index_entry(workflow):
    from chronos.google_calendar import CalendarError
    service, calendar, state = await submitted(workflow)
    clone = {**state, "version": "abcdef12", "event_id": "other-event"}
    workflow.patch("meeting:cd34", **clone)
    workflow.patch("meeting-invitations", cd34={"version": clone["version"], "event_id": clone["event_id"]})
    calendar.verify_owner.side_effect = CalendarError("account unavailable")
    await MeetingInvitations(workflow).refresh()
    assert workflow.get("meeting-rsvp-health")["cursor"] == 1
    calendar.verify_owner.side_effect = None
    calendar.event.reset_mock()
    await MeetingInvitations(workflow).refresh()
    assert calendar.event.await_args_list[0].args[0] == "other-event"
