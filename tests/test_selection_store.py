import pytest
from dataclasses import replace
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


def test_freeze_keeps_first_content_identity(repo):
    store = SelectionStore(repo)
    chosen = selection().choose("a", selected=True).confirm()
    store.create("key", 123, chosen)
    verified = replace(chosen, catalog=(replace(chosen.catalog[0], sha256="a" * 64), chosen.catalog[1]))
    first = store.freeze_content("key", 123, verified)
    assert store.freeze_content("key", 123, verified) == first
    assert decode(repo.get_material_selection("key")["selection"]) == verified
    changed = replace(verified, catalog=(replace(verified.catalog[0], sha256="b" * 64), verified.catalog[1]))
    with pytest.raises(ValueError):
        store.freeze_content("key", 123, changed)
    with pytest.raises(ValueError):
        store.freeze_content("key", 123, replace(verified, reported_progress="different"))
    with pytest.raises(ValueError):
        store.freeze_content("key", 999, verified)


def test_execution_binding_is_immutable_across_restarts(repo):
    store = SelectionStore(repo)
    store.create('key', 123, selection().choose('a', selected=True).confirm())
    args = dict(model='m', prompt_version='v', pagination_version='p')
    first = store.bind_execution('key', 123, **args)
    assert SelectionStore(repo).bind_execution('key', 123, **args) == first
    for field in args:
        with pytest.raises(ValueError):
            store.bind_execution('key', 123, **{**args, field:'changed'})
    with pytest.raises(ValueError):
        store.bind_execution('key', 999, **args)
    assert repo.get_material_selection('key')['execution'] == args


def test_legacy_attempt_cannot_reset_model_budget(repo):
    store = SelectionStore(repo)
    store.create('key', 123, selection().choose('a', selected=True).confirm())
    repo.mutate_material_selection('key', lambda previous: {**previous, 'processing_status':'retry'})
    with pytest.raises(ValueError):
        store.bind_execution('key', 123, model='new', prompt_version='v', pagination_version='p')
