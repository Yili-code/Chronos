"""Owner-scoped, explicit preferences; never executable instructions or approvals."""
from html import escape


def state_key(chat_id):
    return f"owner:preferences:{chat_id}"


def read(db, chat_id):
    return dict(db.get_task_action_state(state_key(chat_id)) or {})


def render(items):
    if not items:
        return "目前沒有已保存的偏好。"
    return "已保存的偏好：\n" + "\n".join(
        f"• {escape(key)}：{escape(value)}" for key, value in sorted(items.items()))


def apply(db, chat_id, operation, key=None, value=None):
    if operation == "list":
        return render(read(db, chat_id))
    if not key or (operation == "set" and not value):
        return "請說明要記住或忘記哪一項偏好。沒有變更。"

    def transition(old):
        items = dict(old or {})
        if operation == "forget":
            items.pop(key, None)
        elif operation == "set":
            if key not in items and len(items) >= 20:
                raise ValueError("偏好已達 20 項，請先忘記不再適用的項目。")
            items[key] = value
        else:
            raise ValueError("Unknown preference operation")
        return items

    before = read(db, chat_id)
    if operation == "forget" and key not in before:
        return "找不到這項已保存的偏好。沒有變更。"
    try:
        db.mutate_task_action_state(state_key(chat_id), transition)
    except ValueError as error:
        return escape(str(error))
    return (f"已記住：{escape(key)}：{escape(value)}" if operation == "set"
            else f"已忘記：{escape(key)}")
