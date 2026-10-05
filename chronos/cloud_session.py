"""Validate minimally scoped cloud browser cookies without logging their values.

This module does not export Chrome cookies or call Secret Manager. A separately
authorized provisioning path must supply the payload in memory.
"""
import json
import math
from pathlib import Path


class CloudSessionError(ValueError):
    pass


def load_cloud_session(path: Path):
    """Read a mounted secret with a bounded allocation and no credential fallback.

    Secret Manager mounts may be symlinks, so do not reject symlinks. The caller
    supplies the deployment-controlled mount path, never a request parameter.
    Errors deliberately omit paths, contents and underlying OS diagnostics.
    """
    try:
        with path.open('rb') as stream:
            raw = stream.read(65537)
    except OSError:
        raise CloudSessionError('session_unavailable') from None
    return validate_cloud_session(raw)


def validate_cloud_session(raw: bytes):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 65536:
        raise CloudSessionError('invalid_session_size')
    try:
        state = json.loads(raw)
    except (ValueError, UnicodeError):
        raise CloudSessionError('invalid_session_format') from None
    if not isinstance(state, dict) or set(state) != {'cookies','origins'} or state['origins'] != []:
        raise CloudSessionError('unsupported_session_storage')
    cookies=state['cookies']
    if not isinstance(cookies,list) or not 1 <= len(cookies) <= 50:
        raise CloudSessionError('invalid_cookie_count')
    fields={'name','value','domain','path','expires','httpOnly','secure','sameSite'}
    seen=set()
    for cookie in cookies:
        if not isinstance(cookie,dict) or set(cookie) != fields:
            raise CloudSessionError('unsupported_cookie_fields')
        # No parent ntou.edu.tw or CAS SSO cookies: unrelated school services
        # must not become accessible merely to collect TronClass content.
        if cookie['domain'] != 'tronclass.ntou.edu.tw' or cookie['secure'] is not True:
            raise CloudSessionError('unsupported_cookie_scope')
        for field in ('name','value','path'):
            value=cookie[field]
            if not isinstance(value,str) or len(value)>8192 or any(ord(c)<32 for c in value):
                raise CloudSessionError('invalid_cookie_text')
        if not cookie['name'] or not cookie['path'].startswith('/'):
            raise CloudSessionError('invalid_cookie_text')
        expiry=cookie['expires']
        if type(expiry) not in (int,float) or not math.isfinite(expiry) or expiry < -1:
            raise CloudSessionError('invalid_cookie_expiry')
        if type(cookie['httpOnly']) is not bool or cookie['sameSite'] not in ('Strict','Lax','None'):
            raise CloudSessionError('invalid_cookie_flags')
        key=(cookie['name'],cookie['domain'],cookie['path'])
        if key in seen:
            raise CloudSessionError('duplicate_cookie')
        seen.add(key)
    return state
