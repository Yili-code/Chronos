"""Bounded subject search and atomic local draft/monitor preparation."""
import asyncio
import copy
import json
import re

from .gmail import GmailError, message_id
from .mail_correspondence import MailCorrespondence, fingerprint
from .mail_followups import MailFollowups
from .mail_natural_workflow import LocalChange


def command(text):
    if re.match(r'^(?:搜尋郵件\s*[:：]|/mail_search\b)', text.strip(), re.I):
        return False
    first_clause = re.split(r'[，,；;]', text.strip(), maxsplit=1)[0]
    if not re.search(r'郵件|信件|信\s*$|\b(?:email|mail|message)\b', first_clause, re.I):
        return False
    return bool(re.match(r'^(?:(?:請幫我|幫我|請)\s*)?(?:找出|尋找|搜尋|find\b|search\s+for\b)', text.strip(), re.I)
                and re.search(r'草擬|起草|撰寫|draft', text, re.I)
                and re.search(r'追蹤|track', text, re.I))


def parse(text):
    parts = [p.strip() for p in re.split(r'[，,；;]', text.strip())]
    if len(parts) not in {2, 3} or len(text) > 1000:
        return None
    found = re.fullmatch(r'(?:(?:請幫我|幫我|請)\s*)?(?:找出|尋找|搜尋)\s*(.+?)(?:郵件|信)|'
                         r'(?:find|search\s+for)\s+(?:the\s+)?(.+?)\s+(?:email|mail|message)', parts[0], re.I)
    if not found:
        return None
    term = (found[1] or found[2]).strip()
    if not term or len(term) > 120 or re.search(r'[\x00-\x1f"\\:(){}\[\]]', term):
        return None
    steps = re.fullmatch(
        r'((?:草擬|起草|撰寫)(?:(?:中文|英文|正式|簡潔)\s*)?回覆)\s*(?:並且|並|和|及)\s*追蹤(?:回覆)?'
        r'(?:\s*(\d{1,4})\s*(天|小時)(?:後)?)?|'
        r'(draft\s+(?:a\s+)?reply)\s+and\s+track(?:\s+(?:it|replies))?'
        r'(?:\s+in\s+(\d{1,4})\s+(days?|hours?))?', parts[1], re.I)
    if not steps:
        return None
    if len(parts) == 3 and not re.fullmatch(
        r'(?:但|不過)?\s*(?:先|暫時|目前)?\s*(?:不要|別|不必)\s*(?:寄(?:出)?|發送)|'
        r'(?:but\s+)?(?:do\s+not|don\x27t)\s+send(?:\s+(?:it|yet))?', parts[2], re.I):
        return None
    number, unit = steps[2] or steps[5], steps[3] or steps[6]
    hours = int(number) * (24 if unit.lower() in {'天', 'day', 'days'} else 1) if number else None
    if hours is not None and not 1 <= hours <= 2160:
        return None
    return term, steps[1] or steps[4], hours


def result(text):
    return lambda: '搜尋與郵件流程\n' + text


async def prepare(mail, text, chat_id):
    mail.require_config()
    if chat_id != mail.settings.telegram_chat_id:
        return result('擁有者不符，未搜尋或套用。')
    parsed = parse(text)
    if parsed is None:
        return result('無法完整核對這個複合要求；僅支援明確主旨搜尋、一般回覆草稿與追蹤，可加明確間隔。其他條件請先用 /mail_search 選擇郵件再處理；未搜尋、未套用或寄送。')
    term, draft_request, hours = parsed
    account = mail.settings.gmail_account
    query = 'subject:"' + term + '" -in:trash -in:spam -in:drafts'

    async def stage():
        await mail.gmail.verify_account()
        ids, next_page = await mail.gmail.search_page(query, None)
        if not isinstance(ids, list) or len(ids) > 5 or len(ids) != len(set(ids)):
            raise GmailError('Unverified search result')
        if not ids:
            return result('本次主旨搜尋沒有符合郵件；尚未建立草稿或追蹤。')
        if len(ids) != 1 or next_page:
            return result('找到多封或仍有下一頁；請先用 /mail_search 選擇明確郵件卡片，不會猜哪封。草稿與追蹤均未套用。')
        message_id(ids[0])
        identifier = ids[0]
        keys = ['message:' + identifier, 'reply:' + identifier, 'meeting:' + identifier, 'contacts', 'followups', 'search']
        expected = {mail.key(k): copy.deepcopy(mail.get(k)) for k in keys}
        old_tracking = (expected[mail.key('followups')] or {}).get(identifier, {})
        if old_tracking.get('status') in {'waiting_owner', 'waiting_other', 'paused', 'notifying', 'reminded'}:
            return result('此封已有追蹤，請先查看或明確調整原追蹤；不會覆蓋既有提醒或投遞狀態，整個流程未套用。')
        item = await mail.gmail.read(identifier)
        if (not isinstance(item, dict) or item.get('id') != identifier or not item.get('thread_id')
                or term.casefold() not in str(item.get('subject', '')).casefold()
                or set(item.get('labels', [])) & {'DRAFT', 'TRASH', 'SPAM'}
                or len(json.dumps(item, ensure_ascii=False).encode('utf-8')) > 300000):
            raise GmailError('Search source could not be verified')
        source_state = {**(expected[mail.key('message:' + identifier)] or {}), 'mail': copy.deepcopy(item)}
        staged = copy.copy(mail)
        staged.get = lambda key: source_state if key == 'message:' + identifier else mail.get(key)
        correspondence = MailCorrespondence(staged)
        # Generic acknowledgement is reversible preparation, never acceptance of terms.
        instruction = draft_request + '\n依完整郵件草擬中性的確認收件回覆，不代表同意報價或其他條件；若仍缺必要立場，向使用者提出問題，不虛構承諾。'
        draft = await correspondence.propose(identifier, instruction, reply_all=False, stage=True)
        if not isinstance(draft, LocalChange):
            return result('草稿步驟未完成，整個流程未套用。\n' + str(draft()))
        draft.value['workflow_owner_request'] = text
        tracking_command = '追蹤回覆' + (f'：{hours} 小時' if hours is not None else '')
        followup = await MailFollowups(staged).prepare(identifier, tracking_command, stage=True)
        if not isinstance(followup, LocalChange):
            return result('追蹤步驟未完成，整個流程未套用。\n' + str(followup()))
        _, latest = await correspondence.context(identifier)
        state = followup.value[identifier]
        if (fingerprint(latest) != draft.value['source'] or state['latest_id'] != latest[-1]['id']
                or draft.value.get('to') != [state['target']]):
            return result('郵件來源或對象在準備期間已變更，整個流程未套用；請重新搜尋。')
        presentation = correspondence.presentation(draft.value)['messages'][0]
        presentation['text'] = ('搜尋與郵件流程已套用（僅本機）\n' + presentation['text']
                                + '\n\n' + MailFollowups(staged).describe(state))
        if len(presentation['text'].encode('utf-16-le')) // 2 > 3900:
            return result('完整預覽超出長度，整個流程未套用；請先選擇郵件分開處理。')
        presentation.update(search_namespace=mail.namespace, search_mail_id=identifier, workflow_query=query)
        presentation['mail_actions'] = [a for a in presentation.get('mail_actions', []) if a['kind'] == 'save_reply']
        changes = {mail.key('message:' + identifier): source_state, mail.key(draft.key): draft.value, mail.key(followup.key): followup.value}

        def apply():
            if mail.settings.gmail_account != account or chat_id != mail.settings.telegram_chat_id:
                return '搜尋與郵件流程\n帳號或擁有者已變更，整個流程未套用。'
            if not mail.db.compare_mail_states(expected, changes):
                return '搜尋與郵件流程\n郵件、草稿、追蹤或聯絡資料已變更，整個流程未套用。'
            return {'messages': [presentation]}
        return apply

    try:
        return await asyncio.wait_for(stage(), timeout=180)
    except (GmailError, TimeoutError):
        return result('搜尋或完整核對未能完成，沒有保存部分結果，不能判定沒有符合郵件；請稍後重試。')
