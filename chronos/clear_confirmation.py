"""A clear-task confirmation belongs to one live request, not every old button."""
from datetime import timedelta
from hashlib import sha256


def key(chat_id):
    return 'clear:chat:'+str(chat_id)


def preview(db,chat_id,update_id,now,text):
    token=sha256(f'clear:{chat_id}:{update_id}'.encode()).hexdigest()[:32]
    state={'token':token,'chat_id':chat_id,'update_id':update_id,'status':'pending',
           'expires_at':(now+timedelta(minutes=10)).isoformat()}
    def save():
        old=db.get_task_action_state(key(chat_id)) or {}
        if old.get('update_id',-1)>update_id:
            return '已有較新的清除確認，請使用最新的確認卡。沒有刪除任務。'
        db._save_task_action_state(key(chat_id),state)
        return {'messages':[{'text':text,'reply_markup':{'inline_keyboard':[
            [{'text':'Delete all tasks','callback_data':'clear:confirm:'+token}],
            [{'text':'Cancel','callback_data':'clear:cancel:'+token}]]}}]}
    return db._run_task_action(save)


def respond(db,chat_id,action,token,now,clear):
    if action=='cancel' and not token:
        return 'Understood. Nothing was deleted.'
    def apply():
        state=db.get_task_action_state(key(chat_id)) or {}
        if (not token or state.get('token')!=token or state.get('chat_id')!=chat_id
                or state.get('status')!='pending' or state.get('expires_at','')<=now.isoformat()):
            return '這張清除確認已失效，沒有刪除任務。若仍要清除，請重新使用 /clear。'
        if action=='cancel':
            db._save_task_action_state(key(chat_id),{**state,'status':'cancelled'})
            return 'Understood. Nothing was deleted.'
        # clear() deletes this approval state with the tasks in the same receipt
        # transaction. Never write first: Firestore requires all reads first.
        return clear()
    return db._run_task_action(apply)
