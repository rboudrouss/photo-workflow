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
    }


def make_derived(img: Image.Image, photo_id: uuid.UUID) -> dict[str, Path]:
    paths = derived_paths(photo_id)
    paths["thumb"].parent.mkdir(parents=True, exist_ok=True)
    for key, size in (("thumb", settings.thumb_size), ("web", settings.web_size)):
        out = img.copy()
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
