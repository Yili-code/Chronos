"""Keep a pending study question from swallowing an unrelated owner request."""
import re
from datetime import datetime, timedelta
from html import escape
from uuid import uuid4
from .task_actions import task_fields
from .study_feedback import PROMPT


def new_request(text):
    from .task_focus import request
    if request(text, datetime.now().astimezone()):
        return True
    return bool(re.search(r'安排|重排|改排|排到|移到|留給|留给|空出|騰出|腾出|查詢|查询|列出|新增|新建|(?:移除|刪除|删除|取消).{0,20}(?:行程|計畫|计划|安排)|\b(?:schedule|reschedule|replan|move|reserve|list|add|create)\b', text, re.I))


def stale(planner, task, state):
    token=uuid4().hex
    ref={'task_id':task['id'],'session_token':state['token'],'snapshot':task_fields(task),
         'expires_at':(datetime.now(planner.settings.tz)+timedelta(minutes=10)).isoformat()}
    planner.db.mutate_task_action_state('learning:feedback-refresh:'+token,lambda _:ref)
    changes='、'.join(label for field,label in [('title','名稱'),('due_at','期限'),('project','標籤'),('timing','時間或內容')] if task_fields(task).get(field)!=(state.get('snapshot') or {}).get(field))
    return {'messages':[{'text':f'這則回報對應的任務{changes}已更新：{escape(task["title"])}。\n已退出舊回報，你可以繼續提出其他要求；若要回報目前這項任務，請按下方按鈕。',
        'parse_mode':'HTML','learning_context_closed':True,'reply_markup':{'inline_keyboard':[[
            {'text':'以目前內容重新回報','callback_data':f'learn:refreshfeedback:{task["id"]}:{token}'}]]}}]}


def refresh(planner, task_id, token):
    now=datetime.now(planner.settings.tz)
    def apply():
        ref=planner.db.get_task_action_state('learning:feedback-refresh:'+token) or {}
        task=planner.db.get_task(task_id)
        state=planner.get(task_id) or {}
        if (ref.get('task_id')!=task_id or ref.get('expires_at','')<=now.isoformat() or not task or task.get('status')!='open'
                or task_fields(task)!=ref.get('snapshot') or state.get('token')!=ref.get('session_token') or state.get('status')!='awaiting_feedback'):
            return '任務或回報狀態又有變更，請查看最新安排；這次沒有記錄進度。'
        updated={k:v for k,v in state.items() if k!='partial_draft'}
        updated.update(snapshot=task_fields(task),previous_feedback_snapshot=state.get('previous_feedback_snapshot',state.get('snapshot')))
        planner.db._save_task_action_state(f'learning:task:{task_id}',updated)
        planner.db._save_task_action_state('learning:feedback-refresh:'+token,{})
        return {'messages':[{'text':PROMPT,'reply_markup':{'force_reply':True},
            'learning_edit':{'task_id':task_id,'token':state['token'],'mode':'feedback'}}]}
    return planner.db._run_task_action(apply)
