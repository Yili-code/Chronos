import pytest
from pydantic import ValidationError

from chronos import main
from chronos.mail_correspondence import ReplyText
from chronos.meeting_coordination import Candidate
from test_mail_correspondence import setup, act
from test_mail_workflow import workflow
from test_meeting_coordination import configure as meeting_setup, NOW
from test_correspondence_webhook import configure
from test_mail_webhook import post
from test_system import system


@pytest.mark.asyncio
async def test_draft_answer_preserves_original_constraints_without_creating_task(workflow):
    setup(workflow)
    workflow.ai._generate_output.return_value = ReplyText(body="Need details", questions=["哪一天？"])
    await act(workflow, "草擬回覆：正式英文，確認日期前不要承諾")
    previous = workflow.get("reply:ab12")
    workflow.ai._generate_output.return_value = ReplyText(body="Thank you; Friday is the proposed date.")
    action = await workflow.prepare_action("ab12", "週五", context_kind="reply", context_version=previous["version"])
    assert "本機草稿" in action()["messages"][0]["text"]
    current = workflow.get("reply:ab12")
    assert "正式英文" in current["owner_request"] and "週五" in current["owner_request"]
    assert current["version"] != previous["version"] and current["status"] == "review"
    assert not workflow.db.list_open_tasks()
    workflow.gmail.create_reply_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_question_answer_never_overwrites_newer_draft(workflow):
    setup(workflow)
    workflow.ai._generate_output.return_value = ReplyText(body="Need details", questions=["哪一天？"])
    await act(workflow, "草擬回覆：正式英文")
    previous = workflow.get("reply:ab12")
    await act(workflow, "補充回覆草稿：週五")
    current = workflow.get("reply:ab12")
    workflow.ai._generate_output.reset_mock()
    reply = (await workflow.prepare_action("ab12", "週六", context_kind="reply", context_version=previous["version"]))()
    assert "過期" in reply
    assert workflow.get("reply:ab12") == current
    workflow.ai._generate_output.assert_not_awaited()


@pytest.mark.asyncio
async def test_meeting_answer_preserves_timezone_and_owner_constraints(workflow):
    service, calendar = meeting_setup(workflow)
    workflow.ai._generate_output.return_value = Candidate(title="", start="", timezone="", minutes=0, questions=["幾點？"])
    (await workflow.prepare_action("ab12", "協調會面：明天，台北，30 分鐘", NOW))()
    previous = service.state("ab12")
    assert previous["status"] == "needs_information"
    workflow.ai._generate_output.return_value = Candidate(title="合作討論", start="2026-11-04T15:00:00+08:00", timezone="Asia/Taipei", minutes=30)
    reply = (await workflow.prepare_action("ab12", "下午三點", NOW, context_kind="meeting", context_version=previous["version"]))()
    assert "尚未聯繫" in reply
    assert "台北" in service.state("ab12")["owner_request"] and "下午三點" in service.state("ab12")["owner_request"]
    calendar.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_two_pending_domains_require_explicit_disambiguation(workflow):
    setup(workflow)
    workflow.patch("reply:ab12", status="needs_information", version="a", owner_request="Draft request")
    workflow.patch("meeting:ab12", status="needs_information", version="b", owner_request="Meeting request")
    assert "同時有" in await act(workflow, "明天")
    workflow.ai._generate_output.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_question_does_not_swallow_explicit_mail_action(workflow):
    setup(workflow)
    workflow.patch("reply:ab12", status="needs_information", version="a", owner_request="Draft request")
    assert "已保留" in await act(workflow, "保留")
    assert workflow.get("message:ab12")["keep"] is True
    workflow.ai._generate_output.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_cancel_is_version_guarded_and_local_only(workflow):
    setup(workflow)
    workflow.patch("reply:ab12", status="needs_information", version="a", owner_request="Draft request")
    cancel = await workflow.prepare_action("ab12", "取消")
    workflow.patch("reply:ab12", status="review", version="b")
    assert "未執行" in cancel()
    assert workflow.get("reply:ab12")["version"] == "b"
    workflow.gmail.create_reply_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_cumulative_request_limit_does_not_silently_drop_constraints(workflow):
    setup(workflow)
    workflow.patch("reply:ab12", status="needs_information", version="a", owner_request="x" * 3995)
    assert "4,000" in await act(workflow, "More constraints")
    workflow.ai._generate_output.assert_not_awaited()
    assert workflow.get("reply:ab12")["owner_request"] == "x" * 3995


def test_preview_budget_accounts_for_supplementary_unicode():
    with pytest.raises(ValidationError):
        ReplyText(body="🙂" * 1500)
    assert ReplyText(body="a" * 2800).body == "a" * 2800


def test_force_reply_and_prompt_version_preserve_actual_webhook_context(system, monkeypatch):
    client, tasks, bot, gmail, mail = configure(system, monkeypatch)
    main.ai._generate_output.return_value = ReplyText(body="Need details", questions=["哪一天？"])
    assert post(client, 26001, text="草擬回覆：正式英文，等確認日期").status_code == 200
    binding = mail.binding(123, 700)
    assert binding["context_kind"] == "reply"
    assert bot.send_message.call_args.kwargs["reply_markup"]["force_reply"] is True
    main.ai._generate_output.return_value = ReplyText(body="Friday remains a proposal.")
    assert post(client, 26002, text="週五", card=700).status_code == 200
    assert mail.get("reply:ab12")["status"] == "review"
    assert "正式英文" in main.ai._generate_output.call_args.args[1]
    main.ai._generate_output.reset_mock()
    assert post(client, 26003, text="週六", card=700).status_code == 200
    assert "過期" in bot.send_message.call_args.args[1]
    main.ai._generate_output.assert_not_awaited()
    assert not tasks.list_open()
    gmail.send_reviewed_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_question_receipt_persists_exact_originating_version_on_both_stores(workflow):
    setup(workflow)
    workflow.ai._generate_output.return_value = ReplyText(body="Need details", questions=["哪一天？"])
    action = await workflow.prepare_action("ab12", "草擬回覆：正式英文")
    receipt = workflow.db.process_update(27001, action)
    version = workflow.get("reply:ab12")["version"]
    assert receipt["messages"][0]["mail_context"] == {"kind": "reply", "version": version}
    workflow.patch("reply:ab12", version="newer")
    def must_not_run():
        raise AssertionError("A persisted receipt must not run again")
    retried = workflow.db.process_update(27001, must_not_run)
    assert retried["messages"][0]["mail_context"]["version"] == version


def test_failed_question_delivery_retry_never_adopts_another_questions_version(system, monkeypatch):
    client, tasks, bot, gmail, mail = configure(system, monkeypatch)
    main.ai._generate_output.return_value = ReplyText(body="Need details", questions=["哪一天？"])
    bot.send_message.side_effect = [
        {"ok": False, "error_code": 400},
        {"ok": True, "result": {"message_id": 701}},
        {"ok": True, "result": {"message_id": 702}},
        {"ok": True, "result": {"message_id": 703}},
    ]
    assert post(client, 27101, text="草擬回覆：正式英文").status_code == 502
    old_version = mail.get("reply:ab12")["version"]
    assert post(client, 27102, text="草擬回覆：友善繁體中文").status_code == 200
    current = mail.get("reply:ab12")
    assert current["version"] != old_version
    assert post(client, 27101, text="草擬回覆：正式英文").status_code == 200
    assert "過期" in bot.send_message.call_args.args[1]
    assert mail.binding(123, 702)["context_version"] == old_version
    assert post(client, 27103, text="週五", card=702).status_code == 200
    assert mail.get("reply:ab12") == current
    assert main.ai._generate_output.await_count == 2
    receipt = main.db.get_update(27101)
    assert receipt["delivered"] and receipt["delivered_count"] == 1
    assert not tasks.list_open()
