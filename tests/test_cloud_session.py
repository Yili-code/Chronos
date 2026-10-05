import json
import pytest
from chronos.cloud_session import validate_cloud_session,CloudSessionError


def state():
    return {'origins':[],'cookies':[{'name':'synthetic','value':'PRIVATE_TEST_VALUE',
        'domain':'tronclass.ntou.edu.tw','path':'/','expires':-1,
        'httpOnly':True,'secure':True,'sameSite':'Lax'}]}


def test_minimal_cookie_state_roundtrip():
    assert validate_cloud_session(json.dumps(state()).encode())==state()


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
