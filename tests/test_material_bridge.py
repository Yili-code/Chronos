import pytest
from chronos.material_bridge import validate_material_observation


def payload():
    return {'status':'observed', 'materials':[{'source_id':'1', 'course_id':'2',
        'activity_id':'3', 'filename':'lecture.pdf', 'uploaded_at':None}]}


def test_metadata_roundtrip_without_unknown_fields():
    assert validate_material_observation(payload()) == payload()


@pytest.mark.parametrize('field', ['url', 'cookie', 'token', 'headers'])
def test_secret_or_url_fields_rejected(field):
    data = payload()
    data['materials'][0][field] = 'must-not-persist'
    with pytest.raises(ValueError):
        validate_material_observation(data)


def test_unobserved_timestamp_is_rejected():
    data = payload()
    data['materials'][0]['uploaded_at'] = '2026-10-02'
    with pytest.raises(ValueError):
        validate_material_observation(data)
