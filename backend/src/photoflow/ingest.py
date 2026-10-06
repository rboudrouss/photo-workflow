"""Ingestion : parcourt un dossier, enregistre chaque photo, genere les derives, met en file."""

from __future__ import annotations

import datetime as dt
import logging
import re
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


class Duplicate(Exception):
    """Fichier deja en base (meme sha256). `existing_id` = la photo existante."""

    def __init__(self, existing_id):
        super().__init__("doublon")
        self.existing_id = existing_id


def ingest_file(session, path: Path, rel_path: str, extractors: list[str] | None = None, filename: str | None = None) -> Photo:
    """Enregistre un fichier image deja en place sur le disque, genere ses derives, met ses jobs en file."""
    digest = images.sha256_file(path)
    existing = session.scalar(select(Photo.id).where(Photo.sha256 == digest))
    if existing is not None:
        raise Duplicate(existing)
    img, width, height, fmt, exif = images.open_for_ingest(path, settings.web_size)
    photo = Photo(
        sha256=digest,
        rel_path=rel_path,
        filename=unicodedata.normalize("NFC", filename or path.name),  # macOS scanne en NFD
        width=width,
        height=height,
        bytes=path.stat().st_size,
        format=(fmt or "").upper() or None,
        phash=images.phash64(img),
        exif={str(k): v for k, v in exif.items() if isinstance(v, (int, float, str))} or None,
        status="ready",
    )
    session.add(photo)
    session.flush()
    images.make_derived(img, photo.id)
    queue.enqueue(session, [photo.id], extractors if extractors is not None else settings.default_extractors)
    return photo


_UNSAFE = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")


def safe_filename(name: str) -> str:
    """Nom de fichier tel qu'envoye, normalise NFC, sans separateurs de chemin ni caracteres de controle."""
    name = unicodedata.normalize("NFC", Path(name).name).strip()
    name = _UNSAFE.sub("_", name)
    return name or "sans-nom"


def upload_target(filename: str, data: bytes) -> tuple[Path, str]:
    """Emplacement d'un fichier televerse : UPLOADS_DIR/<date>/<nom>. Si un fichier du meme nom existe deja
    avec un autre contenu, suffixe ' (2)', ' (3)'... Le nom d'origine reste dans photos.filename."""
    day = dt.date.today().isoformat()
    folder = settings.uploads_root / day
    folder.mkdir(parents=True, exist_ok=True)
    stem, suffix = Path(filename).stem, Path(filename).suffix
    candidate, i = folder / filename, 2
    while candidate.exists():
        if candidate.read_bytes() == data:
            break
        candidate = folder / f"{stem} ({i}){suffix}"
        i += 1
    rel = f"{images.UPLOADS_PREFIX}{day}/{candidate.name}"
    return candidate, rel


def ingest_dir(directory: Path, extractors: list[str] | None = None, limit: int | None = None) -> dict:
    """Ingere tous les fichiers image sous `directory` (qui doit etre sous PHOTOS_ROOT)."""
    directory = directory.resolve()
    root = settings.photos_root.resolve()
    try:
        directory.relative_to(root)
    except ValueError as e:
        raise SystemExit(f"{directory} n'est pas sous PHOTOS_ROOT={root}") from e

    counts = {"new": 0, "duplicate": 0, "error": 0}
    for path in iter_files(directory):
        if limit is not None and counts["new"] >= limit:
            break
        try:
            with session_scope() as session:
                ingest_file(session, path, str(path.relative_to(root)), extractors)
            counts["new"] += 1
            if counts["new"] % 100 == 0:
                log.info("ingestion: %d nouvelles photos", counts["new"])
        except Duplicate:
            counts["duplicate"] += 1
        except Exception:
            log.exception("echec ingestion %s", path)
            counts["error"] += 1
    return counts
