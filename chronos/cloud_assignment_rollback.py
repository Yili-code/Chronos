"""Preview and explicitly confirm a bounded assignment rollback; never auto-run."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from hashlib import sha256
import json
import os

from .assignments import to_record
from .db import create_database
from .settings import settings
from .telegram import TelegramClient


def _window():
    try:
        start = datetime.fromisoformat(os.environ["CHRONOS_ASSIGNMENT_ROLLBACK_START"])
        end = datetime.fromisoformat(os.environ["CHRONOS_ASSIGNMENT_ROLLBACK_END"])
    except (KeyError, ValueError) as error:
        raise ValueError("rollback window is required") from error
    if start.utcoffset() is None or end.utcoffset() is None or not timedelta(0) <= end - start <= timedelta(hours=1):
        raise ValueError("rollback requires an aware window of at most one hour")
    return start, end


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _plan(db, start, end):
    selected = []
    skipped = 0
    for key in db.list_assignment_keys():
        record = db.get_assignment(key)
        if not record or not start <= record["assignment"].discovered_at <= end:
            continue
        snapshot = db.tasks.document(str(record["task_id"])).get()
        task = snapshot.to_dict() if snapshot.exists else None
        if (not task or task.get("status") != "open" or record["assignment"].completed_at
                or not start <= datetime.fromisoformat(task["created_at"]) <= end):
            skipped += 1
            continue
        delivery_key = f"assignment:{key}:discovered"
        delivery = db.get_study_delivery(delivery_key)
        selected.append({"key": key, "task_id": record["task_id"], "task": task,
                         "record": to_record(record["assignment"]), "delivery_key": delivery_key,
                         "delivery": delivery})
    if len(selected) > 150:
        raise ValueError("rollback is limited to 150 records; narrow the time window")
    selected.sort(key=lambda item: item["key"])
    manifest = {"start": start.isoformat(), "end": end.isoformat(), "items": selected}
    return manifest, _digest(manifest), skipped


async def rollback():
    start, end = _window()
    db = create_database(settings)
    if not hasattr(db, "assignments") or not hasattr(db, "_run_transaction"):
        raise RuntimeError("production Firestore is required")
    apply = os.environ.get("CHRONOS_ASSIGNMENT_ROLLBACK_APPLY", "false") == "true"
    confirmation = os.environ.get("CHRONOS_ASSIGNMENT_ROLLBACK_CONFIRM", "")
    if apply and (len(confirmation) != 64 or any(c not in "0123456789abcdef" for c in confirmation)):
        raise ValueError("apply requires the exact plan hash from a prior preview")
    key = "assignment-rollback-" + confirmation
    persisted = db.get_task_action_state(key) if apply else None
    if persisted:
        if persisted["start"] != start.isoformat() or persisted["end"] != end.isoformat():
            raise ValueError("rollback window differs from the confirmed plan")
        state = persisted
    else:
        manifest, plan_hash, skipped = _plan(db, start, end)
        if not apply:
            return {"mode": "preview", "plan_hash": plan_hash, "eligible": len(manifest["items"]),
                    "skipped": skipped, "writes": 0}
        if confirmation != plan_hash:
            raise ValueError("rollback plan changed; preview again before confirming")
        if not manifest["items"]:
            return {"mode": "apply", "rolled_back": 0, "telegram_pending": 0}
        def commit(transaction):
            previous = db.task_action_state.document(key).get(transaction=transaction)
            if previous.exists:
                return previous.to_dict()
            # Read and revalidate every document before any deletion.
            for item in manifest["items"]:
                assignment = db.assignments.document(item["key"]).get(transaction=transaction)
                task = db.tasks.document(str(item["task_id"])).get(transaction=transaction)
                delivery = db.study_deliveries.document(item["delivery_key"]).get(transaction=transaction)
                if (not assignment.exists or assignment.to_dict() != {"task_id": item["task_id"], "record": item["record"]}
                        or not task.exists or task.to_dict() != item["task"]
                        or (delivery.to_dict() if delivery.exists else None) != item["delivery"]):
                    raise ValueError("rollback target changed; preview again")
            pending = [item["delivery"]["message_id"] for item in manifest["items"]
                       if item["delivery"] and item["delivery"].get("message_id")]
            state = {"start": start.isoformat(), "end": end.isoformat(), "rolled_back": len(manifest["items"]),
                     "pending_messages": pending}
            for item in manifest["items"]:
                transaction.delete(db.assignments.document(item["key"]))
                transaction.delete(db.tasks.document(str(item["task_id"])))
                if item["delivery"] is not None:
                    transaction.delete(db.study_deliveries.document(item["delivery_key"]))
            transaction.set(db.task_action_state.document(key), state)
            return state
        state = db._run_transaction(commit)
    bot = TelegramClient(settings.telegram_bot_token)
    if not settings.telegram_chat_id or not bot.enabled:
        return {"mode": "apply", "rolled_back": state["rolled_back"], "telegram_pending": len(state["pending_messages"])}
    for message_id in list(state["pending_messages"]):
        try:
            result = await bot.request("deleteMessage", {"chat_id": settings.telegram_chat_id, "message_id": message_id})
        except Exception:
            continue
        missing = result.get("error_code") == 400 and "message to delete not found" in result.get("description", "").lower()
        if result.get("ok") or missing:
            state = db.mutate_task_action_state(key, lambda old: {**old, "pending_messages": [m for m in old["pending_messages"] if m != message_id]})
    return {"mode": "apply", "rolled_back": state["rolled_back"], "telegram_pending": len(state["pending_messages"])}


def main():
    try:
        result = asyncio.run(rollback())
    except Exception as error:
        print(json.dumps({"rollback": "failed", "failure_type": type(error).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if not result.get("telegram_pending") else 1


if __name__ == "__main__":
    raise SystemExit(main())
