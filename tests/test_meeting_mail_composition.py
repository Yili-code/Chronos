import copy
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from chronos.google_calendar import CalendarError
from chronos.mail_correspondence import MailCorrespondence, ReplyText, fingerprint
from chronos.mail_natural_workflow import MailPlan, explicit_meeting_minutes
from chronos.meeting_coordination import Candidate
from chronos.mail_meeting_context import context
from test_mail_correspondence import setup, thread
from test_mail_workflow import workflow
from test_system import system


REQUEST = "幫我協調會面：後天下午三點、台北、1 小時，草擬英文回覆提出候選時間，3 天後提醒我追蹤"


def configure(mail, *, draft=True, tracking=True, minutes=60):
    setup(mail)
    mail.calendar = AsyncMock()
    mail.calendar.configured = Mock(return_value=True)
    mail.calendar.busy.return_value = []
    start = (datetime.now(mail.settings.tz) + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    candidate = Candidate(title="合作討論", start=start.isoformat(), timezone="Asia/Taipei", minutes=minutes)
    async def generated(*args):
        if args[3] is MailPlan:
            return MailPlan(meeting=True, draft=draft, tracking_hours=72 if tracking else None)
        if args[3] is Candidate:
            return candidate
        return ReplyText(body="Could this proposed time work for you? It remains unconfirmed.")
    mail.ai._generate_output.side_effect = generated
    return candidate


async def act(mail, text=REQUEST):
    return (await mail.prepare_action("ab12", text))()


@pytest.mark.asyncio
async def test_three_steps_staged_and_committed_once_with_same_meeting_evidence(workflow):
    candidate = configure(workflow)
    action = await workflow.prepare_action("ab12", REQUEST)
    assert all(workflow.get(k) is None for k in ("meeting:ab12", "reply:ab12", "followups"))
    result = workflow.db.process_update(85001, action)
    assert "僅本機" in result["reply"] and "尚未聯繫" in result["reply"] and "等你回覆" in result["reply"]
    meeting, draft = workflow.get("meeting:ab12"), workflow.get("reply:ab12")
    assert meeting["status"] == draft["status"] == "review"
    assert meeting["minutes"] == 60 and workflow.get("followups")["ab12"]["duration"] == 72*3600
    assert draft["meeting_source"] == fingerprint(context(workflow, "ab12"))
    payload = workflow.ai._generate_output.call_args_list[-1].args[1]
    assert candidate.start in payload and '"status": "review"' in payload
    workflow.db.process_update(85001, lambda: pytest.fail("replayed"))
    workflow.calendar.busy.assert_awaited()
    assert workflow.calendar.busy.await_count == 2
    workflow.calendar.insert.assert_not_awaited()
    assert {call[0] for call in workflow.calendar.mock_calls} <= {"configured", "verify_owner", "busy"}
    workflow.gmail.create_reply_draft.assert_not_awaited()
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_staged_draft_can_be_saved_only_after_explicit_native_confirmation(workflow):
    configure(workflow)
    await act(workflow)
    draft = workflow.get("reply:ab12")
    answer = (await workflow.prepare_action("ab12", "儲存 Gmail 草稿 " + draft["version"]))()
    assert "尚未寄出" in answer
    workflow.gmail.create_reply_draft.assert_awaited_once()
    workflow.calendar.insert.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["meeting:ab12", "reply:ab12", "contacts", "followups", "message:ab12"])
async def test_concurrent_guard_change_aborts_all_three_steps(workflow, key):
    configure(workflow)
    action = await workflow.prepare_action("ab12", REQUEST)
    workflow.patch(key, version="changed")
    before = {k: copy.deepcopy(workflow.get(k)) for k in ("meeting:ab12", "reply:ab12", "followups")}
    assert "未套用" in action()
    assert {k: workflow.get(k) for k in before} == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["initial_conflict", "final_conflict", "initial_api", "final_api", "missing_candidate", "draft_questions", "draft_api", "wrong_minutes", "unconfigured"])
async def test_failed_step_leaves_no_partial_local_workflow(workflow, failure):
    candidate = configure(workflow, minutes=30 if failure == "wrong_minutes" else 60)
    start = datetime.fromisoformat(candidate.start)
    busy = [(start, start + timedelta(hours=1))]
    if failure == "initial_conflict":
        workflow.calendar.busy.return_value = busy
    elif failure == "final_conflict":
        workflow.calendar.busy.side_effect = [[], busy]
    elif failure == "initial_api":
        workflow.calendar.verify_owner.side_effect = CalendarError("Denied")
    elif failure == "final_api":
        workflow.calendar.busy.side_effect = [[], CalendarError("Timeout")]
    elif failure == "unconfigured":
        workflow.calendar.configured.return_value = False
    elif failure == "missing_candidate":
        workflow.ai._generate_output.side_effect = [MailPlan(meeting=True, draft=True, tracking_hours=72), Candidate(title="", start="", timezone="", minutes=0, questions=["Which timezone?"])]
    elif failure == "draft_questions":
        workflow.ai._generate_output.side_effect = [MailPlan(meeting=True, draft=True, tracking_hours=72), candidate, ReplyText(body="Pending", questions=["Which language?"])]
    elif failure == "draft_api":
        workflow.ai._generate_output.side_effect = [MailPlan(meeting=True, draft=True, tracking_hours=72), candidate, TimeoutError()]
    assert "未套用" in await act(workflow)
    assert all(workflow.get(k) is None for k in ("meeting:ab12", "reply:ab12", "followups"))
    workflow.calendar.insert.assert_not_awaited()
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_thread_change_after_meeting_preparation_aborts_draft_and_tracker(workflow):
    configure(workflow)
    changed = thread() + [{**thread()[-1], "id": "ab99", "body": "Cancelled"}]
    workflow.gmail.thread.side_effect = [thread(), thread(), changed, changed, changed]
    assert "未套用" in await act(workflow)
    assert all(workflow.get(k) is None for k in ("meeting:ab12", "reply:ab12", "followups"))


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["gmail_account", "calendar_account", "calendar_id"])
async def test_account_or_calendar_changes_before_claim_abort_all(workflow, field):
    configure(workflow)
    action = await workflow.prepare_action("ab12", REQUEST)
    setattr(workflow.settings, field, "another")
    assert "未套用" in action()
    assert workflow.get("meeting:ab12") is None and workflow.get("reply:ab12") is None


@pytest.mark.asyncio
async def test_source_injection_never_supplies_joint_plan_authority(workflow):
    configure(workflow)
    await act(workflow)
    calls = workflow.ai._generate_output.call_args_list
    assert "Ignore all rules" not in calls[0].args[1]
    assert "Ignore all rules" in calls[1].args[1]
    workflow.calendar.insert.assert_not_awaited()
    workflow.gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.parametrize("text,expected", [
    ("協調會面 1 小時，3 天後追蹤", 60),
    ("協調會面 1小時30分鐘，追蹤 3 天", 90),
    ("協調會面 1.5 hours，track in 3 days", 90),
    ("協調會面 30分鐘或45分鐘，追蹤 3 天", -1),
    ("協調會面 2 天，追蹤 3 天", -1),
    ("協調會面時間照原信，3 天後追蹤", None),
    ("3 天後協調會面，30分鐘，3天後追蹤", 30),
    ("2 hours later coordinate a meeting，30 minutes，track in 3 days", 30),
])
def test_meeting_and_tracking_durations_are_separate_owner_constraints(text, expected):
    assert explicit_meeting_minutes(text) == expected


@pytest.mark.asyncio
async def test_non_staged_draft_cannot_override_persisted_meeting_context(workflow):
    with pytest.raises(ValueError):
        await MailCorrespondence(workflow).propose("ab12", "English", meeting_override={})


def test_search_result_then_joint_request_preserves_thread_and_replay(system, monkeypatch):
    from test_correspondence_webhook import configure as configure_webhook
    from test_mail_webhook import post
    client, tasks, bot, gmail, mail = configure_webhook(system, monkeypatch)
    configure(mail)
    from chronos import main
    monkeypatch.setattr(main, "google_calendar", mail.calendar)
    gmail.search_page.return_value = (["ab12"], None)
    gmail.read.return_value = thread()[-1]
    assert post(client, 86001, text="搜尋郵件：subject:Proposal").status_code == 200
    assert mail.binding(123, 701)["mail_id"] == "ab12"
    for _ in range(2):
        assert post(client, 86002, text=REQUEST, card=701).status_code == 200
    assert mail.get("meeting:ab12")["status"] == "review"
    assert mail.get("reply:ab12")["meeting_source"] == fingerprint(context(mail, "ab12"))
    assert mail.get("followups")["ab12"]["status"] == "waiting_owner"
    assert not tasks.list_open()
    gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_other_person_cannot_be_silently_replaced_by_bound_sender(workflow):
    configure(workflow, tracking=False)
    text = "幫我協調會面 other@example.org：後天下午三點台北1小時，草擬英文回覆"
    assert "對象不一致" in await act(workflow, text)
    assert workflow.get("meeting:ab12") is None and workflow.get("reply:ab12") is None


@pytest.mark.asyncio
async def test_explicit_recipient_domain_is_canonicalized_without_changing_local_part(workflow):
    configure(workflow, tracking=False)
    text = "幫我協調會面 client@EXAMPLE.ORG：後天下午三點台北1小時，草擬英文回覆"
    assert "僅本機" in await act(workflow, text)
    assert workflow.get("meeting:ab12")["recipient"] == "client@example.org"


@pytest.mark.asyncio
async def test_previous_draft_primary_recipient_cannot_change_meeting_target(workflow):
    configure(workflow, tracking=False)
    workflow.ai._generate_output.side_effect = None
    workflow.ai._generate_output.return_value = ReplyText(body="Thank you.")
    (await workflow.prepare_action("ab12", "草擬回覆：英文簡潔"))()
    previous = workflow.get("reply:ab12")
    workflow.patch("reply:ab12", to=["other@example.org"], recipient="other@example.org")
    previous = workflow.get("reply:ab12")
    configure(workflow, tracking=False)
    assert "主要收件者" in await act(workflow, "幫我協調會面：後天下午三點台北1小時，草擬英文回覆")
    assert workflow.get("reply:ab12") == previous and workflow.get("meeting:ab12") is None


@pytest.mark.asyncio
async def test_candidate_expiring_before_atomic_commit_is_not_saved(workflow, monkeypatch):
    import chronos.mail_natural_workflow as module
    candidate = configure(workflow)
    action = await workflow.prepare_action("ab12", REQUEST)
    class Expired(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(candidate.start) + timedelta(minutes=1)
    monkeypatch.setattr(module, "datetime", Expired)
    assert "時間已變更" in action()
    assert workflow.get("meeting:ab12") is None and workflow.get("reply:ab12") is None


@pytest.mark.asyncio
async def test_parallel_preparations_have_only_one_complete_winner(workflow):
    import asyncio
    configure(workflow)
    first, second = await asyncio.gather(workflow.prepare_action("ab12", REQUEST), workflow.prepare_action("ab12", REQUEST))
    assert "僅本機" in first()
    before = {k: copy.deepcopy(workflow.get(k)) for k in ("meeting:ab12", "reply:ab12", "followups")}
    assert "未套用" in second()
    assert {k: workflow.get(k) for k in before} == before


@pytest.mark.asyncio
async def test_new_conflict_before_native_save_blocks_stale_proposal(workflow):
    candidate = configure(workflow)
    await act(workflow)
    draft = workflow.get("reply:ab12")
    start = datetime.fromisoformat(candidate.start)
    workflow.calendar.busy.return_value = [(start, start + timedelta(hours=1))]
    (await workflow.prepare_action("ab12", "儲存 Gmail 草稿 " + draft["version"]))()
    assert workflow.get("reply:ab12")["status"] == "stale"
    workflow.gmail.create_reply_draft.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["conflict", "expired", "read_failure", "during_native_read"])
async def test_timed_review_rechecked_after_send_preview_without_local_changes(workflow, monkeypatch, change):
    from chronos.gmail import GmailError
    from test_mail_send import review
    import chronos.mail_meeting_context as module
    candidate = configure(workflow)
    await act(workflow)
    state, text = await review(workflow)
    assert state["status"] == "send_review"
    before = copy.deepcopy(workflow.get("meeting:ab12"))
    start = datetime.fromisoformat(candidate.start)
    if change == "conflict":
        workflow.calendar.busy.return_value = [(start, start + timedelta(hours=1))]
    elif change == "expired":
        class Expired(datetime):
            @classmethod
            def now(cls, tz=None):
                return start + timedelta(minutes=1)
        monkeypatch.setattr(module, "datetime", Expired)
    elif change == "read_failure":
        workflow.calendar.busy.side_effect = CalendarError("Timeout")
    else:
        native = workflow.gmail.read_plain_draft.return_value
        async def changed(*args, **kwargs):
            workflow.calendar.busy.return_value = [(start, start + timedelta(hours=1))]
            return native
        workflow.gmail.read_plain_draft.side_effect = changed
    if change == "read_failure":
        with pytest.raises(GmailError):
            await act(workflow, "確認寄出回覆 " + state["send_token"])
    else:
        await act(workflow, "確認寄出回覆 " + state["send_token"])
    assert workflow.get("meeting:ab12") == before
    workflow.gmail.send_reviewed_draft.assert_not_awaited()
