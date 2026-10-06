"""Signal physique du tirage, calcule sans modele : format, marges, tonalite, nettete.

Quasi constant au sein d'une meme pellicule ou d'une meme seance : sert a confirmer ou infirmer
un regroupement en serie (series.py), et donne des indices d'epoque (sepia, marges blanches, format).
"""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from .base import Extractor, PhotoRef

ANALYSIS_SIDE = 512


def _margins(gray: np.ndarray, max_frac: float = 0.15, white: int = 215) -> dict[str, float]:
    """Largeur de marge claire sur chaque bord, en fraction du cote. 0 si pas de marge."""
    h, w = gray.shape
    out = {}
    for side, lines in (("top", gray), ("bottom", gray[::-1]), ("left", gray.T), ("right", gray.T[::-1])):
        n, limit = 0, int(lines.shape[0] * max_frac)
        for i in range(limit):
            if lines[i].mean() < white:
                break
            n += 1
        out[side] = round(n / lines.shape[0], 4)
    return out


def analyze(img: Image.Image) -> dict[str, Any]:
    small = img.copy()
    small.thumbnail((ANALYSIS_SIDE, ANALYSIS_SIDE))
    rgb = np.asarray(small.convert("RGB")).astype(np.float32)
    gray = np.asarray(small.convert("L")).astype(np.float32)
    hsv = np.asarray(small.convert("HSV")).astype(np.float32)

    # Tonalite : saturation moyenne des tons moyens (on ignore les zones tres sombres/claires).
    mid = (gray > 40) & (gray < 215)
    sat = float(hsv[..., 1][mid].mean() / 255) if mid.any() else 0.0
    hue = float(hsv[..., 0][mid].mean() / 255 * 360) if mid.any() else 0.0
    if sat < 0.07:
        tonality = "neutre"
    elif sat < 0.35 and 15 <= hue <= 55:
        tonality = "sepia"
    else:
        tonality = "couleur"

    margins = _margins(gray)
    has_border = sum(1 for v in margins.values() if v >= 0.01) >= 3

    lap = np.asarray(small.convert("L").filter(ImageFilter.FIND_EDGES)).astype(np.float32)
    ratio = max(img.width, img.height) / min(img.width, img.height)
    return {
        "width": img.width,
        "height": img.height,
        "ratio": round(ratio, 3),
        "orientation": "paysage" if img.width > img.height * 1.03 else "portrait" if img.height > img.width * 1.03 else "carre",
        "tonality": tonality,
        "saturation": round(sat, 3),
        "hue": round(hue, 1),
        "luminance": round(float(gray.mean()), 1),
        "contrast": round(float(gray.std()), 1),
        "sharpness": round(float(lap.var()), 1),
        "margins": margins,
        "has_border": has_border,
        "mean_rgb": [round(float(x), 1) for x in rgb.reshape(-1, 3).mean(axis=0)],
    }


class PhysicalExtractor(Extractor):
    name = "physical"
    version = 1
    batch_size = 8

    def run(self, photos: list[PhotoRef]) -> list[dict[str, Any]]:
        return [analyze(Image.open(p.web)) for p in photos]
