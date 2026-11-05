from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

from chronos.mail_links import mail_url, source_url
from chronos.mail_workflow import format_mail_card
from chronos.tasks import format_task, format_task_block


def test_exact_message_search_preserves_account_and_encodes_reserved_characters():
    mail = {"id": "ab12", "thread_id": "cd34", "rfc_message_id": "<message+tag&x@example.com>",
            "subject": "A < B", "sender": "Sender"}
    url = mail_url(mail, "owner+tag@example.com")
    parsed = urlsplit(url)
    assert parse_qs(parsed.query) == {"ui": ["2"], "authuser": ["owner+tag@example.com"]}
    assert unquote(parsed.fragment) == "search/in:anywhere rfc822msgid:message+tag&x@example.com"
    card = format_mail_card(mail, "摘要", "owner+tag@example.com")
    assert "&amp;authuser=" in card and "A &lt; B" in card
    task = {"title": "郵件閱讀｜A < B", "timing": {"source_text": "Gmail: " + url}}
    for text in [format_task(task, ZoneInfo("Asia/Taipei"), html=True),
                 "\n".join(format_task_block(task, ZoneInfo("Asia/Taipei")))]:
        assert 'href="' in text and "開啟郵件" in text and "&amp;authuser=" in text
    assert url in format_task(task, ZoneInfo("Asia/Taipei"))


def test_legacy_task_source_is_visible_without_database_rewrite():
    task = {"title": "Read email: Networking", "timing": {
        "source_text": "Gmail: https://mail.google.com/mail/u/owner@example.com/#all/ab12"}}
    original = task["timing"]["source_text"]
    assert source_url(task) == mail_url({"id": "ab12"}, "owner@example.com")
    assert "郵件閱讀｜Networking" in format_task(task, ZoneInfo("Asia/Taipei"))
    assert task["timing"]["source_text"] == original
    assert source_url({"timing": {"source_text": "https://evil.example/mail/ab12"}}) is None


def test_missing_message_header_uses_thread_and_keeps_account_selector():
    url = mail_url({"id": "ab12", "thread_id": "cd34"}, "owner@example.com")
    assert url.endswith("#all/cd34") and "authuser=owner%40example.com" in url
