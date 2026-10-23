import sqlite3
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterator

from .reply_policy import is_no_change_reply
from .course_tracking import ProgressReportKind, ProgressSession, accept_reply, mark_missed_at_day_end
from .course_tracking_store import session_from_firestore, session_to_firestore
from .assignments import Assignment, to_record, from_record, set_deadline, owner_deadline_edit
from .task_timing import normalize_timing, task_sort_key
from .task_actions import TaskActions, task_fields
from .database_metrics import measured
from .assignment_bridge import TRACKED_COURSES


SCHEMA = """
CREATE TABLE IF NOT EXISTS task_action_state (state_key TEXT PRIMARY KEY, state_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mail_state (state_key TEXT PRIMARY KEY, state_json TEXT NOT NULL);
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
    delivered INTEGER NOT NULL DEFAULT 0,
    messages_json TEXT,
    delivered_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pending_task_edits (
    update_id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    instruction TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tag_aliases (
    project TEXT PRIMARY KEY,
    alias TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS telegram_task_messages (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    PRIMARY KEY(chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS course_sessions (
    session_id TEXT PRIMARY KEY,
    course_key TEXT NOT NULL,
    course_name TEXT NOT NULL,
    class_date TEXT NOT NULL,
    prompt_message_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    reminder_count INTEGER NOT NULL DEFAULT 0,
    report_kind TEXT,
    reported_progress TEXT,
    reply_message_id INTEGER
);
CREATE TABLE IF NOT EXISTS study_deliveries (
    delivery_key TEXT PRIMARY KEY,
    state_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assignments (source_key TEXT PRIMARY KEY, task_id INTEGER NOT NULL UNIQUE, record_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calendar_snapshot (id INTEGER PRIMARY KEY CHECK(id=1), record_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calendar_sync (id INTEGER PRIMARY KEY CHECK(id=1), record_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calendar_overrides (id INTEGER PRIMARY KEY CHECK(id=1), record_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assignment_scan_state (id INTEGER PRIMARY KEY CHECK(id=1), record_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exams (source_key TEXT PRIMARY KEY, record_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS announcements (source_key TEXT PRIMARY KEY, record_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_usage (
    day TEXT NOT NULL,
    operation TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY(day, operation)
);
CREATE TABLE IF NOT EXISTS course_day_decisions (source_key TEXT PRIMARY KEY, decision TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exam_notice_plans (source_key TEXT PRIMARY KEY, record_json TEXT NOT NULL);
"""

REMOVED_STUDY_TABLES = (
    "study_notes",
    "summary_jobs",
    "material_selections",
    "study_poll_jobs",
    "preparation_jobs",
    "ai_daily_budget",
)


class Database(TaskActions):
    def _run_task_action(self, operation):
        scope = self.connect() if self._transaction.get() is not None else self.transaction()
        with scope:
            return operation()

    @measured
    def get_task_action_state(self, key):
        with self.connect() as connection:
            row = connection.execute("SELECT state_json FROM task_action_state WHERE state_key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _save_task_action_state(self, key, state):
        with self.connect() as connection:
            connection.execute("INSERT INTO task_action_state VALUES (?, ?) ON CONFLICT(state_key) "
                               "DO UPDATE SET state_json=excluded.state_json", (key, json.dumps(state)))

    def _load_task_action(self, task_id):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id=? AND status='open'", (task_id,)).fetchone()
            if row is None:
                return None
            linked = connection.execute("SELECT * FROM assignments WHERE task_id=?", (task_id,)).fetchall()
            return {"task": task_fields(self._task_data(row)), "aliases": self.list_tag_aliases(),
                    "assignments": {r["source_key"]: {"task_id": task_id, "record": json.loads(r["record_json"])} for r in linked}}

    def _write_task_action(self, task_id, state, aliases):
        task = state["task"]
        with self.connect() as connection:
            connection.execute("UPDATE tasks SET title=?, due_at=?, project=?, timing_json=? WHERE id=?",
                               (task["title"], task["due_at"], task["project"], json.dumps(task["timing"]), task_id))
            for key, linked in state["assignments"].items():
                connection.execute("UPDATE assignments SET record_json=? WHERE source_key=?", (json.dumps(linked["record"]), key))
            for key, alias in state["aliases"].items():
                if alias is None:
                    connection.execute("DELETE FROM tag_aliases WHERE project=?", (key,))
                else:
                    self.save_tag_alias(key, alias)

    def get_mail_state(self, key):
        with self.connect() as connection:
            row = connection.execute("SELECT state_json FROM mail_state WHERE state_key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def mutate_mail_state(self, key, transition):
        scope = self.connect() if self._transaction.get() is not None else self.transaction()
        with scope as connection:
            state = transition(self.get_mail_state(key))
            connection.execute(
                "INSERT INTO mail_state VALUES (?, ?) ON CONFLICT(state_key) DO UPDATE SET state_json=excluded.state_json",
                (key, json.dumps(state)),
            )
            return state

    def __init__(self, path: Path):
        self.path = path
        self._transaction: ContextVar[sqlite3.Connection | None] = ContextVar("transaction", default=None)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            for table in REMOVED_STUDY_TABLES:
                connection.execute(f"DROP TABLE IF EXISTS {table}")
            task_columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
            if "timing_json" not in task_columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN timing_json TEXT NOT NULL DEFAULT '{}'")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(course_sessions)")}
            if "survey_task_id" not in columns:
                connection.execute("ALTER TABLE course_sessions ADD COLUMN survey_task_id INTEGER")
            if "report_kind" not in columns:
                connection.execute("ALTER TABLE course_sessions ADD COLUMN report_kind TEXT")
            update_columns = {row[1] for row in connection.execute("PRAGMA table_info(telegram_updates)")}
            if "messages_json" not in update_columns:
                connection.execute("ALTER TABLE telegram_updates ADD COLUMN messages_json TEXT")
            if "delivered_count" not in update_columns:
                connection.execute(
                    "ALTER TABLE telegram_updates ADD COLUMN delivered_count INTEGER NOT NULL DEFAULT 0"
                )

    @measured
    def check_ready(self) -> None:
        with self.connect() as connection:
            connection.execute("SELECT 1").fetchone()

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
                "SELECT reply, delivered, messages_json, delivered_count "
                "FROM telegram_updates WHERE update_id = ?", (update_id,)
            ).fetchone()
        if row is None:
            return None
        receipt = {"reply": row["reply"], "delivered": bool(row["delivered"])}
        if row["messages_json"]:
            receipt["messages"] = json.loads(row["messages_json"])
            receipt["delivered_count"] = row["delivered_count"]
        return receipt

    def mark_update_delivered(self, update_id: int) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE telegram_updates SET delivered = 1 WHERE update_id = ?", (update_id,))

    def mark_update_message_delivered(self, update_id: int, delivered_count: int) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT messages_json FROM telegram_updates WHERE update_id=?", (update_id,)
            ).fetchone()
            if row is None:
                return
            messages = json.loads(row[0]) if row[0] else []
            connection.execute(
                "UPDATE telegram_updates SET delivered_count=?, delivered=? WHERE update_id=?",
                (delivered_count, int(delivered_count >= len(messages)), update_id),
            )

    def save_pending_task_edit(
        self, update_id: int, task_id: int, position: int, instruction: str, created_at: datetime
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO pending_task_edits VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(update_id) DO UPDATE SET task_id=excluded.task_id, "
                "position=excluded.position, instruction=excluded.instruction, created_at=excluded.created_at",
                (update_id, task_id, position, instruction, created_at.isoformat()),
            )

    def get_pending_task_edit(self, update_id: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pending_task_edits WHERE update_id=?", (update_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete_pending_task_edit(self, update_id: int) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM pending_task_edits WHERE update_id=?", (update_id,))

    def save_tag_alias(
        self, project: str, alias: str, existing_aliases: dict[str, str] | None = None
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO tag_aliases VALUES (?, ?) "
                "ON CONFLICT(project) DO UPDATE SET alias=excluded.alias",
                (project, alias),
            )

    def list_tag_aliases(self) -> dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT project, alias FROM tag_aliases").fetchall()
        return {row[0]: row[1] for row in rows}

    def bind_task_message(self, chat_id: int, message_id: int, task_id: int) -> None:
        with self.connect() as connection:
            connection.execute("INSERT INTO telegram_task_messages VALUES (?, ?, ?) "
                               "ON CONFLICT(chat_id, message_id) DO UPDATE SET task_id=excluded.task_id",
                               (chat_id, message_id, task_id))

    def task_for_message(self, chat_id: int, message_id: int) -> int | None:
        with self.connect() as connection:
            row = connection.execute("SELECT task_id FROM telegram_task_messages WHERE chat_id=? AND message_id=?",
                                     (chat_id, message_id)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _task_data(row) -> dict:
        data = dict(row)
        data["timing"] = json.loads(data.pop("timing_json", "{}"))
        return data

    def create_task(self, title: str, due_at: datetime | None, project: str | None, created_at: datetime, timing: dict | None = None) -> dict:
        timing = normalize_timing(timing, due_at)
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO tasks(title, project, due_at, created_at, timing_json) VALUES (?, ?, ?, ?, ?)",
                (title, project, due_at.isoformat() if due_at else None, created_at.isoformat(), json.dumps(timing)),
            )
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._task_data(row)

    def create_tasks(self, items: list[dict], created_at: datetime) -> list[dict]:
        scope = self.connect() if self._transaction.get() is not None else self.transaction()
        with scope:
            return [self.create_task(item["title"], item.get("due_at"), item.get("project"),
                                     created_at, item.get("timing")) for item in items]

    def continue_task(self, task, title, now):
        """Close a session task and create its successor in the caller's transaction."""
        successor = self.create_task(title, datetime.fromisoformat(task['due_at']) if task.get('due_at') else None,
                                     task.get('project'), now, task.get('timing'))
        self.complete_task(task['id'], now)
        return successor

    def get_task(self, task_id: int) -> dict | None:
        """Read a task including completion evidence; never changes learning feedback."""
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task_data(row) if row is not None else None

    @measured
    def list_open_tasks(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE status = 'open' ORDER BY due_at IS NULL, due_at, id"
            ).fetchall()
            # Use source identity, never the editable task project/title. Keep
            # historical records intact without presenting them as current work.
            excluded = {row['task_id'] for row in connection.execute(
                "SELECT task_id, record_json FROM assignments")
                if json.loads(row['record_json']).get('course_id') not in TRACKED_COURSES}
        return sorted((self._task_data(row) for row in rows if row['id'] not in excluded), key=task_sort_key)

    def create_assignment(self, assignment: Assignment) -> dict:
        """Persist source identity and its ordinary task together, exactly once."""
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM assignments WHERE source_key=?", (assignment.key,)).fetchone()
            if row:
                return {"task_id": row["task_id"], "assignment": from_record(json.loads(row["record_json"]))}
            task = self.create_task(assignment.title, assignment.deadline, assignment.course_id, assignment.discovered_at)
            connection.execute("INSERT INTO assignments VALUES (?, ?, ?)",
                               (assignment.key, task["id"], json.dumps(to_record(assignment))))
            return {"task_id": task["id"], "assignment": assignment}

    def get_assignment(self, key: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM assignments WHERE source_key=?", (key,)).fetchone()
            if row is None:
                return None
            task = connection.execute("SELECT * FROM tasks WHERE id=?", (row["task_id"],)).fetchone()
        item = from_record(json.loads(row["record_json"]))
        if task and task["completed_at"]:
            item = replace(item, completed_at=datetime.fromisoformat(task["completed_at"]))
        return {"task_id": row["task_id"], "assignment": item, "task_exists": task is not None}

    @measured
    def list_assignment_keys(self) -> list[str]:
        with self.connect() as connection:
            return [row[0] for row in connection.execute("SELECT source_key FROM assignments ORDER BY source_key")]

    def refresh_assignment(self, incoming):
        from .assignments import merge_source_observation
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM assignments WHERE source_key=?", (incoming.key,)).fetchone()
            if not row:
                return False
            task = connection.execute("SELECT * FROM tasks WHERE id=?", (row['task_id'],)).fetchone()
            if not task or task['status'] != 'open':
                return False
            previous = from_record(json.loads(row['record_json']))
            merged = merge_source_observation(previous, incoming)
            if merged == previous:
                return False
            title = merged.title if task['title'] == previous.title else task['title']
            connection.execute("UPDATE assignments SET record_json=? WHERE source_key=?", (json.dumps(to_record(merged)), incoming.key))
            connection.execute("UPDATE tasks SET title=?, due_at=? WHERE id=?", (title,
                merged.deadline.isoformat() if merged.deadline else None, row['task_id']))
            return True

    def get_calendar_snapshot(self):
        with self.connect() as connection:
            row = connection.execute("SELECT record_json FROM calendar_snapshot WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def save_exam(self, record):
        from .exams import validate_exam, exam_key
        record = validate_exam(record)
        with self.connect() as connection:
            connection.execute("INSERT INTO exams VALUES(?, ?) ON CONFLICT(source_key) DO UPDATE SET record_json=excluded.record_json", (exam_key(record), json.dumps(record)))
        return record

    def save_announcement(self, record):
        from .announcements import canonical_announcement
        key, record = canonical_announcement(record)
        with self.connect() as connection:
            connection.execute('INSERT OR IGNORE INTO announcements VALUES (?, ?)', (key, json.dumps(record)))
        return key

    @measured
    def list_announcements(self):
        with self.connect() as connection:
            return [(row[0], json.loads(row[1])) for row in connection.execute('SELECT source_key, record_json FROM announcements ORDER BY source_key')]

    def mutate_ai_usage(self, day: str, operation: str, transition: Callable) -> dict:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT record_json FROM ai_usage WHERE day=? AND operation=?",
                (day, operation),
            ).fetchone()
            state = transition(json.loads(row[0]) if row else None)
            connection.execute(
                "INSERT INTO ai_usage VALUES (?, ?, ?) "
                "ON CONFLICT(day, operation) DO UPDATE SET record_json=excluded.record_json",
                (day, operation, json.dumps(state)),
            )
            return state

    def list_ai_usage(self, day: str) -> dict[str, dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT operation, record_json FROM ai_usage WHERE day=? ORDER BY operation", (day,)
            ).fetchall()
        return {row[0]: json.loads(row[1]) for row in rows}

    def save_course_day_decision(self, key, decision):
        if decision not in {'class', 'off', 'auto'}:
            raise ValueError('invalid course-day decision')
        with self.connect() as connection:
            connection.execute("INSERT INTO course_day_decisions VALUES(?, ?) ON CONFLICT(source_key) DO UPDATE SET decision=excluded.decision", (key, decision))

    def get_course_day_decision(self, key):
        with self.connect() as connection:
            row = connection.execute("SELECT decision FROM course_day_decisions WHERE source_key=?", (key,)).fetchone()
        return row[0] if row else 'auto'

    def freeze_exam_notice_plan(self, key, notices):
        with self.transaction() as connection:
            connection.execute("INSERT OR IGNORE INTO exam_notice_plans VALUES(?, ?)", (key, json.dumps(notices)))
            row = connection.execute("SELECT record_json FROM exam_notice_plans WHERE source_key=?", (key,)).fetchone()
            return json.loads(row[0])

    def list_exams(self):
        with self.connect() as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT record_json FROM exams ORDER BY source_key")]

    def mutate_calendar_sync(self, transition):
        with self.transaction() as connection:
            row = connection.execute("SELECT record_json FROM calendar_sync WHERE id=1").fetchone()
            state = transition(json.loads(row[0]) if row else None)
            connection.execute("INSERT INTO calendar_sync VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET record_json=excluded.record_json", (json.dumps(state),))
            return state

    def save_calendar_snapshot(self, snapshot):
        from .calendar_snapshot import validate_snapshot
        snapshot = validate_snapshot(snapshot)
        with self.transaction() as connection:
            current = self.get_calendar_snapshot()
            if current and datetime.fromisoformat(current["fetched_at"]) > datetime.fromisoformat(snapshot["fetched_at"]):
                return current
            connection.execute("INSERT INTO calendar_snapshot VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET record_json=excluded.record_json", (json.dumps(snapshot),))
        return snapshot

    def get_calendar_overrides(self):
        with self.connect() as connection:
            row = connection.execute("SELECT record_json FROM calendar_overrides WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def save_calendar_overrides(self, record):
        from .calendar_overrides import CalendarOverrides
        value = CalendarOverrides.from_record(record).to_record()
        scope = self.connect() if self._transaction.get() is not None else self.transaction()
        with scope as connection:
            connection.execute("INSERT INTO calendar_overrides VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET record_json=excluded.record_json", (json.dumps(value),))
        return value

    def get_assignment_scan_state(self):
        with self.connect() as connection:
            row = connection.execute("SELECT record_json FROM assignment_scan_state WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def save_assignment_scan_state(self, record):
        from .assignment_scan_state import AssignmentScanState
        value = AssignmentScanState.from_record(record).to_record()
        scope = self.connect() if self._transaction.get() is not None else self.transaction()
        with scope as connection:
            connection.execute("INSERT INTO assignment_scan_state VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET record_json=excluded.record_json", (json.dumps(value),))
        return value

    def complete_task(self, task_id: int, completed_at: datetime) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ? AND status = 'open'",
                (completed_at.isoformat(), task_id),
            )
            if cursor.rowcount == 1:
                row = connection.execute("SELECT record_json FROM assignments WHERE task_id=?", (task_id,)).fetchone()
                if row:
                    data = json.loads(row[0])
                    data["completed_at"] = completed_at.isoformat()
                    connection.execute("UPDATE assignments SET record_json=? WHERE task_id=?", (json.dumps(data), task_id))
        return cursor.rowcount == 1

    def confirm_assignment_deadline(self, task_id: int, deadline: datetime) -> bool:
        scope = self.connect() if self._transaction.get() is not None else self.transaction()
        with scope as connection:
            row = connection.execute("SELECT record_json FROM assignments WHERE task_id=?", (task_id,)).fetchone()
            task = connection.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None or task is None or task["status"] != "open":
                return False
            updated = set_deadline(from_record(json.loads(row[0])), deadline, origin="owner")
            connection.execute("UPDATE assignments SET record_json=? WHERE task_id=?", (json.dumps(to_record(updated)), task_id))
            connection.execute("UPDATE tasks SET due_at=?, timing_json=json_remove(timing_json, '$.due_date') WHERE id=?",
                               (updated.deadline.isoformat(), task_id))
            return True

    def postpone_task(self, task_id: int, due_at: datetime) -> bool:
        scope = self.connect() if self._transaction.get() is not None else self.transaction()
        with scope as connection:
            cursor = connection.execute(
                "UPDATE tasks SET due_at = ?, timing_json=json_remove(timing_json, '$.due_date') WHERE id = ? AND status = 'open'", (due_at.isoformat(), task_id)
            )
            if cursor.rowcount == 1:
                self._sync_assignment_deadline(connection, task_id, due_at)
        return cursor.rowcount == 1

    def edit_task(self, task_id: int, title: str, due_at: datetime | None, project: str | None, timing: dict | None = None) -> bool:
        scope = self.connect() if self._transaction.get() is not None else self.transaction()
        with scope as connection:
            cursor = connection.execute(
                "UPDATE tasks SET title = ?, due_at = ?, project = ?, "
                "timing_json = COALESCE(?, json_remove(timing_json, '$.due_date')) WHERE id = ? AND status = 'open'",
                (title, due_at.isoformat() if due_at else None, project,
                 json.dumps(normalize_timing(timing, due_at)) if timing is not None else None, task_id),
            )
            if cursor.rowcount == 1:
                self._sync_assignment_deadline(connection, task_id, due_at)
        return cursor.rowcount == 1

    def _sync_assignment_deadline(self, connection, task_id, deadline):
        row = connection.execute("SELECT record_json FROM assignments WHERE task_id=?", (task_id,)).fetchone()
        if row is not None:
            updated = owner_deadline_edit(from_record(json.loads(row[0])), deadline)
            connection.execute("UPDATE assignments SET record_json=? WHERE task_id=?",
                               (json.dumps(to_record(updated)), task_id))

    def clear_tasks(self) -> int:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM tasks")
            connection.execute("DELETE FROM task_action_state")
        return cursor.rowcount

    def process_update(self, update_id: int, action: Callable[[], str | dict]) -> dict:
        """Persist a Telegram mutation and its reply in one transaction."""
        with self.transaction() as connection:
            receipt = self.get_update(update_id)
            if receipt is None:
                output = action()
                if isinstance(output, str):
                    connection.execute(
                        "INSERT INTO telegram_updates(update_id, reply) VALUES (?, ?)",
                        (update_id, output),
                    )
                    return {"reply": output, "delivered": False}
                messages = output["messages"]
                reply = messages[0]["text"]
                connection.execute(
                    "INSERT INTO telegram_updates(update_id, reply, messages_json) VALUES (?, ?, ?)",
                    (update_id, reply, json.dumps(messages)),
                )
                return {
                    "reply": reply,
                    "delivered": False,
                    "messages": messages,
                    "delivered_count": 0,
                }
            return receipt

    def create_course_session(self, session: ProgressSession, *, create_tasks: bool = False) -> ProgressSession:
        with self.transaction() as connection:
            existing = self.get_course_session(session.session_id)
            if existing is not None:
                return existing
            if create_tasks:
                task = self.create_task(
                    f"填寫{session.course_name}進度（{session.class_date:%m/%d}）", None,
                    session.course_name, datetime.now().astimezone())
                session = replace(session, survey_task_id=task["id"])
            data = session_to_firestore(session)
            connection.execute(
                """INSERT OR IGNORE INTO course_sessions
                (session_id, course_key, course_name, class_date, prompt_message_id,
                 status, reminder_count, report_kind, reported_progress, reply_message_id, survey_task_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(data[field] for field in (
                    "session_id", "course_key", "course_name", "class_date",
                    "prompt_message_id", "status", "reminder_count", "report_kind",
                    "reported_progress", "reply_message_id", "survey_task_id",
                )),
            )
        return self.get_course_session(session.session_id) or session

    def get_course_session_by_prompt(self, prompt_id: int) -> ProgressSession | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM course_sessions WHERE prompt_message_id = ?", (prompt_id,)
            ).fetchone()
        return session_from_firestore(dict(row)) if row else None

    def record_course_reply(self, prompt_id: int, message_id: int, text: str, *, local_date=None) -> str:
        """Called inside process_update, sharing the webhook receipt transaction."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM course_sessions WHERE prompt_message_id = ?", (prompt_id,)
            ).fetchone()
            if row is None:
                return "這則回覆未對應到課後進度問題。請直接回覆原始訊息。"
            session = session_from_firestore(dict(row))
            if is_no_change_reply(text):
                return "收到"
            if local_date is not None:
                expired = mark_missed_at_day_end(session, local_date=local_date)
                if expired != session:
                    self.save_course_session(expired)
                    return "這堂課的回覆期限已結束。進度未變更。"
            answered = accept_reply(session, reply_to_message_id=prompt_id,
                                    reply_message_id=message_id, text=text)
            if answered is None:
                return "這堂課已記錄或結束。進度未變更。"
            if answered.report_kind is ProgressReportKind.UNKNOWN_SCOPE:
                self.create_task(
                    f"Check {session.course_key.replace('-', ' ').replace('operating systems', 'operating system')} {session.class_date:%m/%d} scope",
                    None, None, datetime.now().astimezone(),
                )
            if session.survey_task_id is not None:
                now = datetime.now().astimezone()
                self.complete_task(session.survey_task_id, now)
            self.save_course_session(answered)
            if answered.report_kind is ProgressReportKind.UNKNOWN_SCOPE:
                return (f"已記錄 {session.course_name}（{session.class_date:%m/%d}）：範圍尚待確認。\n"
                        "填寫進度代辦已完成，已新增確認上課範圍代辦。")
            if answered.report_kind is ProgressReportKind.NO_PROGRESS:
                return (f"已記錄 {session.course_name}（{session.class_date:%m/%d}）：今天無課或沒有進度。\n"
                        "填寫進度代辦已完成，其他代辦保持不變。")
            return (f"已記錄 {session.course_name}（{session.class_date:%m/%d}）的進度。\n"
                    "填寫進度代辦已完成，其他代辦保持不變。")

    def get_course_session(self, session_id: str) -> ProgressSession | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM course_sessions WHERE session_id = ?", (session_id,)).fetchone()
        return session_from_firestore(dict(row)) if row else None

    def list_pending_course_sessions(self) -> list[ProgressSession]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM course_sessions WHERE status NOT IN ('answered','missed')").fetchall()
        return [session_from_firestore(dict(row)) for row in rows]

    def list_answered_course_sessions(self) -> list[ProgressSession]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM course_sessions WHERE status='answered' ORDER BY class_date DESC, session_id").fetchall()
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
                prompt_message_id=?, status=?, reminder_count=?, report_kind=?, reported_progress=?, reply_message_id=?
                WHERE session_id=?""",
                tuple(data[field] for field in (
                    "course_key", "course_name", "class_date", "prompt_message_id",
                    "status", "reminder_count", "report_kind", "reported_progress", "reply_message_id",
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
