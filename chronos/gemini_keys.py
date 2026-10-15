"""Bounded Gemini credential failover; no task mutations or credential logging."""
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import logging
import math
import random
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
        deadline = time.monotonic() + self.settings.ai_timeout
        cooldown = getattr(self.settings, "gemini_key_cooldown_seconds", 60)
        active = list(keys)
        max_attempts = max(attempts, len(active))
        transient_failures: dict[tuple[str, str], tuple[str, httpx.Response | None]] = {}
        last_response = None
        last_transport_error = None
        key_index = 0
        attempt = 0
        retry_round = 0

        async def wait_before_retry() -> None:
            nonlocal retry_round
            retry_round += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise httpx.ReadTimeout("AI request timed out")
            base = min(2 ** (retry_round - 1), 8.0)
            delay = base * (0.5 + random.random() * 0.5)
            if delay >= remaining:
                raise httpx.ReadTimeout("AI request timed out")
            logger.warning("Gemini transient failure across available keys; retrying after backoff")
            await asyncio.sleep(delay)

        while active and attempt < max_attempts:
            if key_index >= len(active):
                key_index = 0
            label, key = active[key_index]
            attempt += 1
            if before_attempt:
                await before_attempt()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise httpx.ReadTimeout("AI request timed out")
            # ai_timeout is the end-to-end deadline. Do not divide it among
            # hypothetical retries: a healthy but slow request must be able to
            # use the remaining budget. Fast failures still leave time for
            # credential failover or a bounded transient retry.
            allowance = remaining
            headers = {**request.get("headers", {}), "x-goog-api-key": key}
            try:
                try:
                    async with asyncio.timeout(allowance):
                        response = await client.post(url, **{**request, "headers": headers})
                except TimeoutError:
                    raise httpx.ReadTimeout("AI request timed out") from None
            except httpx.TransportError as error:
                if not failover_on_transport:
                    raise
                last_transport_error = error
                transient_failures[self._identity(key, url)] = (key, None)
                key_index += 1
                if attempt >= max_attempts:
                    break
                if key_index >= len(active):
                    key_index = 0
                    await wait_before_retry()
                elif len(active) > 1:
                    logger.warning("Gemini %s transport unavailable; trying next key", label)
                continue

            failover = (response.status_code == 429 or credential_rejected(response)
                        or response.status_code in TRANSIENT_CODES)
            if not failover:
                return response
            last_response = response
            if response.status_code not in TRANSIENT_CODES:
                delay = retry_delay(response, cooldown) if response.status_code == 429 else cooldown
                self._block(key, url, delay)
                active.pop(key_index)
                if active:
                    logger.warning("Gemini %s returned HTTP %s; trying next key", label, response.status_code)
                continue

            transient_failures[self._identity(key, url)] = (key, response)
            key_index += 1
            if attempt >= max_attempts:
                break
            if key_index >= len(active):
                key_index = 0
                await wait_before_retry()
            elif len(active) > 1:
                logger.warning("Gemini %s returned HTTP %s; trying next key", label, response.status_code)

        for key, response in transient_failures.values():
            delay = retry_delay(response, cooldown) if response is not None else cooldown
            self._block(key, url, delay)
        if last_response is not None:
            return last_response
        if last_transport_error is not None:
            raise last_transport_error
        raise GeminiKeysUnavailable("No available Gemini key")
