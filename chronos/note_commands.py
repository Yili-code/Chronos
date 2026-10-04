"""Read-only note commands. Returned text needs no Telegram parse mode."""
import re


def text_pages(text: str, limit: int = 3000) -> list[str]:
    """Bound UTF-16 units without cutting Python Unicode code points."""
    pages, current, units = [], [], 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > limit:
            pages.append("".join(current))
            current, units = [], 0
        current.append(char)
        units += width
    if current:
        pages.append("".join(current))
    return pages or [""]


def note_command(db, command: str) -> str | None:
    listing = re.fullmatch(r"notes(?:\s+(.+))?", command)
    if listing:
        notes = db.list_study_notes(course=listing.group(1), limit=10)
        if not notes:
            return "尚無符合條件的課程筆記。"
        lines = ["最近課程筆記（最多 10 份）："]
        for note in notes:
            label = note.course.replace("\n", " ").replace("\r", " ")[:60]
            if note.page_start is not None:
                filename = note.sources[0].filename.replace("\n", " ").replace("\r", " ")[:80]
                label += f" · {filename} · p. {note.page_start}–{note.page_end} · {note.segment_index}/{note.segment_total}"
            lines.append(f"{label} · {note.class_date}\n/note {note.content_fingerprint}")
        return "\n\n".join(lines)
    reading = re.fullmatch(r"note\s+([0-9a-f]{64})(?:\s+([1-9][0-9]{0,4}))?", command)
    if reading:
        note = db.get_study_note(reading.group(1))
        if note is None:
            return "找不到這份筆記。請用 /notes 查看可用編號。"
        pages = text_pages(note.markdown)
        page = int(reading.group(2) or "1")
        if page > len(pages):
            return f"頁碼超出範圍；這份筆記共 {len(pages)} 段。"
        result = f"筆記 {page}/{len(pages)}\n\n{pages[page - 1]}"
        if page < len(pages):
            result += f"\n\n下一段：/note {note.content_fingerprint} {page + 1}"
        return result
    if command == "note" or command.startswith("note "):
        return "用法：/note 完整編號 [段落編號]；請先用 /notes 取得編號。"
    return None
