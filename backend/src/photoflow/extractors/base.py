"""Contrat d'un extracteur.

Un extracteur recoit un lot de photos et renvoie un resultat JSON par photo.
Il peut en plus ecrire dans ses propres tables via `persist`.
Chaque extracteur est versionne : changer `version` permet de relancer proprement.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..models import Extraction


@dataclass
class PhotoRef:
    id: uuid.UUID
    original: Path
    web: Path
    thumb: Path
    width: int
    height: int


class Extractor:
    name: str = "base"
    version: int = 1
    batch_size: int = 1
    model_name: str | None = None

    def run(self, photos: list[PhotoRef]) -> list[dict[str, Any]]:
        """Un dict de resultat par photo, dans le meme ordre."""
        raise NotImplementedError

    def persist(self, session: Session, photo: PhotoRef, result: dict[str, Any]) -> None:
        """Par defaut : upsert dans `extractions`. Les extracteurs peuvent surcharger et appeler super()."""
        stmt = insert(Extraction).values(
            photo_id=photo.id,
            extractor=self.name,
            version=self.version,
            model=self.model_name,
            data=result,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="extractions_photo_extractor_uq",
            set_={"version": self.version, "model": self.model_name, "data": result},
        )
        session.execute(stmt)
