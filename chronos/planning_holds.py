"""Approved local planning reservations, distinct from Calendar events."""
from datetime import datetime
KEY='planning:holds'


def slots(db):
    return (db.get_task_action_state(KEY) or {}).get('slots',[])


def windows(db):
    return [(datetime.fromisoformat(s['start']),datetime.fromisoformat(s['end'])) for s in slots(db)]


def save(db, token, items, now):
    def merge(old):
        kept=[s for s in (old or {}).get('slots',[]) if s['end']>now.isoformat() and s.get('token')!=token]
        return {'slots':kept+[{**s,'token':token} for s in items]}
    return db.mutate_task_action_state(KEY,merge)


def events(db,begin,end):
    return [{'id':'hold:'+s['token']+':'+str(n),'summary':s['title']+'（保留時段）',
        'start':{'dateTime':s['start']},'end':{'dateTime':s['end']},'_planning_hold':True}
        for n,s in enumerate(slots(db)) if datetime.fromisoformat(s['start'])<end and datetime.fromisoformat(s['end'])>begin]


def release(db,chat_id,token):
    removed=[]
    def update(old):
        all_slots=(old or {}).get('slots',[])
        removed.extend(s for s in all_slots if s.get('token')==token and s.get('chat_id')==chat_id)
        return {'slots':[s for s in all_slots if not(s.get('token')==token and s.get('chat_id')==chat_id)]}
    db.mutate_task_action_state(KEY,update)
    return '已解除這次保留。已改排的行程會維持目前位置，可用 /today 查看。' if removed else '這次保留已解除或不屬於目前對話，沒有變更其他安排。'
