from datetime import datetime
from zoneinfo import ZoneInfo

from chronos import main
from chronos import firestore_db
from chronos.firestore_db import FirestoreDatabase
from chronos.tasks import TaskService


TZ = ZoneInfo("Asia/Taipei")


class FakeSnapshot:
    def __init__(self, reference, data):
        self.reference = reference
        self.id = reference.id
        self._data = data
        self.exists = data is not None

    def get(self, key):
        return self._data[key]

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeDocument:
    def __init__(self, client, collection, document_id):
        self.client = client
        self.collection = collection
        self.id = document_id

    @property
    def key(self):
        return self.collection, self.id

    def get(self, transaction=None):
        if transaction is not None and transaction.has_writes:
            raise RuntimeError("Firestore does not allow reads after writes in a transaction")
        return FakeSnapshot(self, self.client.data.get(self.key))

    def update(self, values):
        self.client.data[self.key].update(values)


class FakeQuery:
    def __init__(self, collection, field_filter):
        self.collection = collection
        self.field_filter = field_filter

    def stream(self, transaction=None):
        if transaction is not None and transaction.has_writes:
            raise RuntimeError("Firestore does not allow reads after writes in a transaction")
        self.collection.client.last_query_transaction = transaction
        return [
            FakeSnapshot(FakeDocument(self.collection.client, self.collection.name, document_id), data)
            for (name, document_id), data in self.collection.client.data.items()
            if name == self.collection.name and (
                data.get(self.field_filter.field_path) in self.field_filter.value
                if self.field_filter.op_string == "in"
                else data.get(self.field_filter.field_path) == self.field_filter.value
            )
        ]


class FakeCollection:
    def __init__(self, client, name):
        self.client = client
        self.name = name

    def document(self, document_id):
        return FakeDocument(self.client, self.name, document_id)

    def where(self, filter):
        return FakeQuery(self, filter)

    def stream(self, transaction=None):
        if transaction is not None and transaction.has_writes:
            raise RuntimeError("Firestore does not allow reads after writes in a transaction")
        self.client.last_query_transaction = transaction
        return [
            FakeSnapshot(FakeDocument(self.client, self.name, document_id), data)
            for (name, document_id), data in list(self.client.data.items())
            if name == self.name
        ]


class FakeTransaction:
    def __init__(self, client):
        self.client = client
        self.has_writes = False

    def set(self, reference, values):
        self.has_writes = True
        self.client.data[reference.key] = dict(values)

    def create(self, reference, values):
        self.has_writes = True
        if reference.key in self.client.data:
            raise RuntimeError("already exists")
        self.client.data[reference.key] = dict(values)

    def update(self, reference, values):
        self.has_writes = True
        self.client.data[reference.key].update(values)

    def delete(self, reference):
        self.has_writes = True
        del self.client.data[reference.key]


class FakeClient:
    def __init__(self, **kwargs):
        self.data = {}
        self.last_query_transaction = None

    def collection(self, name):
        return FakeCollection(self, name)

    def transaction(self):
        return FakeTransaction(self)


def database(monkeypatch):
    monkeypatch.setattr(firestore_db.firestore, "Client", FakeClient)
    monkeypatch.setattr(firestore_db.firestore, "transactional", lambda operation: operation)
    return FirestoreDatabase("test-project", "(default)", "test")


def test_real_command_actions_do_not_read_after_firestore_writes(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from chronos import main
    from chronos.tasks import TaskService, ParsedTask
    db = database(monkeypatch)
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "tasks", TaskService(db, TZ))
    fake_ai = AsyncMock()
    fake_ai.parse.return_value = ParsedTask("Task", datetime(2026, 10, 20, 12, tzinfo=TZ))
    fake_ai.edit.return_value = ParsedTask("Changed", datetime(2026, 10, 21, 12, tzinfo=TZ))
    monkeypatch.setattr(main, "ai", fake_ai)
    db.create_task("Task", None, None, datetime(2026, 10, 5, 12, tzinfo=TZ))
    for update, command in enumerate(("/reschedule 1 tomorrow", "/edit 1 rename", "/done 1"), start=1):
        action = asyncio.run(main.prepare_message(command))
        result = db.process_update(update, action)
        assert result["reply"]
    assert db.list_open_tasks() == []


def test_firestore_scheduler_reply_and_stop_reminders(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from chronos.study_scheduler import tick_study
    db = database(monkeypatch)
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 701}}
    for hour in (12, 13):
        asyncio.run(tick_study(db, bot, 123, datetime(2026, 10, 5, hour, 10, tzinfo=TZ)))
    assert db.get_course_session("security:2026-10-05").reminder_count == 1
    assert bot.send_message.await_count == 2
    db.process_update(9000, lambda: db.record_course_reply(701, 702, "Chapter 5"))
    asyncio.run(tick_study(db, bot, 123, datetime(2026, 10, 5, 14, 10, tzinfo=TZ)))
    assert db.get_course_session("security:2026-10-05").status.value == "answered"
    assert bot.send_message.await_count == 2


def test_firestore_scheduler_expires_unanswered_sessions(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from chronos.study_scheduler import tick_study
    db = database(monkeypatch)
    bot = AsyncMock()
    bot.send_message.return_value = {"ok": True, "result": {"message_id": 701}}
    asyncio.run(tick_study(db, bot, 123, datetime(2026, 10, 5, 12, 10, tzinfo=TZ)))
    asyncio.run(tick_study(db, bot, 123, datetime(2026, 10, 6, 0, 0, tzinfo=TZ)))
    assert db.get_course_session("security:2026-10-05").status.value == "missed"
    assert bot.send_message.await_count == 1


def test_firestore_late_reply_expires_before_cleanup(monkeypatch):
    from datetime import date
    from chronos.course_tracking import COURSE_SCHEDULE, new_session
    db = database(monkeypatch)
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 101)
    db.create_course_session(session)
    receipt = db.process_update(202, lambda: db.record_course_reply(
        101, 102, "Chapter 4", local_date=date(2026, 10, 6)))
    assert "期限已結束" in receipt["reply"]
    assert db.get_course_session(session.session_id).status.value == "missed"
    assert db.get_update(202) == receipt


def test_course_reply_shares_receipt_transaction(monkeypatch):
    from datetime import date
    from chronos.course_tracking import COURSE_SCHEDULE, new_session
    db = database(monkeypatch)
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 601)
    db.create_course_session(session)
    action = lambda: db.record_course_reply(601, 602, "Chapter 4")
    receipt = db.process_update(1234, action)
    transaction = db.client.last_query_transaction
    assert transaction is not None
    assert transaction.has_writes
    assert db.process_update(1234, lambda: "should never run") == receipt
    assert db.get_course_session(session.session_id).reported_progress == "Chapter 4"
    assert db.get_update(1234) == receipt


def test_delivery_claim_survives_new_ledger_instance(monkeypatch):
    from datetime import timezone, timedelta
    from chronos.study_delivery import StudyDeliveryLedger
    db = database(monkeypatch)
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    claim = StudyDeliveryLedger(db).claim("security:2026-10-05:prompt", now)
    assert claim is not None
    recreated = StudyDeliveryLedger(db)
    assert recreated.claim("security:2026-10-05:prompt", now) is None
    assert recreated.claim("security:2026-10-05:prompt", now + timedelta(minutes=3)) is None
    result = recreated.finish("security:2026-10-05:prompt", claim, now, message_id=123)
    assert result["status"] == "sent"


def test_firestore_task_lifecycle_and_order(monkeypatch):
    db = database(monkeypatch)
    service = TaskService(db, TZ)
    no_due = service.create("無期限")
    later = service.create("較晚", datetime(2026, 9, 21, 10, tzinfo=TZ))
    earlier = service.create("較早", datetime(2026, 9, 20, 10, tzinfo=TZ), "Chronos")
    assert [task["id"] for task in service.list_open()] == [earlier["id"], later["id"], no_due["id"]]
    assert service.postpone(no_due["id"], datetime(2026, 9, 19, 10, tzinfo=TZ))
    assert service.list_open()[0]["id"] == no_due["id"]
    assert service.complete(no_due["id"])
    assert not service.complete(no_due["id"])


def test_firestore_edit_updates_open_task(monkeypatch):
    db = database(monkeypatch)
    service = TaskService(db, TZ)
    task = service.create("Old", project="Chronos")
    due = datetime(2026, 10, 3, 18, tzinfo=TZ)
    assert service.edit(task["id"], "New", due, None)["title"] == "New"
    assert service.list_open()[0]["due_at"] == due.isoformat()
    assert service.complete(task["id"])
    assert service.edit(task["id"], "No", None, None) is None


def test_firestore_clear_deletes_open_and_completed_tasks(monkeypatch):
    db = database(monkeypatch)
    service = TaskService(db, TZ)
    completed = service.create("Completed")
    service.create("Open")
    assert service.complete(completed["id"])
    assert service.clear() == 2
    assert db.client.last_query_transaction is not None
    assert service.list_open() == []


def test_firestore_clear_callback_does_not_read_after_writing(monkeypatch):
    db = database(monkeypatch)
    service = TaskService(db, TZ)
    service.create("Open")
    monkeypatch.setattr(main, "tasks", service)

    receipt = db.process_update(102, main.clear_tasks_reply)

    assert receipt == {"reply": "Deleted 1 task.\n\nNo open tasks.", "delivered": False}
    assert service.list_open() == []


def test_firestore_update_receipt_deduplicates_mutation(monkeypatch):
    db = database(monkeypatch)
    service = TaskService(db, TZ)
    calls = 0

    def action():
        nonlocal calls
        calls += 1
        service.create("只建立一次")
        return "已新增"

    assert db.process_update(100, action) == {"reply": "已新增", "delivered": False}
    assert db.process_update(100, action) == {"reply": "已新增", "delivered": False}
    assert calls == 1
    assert len(service.list_open()) == 1
    db.mark_update_delivered(100)
    assert db.get_update(100)["delivered"] is True


def test_firestore_position_is_resolved_inside_receipt_transaction(monkeypatch):
    db = database(monkeypatch)
    service = TaskService(db, TZ)
    later = service.create("Later", datetime(2026, 9, 21, 10, tzinfo=TZ))
    earlier = service.create("Earlier", datetime(2026, 9, 20, 10, tzinfo=TZ))

    def action():
        task = service.complete_position(1)
        return f"Completed: {task['title']}"

    assert db.process_update(101, action)["reply"] == "Completed: Earlier"
    assert db.client.last_query_transaction is not None
    remaining = service.list_open()
    assert [task["id"] for task in remaining] == [later["id"]]
    assert all(task["id"] != earlier["id"] for task in remaining)


def test_firestore_collection_prefix_is_validated(monkeypatch):
    monkeypatch.setattr(firestore_db.firestore, "Client", FakeClient)
    try:
        FirestoreDatabase("test-project", "(default)", "invalid/prefix")
    except ValueError as error:
        assert "COLLECTION_PREFIX" in str(error)
    else:
        raise AssertionError("invalid prefix was accepted")
