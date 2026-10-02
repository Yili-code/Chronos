from __future__ import annotations

import re
from contextvars import ContextVar
from datetime import datetime
from typing import Callable

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .course_tracking import ProgressSession
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
        self._transaction: ContextVar[firestore.Transaction | None] = ContextVar(
            "firestore_transaction", default=None
        )

    def initialize(self) -> None:
        # Firestore collections are created on their first write.
        return None

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

    def get_course_session(self, session_id: str) -> ProgressSession | None:
        snapshot = self.course_sessions.document(session_id).get()
        return session_from_firestore(snapshot.to_dict()) if snapshot.exists else None

    def save_course_session(self, session: ProgressSession) -> ProgressSession:
        def save(transaction):
            reference = self.course_sessions.document(session.session_id)
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists:
                raise KeyError(session.session_id)
            transaction.update(reference, session_to_firestore(session))
            return session

        return self._run_transaction(save)
