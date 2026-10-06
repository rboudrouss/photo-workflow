"""Ingestion : parcourt un dossier, enregistre chaque photo, genere les derives, met en file."""

from __future__ import annotations

import logging
import unicodedata
from pathlib import Path

from PIL import Image
from sqlalchemy import select

from . import images, queue
from .config import settings
from .db import session_scope
from .models import Photo

log = logging.getLogger(__name__)


def iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in images.SUPPORTED_EXT and not p.name.startswith("."):
            yield p


def _exif_dict(img: Image.Image) -> dict | None:
    try:
        exif = img.getexif()
    except Exception:
        return None
    if not exif:
        return None
    out = {}
    for k, v in exif.items():
        if isinstance(v, (int, float, str)):
            out[str(k)] = v
    return out or None


def ingest_dir(directory: Path, extractors: list[str] | None = None, limit: int | None = None) -> dict:
    """Ingere tous les fichiers image sous `directory` (qui doit etre sous PHOTOS_ROOT)."""
    directory = directory.resolve()
    root = settings.photos_root.resolve()
    try:
        directory.relative_to(root)
    except ValueError as e:
        raise SystemExit(f"{directory} n'est pas sous PHOTOS_ROOT={root}") from e

    extractors = extractors if extractors is not None else settings.default_extractors
    counts = {"new": 0, "duplicate": 0, "error": 0, "jobs": 0}
    new_ids = []

    with session_scope() as session:
        known = set(session.scalars(select(Photo.sha256)).all())

    for i, path in enumerate(iter_files(directory)):
        if limit is not None and counts["new"] >= limit:
            break
        try:
            digest = images.sha256_file(path)
            if digest in known:
                counts["duplicate"] += 1
                continue
            img = images.open_image(path)
            with session_scope() as session:
                photo = Photo(
                    sha256=digest,
                    rel_path=str(path.relative_to(root)),
                    filename=unicodedata.normalize("NFC", path.name),  # macOS scanne en NFD
                    width=img.width,
                    height=img.height,
                    bytes=path.stat().st_size,
                    format=(Image.open(path).format or "").upper() or None,
                    phash=images.phash64(img),
                    exif=_exif_dict(img),
                    status="ready",
                )
                session.add(photo)
                session.flush()
                images.make_derived(img, photo.id)
                counts["jobs"] += queue.enqueue(session, [photo.id], extractors)
                new_ids.append(photo.id)
            known.add(digest)
            counts["new"] += 1
            if counts["new"] % 100 == 0:
                log.info("ingestion: %d nouvelles photos", counts["new"])
        except Exception:
            log.exception("echec ingestion %s", path)
            counts["error"] += 1

    return counts
