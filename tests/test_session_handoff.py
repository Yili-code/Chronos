import json
import threading
from http.client import HTTPConnection
from unittest.mock import Mock
import pytest
from chronos.local_observation_server import LocalObservationServer
from test_cloud_session_publish import payload


@pytest.mark.parametrize('fails', [False, True])
def test_secret_handoff_is_origin_bound_one_shot_and_redacted(tmp_path, fails):
    publisher = Mock(return_value='1', side_effect=RuntimeError('PRIVATE_TEST_VALUE') if fails else None)
    server = LocalObservationServer(0, catalog_path=tmp_path/'catalog.sqlite3',
        extension_id='a'*32, session_publisher=publisher)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    def post(origin, body):
        headers = {'X-Chronos-Bridge': '1'}
        if origin: headers['Origin'] = origin
        conn = HTTPConnection('127.0.0.1', server.server_port)
        conn.request('POST', '/v1/cloud-session', body=body, headers=headers)
        response = conn.getresponse()
        result = response.status, response.read().decode()
        conn.close()
        return result
    try:
        origin = 'chrome-extension://' + 'a'*32
        assert post(None, payload())[0] == 403
        assert post('https://example.test', payload())[0] == 403
        assert post(origin, b'{}')[0] == 400
        publisher.assert_not_called()
        status, receipt = post(origin, payload())
        assert status == (503 if fails else 202)
        assert 'PRIVATE_TEST_VALUE' not in receipt
        assert post(origin, payload())[0] == 409
        publisher.assert_called_once()
        assert not list(tmp_path.glob('*.sqlite3'))
    finally:
        server.shutdown()
        server.server_close()
