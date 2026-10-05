import json
from unittest.mock import MagicMock
import httpx
import pytest
from chronos.cloud_session import CloudSessionError
from chronos.cloud_session_publish import publish_session, SessionPublishError, PARENT, ENDPOINT


def payload():
    return json.dumps({'origins': [], 'cookies': [{
        'name': 'synthetic', 'value': 'PRIVATE_TEST_VALUE',
        'domain': 'tronclass.ntou.edu.tw', 'path': '/', 'expires': -1,
        'httpOnly': True, 'secure': True, 'sameSite': 'Lax'}]}).encode()


@pytest.mark.parametrize('project', ['yili-chronos-prod', '871491483193'])
def test_upload_returns_only_version_without_redirect_or_retry(project):
    client = MagicMock()
    client.post.return_value = httpx.Response(200, json={'name':
        f'projects/{project}/secrets/chronos-tronclass-session/versions/12'})
    assert publish_session(payload(), 'synthetic-token', client=client) == '12'
    client.post.assert_called_once()
    assert client.post.call_args.args == (ENDPOINT,)
    assert client.post.call_args.kwargs['follow_redirects'] is False


def test_invalid_scope_never_uploads():
    client = MagicMock()
    with pytest.raises(CloudSessionError):
        publish_session(payload().replace(b'tronclass.ntou.edu.tw', b'.ntou.edu.tw'), 'token', client=client)
    client.post.assert_not_called()


@pytest.mark.parametrize('status', [302, 400, 401, 403, 404, 429, 500, 503])
def test_rejection_never_exposes_response_or_retries(status):
    client = MagicMock()
    client.post.return_value = httpx.Response(status, text='PRIVATE_TEST_VALUE')
    with pytest.raises(SessionPublishError) as error:
        publish_session(payload(), 'token', client=client)
    assert str(error.value) in ('publish_rejected', 'publish_uncertain')
    client.post.assert_called_once()


def test_timeout_is_uncertain_not_failed_and_not_retried():
    client = MagicMock()
    client.post.side_effect = httpx.ReadTimeout('PRIVATE_TEST_VALUE')
    with pytest.raises(SessionPublishError, match='^publish_uncertain$'):
        publish_session(payload(), 'token', client=client)
    client.post.assert_called_once()


@pytest.mark.parametrize('body', [{}, {'name': PARENT + '/versions/latest'}, {'name': 7}])
def test_invalid_receipt_does_not_claim_success(body):
    client = MagicMock()
    client.post.return_value = httpx.Response(200, json=body)
    with pytest.raises(SessionPublishError, match='^publish_uncertain$'):
        publish_session(payload(), 'token', client=client)
