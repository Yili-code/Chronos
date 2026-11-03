import base64
import json
from email import policy
from email.parser import BytesParser

import httpx
import pytest

from chronos.gmail import GmailClient, GmailError
from chronos.settings import Settings


def client(handler):
    config = Settings(_env_file=None, gmail_account="owner@example.com")
    gmail = GmailClient(config, transport=httpx.MockTransport(handler))
    gmail._token, gmail._expires = "synthetic-access", float("inf")
    return gmail


@pytest.mark.asyncio
async def test_native_draft_mime_thread_headers_and_no_send_endpoint():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "POST" and request.url.path.endswith("/drafts")
        message = json.loads(request.content)["message"]
        assert message["threadId"] == "th1"
        mime = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(message["raw"]))
        assert str(mime["To"]) == "client@example.org"
        assert str(mime["In-Reply-To"]) == "<parent@example.org>"
        assert str(mime["References"]) == "<earlier@example.org> <parent@example.org>"
        assert str(mime["Subject"]) == "中文主旨"
        assert "你好" in mime.get_content()
        assert not mime.get("Cc") and not mime.get("Bcc") and not mime.is_multipart()
        return httpx.Response(200, json={"id": "dr1", "message": {"id": "msg1", "threadId": "th1", "labelIds": ["DRAFT"]}})
    gmail = client(handler)
    assert await gmail.create_reply_draft("th1", "client@example.org", "中文主旨", "你好\n再聯絡", "<parent@example.org>", "<earlier@example.org>") == "dr1"
    assert len(calls) == 1 and not hasattr(gmail, "send")


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"recipient": "x@example.org\r\nBcc: other@example.org"},
    {"recipient": "x@example.org,y@example.org"}, {"subject": "Hello\nBcc: hidden@example.org"},
    {"parent_id": "<parent@example.org>\nInjected: yes"}, {"references": "\r\nBcc: hidden@example.org"},
    {"thread_id": "../../send"}, {"body": ""}, {"recipient": "missing"},
])
async def test_header_and_recipient_injection_rejected_before_http(changes):
    calls = []
    gmail = client(lambda request: calls.append(request))
    args = dict(thread_id="th1", recipient="client@example.org", subject="Proposal", body="Hello", parent_id="<parent@example.org>")
    args.update(changes)
    with pytest.raises(GmailError):
        await gmail.create_reply_draft(**args)
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    {"id": "dr1"}, {"id": "dr1", "message": {"threadId": "wrong", "labelIds": ["DRAFT"]}},
    {"id": "dr1", "message": {"threadId": "th1", "labelIds": ["SENT"]}},
    {"id": "../../send", "message": {"threadId": "th1", "labelIds": ["DRAFT"]}},
])
async def test_incomplete_or_inconsistent_native_response_never_claims_success(response):
    gmail = client(lambda request: httpx.Response(200, json=response))
    with pytest.raises(GmailError) as error:
        await gmail.create_reply_draft("th1", "client@example.org", "Proposal", "Hello", "<parent@example.org>")
    assert error.value.status is None


@pytest.mark.asyncio
async def test_thread_get_decodes_all_messages_and_automatic_reply_metadata():
    def raw(identifier, received, automatic=False):
        return {"id": identifier, "threadId": "th1", "internalDate": str(received), "payload": {"headers": [
            {"name": "Auto-Submitted", "value": "auto-replied" if automatic else "no"},
            {"name": "Message-ID", "value": "<" + identifier + "@example.org>"}]}}
    gmail = client(lambda request: httpx.Response(200, json={"id": "th1", "messages": [raw("b", 2000, True), raw("a", 1000)]}))
    messages = await gmail.thread("th1")
    assert [m["id"] for m in messages] == ["a", "b"]
    assert messages[-1]["automatic"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"id": "th1", "messages": []}, {"id": "wrong", "messages": [{}]},
    {"id": "th1", "messages": [{"id": "a", "threadId": "other"}]}])
async def test_incomplete_thread_response_rejected(payload):
    gmail = client(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(GmailError):
        await gmail.thread("th1")


@pytest.mark.asyncio
async def test_send_uses_immutable_approved_mime_instead_of_mutable_native_recipient():
    def handler(request):
        assert request.url.path.endswith("drafts/send") and request.method == "POST"
        data = json.loads(request.content)
        assert data["id"] == "dr1"
        mime = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(data["message"]["raw"]))
        assert str(mime["To"]) == "approved@example.org" and not mime.get("Bcc")
        assert "Approved body" in mime.get_content()
        return httpx.Response(200, json={"id": "sent1", "threadId": "th1", "labelIds": ["SENT"]})
    gmail = client(handler)
    assert await gmail.send_reviewed_draft("dr1", "th1", "approved@example.org", "Proposal", "Approved body", "<parent@example.org>") == "sent1"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["bcc", "multipart", "recipient", "sent"])
async def test_native_raw_draft_inspection_rejects_hidden_or_unsupported_content(change):
    from email.message import EmailMessage
    mime = EmailMessage()
    mime["To"], mime["Subject"] = "approved@example.org", "Proposal"
    mime.set_content("Body")
    if change == "bcc":
        mime["Bcc"] = "hidden@example.org"
    if change == "multipart":
        mime.add_attachment(b"private", maintype="application", subtype="octet-stream", filename="private.bin")
    if change == "recipient":
        mime.replace_header("To", "one@example.org,two@example.org")
    labels = ["SENT"] if change == "sent" else ["DRAFT"]
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    gmail = client(lambda request: httpx.Response(200, json={"id": "dr1", "message": {"id": "native1", "threadId": "th1", "raw": raw, "labelIds": labels}}))
    with pytest.raises(GmailError):
        await gmail.read_plain_draft("dr1")


@pytest.mark.asyncio
async def test_native_raw_draft_inspection_preserves_unicode_and_lines():
    from email.message import EmailMessage
    mime = EmailMessage()
    mime["To"], mime["Subject"], mime["In-Reply-To"] = "approved@example.org", "中文主旨", "<parent@example.org>"
    mime.set_content("第一行\n第二行")
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    gmail = client(lambda request: httpx.Response(200, json={"id": "dr1", "message": {"id": "native1", "threadId": "th1", "raw": raw, "labelIds": ["DRAFT"]}}))
    parsed = await gmail.read_plain_draft("dr1")
    assert parsed["subject"] == "中文主旨" and parsed["body"] == "第一行\n第二行\n"
