"""Force estimee d'un modele VLM a partir de son nom, pour prioriser les re-analyses.

Un worker distant declare son modele VLM ; le serveur lui donne d'abord les photos jamais analysees, puis
celles dont la meilleure legende vient d'un modele de rang inferieur. Le rang n'a pas besoin d'etre exact,
juste ordonne : un 4B < un 32B < un modele cloud. Surcharge possible cote worker avec VLM_RANK.
"""

from __future__ import annotations

import re

CLOUD_RANK = 1000.0  # Claude, GPT, Gemini : au-dessus de tout modele local courant

_CLOUD = re.compile(r"claude|gpt-|gemini|anthropic|openai", re.I)
_PARAMS = re.compile(r"(\d+(?:\.\d+)?)\s*b(?![a-z0-9])", re.I)  # "4B", "32B", "30B-A3B" -> premier nombre


def rank_of_model(model: str | None) -> float:
    """Rang d'un nom de modele ('Qwen3-VL-32B-Instruct-GGUF:Q8_0' -> 32.0, 'claude-opus-5-5' -> 1000)."""
    if not model:
        return 0.0
    if _CLOUD.search(model):
        return CLOUD_RANK
    m = _PARAMS.search(model)
    return float(m.group(1)) if m else 0.0


def rank_of_source(source: str | None) -> float | None:
    """Rang d'une source de legende ('vlm:llama:<modele>', 'claude-session:...'). None pour 'human'."""
    if not source or source == "human":
        return None
    return rank_of_model(source)
