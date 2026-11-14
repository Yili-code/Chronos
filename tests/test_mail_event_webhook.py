from unittest.mock import AsyncMock, Mock
from datetime import datetime, timedelta

from chronos import main
from chronos.mail_screening import MailDecision
from chronos.mail_event_context import InvitationTiming
from chronos.mail_task_event import MailTaskIntent
from test_system import system
from test_mail_webhook import enable, post, immediate_notice_timer


def test_invitation_reply_lists_options_without_moving_meeting(system, monkeypatch):
    client, tasks, bot, config = system
    calendar = setup_event(system, monkeypatch)
    day = (datetime.now(config.tz) + timedelta(days=10)).replace(hour=0, minute=0, second=0, microsecond=0)
    start, end = day + timedelta(hours=14), day + timedelta(hours=17)
    evidence = f"{start.isoformat()} 到 {end.isoformat()}，台北"
    timing = InvitationTiming(start=start.isoformat(), end=end.isoformat(),
                              start_evidence=evidence, end_evidence=evidence)
    decision = MailDecision(tier='information', confidence=.9, reason='Invitation',
                            summary='交流活動', next_step='', optional_action=True, invitation=timing)
    main.mail_workflow().patch('message:ab12', screening=decision.model_dump(),
                               mail={'id': 'ab12', 'subject': 'School Party', 'body': evidence})
    meeting_start, meeting_end = start + timedelta(hours=1), start + timedelta(hours=2)
    calendar.agenda.return_value = [{
        'summary': '小組會議', 'start': {'dateTime': meeting_start.isoformat()},
        'end': {'dateTime': meeting_end.isoformat()}, 'attendees': [{'email': 'guest@example.test'}],
    }]
    calendar.busy.return_value = [(meeting_start, meeting_end)]

    for _ in range(2):
        assert post(client, 9781, text='小組會議可以移到什麼時候', card=500).status_code == 200
    bot.send_message.assert_awaited_once()
    text = bot.send_message.await_args.args[1]
    assert '「小組會議」的改期候選' in text and '請先與主辦人或其他參與者確認' in text
    assert '15:00–16:00' not in text
    calendar.agenda.assert_awaited_once()
    calendar.insert.assert_not_awaited()
    calendar.move_event.assert_not_awaited()
    main.ai._generate_output.assert_not_awaited()
    assert not tasks.list_open()
    assert post(client, 9782, text='交通怎麼去？', card=500).status_code == 200
    assert bot.send_message.await_args.kwargs['parse_mode'] == 'HTML'


def setup_event(system, monkeypatch, *, missing_end=False):
    client, tasks, bot, config = system
    enable(config, monkeypatch)
    config.calendar_account = 'owner@example.com'
    evidence = '2027年11月08日 12:00 UTC，持續 2 hours'
    main.mail_workflow().patch('message:ab12', mail={'id': 'ab12', 'subject': 'Weekend Practice #31', 'body': evidence})
    main.ai._generate_output.return_value = MailTaskIntent(kind='event', title='Weekend Practice #31',
        start='2027-11-08T12:00:00+00:00', end=None if missing_end else '2027-11-08T14:00:00+00:00',
        start_evidence=evidence, end_evidence='2 hours')
    calendar = AsyncMock()
    calendar.configured = Mock(return_value=True)
    calendar.event.return_value = None
    calendar.busy.return_value = []
    async def insert(body):
        return body
    calendar.insert.side_effect = insert
    monkeypatch.setattr(main, 'google_calendar', calendar)
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 777}}
    return calendar


def test_event_task_callback_books_once_and_cleans_only_after_success(system, monkeypatch):
    client, tasks, bot, config = system
    calendar = setup_event(system, monkeypatch)
    assert post(client, 9800, callback='mail:task:ab12').status_code == 200
    assert post(client, 9800, callback='mail:task:ab12').status_code == 200
    calendar.insert.assert_awaited_once()
    assert not tasks.list_open()
    bot.send_message.assert_awaited_once()
    assert '已加入行事曆' in bot.send_message.await_args.args[1]
    assert main.db.get_update(9800)['delivered']
    assert [c.args[1]['message_id'] for c in bot.request.await_args_list] == [500, 777, 500]
    main.asyncio.sleep.assert_awaited_once()


def test_event_question_has_bound_reply_context_and_finishes_from_owner_facts(system, monkeypatch):
    client, tasks, bot, config = system
    calendar = setup_event(system, monkeypatch, missing_end=True)
    main.ai._generate_output.return_value.question = '這場昔別的活動是在吃碎具體的哪一天？'
    assert post(client, 9801, callback='mail:task:ab12').status_code == 200
    calendar.insert.assert_not_awaited()
    assert bot.send_message.await_args.args[1] == (
        '郵件活動\n請回覆活動日期、開始和結束時間，以及時區。也可以回覆「取消」。'
    )
    assert bot.send_message.await_args.kwargs['reply_markup']['force_reply']
    assert bot.send_message.await_args.kwargs['delete_after_seconds'] == 60
    binding = main.mail_workflow().binding(123, 777)
    assert binding['context_kind'] == 'task_event' and binding['origin_message_id'] == 500
    owner = '2027-11-08 20:00–22:00 台北'
    main.ai._generate_output.return_value = MailTaskIntent(kind='event', title='Weekend Practice #31',
        start='2027-11-08T12:00:00+00:00', end='2027-11-08T14:00:00+00:00',
        start_evidence=owner, end_evidence=owner)
    assert post(client, 9802, text=owner, card=777).status_code == 200
    calendar.insert.assert_awaited_once()
    assert not tasks.list_open()


def test_spoofed_event_callback_is_rejected_before_ai_or_calendar(system, monkeypatch):
    client, tasks, bot, config = system
    calendar = setup_event(system, monkeypatch)
    assert post(client, 9803, callback='mail:task:ab13').status_code == 403
    main.ai._generate_output.assert_not_awaited()
    calendar.insert.assert_not_awaited()


def test_cancelled_event_question_cleans_prompt_and_notice_but_keeps_source(system, monkeypatch):
    client, tasks, bot, config = system
    calendar = setup_event(system, monkeypatch, missing_end=True)
    assert post(client, 9810, callback='mail:task:ab12').status_code == 200
    bot.request.assert_awaited_once_with('deleteMessage', {'chat_id': 123, 'message_id': 777})
    assert 59 < main.asyncio.sleep.await_args.args[0] <= 60
    bot.request.reset_mock()
    main.asyncio.sleep.reset_mock()
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 778}}
    assert post(client, 9811, text='取消', card=777).status_code == 200
    deleted = [c.args[1]['message_id'] for c in bot.request.await_args_list]
    assert deleted == [777, 778] and 500 not in deleted
    main.asyncio.sleep.assert_awaited_once()
    calendar.insert.assert_not_awaited()
    assert post(client, 9811, text='取消', card=777).status_code == 200
    assert bot.send_message.await_count == 2
    main.asyncio.sleep.assert_awaited_once()
