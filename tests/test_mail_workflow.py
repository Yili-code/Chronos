import asyncio
import copy
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chronos.db import Database
from chronos.gmail import GmailError
from chronos.mail_workflow import MailWorkflow, MailSummary, filter_reason
from chronos.settings import Settings
from chronos.tasks import ParsedTask
from chronos.telegram import TelegramError


def message(identifier="ab12", **changes):
    return {"id": identifier, "subject": "Summer sale 20% off", "sender": "Shop <sale@shop.example>",
            "labels": ["UNREAD", "CATEGORY_PROMOTIONS"], "unsubscribe": True, "has_reply": False,
            "body": "Get your coupon now", "snippet": "20% off", "date": "2026-10-07", **changes}


@pytest.mark.parametrize("changes", [
    {"subject": "Payment receipt with discount"}, {"body": "Security verification"},
    {"body": "你的訂單與優惠"}, {"body": "作業報告"}, {"has_reply": True},
    {"unsubscribe": False}, {"body_truncated": True},
    {"labels": ["UNREAD", "CATEGORY_PROMOTIONS", "STARRED"]},
    {"labels": ["UNREAD", "CATEGORY_PROMOTIONS", "IMPORTANT"]},
    {"labels": ["UNREAD", "CATEGORY_PERSONAL"]}, {"labels": ["CATEGORY_PROMOTIONS"]},
    {"subject": "Weekly newsletter", "body": "Hello reader", "snippet": "Hello reader"},
])
def test_filter_keeps_protected_or_uncertain_mail(changes):
    assert filter_reason(message(**changes)) is None


def test_promotion_requires_all_signals_and_honors_allowlist():
    assert filter_reason(message())
    assert filter_reason(message(), "@shop.example") is None
    assert filter_reason(message(), "sale@shop.example") is None
    assert filter_reason(message(sender="x@evilshop.example"), "@shop.example")


@pytest.fixture(params=["sqlite", "firestore"])
def workflow(request, tmp_path, monkeypatch):
    if request.param == "firestore":
        from test_firestore import database
        db = database(monkeypatch)
    else:
        db = Database(tmp_path / "mail.db")
        db.initialize()
    config = Settings(_env_file=None, enable_gmail=True, gmail_account="owner@example.com",
                      gmail_client_id="client", gmail_client_secret="secret", gmail_refresh_token="refresh",
                      telegram_chat_id=123, telegram_bot_token="token", telegram_webhook_secret="hook")
    gmail = AsyncMock()
    gmail.unread_ids.return_value = (["ab12", "ab13"], False)
    mails = {"ab12": message(), "ab13": message("ab13", subject="Homework", body="Please finish the report")}
    gmail.read.side_effect = lambda identifier: copy.deepcopy(mails[identifier])
    async def trash(identifier):
        mails[identifier]["labels"].append("TRASH")
    gmail.trash.side_effect = trash
    bot = AsyncMock()
    bot.enabled = True
    counter = iter(range(100, 1000))
    bot.send_message.side_effect = lambda *a, **kw: {"ok": True, "result": {"message_id": next(counter)}}
    ai = AsyncMock()
    ai._generate_output.return_value = MailSummary(summary="待完成報告", next_step="確認期限")
    ai.parse.return_value = ParsedTask("Finish report")
    return MailWorkflow(db, gmail, bot, ai, config)


@pytest.mark.asyncio
async def test_digest_trashes_only_promotions_and_binds_retained_card(workflow):
    now = datetime(2026, 10, 7, 8, tzinfo=workflow.settings.tz)
    assert (await workflow.daily(now))["processed"] == 2
    workflow.gmail.trash.assert_awaited_once_with("ab12")
    assert workflow.binding(123, 101)["mail_id"] == "ab13"
    assert "垃圾桶" in workflow.telegram.send_message.call_args_list[0].args[1]
    workflow.gmail.mark_read.assert_not_awaited()
    workflow.ai.parse.assert_not_awaited()
    assert (await workflow.daily(now))["already_complete"]
    assert workflow.telegram.send_message.await_count == 3
    assert workflow.already_reported("ab13")


@pytest.mark.asyncio
async def test_gmail_failure_keeps_journal_and_resumes_next_day(workflow):
    now = datetime(2026, 10, 7, 8, tzinfo=workflow.settings.tz)
    real_trash = workflow.gmail.trash.side_effect
    async def uncertain(identifier):
        await real_trash(identifier)
        raise GmailError("uncertain write")
    workflow.gmail.trash.side_effect = uncertain
    with pytest.raises(GmailError):
        await workflow.daily(now)
    workflow.gmail.trash.side_effect = real_trash
    await workflow.daily(now + timedelta(days=1))
    # It sees TRASH on recovery, reports the success and does not repeat the write.
    assert workflow.gmail.trash.await_count == 1
    assert "已移到垃圾桶" in workflow.telegram.send_message.call_args_list[0].args[1]
    assert workflow.get("day:2026-10-07")["complete"]


@pytest.mark.asyncio
async def test_unknown_telegram_outcome_is_not_blindly_resent(workflow):
    workflow.telegram.send_message.side_effect = TelegramError("timeout")
    now = datetime(2026, 10, 7, 8, tzinfo=workflow.settings.tz)
    with pytest.raises(GmailError):
        await workflow.daily(now)
    with pytest.raises(GmailError):
        await workflow.daily(now + timedelta(days=1))
    assert workflow.telegram.send_message.await_count == 1
    assert workflow.gmail.trash.await_count == 1


@pytest.mark.asyncio
async def test_task_creation_is_atomic_across_updates_and_preserves_source(workflow):
    workflow.patch("message:ab13", mail=message("ab13"))
    # Both requests prepared before either commits, as on concurrent webhook instances.
    actions = [await workflow.prepare_action("ab13", "新增任務：明天完成報告") for _ in range(2)]
    first = workflow.db.process_update(701, actions[0])
    second = workflow.db.process_update(702, actions[1])
    assert "#1" in first["reply"] and "#1" in second["reply"]
    tasks = workflow.db.list_open_tasks()
    assert len(tasks) == 1
    assert "#all/ab13" in tasks[0]["timing"]["source_text"]
    assert tasks[0]["due_at"] is None


@pytest.mark.asyncio
async def test_keep_protects_mail_and_unknown_or_send_requests_do_nothing(workflow):
    workflow.patch("message:ab12", mail=message())
    action = await workflow.prepare_action("ab12", "保留")
    workflow.db.process_update(800, action)
    action = await workflow.prepare_action("ab12", "幫我寄信")
    assert "不會自動寄出" in action()
    await workflow.daily(datetime(2026, 10, 7, 8, tzinfo=workflow.settings.tz))
    workflow.gmail.trash.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_trash_and_read_are_separate(workflow):
    workflow.patch("message:ab13", mail=message("ab13"))
    action = await workflow.prepare_action("ab13", "已讀")
    workflow.db.process_update(810, action)
    workflow.gmail.mark_read.assert_awaited_once_with("ab13")
    workflow.gmail.trash.assert_not_awaited()
    action = await workflow.prepare_action("ab13", "刪除")
    workflow.db.process_update(811, action)
    workflow.gmail.trash.assert_awaited_once_with("ab13")


@pytest.mark.asyncio
async def test_account_or_configuration_failure_never_changes_mail(workflow):
    workflow.gmail.verify_account.side_effect = GmailError("wrong account")
    with pytest.raises(GmailError):
        await workflow.daily()
    workflow.gmail.trash.assert_not_awaited()
    workflow.settings.telegram_chat_id = -123
    with pytest.raises(GmailError, match="private"):
        workflow.require_config()


@pytest.mark.asyncio
async def test_ai_failure_falls_back_to_excerpt_without_changing_mail(workflow):
    from chronos.ai import AIError
    workflow.ai._generate_output.side_effect = AIError("unavailable")
    result = await workflow.summary(message())
    assert "原文摘錄" in result and "20% off" in result


@pytest.mark.asyncio
async def test_concurrent_daily_run_is_rejected(workflow):
    import time
    workflow.patch("daily-lock", owner="another-worker", until=time.time() + 60)
    with pytest.raises(GmailError, match="Another"):
        await workflow.daily()
    workflow.gmail.unread_ids.assert_not_awaited()
