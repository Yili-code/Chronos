import asyncio

from test_learning_calendar import setup, NOW
from chronos.planning_notices import bind, cleanup, KEY
from test_system import system
from unittest.mock import AsyncMock
from chronos import main
from chronos.intents import Intent


def test_notice_waits_for_entire_batch_and_retries_after_restart(setup):
    planner, task, _, bot = setup
    other = planner.db.create_task('Other', None, None, NOW)
    ids = [task['id'], other['id']]
    bind(planner, 123, 99, ids)
    for task_id in ids:
        planner.update(task_id, lambda _: {'status': 'queued'})
    asyncio.run(cleanup(planner))
    bot.request.assert_not_awaited()
    planner.update(ids[0], lambda _: {'delivery': 'scheduled_sent'})
    planner.update(ids[1], lambda _: {'delivery': 'scheduled_pending'})
    asyncio.run(cleanup(planner))
    bot.request.assert_not_awaited()
    planner.update(ids[1], lambda _: {'delivery': 'proposal_sent'})
    bot.request.return_value = {'ok': False, 'error_code': 429}
    asyncio.run(cleanup(planner))
    assert planner.db.get_task_action_state(KEY)
    from chronos.learning_calendar import LearningCalendar
    restarted = LearningCalendar(planner.db, planner.calendar, bot, planner.settings)
    bot.request.return_value = {'ok': True}
    asyncio.run(cleanup(restarted))
    bot.request.assert_awaited_with('deleteMessage', {'chat_id': 123, 'message_id': 99})
    assert not planner.db.get_task_action_state(KEY)


def test_tick_deletes_notice_only_after_result_delivery(setup):
    planner, task, _, bot = setup
    planner.settings.learning_auto_accept = True
    planner.request_proposals([task['id']], minutes=60, immediate=True)
    bind(planner, 123, 99, [task['id']])
    asyncio.run(planner.tick(NOW, force_proposal=True))
    assert planner.get(task['id'])['delivery'] == 'scheduled_sent'
    bot.request.assert_any_await('deleteMessage', {'chat_id': 123, 'message_id': 99})
    assert not planner.db.get_task_action_state(KEY)


def test_webhook_binds_queue_notice_and_replay_does_not_send_again(system, monkeypatch):
    client, service, bot, config = system
    config.enable_learning_calendar = True
    task = service.create('Report')
    monkeypatch.setattr(main.ai, 'classify_intent', AsyncMock(
        return_value=Intent(action='plan_request', task_ids=[task['id']])))
    from chronos.learning_calendar import LearningCalendar
    monkeypatch.setattr(LearningCalendar, 'tick', AsyncMock(return_value={}))
    bot.send_message.return_value = {'ok': True, 'result': {'message_id': 999}}
    payload = {'update_id': 9955, 'message': {'message_id': 42,
               'chat': {'id': 123}, 'text': 'Schedule the report'}}
    for _ in range(2):
        response = client.post('/telegram/webhook', json=payload,
            headers={'X-Telegram-Bot-Api-Secret-Token': 'test-hook'})
        assert response.status_code == 200
    notice = main.db.get_task_action_state(KEY)['123:999']
    assert notice == {'chat_id': 123, 'message_id': 999, 'task_ids': [task['id']]}
    bot.send_message.assert_awaited_once()
