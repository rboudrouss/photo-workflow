"""Contrat d'un extracteur.

Un extracteur recoit un lot de photos et renvoie un resultat JSON par photo. Ce dict doit etre
auto-suffisant (vecteurs compris) : c'est lui qui voyage jusqu'au serveur quand le worker est distant,
et c'est `photoflow.persist` qui sait l'ecrire en base. Chaque extracteur est versionne : changer
`version` permet de relancer proprement.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..persist import persist


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

    def needs_original(self, photo: PhotoRef) -> bool:
        """True si l'extracteur lit l'original plutot que le derive web (un worker distant ne telecharge que l'un)."""
        return False

    def persist(self, session: Session, photo: PhotoRef, result: dict[str, Any]) -> None:
        """Ecrit le resultat en base (worker connecte a la base). Voir photoflow.persist."""
        persist(session, self.name, photo.id, self.version, self.model_name, result)
