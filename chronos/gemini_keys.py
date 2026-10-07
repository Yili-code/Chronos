"""Bounded Gemini credential failover; no task mutations or credential logging."""
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import logging
import math
import time

import httpx


logger = logging.getLogger("chronos.gemini_keys")
TRANSIENT_CODES = {500, 502, 503, 504}


class GeminiKeysUnavailable(Exception):
    """All configured keys are temporarily cooling down."""


def configured_keys(settings) -> list[tuple[str, str]]:
    result = []
    for label, attribute in (("primary", "gemini_api_key"), ("secondary", "gemini_api_key_secondary")):
        value = (getattr(settings, attribute, "") or "").strip()
        if value and all(value != key for _, key in result):
            result.append((label, value))
    return result


def credential_rejected(response: httpx.Response) -> bool:
    if response.status_code in {401, 403}:
        return True
    if response.status_code == 400:
        try:
            return any(item.get("reason") == "API_KEY_INVALID"
                       for item in response.json()["error"].get("details", []) if isinstance(item, dict))
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
    return False


def retry_delay(response: httpx.Response, default: float) -> float:
    """Honor provider delay without trusting arbitrary response text or unbounded values."""
    delays = [default]
    header = response.headers.get("Retry-After")
    if header:
        try:
            delays.append(float(header))
        except ValueError:
            try:
                delays.append((parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    try:
        for detail in response.json()["error"].get("details", []):
            if isinstance(detail, dict) and detail.get("@type", "").endswith("google.rpc.RetryInfo"):
                duration = detail.get("retryDelay", "")
                if isinstance(duration, str) and duration.endswith("s"):
                    delays.append(float(duration[:-1]))
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    return min(172800.0, max(delay for delay in delays if math.isfinite(delay) and delay > 0))


class GeminiKeyRouter:
    def __init__(self, settings):
        self.settings = settings
        # Fingerprints, not raw credentials. State is local to this provider instance.
        self._blocked_until: dict[tuple[str, str], float] = {}

    @staticmethod
    def _identity(key, url):
        return hashlib.sha256(key.encode()).hexdigest(), url

    def _block(self, key, url, delay):
        self._blocked_until[self._identity(key, url)] = time.monotonic() + delay

    async def post(self, client, url, *, attempts=1, before_attempt=None,
                   failover_on_transport=True, **request):
        configured = configured_keys(self.settings)
        keys = [(label, key) for label, key in configured
                if self._blocked_until.get(self._identity(key, url), 0) <= time.monotonic()]
        if not keys:
            raise GeminiKeysUnavailable("No available Gemini key")
        # With two configured keys, at most one attempt per key. A single-key
        # deployment retains bounded transient retries, but never retries a 429 inline.
        per_key_attempts = 1 if len(configured) > 1 else attempts
        deadline = time.monotonic() + self.settings.ai_timeout
        cooldown = getattr(self.settings, "gemini_key_cooldown_seconds", 60)
        for index, (label, key) in enumerate(keys):
            for attempt in range(per_key_attempts):
                if before_attempt:
                    await before_attempt()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise httpx.ReadTimeout("AI request timed out")
                # Reserve time for the secondary even if the primary hangs.
                allowance = remaining / (len(keys) - index)
                headers = {**request.get("headers", {}), "x-goog-api-key": key}
                try:
                    try:
                        async with asyncio.timeout(allowance):
                            response = await client.post(url, **{**request, "headers": headers})
                    except TimeoutError:
                        raise httpx.ReadTimeout("AI request timed out") from None
                except httpx.TransportError:
                    if not failover_on_transport:
                        raise
                    if attempt + 1 < per_key_attempts:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    self._block(key, url, cooldown)
                    if index + 1 == len(keys):
                        raise
                    logger.warning("Gemini %s transport unavailable; trying secondary", label)
                    break
                failover = (response.status_code == 429 or credential_rejected(response)
                            or response.status_code in TRANSIENT_CODES)
                if not failover:
                    return response
                if response.status_code in TRANSIENT_CODES and attempt + 1 < per_key_attempts:
                    await asyncio.sleep(2 ** attempt)
                    continue
                delay = retry_delay(response, cooldown) if response.status_code == 429 else cooldown
                self._block(key, url, delay)
                if index + 1 == len(keys):
                    return response
                logger.warning("Gemini %s returned HTTP %s; trying secondary", label, response.status_code)
                break
        raise GeminiKeysUnavailable("No available Gemini key")
