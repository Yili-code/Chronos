import base64
import json

import httpx
import pytest

from chronos.gmail import GmailClient, GmailError, decode_message
from chronos.gmail_authorize import save_env
from chronos.settings import Settings


@pytest.mark.asyncio
async def test_transport_refresh_pagination_and_only_safe_write_endpoints():
    calls = []
    def respond(request):
        calls.append(request)
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "access", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer access"
        path = request.url.path
        if path.endswith("profile"):
            return httpx.Response(200, json={"emailAddress": "owner@example.com"})
        if path.endswith("messages"):
            if request.url.params.get("pageToken"):
                return httpx.Response(200, json={"messages": [{"id": "ab2"}]})
            return httpx.Response(200, json={"messages": [{"id": "ab1"}], "nextPageToken": "next"})
        if path.endswith("modify"):
            assert json.loads(request.content) == {"removeLabelIds": ["UNREAD"]}
        else:
            assert path.endswith("/trash")
        return httpx.Response(200, json={"id": "ab2"})
    config = Settings(_env_file=None, gmail_client_id="client", gmail_client_secret="secret",
                      gmail_refresh_token="refresh", gmail_account="owner@example.com")
    client = GmailClient(config, transport=httpx.MockTransport(respond))
    await client.verify_account()
    assert await client.unread_ids(2, skip=lambda identifier: identifier == "ab1") == (["ab2"], False)
    await client.trash("ab2")
    await client.mark_read("ab2")
    assert len([c for c in calls if c.url.host == "oauth2.googleapis.com"]) == 1
    assert not hasattr(client, "send") and not hasattr(client, "delete")
    with pytest.raises(GmailError):
        await client.trash("../../send")


@pytest.mark.asyncio
async def test_oauth_error_never_leaks_response_or_secrets():
    config = Settings(_env_file=None, gmail_client_id="client", gmail_client_secret="secret",
                      gmail_refresh_token="refresh", gmail_account="owner@example.com")
    client = GmailClient(config, transport=httpx.MockTransport(lambda r: httpx.Response(400, text="secret refresh")))
    with pytest.raises(GmailError) as error:
        await client.verify_account()
    assert "secret" not in str(error.value) and "refresh" not in str(error.value)


def test_decode_multipart_uses_plain_text_ignores_attachments():
    encode = lambda s: base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")
    mail = decode_message({"id": "abc", "payload": {"headers": [{"name": "Subject", "value": "Hello &amp; goodbye"}],
        "parts": [{"mimeType": "text/plain", "body": {"data": encode("Important invoice")}},
                  {"mimeType": "text/html", "body": {"data": encode("<b>HTML</b>")}},
                  {"filename": "attached.txt", "mimeType": "text/plain", "body": {"data": encode("Attachment")}}]}})
    assert mail["body"] == "Important invoice"
    assert mail["subject"] == "Hello & goodbye"


def test_authorization_env_preserves_existing_settings_and_is_readable(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# Existing config\nCHRONOS_TELEGRAM_CHAT_ID=123\nCHRONOS_GMAIL_ACCOUNT=old\n", encoding="utf-8")
    save_env(path, {"CHRONOS_GMAIL_ACCOUNT": "owner@example.com", "CHRONOS_GMAIL_CLIENT_SECRET": "private-secret"})
    config = Settings(_env_file=path)
    assert config.telegram_chat_id == 123
    assert config.gmail_account == "owner@example.com"
    assert config.gmail_client_secret == "private-secret"
    assert "# Existing config" in path.read_text()
    assert list(tmp_path.glob(".env.gmail-*")) == []
