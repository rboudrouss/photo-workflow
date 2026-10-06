"""Persistance des resultats d'extraction, a partir du seul dict renvoye par `Extractor.run`.

Separe des extracteurs pour que l'API puisse enregistrer ce qu'un worker distant lui envoie sans charger
torch ou InsightFace. Chaque fonction recoit le resultat complet (y compris vecteurs), ecrit dans les tables
dediees, puis range dans `extractions` une copie sans les champs lourds.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .config import settings
from .modelrank import rank_of_source
from .models import Caption, Extraction, Face, ImageEmbedding
from .nudity import apply_level


class ResultError(ValueError):
    """Resultat mal forme : on ne retente pas, le job passe en echec avec ce message."""


def _store_extraction(session: Session, photo_id: uuid.UUID, name: str, version: int, model: str | None, data: dict) -> None:
    stmt = insert(Extraction).values(photo_id=photo_id, extractor=name, version=version, model=model, data=data)
    stmt = stmt.on_conflict_do_update(
        constraint="extractions_photo_extractor_uq", set_={"version": version, "model": model, "data": data}
    )
    session.execute(stmt)


def _without(result: dict, *keys: str) -> dict:
    return {k: v for k, v in result.items() if k not in keys}


def _vector(value: Any, dim: int, what: str) -> list[float]:
    if not isinstance(value, list) or len(value) != dim:
        raise ResultError(f"{what}: vecteur de {len(value) if isinstance(value, list) else '?'} valeurs, {dim} attendues")
    try:
        return [float(x) for x in value]
    except (TypeError, ValueError) as e:
        raise ResultError(f"{what}: vecteur non numerique") from e


def persist_generic(session: Session, photo_id: uuid.UUID, version: int, model: str | None, result: dict) -> None:
    _store_extraction(session, photo_id, "physical", version, model, result)


def persist_embedding(session: Session, photo_id: uuid.UUID, version: int, model: str | None, result: dict) -> None:
    if result.get("model") != settings.embedding_model:
        raise ResultError(f"modele d'embedding {result.get('model')!r} differe de celui de la base ({settings.embedding_model})")
    vec = _vector(result.get("vector"), settings.embedding_dim, "embedding")
    stmt = insert(ImageEmbedding).values(photo_id=photo_id, model=settings.embedding_model, embedding=vec)
    stmt = stmt.on_conflict_do_update(index_elements=[ImageEmbedding.photo_id], set_={"model": settings.embedding_model, "embedding": vec})
    session.execute(stmt)
    _store_extraction(session, photo_id, "embedding", version, model, _without(result, "vector"))


def persist_faces(session: Session, photo_id: uuid.UUID, version: int, model: str | None, result: dict) -> None:
    faces = result.get("faces")
    if not isinstance(faces, list):
        raise ResultError("faces: liste attendue")
    rows = []
    for f in faces:
        bbox = f.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            raise ResultError("faces: bbox invalide")
        rows.append(
            Face(
                photo_id=photo_id,
                bbox=[int(v) for v in bbox],
                det_score=float(f.get("det_score", 0)),
                age=int(f["age"]) if f.get("age") is not None else None,
                gender=f.get("gender") if f.get("gender") in ("M", "F") else None,
                embedding=_vector(f.get("embedding"), 512, "faces"),
            )
        )
    session.execute(delete(Face).where(Face.photo_id == photo_id))
    session.add_all(rows)
    light = [_without(f, "embedding") for f in faces]
    _store_extraction(session, photo_id, "faces", version, model, {**_without(result, "faces"), "faces": light})


def persist_nudity(session: Session, photo_id: uuid.UUID, version: int, model: str | None, result: dict) -> None:
    apply_level(session, photo_id, result["level"], f"nudity:{result.get('model')}")
    _store_extraction(session, photo_id, "nudity", version, model, result)


def persist_caption(session: Session, photo_id: uuid.UUID, source: str, analysis) -> None:
    """Enregistre une legende machine (PhotoAnalysis) et propage son niveau de nudite."""
    data = analysis.model_dump(mode="json")
    rank = rank_of_source(source)
    stmt = insert(Caption).values(
        photo_id=photo_id, source=source, title=analysis.titre, description=analysis.description, data=data, model_rank=rank
    )
    stmt = stmt.on_conflict_do_update(
        constraint="captions_photo_source_uq",
        set_={"title": analysis.titre, "description": analysis.description, "data": data, "model_rank": rank},
    )
    session.execute(stmt)
    apply_level(session, photo_id, analysis.nudite.niveau.value, source)


def persist_vlm(session: Session, photo_id: uuid.UUID, version: int, model: str | None, result: dict) -> None:
    from .extractors.vlm import PhotoAnalysis  # module leger (pydantic), pas de modele charge

    source = result.get("source")
    if not isinstance(source, str) or not source.startswith("vlm:"):
        raise ResultError("vlm: source manquante")
    try:
        analysis = PhotoAnalysis.model_validate(_without(result, "source"))
    except Exception as e:
        raise ResultError(f"vlm: analyse invalide: {e}"[:1000]) from e
    persist_caption(session, photo_id, source, analysis)
    _store_extraction(session, photo_id, "vlm", version, model, result)


PERSISTERS: dict[str, Callable[[Session, uuid.UUID, int, str | None, dict], None]] = {
    "physical": persist_generic,
    "embedding": persist_embedding,
    "faces": persist_faces,
    "nudity": persist_nudity,
    "vlm": persist_vlm,
}


def persist(session: Session, extractor: str, photo_id: uuid.UUID, version: int, model: str | None, result: dict) -> None:
    """Point d'entree unique, pour le worker local comme pour l'API qui recoit un resultat distant."""
    if extractor not in PERSISTERS:
        raise ResultError(f"extracteur inconnu: {extractor}")
    if not isinstance(result, dict):
        raise ResultError("resultat: objet JSON attendu")
    PERSISTERS[extractor](session, photo_id, version, model, result)
