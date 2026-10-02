import sqlite3
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from .course_tracking import ProgressSession, accept_reply, mark_missed_at_day_end
from .course_tracking_store import session_from_firestore, session_to_firestore


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
CREATE TABLE IF NOT EXISTS course_sessions (
    session_id TEXT PRIMARY KEY,
    course_key TEXT NOT NULL,
    course_name TEXT NOT NULL,
    class_date TEXT NOT NULL,
    prompt_message_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    reminder_count INTEGER NOT NULL DEFAULT 0,
    reported_progress TEXT,
    reply_message_id INTEGER
);
CREATE TABLE IF NOT EXISTS study_deliveries (
    delivery_key TEXT PRIMARY KEY,
    state_json TEXT NOT NULL
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

    def mutate_study_delivery(self, key: str, transition: Callable) -> dict:
        """Atomically apply a pure delivery-state transition; never send here."""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT state_json FROM study_deliveries WHERE delivery_key=?", (key,)
            ).fetchone()
            state = transition(json.loads(row[0]) if row else None)
            connection.execute(
                "INSERT INTO study_deliveries VALUES (?, ?) ON CONFLICT(delivery_key) "
                "DO UPDATE SET state_json=excluded.state_json", (key, json.dumps(state))
            )
            return state

    def get_study_delivery(self, key: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT state_json FROM study_deliveries WHERE delivery_key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_failed_study_deliveries(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT delivery_key, state_json FROM study_deliveries").fetchall()
        return [row[0] for row in rows if not row[0].startswith("notice:")
                and json.loads(row[1])["status"] in {"uncertain", "failed"}]

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

    def create_course_session(self, session: ProgressSession) -> ProgressSession:
        data = session_to_firestore(session)
        with self.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO course_sessions
                (session_id, course_key, course_name, class_date, prompt_message_id,
                 status, reminder_count, reported_progress, reply_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(data[field] for field in (
                    "session_id", "course_key", "course_name", "class_date",
                    "prompt_message_id", "status", "reminder_count",
                    "reported_progress", "reply_message_id",
                )),
            )
        return self.get_course_session(session.session_id) or session

    def record_course_reply(self, prompt_id: int, message_id: int, text: str, *, local_date=None) -> str:
        """Called inside process_update, sharing the webhook receipt transaction."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM course_sessions WHERE prompt_message_id = ?", (prompt_id,)
            ).fetchone()
            if row is None:
                return "這則訊息不是課後進度問題，請回覆原始課後訊息。"
            session = session_from_firestore(dict(row))
            if local_date is not None:
                expired = mark_missed_at_day_end(session, local_date=local_date)
                if expired != session:
                    self.save_course_session(expired)
                    return "這堂課的回覆期限已結束，未變更進度。"
            answered = accept_reply(session, reply_to_message_id=prompt_id,
                                    reply_message_id=message_id, text=text)
            if answered is None:
                return "這堂課已記錄或已結束，未變更進度。"
            self.save_course_session(answered)
            return f"已記錄{session.course_name}（{session.class_date}）的進度。"

    def get_course_session(self, session_id: str) -> ProgressSession | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM course_sessions WHERE session_id = ?", (session_id,)).fetchone()
        return session_from_firestore(dict(row)) if row else None

    def list_pending_course_sessions(self) -> list[ProgressSession]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM course_sessions WHERE status NOT IN ('answered','missed')").fetchall()
        return [session_from_firestore(dict(row)) for row in rows]

    def mutate_course_session(self, session_id: str, transition: Callable) -> ProgressSession:
        with self.transaction():
            current = self.get_course_session(session_id)
            if current is None:
                raise KeyError(session_id)
            return self.save_course_session(transition(current))

    def save_course_session(self, session: ProgressSession) -> ProgressSession:
        data = session_to_firestore(session)
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE course_sessions SET course_key=?, course_name=?, class_date=?,
                prompt_message_id=?, status=?, reminder_count=?, reported_progress=?, reply_message_id=?
                WHERE session_id=?""",
                tuple(data[field] for field in (
                    "course_key", "course_name", "class_date", "prompt_message_id",
                    "status", "reminder_count", "reported_progress", "reply_message_id",
                )) + (session.session_id,),
            )
        if cursor.rowcount != 1:
            raise KeyError(session.session_id)
        return session


def create_database(settings):
    if settings.database_backend == "firestore":
        from .firestore_db import FirestoreDatabase

        return FirestoreDatabase(
            project_id=settings.firestore_project_id or None,
            database_id=settings.firestore_database,
            collection_prefix=settings.firestore_collection_prefix,
        )
    return Database(settings.database_path)
