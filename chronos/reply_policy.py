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


def conversational_reply(text: str) -> str | None:
    """Recognize complete social turns only; mixed requests keep normal routing."""
    normalized = unicodedata.normalize("NFKC", text).casefold().strip()
    normalized = re.sub(r"[，。！？,.!?、]+$", "", normalized).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if normalized in {"thanks", "thank you", "thanks a lot", "thank you very much",
                      "謝謝", "谢谢", "多謝", "感謝", "謝啦", "辛苦了"}:
        return "You’re welcome, Sir."
    if normalized in {"hi", "hello", "hey", "你好", "嗨", "哈囉", "哈啰"}:
        return "Hello, Sir. What would you like to take care of?"
    if normalized in {"good morning", "早安", "早上好"}:
        return "Good morning, Sir. What would you like to take care of?"
    if normalized in {"good evening", "晚上好"}:
        return "Good evening, Sir. What would you like to take care of?"
    if normalized in {"good night", "晚安"}:
        return "Good night, Sir."
    if normalized in {"who are you", "what are you", "are you human", "are you a bot",
                      "are you ai", "are you an ai", "你是誰", "你是谁", "你是真人嗎",
                      "你是人嗎", "你是機器人嗎", "你是ai嗎", "你是 ai 嗎"}:
        return "I’m Chronos, your AI assistant for tasks, reminders and calendar planning."
    if normalized in {"what can you do", "how can you help", "你能做什麼", "你可以做什麼"}:
        return ("I can help with tasks, reminders and calendar planning. "
                "Tell me what you need, or use /info to see the available options.")
    if is_no_change_reply(text):
        if re.search(r"記錄|记录|更新|不變|不变|record|update|unchanged", normalized):
            return "Understood, Sir. I’ll leave everything as it is."
        return "Understood, Sir."
    return None
