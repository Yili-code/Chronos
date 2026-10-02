"""Telegram selection presentation and revision-scoped callback application."""
import re
from .selection_store import SelectionStore, decode


def selection_view(key, state, page=0):
    SelectionStore.validate_key(key)
    selection = decode(state["selection"])
    if selection.confirmed:
        names = [item.filename[:100] for item in selection.catalog if item.source_id in selection.selected_ids]
        return "已完成選擇；摘要尚待處理。\n" + "\n".join(names), {"inline_keyboard": []}
    pages = max(1, (len(selection.catalog) + 7) // 8)
    page = min(max(page, 0), pages - 1)
    prefix = f"pdf:{key}:{state['revision']}:"
    rows = []
    for index in range(page * 8, min(len(selection.catalog), (page + 1) * 8)):
        item = selection.catalog[index]
        selected = item.source_id in selection.selected_ids
        rows.append([{"text": ("[已選] " if selected else "[未選] ") + item.filename[:50],
                      "callback_data": prefix + f"{'u' if selected else 's'}{index}"}])
    navigation = []
    if page:
        navigation.append({"text": "上一頁", "callback_data": prefix + f"p{page - 1}"})
    if page + 1 < pages:
        navigation.append({"text": "下一頁", "callback_data": prefix + f"p{page + 1}"})
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "完成選擇", "callback_data": prefix + "done"}])
    if any(len(button["callback_data"].encode()) > 64 for row in rows for button in row):
        raise ValueError("callback exceeds Telegram limit")
    return f"請選擇 PDF（{page + 1}/{pages} 頁，已選 {len(selection.selected_ids)} 份）；按完成選擇才確認。", {"inline_keyboard": rows}


def apply_callback(db, chat_id, data, *, message_id):
    match = re.fullmatch(r"pdf:([A-Za-z0-9_-]{1,40}):(\d{1,8}):(done|[sup]\d{1,5})", data or "")
    if not match:
        raise ValueError("invalid selection action")
    key, revision, action = match.groups()
    state = db.get_material_selection(key)
    if state is None or state["chat_id"] != chat_id:
        raise ValueError("selection not available")
    if type(message_id) is not int or state.get("message_id") != message_id:
        raise ValueError("selection message mismatch")
    page = 0
    if action.startswith("p"):
        page = int(action[1:])
    elif int(revision) == state["revision"]:
        store = SelectionStore(db)
        if action == "done":
            state = store.apply(key, chat_id, int(revision), confirm=True)
        else:
            index = int(action[1:])
            catalog = state["selection"]["catalog"]
            if index >= len(catalog):
                raise ValueError("unknown material")
            state = store.apply(key, chat_id, int(revision), source_id=catalog[index]["source_id"], selected=action[0] == "s")
            page = index // 8
    return selection_view(key, state, page)
