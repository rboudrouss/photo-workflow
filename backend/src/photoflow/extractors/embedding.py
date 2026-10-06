"""Embeddings image et texte avec SigLIP 2 (meme espace vectoriel pour les deux).

Sert a : recherche "plage" -> photos de plage, photos proches, regroupement par serie.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np
from PIL import Image
from ..config import settings
from .base import Extractor, PhotoRef

log = logging.getLogger(__name__)


def _pooled(out):
    """Selon la version de transformers, get_*_features renvoie un tenseur ou un ModelOutput."""
    return getattr(out, "pooler_output", out)


class SiglipEncoder:
    """Singleton paresseux ; partage entre le worker et l'API (pour encoder les requetes texte)."""

    _instance: "SiglipEncoder | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        import torch
        from transformers import AutoModel, AutoProcessor

        self.torch = torch
        self.device = settings.device if settings.device != "cuda" or torch.cuda.is_available() else "cpu"
        log.info("chargement %s sur %s", settings.embedding_model, self.device)
        self.model = AutoModel.from_pretrained(settings.embedding_model, cache_dir=settings.models_dir).to(self.device).eval()
        self.processor = AutoProcessor.from_pretrained(settings.embedding_model, cache_dir=settings.models_dir)

    @classmethod
    def get(cls) -> "SiglipEncoder":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def encode_images(self, imgs: list[Image.Image]) -> np.ndarray:
        with self.torch.no_grad():
            inputs = self.processor(images=imgs, return_tensors="pt").to(self.device)
            feats = _pooled(self.model.get_image_features(**inputs))
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats.float().cpu().numpy()

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        with self.torch.no_grad():
            inputs = self.processor(
                text=texts, padding="max_length", max_length=64, truncation=True, return_tensors="pt"
            ).to(self.device)
            feats = _pooled(self.model.get_text_features(**inputs))
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats.float().cpu().numpy()


class EmbeddingExtractor(Extractor):
    name = "embedding"
    version = 1
    batch_size = settings.embedding_batch_size

    def __init__(self) -> None:
        self.model_name = settings.embedding_model
        self.encoder = SiglipEncoder.get()

    def run(self, photos: list[PhotoRef]) -> list[dict[str, Any]]:
        imgs = [Image.open(p.web).convert("RGB") for p in photos]
        vecs = self.encoder.encode_images(imgs)
        if vecs.shape[1] != settings.embedding_dim:
            raise RuntimeError(
                f"EMBEDDING_DIM={settings.embedding_dim} mais le modele produit {vecs.shape[1]} dimensions"
            )
        return [{"model": self.model_name, "dim": int(vecs.shape[1]), "vector": v.tolist()} for v in vecs]
