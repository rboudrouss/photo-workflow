"""Registre des extracteurs. Import paresseux pour ne pas charger torch dans l'API."""

from __future__ import annotations

from .base import Extractor, PhotoRef

_REGISTRY: dict[str, str] = {
    "embedding": "photoflow.extractors.embedding:EmbeddingExtractor",
    "faces": "photoflow.extractors.faces:FaceExtractor",
    "nudity": "photoflow.extractors.nudity:NudityExtractor",
    "physical": "photoflow.extractors.physical:PhysicalExtractor",
    "vlm": "photoflow.extractors.vlm:VLMExtractor",
}


def available() -> list[str]:
    return sorted(_REGISTRY)


def load(name: str) -> Extractor:
    import importlib

    if name not in _REGISTRY:
        raise KeyError(f"extracteur inconnu: {name} (disponibles: {', '.join(available())})")
    module, cls = _REGISTRY[name].split(":")
    return getattr(importlib.import_module(module), cls)()


__all__ = ["Extractor", "PhotoRef", "available", "load"]
