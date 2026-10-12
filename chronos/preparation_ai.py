"""Bounded Gemini provider for explicit assignment-preparation requests."""

from datetime import datetime
import json

import httpx
from pydantic import BaseModel

from .ai_budget import BudgetExceeded
from .course_tracking import TAIPEI
from .gemini_keys import GeminiKeyRouter, GeminiKeysUnavailable, configured_keys


class PreparationProviderError(RuntimeError):
    """A sanitized provider failure with no request or response content."""


class GeminiPreparationProvider:
    def __init__(self, settings, *, free_tier_confirmed=False, transport=None,
                 budget=None, budget_notice=None):
        self.settings = settings
        self.free_tier_confirmed = free_tier_confirmed
        self.transport = transport
        self.budget = budget
        self.budget_notice = budget_notice
        self.key_router = GeminiKeyRouter(settings)

    async def request(self, body: dict, model: str, *, output_type: type[BaseModel]) -> dict:
        config = self.settings
        if not self.free_tier_confirmed:
            raise ValueError("free-tier confirmation required")
        if not configured_keys(config) or model != config.gemini_model:
            raise ValueError("preparation model configuration mismatch")
        if config.gemini_api_base != "https://generativelanguage.googleapis.com/v1beta":
            raise ValueError("unsupported preparation endpoint")
        body = {
            **body,
            "generationConfig": {
                **body.get("generationConfig", {}),
                "maxOutputTokens": 8192,
            },
        }

        async def reserve_attempt():
            if self.budget is None:
                if not isinstance(self.transport, httpx.MockTransport):
                    raise BudgetExceeded("daily preparation budget must be configured")
                return
            now = datetime.now(TAIPEI)
            estimate = len(json.dumps(body, ensure_ascii=False).encode("utf-8")) + 8192
            try:
                reservation = self.budget.reserve(now, estimate)
            except BudgetExceeded:
                if self.budget_notice:
                    await self.budget_notice("exhausted", now)
                raise
            if reservation["near_limit"] and self.budget_notice:
                await self.budget_notice("near_limit", now)

        try:
            async with httpx.AsyncClient(
                timeout=config.ai_timeout,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = await self.key_router.post(
                    client,
                    f"{config.gemini_api_base}/models/{model}:generateContent",
                    json=body,
                    before_attempt=reserve_attempt,
                    failover_on_transport=False,
                )
        except GeminiKeysUnavailable:
            raise PreparationProviderError("preparation provider keys unavailable") from None
        except httpx.HTTPError:
            raise PreparationProviderError("preparation provider transport unavailable") from None
        if not response.is_success:
            raise PreparationProviderError("preparation provider rejected request")
        try:
            candidate = response.json()["candidates"][0]
            if candidate.get("finishReason") != "STOP":
                raise ValueError("incomplete output")
            text = "".join(
                part.get("text", "")
                for part in candidate["content"]["parts"]
                if not part.get("thought")
            )
            return output_type.model_validate_json(text).model_dump()
        except (ValueError, KeyError, IndexError, TypeError):
            raise PreparationProviderError("preparation provider returned invalid output") from None
