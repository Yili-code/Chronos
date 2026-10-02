from datetime import datetime, timezone
import pytest
from chronos.study_materials import PdfMaterial, MaterialSelection, course_catalog


def material(key='a', course='os', day=1, checksum='a'):
    return PdfMaterial(key, course, key+'.pdf', datetime(2026, 10, day, tzinfo=timezone.utc), checksum*64)


def test_catalog_filters_course_and_orders_newest_first():
    assert [x.source_id for x in course_catalog('os', (material(), material('b', day=2), material('c', course='db')))] == ['b', 'a']


def test_selection_is_explicit_and_duplicate_callback_is_idempotent():
    selection = MaterialSelection('os:2026-10-01', 'os', 'Chapter 1', (material(),))
    with pytest.raises(ValueError, match='at least one'):
        selection.confirm()
    chosen = selection.choose('a', selected=True)
    assert chosen.choose('a', selected=True) == chosen
    with pytest.raises(ValueError, match='confirmation'):
        chosen.generation_key(model='test', prompt_version='v1')
    confirmed = chosen.confirm()
    assert confirmed.confirm() == confirmed
    with pytest.raises(ValueError, match='immutable'):
        confirmed.choose('a', selected=False)


def test_cross_course_material_cannot_be_selected():
    selection = MaterialSelection('session', 'os', 'Chapter 1', (material(),))
    with pytest.raises(ValueError, match='catalog'):
        selection.choose('other', selected=True)
    with pytest.raises(ValueError, match='course'):
        MaterialSelection('session', 'os', 'Chapter 1', (material(course='db'),))


def test_generation_identity_ignores_click_order_but_tracks_content():
    catalog = (material(), material('b'))
    base = MaterialSelection('session', 'os', 'Chapter 1', catalog)
    first = base.choose('a', selected=True).choose('b', selected=True).confirm()
    second = base.choose('b', selected=True).choose('a', selected=True).confirm()
    key = lambda state: state.generation_key(model='test', prompt_version='v1')
    assert key(first) == key(second)
    changed = MaterialSelection('session', 'os', 'Chapter 1', (material(checksum='b'), material('b')), frozenset({'a','b'}), True)
    assert key(first) != key(changed)
