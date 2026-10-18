"""Reply-bound personal Calendar previews, separate from study tasks."""
from datetime import datetime, timedelta
from hashlib import sha256
from html import escape

from .ai import AIError
from .google_calendar import CalendarError
from .learning_calendar import class_windows, short_task_windows, overlaps
from .task_timing import display_date


def slot_line(slot, settings):
    start, end = (datetime.fromisoformat(slot[k]).astimezone(settings.tz) for k in ('start', 'end'))
    return f"{display_date(start.date().isoformat())} · {start:%H:%M}–{end:%H:%M}"


def bind_free_message(db, chat_id, message_id, message):
    if message.get('free_slot'):
        db.mutate_task_action_state(f'free:message:{chat_id}:{message_id}', lambda old: old or message['free_slot'])
    if message.get('free_booking'):
        db.mutate_task_action_state('free:booking:'+message['free_booking'],
            lambda old: {**old, 'message_id': message_id} if old and old['status']=='pending' else old)


async def preview_free_event(db, ai, settings, chat_id, message_id, instruction, update_id, now=None):
    now = now or datetime.now(settings.tz)
    key = f'free:message:{chat_id}:{message_id}'
    slot = db.get_task_action_state(key)
    if not slot or slot.get('expires_at','') <= now.isoformat():
        return 'This slot expired. Use /free to check availability again.'
    try:
        output = await ai.calendar_event_title(instruction)
    except AIError:
        return 'I could not prepare the event. Nothing booked. Reply again or use /free.'
    if not output.actionable:
        return 'Reply with an event name for this exact two-hour slot. Use /free to choose a new slot.'
    token = sha256(f'free:{chat_id}:{update_id}'.encode()).hexdigest()[:32]
    def save():
        current = db.get_task_action_state(key)
        existing = db.get_task_action_state('free:booking:'+token)
        active = db.get_task_action_state('free:booking:'+current['active_token']) if current and current.get('active_token') else None
        if not current or current.get('expires_at','') <= now.isoformat() or (active and active['status'] in {'creating','booked'}):
            return False
        saved = existing or {**slot, 'title': output.title, 'chat_id': chat_id,
                             'source_key': key, 'status':'pending', 'token':token}
        db._save_task_action_state('free:booking:'+token, saved)
        db._save_task_action_state(key,{**current,'active_token':token})
        return saved
    saved = db._run_task_action(save)
    if not saved:
        return 'This slot expired or has already been booked. Use /free.'
    return {'messages':[{'text':f"<b>Add to Calendar?</b>\n{escape(saved['title'])}\n{slot_line(saved,settings)}",
        'parse_mode':'HTML','free_booking':token,'reply_markup':{'inline_keyboard':[[
            {'text':'Allow','callback_data':'free:allow:'+token},
            {'text':'Cancel','callback_data':'free:cancel:'+token}]]}}]}


async def free_booking_callback(db, calendar, settings, chat_id, message_id, token, action, now=None):
    now = now or datetime.now(settings.tz)
    key = 'free:booking:'+token
    def claim():
        state = db.get_task_action_state(key)
        source = db.get_task_action_state(state['source_key']) if state else None
        if (not state or state.get('chat_id')!=chat_id or state.get('message_id')!=message_id
                or not source or source.get('active_token')!=token):
            return None
        if state['status']=='booked':
            return state
        if state['status'] not in {'pending','creating'}:
            return None
        if action=='cancel':
            if state['status']=='creating':
                return {**state,'status':'in_progress'}
            updated={**state,'status':'cancelled'}
        elif state['status']=='pending' and state['expires_at'] <= now.isoformat():
            updated={**state,'status':'expired'}
        else:
            updated={**state,'status':'creating'}
        db._save_task_action_state(key,updated)
        return updated
    state=db._run_task_action(claim)
    if not state:
        return 'This preview is no longer active. Use /free.'
    if state['status']=='in_progress':
        return 'Booking is being checked. Tap Allow again to check its result.'
    if state['status']=='cancelled':
        return 'Cancelled. Nothing booked.'
    if state['status']=='expired':
        return 'This slot expired. Nothing booked. Use /free.'
    if state['status']=='booked':
        return 'This event has already been added to Calendar.'
    event_id=sha256(('chronos-free:'+token).encode()).hexdigest()
    await calendar.verify_owner()
    event=await calendar.event(event_id)
    if event is None:
        start,end=(datetime.fromisoformat(state[k]).astimezone(settings.tz) for k in ('start','end'))
        busy=await calendar.busy(start,end)
        policy=db.get_task_action_state('learning:course-calendar-policy')
        if start <= now or overlaps(start,end,[*busy,*class_windows(start.date(),settings.tz,policy),
                                              *short_task_windows(start.date(),settings.tz,120)]):
            db.mutate_task_action_state(key,lambda old:{**old,'status':'conflict'})
            return 'That slot is no longer free. Nothing booked. Use /free to find another.'
        event=await calendar.insert({'id':event_id,'summary':state['title'],'colorId':'6',
            'start':{'dateTime':state['start'],'timeZone':settings.timezone},
            'end':{'dateTime':state['end'],'timeZone':settings.timezone},
            'visibility':'private','transparency':'opaque',
            'extendedProperties':{'private':{'chronosFreeToken':token}}})
    if event.get('status')=='cancelled' or event.get('extendedProperties',{}).get('private',{}).get('chronosFreeToken')!=token:
        raise CalendarError('Calendar event ownership or status changed. No replacement was created.')
    db.mutate_task_action_state(key,lambda old:{**old,'status':'booked','event_id':event_id})
    return f"Added to Calendar\n{state['title']}\n{slot_line(state,settings)}"
