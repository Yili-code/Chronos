import asyncio
from copy import deepcopy

import pytest

from chronos.google_calendar import CalendarError
from chronos.meeting_rescheduling import MeetingRescheduling
from test_meeting_coordination import action
from test_meeting_invitations import submitted
from test_meeting_hold_conversion import held
from test_mail_workflow import workflow
from test_system import system


REQUEST = "：2026-11-06T15:00:00+08:00；時區：Asia/Taipei；分鐘：45"


async def ready(workflow, private=False):
    if private:
        service, calendar, state, event = await held(workflow)
    else:
        service, calendar, state = await submitted(workflow)
    calendar.agenda.return_value = []
    original = deepcopy(calendar.event.return_value)
    def update(identifier, body, etag):
        event = {**deepcopy(original), **deepcopy(body), "etag": "v2"}
        calendar.event.return_value = event
        return event
    calendar.invite_existing.side_effect = update
    calendar.move_event.side_effect = update
    text = await action(workflow, "準備會面改期 " + state["version"] + REQUEST)
    return service, calendar, service.state("ab12"), text, original


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [False, True])
async def test_review_displays_old_and_new_without_mutating_provider(workflow, private):
    service, calendar, state, text, old = await ready(workflow, private)
    assert "原時間" in text and "新時間" in text and "2026-11-06" in text
    assert state["start"] == old["start"]["dateTime"]
    calendar.invite_existing.assert_not_awaited()
    calendar.move_event.assert_not_awaited()
    assert state["reschedule"]["status"] == "review"


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [False, True])
async def test_confirmation_updates_same_event_with_conditional_version(workflow, private):
    service, calendar, state, text, old = await ready(workflow, private)
    text = await action(workflow, "確認會面改期 " + state["reschedule"]["token"])
    current = service.state("ab12")
    assert current["reschedule"]["status"] == "completed"
    assert current["start"] == "2026-11-06T15:00:00+08:00" and current["minutes"] == 45
    assert current["event_id"] == old["id"]
    called = calendar.move_event if private else calendar.invite_existing
    args = called.call_args.args
    assert args[0] == old["id"] and args[2] == old["etag"]
    assert args[1]["extendedProperties"]["private"]["chronosReschedule"] == state["reschedule"]["token"]
    if not private:
        assert current["status"] == "invite_pending" and "尚未確認新時間" in text
        assert all(a["responseStatus"] == "needsAction" for a in args[1]["attendees"])
    calendar.delete_event.assert_not_awaited()
    calendar.cancel_invitation.assert_not_awaited()
    await action(workflow, "確認會面改期 " + state["reschedule"]["token"])
    called.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_review_keeps_original_invitation(workflow):
    service, calendar, state, text, old = await ready(workflow)
    await action(workflow, "取消會面改期準備 " + state["reschedule"]["token"])
    assert service.state("ab12")["start"] == state["start"]
    assert service.state("ab12")["reschedule"]["status"] == "cancelled"
    calendar.invite_existing.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("etag", "edited"), ("status", "cancelled"), ("recurrence", ["RRULE:FREQ=DAILY"]), ("attendees", [])])
async def test_external_event_changes_after_review_block_update(workflow, field, value):
    service, calendar, state, text, old = await ready(workflow)
    calendar.event.return_value = {**old, field: value}
    await action(workflow, "確認會面改期 " + state["reschedule"]["token"])
    calendar.invite_existing.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_busy_event_after_review_blocks_update(workflow):
    service, calendar, state, text, old = await ready(workflow)
    calendar.agenda.return_value = [{"id": "busy", "_calendar_id": "other", "start": {"dateTime": "2026-11-06T15:10:00+08:00"}, "end": {"dateTime": "2026-11-06T16:00:00+08:00"}}]
    await action(workflow, "確認會面改期 " + state["reschedule"]["token"])
    calendar.invite_existing.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_event_on_other_calendar_still_counts_as_busy(workflow):
    service, calendar, state = await submitted(workflow)
    old = deepcopy(calendar.event.return_value)
    calendar.agenda.return_value = [{**old, "_calendar_id": "other"}]
    await action(workflow, "準備會面改期 " + state["version"] + "：2026-11-04T15:10:00+08:00；時區：Asia/Taipei；分鐘：45")
    assert not service.state("ab12").get("reschedule")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403, 412, 429, None, 500])
async def test_rejected_or_unknown_updates_never_replay(workflow, status):
    service, calendar, state, text, old = await ready(workflow)
    calendar.invite_existing.side_effect = CalendarError("provider failure", status)
    command = "確認會面改期 " + state["reschedule"]["token"]
    await action(workflow, command)
    expected = "rejected" if status in {400, 403, 412, 429} else "uncertain"
    assert service.state("ab12")["reschedule"]["status"] == expected
    assert service.state("ab12")["start"] == state["start"]
    await action(workflow, command)
    calendar.invite_existing.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_update_can_reconcile_without_resubmission(workflow):
    service, calendar, state, text, old = await ready(workflow)
    saved = {}
    def uncertain(identifier, body, etag):
        saved.update({**deepcopy(old), **deepcopy(body), "etag": "new"})
        raise CalendarError("timeout")
    calendar.invite_existing.side_effect = uncertain
    await action(workflow, "確認會面改期 " + state["reschedule"]["token"])
    calendar.event.return_value = saved
    await action(workflow, "查看會面改期")
    assert service.state("ab12")["reschedule"]["status"] == "completed"
    calendar.invite_existing.assert_awaited_once()


@pytest.mark.asyncio
async def test_old_acceptances_cannot_confirm_new_time(workflow):
    service, calendar, state, text, old = await ready(workflow)
    def preserved(identifier, body, etag):
        result = {**deepcopy(old), **deepcopy(body), "etag": "new"}
        for guest in result["attendees"]:
            guest["responseStatus"] = "accepted"
        return result
    calendar.invite_existing.side_effect = preserved
    await action(workflow, "確認會面改期 " + state["reschedule"]["token"])
    assert service.state("ab12")["reschedule"]["status"] == "unverified"
    assert service.state("ab12")["start"] == old["start"]["dateTime"]


@pytest.mark.asyncio
async def test_pending_review_blocks_competing_cancellation_and_reproposal(workflow):
    service, calendar, state, text, old = await ready(workflow)
    await action(workflow, "準備取消會面邀請 " + state["version"])
    await action(workflow, "協調會面：明天下午四點，台北，30 分鐘")
    assert service.state("ab12")["reschedule"]["token"] == state["reschedule"]["token"]
    assert service.state("ab12")["status"] == state["status"]
    calendar.cancel_invitation.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_review_is_not_submitted(workflow):
    service, calendar, state, text, old = await ready(workflow)
    workflow.patch("meeting:ab12", reschedule={**state["reschedule"], "expires": 0})
    await action(workflow, "確認會面改期 " + state["reschedule"]["token"])
    calendar.invite_existing.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_confirmations_apply_once(workflow):
    service, calendar, state, text, old = await ready(workflow)
    started, finish = asyncio.Event(), asyncio.Event()
    original = calendar.invite_existing.side_effect
    async def applying(*args):
        started.set()
        await finish.wait()
        return original(*args)
    calendar.invite_existing.side_effect = applying
    command = "確認會面改期 " + state["reschedule"]["token"]
    first = asyncio.create_task(action(workflow, command))
    await started.wait()
    await action(workflow, command)
    finish.set()
    await first
    calendar.invite_existing.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_reschedule_is_recovered_by_rsvp_poll_without_replay(workflow):
    service, calendar, state, text, old = await ready(workflow)
    saved = {}
    def uncertain(identifier, body, etag):
        saved.update({**deepcopy(old), **deepcopy(body), "etag": "new"})
        raise CalendarError("timeout")
    calendar.invite_existing.side_effect = uncertain
    await action(workflow, "確認會面改期 " + state["reschedule"]["token"])
    calendar.event.return_value = saved
    from chronos.meeting_invitations import MeetingInvitations
    result = await MeetingInvitations(workflow).refresh()
    assert result["notified"] == 1
    assert service.state("ab12")["reschedule"]["status"] == "completed"
    assert (await MeetingInvitations(workflow).refresh())["notified"] == 0
    calendar.invite_existing.assert_awaited_once()


@pytest.mark.asyncio
async def test_old_mail_draft_cannot_send_during_reschedule_review(workflow):
    from test_mail_send import saved
    service, calendar, meeting = await submitted(workflow)
    draft = await saved(workflow)
    calendar.agenda.return_value = []
    await action(workflow, "準備會面改期 " + meeting["version"] + REQUEST)
    await action(workflow, "準備寄出回覆草稿 " + draft["version"])
    workflow.gmail.send_reviewed_draft.assert_not_awaited()
    assert workflow.get("reply:ab12")["status"] == "gmail_draft"


@pytest.mark.asyncio
async def test_rescheduled_private_hold_can_convert_to_invitation(workflow):
    service, calendar, state, text, old = await ready(workflow, True)
    await action(workflow, "確認會面改期 " + state["reschedule"]["token"])
    current = service.state("ab12")
    calendar.agenda.return_value = [{**deepcopy(calendar.event.return_value), "_calendar_id": workflow.settings.calendar_id}]
    await action(workflow, "準備會面邀請 " + current["version"] + "：client@example.org；地點：台北")
    reviewed = service.state("ab12")
    assert reviewed["status"] == "invite_review"
    await action(workflow, "確認會面邀請 " + reviewed["invite_token"])
    assert service.state("ab12")["status"] == "invite_pending"
    assert service.state("ab12")["start"] == "2026-11-06T15:00:00+08:00"


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [
    "：2026-11-06T15:00:00；時區：Asia/Taipei；分鐘：30",
    "：2026-11-06T15:00:00+00:00；時區：Asia/Taipei；分鐘：30",
    "：2026-11-06T15:00:00+08:00；時區：Missing/Zone；分鐘：30",
    "：2026-11-06T15:00:00+08:00；時區：Asia/Taipei；分鐘：0",
    "：2027-03-14T02:30:00-05:00；時區：America/New_York；分鐘：30",
])
async def test_incomplete_or_invalid_time_never_prepares_reschedule(workflow, suffix):
    service, calendar, state = await submitted(workflow)
    await action(workflow, "準備會面改期 " + state["version"] + suffix)
    assert not service.state("ab12").get("reschedule")
    calendar.invite_existing.assert_not_awaited()


@pytest.mark.asyncio
async def test_duration_crossing_dst_uses_elapsed_minutes_and_correct_end_offset(workflow):
    service, calendar, state = await submitted(workflow)
    calendar.agenda.return_value = []
    await action(workflow, "準備會面改期 " + state["version"] + "：2027-03-14T01:30:00-05:00；時區：America/New_York；分鐘：120")
    change = service.state("ab12")["reschedule"]
    assert change["end"] == "2027-03-14T04:30:00-04:00"


@pytest.mark.asyncio
async def test_preference_revocation_in_claim_stops_update(workflow, monkeypatch):
    service, calendar, state, text, old = await ready(workflow)
    import chronos.mail_recipients as recipients
    original = workflow.db.mutate_mail_state
    def mutate(key, transition):
        if key == workflow.key("meeting:ab12"):
            monkeypatch.setattr(recipients, "preferences_allow", lambda *args: False)
        return original(key, transition)
    monkeypatch.setattr(workflow.db, "mutate_mail_state", mutate)
    await action(workflow, "確認會面改期 " + state["reschedule"]["token"])
    calendar.invite_existing.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_thread_message_invalidates_reschedule_confirmation(workflow):
    service, calendar, state, text, old = await ready(workflow)
    messages = workflow.gmail.thread.return_value
    workflow.gmail.thread.return_value = messages + [{**messages[-1], "id": "newer", "received_at": 8, "body": "Cancel"}]
    await action(workflow, "確認會面改期 " + state["reschedule"]["token"])
    calendar.invite_existing.assert_not_awaited()


def test_webhook_reschedule_confirmation_replay_updates_one_private_event(system, monkeypatch):
    from datetime import datetime, timedelta
    from test_secretary_integration import integrated
    from test_mail_webhook import post
    from chronos import main
    client, tasks, bot, gmail, mail, calendar, events = integrated(system, monkeypatch)
    assert post(client, 31001, text="協調會面：後天下午三點，台北，30 分鐘").status_code == 200
    state = mail.get("meeting:ab12")
    assert post(client, 31002, text="暫留會面時段 " + state["version"], card=700).status_code == 200
    events[state["event_id"]]["organizer"] = {"self": True}
    calendar.agenda.return_value = []
    when = (datetime.now(main.settings.tz) + timedelta(days=3)).replace(hour=16, minute=0, second=0, microsecond=0)
    command = "準備會面改期 " + state["version"] + "：" + when.isoformat() + "；時區：Asia/Taipei；分鐘：30"
    assert post(client, 31003, text=command, card=700).status_code == 200
    reviewed = mail.get("meeting:ab12")
    assert reviewed["reschedule"]["status"] == "review"
    def update(identifier, body, etag):
        events[identifier] = {**events[identifier], **body, "etag": "v2"}
        return events[identifier]
    calendar.move_event.side_effect = update
    command = "確認會面改期 " + reviewed["reschedule"]["token"]
    assert post(client, 31004, text=command, card=700).status_code == 200
    assert post(client, 31004, text=command, card=700).status_code == 200
    assert mail.get("meeting:ab12")["reschedule"]["status"] == "completed"
    calendar.move_event.assert_awaited_once()
    assert len(events) == 1 and not tasks.list_open()
    gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_expiry_during_remote_checks_is_rechecked_in_atomic_claim(workflow, monkeypatch):
    from types import SimpleNamespace
    import chronos.meeting_rescheduling as module
    service, calendar, state, text, old = await ready(workflow)
    original = workflow.db.mutate_mail_state
    def mutate(key, transition):
        if key == workflow.key("meeting:ab12"):
            monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: state["reschedule"]["expires"] + 1))
        return original(key, transition)
    monkeypatch.setattr(workflow.db, "mutate_mail_state", mutate)
    await action(workflow, "確認會面改期 " + state["reschedule"]["token"])
    calendar.invite_existing.assert_not_awaited()
