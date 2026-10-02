from __future__ import annotations

import re
from contextvars import ContextVar
from datetime import datetime
from typing import Callable

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .course_tracking import ProgressSession, accept_reply
from .course_tracking_store import session_from_firestore, session_to_firestore


class FirestoreDatabase:
    """Firestore-backed task and Telegram receipt repository for Cloud Run."""

    def __init__(self, project_id: str | None, database_id: str, collection_prefix: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", collection_prefix):
            raise ValueError("CHRONOS_FIRESTORE_COLLECTION_PREFIX may contain only letters, digits, underscores, and hyphens")
        self.client = firestore.Client(project=project_id, database=database_id)
        self.tasks = self.client.collection(f"{collection_prefix}_tasks")
        self.updates = self.client.collection(f"{collection_prefix}_telegram_updates")
        self.meta = self.client.collection(f"{collection_prefix}_meta")
        self.course_sessions = self.client.collection(f"{collection_prefix}_course_sessions")
        self.study_deliveries = self.client.collection(f"{collection_prefix}_study_deliveries")
        self._transaction: ContextVar[firestore.Transaction | None] = ContextVar(
            "firestore_transaction", default=None
        )

    def initialize(self) -> None:
        # Firestore collections are created on their first write.
        return None

    def mutate_study_delivery(self, key: str, transition: Callable) -> dict:
        """Transaction callbacks must be pure: Firestore can replay them."""
        def mutate(transaction):
            reference = self.study_deliveries.document(key)
            snapshot = reference.get(transaction=transaction)
            state = transition(snapshot.to_dict() if snapshot.exists else None)
            transaction.set(reference, state)
            return state

        return self._run_transaction(mutate)

    def get_study_delivery(self, key: str) -> dict | None:
        snapshot = self.study_deliveries.document(key).get()
        return snapshot.to_dict() if snapshot.exists else None

    @staticmethod
    def _task_data(task_id: int, data: dict) -> dict:
        return {"id": task_id, **data}

    def _run_transaction(self, operation):
        transaction = self.client.transaction()

        @firestore.transactional
        def run(active_transaction):
            token = self._transaction.set(active_transaction)
            try:
                return operation(active_transaction)
            finally:
                self._transaction.reset(token)

        return run(transaction)

    def create_task(self, title: str, due_at: datetime | None, project: str | None, created_at: datetime) -> dict:
        def create(transaction):
            counter_ref = self.meta.document("task_counter")
            counter = counter_ref.get(transaction=transaction)
            task_id = int(counter.get("next_id")) if counter.exists else 1
            data = {
                "title": title,
                "project": project,
                "due_at": due_at.isoformat() if due_at else None,
                "status": "open",
                "created_at": created_at.isoformat(),
                "completed_at": None,
            }
            transaction.set(counter_ref, {"next_id": task_id + 1})
            transaction.create(self.tasks.document(str(task_id)), data)
            return self._task_data(task_id, data)

        active = self._transaction.get()
        return create(active) if active is not None else self._run_transaction(create)

    def list_open_tasks(self) -> list[dict]:
        query = self.tasks.where(filter=FieldFilter("status", "==", "open"))
        active = self._transaction.get()
        snapshots = query.stream(transaction=active) if active is not None else query.stream()
        result = [self._task_data(int(snapshot.id), snapshot.to_dict()) for snapshot in snapshots]
        return sorted(result, key=lambda task: (task.get("due_at") is None, task.get("due_at") or "", task["id"]))

    def _update_open_task(self, task_id: int, values: dict) -> bool:
        def update(transaction):
            reference = self.tasks.document(str(task_id))
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists or snapshot.get("status") != "open":
                return False
            transaction.update(reference, values)
            return True

        active = self._transaction.get()
        return update(active) if active is not None else self._run_transaction(update)

    def complete_task(self, task_id: int, completed_at: datetime) -> bool:
        return self._update_open_task(task_id, {"status": "done", "completed_at": completed_at.isoformat()})

    def postpone_task(self, task_id: int, due_at: datetime) -> bool:
        return self._update_open_task(task_id, {"due_at": due_at.isoformat()})

    def edit_task(self, task_id: int, title: str, due_at: datetime | None, project: str | None) -> bool:
        return self._update_open_task(
            task_id,
            {"title": title, "due_at": due_at.isoformat() if due_at else None, "project": project},
        )

    def clear_tasks(self) -> int:
        def clear(transaction):
            snapshots = list(self.tasks.stream(transaction=transaction))
            for snapshot in snapshots:
                transaction.delete(snapshot.reference)
            return len(snapshots)

        active = self._transaction.get()
        return clear(active) if active is not None else self._run_transaction(clear)

    def get_update(self, update_id: int) -> dict | None:
        reference = self.updates.document(str(update_id))
        active = self._transaction.get()
        snapshot = reference.get(transaction=active) if active is not None else reference.get()
        return snapshot.to_dict() if snapshot.exists else None

    def process_update(self, update_id: int, action: Callable[[], str]) -> dict:
        def process(transaction):
            reference = self.updates.document(str(update_id))
            snapshot = reference.get(transaction=transaction)
            if snapshot.exists:
                return snapshot.to_dict()
            reply = action()
            receipt = {"reply": reply, "delivered": False}
            transaction.create(reference, receipt)
            return receipt

        return self._run_transaction(process)

    def mark_update_delivered(self, update_id: int) -> None:
        self.updates.document(str(update_id)).update({"delivered": True})

    def create_course_session(self, session: ProgressSession) -> ProgressSession:
        """Create once by deterministic session id; retries return the existing value."""
        def create(transaction):
            reference = self.course_sessions.document(session.session_id)
            snapshot = reference.get(transaction=transaction)
            if snapshot.exists:
                return session_from_firestore(snapshot.to_dict())
            transaction.create(reference, session_to_firestore(session))
            return session

        return self._run_transaction(create)

    def record_course_reply(self, prompt_id: int, message_id: int, text: str) -> str:
        """Read state and write progress inside the webhook receipt transaction."""
        def record(transaction):
            query = self.course_sessions.where(filter=FieldFilter("prompt_message_id", "==", prompt_id))
            snapshots = list(query.stream(transaction=transaction))
            if len(snapshots) != 1:
                return "這則訊息不是課後進度問題，請回覆原始課後訊息。"
            snapshot = snapshots[0]
            session = session_from_firestore(snapshot.to_dict())
            answered = accept_reply(session, reply_to_message_id=prompt_id,
                                    reply_message_id=message_id, text=text)
            if answered is None:
                return "這堂課已記錄或已結束，未變更進度。"
            transaction.update(snapshot.reference, session_to_firestore(answered))
            return f"已記錄{session.course_name}（{session.class_date}）的進度。"

        active = self._transaction.get()
        return record(active) if active is not None else self._run_transaction(record)

    def get_course_session(self, session_id: str) -> ProgressSession | None:
        snapshot = self.course_sessions.document(session_id).get()
        return session_from_firestore(snapshot.to_dict()) if snapshot.exists else None

    def list_pending_course_sessions(self) -> list[ProgressSession]:
        query = self.course_sessions.where(filter=FieldFilter("status", "in", ["pending", "reminded_once", "reminded_twice"]))
        return [session_from_firestore(snapshot.to_dict()) for snapshot in query.stream()]

    def mutate_course_session(self, session_id: str, transition: Callable) -> ProgressSession:
        def mutate(transaction):
            reference = self.course_sessions.document(session_id)
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists:
                raise KeyError(session_id)
            updated = transition(session_from_firestore(snapshot.to_dict()))
            transaction.update(reference, session_to_firestore(updated))
            return updated
        return self._run_transaction(mutate)

    def save_course_session(self, session: ProgressSession) -> ProgressSession:
        def save(transaction):
            reference = self.course_sessions.document(session.session_id)
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists:
                raise KeyError(session.session_id)
            transaction.update(reference, session_to_firestore(session))
            return session

        return self._run_transaction(save)
