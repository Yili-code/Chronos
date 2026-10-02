from datetime import date

from chronos import firestore_db
from chronos.course_tracking import COURSE_SCHEDULE, ProgressStatus, new_session
from chronos.firestore_db import FirestoreDatabase


class Snapshot:
    def __init__(self, reference, data):
        self.reference = reference
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class Document:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def get(self, transaction=None):
        return Snapshot(self, self.store.get(self.key))


class Collection:
    def __init__(self):
        self.data = {}

    def document(self, key):
        return Document(self.data, key)


class Transaction:
    def get(self, reference):
        return reference.get(transaction=self)

    def create(self, reference, data):
        if reference.key in reference.store:
            raise RuntimeError("already exists")
        reference.store[reference.key] = dict(data)

    def update(self, reference, data):
        if reference.key not in reference.store:
            raise KeyError(reference.key)
        reference.store[reference.key].update(data)


class Client:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, Collection())

    def transaction(self):
        return Transaction()


def make_db(monkeypatch):
    client = Client()
    monkeypatch.setattr(firestore_db.firestore, "Client", lambda **_: client)
    monkeypatch.setattr(firestore_db.firestore, "transactional", lambda operation: operation)
    return FirestoreDatabase("project", "(default)", "test")


def test_course_session_create_is_idempotent_and_persisted(monkeypatch):
    db = make_db(monkeypatch)
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 101)
    assert db.create_course_session(session) == session
    assert db.create_course_session(session) == session
    assert db.get_course_session(session.session_id) == session


def test_course_session_save_updates_state(monkeypatch):
    db = make_db(monkeypatch)
    session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 101)
    db.create_course_session(session)
    answered = session.__class__(**{**session.__dict__, "status": ProgressStatus.ANSWERED, "reported_progress": "第四章"})
    assert db.save_course_session(answered) == answered
    assert db.get_course_session(session.session_id) == answered
