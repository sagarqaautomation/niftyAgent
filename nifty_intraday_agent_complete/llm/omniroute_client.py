"""Small, optional OpenAI-compatible client for an OmniRoute gateway.

This module is deliberately not imported by the signal engine. AI failures are
non-fatal and cannot create, modify, or authorize trading signals.
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

from config import settings

logger = logging.getLogger(__name__)


class OmniRouteClient:
    """Call a configured OmniRoute model, then an optional fallback model."""

    def __init__(self, sdk_client: Any | None = None) -> None:
        self.enabled = settings.omniroute_enabled
        self.primary_model = settings.omniroute_model.strip()
        self.fallback_model = settings.omniroute_fallback_model.strip()
        self._client = sdk_client
        if not self.enabled:
            return
        if not settings.omniroute_api_key:
            logger.warning("OmniRoute enabled but OMNIROUTE_API_KEY is missing")
            self.enabled = False
            return
        if not self._client:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    base_url=settings.omniroute_base_url.rstrip("/") + "/",
                    api_key=settings.omniroute_api_key,
                    timeout=settings.omniroute_timeout_seconds,
                    max_retries=0,
                )
            except Exception as exc:
                logger.warning("Could not initialize OmniRoute client: %s", type(exc).__name__)
                self.enabled = False

    def chat_completion(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.1,
    ) -> str | None:
        """Return text or None when disabled/unavailable; never raises to caller."""
        if not self.enabled or self._client is None:
            return None
        models = [self.primary_model]
        if self.fallback_model and self.fallback_model != self.primary_model:
            models.append(self.fallback_model)

        for model in models:
            if not model:
                continue
            try:
                response = self._client.chat.completions.create(
                    model=model,
                    messages=list(messages),
                    temperature=temperature,
                )
                text = response.choices[0].message.content
                if isinstance(text, str) and text.strip():
                    return text.strip()
                logger.warning("OmniRoute returned an empty response for model %s", model)
            except Exception as exc:
                logger.warning(
                    "OmniRoute request failed for model %s (%s)",
                    model,
                    type(exc).__name__,
                )
        return None
