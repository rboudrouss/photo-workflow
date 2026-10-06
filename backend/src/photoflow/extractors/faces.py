"""Detection et embedding de visages avec InsightFace (RetinaFace + ArcFace, modele buffalo_l).

Les embeddings (512d, normalises) sont ensuite regroupes par `photoflow faces cluster`.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from PIL import Image
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Face
from .base import Extractor, PhotoRef

log = logging.getLogger(__name__)

# Au-dela de cette taille on travaille sur le derive "web" (plus rapide), sinon sur l'original
# pour ne pas perdre les petits visages des photos de groupe.
MAX_ORIGINAL_SIDE = 2400


class FaceExtractor(Extractor):
    name = "faces"
    version = 1
    batch_size = 1

    def __init__(self) -> None:
        from insightface.app import FaceAnalysis

        self.model_name = settings.face_model
        providers = ["CPUExecutionProvider"]
        ctx_id = -1
        if settings.device == "cuda":
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            ctx_id = 0
        elif settings.device == "mps":
            providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
        self.app = FaceAnalysis(name=self.model_name, root=str(settings.models_dir / "insightface"), providers=providers)
        self.app.prepare(ctx_id=ctx_id, det_size=(settings.face_det_size, settings.face_det_size), det_thresh=settings.face_min_score)
        self._last: dict = {}

    def run(self, photos: list[PhotoRef]) -> list[dict[str, Any]]:
        out = []
        for p in photos:
            use_original = max(p.width, p.height) <= MAX_ORIGINAL_SIDE
            img = Image.open(p.original if use_original else p.web).convert("RGB")
            scale = p.width / img.width  # facteur pour revenir aux coordonnees de l'original
            bgr = np.asarray(img)[:, :, ::-1].copy()
            faces = self.app.get(bgr)
            kept = []
            for f in faces:
                x1, y1, x2, y2 = [float(v) for v in f.bbox]
                if min(x2 - x1, y2 - y1) < settings.face_min_px:
                    continue
                kept.append(
                    {
                        "bbox": [round(x1 * scale), round(y1 * scale), round(x2 * scale), round(y2 * scale)],
                        "det_score": float(f.det_score),
                        "age": int(f.age) if getattr(f, "age", None) is not None else None,
                        "gender": ("M" if int(f.gender) == 1 else "F") if getattr(f, "gender", None) is not None else None,
                        "embedding": f.normed_embedding.astype(np.float32).tolist(),
                    }
                )
            self._last[p.id] = kept
            out.append({"count": len(kept), "model": self.model_name, "source": "original" if use_original else "web"})
        return out

    def persist(self, session: Session, photo: PhotoRef, result: dict[str, Any]) -> None:
        session.execute(delete(Face).where(Face.photo_id == photo.id))
        for f in self._last.get(photo.id, []):
            session.add(
                Face(
                    photo_id=photo.id,
                    bbox=f["bbox"],
                    det_score=f["det_score"],
                    age=f["age"],
                    gender=f["gender"],
                    embedding=f["embedding"],
                )
            )
        super().persist(session, photo, result)
