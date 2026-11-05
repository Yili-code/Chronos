"""Resolve qualified names from explicit local fields, without recipient mutation."""
import re
import copy

from .gmail import email_address


NAMED_REPLY = re.compile(
    r"^(?:(?:請幫我|幫我|請)\s*)?(?:回給|回覆給|reply\s+to|respond\s+to)\s*"
    r"([^,，;；\n:：]{1,80})(.*)$", re.I | re.S)


def request_parts(text):
    text = re.sub(
        r"^(?:(?:請幫我|幫我|請)\s*)?(?:草擬(?:郵件)?回覆|起草回覆|撰寫回覆|draft\s+(?:a\s+)?reply)\s*[:：]\s*",
        "", text.strip(), count=1, flags=re.I)
    match = NAMED_REPLY.fullmatch(text)
    if not match:
        return None
    name = match[1].strip()
    if re.match(r"^(?:他|她|對方|這封|this\s+email\b|him\b|her\b|them\b)", name, re.I):
        return None
    qualifiers = []
    for clause in re.split(r"[,，;；]", match[2]):
        clause = clause.strip()
        if not clause:
            continue
        negated = re.match(r"^(?:不是|不要|不指|not\s+)", clause, re.I)
        value = clause[negated.end():] if negated else clause
        value = re.sub(r"(?:那位|這位|那個|這個)(?:的)?$", "", value).strip()
        qualifiers.append((value, bool(negated)))
    return name, qualifiers


def qualifies(contact, qualifier):
    values = [contact.get(key) for key in ('company', 'role')]
    values = [v.strip() for v in values if isinstance(v, str)]
    # These are explicit labels in owner-maintained fields, not domain inference.
    if qualifier in {'公司', '工作', '同事'}:
        return any(re.search(r'公司|同事|工作', v) for v in values)
    if qualifier in {'學校', '同學', '大學'}:
        return any(re.search(r'學校|同學|大學|學院|\b(?:university|school|college)\b', v, re.I) for v in values)
    return bool(qualifier) and any(qualifier.casefold() == v.casefold() for v in values)


async def prepare(mail, identifier, request):
    from .mail_correspondence import MailCorrespondence, text_result
    parts = request_parts(request)
    if parts is None:
        return None
    name, qualifiers = parts
    account = mail.settings.gmail_account
    registry = copy.deepcopy(mail.get('contacts') or {})
    items = registry.get('items', {})
    candidates = [c for c in items.values() if isinstance(c, dict)
                  and isinstance(c.get('name'), str) and c['name'].strip().casefold() == name.casefold()
                  and all(qualifies(c, q) != negative for q, negative in qualifiers)]
    if len(candidates) != 1:
        return text_result('無法依明確聯絡人資料唯一確認對象；請提供確切 Email 或補充公司／職稱等識別資料。沒有猜測、合併或改變收件者，尚未草擬或寄送。')
    selected = candidates[0]
    if selected.get('do_not_contact'):
        return text_result('該聯絡人設為不主動聯絡，請先確認聯絡偏好；尚未草擬或寄送。')
    bound = copy.deepcopy(mail.get('message:' + identifier))
    _, messages = await MailCorrespondence(mail).context(identifier)
    latest = messages[-1]
    sender = email_address(latest.get('sender', ''))
    reply_to = email_address(latest.get('reply_to', ''))
    addresses = selected.get('emails', [])
    valid = (isinstance(addresses, list) and sender and not latest.get('sender_ambiguous')
             and not latest.get('automatic') and (not reply_to or reply_to == sender)
             and sender in [email_address(a) for a in addresses if isinstance(a, str)])

    def apply():
        if (mail.settings.gmail_account != account or mail.get('contacts') != registry
                or mail.get('message:' + identifier) != bound):
            return '回覆草稿\n聯絡人、帳號或來源卡片已變更，請重新確認；沒有套用或顯示舊身份資料。'
        if not valid:
            return '回覆草稿\n已依本機資料找到符合限定的聯絡人，但與目前郵件串的已核對寄件者不符或身份不明。請使用正確對象的郵件卡片；不會自行跨對話換收件者，尚未草擬或寄送。'
        return ('回覆草稿\n依你維護的明確聯絡人資料及目前郵件寄件者，對象為：'
                + selected['name'] + ' · ' + str(selected.get('company') or '公司未提供')
                + ' · ' + str(selected.get('role') or '職稱未提供') + '\nEmail：' + sender
                + '\n這不是外部身份驗證；原有聯絡人與草稿未變更。請補充「草擬回覆：內容」，尚未草擬、儲存或寄送。')
    return apply
