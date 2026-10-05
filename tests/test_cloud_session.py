import json
import pytest
from chronos.cloud_session import validate_cloud_session,CloudSessionError,load_cloud_session


def state():
    return {'origins':[],'cookies':[{'name':'synthetic','value':'PRIVATE_TEST_VALUE',
        'domain':'tronclass.ntou.edu.tw','path':'/','expires':-1,
        'httpOnly':True,'secure':True,'sameSite':'Lax'}]}


def test_minimal_cookie_state_roundtrip():
    assert validate_cloud_session(json.dumps(state()).encode())==state()


def test_load_mounted_secret(tmp_path):
    secret = tmp_path / 'session'
    secret.write_bytes(json.dumps(state()).encode())
    assert load_cloud_session(secret) == state()


@pytest.mark.parametrize('kind', ['missing', 'directory', 'empty', 'oversized', 'malformed'])
def test_mount_failures_are_safe_and_do_not_fallback(tmp_path, kind):
    secret = tmp_path / 'PRIVATE_TEST_PATH'
    expected = 'session_unavailable'
    if kind == 'directory':
        secret.mkdir()
    elif kind in ('empty', 'oversized', 'malformed'):
        secret.write_bytes({'empty': b'', 'oversized': b'x' * 65537,
                            'malformed': b'PRIVATE_TEST_VALUE'}[kind])
        expected = 'invalid_session_format' if kind == 'malformed' else 'invalid_session_size'
    with pytest.raises(CloudSessionError) as error:
        load_cloud_session(secret)
    assert str(error.value) == expected
    assert error.value.__suppress_context__ or kind in ('empty', 'oversized')


@pytest.mark.parametrize('field,value', [('domain','.ntou.edu.tw'),('domain','tccas.ntou.edu.tw'),
    ('secure',False),('expires',float('nan')),('expires',True),('httpOnly','true'),('path','relative')])
def test_scope_and_shape_rejection_never_echoes_values(field,value):
    payload=state();payload['cookies'][0][field]=value
    with pytest.raises(CloudSessionError) as error:
        validate_cloud_session(json.dumps(payload).encode())
    assert 'PRIVATE_TEST_VALUE' not in str(error.value)


def test_no_localstorage_or_duplicate_cookie():
    payload=state();payload['origins']=[{'localStorage':[]}]
    with pytest.raises(CloudSessionError):validate_cloud_session(json.dumps(payload).encode())
    payload=state();payload['cookies']*=2
    with pytest.raises(CloudSessionError):validate_cloud_session(json.dumps(payload).encode())
