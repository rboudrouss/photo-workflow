"""Export Delcampe.

Delcampe n'expose pas d'API ouverte. Deux voies :
- Easy Uploader (Club+ Gold) : import CSV/Excel. Le format exact des colonnes depend du modele
  fourni par Delcampe dans l'interface vendeur ; ce module produit un CSV generique a remapper.
- Saisie manuelle : `fiche()` renvoie le texte pret a coller.

Voir docs/delcampe.md.
"""

from __future__ import annotations

import csv
import io
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Caption, Photo


def best_caption(session: Session, photo_id: uuid.UUID) -> Caption | None:
    """La legende humaine prime, sinon la plus recente."""
    caps = session.scalars(select(Caption).where(Caption.photo_id == photo_id)).all()
    if not caps:
        return None
    caps.sort(key=lambda c: (c.source != "human", -(c.updated_at.timestamp() if c.updated_at else 0)))
    return caps[0]


def fiche(session: Session, photo: Photo) -> dict:
    cap = best_caption(session, photo.id)
    data = (cap.data if cap else None) or {}
    lines = []
    if cap and cap.description:
        lines.append(cap.description)
    ep = data.get("epoque") or {}
    lieu = data.get("lieu") or {}
    if ep.get("decennie") and ep.get("decennie") != "inconnue":
        lines.append(f"Epoque estimee : {ep['decennie']} (confiance {ep.get('confiance', '?')}).")
    loc = ", ".join(v for v in [lieu.get("lieu_precis"), lieu.get("ville"), lieu.get("region"), lieu.get("pays")] if v)
    if loc:
        lines.append(f"Lieu suppose : {loc} (confiance {lieu.get('confiance', '?')}).")
    piece = data.get("piece") or {}
    if piece:
        det = ", ".join(str(v) for v in [piece.get("pays"), piece.get("valeur_faciale"), piece.get("type_ou_graveur"), piece.get("annee"), piece.get("metal"), piece.get("atelier")] if v)
        if det:
            lines.append(f"Piece : {det}.")
        if piece.get("etat_estime"):
            lines.append(f"Etat estime : {piece['etat_estime']} (a confirmer en main).")
        if piece.get("annotations"):
            lines.append("Annotations de l'etui : " + " / ".join(piece["annotations"]) + ".")
    cp = data.get("carte_postale") or {}
    if cp:
        det = ", ".join(str(v) for v in [cp.get("editeur"), f"n° {cp['numero']}" if cp.get("numero") else None, "carte-photo" if cp.get("carte_photo") else None] if v)
        if det:
            lines.append(f"Carte postale : {det}.")
        if cp.get("legende_imprimee"):
            lines.append(f"Legende : {cp['legende_imprimee']}")
        if cp.get("voyagee") is not None:
            lines.append("Voyagee" + (f", cachet {cp.get('cachet_lieu') or ''} {cp.get('cachet_date') or ''}".rstrip() if cp.get("voyagee") else "non voyagee") + ".")
    if (data.get("nombre_objets") or 1) > 1 and data.get("lot"):
        lines.append(f"Lot de {data['nombre_objets']} : " + " ; ".join(o.get("resume", "") for o in data["lot"]) + ".")
    if data.get("type_objet") in (None, "photographie"):
        lines.append(f"Format : {photo.width} x {photo.height} px (scan). Tirage original, voir photo pour l'etat.")
    if data.get("etat"):
        lines.append("Defauts : " + ", ".join(data["etat"]) + ".")
    return {
        "photo_id": str(photo.id),
        "title": (cap.title if cap else None) or photo.filename,
        "description": "\n".join(lines),
        "category": data.get("categorie_delcampe"),
        "type_objet": data.get("type_objet"),
        "face": data.get("face"),
        "tags": data.get("tags") or [],
        "nudity_level": photo.nudity_level,
        "source": cap.source if cap else None,
    }


def export_csv(session: Session, photo_ids: list[uuid.UUID]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["photo_id", "title", "description", "category", "tags", "nudity_level", "image_file"])
    for pid in photo_ids:
        photo = session.get(Photo, pid)
        if not photo:
            continue
        f = fiche(session, photo)
        w.writerow([f["photo_id"], f["title"], f["description"], f["category"] or "", ", ".join(f["tags"]), f["nudity_level"] or "", photo.rel_path])
    return buf.getvalue()
