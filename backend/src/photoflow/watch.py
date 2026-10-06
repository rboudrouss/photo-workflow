"""Surveillance de PHOTOS_ROOT : tout fichier image qui y apparait est ingere comme un upload.

Boucle simple, sans inotify (fonctionne aussi sur un montage reseau ou un volume Docker) : a chaque passage,
les fichiers dont le chemin relatif n'est pas en base sont candidats ; un candidat n'est ingere qu'au passage
suivant s'il n'a pas change de taille ni de date entre-temps (copie terminee). Un fichier en echec est
retente apres RETRY_AFTER. Un fichier remplace sous le meme nom n'est pas revu (chemin deja connu).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from sqlalchemy import select

from . import ingest
from .config import settings
from .db import session_scope
from .models import Photo

log = logging.getLogger(__name__)

RETRY_AFTER = 600.0  # secondes avant de retenter un fichier en echec


def _known(session) -> set[str]:
    return set(session.scalars(select(Photo.rel_path)).all())


def scan_once(root: Path, seen: dict[str, tuple[int, float]], failed: dict[str, float]) -> dict:
    """Un passage. `seen` : signature (taille, mtime) des candidats vus au passage precedent."""
    counts = {"new": 0, "duplicate": 0, "error": 0, "waiting": 0}
    with session_scope() as session:
        known = _known(session)
    now = time.time()
    current: dict[str, tuple[int, float]] = {}
    for path in ingest.iter_files(root):
        rel = str(path.relative_to(root))
        if rel in known:
            continue
        if rel in failed and now - failed[rel] < RETRY_AFTER:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        sig = (st.st_size, st.st_mtime)
        current[rel] = sig
        if seen.get(rel) != sig:
            counts["waiting"] += 1  # vu pour la premiere fois ou encore en cours de copie
            continue
        try:
            with session_scope() as session:
                photo = ingest.ingest_file(session, path, rel)
            log.info("ingere %s (%s)", rel, photo.id)
            counts["new"] += 1
            failed.pop(rel, None)
        except ingest.Duplicate:
            log.info("%s : contenu deja en base, ignore", rel)
            counts["duplicate"] += 1
            failed[rel] = now  # memorise pour ne pas rehacher a chaque passage
        except Exception:
            log.exception("echec ingestion %s", rel)
            counts["error"] += 1
            failed[rel] = now
    seen.clear()
    seen.update(current)
    return counts


def watch(interval: float = 30.0, once: bool = False) -> None:
    root = settings.photos_root
    if not root.is_dir():
        raise SystemExit(f"PHOTOS_ROOT={root} n'est pas un dossier")
    log.info("surveillance de %s toutes les %.0f s", root, interval)
    seen: dict[str, tuple[int, float]] = {}
    failed: dict[str, float] = {}
    while True:
        try:
            counts = scan_once(root, seen, failed)
            if counts["new"] or counts["error"]:
                log.info("passage : %s", counts)
        except Exception:
            log.exception("passage en echec (base injoignable ?)")
        if once:
            return
        time.sleep(interval)
