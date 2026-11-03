from unittest.mock import AsyncMock

from chronos import main
from chronos.mail_correspondence import ReplyText
from test_mail_correspondence import thread
from test_mail_webhook import enable, post
from test_system import system


def configure(system, monkeypatch):
    client, tasks, bot, settings = system
    gmail = enable(settings, monkeypatch)
    mail = main.mail_workflow()
    mail.patch("message:ab12", mail=thread()[-1])
    gmail.thread.return_value = thread()
    gmail.create_reply_draft.return_value = "dr1"
    monkeypatch.setattr(main.ai, "_generate_output", AsyncMock(return_value=ReplyText(body="Thank you; I will check the date.")))
    counter = iter(range(700, 800))
    bot.send_message.side_effect = lambda *args, **kwargs: {"ok": True, "result": {"message_id": next(counter)}}
    return client, tasks, bot, gmail, mail


def test_reply_to_preview_saves_one_unsent_draft_and_retries_receipt(system, monkeypatch):
    client, tasks, bot, gmail, mail = configure(system, monkeypatch)
    assert post(client, 21001, text="草擬回覆：簡潔英文，不承諾日期").status_code == 200
    assert mail.binding(123, 700)["mail_id"] == "ab12"
    token = mail.get("reply:ab12")["version"]
    for _ in range(2):
        assert post(client, 21002, text="儲存 Gmail 草稿 " + token, card=700).status_code == 200
    gmail.create_reply_draft.assert_awaited_once()
    main.ai._generate_output.assert_awaited_once()
    assert bot.send_message.await_count == 2
    assert "尚未寄出" in bot.send_message.call_args.args[1]
    assert not tasks.list_open()


def test_draft_operations_reject_other_owner_and_old_edited_messages(system, monkeypatch):
    client, tasks, bot, gmail, mail = configure(system, monkeypatch)
    assert post(client, 21101, text="草擬回覆：英文", chat=456).status_code == 403
    assert post(client, 21102, text="草擬回覆：英文", edited=True).status_code == 200
    main.ai._generate_output.assert_not_awaited()
    gmail.thread.assert_not_awaited()
    assert not mail.get("reply:ab12")


def test_tracking_reply_stays_bound_and_cancellation_has_no_external_side_effect(system, monkeypatch):
    client, tasks, bot, gmail, mail = configure(system, monkeypatch)
    assert post(client, 21201, text="追蹤回覆：3 天").status_code == 200
    assert "等你回覆" in bot.send_message.call_args.args[1]
    assert mail.binding(123, 700)["mail_id"] == "ab12"
    assert post(client, 21202, text="停止追蹤", card=700).status_code == 200
    assert mail.get("followups")["ab12"]["status"] == "cancelled"
    gmail.create_reply_draft.assert_not_awaited()
    main.ai._generate_output.assert_not_awaited()
    assert not tasks.list_open()
