"""Detection de nudite avec NudeNet (detecteur de parties du corps, ONNX).

Le detecteur distingue chaque partie couverte / decouverte. On ne retient que sexe, anus, poitrine
feminine et fesses DECOUVERTS : plage, maillot, torse masculin, ventre, pieds ne declenchent rien.
Regles et seuils dans photoflow/nudity.py.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from PIL import Image
from sqlalchemy.orm import Session

from ..config import settings
from ..nudity import apply_level, level_from_detections
from .base import Extractor, PhotoRef

log = logging.getLogger(__name__)


class NudityExtractor(Extractor):
    name = "nudity"
    version = 1
    batch_size = 1

    def __init__(self) -> None:
        from nudenet import NudeDetector

        providers = ["CPUExecutionProvider"]
        if settings.device == "cuda":
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        elif settings.device == "mps":
            providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
        self.model_name = f"nudenet-{settings.nudity_resolution}"
        self.detector = NudeDetector(providers=providers, inference_resolution=settings.nudity_resolution)

    def run(self, photos: list[PhotoRef]) -> list[dict[str, Any]]:
        out = []
        for p in photos:
            img = Image.open(p.web).convert("RGB")
            scale = p.width / img.width
            bgr = np.asarray(img)[:, :, ::-1].copy()
            raw = self.detector.detect(bgr)
            detections = [
                {
                    "class": d["class"],
                    "score": round(float(d["score"]), 3),
                    # box NudeNet = [x, y, w, h] sur l'image analysee ; on la ramene a l'original en [x1,y1,x2,y2]
                    "bbox": [
                        round(d["box"][0] * scale), round(d["box"][1] * scale),
                        round((d["box"][0] + d["box"][2]) * scale), round((d["box"][1] + d["box"][3]) * scale),
                    ],
                }
                for d in raw
            ]
            level, kept = level_from_detections(detections)
            out.append(
                {
                    "level": level,
                    "model": self.model_name,
                    "retained": kept,
                    "all_detections": [d for d in detections if d["score"] >= 0.2],
                }
            )
        return out

    def persist(self, session: Session, photo: PhotoRef, result: dict[str, Any]) -> None:
        apply_level(session, photo.id, result["level"], f"nudity:{self.model_name}")
        super().persist(session, photo, result)
