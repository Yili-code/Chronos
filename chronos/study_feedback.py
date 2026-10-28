"""Owner-reported study progress; no estimates or model-produced measurements."""
import re
import unicodedata

PROMPT = '這次大概專心讀了多久？讀到第幾頁了？\n例如「讀了兩小時，到第 20 頁，下次從 21 頁開始」。'
NUMBER = r'(?:\d+(?:\.\d+)?|[零〇一二兩两三四五六七八九十百]+)'


def number(value):
    if re.fullmatch(r'\d+(?:\.\d+)?', value):
        return float(value)
    digits = dict(zip('零〇一二兩两三四五六七八九', [0, 0, 1, 2, 2, 2, 3, 4, 5, 6, 7, 8, 9]))
    result, digit = 0, 0
    for char in value:
        if char in digits:
            digit = digits[char]
        else:
            result += (digit or 1) * {'十': 10, '百': 100}[char]
            digit = 0
    return result + digit


def extract(text):
    text = unicodedata.normalize('NFKC', text).strip()
    pipe = re.fullmatch(r'(\d+)\s*\|\s*(\d+)\s*/\s*(\d+)\s*\|\s*(\d+)', text)
    if pipe:
        return dict(zip(('minutes', 'completed', 'total', 'next_page'), map(int, pipe.groups())))
    if re.search(r'(?:^|[，,\s])-\s*\d', text):
        return {'minutes': -1}
    result = {}
    # Split reports containing rest/phone time rather than adding that time to focus.
    clauses = re.split(r'[，,。;；]|(?=休息|滑手機|滑手机|break|phone)', text, flags=re.I)
    focus = ' '.join(part for part in clauses if not re.search(r'休息|滑手機|滑手机|break|phone|預計|原訂|預定|打算|計畫|planned|plan to|明天|後天|下次|還沒|沒有讀|沒讀', part, re.I))
    if re.search(r'含|其中|includ', text, re.I) and re.search(r'休息|break|phone|滑手機', text, re.I) and not re.search(r'專心|专心|focused', focus, re.I):
        focus = ''
    hours = re.search(rf'({NUMBER})\s*(?:個|个)?(半)?(?:小時|小时|hours?|hrs?)(半)?', focus, re.I)
    mins = re.search(rf'({NUMBER})\s*(?:分鐘|分钟|分(?![之數数])|minutes?|mins?)', focus, re.I)
    if hours or mins or re.search(r'半(?:個|个)?(?:小時|小时)', focus):
        value = (number(hours[1]) * 60 + (30 if hours[2] or hours[3] else 0)) if hours else 30 if re.search(r'半(?:個|个)?(?:小時|小时)', focus) else 0
        value += number(mins[1]) if mins else 0
        result['minutes'] = value
    pages = re.search(r'(\d+)\s*/\s*(\d+)\s*(?:頁|页|pages?)', text, re.I)
    if pages:
        result.update(completed=int(pages[1]), total=int(pages[2]))
    else:
        page_text = re.split(r'下次(?:從|从)|接著(?:從|从)|next(?:\s+page)?', text, flags=re.I)[0]
        reached = re.search(rf'(?:讀到|读到|看到|做到|到第?|第|through\s+page|up\s+to\s+page|page)\s*({NUMBER})\s*(?:頁|页)?', page_text, re.I)
        if reached:
            result['completed'] = number(reached[1])
        total = re.search(rf'(?:共|總共|总共|total)[為为是:：\s]*({NUMBER})\s*(?:頁|页|pages?)', text, re.I)
        total = total or re.search(rf'(?:總頁數|总页数|total\s+pages)[為为是:：\s]*({NUMBER})', text, re.I)
        if total:
            result['total'] = number(total[1])
    next_page = re.search(rf'(?:下次(?:從|从)|接著(?:從|从)|next(?:\s+page)?[ :：]*)\s*第?({NUMBER})\s*(?:頁|页)?', text, re.I)
    if next_page:
        result['next_page'] = number(next_page[1])
    return result
