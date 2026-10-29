"""Literal owner references constrain model-selected mutation identities."""
import re
from .study_feedback import NUMBER, number


def operation_text(text):
    markers=list(re.finditer(
        r'(?:內容|内容|標題|标题|名稱|名称|名字)\s*(?:改成|改為|改为|修改為|修改为|更新為|更新为|就叫|叫做|叫|是|為|为|[:：])|'
        r'改名(?:為|为)|更名(?:為|为)|\b(?:content|title|name)\s+(?:to|as|is|[:：])|\brename\b[^，,。;；]*?\bto\b',text,re.I))
    return text[:markers[0].end()] if markers else text


def nonoperative(text):
    text=operation_text(text)
    return bool(re.search(r'如果我說|如果我说|假設我說|假设我说|有人(?:說|说|傳|传)|只是(?:個|个)?例子|這句話.{0,12}(?:意思|翻譯)|这句话.{0,12}(?:意思|翻译)',text))


def completion_blocked(text):
    latest=re.split(r'[，,。;；]|但是|不過|不过|但|\bbut\b',text,flags=re.I)[-1].strip()
    if (re.search(r'(?:已經|已经|已|剛|刚|already|have).{0,12}(?:完成|finished|completed)',latest,re.I)
            and not re.search(r'不要|還沒|还没|未完成|\bnot\b',latest,re.I)):
        text=latest
    return nonoperative(text) or bool(re.search(
        r'(?:不要|別|别|不用|不需要).{0,25}(?:完成|讀完|读完|done|complete)|'
        r'還沒|还没|尚未|未完成|沒有完成|没有完成|打算|預計|预计|計畫|计划|明天|後天|后天|'
        r'\b(?:not\s+(?:done|finished|complete)|haven.t|plan\s+to|intend\s+to|will\s+(?:finish|complete))\b',
        text,re.I))


def literal_ids(text,items):
    """None means no literal reference; [] means unsafe/unresolved references."""
    text=operation_text(text)
    matches=list(re.finditer(r'(?:(?<![a-z0-9_])tasks?|任務|任务)\s*((?:#?\s*-?\d+)(?:\s*(?:[,，、/&]|(?<![a-z])and(?![a-z]))\s*#?\s*-?\d+)*)(?![a-z0-9_]|\.\d)',text,re.I))
    ordinals=list(re.finditer(r'第\s*('+NUMBER+r')\s*(?:項|项|個任務|个任务)',text))
    if not matches and not ordinals:
        return None
    if nonoperative(text):
        return []
    positions={item['position']:item['id'] for item in items}
    existing={item['id'] for item in items}
    result=[]
    for match in matches:
        if result and re.search(r'(?:在|排到|排在|移到|before|after)\s*$',text[max(0,match.start()-12):match.start()],re.I):
            continue
        if re.search(r'不要(?:碰|動|动|改|完成)?\s*$|別(?:碰|動|动|改)\s*$|don.t\s+(?:touch|change)\s*$',text[max(0,match.start()-20):match.start()],re.I):
            continue
        for index,token in enumerate(re.finditer(r'#?\s*-?\d+',match[1])):
            reference=token[0]
            stable=reference.lstrip().startswith('#')
            suffix=text[match.start(1)+token.end():]
            if index and not stable and re.match(r'(?:\.\d+)?\s*(?:分鐘|分钟|小時|小时|分\b|minutes?\b|mins?\b|hours?\b|hrs?\b)',suffix,re.I):
                continue
            value=int(reference.replace('#','').replace(' ',''))
            target=value if stable and value in existing else positions.get(value) if not stable else None
            if target is None:
                return []
            result.append(target)
    for match in ordinals:
        value=number(match[1])
        if value!=int(value) or int(value) not in positions:
            return []
        result.append(positions[int(value)])
    result=list(dict.fromkeys(result))
    if len(result)>1 and re.search(r'或|還是|还是|\bor\b',text,re.I):
        return []
    return result


def explicit_completion(text,items):
    """Recognize only a complete imperative/report and optional protected targets."""
    if completion_blocked(text) or re.search(r'[？?]',text):
        return None
    ref=r'(?:(?:task|任務|任务)\s*#?\s*-?\d+|第\s*'+NUMBER+r'\s*(?:項|项))'
    parts=re.split(r'[，,;；]|但',text.strip().rstrip('。.!！'),flags=re.I)
    first=parts[0].strip()
    patterns=[r'(?:請|请|幫我|帮我|please)?\s*(?:完成|complete|finish)\s*'+ref,
              r'(?:把|將|将)?\s*'+ref+r'\s*(?:標(?:記)?(?:成|為|为))?\s*(?:已經|已经|已|全部|完整)?\s*(?:完成|done|completed|finished)(?:了)?',
              r'mark\s+'+ref+r'\s+(?:as\s+)?(?:done|complete)']
    if not any(re.fullmatch(pattern,first,re.I) for pattern in patterns):
        return None
    if any(part.strip() and not re.fullmatch(r'(?:不要|別|别|don.t)\s*(?:碰|動|动|改|touch|change)\s*'+ref,part.strip(),re.I) for part in parts[1:]):
        return None
    return literal_ids(text,items)
