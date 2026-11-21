import pytest

from test_mail_send import review, saved
from test_mail_correspondence import act, setup, propose
from test_mail_workflow import workflow


@pytest.mark.asyncio
async def test_new_cancellation_after_send_preview_blocks_mail_submission(workflow):
    state, text = await review(workflow)
    workflow.patch("meeting:ab12", status="invite_cancelled", version="12345678", event_id="old-event")
    await act(workflow, "確認寄出回覆 " + state["send_token"])
    workflow.gmail.send_reviewed_draft.assert_not_awaited()
    assert workflow.get("reply:ab12")["status"] == "gmail_draft"


@pytest.mark.asyncio
async def test_meeting_cancelled_after_drafting_blocks_native_draft_creation(workflow):
    setup(workflow)
    state = await propose(workflow)
    workflow.patch("meeting:ab12", status="cancelled", version="12345678")
    await act(workflow, "儲存 Gmail 草稿 " + state["version"])
    workflow.gmail.create_reply_draft.assert_not_awaited()
    assert workflow.get("reply:ab12")["status"] == "stale"


@pytest.mark.asyncio
async def test_meaningful_meeting_change_while_ai_runs_cannot_persist_old_draft(workflow):
    from chronos.mail_correspondence import ReplyText
    setup(workflow)
    async def generated(*args):
        workflow.patch("meeting:ab12", status="cancelled", version="12345678")
        return ReplyText(body="Old meeting proposal")
    workflow.ai._generate_output.side_effect = generated
    await act(workflow, "草擬回覆：不承諾，確認細節")
    assert not workflow.get("reply:ab12")
    workflow.gmail.create_reply_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_meeting_grounding_omits_internal_confirmation_codes(workflow):
    import json
    setup(workflow)
    workflow.patch("meeting:ab12", status="review", version="12345678", invite_token="87654321", title="Meeting")
    await propose(workflow)
    data = json.loads(workflow.ai._generate_output.call_args.args[1])
    assert data["related_meeting"] == {"status": "review", "title": "Meeting"}
    assert "87654321" not in workflow.ai._generate_output.call_args.args[1]


@pytest.mark.asyncio
async def test_polling_bookkeeping_does_not_invalidate_reviewed_draft(workflow):
    workflow.patch("meeting:ab12", status="review", version="12345678", title="Proposal")
    state, text = await review(workflow)
    workflow.patch("meeting:ab12", rsvp_checked_at=1234, notice_claim="new", rsvp_notice_error=True)
    await act(workflow, "確認寄出回覆 " + state["send_token"])
    workflow.gmail.send_reviewed_draft.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"status": "cancelled"}, {"attendees": []}, {"start": {"dateTime": "2026-11-04T18:00:00+08:00"}},
    {"attendeesOmitted": True}, {"recurrence": ["RRULE:FREQ=WEEKLY"]}])
async def test_calendar_changes_without_mail_or_local_state_changes_block_send(workflow, change):
    from test_meeting_invitations import submitted
    service, calendar, meeting = await submitted(workflow)
    state = await saved(workflow)
    await act(workflow, "準備寄出回覆草稿 " + state["version"])
    state = workflow.get("reply:ab12")
    assert state["status"] == "send_review"
    calendar.event.return_value = {**calendar.event.return_value, **change}
    await act(workflow, "確認寄出回覆 " + state["send_token"])
    workflow.gmail.send_reviewed_draft.assert_not_awaited()
    assert workflow.get("reply:ab12")["status"] == "gmail_draft"


@pytest.mark.asyncio
async def test_calendar_read_failure_keeps_mail_unsent(workflow):
    from chronos.gmail import GmailError
    from chronos.google_calendar import CalendarError
    from test_meeting_invitations import submitted
    service, calendar, meeting = await submitted(workflow)
    state = await saved(workflow)
    calendar.event.side_effect = CalendarError("timeout")
    with pytest.raises(GmailError, match="Calendar state"):
        await act(workflow, "準備寄出回覆草稿 " + state["version"])
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_calendar_change_during_native_draft_read_is_rechecked(workflow):
    from test_meeting_invitations import submitted
    service, calendar, meeting = await submitted(workflow)
    state = await saved(workflow)
    await act(workflow, "準備寄出回覆草稿 " + state["version"])
    state = workflow.get("reply:ab12")
    native = workflow.gmail.read_plain_draft.return_value
    async def changed(*args, **kwargs):
        calendar.event.return_value = {**calendar.event.return_value, "status": "cancelled"}
        return native
    workflow.gmail.read_plain_draft.side_effect = changed
    await act(workflow, "確認寄出回覆 " + state["send_token"])
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_native_draft_cannot_bypass_related_meeting_binding(workflow):
    state = await saved(workflow)
    workflow.db.mutate_mail_state(workflow.key("reply:ab12"), lambda old: {k: v for k, v in old.items() if k != "meeting_source"})
    workflow.patch("meeting:ab12", status="review", version="12345678")
    await act(workflow, "準備寄出回覆草稿 " + state["version"])
    assert workflow.get("reply:ab12")["status"] == "gmail_draft"
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_meeting_change_at_atomic_send_claim_blocks_submission(workflow, monkeypatch):
    state, text = await review(workflow)
    original = workflow.db.mutate_mail_state
    changed = False
    def mutate(key, transition):
        nonlocal changed
        if key == workflow.key("reply:ab12") and not changed:
            changed = True
            workflow.patch("meeting:ab12", status="cancelled", version="12345678")
        return original(key, transition)
    monkeypatch.setattr(workflow.db, "mutate_mail_state", mutate)
    await act(workflow, "確認寄出回覆 " + state["send_token"])
    workflow.gmail.send_reviewed_draft.assert_not_awaited()
    assert workflow.get("reply:ab12")["status"] == "gmail_draft"
