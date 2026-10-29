"""Owner field choices distinguish edits from instructions to preserve fields."""
import re
from .task_references import operation_text


def title_change(text):
    head=re.split(r'[:：]',operation_text(text),maxsplit=1)[0]
    pattern=r'改名|更名|重新命名|(?:修改|更改|改|更新|調整|调整)[^，,。;；]{0,8}(?:標題|标题|名稱|名称|名字)|(?:標題|标题|名稱|名称|名字)\s*(?:改成|改為|改为|換成|换成)|\brename\b|\b(?:change|edit|update)\s+(?:the\s+)?(?:title|name)\b'
    return any(not re.search(r'不要|不用|不需|別|别|不$',head[max(0,m.start()-5):m.start()]) for m in re.finditer(pattern,head,re.I))


def content_change(text):
    text=operation_text(text)
    return bool(re.search(r'(?:修改|更改|改|更新|清空|刪除|删除)(?:他|它|這個任務|这个任务|的|之|\s){0,6}(?:內容|内容)|(?:內容|内容)\s*(?:改成|改為|改为|清空|刪除|删除)|\b(?:edit|change|update|clear)\s+(?:the\s+)?content\b',text,re.I))


def content_only(text):
    if re.search(r'(?im)^Only change content; preserve title\.\s*$',text):
        return True
    text=operation_text(text)
    return bool(re.search(r'只(?:能|要)?(?:改|修改|更新)(?:內容|内容)|\bonly\s+(?:change|edit|update)\s+content\b',text,re.I))


def open_status_request(text):
    if re.search(r'內容|内容|標題|标题|\b(?:content|title)\b',text,re.I):
        return False
    return bool(re.fullmatch(r'[^，,。;；:：]{1,150}(?:改成|改為|改为|標成|標為|标为|設為|设为|mark\s+(?:as\s+)?)(?:未完成|incomplete|not\s+done)(?:[，,]\s*不要(?:建立|新增|另建)(?:第二份|重複任務|重复任务))?[。.!！]?',text.strip(),re.I))
