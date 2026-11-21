"""Source-aware read queries and durable, owner-requested cloud refresh."""
import asyncio
from datetime import datetime, timedelta
from html import escape
from uuid import uuid4

import google.auth
from google.auth.transport.requests import Request
import httpx

from .assignment_bridge import TRACKED_COURSES
from .assignment_scheduler import COURSE_NAMES
from .assignment_scan_state import AssignmentScanState
from .study_delivery import StudyDeliveryLedger
from .study_scheduler import delivery_outcome

KEY = 'assignment:interactive-request'
OUTCOME = 'assignment:last-outcome'
COURSES = {'security': '189717', 'computer-architecture': '189687', 'software-engineering': '188571',
    'graph-algorithms': '189756', 'database-systems': '189684', 'competitive-programming': '193842',
    'operating-systems': '192072'}
ACTIVE = {'queued', 'launching', 'running', 'launch_uncertain'}


def configured(settings):
    return bool(settings.assignment_scan_job and (settings.assignment_scan_project or settings.firestore_project_id))


def outstanding(db, course=None):
    records = []
    for key in db.list_assignment_keys():
        record = db.get_assignment(key)
        if not record or not record['task_exists']:
            continue
        item = record['assignment']
        if item.course_id not in TRACKED_COURSES or item.status == 'done':
            continue
        if course and item.course_id != COURSES[course]:
            continue
        records.append(record)
    return sorted(records, key=lambda r: (r['assignment'].deadline is None,
        r['assignment'].deadline.isoformat() if r['assignment'].deadline else '', r['task_id']))


def query(db, settings, *, course=None, page=1, now=None):
    if course is not None and course not in COURSES:
        raise ValueError('Unknown course')
    now = now or datetime.now(settings.tz)
    state = AssignmentScanState.from_record(db.get_assignment_scan_state())
    latest = db.get_task_action_state(OUTCOME) or {}
    request = db.get_task_action_state(KEY) or {}
    records = outstanding(db, course)
    lines = ['<b>TronClass 作業</b>']
    if course:
        lines.append(escape(COURSE_NAMES[COURSES[course]]))
    if state.last_success_at:
        observed = datetime.fromisoformat(state.last_success_at).astimezone(settings.tz)
        lines.append(f"<b>上次成功檢查</b> {observed:%m/%d %H:%M}（台北時間）")
        if now - observed > timedelta(hours=1):
            lines.append('資料已超過一小時，尚未即時重查。')
    else:
        lines.append('尚無成功檢查紀錄，無法判定現在有沒有新作業。')
    if state.consecutive_failures or latest.get('assignment_scan') in {'runner_failed', 'failure_recorded', 'failure_notified', 'failure_notice_pending'}:
        lines.append('最近一次檢查失敗；以下僅為已保存資料。')
    elif latest.get('assignment_scan') == 'paused_by_calendar':
        lines.append('最近一次檢查受掃描日期設定暫停，沒有重新確認來源。')
    elif latest.get('assignment_scan') == 'completed':
        count = latest.get('created', 0)
        lines.append(f"上次掃描新增 {count} 項作業（全課程範圍）。")
    if request.get('status') in ACTIVE:
        label = '啟動結果尚未確認，請勿重複啟動。' if request['status'] == 'launch_uncertain' else '已收到重新檢查要求，等待完成。'
        if request.get('expires_at', '') <= now.isoformat():
            label = '先前檢查要求已逾時，無法確認結果；可重新要求檢查。'
        lines.append(label)
    elif request.get('outcome', {}).get('assignment_scan') == 'launch_failed':
        lines.append('手動檢查未能啟動；請確認雲端掃描設定與權限。')
    lines += ['', f'<b>已追蹤且未結案</b> {len(records)} 項']
    pages = max(1, (len(records) + 4) // 5)
    if not 1 <= page <= pages:
        lines.append(f'目前共 {pages} 頁，請重新指定頁碼。')
    else:
        for record in records[(page - 1) * 5:page * 5]:
            item = record['assignment']
            due = item.deadline.astimezone(settings.tz).strftime('%m/%d %H:%M') if item.deadline else '尚未提供'
            url = f'https://tronclass.ntou.edu.tw/course/{item.course_id}/learning-activity#/{item.source_id}'
            lines += ['', f"<b>#{record['task_id']} {escape(item.title[:180])}</b>",
                escape(COURSE_NAMES[item.course_id]) + f' · 截止 {due}', f'<a href="{escape(url, quote=True)}">查看作業</a>']
        if not records:
            lines.append('目前沒有已追蹤的待結案作業；不代表 TronClass 現在一定沒有新作業。')
        if pages > 1:
            lines.append(f'第 {page}/{pages} 頁；/assignments [課程代號] 頁碼')
    message = {'text': '\n'.join(lines), 'parse_mode': 'HTML'}
    if configured(settings):
        message['reply_markup'] = {'inline_keyboard': [[{'text': '立即檢查並同步新作業', 'callback_data': 'assignments:refresh'}]]}
    return {'messages': [message]}


def request_refresh(db, settings, chat_id, update_id, now=None):
    now = now or datetime.now(settings.tz)
    if chat_id is None or chat_id != settings.telegram_chat_id or update_id is None:
        return '請在已授權的 Telegram 私人對話要求檢查。'
    if not configured(settings):
        return '尚未設定手動 TronClass 檢查，沒有啟動掃描。已保存作業仍可查詢。'
    token = uuid4().hex
    def transition(old):
        if old and old.get('status') in ACTIVE and old['expires_at'] > now.isoformat():
            return old
        if old and old.get('update_id') == update_id:
            return old
        if old and now < datetime.fromisoformat(old['created_at']) + timedelta(minutes=5):
            return old
        return {'token': token, 'chat_id': chat_id, 'update_id': update_id, 'status': 'queued',
            'created_at': now.isoformat(), 'expires_at': (now + timedelta(minutes=30)).isoformat()}
    saved = db.mutate_task_action_state(KEY, transition)
    text = ('已收到 TronClass 檢查要求。完成後會回覆結果，查到的新作業會同步到任務清單。'
            if saved['token'] == token else '已有檢查要求或剛完成檢查，沒有重複啟動。可查詢已保存結果。')
    return {'messages': [{'text': text, 'assignment_refresh': saved['token']}]}


async def launch(settings):
    def bearer():
        credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
        credentials.refresh(Request())
        return credentials.token
    token = await asyncio.wait_for(asyncio.to_thread(bearer), timeout=10)
    project = settings.assignment_scan_project or settings.firestore_project_id
    url = f'https://run.googleapis.com/v2/projects/{project}/locations/{settings.assignment_scan_region}/jobs/{settings.assignment_scan_job}:run'
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, headers={'Authorization': 'Bearer ' + token}, json={})
    if response.status_code >= 500:
        raise httpx.TransportError('Job launch result uncertain')
    response.raise_for_status()


async def dispatch(db, settings, telegram, token, now=None, launcher=None):
    now = now or datetime.now(settings.tz)
    claim = uuid4().hex
    state = db.mutate_task_action_state(KEY, lambda old:
        {**old, 'status': 'launching', 'launch_claim': claim} if old and old['token'] == token
        and old['status'] == 'queued' and old['expires_at'] > now.isoformat() else old)
    if not state or state.get('launch_claim') != claim:
        return
    try:
        await (launcher or launch)(settings)
    except (httpx.TransportError, TimeoutError):
        db.mutate_task_action_state(KEY, lambda old: {**old, 'status': 'launch_uncertain'}
            if old and old['token'] == token and old['status'] == 'launching' else old)
        return
    except Exception:
        await finish(db, telegram, now, {'assignment_scan': 'launch_failed'}, token=token)
        return
    db.mutate_task_action_state(KEY, lambda old: {**old, 'status': 'running'}
        if old and old['token'] == token and old['status'] == 'launching' else old)


async def finish(db, telegram, now, report, *, token=None):
    """Called by any finishing scan; overlapping runs can satisfy a pending request."""
    if report.get('assignment_scan') == 'already_running':
        return
    request = db.get_task_action_state(KEY)
    if not request or (token and request['token'] != token):
        return
    if request.get('status') in ACTIVE and request['expires_at'] <= now.isoformat():
        return
    if request['status'] in ACTIVE:
        status = report.get('assignment_scan', 'runner_failed')
        counts = {k: int(report[k]) for k in ('created', 'updated', 'existing') if type(report.get(k)) is int}
        count = counts.get('created', 0)
        text = (f"TronClass 檢查完成：這次新增 {count} 項作業。\n"
                + ('本次沒有新增；原有未完成作業仍保留。' if count == 0 else '新作業已同步至任務清單。')
                + f'\n已追蹤且未結案：{len(outstanding(db))} 項。' if status == 'completed' else
                'TronClass 檢查受掃描日期設定暫停，這次沒有重新確認有無新作業。' if status == 'paused_by_calendar' else
                'TronClass 手動檢查未能啟動，請確認雲端設定與權限。' if status == 'launch_failed' else
                'TronClass 這次檢查未完成，尚無法判定有無新作業。已保存資料未作為空結果回覆。')
        text += '\n資料檢查時間：' + now.strftime('%m/%d %H:%M') + '（台北時間）。可查詢待結案作業。'
        def complete(old):
            if not old or old['token'] != request['token'] or old['status'] not in ACTIVE:
                return old
            return {**old, 'status': 'completed', 'outcome': {'assignment_scan': status, **counts},
                    'result_text': text, 'completed_at': now.isoformat()}
        request = db.mutate_task_action_state(KEY, complete)
    if not request or not request.get('result_text'):
        return
    ledger = StudyDeliveryLedger(db)
    key = 'assignment:interactive-result:' + request['token']
    claim = ledger.claim(key, now)
    if not claim:
        return
    try:
        result = await telegram.send_message(request['chat_id'], request['result_text'])
    except Exception:
        ledger.finish(key, claim, now)
        return
    message_id, rejected = delivery_outcome(result)
    ledger.finish(key, claim, now, message_id=message_id, definitely_rejected=rejected)
