"""Niveaux de nudite et regles de fusion entre les signaux (NudeNet, VLM, humain).

Niveaux, du plus bas au plus haut :
  aucune     : rien, y compris plage, maillot de bain, torse nu masculin, sous-vetements non suggestifs
  suggestive : pose ou tenue erotisee (lingerie, deshabille, pin-up) sans nudite visible
  partielle  : poitrine feminine ou fesses decouvertes
  integrale  : sexe ou anus visible

Un niveau fixe par un humain n'est jamais ecrase par un extracteur.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

LEVELS = ["aucune", "suggestive", "partielle", "integrale"]
RANK = {lvl: i for i, lvl in enumerate(LEVELS)}

# Classes NudeNet qui comptent, et le niveau qu'elles impliquent.
# Les classes *_COVERED, BELLY/ARMPITS/FEET_EXPOSED, MALE_BREAST_EXPOSED et FACE_* sont ignorees :
# c'est exactement ce qu'on voit sur une plage.
NUDENET_LEVEL = {
    "FEMALE_GENITALIA_EXPOSED": "integrale",
    "MALE_GENITALIA_EXPOSED": "integrale",
    "ANUS_EXPOSED": "integrale",
    "FEMALE_BREAST_EXPOSED": "partielle",
    "BUTTOCKS_EXPOSED": "partielle",
}

# Seuils de score par classe. Les parties genitales ont des faux positifs frequents sur des zones sombres
# ou floues de vieilles photos : on exige plus.
NUDENET_MIN_SCORE = {
    "FEMALE_GENITALIA_EXPOSED": 0.55,
    "MALE_GENITALIA_EXPOSED": 0.55,
    "ANUS_EXPOSED": 0.6,
    "FEMALE_BREAST_EXPOSED": 0.45,
    "BUTTOCKS_EXPOSED": 0.5,
}


def max_level(*levels: str | None) -> str:
    best = "aucune"
    for lvl in levels:
        if lvl in RANK and RANK[lvl] > RANK[best]:
            best = lvl
    return best


def level_from_detections(detections: list[dict]) -> tuple[str, list[dict]]:
    """Niveau derive des detections NudeNet, et la liste des detections retenues."""
    kept, level = [], "aucune"
    for d in detections:
        cls, score = d["class"], float(d["score"])
        if cls in NUDENET_LEVEL and score >= NUDENET_MIN_SCORE[cls]:
            kept.append(d)
            level = max_level(level, NUDENET_LEVEL[cls])
    return level, kept


def apply_level(session: Session, photo_id: uuid.UUID, level: str, source: str) -> None:
    """Met a jour photos.nudity_level.

    - source 'human' : ecrase toujours.
    - sinon : ne touche pas une valeur humaine ; sinon prend le max entre la valeur machine existante
      et la nouvelle (un extracteur ne peut pas abaisser ce qu'un autre a detecte, sauf relance de lui-meme).
    """
    if level not in RANK:
        raise ValueError(f"niveau inconnu: {level}")
    if source == "human":
        session.execute(
            text("UPDATE photos SET nudity_level = :l, nudity_source = 'human' WHERE id = :id"),
            {"l": level, "id": photo_id},
        )
        return
    row = session.execute(
        text("SELECT nudity_level, nudity_source FROM photos WHERE id = :id"), {"id": photo_id}
    ).first()
    if row is None or row.nudity_source == "human":
        return
    current = row.nudity_level
    # Relance du meme extracteur : il peut corriger sa propre valeur ; un autre extracteur ne peut que monter.
    new = level if row.nudity_source == source else max_level(current, level)
    src = source if (new == level) else row.nudity_source
    session.execute(
        text("UPDATE photos SET nudity_level = :l, nudity_source = :s WHERE id = :id"),
        {"l": new, "s": src, "id": photo_id},
    )
