"""Non-executable reset clarification/status and stale-delivery protection."""
from datetime import datetime, timedelta
from html import escape
import re
from uuid import uuid4

from . import study_reset, event_reset

REMOVED = '/eventreset 已移除。請直接描述要重排的活動，例如「重排所有讀書行程」或「重排所有會議」。'
QUESTION = '要重排哪一類活動？例如讀書、會議或健身。'
EXPIRED = '這則重排要求已過期，已停止重試。仍需要重排時，請重新傳送新的要求。'


def receipt_reply(reply):
    messages=reply['messages'] if isinstance(reply,dict) else [{'text':reply}]
    return {'messages':[{**m,'reset_conversation':True} for m in messages]}


def removed_command(text):
    return bool(re.fullmatch(r'\s*/eventreset(?:@[A-Za-z0-9_]+)?(?:\s+[\s\S]*)?\s*',text,re.I))


def broad_request(text):
    text=text.strip().rstrip('。.!！?？')
    return bool(re.fullmatch(r'(?:請|请|幫我|帮我)?\s*(?:重排|重新安排|重新排)(?:我(?:的)?)?(?:全部|所有)(?:的)?(?:行程|活動|活动|日程)',text)
        or re.fullmatch(r'(?:replan|reschedule|rebuild|reset)\s+(?:all\s+(?:of\s+)?)?(?:my\s+)?(?:events|calendar|schedule)',text,re.I))


def study_alias(text):
    return bool(re.fullmatch(r'\s*study[ _-]*reset[。.!！\s]*',text,re.I))


def typed_request(text):
    m=re.fullmatch(r'\s*(?:請|请|幫我|帮我)?\s*重排(?:我(?:的)?)?(?:所有|全部)(?:的)?(.+?)(?:行程|活動|活动|日程)(?:[，,\s]+.*)?[。.!！\s]*',text)
    if not m:
        return None
    label=m[1].strip()
    if any(word in label for word in ('未完成','尚未','剩下')):
        return None
    try:
        return event_reset.normalize_type(label)
    except ValueError:
        return None


def waiting(text):
    return bool(re.fullmatch(r'\s*(?:(?:i(?:\s+am|.m)?\s+)?(?:am\s+)?(?:still\s+)?waiting(?:\s+bro)?|還在等|还在等|怎麼還沒好|怎么还没好|好了嗎|好了吗|進度|进度)[。.!！?？\s]*',text,re.I))


def bulk_language(text):
    return bool(re.search(r'重排|重新安排|重建|\b(?:reschedule|replan|rebuild|reset)\b',text,re.I))


def candidate(text):
    return removed_command(text) or study_alias(text) or broad_request(text) or waiting(text) or type_answer(text) is not None or bulk_language(text) or study_reset.command_args(text) is not None


def scope_question(state,now):
    return bool(state and state.get('expires_at','')>now.isoformat() and
        (state.get('reset_scope_question') or broad_request(state.get('instruction',''))))


def type_answer(text):
    value=text.strip().rstrip('。.!！').casefold()
    if value in {'study','study reset','study_reset','讀書','读书','讀書計畫','学习','學習'}:
        return 'study'
    if value in {'meeting','workout','work','social','exam','contest','other',*event_reset.ALIASES}:
        return event_reset.normalize_type(value)
    return None


def retire_unstarted(db,now):
    """Only release a preflight journal after its exclusive lease has ended."""
    def retire():
        state=db.get_task_action_state(study_reset.KEY) or {}
        lease=db.get_task_action_state('learning:tick') or {}
        if (state.get('status')=='planning' and lease.get('until','')<=now.isoformat()
                and not any(state.get(k) for k in ('snapshots','sessions','removed','deleted','inserted'))):
            db._save_task_action_state(study_reset.KEY,{**state,'status':'failed',
                'error':'Planning ended before execution; a fresh owner request is required.','ended_at':now.isoformat()})
            return True
        return False
    return db._run_task_action(retire)


async def prepare(planner,chat_id,text,update_id,now=None,request_date=None):
    now=now or datetime.now(planner.settings.tz)
    db=planner.db
    if removed_command(text):
        return REMOVED
    pending_key='intent:clarify:'+str(chat_id)
    pending=db.get_task_action_state(pending_key) or {}
    state=db.get_task_action_state(study_reset.KEY) or {}
    pending_scope=scope_question(pending,now)
    choice=type_answer(text) if pending_scope else None
    explicit=study_alias(text) or broad_request(text) or study_reset.looks_reset(text)
    bulk=explicit or bulk_language(text) or choice is not None or study_reset.command_args(text) is not None or study_reset.pending_reply(state,text)
    # Telegram retries retain the original message timestamp. Never turn an old failed delivery into a new bulk write.
    if request_date is not None and now.timestamp()-request_date>600 and (bulk or waiting(text) and state.get('status') in study_reset.ACTIVE|{'failed','question'}):
        retire_unstarted(db,now)
        return EXPIRED
    if waiting(text) and (pending_scope or state.get('status') in study_reset.ACTIVE|{'failed','question'}):
        if pending_scope and state.get('status') not in study_reset.ACTIVE|{'failed'}:
            return QUESTION
        if state.get('status')=='question':
            return escape(state.get('question') or QUESTION)
        if state.get('status')=='planning':
            lease=db.get_task_action_state('learning:tick') or {}
            if lease.get('until','')<=now.isoformat():
                retire_unstarted(db,now)
                return '先前重排在規劃階段中斷，尚未進入行程變更。請重新傳送新的重排要求。'
            return '正在規劃重排，目前尚未進入行程變更。'
        if state.get('status')=='failed':
            return '先前重排已停止，請重新傳送新的重排要求。'
        return ('重排'+('暫停' if state.get('status')=='partial' else '進行中')+
            f"：已確認刪除 {len(state.get('deleted',[]))} 個舊行程、建立 {len(state.get('inserted',[]))} 個新時段。")
    if broad_request(text):
        db.mutate_task_action_state(pending_key,lambda old:{'action':'event_reset','instruction':text,
            'question':QUESTION,'reset_scope_question':True,'candidate_ids':[],'snapshots':{},
            'expires_at':(now+timedelta(hours=2)).isoformat(),'token':uuid4().hex})
        return QUESTION
    if pending_scope and text.strip().lower() in {'cancel','/cancel','取消'}:
        db.mutate_task_action_state(pending_key,lambda old:{})
        return '已取消重排追問，行程未變更。'
    category='study' if study_alias(text) else choice or typed_request(text)
    if category:
        instruction=((pending.get('instruction','')+'\nOwner selected '+category) if pending_scope and (study_alias(text) or choice) else
                     'Rebuild all unfinished study sessions.' if study_alias(text) else text)
        result=await event_reset.run(planner,chat_id,category,instruction,update_id,now)
        if pending_scope:
            db.mutate_task_action_state(pending_key,lambda old:{} if old and old.get('token')==pending.get('token') else old)
        return result
    return None
