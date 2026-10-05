"""Explicit, idempotent preparation requests; no generation in webhook transactions."""
from hashlib import sha256
import json
import re

from .assignments import to_record


def prepare_action(db, command, now):
    match = re.fullmatch(r'prepare\s+([1-9][0-9]{0,18})', command)
    if not match:
        return lambda: '用法：/prepare 作業固定ID（作業通知中的編號，不是 /tasks 清單順位）。'
    task_id = int(match[1])
    def enqueue():
        record = next((record for key in db.list_assignment_keys()
                       if (record := db.get_assignment(key)) and record['task_id'] == task_id), None)
        if not record or not record['task_exists'] or record['assignment'].status == 'done':
            return '找不到未完成的作業；未建立準備工作。'
        item = record['assignment']
        if not item.description.strip():
            return '缺少作業說明，請先取得要求；不會憑標題生成草稿。'
        source = to_record(item)
        key = sha256(json.dumps({'source': source, 'version': 'prepare-v1'}, sort_keys=True).encode()).hexdigest()
        state = db.mutate_preparation(key, lambda previous: previous or {
            'status': 'queued', 'assignment': source, 'task_id': task_id,
            'requested_at': now.isoformat(), 'version': 'prepare-v1', 'draft': None,
            'last_error': None, 'attempt_count': 0})
        return (f"作業 #{task_id} 準備狀態：{state['status']}。"
                '由已配置額度的工作程式處理；目前只保存文字要求，未讀取附件，不會自動提交。')
    return enqueue
