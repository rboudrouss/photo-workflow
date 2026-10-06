"""Backend VLM via Claude Code en mode non interactif (`claude -p`), authentifie par CLAUDE_CODE_OAUTH_TOKEN.

Pour utiliser un abonnement Claude (jeton cree avec `claude setup-token`) au lieu d'une cle API Console.
L'image passe en entree stream-json, sans aucun outil, et le JSON est contraint par `--json-schema`.
Le CLI `claude` doit etre installe (image worker construite avec CLAUDE_CLI=1).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile

from ...config import settings
from ...images import to_base64
from ..vlm import SYSTEM_PROMPT, USER_PROMPT, PhotoAnalysis, VLMBackend, json_schema

log = logging.getLogger(__name__)


class ClaudeCodeBackend(VLMBackend):
    backend_name = "claude-code"

    def __init__(self, model: str | None = None) -> None:
        self.model_name = model or settings.anthropic_model
        self.exe = shutil.which("claude")
        if not self.exe:
            raise RuntimeError("CLI `claude` introuvable (image worker construite sans CLAUDE_CLI=1 ?)")
        if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            raise RuntimeError("CLAUDE_CODE_OAUTH_TOKEN manquant")
        self.schema = json.dumps(json_schema(strip_lengths=True))
        # HOME jetable : ni reglages, ni memoire, ni historique d'un utilisateur ne se melent a l'analyse.
        self.home = tempfile.mkdtemp(prefix="photoflow-claude-")

    def analyze(self, jpeg: bytes) -> PhotoAnalysis:
        message = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": to_base64(jpeg)}},
                    {"type": "text", "text": USER_PROMPT},
                ],
            },
        }
        cmd = [
            self.exe, "-p",
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--json-schema", self.schema,
            "--system-prompt", SYSTEM_PROMPT,
            "--tools", "",
            "--model", self.model_name,
            "--effort", settings.anthropic_effort,
            "--no-session-persistence",
        ]
        proc = subprocess.run(
            cmd, input=json.dumps(message) + "\n", capture_output=True, text=True,
            timeout=settings.vlm_timeout, cwd=self.home, env=dict(os.environ, HOME=self.home),
        )
        result = None
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "result":
                result = event
        if result is None:
            raise RuntimeError(f"claude -p sans resultat (code {proc.returncode}): {proc.stderr.strip()[-300:]}")
        if result.get("is_error") or result.get("structured_output") is None:
            raise RuntimeError(f"claude -p: {str(result.get('result'))[:300]}")
        log.info("claude-code %s: cout=%s", self.model_name, result.get("total_cost_usd"))
        return PhotoAnalysis.model_validate(result["structured_output"])
