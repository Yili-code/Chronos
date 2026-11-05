import copy
from datetime import datetime, timedelta

import pytest

from chronos.ai import AIError
from chronos.gmail import GmailError
from chronos.mail_screening import MailDecision, decide, digest
from chronos.telegram import TelegramError
from test_mail_workflow import workflow, message  # noqa: F401


def decision(tier="low_value", **changes):
    return MailDecision(**{"tier": tier, "confidence": .97, "reason": "例行群發優惠，沒有待辦事項。",
                           "summary": "商店優惠資訊。", "next_step": "", **changes})


def setup_screening(workflow, mails, choices):
    workflow.settings.gmail_first_layer_screening = True
    workflow.gmail.message_ids.return_value = (list(mails), False)
    workflow.gmail.read.side_effect = lambda identifier: copy.deepcopy(mails[identifier])
    async def archive(identifier):
        mails[identifier]["labels"] = [label for label in mails[identifier]["labels"] if label != "INBOX"]
    workflow.gmail.archive.side_effect = archive
    workflow.ai._generate_output.side_effect = choices
    return datetime(2026, 11, 5, 7, tzinfo=workflow.settings.tz)


@pytest.mark.asyncio
async def test_daily_screens_before_notification_without_trash_or_task(workflow):
    mails = {"ab12": message(labels=["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"]),
             "ab13": message("ab13", subject="Weekly technology digest", body="New tools", snippet="New tools"),
             "ab14": message("ab14", subject="Interview", body="Reply by Friday", unsubscribe=False)}
    now = setup_screening(workflow, mails, [decision(), decision("information"),
        decision("important", reason="面試需要回覆。", action_required=True, next_step="週五前回覆。")])
    await workflow.daily(now)
    workflow.gmail.archive.assert_awaited_once_with("ab12")
    workflow.gmail.trash.assert_not_awaited()
    workflow.gmail.mark_read.assert_not_awaited()
    workflow.ai.parse.assert_not_awaited()
    assert "UNREAD" in mails["ab12"]["labels"]
    assert workflow.telegram.send_message.await_count == 2
    card, aggregate = workflow.telegram.send_message.call_args_list
    assert "需要你做的事" in card.args[1]
    assert workflow.binding(123, 100)["mail_id"] == "ab14"
    assert "資訊 1 封 · 已封存 1 封" in aggregate.args[1]
    assert aggregate.kwargs['parse_mode'] == 'HTML'
    assert "Weekly technology digest" in aggregate.args[1]
    assert "mail.google.com" in aggregate.args[1]
    assert all(workflow.already_reported(identifier) for identifier in mails)
    await workflow.daily(now)
    assert workflow.telegram.send_message.await_count == 2


@pytest.mark.parametrize("changes", [
    {"attachments": [{"name": "offer.pdf"}]}, {"body_incomplete": True},
    {"body_truncated": True}, {"alternative_body": "different body"},
    {"sender_ambiguous": True}, {"envelope_ambiguous": True}, {"has_reply": True},
    {"labels": ["UNREAD", "CATEGORY_PROMOTIONS", "IMPORTANT"]},
    {"labels": ["UNREAD", "CATEGORY_PROMOTIONS", "STARRED"]},
    {"labels": ["UNREAD", "CATEGORY_PERSONAL"]},
    {"subject": "Software internship opportunities"}, {"subject": "Scholarship application"},
    {"subject": "Invoice"}, {"subject": "Security alert"}, {"unsubscribe": False},
])
@pytest.mark.asyncio
async def test_low_value_model_advice_cannot_override_protected_evidence(workflow, changes):
    workflow.ai._generate_output.return_value = decision()
    workflow.ai._generate_output.side_effect = None
    result = await decide(workflow, message(**changes), {})
    assert result.tier == "information"
    workflow.gmail.archive.assert_not_awaited()


@pytest.mark.asyncio
async def test_keep_allowlist_and_low_confidence_protect_archive_without_forcing_cards(workflow):
    workflow.ai._generate_output.return_value = decision()
    assert (await decide(workflow, message(), {"keep": True})).tier == "information"
    workflow.settings.gmail_keep_senders = "@shop.example"
    assert (await decide(workflow, message(), {})).tier == "information"
    workflow.settings.gmail_keep_senders = ""
    workflow.ai._generate_output.return_value = decision(confidence=.5)
    assert (await decide(workflow, message(), {})).tier == "information"
    workflow.ai._generate_output.return_value = decision(action_required=True, next_step="請回覆面試可出席時段。")
    assert (await decide(workflow, message(), {})).tier == "important"


@pytest.mark.asyncio
async def test_provider_failure_preserves_original_without_sending_fallback_cards(workflow):
    now = setup_screening(workflow, {"ab12": message()}, AIError("unavailable"))
    with pytest.raises(GmailError):
        await workflow.daily(now)
    workflow.gmail.archive.assert_not_awaited()
    workflow.telegram.send_message.assert_not_awaited()
    assert not workflow.get("daily:2026-11-05").get("complete")
    assert not workflow.get("message:ab12").get("screened")


@pytest.mark.asyncio
async def test_uncertain_archive_reconciles_gmail_without_repeating_write(workflow):
    mails = {"ab12": message(labels=["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"])}
    now = setup_screening(workflow, mails, [decision()])
    actual = workflow.gmail.archive.side_effect
    async def uncertain(identifier):
        await actual(identifier)
        raise GmailError("unknown result")
    workflow.gmail.archive.side_effect = uncertain
    with pytest.raises(GmailError):
        await workflow.daily(now)
    assert workflow.get("daily:2026-11-05:ab12")["archive_pending"]
    await workflow.daily(now + timedelta(days=1))
    assert workflow.gmail.archive.await_count == 1
    assert workflow.ai._generate_output.await_count == 1
    assert workflow.get("daily:2026-11-05")["complete"]


@pytest.mark.asyncio
async def test_new_protection_cancels_pending_archive(workflow):
    mails = {"ab12": message(labels=["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"])}
    now = setup_screening(workflow, mails, [decision(), decision()])
    workflow.gmail.archive.side_effect = GmailError("not applied")
    with pytest.raises(GmailError):
        await workflow.daily(now)
    mails["ab12"]["labels"].append("STARRED")
    await workflow.daily(now + timedelta(days=1))
    assert workflow.gmail.archive.await_count == 1
    assert workflow.get("message:ab12")["screening"]["tier"] == "information"
    assert workflow.telegram.send_message.await_count == 1  # Digest only, not an owner-decision card.


@pytest.mark.asyncio
async def test_ambiguous_digest_is_not_resent_or_reclassified(workflow):
    now = setup_screening(workflow, {"ab12": message()}, [decision("information")])
    workflow.telegram.send_message.side_effect = TelegramError("timeout")
    with pytest.raises(GmailError):
        await workflow.daily(now)
    with pytest.raises(GmailError):
        await workflow.daily(now + timedelta(days=1))
    assert workflow.telegram.send_message.await_count == 1
    assert workflow.ai._generate_output.await_count == 1
    assert not workflow.get("daily:2026-11-05").get("complete")


@pytest.mark.asyncio
async def test_digest_splits_large_batch_and_keeps_stable_receipts(workflow):
    ids = [f"a{index:03x}" for index in range(40)]
    for identifier in ids:
        workflow.patch("message:" + identifier, mail=message(identifier, subject="A" * 240))
        workflow.patch("daily:2026-11-05:" + identifier,
                       screening=decision("information", summary="B" * 400).model_dump(), screening_done=True)
    state = {"ids": ids, "more": True}
    await digest(workflow, "daily:2026-11-05", state)
    calls = workflow.telegram.send_message.call_args_list
    assert len(calls) > 1
    assert all(len(call.args[1]) <= 3000 for call in calls)
    await digest(workflow, "daily:2026-11-05", state)
    assert workflow.telegram.send_message.await_count == len(calls)
