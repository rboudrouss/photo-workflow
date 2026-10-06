"""Worker : reclame des jobs, lance les extracteurs, persiste les resultats."""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid

from sqlalchemy import select

from . import extractors, queue
from .config import settings
from .db import session_scope
from .extractors.base import PhotoRef
from .images import derived_paths, original_path
from .models import Photo

log = logging.getLogger(__name__)


def _refs(session, photo_ids: list[uuid.UUID]) -> dict[uuid.UUID, PhotoRef]:
    photos = session.scalars(select(Photo).where(Photo.id.in_(photo_ids))).all()
    out = {}
    for p in photos:
        d = derived_paths(p.id)
        out[p.id] = PhotoRef(id=p.id, original=original_path(p.rel_path), web=d["web"], thumb=d["thumb"], width=p.width, height=p.height)
    return out


def run_once(loaded: dict[str, extractors.Extractor], worker_id: str) -> int:
    """Traite au plus un lot par extracteur. Renvoie le nombre de jobs traites."""
    processed = 0
    for name, ex in loaded.items():
        with session_scope() as session:
            claimed = queue.claim(session, name, worker_id, ex.batch_size)
        if not claimed:
            continue
        with session_scope() as session:
            refs = _refs(session, [pid for _, pid, _ in claimed])
        jobs = [(jid, refs[pid], att) for jid, pid, att in claimed if pid in refs]
        photos = [ref for _, ref, _ in jobs]
        t0 = time.time()
        try:
            results = ex.run(photos)
            errors = [None] * len(photos)
        except Exception as e:  # echec du lot entier : on retente photo par photo si lot > 1
            log.exception("echec lot %s", name)
            results, errors = [], []
            for ref in photos:
                try:
                    results.append(ex.run([ref])[0])
                    errors.append(None)
                except Exception as e2:
                    results.append(None)
                    errors.append(f"{type(e2).__name__}: {e2}"[:2000])
            if len(photos) == 1 and errors[0] is None:
                errors[0] = f"{type(e).__name__}: {e}"[:2000]
        with session_scope() as session:
            for (jid, ref, attempts), result, err in zip(jobs, results, errors):
                if err is None:
                    try:
                        ex.persist(session, ref, result)
                    except Exception as e:
                        session.rollback()
                        err = f"persist: {type(e).__name__}: {e}"[:2000]
                        log.exception("echec persist %s %s", name, ref.id)
                queue.finish(session, jid, err, attempts, settings.worker_max_attempts)
        processed += len(jobs)
        log.info("%s: %d photo(s) en %.1fs", name, len(jobs), time.time() - t0)
    return processed


def main(names: list[str], once: bool = False) -> None:
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    loaded = {n: extractors.load(n) for n in names}
    log.info("worker %s pret, extracteurs: %s", worker_id, ", ".join(loaded))
    with session_scope() as session:
        n = queue.reset_stale(session)
        if n:
            log.info("%d job(s) orphelin(s) remis en file", n)
    while True:
        n = run_once(loaded, worker_id)
        if once and n == 0:
            return
        if n == 0:
            time.sleep(settings.worker_poll_seconds)
