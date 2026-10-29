"""Literal scheduling constraints supplement model extraction without inventing work."""
from datetime import datetime,timedelta
import re
from .study_feedback import NUMBER,number


def literal_window(text,now):
    match=re.search(r'(?:現在|现在|now).*?(?:到|至|until|to)\s*(?:今天|今日|today)?\s*(早上|上午|中午|下午|晚上)?\s*('+NUMBER+r')(?:[:：](\d{2})|[點点時时](半|\d{1,2}分)?)',text,re.I)
    if not match:return None
    hour=int(number(match[2]));minute=int(match[3] or (30 if match[4]=='半' else (match[4] or '0').removesuffix('分')))
    if match[1] in {'下午','晚上'} and hour<12:hour+=12
    if not 0<=hour<=23 or not 0<=minute<60:raise ValueError('請確認安排的截止時間。')
    begin=now.replace(second=0,microsecond=0)+timedelta(minutes=1)
    stop=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
    if stop<=begin:raise ValueError(f'今天 {hour:02d}:{minute:02d} 已經過了，請指定新的安排範圍。')
    return begin,stop


def chapter_refs(text,tasks):
    match=re.search(r'\bDB\s*0?\d{1,2}(?:\s*[,，、/]\s*0?\d{1,2})*',text,re.I)
    if not match:return set()
    numbers={int(n) for n in re.findall(r'\d+',match[0])}
    result=set()
    for n in numbers:
        candidates=[i for i,t in tasks.items() if (m:=re.fullmatch(r'(?:Study |Review )?(?:Database[–—_ -]*systems?|Database|DB|資料庫)\s*(?:Chapter|Ch\.?|第)\s*(\d+)\s*章?',t['title'],re.I)) and int(m[1])==n]
        if len(candidates)!=1:raise ValueError(f'DB 第 {n} 章無法唯一對應到未完成任務，請補充名稱。')
        result.add(candidates[0])
    return result


def minimum(text):
    m=re.search(r'(?:至少|at least)\s*(?:分配|安排|各|each\s*)?\s*('+NUMBER+r')\s*(?:個|个)?(小時|小时|hours?|分鐘|分钟|min(?:utes?)?)',text,re.I)
    return int(number(m[1])*(60 if m[2].lower().startswith('h') or m[2] in {'小時','小时'} else 1)) if m else None


def rest_minutes(text):
    m=re.search(r'(?:休息|breaks?)\s*(?:各|約|约)?\s*('+NUMBER+r')\s*(?:分(?:鐘|钟)?|minutes?|mins?)',text,re.I)
    if not m:m=re.search(r'('+NUMBER+r')\s*(?:分(?:鐘|钟)?|minutes?|mins?)\s*(?:的)?(?:短)?休息',text,re.I)
    if m:return int(number(m[1]))
    return 5 if re.search(r'休息|break',text,re.I) else 0


def approved_window(state,start,end):
    window=state.get('owner_window')
    if not window:return False
    try:
        a,b=(datetime.fromisoformat(window[k]) for k in ('start','end'))
        return a.utcoffset() is not None and b.utcoffset() is not None and a<=start<end<=b and b-a<=timedelta(days=1)
    except (KeyError,ValueError,TypeError):return False


def revision_instruction(text,recent):
    if not recent or recent.get('action')!='study_replan':return text
    m=re.fullmatch(r'\s*(?:(?:改成|改為|改为|改|每次|休息|短休息)\s*)*('+NUMBER+r')\s*分(?:鐘|钟)?(?:\s*(?:休息|短休息|就好|再排|重排))*[。.!！]?\s*',text)
    if not m:return text
    value=number(m[1])
    if value!=int(value):return text
    original=recent.get('instruction','')
    original=re.sub(r'(?:休息|breaks?)\s*(?:各|約|约)?\s*'+NUMBER+r'\s*(?:分(?:鐘|钟)?|minutes?|mins?)','',original,flags=re.I)
    original=re.sub(NUMBER+r'\s*(?:分(?:鐘|钟)?|minutes?|mins?)\s*(?:的)?(?:短)?休息','',original,flags=re.I)
    return original+'\n每次休息'+str(int(value))+'分鐘'
