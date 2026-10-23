"""Conservative whole-reply recognition for explicit no-change acknowledgements."""
import re
import unicodedata


def is_no_change_reply(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = re.sub(r"[\s，。！？,.!?、'’]", "", normalized)
    return normalized in {
        "收到", "好", "好的", "了解", "謝謝", "谢谢", "ok", "okay", "thanks", "thankyou", "gotit",
        "不需要記錄", "不需要记录", "不用記錄", "不用记录", "不要記錄", "不要记录",
        "不需要更新", "不用更新", "不要更新", "保持不變", "保持不变",
        "不需要更新tasklist", "不用更新tasklist", "不要更新tasklist",
        "不需要更新任務清單", "不用更新任務清單", "不需要更新任务清单",
        "noneedtorecord", "dontrecordthis", "noneedtoupdate", "noneedtoupdatethetasklist",
        "leavethetasksunchanged",
    }
