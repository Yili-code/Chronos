import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator


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
