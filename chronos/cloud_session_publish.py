"""In-memory session provisioning via the operator's existing gcloud identity.

This is not imported by the webhook service. It never creates IAM grants or
secrets, and returns only a version number. No automatic retry: an uncertain
response may already have created a version.
"""
import base64
import json
import re
import subprocess

import httpx

from .cloud_session import validate_cloud_session

PARENT = 'projects/yili-chronos-prod/secrets/chronos-tronclass-session'
ENDPOINT = f'https://secretmanager.googleapis.com/v1/{PARENT}:addVersion'


class SessionPublishError(ValueError):
    pass


def operator_token(gcloud):
    try:
        result = subprocess.run([str(gcloud), 'auth', 'print-access-token'],
                                capture_output=True, text=True, timeout=30, check=False)
        token = result.stdout.strip()
        if result.returncode or not token or any(c.isspace() for c in token):
            raise SessionPublishError('operator_auth_unavailable')
        return token
    except (OSError, subprocess.SubprocessError):
        raise SessionPublishError('operator_auth_unavailable') from None


def publish_session(raw, token, *, client):
    # Validate before any external write. Serialize only the narrow schema.
    state = validate_cloud_session(raw)
    encoded = base64.b64encode(json.dumps(state, ensure_ascii=True).encode()).decode()
    try:
        response = client.post(ENDPOINT,
            headers={'Authorization': 'Bearer ' + token},
            json={'payload': {'data': encoded}}, timeout=30, follow_redirects=False)
    except httpx.HTTPError:
        raise SessionPublishError('publish_uncertain') from None
    if response.status_code != 200:
        # No response bodies or request exceptions may escape into diagnostics.
        label = 'publish_rejected' if response.status_code in (400, 401, 403, 404) else 'publish_uncertain'
        raise SessionPublishError(label)
    try:
        name = response.json()['name']
        # Google may canonicalize the project ID to its immutable number.
        match = re.fullmatch(r'projects/(?:yili-chronos-prod|871491483193)/secrets/'
                             r'chronos-tronclass-session/versions/([1-9][0-9]*)', name)
        if not match:
            raise ValueError()
        return match.group(1)
    except (ValueError, KeyError, TypeError):
        raise SessionPublishError('publish_uncertain') from None
