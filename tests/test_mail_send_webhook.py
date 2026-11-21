from chronos import main
from test_correspondence_webhook import configure
from test_mail_webhook import post
from test_system import system


def prepared(system, monkeypatch):
    client, tasks, bot, gmail, mail = configure(system, monkeypatch)
    gmail.send_reviewed_draft.return_value = "sent1"
    assert post(client, 25001, text="草擬回覆：英文簡潔，不承諾時間").status_code == 200
    state = mail.get("reply:ab12")
    assert post(client, 25002, text="儲存 Gmail 草稿 " + state["version"], card=700).status_code == 200
    state = mail.get("reply:ab12")
    gmail.read_plain_draft.return_value = {key: state[key] for key in ("recipient", "subject", "body", "parent_id", "thread_id")}
    gmail.read_plain_draft.return_value["message_id"] = "native1"
    assert post(client, 25003, text="準備寄出回覆草稿 " + state["version"], card=701).status_code == 200
    return client, tasks, bot, gmail, mail


def test_exact_send_confirmation_is_card_bound_and_receipt_idempotent(system, monkeypatch):
    client, tasks, bot, gmail, mail = prepared(system, monkeypatch)
    assert mail.binding(123, 702)["mail_id"] == "ab12"
    state = mail.get("reply:ab12")
    command = "確認寄出回覆 " + state["send_token"]
    for _ in range(2):
        assert post(client, 25004, text=command, card=702).status_code == 200
    assert post(client, 25005, text=command, card=702).status_code == 200
    gmail.send_reviewed_draft.assert_awaited_once()
    assert mail.get("reply:ab12")["status"] == "sent"
    assert "SENT" in bot.send_message.call_args.args[1]
    assert not tasks.list_open()


def test_another_chat_and_edited_confirmation_cannot_send(system, monkeypatch):
    client, tasks, bot, gmail, mail = prepared(system, monkeypatch)
    command = "確認寄出回覆 " + mail.get("reply:ab12")["send_token"]
    assert post(client, 25101, text=command, chat=456, card=702).status_code == 403
    assert post(client, 25102, text=command, edited=True, card=702).status_code == 200
    gmail.send_reviewed_draft.assert_not_awaited()


def test_native_bcc_or_other_body_change_prevents_send_after_preview(system, monkeypatch):
    client, tasks, bot, gmail, mail = prepared(system, monkeypatch)
    command = "確認寄出回覆 " + mail.get("reply:ab12")["send_token"]
    gmail.read_plain_draft.return_value["body"] = "Different promise"
    assert post(client, 25201, text=command, card=702).status_code == 200
    assert "阻擋" in bot.send_message.call_args.args[1]
    gmail.send_reviewed_draft.assert_not_awaited()
    assert not tasks.list_open()
