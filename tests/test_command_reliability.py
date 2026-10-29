"""100 synthetic webhook commands, with opt-in real-model capture and store replay.

No production Calendar, database or Telegram client is used. Live capture reads
only model credentials into memory; its outputs contain fixed synthetic data.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import time
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from chronos import main, intents, calendar_replan
from chronos.ai import AIError, ExternalAI
from chronos.db import Database
from chronos.learning_calendar import LearningCalendar
from chronos.settings import Settings
from chronos.study_reflow import bounds
from chronos.task_actions import task_fields
from chronos.tasks import TaskService
from test_firestore import database as firestore_database

FIXTURES = Path(__file__).parent / 'fixtures'
CASES = json.loads((FIXTURES / 'command_reliability_cases.json').read_text(encoding='utf-8'))
CAPTURE = FIXTURES / 'command_reliability_model.json'
NOW = datetime.fromisoformat('2026-10-29T12:28:00+08:00')
LIVE = os.environ.get('CHRONOS_COMMAND_LIVE') == '1'


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz)


@pytest.fixture(scope='session')
def command_model_config():
    if not LIVE:
        return None
    gc = str(Path(os.environ['LOCALAPPDATA']) / 'Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd')
    def cloud(*args):
        result = subprocess.run([gc, *args, '--project', 'yili-chronos-prod', '--quiet'],
            capture_output=True, text=True, encoding='utf-8')
        if result.returncode:
            raise RuntimeError('Read-only model configuration query failed')
        return result.stdout.strip()
    service = json.loads(cloud('run', 'services', 'describe', 'chronos', '--region', 'asia-east1', '--format=json'))
    values = {e['name']: e for e in service['spec']['template']['spec']['containers'][0]['env']}
    def value(name, default=''):
        entry = values.get(name)
        if not entry:
            return default
        if 'value' in entry:
            return entry['value']
        ref = entry['valueFrom']['secretKeyRef']
        return cloud('secrets', 'versions', 'access', ref['key'], '--secret', ref['name'])
    return Settings(_env_file=None, gemini_api_key=value('CHRONOS_GEMINI_API_KEY'),
        gemini_api_key_secondary=value('CHRONOS_GEMINI_API_KEY_SECONDARY'),
        gemini_model=value('CHRONOS_GEMINI_MODEL', 'gemini-3.5-flash-lite'),
        gemini_api_base=value('CHRONOS_GEMINI_API_BASE', 'https://generativelanguage.googleapis.com/v1beta'))


class ScenarioAI(ExternalAI):
    def __init__(self, config, captured):
        super().__init__(config or Settings(_env_file=None), usage=self)
        self.outputs, self.captured, self.cursor = [], captured, 0
        self.provider_usage = []

    def record(self, subject, **values):
        self.provider_usage.append({'subject':subject, **{k:v for k,v in values.items() if k!='now'}})

    async def _generate_output(self, prompt, text, schema, output_model, subject, **kwargs):
        if LIVE:
            result = await super()._generate_output(prompt, text, schema, output_model, subject, **kwargs)
        else:
            assert self.cursor < len(self.captured), f'No recorded model output for {subject}'
            entry = self.captured[self.cursor]
            assert entry['subject'] == subject, (entry['subject'], subject)
            self.cursor += 1
            result = output_model.model_validate(entry['output'])
        self.outputs.append({'subject': subject, 'output': result.model_dump(mode='json')})
        return result


def text_of(receipt):
    return '\n'.join(m['text'] for m in receipt.get('messages', [])) if receipt.get('messages') else receipt.get('reply','')


@pytest.fixture(params=['sqlite', 'firestore'])
def command_scenario(request, tmp_path, monkeypatch, command_model_config):
    db = firestore_database(monkeypatch) if request.param == 'firestore' else Database(tmp_path / 'commands.db')
    db.initialize()
    cfg = Settings(_env_file=None, enable_learning_calendar=True, enable_internal_scheduler=False,
        learning_auto_accept=False, telegram_chat_id=123, telegram_webhook_secret='test-hook',
        scheduler_secret='test-scheduler', calendar_account='owner@example.com',
        learning_enabled_since=(NOW-timedelta(days=30)).isoformat())
    calendar, bot = AsyncMock(), AsyncMock()
    calendar.configured = lambda: True
    events = {}
    def agenda(*args):
        return [deepcopy(e) for e in events.values() if len(args)<2 or
                (bounds(e,cfg.tz)[0]<args[1] and bounds(e,cfg.tz)[1]>args[0])]
    calendar.agenda.side_effect = agenda
    calendar.busy.side_effect = lambda *args: [bounds(e,cfg.tz) for e in agenda(*args)]
    calendar.event.side_effect = lambda event_id: deepcopy(events.get(event_id))
    calendar.search_events.side_effect = lambda query: [deepcopy(e) for e in events.values() if query.lower() in e.get('summary','').lower()]
    calendar.managed_events.side_effect = agenda
    calendar.task_events.side_effect = lambda task_id: [deepcopy(e) for e in events.values() if e.get('extendedProperties',{}).get('private',{}).get('chronosTask')==str(task_id)]
    async def move(event_id, body, etag):
        assert events[event_id]['etag']==etag
        events[event_id].update(deepcopy(body)); events[event_id]['etag']+='x'
        return deepcopy(events[event_id])
    async def insert(body):
        assert body['id'] not in events
        events[body['id']]={**deepcopy(body),'etag':'v1','_calendar_id':'primary',
            'creator':{'email':'owner@example.com'},'organizer':{'self':True}}
        return deepcopy(events[body['id']])
    async def delete(event_id, etag):
        assert events[event_id]['etag']==etag
        events.pop(event_id)
    calendar.move_event.side_effect=move;calendar.insert.side_effect=insert;calendar.delete_event.side_effect=delete
    counter=[500]
    async def send(*args, **kwargs):
        counter[0]+=1
        return {'ok':True,'result':{'message_id':counter[0]}}
    bot.send_message.side_effect=send
    bot.request.return_value={'ok':True}
    bot.answer_callback_query.return_value={'ok':True}
    tasks=TaskService(db,cfg.tz)
    seed=db.create_task('Closed seed',None,None,NOW,{})
    db.complete_task(seed['id'],NOW)
    aliases={}
    for name,title,minutes,tag in [
        ('db2','Database systems Chapter 2',60,'db'),('os','Operating Systems lecture 3',67,'os'),
        ('ispm1','ISPM Chapter 1',90,'ispm'),('ispm2','ISPM Chapter 2',45,'ispm'),
        ('cp','Competition programming past papers',90,'cp'),('db3','Database systems Chapter 3',60,'db'),
        ('db4','Database systems Chapter 4',60,'db'),('db5','Database systems Chapter 5',60,'db'),
        ('past','Database systems midterm past papers',90,'db'),('report','Database report',60,'db'),
        ('register','Register Orion and YC',30,None)]:
        aliases[name]=db.create_task(title,None,tag,NOW,{'estimated_minutes':minutes})
    # The planner AI is installed per case; all transports are isolated here.
    planner=LearningCalendar(db,calendar,bot,cfg)
    for name,hour,minute,duration in [('os',13,0,67),('ispm1',14,15,90),('ispm2',15,45,45)]:
        task=aliases[name];start=NOW.replace(hour=hour,minute=minute);end=start+timedelta(minutes=duration)
        token,event_id='synthetic-'+name,'event-'+name
        planner.update(task['id'],lambda _,t=task,s=start,e=end,m=duration,k=token,i=event_id:{'status':'scheduled','token':k,
            'event_id':i,'minutes':m,'snapshot':task_fields(t),'start':s.isoformat(),'end':e.isoformat()})
        events[event_id]={'id':event_id,'etag':'v1','_calendar_id':'primary','summary':task['title'],
            'creator':{'email':'owner@example.com'},'organizer':{'self':True},
            'start':{'dateTime':start.isoformat()},'end':{'dateTime':end.isoformat()},
            'extendedProperties':{'private':{'chronosTask':str(task['id']),'chronosToken':token}}}
        db.mutate_task_action_state('learning:day:'+NOW.date().isoformat(),lambda old,k=token,m=duration:{**(old or {}),k:m})
    events['fixed-exam']={'id':'fixed-exam','etag':'v1','_calendar_id':'primary','summary':'Competition programming midterm',
        'creator':{'email':'owner@example.com'},'organizer':{'self':True},
        'start':{'dateTime':NOW.replace(hour=18,minute=30).isoformat()},
        'end':{'dateTime':NOW.replace(hour=21,minute=30).isoformat()},
        'extendedProperties':{'private':{'chronosEventCategory':'exam'}}}
    monkeypatch.setattr(planner,'tick',AsyncMock(return_value={}))
    for key,value in {'db':db,'settings':cfg,'tasks':tasks,'google_calendar':calendar,'telegram':bot,
                      'learning_calendar':lambda:planner}.items():
        monkeypatch.setattr(main,key,value)
    # Use a single explicit local reference time throughout every command.
    import sys
    for module in list(sys.modules.values()):
        if module and getattr(module,'__name__','').startswith('chronos.') and getattr(module,'datetime',None) is datetime:
            monkeypatch.setattr(module,'datetime',Clock)
    with TestClient(main.app,raise_server_exceptions=True) as client:
        yield {'db':db,'cfg':cfg,'tasks':tasks,'planner':planner,'calendar':calendar,'events':events,
               'aliases':aliases,'client':client,'bot':bot,'backend':request.param}


def configure_profile(s,case):
    db,planner=s['db'],s['planner'];profile=case.get('profile')
    if profile in {'feedback','stale_feedback'}:
        task=s['aliases']['db2'];token='feedback-token'
        planner.update(task['id'],lambda _:{'status':'awaiting_feedback','token':token,'minutes':60,
            'event_id':'past-feedback','snapshot':task_fields(task),
            'start':(NOW-timedelta(hours=2)).isoformat(),'end':(NOW-timedelta(hours=1)).isoformat()})
        ref={'task_id':task['id'],'token':token,'mode':'feedback','expires_at':(NOW+timedelta(hours=2)).isoformat()}
        db.mutate_task_action_state('learning:edit-chat:123',lambda _:ref)
        if profile=='stale_feedback':db.edit_task_with_undo(task['id'],'Database systems Chapter 2 updated',None,'db',task['timing'])
    if profile=='clarification':
        db.mutate_task_action_state('intent:clarify:123',lambda _:{'token':'old-question','instruction':'修改資料庫報告',
            'question':'要改標題還是內容？','candidate_ids':[s['aliases']['report']['id']],
            'expires_at':(NOW+timedelta(minutes=10)).isoformat()})
    if profile=='registration':
        db.mutate_task_action_state('intent:recent:123',lambda _:{'action':'task_create','task_ids':[s['aliases']['register']['id']],
            'instruction':'報名 Orion 和 YC','expires_at':(NOW+timedelta(minutes=10)).isoformat()})
    if profile=='pending_review':
        db.mutate_task_action_state(calendar_replan.key(123),lambda _:{'status':'pending','token':'old-review',
            'chat_id':123,'start':NOW.replace(hour=13).isoformat(),'end':NOW.replace(hour=18).isoformat(),
            'calendar_moves':[],'expires_at':(NOW+timedelta(hours=1)).isoformat(),'message_id':400})
    if profile=='report_due':
        task=s['aliases']['report']
        db.edit_task(task['id'],task['title'],NOW+timedelta(days=4),task['project'],{'content':'Existing details'})


@pytest.mark.parametrize('case',CASES,ids=lambda c:c['id']+'-'+c['family'])
def test_high_risk_command(command_scenario,command_model_config,monkeypatch,case):
    s=command_scenario;db=s['db'];configure_profile(s,case)
    recorded=json.loads(CAPTURE.read_text(encoding='utf-8')) if CAPTURE.exists() else {}
    ai=ScenarioAI(command_model_config,recorded.get(case['id'],[]))
    monkeypatch.setattr(main,'ai',ai);s['planner'].ai=ai
    positions={t['id']:n for n,t in enumerate(s['tasks'].list_open(),1)}
    substitutions={name+suffix:value for name,t in s['aliases'].items() for suffix,value in [('_id',t['id']),('_pos',positions[t['id']])]}
    command=case['text'].format(**substitutions)
    before=deepcopy(db.list_open_tasks());before_events=deepcopy(s['events'])
    before_plans={t['id']:s['planner'].get(t['id']) for t in before}
    before_review=db.get_task_action_state(calendar_replan.key(123))
    from chronos.reminders import KEY as reminder_key
    before_reminders=db.get_task_action_state(reminder_key)
    payload={'update_id':700001,'message':{'message_id':700001,'chat':{'id':123},'text':command}}
    output_dir=Path('.pytest-tmp/command-results');output_dir.mkdir(parents=True,exist_ok=True)
    result={'id':case['id'],'family':case['family'],'command':command,'backend':s['backend'],'live':LIVE}
    try:
        response=s['client'].post('/telegram/webhook',headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'},json=payload)
        assert response.status_code==200,response.text
        receipt=db.get_update(700001);reply=text_of(receipt)
        result['reply']=reply;result['model_outputs']=ai.outputs;result['messages']=receipt.get('messages',[])
        # Every command must remain idempotent through actual webhook receipts.
        after=deepcopy(db.list_open_tasks());events_after=deepcopy(s['events']);calls=len(ai.outputs)
        deliveries=s['bot'].send_message.await_count
        duplicate=s['client'].post('/telegram/webhook',headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'},json=payload)
        assert duplicate.status_code==200,duplicate.text
        assert db.list_open_tasks()==after and s['events']==events_after and len(ai.outputs)==calls
        assert s['bot'].send_message.await_count==deliveries,'A delivered update was sent again'
        expect=case['expect'];target=s['aliases'].get(case.get('target'));review=db.get_task_action_state(calendar_replan.key(123))
        result['review']=review;result['recent']=db.get_task_action_state('intent:recent:123')
        if case['expect'] in {'readonly','query','availability'}:
            assert {t['id']:s['planner'].get(t['id']) for t in before}==before_plans,'Read-only command changed a work plan'
            assert review==before_review,'Read-only command created or replaced a scheduling proposal'
            assert db.get_task_action_state(reminder_key)==before_reminders,'Read-only command changed reminders'
            assert not any(m.get('free_booking') or m.get('planning_requested') for m in receipt.get('messages',[])),reply
        if case['expect']=='query':
            assert not any(w in reply for w in ['could not recognise','目前無法理解','未能完成']),reply
            if case.get('query_start'):
                call=s['calendar'].agenda.await_args
                assert call and call.args[0].astimezone(s['cfg'].tz).isoformat()==case['query_start'],reply
                assert call.args[1].astimezone(s['cfg'].tz).isoformat()==case['query_end'],reply
        if case['expect']=='availability':
            assert f"共 {case['expected_minutes']} 分鐘" in reply,reply
            assert '14:07–14:15' in reply and '16:30–17:00' in reply,reply
        if expect=='complete':
            assert db.get_task(target['id'])['status']=='done',reply
            assert {t['id'] for t in after}=={t['id'] for t in before}-{target['id']}
        elif expect=='progress':
            saved=s['planner'].get(target['id'])
            assert saved.get('feedback',{}).get('actual_minutes')==120,(saved,reply)
            assert saved.get('progress',{}).get('completed_pages')==20,(saved,reply)
            successor=saved.get('successor_task_id')
            assert successor and db.get_task(successor)['status']=='open',(saved,reply)
            assert s['planner'].get(successor)['progress']['next_page']==21
            assert len(after)==len(before)
        elif expect=='remaining':
            current=db.get_task(target['id'])
            assert len(after)==len(before) and current['title']==target['title']
            if review and review.get('study_revision'):
                matching=[a for a in [*review.get('revision_additions',[]),*review.get('calendar_moves',[])] if a.get('task_id')==target['id']]
                assert matching and all(a.get('minutes')==90 for a in matching),review
            else:
                content=current['timing'].get('content','')
                assert '90' in content and '30' not in content,(current,reply)
        elif expect=='create':
            assert len(after)==len(before)+case.get('delta',1),reply
            if case.get('contains'):assert any(case['contains'] in t['title'] for t in after if t['id'] not in {x['id'] for x in before}),reply
        elif expect=='edit':
            current=db.get_task(target['id']);old=next(t for t in before if t['id']==target['id'])
            assert len(after)==len(before),reply
            for field in ('title','project','due'):
                if field in case:assert current['due_at' if field=='due' else field]==case[field],(current,reply)
            if case.get('content'):assert case['content'] in current['timing'].get('content',''),(current,reply)
            if case.get('no_content'):assert not current['timing'].get('content'),(current,reply)
            if case.get('no_project'):assert not current['project'],(current,reply)
            if case.get('no_due'):assert not current['due_at'],(current,reply)
            if 'title' not in case:assert current['title']==old['title']
            assert all(t==next(x for x in before if x['id']==t['id']) for t in after if t['id']!=target['id'])
        else:
            assert after==before,reply
        if expect=='focus':
            assert review and (review.get('focus_task') or {}).get('id')==target['id'],reply
            assert review['focus_reservation']['title']==target['title'],reply
            expected_day=(NOW+timedelta(days=1 if case.get('tomorrow') else 0)).date().isoformat()
            assert review['start']==expected_day+'T13:00:00+08:00' and review['end']==expected_day+'T18:00:00+08:00',review
        if expect in {'study','study_or_capacity'}:
            assert review and review.get('study_revision') or (expect=='study_or_capacity' and any(w in reply for w in ['不足','額度','期限','No slot','budget','limit'])),reply
            if review and 'break_minutes' in case:assert review['break_minutes']==case['break_minutes'],review
        if expect=='capacity':
            assert not review,reply
            assert {t['id']:s['planner'].get(t['id']) for t in before}==before_plans
            assert any(w in reply for w in ['不足','截止','已過','過了','passed','超過','limit','無法','容量']),reply
        if expect=='blocked':
            assert any(w in reply for w in ['conflict','不符合','No alternative','No slot','無法','找不到','unchanged']),reply
        if expect in {'reschedule','reschedule_window'}:
            current=s['planner'].get(target['id'])
            if expect=='reschedule':
                assert current['start']==case['start'] and current['minutes']==case['minutes'],(current,reply)
            else:assert current['start'][:10]=='2026-10-30',reply
        if expect=='slot':assert review and review.get('task_slot'),reply
        if expect=='cancel':
            assert not db.get_task_action_state('learning:edit-chat:123'),reply
            assert not review or review.get('status') in {'cancelled','expired'},reply
        if expect=='date_followup':
            assert not db.get_task_action_state('intent:clarify:123'),reply
            assert len(after)==len(before),reply
        if expect=='reminder':
            from chronos.reminders import KEY
            assert db.get_task_action_state(KEY),reply
        if expect=='event':
            tokens=[m['free_booking'] for m in receipt.get('messages',[]) if m.get('free_booking')]
            assert len(tokens)==1,reply
            event_review=db.get_task_action_state('free:booking:'+tokens[0])
            assert event_review and event_review['status']=='pending' and event_review['explicit_event'],event_review
        if expect not in {'reschedule','reschedule_window'}:
            assert s['events']==before_events,'Calendar changed before confirmation'
            s['calendar'].move_event.assert_not_awaited();s['calendar'].insert.assert_not_awaited();s['calendar'].delete_event.assert_not_awaited()
        if expect=='replan_safe' and review:
            assert all(m['event_id']!='fixed-exam' for m in review.get('calendar_moves',[])),review
        result['passed']=True
    except BaseException as error:
        result['passed']=False;result['failure_type']=type(error).__name__;result['failure']=str(error)[:2400]
        raise
    finally:
        result['model_outputs']=ai.outputs
        result['provider_usage']=ai.provider_usage
        (output_dir/(case['id']+'-'+s['backend']+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
        attempts=Path('.pytest-tmp/command-attempts');attempts.mkdir(parents=True,exist_ok=True)
        (attempts/(str(time.time_ns())+'-'+case['id']+'-'+s['backend']+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
        if LIVE:
            recorded[case['id']]=ai.outputs
            CAPTURE.write_text(json.dumps(recorded,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
