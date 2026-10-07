"""Chargement d'images, derives (miniatures), hash perceptuel."""

from __future__ import annotations

import base64
import hashlib
import io
import uuid
from pathlib import Path

import imagehash
from PIL import Image, ImageOps

from .config import settings

SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def open_image(path: Path) -> Image.Image:
    """Ouvre, applique l'orientation EXIF, convertit en RGB."""
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


_ORIENT_SWAP = {5, 6, 7, 8}  # orientations EXIF qui echangent largeur et hauteur


def open_for_ingest(path: Path, max_side: int) -> tuple[Image.Image, int, int, str | None, dict]:
    """Ouvre un scan pour l'ingestion SANS le decoder en pleine resolution.

    Un scan de 40 Mpx decode en entier = 120 Mo de RGB, et on en fait plusieurs copies : de quoi faire tuer un
    petit conteneur. Pour un JPEG, `draft` demande a libjpeg de decoder directement a 1/2, 1/4 ou 1/8 de la
    taille, juste au-dessus de `max_side` : quelques Mo au lieu de 120. Les dimensions renvoyees sont celles de
    l'original (lues dans l'en-tete, orientation EXIF appliquee). L'image renvoyee sert aux derives et au pHash.
    """
    im = Image.open(path)
    fmt = im.format
    width, height = im.size
    exif = {}
    try:
        exif = dict(im.getexif())
    except Exception:
        pass
    if exif.get(0x0112) in _ORIENT_SWAP:
        width, height = height, width
    if fmt == "JPEG":
        im.draft("RGB", (max_side, max_side))
    im = ImageOps.exif_transpose(im)
    if im.mode != "RGB":
        im = im.convert("RGB")
    if max(im.size) > max_side * 2:  # formats sans draft (TIFF, PNG) : on reduit aussitot, une seule copie
        im.thumbnail((max_side * 2, max_side * 2), Image.Resampling.LANCZOS)
    return im, width, height, fmt, exif


def phash64(img: Image.Image) -> int:
    """pHash 64 bits, renvoye en bigint signe pour Postgres."""
    h = imagehash.phash(img, hash_size=8)
    value = int(str(h), 16)
    return value - (1 << 64) if value >= (1 << 63) else value


def derived_paths(photo_id: uuid.UUID) -> dict[str, Path]:
    sub = settings.derived_dir / str(photo_id)[:2]
    return {
        "thumb": sub / f"{photo_id}_thumb.jpg",
        "web": sub / f"{photo_id}_web.jpg",
        "public": sub / f"{photo_id}_public.jpg",
    }


def public_image(photo_id: uuid.UUID, rel_path: str) -> Path:
    """JPEG du lien public (export Delcampe) : plus grand que le derive web, cree a la premiere demande et garde."""
    path = derived_paths(photo_id)["public"]
    if not path.exists():
        img, *_ = open_for_ingest(original_path(rel_path), settings.public_image_size)
        img.thumbnail((settings.public_image_size, settings.public_image_size), Image.Resampling.LANCZOS)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        img.save(tmp, "JPEG", quality=90, optimize=True)
        tmp.replace(path)
    return path


def make_derived(img: Image.Image, photo_id: uuid.UUID) -> dict[str, Path]:
    paths = derived_paths(photo_id)
    paths["thumb"].parent.mkdir(parents=True, exist_ok=True)
    # Du plus grand au plus petit, chaque derive part du precedent : une seule copie de travail a la fois.
    out = img
    for key, size in (("web", settings.web_size), ("thumb", settings.thumb_size)):
        out = out.copy() if out is img else out
        out.thumbnail((size, size), Image.Resampling.LANCZOS)
        out.save(paths[key], "JPEG", quality=88, optimize=True)
    return paths


def resize_for_vlm(img: Image.Image, max_side: int | None = None) -> bytes:
    """JPEG redimensionne pour envoi a un VLM (local ou cloud)."""
    max_side = max_side or settings.vlm_max_side
    out = img.copy()
    out.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def to_base64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")


UPLOADS_PREFIX = "_uploads/"


def original_path(rel_path: str) -> Path:
    """Les photos televersees ont un rel_path '_uploads/<date>/<nom>' resolu sous UPLOADS_DIR (settings.uploads_root),
    les autres sous PHOTOS_ROOT. Si UPLOADS_DIR = PHOTOS_ROOT/_uploads, les deux coincident et le watcher les voit."""
    if rel_path.startswith(UPLOADS_PREFIX):
        return settings.uploads_root / rel_path[len(UPLOADS_PREFIX):]
    return settings.photos_root / rel_path
