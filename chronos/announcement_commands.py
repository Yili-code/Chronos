"""Read-only saved bulletin lookup; never fetches, generates or sends notices."""
from datetime import datetime
import re


def short_datetime(value):
    return datetime.fromisoformat(value).strftime('%m/%d %H:%M')


def announcements_query(db, command):
    match = re.fullmatch(r'announcements(?: ([1-9][0-9]{0,5}))?', command)
    if not match:
        return '用法：/announcements [頁碼]。只查閱已保存版本，不會抓取 TronClass。'
    records = sorted(db.list_announcements(),
        key=lambda entry:(entry[1]['announcement']['published_at'],entry[0]), reverse=True)
    if not records:
        return '尚無已保存公告；這不代表 TronClass 沒有公告。'
    page = int(match[1] or 1)
    pages = (len(records)+2)//3
    if page > pages:
        return f'目前共 {pages} 頁。'
    lines = ['已保存公告內容版本（非完整課程清單；同一公告可能有多個版本）']
    for key, record in records[(page-1)*3:page*3]:
        item = record['announcement']
        lines.append(f"{item['title']}\n課程：{item['course_id']}；發布：{short_datetime(item['published_at'])}\n/announcement {key}")
    lines.append(f'第 {page}/{pages} 頁')
    if page < pages:
        lines.append(f'下一頁：/announcements {page+1}')
    return '\n\n'.join(lines)


def announcement_query(db, command):
    match = re.fullmatch(r'announcement ([a-f0-9]{64})(?: ([1-9][0-9]{0,5}))?', command)
    if not match:
        return '請從 /announcements 選擇公告，使用 /announcement 版本編號 [頁碼]。'
    record = next((record for key, record in db.list_announcements() if key==match[1]),None)
    if record is None:
        return '找不到此已保存公告版本。'
    item = record['announcement']
    text = (f"{item['title']}\n課程：{item['course_id']}\n發布：{short_datetime(item['published_at'])}\n"
            '此為已保存內容版本，不保證是最新公告；連結已移除。\n\n'+item['content'])
    parts = [text[i:i+1500] for i in range(0,len(text),1500)]
    page = int(match[2] or 1)
    if page > len(parts):
        return f'目前共 {len(parts)} 頁。'
    footer = f'\n\n第 {page}/{len(parts)} 頁'
    if page < len(parts):
        footer += f'\n下一頁：/announcement {match[1]} {page+1}'
    return parts[page-1]+footer
