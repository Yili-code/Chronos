import base64
from unittest.mock import AsyncMock

import pytest

from chronos.gmail import GmailClient, GmailError
from chronos.settings import Settings
from test_executive_mail_evidence import sample_mail
from test_mail_correspondence import act
from test_mail_workflow import workflow
from test_system import system


def attachment_mail():
    return sample_mail({"filename": "terms.txt", "partId": "1", "mimeType": "text/plain",
        "body": {"attachmentId": "remote_id", "size": 5}})


def test_inventory_records_metadata_without_attachment_content():
    mail = attachment_mail()
    assert mail["attachments"] == [{"path": "0.1", "filename": "terms.txt", "mime_type": "text/plain", "size": 5}]
    assert mail["body_incomplete"]
    assert "remote_id" not in str(mail["attachments"])


@pytest.mark.asyncio
async def test_transport_reads_only_the_bound_attachment():
    client = GmailClient(Settings(_env_file=None))
    raw = {"id": "ab12", "payload": {"parts": [{"filename": "terms.txt", "mimeType": "text/plain",
        "body": {"attachmentId": "remote_id", "size": 5}}]}}
    client._request = AsyncMock(side_effect=[raw, {"data": base64.urlsafe_b64encode(b"hello").decode(), "size": 5}])
    result = await client.read_attachment("ab12", "0.0")
    assert result["data"] == b"hello" and result["filename"] == "terms.txt"
    assert client._request.call_args.args == ("GET", "messages/ab12/attachments/remote_id")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../secret", "0.-1", "0.100", "https://example.org", "0." + ".".join(["0"] * 22)])
async def test_invalid_attachment_path_never_calls_provider(path):
    client = GmailClient(Settings(_env_file=None))
    client._request = AsyncMock()
    with pytest.raises(GmailError):
        await client.read_attachment("ab12", path)
    client._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_attachment_inventory_and_text_read_remain_owner_card_bound(workflow):
    mail = attachment_mail()
    workflow.patch("message:ab12", mail=mail)
    workflow.gmail.read.side_effect = None
    workflow.gmail.read.return_value = mail
    workflow.gmail.read_attachment.return_value = {"filename": "terms.txt", "mime_type": "text/plain", "data": b"hello"}
    text = await act(workflow, "查看郵件附件")
    assert "terms.txt" in text and "尚未讀取" in text
    state = workflow.get("attachments:ab12")
    result = await act(workflow, "讀取郵件附件 " + state["version"] + " 1")
    assert "hello" in result
    assert workflow.get("attachments:ab12")["reviews"]["0.1"]["sha256"]
    workflow.gmail.read_attachment.assert_awaited_once_with("ab12", "0.1")
    workflow.gmail.create_reply_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_attachment_selection_is_not_read(workflow):
    workflow.patch("message:ab12", mail=attachment_mail())
    workflow.gmail.read.side_effect = None
    workflow.gmail.read.return_value = attachment_mail()
    await act(workflow, "查看郵件附件")
    state = workflow.get("attachments:ab12")
    mail = attachment_mail()
    mail["attachments"][0]["filename"] = "changed.txt"
    workflow.patch("message:ab12", mail=mail)
    workflow.gmail.read.side_effect = None
    workflow.gmail.read.return_value = mail
    assert "變更" in await act(workflow, "讀取郵件附件 " + state["version"] + " 1")
    workflow.gmail.read_attachment.assert_not_awaited()


@pytest.mark.asyncio
async def test_binary_preview_does_not_claim_it_was_understood(workflow):
    mail = attachment_mail()
    mail["attachments"][0].update(filename="contract.exe", mime_type="application/octet-stream")
    workflow.patch("message:ab12", mail=mail)
    workflow.gmail.read.side_effect = None
    workflow.gmail.read.return_value = mail
    await act(workflow, "查看郵件附件")
    state = workflow.get("attachments:ab12")
    assert "不支援" in await act(workflow, "讀取郵件附件 " + state["version"] + " 1")
    workflow.gmail.read_attachment.assert_not_awaited()


@pytest.mark.asyncio
async def test_inventory_is_paginated_without_losing_stored_metadata(workflow):
    mail = attachment_mail()
    mail["attachments"] = [{**mail["attachments"][0], "path": "0." + str(i), "filename": "檔" * 160} for i in range(20)]
    workflow.patch("message:ab12", mail=mail)
    workflow.gmail.read.side_effect = None
    workflow.gmail.read.return_value = mail
    text = await act(workflow, "查看郵件附件 第2頁")
    assert "6." in text and "10." in text and "11." not in text
    assert len(text.encode("utf-16-le")) // 2 < 4096
    assert len(workflow.get("attachments:ab12")["parts"]) == 20


@pytest.mark.asyncio
async def test_invalid_utf8_attachment_is_not_saved_as_read(workflow):
    mail = attachment_mail()
    workflow.patch("message:ab12", mail=mail)
    workflow.gmail.read.side_effect = None
    workflow.gmail.read.return_value = mail
    await act(workflow, "查看郵件附件")
    state = workflow.get("attachments:ab12")
    workflow.gmail.read_attachment.return_value = {"filename": "terms.txt", "mime_type": "text/plain", "data": b"\xff"}
    assert "未保存" in await act(workflow, "讀取郵件附件 " + state["version"] + " 1")
    assert not workflow.get("attachments:ab12")["reviews"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"data": "***", "size": 5},
    {"data": "eA==", "size": 5},
    {"attachmentId": "https://attacker.example/blob", "size": 5},
    {"data": "eA==", "size": 1048577},
])
async def test_invalid_provider_attachment_data_is_rejected(body):
    client = GmailClient(Settings(_env_file=None))
    client._request = AsyncMock(return_value={"id": "ab12", "payload": {
        "filename": "terms.txt", "mimeType": "text/plain", "body": body}})
    with pytest.raises(GmailError):
        await client.read_attachment("ab12", "0")


def test_attachment_previews_bind_to_the_actual_delivered_card(system, monkeypatch):
    from test_correspondence_webhook import configure
    from test_mail_webhook import post
    client, tasks, bot, gmail, mail = configure(system, monkeypatch)
    gmail.read.side_effect = None
    gmail.read.return_value = attachment_mail()
    gmail.read_attachment.return_value = {"filename": "terms.txt", "mime_type": "text/plain", "data": b"hello"}
    assert post(client, 29801, text="查看郵件附件").status_code == 200
    assert mail.binding(123, 700)["mail_id"] == "ab12"
    state = mail.get("attachments:ab12")
    command = "讀取郵件附件 " + state["version"] + " 1"
    assert post(client, 29802, text=command, card=700, chat=456).status_code == 403
    gmail.read_attachment.assert_not_awaited()
    assert post(client, 29803, text=command, card=700).status_code == 200
    assert mail.binding(123, 701)["mail_id"] == "ab12"
    assert not tasks.list_open()
    gmail.send_reviewed_draft.assert_not_awaited()
