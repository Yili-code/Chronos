import pytest
from chronos.selection_store import SelectionStore, decode
from chronos.study_materials import MaterialSelection, PdfMaterial
from test_note_repository import repo


def selection():
    return MaterialSelection("session", "course", "Chapter 1", (
        PdfMaterial("a", "course", "a.pdf", None), PdfMaterial("b", "course", "b.pdf", None)))


def test_selection_roundtrip_stale_events_and_explicit_confirm(repo):
    store = SelectionStore(repo)
    initial = store.create("key", 123, selection())
    assert decode(initial["selection"]) == selection()
    with pytest.raises(ValueError):
        store.apply("key", 123, 0, confirm=True)
    chosen = store.apply("key", 123, 0, source_id="a", selected=True)
    assert store.apply("key", 123, 0, source_id="a", selected=False) == chosen
    both = store.apply("key", 123, 1, source_id="b", selected=True)
    assert set(both["selection"]["selected_ids"]) == {"a", "b"}
    confirmed = store.apply("key", 123, 2, confirm=True)
    assert confirmed["selection"]["confirmed"] is True
    assert store.apply("key", 123, 3, source_id="a", selected=False) == confirmed
    assert store.create("key", 123, selection()) == confirmed


def test_selection_rejects_wrong_chat_or_unknown_source(repo):
    store = SelectionStore(repo)
    store.create("key", 123, selection())
    with pytest.raises(ValueError):
        store.apply("key", 999, 0, source_id="a", selected=True)
    with pytest.raises(ValueError):
        store.apply("key", 123, 0, source_id="other", selected=True)
