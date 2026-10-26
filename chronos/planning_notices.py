"""Durable cleanup of queue notices after their batch results are delivered."""

KEY = 'learning:queue-notices'


def bind(planner, chat_id, message_id, task_ids):
    notice = {'chat_id': chat_id, 'message_id': message_id,
              'task_ids': list(task_ids)}
    name = f'{chat_id}:{message_id}'
    planner.db.mutate_task_action_state(KEY, lambda old: {**(old or {}), name: notice})


async def cleanup(planner):
    notices = planner.db.get_task_action_state(KEY) or {}
    for name, notice in notices.items():
        ready = True
        for task_id in notice['task_ids']:
            state = planner.get(task_id) or {}
            if state.get('delivery') not in {'scheduled_sent', 'proposal_sent', 'feedback_sent'}:
                ready = False
                break
        if not ready:
            continue
        try:
            result = await planner.telegram.request('deleteMessage', {
                'chat_id': notice['chat_id'], 'message_id': notice['message_id']})
        except RuntimeError:
            continue
        missing = (result.get('error_code') == 400 and
                   'message to delete not found' in result.get('description', '').lower())
        if result.get('ok') or missing:
            planner.db.mutate_task_action_state(KEY, lambda old: {
                key: value for key, value in (old or {}).items() if key != name})
