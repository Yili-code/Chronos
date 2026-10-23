"""Owner-approved task wording and course tags; suggestions never mutate tasks."""
from datetime import datetime, timedelta
from dataclasses import replace
from html import escape
import re
from uuid import uuid4

from .ai import AIError, TaskReviewOutput
from .course_tracking import COURSE_SCHEDULE
from .task_actions import task_fields
from .tasks import is_course_scope_task, project_display_name


COURSE_TAGS = {'security': 'ispm', 'computer-architecture': 'ca', 'software-engineering': 'se',
    'graph-algorithms': 'ga', 'database-systems': 'db', 'competitive-programming': 'cp', 'operating-systems': 'os'}
COURSE_NAMES = {course.key: course.name for course in COURSE_SCHEDULE}
COURSE_PATTERNS = {
    'security': r'information security|secure network|secure transport|transport security|network security|firewalls?|intrusion prevention|encryption|資訊安全|資安|防火牆|加密',
    'computer-architecture': r'computer architecture|計算機結構',
    'software-engineering': r'software engineering|軟體工程',
    'graph-algorithms': r'graph algorithms?|圖論',
    'database-systems': r'\bdatabase\b|資料庫',
    'competitive-programming': r'competitive programming|程式競賽技巧導論',
    'operating-systems': r'operating systems?|作業系統',
}
KEY = 'task-review:'
OPEN_KEYBOARD = {'inline_keyboard': [[{'text': 'Review wording & course tags', 'callback_data': 'taskreview:open'}]]}


def infer_course(task):
    text = task['title'] + '\n' + (task.get('timing') or {}).get('content', '')
    if re.search(r'atcoder|contest|competition|聚會|meeting|比賽', text, re.I):
        return None
    matches = [key for key, pattern in COURSE_PATTERNS.items() if re.search(pattern, text, re.I)]
    return matches[0] if len(matches) == 1 else None


def defer_inferred_course_tags(parsed_tasks, instruction):
    """A supplied tag is explicit; a course tag invented by the parser needs approval."""
    explicit = re.search(r'#[\w.-]+|(?:標籤|标签|專案|专案)\s*(?:[:：=]|設為|设为|為|为|用)\s*\S+|\b(?:tag|project)\s+(?:(?:is|as)\s+)?[A-Za-z][\w.-]*', instruction, re.I)
    if explicit:
        return parsed_tasks
    return [replace(item, project=None) if infer_course({'title': item.title, 'timing': item.timing}) else item
            for item in parsed_tasks]


def course_project(course, tasks, aliases):
    candidates = {COURSE_TAGS[course], course, COURSE_NAMES[course]}
    for task in tasks:
        project = task.get('project')
        if project and (project.casefold() in candidates or
                        project_display_name(project, aliases).casefold() == COURSE_TAGS[course]):
            return project
    return COURSE_TAGS[course]


def proposal(task, title, course, project, reason, chat_id, now):
    return {'token': uuid4().hex, 'task_id': task['id'], 'before': task_fields(task),
        'title': title, 'course': course, 'project': project, 'reason': reason,
        'chat_id': chat_id, 'expires_at': (now + timedelta(minutes=10)).isoformat(), 'status': 'pending'}


def card(state, aliases):
    before = state['before']
    old_title = before['title'][:180] + ('…' if len(before['title']) > 180 else '')
    lines = ['<b>Suggested task update</b>']
    if state['title'] != before['title']:
        lines += ['', '<b>Title</b>', '<b>Before</b> ' + escape(old_title), '<b>After</b> ' + escape(state['title'])]
    else:
        lines.append(escape(old_title))
    if state['project'] != before.get('project'):
        lines += ['', '<b>Suggested course tag</b> #' + escape(project_display_name(state['project'], aliases)),
                  '<b>Course</b> ' + escape(COURSE_NAMES[state['course']])]
    lines += ['', escape(state['reason']), '確認後才會套用；其他欄位保持不變。']
    return {'text': '\n'.join(lines), 'parse_mode': 'HTML', 'task_review': state['token'],
        'reply_markup': {'inline_keyboard': [[{'text': 'Apply', 'callback_data': 'taskreview:apply:' + state['token']},
                                             {'text': 'Keep unchanged', 'callback_data': 'taskreview:cancel:' + state['token']}]]}}


def new_task_suggestion(db, task, all_tasks, aliases, chat_id, now):
    """Write-only after creation, compatible with Firestore read-before-write."""
    course = infer_course(task) if not task.get('project') and chat_id is not None else None
    if not course:
        return None
    state = proposal(task, task['title'], course, course_project(course, all_tasks, aliases),
                     '內容與這門課的主題相符，請確認是否使用此標籤。', chat_id, now)
    db._save_task_action_state(KEY + state['token'], state)
    return card(state, aliases)


async def prepare(db, tasks, ai, settings, chat_id):
    if chat_id is None:
        return lambda: '請在 Telegram 對話預覽任務用詞與課程標籤。'
    items = tasks.list_open()
    if not items:
        return lambda: '目前沒有待辦任務。'
    aliases = tasks.project_aliases()
    eligible = [item for item in items if not is_course_scope_task(item)]
    selected = eligible[:20]
    if not selected:
        return lambda: '目前只有課程範圍確認事項，沒有需要調整的學習任務。'
    now = datetime.now(settings.tz)
    try:
        output = TaskReviewOutput.model_validate(await ai.review_tasks(selected))
        ids = [item.task_id for item in output.items]
        if len(ids) != len(set(ids)) or set(ids) != {item['id'] for item in selected}:
            raise AIError('The review did not match the requested tasks.')
    except (AIError, ValueError):
        return lambda: '目前無法產生建議，任務與標籤都沒有變更。請稍後再試。'
    by_id = {item['id']: item for item in selected}
    states = []
    for item in output.items:
        task = by_id[item.task_id]
        # Existing explicit tags always win. Contests are not course assignments.
        course = item.course_key if not task.get('project') and not re.search(r'atcoder|contest|比賽', task['title'], re.I) else None
        project = course_project(course, items, aliases) if course else task.get('project')
        if item.title != task['title'] or project != task.get('project'):
            states.append(proposal(task, item.title, course, project, item.reason, chat_id, now))

    def save():
        current = {item['id']: task_fields(item) for item in tasks.list_open()}
        if any(current.get(item['id']) != task_fields(item) for item in selected):
            return '任務清單剛有變更，請重新產生建議。沒有套用任何變更。'
        if not states:
            return '目前沒有需要調整的用詞或課程標籤。'
        for state in states:
            db._save_task_action_state(KEY + state['token'], state)
        return {'messages': [{'text': '以下是建議；每張卡片都需確認，才會更新該任務。' +
            ('\n本次先檢查前 20 項任務。' if len(eligible) > 20 else ''), 'parse_mode': 'HTML'},
            *(card(state, aliases) for state in states)]}
    return save


def bind(db, chat_id, message_id, message):
    if message.get('task_review'):
        db.mutate_task_action_state(KEY + message['task_review'], lambda old:
            {**old, 'message_id': message_id} if old and old['chat_id'] == chat_id and old['status'] == 'pending' else old)


def respond(db, tasks, settings, chat_id, message_id, token, action, now=None):
    now = now or datetime.now(settings.tz)
    if action not in {'apply', 'cancel'}:
        return '無效的確認操作，沒有變更任務。'
    def apply():
        state = db.get_task_action_state(KEY + token)
        if not state or state['chat_id'] != chat_id or state.get('message_id') != message_id:
            return '這張建議卡無效，沒有變更任何任務。'
        if state['status'] != 'pending':
            return '這張建議已處理，沒有重複變更。'
        if state['expires_at'] <= now.isoformat():
            return '建議已過期，請重新產生。沒有變更。'
        if action == 'cancel':
            db._save_task_action_state(KEY + token, {**state, 'status': 'cancelled'})
            return '已保留原本的任務與標籤。'
        current = tasks.get_open_by_id(state['task_id'])
        if not current or task_fields(current) != state['before']:
            return '這項任務已被修改或結束，請重新確認。沒有套用舊建議。'
        aliases = tasks.project_aliases()
        due = datetime.fromisoformat(current['due_at']) if current.get('due_at') else None
        updated = tasks.edit_with_undo(current['id'], state['title'], due, state['project'], current.get('timing') or {})
        if not updated:
            return '這項任務已結束，沒有變更。'
        db._save_task_action_state(KEY + token, {**state, 'status': 'applied'})
        return {'messages': [{'text': '<b>Task updated</b>\n' + escape(updated['title']) +
            ('\n#' + escape(project_display_name(updated['project'], aliases)) if updated.get('project') else ''), 'parse_mode': 'HTML',
            'reply_markup': {'inline_keyboard': [[{'text': 'Undo this edit',
                'callback_data': 'task:undo:' + updated['undo_token']}]]}}]}
    return db._run_task_action(apply)
