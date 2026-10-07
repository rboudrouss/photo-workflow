"""Backend VLM via l'API Anthropic (cle API Console, facturation a l'usage).

Deux usages :
- synchrone (`AnthropicBackend.analyze`) pour le worker et les tests de qualite ;
- en lot (`build_batch_request`) pour l'API Batches, moitie prix, voir cli `claude-batch`.

Le JSON est contraint par `output_config.format` (sorties structurees), puis valide par pydantic.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from ...config import settings
from ...images import to_base64
from ..vlm import SYSTEM_PROMPT, USER_PROMPT, PhotoAnalysis, VLMBackend, json_schema, parse_output

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _client() -> anthropic.Anthropic:
    # Sans cle explicite, le SDK lit ANTHROPIC_API_KEY ou un profil `ant auth login` (compte Console).
    return anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=3, timeout=settings.vlm_timeout)


def message_params(jpeg: bytes, model: str | None = None) -> dict[str, Any]:
    """Parametres communs a messages.create et aux requetes Batches."""
    return {
        "model": model or settings.anthropic_model,
        "max_tokens": 4096,
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": to_base64(jpeg)},
                    },
                    {"type": "text", "text": USER_PROMPT},
                ],
            }
        ],
        "output_config": {
            "effort": settings.anthropic_effort,
            "format": {"type": "json_schema", "schema": json_schema(strip_lengths=True)},
        },
    }


def parse_response(message: Any) -> PhotoAnalysis:
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        raise RuntimeError(f"refus du modele: {details}")
    text = next((b.text for b in message.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"pas de bloc texte dans la reponse (stop_reason={message.stop_reason})")
    return parse_output(json.loads(text))


class AnthropicBackend(VLMBackend):
    backend_name = "anthropic"

    def __init__(self, model: str | None = None) -> None:
        self.model_name = model or settings.anthropic_model
        self.client = _client()

    def analyze(self, jpeg: bytes) -> PhotoAnalysis:
        params = message_params(jpeg, self.model_name)
        if settings.anthropic_fallbacks:
            # En cas de refus par un classifieur de securite, l'API rejoue la requete sur un modele de repli.
            message = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
        else:
            message = self.client.messages.create(**params)
        analysis = parse_response(message)
        u = message.usage
        log.info(
            "anthropic %s: in=%s out=%s cache_read=%s",
            self.model_name, u.input_tokens, u.output_tokens, getattr(u, "cache_read_input_tokens", None),
        )
        return analysis
