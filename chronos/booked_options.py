"""Telegram availability preview for an existing booking."""
from datetime import datetime, time, timedelta

from .google_calendar import CalendarError
from .learning_calendar import find_slot
from .study_reflow import bounds
from .study_reschedule import scheduling_inputs


async def adjustment_card(planner, task, state, now):
    cfg = planner.settings
    stop = now + timedelta(days=7)
    binding = dict(task_id=task['id'], token=state['token'], mode='booked',
                   original=state['start'], original_end=state['end'], choices=[])
    start, end = (datetime.fromisoformat(state[k]).astimezone(cfg.tz) for k in ('start', 'end'))
    lines = [task['title'][:120], f"原安排 {start:%m/%d %H:%M}–{end:%H:%M}（{state['minutes']} 分鐘）"]
    buttons = []
    try:
        await planner.calendar.verify_owner()
        events = await planner.calendar.agenda(now, stop)
        busy, reserved, budget, policy = scheduling_inputs(planner, task['id'], state, events, now, 8)
        upcoming = sorted(((bounds(e, cfg.tz), e) for e in events), key=lambda item: item[0])
        lines.append("近期行程（最多顯示 8 筆）")
        for (begin, finish), event in upcoming[:8]:
            lines.append(f"{begin:%m/%d %H:%M}–{finish:%m/%d %H:%M} {event.get('summary', '忙碌')[:60]}")
        if not upcoming:
            lines.append("未來七天沒有其他行程。")
        lines.append("接下來三天的行事曆空檔（工作時段內）")
        occupied = sorted(bounds(e, cfg.tz) for e in events if e.get("transparency") != "transparent")
        for offset in range(3):
            day = now.date() + timedelta(days=offset)
            midnight = datetime.combine(day, time(), cfg.tz)
            cursor = max(now, midnight + timedelta(hours=cfg.learning_start_hour))
            limit = midnight + timedelta(hours=cfg.learning_end_hour)
            gaps = []
            for left, right in occupied + [(limit, limit)]:
                if cursor >= limit:
                    break
                if left > cursor:
                    gaps.append(f"{cursor:%H:%M}–{min(left, limit):%H:%M}")
                cursor = max(cursor, right)
            lines.append(f"{day:%m/%d} " + ("、".join(gaps[:6]) or "無空檔") + (" 等" if len(gaps) > 6 else ""))
        lines.append("最早可用空檔")
        cursor = now
        while len(buttons) < 3:
            slot = find_slot(task, now, busy, reserved, cfg, state['minutes'], budget, policy,
                             window=(cursor, stop))
            if not slot:
                break
            begin, finish = slot
            cursor = finish
            if slot == (start, end):
                cursor = begin + timedelta(minutes=15)
                continue
            label = f"{begin:%m/%d %H:%M}–{finish:%H:%M}"
            index = len(buttons)
            lines.append(label)
            binding['choices'].append(begin.strftime('%Y-%m-%d %H:%M'))
            buttons.append([dict(text=label, callback_data=f"learn:pick{index}:{task['id']}:{state['token']}")])
        if not buttons:
            lines.append("未來七天沒有符合任務時長與排程設定的空檔。")
    except CalendarError:
        lines.append("目前無法讀取完整行程，請稍後重試。")
    lines.append("可直接回覆新的日期、時間及時長，或輸入 /cancel 保留原安排。")
    return {'messages': [dict(text='\n'.join(lines), learning_edit=binding,
                              reply_markup={'inline_keyboard': buttons} if buttons else {'force_reply': True})]}


async def select_slot(planner, chat_id, message_id, task_id, token, index, update_id):
    key = f'learning:edit-chat:{chat_id}'
    pending = planner.db.get_task_action_state(key) or {}
    state = planner.get(task_id) or {}
    now = datetime.now(planner.settings.tz)
    if (pending.get('prompt_message_id') != message_id or pending.get('task_id') != task_id
            or pending.get('token') != token or state.get('token') != token
            or state.get('status') != 'scheduled' or pending.get('original') != state.get('start')
            or pending.get('original_end') != state.get('end') or pending.get('expires_at', '') <= now.isoformat()
            or index >= len(pending.get('choices', []))):
        return '這份空檔清單已過期，請重新開啟改期。'
    result = await planner.revise(task_id, token, pending['choices'][index], now, update_id=update_id)
    planner.db.mutate_task_action_state(key, lambda current: {} if current == pending else current)
    return result
