"""Reproduce incomplete MIME evidence reaching automatic deletion decisions."""
import base64

import pytest

from chronos.gmail import decode_message
from chronos.mail_workflow import filter_reason, format_mail_card
from test_mail_workflow import workflow  # Exercise both persistence backends.


def encoded(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


CASES = [
    ("E01", "有報價附件的促銷信也先幫我整理", {"filename": "quote.txt", "mimeType": "text/plain", "body": {"data": encoded("Payment required")}}, None),
    ("E02", "折扣信附 PDF 合約，先看清楚再清理", {"filename": "contract.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "remote"}}, None),
    ("E03", "這封信的完整內文還沒下載，別漏掉期限", {"mimeType": "text/plain", "body": {"attachmentId": "remote", "size": 100}}, None),
    ("E04", "內容亂碼的優惠信確認後再處理", {"mimeType": "text/plain", "body": {"data": "***"}}, None),
    ("E05", "這封信的非 UTF-8 區塊可能有重要資訊", {"mimeType": "text/plain", "body": {"data": "_w"}}, None),
    ("E06", "加密附件看不到，不要當成一般廣告", {"mimeType": "application/pkcs7-mime", "body": {"data": encoded("opaque")}}, None),
    ("E07", "含行事曆邀請的折扣活動信先保留", {"mimeType": "text/calendar", "body": {"data": encoded("BEGIN:VCALENDAR")}}, None),
    ("E08", "轉寄附件可能有付款資訊，不要漏看", {"mimeType": "message/rfc822", "parts": [{"mimeType": "text/plain", "body": {"data": encoded("Private detail")}}]}, None),
    ("E09", "安全通知附了文件，照既有規則但不要猜附件", {"filename": "incident.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "remote"}}, None),
    ("E10", "沒有附件而且資訊完整的普通促銷照規則清理", None, "促銷分類＋退訂標記＋明確優惠內容"),
]


def sample_mail(extra=None, security=False):
    parts = [{"mimeType": "text/plain", "body": {"data": encoded("Sale coupon 20% off")}}]
    if extra:
        parts.append(extra)
    return decode_message({"id": "ab12", "internalDate": "200000", "labelIds": ["UNREAD", "CATEGORY_PROMOTIONS"],
        "payload": {"mimeType": "multipart/mixed", "headers": [
            {"name": "Subject", "value": "Security alert" if security else "Sale 20% off"},
            {"name": "From", "value": "shop@example.com"},
            {"name": "List-Unsubscribe", "value": "<mailto:unsubscribe@example.com>"}], "parts": parts}})


@pytest.mark.parametrize("identifier,instruction,part,expected", CASES, ids=[c[0] for c in CASES])
def test_incomplete_evidence_is_not_automatically_trashed(identifier, instruction, part, expected):
    mail = sample_mail(part, security=identifier == "E09")
    assert filter_reason(mail, discard_account_security_after=100) == expected


def test_attachment_body_is_not_included_in_summary_input():
    mail = sample_mail({"mimeType": "message/rfc822", "parts": [
        {"mimeType": "text/plain", "body": {"data": encoded("PRIVATE ATTACHED MESSAGE")}}]})
    assert "PRIVATE ATTACHED MESSAGE" not in mail["body"]


def test_truncated_security_mail_is_retained_and_card_discloses_limits():
    mail = sample_mail({"mimeType": "text/plain", "body": {"data": encoded("x" * 13000)}}, security=True)
    assert mail["body_truncated"] is True
    assert filter_reason(mail, discard_account_security_after=100) is None
    assert "部分內文或附件未讀取" in format_mail_card(mail, "可讀內容", "owner@example.com")


@pytest.mark.asyncio
async def test_daily_preserves_incomplete_mail_and_delivers_owner_card(workflow):
    mail = sample_mail({"filename": "contract.pdf", "mimeType": "application/pdf",
                        "body": {"attachmentId": "remote"}})
    workflow.gmail.message_ids.return_value = (["ab12"], False)
    workflow.gmail.read.side_effect = lambda _: mail.copy()
    result = await workflow.daily()
    assert result["processed"] == 1
    workflow.gmail.trash.assert_not_awaited()
    workflow.gmail.archive.assert_not_awaited()
    workflow.gmail.mark_read.assert_not_awaited()
    assert workflow.get("message:ab12")["mail"]["body_incomplete"] is True
    assert workflow.binding(123, 100)["mail_id"] == "ab12"
    assert workflow.already_reported("ab12")
    assert "部分內文或附件未讀取" in workflow.telegram.send_message.call_args_list[0].args[1]
