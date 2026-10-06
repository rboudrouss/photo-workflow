"""Backend VLM via une API OpenAI-compatible : llama.cpp (llama-server), vLLM, MLX, Ollama.

Le JSON schema est impose via `response_format` (grammaire GBNF cote llama.cpp),
ce qui garantit un JSON valide meme avec un petit modele.
"""

from __future__ import annotations

import json
import logging
import time

import httpx

from ...config import settings
from ...images import to_base64
from ..vlm import SYSTEM_PROMPT, USER_PROMPT, PhotoAnalysis, VLMBackend, json_schema

log = logging.getLogger(__name__)


class OpenAICompatBackend(VLMBackend):
    backend_name = "llama"

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model_name = model or settings.llm_model
        self.client = httpx.Client(
            timeout=settings.vlm_timeout, headers={"Authorization": f"Bearer {settings.llm_api_key}"}
        )
        self._wait_ready()
        if self.model_name == "local":
            self.model_name = self._discover_model()

    def _wait_ready(self, max_wait: float = 3600.0) -> None:
        """Au premier demarrage, llama-server telecharge le modele : on attend qu'il reponde."""
        t0 = time.time()
        while True:
            try:
                r = self.client.get(f"{self.base_url}/models", timeout=10)
                if r.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if time.time() - t0 > max_wait:
                raise RuntimeError(f"serveur VLM injoignable sur {self.base_url} apres {int(max_wait)} s")
            log.info("serveur VLM pas pret sur %s, nouvel essai dans 30 s", self.base_url)
            time.sleep(30)

    def _discover_model(self) -> str:
        try:
            r = self.client.get(f"{self.base_url}/models")
            r.raise_for_status()
            data = r.json().get("data") or []
            if data:
                mid = data[0].get("id", "local")
                return mid.rsplit("/", 1)[-1]
        except Exception as e:  # serveur pas encore pret, nom par defaut
            log.warning("impossible de lister les modeles sur %s: %s", self.base_url, e)
        return "local"

    def analyze(self, jpeg: bytes) -> PhotoAnalysis:
        payload = {
            "model": self.model_name,
            "temperature": 0.2,
            "max_tokens": 2048,
            # Les petits modeles bouclent parfois dans une chaine (legende repetee) : on penalise la repetition.
            "repeat_penalty": 1.15,
            "presence_penalty": 0.3,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{to_base64(jpeg)}"}},
                        {"type": "text", "text": USER_PROMPT},
                    ],
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "photo_analysis", "strict": True, "schema": json_schema()},
            },
        }
        for attempt in range(4):
            try:
                r = self.client.post(f"{self.base_url}/chat/completions", json=payload)
                r.raise_for_status()
                break
            except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
                if attempt == 3:
                    raise
                log.warning("serveur VLM indisponible (%s), nouvel essai dans 30 s", e)
                time.sleep(30)
        choice = r.json()["choices"][0]
        if choice.get("finish_reason") == "length":
            raise RuntimeError("sortie tronquee par max_tokens (le modele a boucle) ; voir repeat_penalty et maxLength")
        return PhotoAnalysis.model_validate(json.loads(choice["message"]["content"]))
