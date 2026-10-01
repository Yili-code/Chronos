from datetime import datetime
from zoneinfo import ZoneInfo

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
        return FakeSnapshot(self, self.client.data.get(self.key))

    def update(self, values):
        self.client.data[self.key].update(values)


class FakeQuery:
    def __init__(self, collection):
        self.collection = collection

    def stream(self, transaction=None):
        self.collection.client.last_query_transaction = transaction
        return [
            FakeSnapshot(FakeDocument(self.collection.client, self.collection.name, document_id), data)
            for (name, document_id), data in self.collection.client.data.items()
            if name == self.collection.name and data.get("status") == "open"
        ]


class FakeCollection:
    def __init__(self, client, name):
        self.client = client
        self.name = name

    def document(self, document_id):
        return FakeDocument(self.client, self.name, document_id)

    def where(self, filter):
        return FakeQuery(self)


class FakeTransaction:
    def __init__(self, client):
        self.client = client

    def set(self, reference, values):
        self.client.data[reference.key] = dict(values)

    def create(self, reference, values):
        if reference.key in self.client.data:
            raise RuntimeError("already exists")
        self.client.data[reference.key] = dict(values)

    def update(self, reference, values):
        self.client.data[reference.key].update(values)


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
