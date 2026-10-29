"""Explicit whole-afternoon allocation is a focus request, not completion."""
import re
from datetime import timedelta
from .task_actions import task_fields


def window(text, now):
    """Recognize an allocation request independently of its target reference."""
    from .task_references import operation_text
    text=operation_text(text)
    clause=re.split(r'[，,;；]',text,maxsplit=1)[0]
    if re.search(r'不要|不想|不用|不必|別|别|\b(?:don.t|do not)\b',clause,re.I):
        return None
    if re.search(r'[？?]|如果|假如|例如|例子|有人|不要把|別把|别把|do not reserve|don.t reserve', text, re.I):
        return None
    chinese = re.search(r'(?:今天|今日|明天)?下午.{0,12}(?:全部|全都|都|全|只)(?:用來|用来|拿來|拿来|留給|留给|做|安排|準備|准备)', text)
    english = re.search(r'\breserve\s+(?:this|tomorrow)\s+afternoon\s+for\b', text, re.I)
    if not chinese and not english:
        return None
    day = now + timedelta(days=1 if re.search(r'明天下午|tomorrow afternoon', text, re.I) else 0)
    start = max(now, day.replace(hour=13, minute=0, second=0, microsecond=0))
    end = day.replace(hour=18, minute=0, second=0, microsecond=0)
    return {'start': start, 'end': end}


def request(text, now):
    scope = window(text, now)
    refs = re.findall(r'(?:\btask|任務|任务)\s*(#?)([1-9]\d*)\b', text, re.I)
    if not scope or len(refs) != 1:
        return None
    return {**scope, 'reference': int(refs[0][1]), 'stable_id': bool(refs[0][0])}


def named_target(text,tasks):
    """Resolve a unique named course/material without inventing a new target."""
    clause=re.split(r'[，,;；]',text,maxsplit=1)[0]
    groups={
        'db':r'database(?:[ _-]+systems?)?|資料庫|\bdb\b',
        'os':r'operating[ _-]+systems?|作業系統|\bos\b',
        'cp':r'competiti(?:on|ve)[ _-]+programming|程式競賽技巧導論|競程|\bcp\b',
        'ispm':r'資訊安全管理|\bispm\b','ga':r'graph[ _-]+algorithms?|圖論演算法|\bga\b',
        'se':r'software[ _-]+engineering|軟體工程|\bse\b','ca':r'computer[ _-]+architecture|計算機組織|\bca\b',
        'past':r'past[ _-]*papers?|歷屆試題|考古(?:題)?','report':r'\breport\b|報告',
        'midterm':r'\bmidterm\b|期中考','cheatsheet':r'cheat[ _-]*sheet|大抄',
    }
    def normalize(value):
        for key,pattern in groups.items():value=re.sub(pattern,' '+key+' ',value,flags=re.I)
        return re.sub(r'\s+',' ',value).strip().casefold()
    source=normalize(clause)
    courses={'db','os','cp','ispm','ga','se','ca'}
    needed={key for key in courses if re.search(r'(?<![a-z0-9])'+key+r'(?![a-z0-9])',source)}
    numbers=set(re.findall(r'\d+',source))
    exact=[]
    for task in tasks:
        name=normalize(task['title']);descriptor=normalize(task['title']+' '+(task.get('project') or ''))
        if (len(name)>=4 and re.search(r'(?<![a-z0-9])'+re.escape(name)+r'(?![a-z0-9])',source)
                and numbers.issubset(set(re.findall(r'\d+',name)))
                and all(re.search(r'(?<![a-z0-9])'+key+r'(?![a-z0-9])',descriptor) for key in needed)):
            exact.append((len(name),task['id']))
    if exact:
        longest=max(length for length,_ in exact)
        ids={i for length,i in exact if length==longest}
        return next(iter(ids)) if len(ids)==1 else None
    return None


def validate(planner, review):
    target = review.get('focus_task')
    if target:
        task = planner.db.get_task(target['id'])
        if not task or task.get('status') != 'open' or task_fields(task) != target['snapshot']:
            from .study_reflow import ReflowReviewError
            raise ReflowReviewError('專注時段對應的任務已變更，請重新確認。沒有調整行程。')
