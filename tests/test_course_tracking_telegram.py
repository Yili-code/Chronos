from chronos.course_tracking_telegram import parse_reply_context


def test_parser_keeps_only_correlation_fields():
    result = parse_reply_context({
        "message_id": 303,
        "text": " 第四章 ",
        "reply_to_message": {"message_id": 101, "text": "prompt"},
        "from": {"id": 123, "token": "must-not-be-read"},
    })
    assert result.message_id == 303
    assert result.reply_to_message_id == 101
    assert result.text == "第四章"
    assert "token" not in repr(result)


def test_parser_rejects_non_text_or_malformed_reply():
    assert parse_reply_context({"message_id": 303, "text": "   "}) is None
    assert parse_reply_context({"message_id": 303, "text": "x", "reply_to_message": {}}) is None
