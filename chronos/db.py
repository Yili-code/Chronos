import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    project TEXT,
    due_at TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'done')),
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_at);
CREATE TABLE IF NOT EXISTS telegram_updates (
    update_id INTEGER PRIMARY KEY,
    reply TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._transaction: ContextVar[sqlite3.Connection | None] = ContextVar("transaction", default=None)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        active = self._transaction.get()
        if active is not None:
            yield active
            return
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Group synchronous task mutations and update receipts atomically."""
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            token = self._transaction.set(connection)
            try:
                yield connection
            finally:
                self._transaction.reset(token)

    def get_update(self, update_id: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT reply, delivered FROM telegram_updates WHERE update_id = ?", (update_id,)
            ).fetchone()
        return dict(row) if row else None

    def mark_update_delivered(self, update_id: int) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE telegram_updates SET delivered = 1 WHERE update_id = ?", (update_id,))

    def create_task(self, title: str, due_at: datetime | None, project: str | None, created_at: datetime) -> dict:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO tasks(title, project, due_at, created_at) VALUES (?, ?, ?, ?)",
                (title, project, due_at.isoformat() if due_at else None, created_at.isoformat()),
            )
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def list_open_tasks(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE status = 'open' ORDER BY due_at IS NULL, due_at, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def complete_task(self, task_id: int, completed_at: datetime) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ? AND status = 'open'",
                (completed_at.isoformat(), task_id),
            )
        return cursor.rowcount == 1

    def postpone_task(self, task_id: int, due_at: datetime) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET due_at = ? WHERE id = ? AND status = 'open'", (due_at.isoformat(), task_id)
            )
        return cursor.rowcount == 1

    def edit_task(self, task_id: int, title: str, due_at: datetime | None, project: str | None) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET title = ?, due_at = ?, project = ? WHERE id = ? AND status = 'open'",
                (title, due_at.isoformat() if due_at else None, project, task_id),
            )
        return cursor.rowcount == 1

    def clear_tasks(self) -> int:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM tasks")
        return cursor.rowcount

    def process_update(self, update_id: int, action: Callable[[], str]) -> dict:
        """Persist a Telegram mutation and its reply in one transaction."""
        with self.transaction() as connection:
            receipt = self.get_update(update_id)
            if receipt is None:
                reply = action()
                connection.execute(
                    "INSERT INTO telegram_updates(update_id, reply) VALUES (?, ?)", (update_id, reply)
                )
                return {"reply": reply, "delivered": False}
            return receipt


def create_database(settings):
    if settings.database_backend == "firestore":
        from .firestore_db import FirestoreDatabase

        return FirestoreDatabase(
            project_id=settings.firestore_project_id or None,
            database_id=settings.firestore_database,
            collection_prefix=settings.firestore_collection_prefix,
        )
    return Database(settings.database_path)
