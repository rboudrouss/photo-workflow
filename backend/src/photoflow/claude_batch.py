"""Traitement en lot via l'API Batches d'Anthropic (moitie prix, resultats sous 24 h).

Flux : `submit` construit un lot de requetes, enregistre la correspondance custom_id -> photo
dans `claude_batches`, puis `collect` recupere les resultats et les persiste en legendes.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, text

from .config import settings
from .db import session_scope
from .extractors.vlm import persist_caption
from .extractors.vlm_backends.anthropic_backend import _client, message_params, parse_response
from .images import derived_paths, open_image, resize_for_vlm
from .models import ClaudeBatch, Photo

log = logging.getLogger(__name__)

MAX_PER_BATCH = 10_000  # limite API : 100 000 requetes ou 256 Mo ; on reste prudent avec des images


def photos_without_caption(session, source_prefix: str, limit: int | None) -> list[Photo]:
    q = text(
        """
        SELECT p.id FROM photos p
        WHERE p.status = 'ready' AND NOT EXISTS (
            SELECT 1 FROM captions c WHERE c.photo_id = p.id AND c.source LIKE :prefix
        )
        ORDER BY p.ingested_at
        """ + ("LIMIT :limit" if limit else "")
    )
    params = {"prefix": source_prefix + "%"}
    if limit:
        params["limit"] = limit
    ids = [r.id for r in session.execute(q, params).all()]
    return session.scalars(select(Photo).where(Photo.id.in_(ids))).all() if ids else []


def submit(limit: int | None = None, model: str | None = None, only_missing: bool = True) -> list[str]:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    model = model or settings.anthropic_model
    source = f"vlm:anthropic:{model}"
    client = _client()
    batch_ids: list[str] = []

    with session_scope() as session:
        photos = photos_without_caption(session, source if only_missing else "\x00", limit)
        if not photos:
            log.info("rien a envoyer")
            return []
        log.info("%d photo(s) a envoyer a %s", len(photos), model)

        for start in range(0, len(photos), MAX_PER_BATCH):
            chunk = photos[start : start + MAX_PER_BATCH]
            requests, mapping = [], {}
            for p in chunk:
                jpeg = resize_for_vlm(open_image(derived_paths(p.id)["web"]))
                cid = f"p-{p.id}"
                mapping[cid] = str(p.id)
                requests.append(Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**message_params(jpeg, model))))
            batch = client.messages.batches.create(requests=requests)
            session.add(ClaudeBatch(id=batch.id, model=model, count=len(requests), mapping=mapping))
            session.commit()
            batch_ids.append(batch.id)
            log.info("lot %s envoye (%d requetes)", batch.id, len(requests))
    return batch_ids


def status() -> list[dict]:
    client = _client()
    out = []
    with session_scope() as session:
        for b in session.scalars(select(ClaudeBatch).order_by(ClaudeBatch.created_at)).all():
            remote = client.messages.batches.retrieve(b.id)
            out.append(
                {
                    "id": b.id,
                    "model": b.model,
                    "local_status": b.status,
                    "remote_status": remote.processing_status,
                    "counts": remote.request_counts.model_dump(),
                }
            )
    return out


def collect(batch_id: str) -> dict:
    client = _client()
    model = None
    with session_scope() as session:
        b = session.get(ClaudeBatch, batch_id)
        if not b:
            raise SystemExit(f"lot inconnu: {batch_id}")
        remote = client.messages.batches.retrieve(batch_id)
        if remote.processing_status != "ended":
            raise SystemExit(f"lot {batch_id} pas termine: {remote.processing_status}")
        mapping, model = dict(b.mapping), b.model

    source = f"vlm:anthropic:{model}"
    counts = {"ok": 0, "invalid": 0, "errored": 0, "other": 0}
    with session_scope() as session:
        for result in client.messages.batches.results(batch_id):
            pid = mapping.get(result.custom_id)
            if pid is None:
                counts["other"] += 1
                continue
            kind = result.result.type
            if kind == "succeeded":
                try:
                    analysis = parse_response(result.result.message)
                    persist_caption(session, uuid.UUID(pid), source, analysis)
                    counts["ok"] += 1
                except Exception as e:
                    log.warning("%s: reponse invalide: %s", pid, e)
                    counts["invalid"] += 1
            elif kind == "errored":
                log.warning("%s: erreur %s", pid, result.result.error)
                counts["errored"] += 1
            else:
                counts["other"] += 1
        b = session.get(ClaudeBatch, batch_id)
        b.status = "collected"
        session.execute(text("UPDATE claude_batches SET collected_at = now() WHERE id = :id"), {"id": batch_id})
    return counts
