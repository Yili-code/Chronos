"""Category-parameterized rebuild selection and planning over the shared executor."""
from datetime import datetime, timedelta, time
from hashlib import sha256
from html import escape
import json
import re
import time as clock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from . import study_reset as core
from .ai import AIError
from .google_calendar import CalendarError
from .study_reflow import bounds
from .task_actions import task_fields

ALIASES = {'學習':'study','学习':'study','讀書':'study','读书':'study',
           '工作':'work',
           '會議':'meeting','会议':'meeting','社交':'social','考試':'exam','考试':'exam',
           '競賽':'contest','竞赛':'contest','其他':'other','運動':'workout','运动':'workout','健身':'workout'}


def normalize_type(value):
    value = value.strip().casefold()
    value = ALIASES.get(value, value)
    if not re.fullmatch(r'[\w][\w-]{0,39}', value) or value in {'all','全部','所有'}:
        raise ValueError('請指定單一活動類型，例如 study、meeting 或 workout。')
    return value


async def resume(planner, chat_id, text, update_id, now=None):
    state = planner.db.get_task_action_state(core.KEY) or {}
    if state.get('event_type', 'study') == 'study':
        return await core.run(planner, chat_id, text, update_id, now)
    return await run(planner, chat_id, state['event_type'], text, update_id, now)


class ItemChoice(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    item_id: int
    selected: bool
    minutes: int = Field(ge=1, le=10080)


class ResetOutput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    actionable: bool
    include_past: bool | None = None
    start: str | None = None
    end: str | None = None
    start_hour: int | None = Field(default=None, ge=0, le=23)
    end_hour: int | None = Field(default=None, ge=1, le=24)
    weekdays: list[int] = Field(default_factory=lambda:list(range(7)), max_length=7)
    items: list[ItemChoice] = Field(default_factory=list, max_length=150)
    dependencies: list[core.Dependency] = Field(default_factory=list, max_length=500)
    question: str | None = None


def plan(planner, items, choices, order, edges, removed, fixed, output, start, stop, now):
    from .learning_calendar import find_slot, class_windows, overlaps
    from .backward_planning import reserved_windows
    cfg, db = planner.settings, planner.db
    policy = db.get_task_action_state('learning:course-calendar-policy')
    busy = [*fixed, *reserved_windows(db)]
    budgets = {}
    day = now.date()
    begin_hour = output.start_hour if output.start_hour is not None else cfg.learning_start_hour
    end_hour = output.end_hour if output.end_hour is not None else cfg.learning_end_hour
    if begin_hour >= end_hour or not output.weekdays or any(d not in range(7) for d in output.weekdays):
        raise ValueError('指定的可排時間或星期沒有可用時段，請調整條件。')
    config = cfg.model_copy(update={'learning_start_hour':begin_hour,'learning_end_hour':end_hour})
    while day <= stop.date():
        bucket = dict(db.get_task_action_state('learning:day:' + day.isoformat()) or {})
        for event in removed:
            bucket.pop(event.get('extendedProperties',{}).get('private',{}).get('chronosToken'),None)
        budgets[day.isoformat()] = sum(bucket.values())
        busy.extend(class_windows(day,cfg.tz,policy))
        day += timedelta(days=1)
    for task in db.list_open_tasks():
        saved = planner.get(task['id']) or {}
        if task['id'] not in items and saved.get('status') in {'queued','planning','proposed','editing','creating'} and saved.get('start') and saved.get('end'):
            a,b=(datetime.fromisoformat(saved[k]) for k in ('start','end'))
            busy.append((a,b))
            if a.date().isoformat() in budgets:
                budgets[a.date().isoformat()] += (b-a).total_seconds()/60
    for a,b in reserved_windows(db):
        if a.date().isoformat() in budgets:
            budgets[a.date().isoformat()] += (b-a).total_seconds()/60
    ends, sessions = {}, []
    for i in order:
        cursor = max([start,items[i].get('_not_before',start),*(ends[a] for a,b in edges if b==i)])
        remaining = choices[i]
        templates=list(items[i].get('_templates',[]))
        while remaining:
            template=templates.pop(0) if templates else None
            duration = (int((bounds(template,cfg.tz)[1]-bounds(template,cfg.tz)[0]).total_seconds()/60) if template else
                        remaining if items[i].get('_event') else min(180,remaining))
            slot, day = None, cursor.date()
            while day <= stop.date():
                if day.weekday() not in output.weekdays:
                    day += timedelta(days=1)
                    continue
                a = max(cursor,datetime.combine(day,time(begin_hour),cfg.tz),now+timedelta(minutes=10))
                b = min(stop,items[i].get('_deadline',stop),datetime.combine(day,time(end_hour%24),cfg.tz)+(timedelta(days=1) if end_hour==24 else timedelta()))
                if items[i].get('_event'):
                    a = a.replace(second=0,microsecond=0)+timedelta(minutes=(-a.minute)%15)
                    while a+timedelta(minutes=duration)<=b:
                        end=a+timedelta(minutes=duration)
                        if not overlaps(a,end,busy):
                            slot=(a,end)
                            break
                        a+=timedelta(minutes=15)
                elif a<b:
                    task = items[i]
                    if not task.get('due_at') and not (task.get('timing') or {}).get('due_date'):
                        task={**task,'due_at':stop.isoformat()}
                    slot=find_slot(task,now,busy,[],config,duration,budgets,policy,window=(a,b))
                if slot:
                    break
                day+=timedelta(days=1)
            if not slot:
                raise ValueError('保留原時長後無法排入指定期間，請放寬時間範圍或指定其他條件；尚未變更行程。')
            sessions.append({'task_id':i,'title':items[i]['title'],'start':slot[0].isoformat(),
                'end':slot[1].isoformat(),'minutes':duration,'original_minutes':choices[i],
                **({'template':template} if template else {})})
            if len(sessions)>200:
                raise ValueError('重建時段超過 200 個，請縮小範圍。')
            busy.append(slot)
            if not items[i].get('_event'):
                budgets[slot[0].date().isoformat()]+=duration
            remaining-=duration
            cursor=slot[1]
        ends[i]=cursor
    return sessions


async def run(planner, chat_id, event_type, instruction, update_id, now=None):
    event_type=normalize_type(event_type)
    now=now or datetime.now(planner.settings.tz)
    if event_type=='study':
        return await core.run(planner,chat_id,'Rebuild all unfinished study sessions in prerequisite order.\n'+instruction,update_id,now)
    db,cfg=planner.db,planner.settings
    prior=db.get_task_action_state(core.KEY) or {}
    token=sha256(f'event-reset:{event_type}:{chat_id}:{update_id}'.encode()).hexdigest()[:32]
    if prior.get('status') in core.ACTIVE and prior.get('event_type','study')!=event_type:
        return '另一類型的重建尚待恢復，請先完成 '+escape(prior.get('event_type','study'))+' 的操作。'
    if prior.get('chat_id',chat_id)!=chat_id and prior.get('status') in core.ACTIVE|{'question'}:
        return '另一個對話的重建尚未完成。'
    if prior.get('token')==token and prior.get('status')=='completed':
        return core.result(prior)
    if prior.get('status')=='question' and instruction.strip().lower() in {'cancel','/cancel','取消'}:
        core.save(planner,{**prior,'status':'cancelled'})
        return '已取消，行程未變更。'
    from .calendar_replan import key
    if (db.get_task_action_state(key(chat_id)) or {}).get('status')=='creating':
        return '請先完成目前 Calendar 操作的恢復核對。'
    lease=uuid4().hex
    deadline=clock.monotonic()+540
    tick=db.mutate_task_action_state('learning:tick',lambda old:{'claim':lease,'until':(now+timedelta(minutes=10)).isoformat()}
        if not old or old['until']<=now.isoformat() else old)
    if tick['claim']!=lease:
        return '排程正在處理其他操作，請稍後再試。'
    try:
        if prior.get('status') in {'applying','partial'}:
            state=prior
        else:
            state={'status':'planning','token':token,'chat_id':chat_id,'event_type':event_type,
                'instruction':instruction,'updated_at':now.isoformat()}
            db.mutate_task_action_state(core.KEY,lambda old:state)
            await planner.calendar.verify_owner()
            stop=now+timedelta(days=30)
            future_only=bool(re.search(r'只排未來|只排未来|不含過去|不含过去|future only',instruction,re.I)
                or prior.get('status')=='question' and instruction.strip().lower() in {'no','不要過去的','不要过去的'})
            agenda=await planner.calendar.agenda(now if future_only else now-timedelta(days=30),stop)
            tagged=(await planner.calendar.category_events(event_type,start=now,end=stop) if future_only else
                    await planner.calendar.category_events(event_type))
            events={('writable' if core.writable(e,cfg) else e.get('_calendar_id'),e['id']):e
                for e in [*agenda,*tagged] if e.get('status')!='cancelled'}
            tasks={t['id']:t for t in db.list_open_tasks()}
            candidates={i:{**t,'kind':'task'} for i,t in tasks.items()}
            for n,e in enumerate(events.values(),1):
                if not e.get('extendedProperties',{}).get('private',{}).get('chronosTask'):
                    candidates[-n]={'id':-n,'title':e.get('summary','Untitled event'),'kind':'event','_event':e}
            if len(candidates)>150:
                raise ValueError('候選項目過多，請縮小活動類型或時間範圍。')
            context=prior if prior.get('status')=='question' and prior.get('event_type')==event_type else None
            payload=[{'id':i,'kind':t['kind'],**task_fields(t),
                **({'event':{**{k:t['_event'].get(k) for k in ('id','summary','start','end')},
                    'category':t['_event'].get('extendedProperties',{}).get('private',{}).get('chronosEventCategory')},
                    'can_reset':bool(core.deletable(t['_event'],cfg))} if t.get('_event') else {})} for i,t in candidates.items()]
            output=ResetOutput.model_validate(await planner.ai.parse_event_reset(event_type,instruction,payload,
                {'clarification':context,'constraints':core.constraints(planner)},now))
            if not output.actionable or output.question:
                raise ValueError(output.question or '請補充要重排的活動與範圍。')
            if future_only:
                output.include_past=False
            ids=[c.item_id for c in output.items]
            if len(ids)!=len(set(ids)) or set(ids)!=set(candidates):
                raise ValueError('活動選取資料不完整，請重新指定類型與範圍。')
            choices={c.item_id:c.minutes for c in output.items if c.selected}
            items={i:candidates[i] for i in choices}
            if not items or len(items)>30:
                raise ValueError('請指定一至三十項可重排的活動；沒有符合條件時不會改動其他類型。')
            start=datetime.fromisoformat(output.start) if output.start else now
            stop=datetime.fromisoformat(output.end) if output.end else stop
            if start.tzinfo is None or stop.tzinfo is None or not now<=start<stop<=now+timedelta(days=30):
                raise ValueError('請指定未來 30 天內的新排程範圍。')
            removed,snapshots,past=[],{},False
            for i,item in items.items():
                old=planner.get(i) or {} if i>0 else {}
                if old.get('status') in {'creating','backward_managed','reset_pending'} or str(old.get('delivery','')).endswith(('_uncertain','_sending')):
                    raise ValueError('選取項目有尚待恢復的操作，請先完成核對。')
                source=[item['_event']] if i<0 else await planner.calendar.task_events(i)
                selected=[]
                for event in source:
                    props=event.get('extendedProperties',{}).get('private',{})
                    category=props.get('chronosEventCategory')
                    if category and normalize_type(category) not in {event_type,'other'}:
                        raise ValueError('選取結果包含其他已標示類型，請重新指定活動。')
                    if i>0 and (props.get('chronosTask')!=str(i) or not props.get('chronosToken')):
                        raise ValueError('任務行程歸屬無法核對。')
                    history=db.get_task_action_state('learning:history:'+props.get('chronosToken','')) or {} if i>0 else {}
                    if history.get('feedback',{}).get('outcome')=='done':
                        continue
                    a,b=bounds(event,cfg.tz)
                    if (b-a).total_seconds()<=0 or (b-a).total_seconds()%60:
                        raise ValueError('活動時長不是正整數分鐘，請先確認時間；不會自動改變原時長。')
                    if a<now<b:
                        raise ValueError('有活動正在進行，請結束後再重排。')
                    if b<=now:
                        past=True
                        if output.include_past is False:
                            continue
                    if not core.deletable(event,cfg):
                        raise ValueError('共享、重複或非本人擁有的活動不能自動刪除重建，請排除這些活動。')
                    if event.get('attachments') or event.get('conferenceData'):
                        raise ValueError('含附件或線上會議資料的活動需先確認保留方式；目前不會刪除重建。')
                    selected.append(event)
                if i<0 and not selected:
                    continue
                if i>0 and old.get('event_id') and old.get('end','')>now.isoformat() and old['event_id'] not in {e['id'] for e in selected}:
                    raise ValueError('現有任務行程無法完整核對，請先重新整理。')
                removed.extend(selected)
                snapshot={'task':task_fields(item),'state':old or None}
                if i<0:
                    event=selected[0]
                    snapshot['event']=event
                    free_token=event.get('extendedProperties',{}).get('private',{}).get('chronosFreeToken')
                    booking=db.get_task_action_state('free:booking:'+free_token) if free_token else None
                    if booking and (booking.get('status')!='booked' or booking.get('event_id')!=event['id']):
                        raise ValueError('活動預約紀錄需要先恢復核對。')
                    snapshot['booking']=booking
                    choices[i]=max(1,int((bounds(event,cfg.tz)[1]-bounds(event,cfg.tz)[0]).total_seconds()/60))
                else:
                    explicit=(item.get('timing') or {}).get('estimated_minutes')
                    selected.sort(key=lambda e:bounds(e,cfg.tz)[0])
                    choices[i]=max(explicit or 0,sum(int((bounds(e,cfg.tz)[1]-bounds(e,cfg.tz)[0]).total_seconds()/60) for e in selected)) or choices[i]
                    item['_templates']=selected
                snapshots[str(i)]=snapshot
            items={i:items[i] for i in choices if str(i) in snapshots}
            choices={i:choices[i] for i in items}
            if past and output.include_past is None:
                raise ValueError('是否包含已過期、但未確認完成的 '+event_type+' 活動？請回答「包含」或「只排未來」。')
            if not items or len(removed)>100:
                raise ValueError('沒有可重建活動，或舊行程超過 100 個；請縮小範圍。')
            dependencies,anchors=[],{}
            for relation in output.dependencies:
                a,b=relation.before,relation.after
                if a in items and b in items:
                    dependencies.append(relation)
                    continue
                fixed_id=b if a in items else a if b in items else None
                target=a if a in items else b
                fixed_item=candidates.get(fixed_id) or {}
                event=fixed_item.get('_event')
                if not event or fixed_id in items:
                    raise ValueError('前置關係指向無法核對的項目，請重新指定順序。')
                ea,eb=bounds(event,cfg.tz)
                anchors[(event.get('_calendar_id'),event['id'])]=event
                if a in items:
                    items[target]['_deadline']=min(items[target].get('_deadline',stop),ea)
                    if target>0:
                        due=items[target].get('due_at') or (items[target].get('timing') or {}).get('due_date')
                        old_due=(datetime.fromisoformat(due) if due and len(due)>10 else
                            datetime.combine(datetime.fromisoformat(due).date(),time.max,cfg.tz) if due else stop)
                        items[target]['due_at']=min(old_due,ea).isoformat()
                else:
                    items[target]['_not_before']=max(items[target].get('_not_before',start),eb)
            order,edges=core.ordered(items,dependencies)
            removed_ids={e['id'] for e in removed}
            fixed=[bounds(e,cfg.tz) for e in agenda if e.get('transparency')!='transparent' and not (e['id'] in removed_ids and core.writable(e,cfg))]
            sessions=plan(planner,items,choices,order,edges,removed,fixed,output,start,stop,now)
            for n,s in enumerate(sessions):
                s['event_id']=sha256(f'event-reset:{token}:{n}'.encode()).hexdigest()
                s['token']=sha256(f'event-reset-token:{token}:{n}'.encode()).hexdigest()[:32]
            state={**state,'status':'applying','snapshots':snapshots,'removed':removed,'sessions':sessions,
                'factor':1,'deleted':[],'inserted':[],'constraints':core.constraints(planner),'exams':list(anchors.values())}
            if len(json.dumps(state,ensure_ascii=False).encode('utf-8'))>700_000:
                raise ValueError('重建紀錄內容過大，請縮小活動範圍；尚未刪除任何行程。')
            core.save(planner,state)
        return await core.execute(planner,state,now,lease,deadline)
    except (ValueError,AIError,CalendarError) as error:
        state=db.get_task_action_state(core.KEY) or state
        if state.get('status') in {'applying','partial'}:
            state={**state,'status':'partial','error':str(error)}
            core.save(planner,state)
            return core.result(state)
        core.save(planner,{**state,'status':'question','question':str(error)})
        return escape(str(error))+'\n尚未刪除或建立行程。'
    finally:
        db.mutate_task_action_state('learning:tick',lambda old:{**old,'until':now.isoformat()} if old and old.get('claim')==lease else old)
