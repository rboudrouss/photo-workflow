"""API HTTP (FastAPI) consommee par le front.

Routes principales :
  GET  /api/stats
  GET  /api/photos?q=&mode=text|vector&page=&page_size=
  GET  /api/photos/{id}            photo + legendes + extractions + visages + proches + doublons
  PUT  /api/photos/{id}/caption    legende humaine
  GET  /api/photos/{id}/fiche      texte pret a coller sur Delcampe
  GET  /api/export/delcampe.csv?ids=a,b,c
  POST /api/upload                 televerser des photos (multipart, champ files)
  GET  /api/series, /api/series/{id}, PUT /api/series/{id}, POST /api/series/{id}/propagate
  GET  /api/faces/clusters         clusters de visages
  GET  /api/faces/clusters/{cid}
  PUT  /api/faces/clusters/{cid}/person   nommer un cluster
  GET  /api/faces/{id}/crop.jpg
  GET  /media/{thumb|web}/{id}.jpg, /media/original/{id}
  /api/worker/*, /api/workers/*  workers distants (api/workers.py)
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .. import delcampe, ingest
from ..config import settings
from ..nudity import LEVELS as NUDITY_LEVELS, apply_level
from ..db import get_session
from ..images import SUPPORTED_EXT, derived_paths, open_image, original_path
from ..models import Caption, Face, Person, Photo, Series
from ..queue import stats as job_stats

from .workers import router as workers_router

app = FastAPI(title="photoflow", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"]
)
app.include_router(workers_router)


# --------------------------------------------------------------------------- helpers

def _media(photo_id: uuid.UUID) -> dict:
    return {
        "thumb": f"/media/thumb/{photo_id}.jpg",
        "web": f"/media/web/{photo_id}.jpg",
        "original": f"/media/original/{photo_id}",
    }


def _caption_out(c: Caption) -> dict:
    return {
        "id": str(c.id), "source": c.source, "title": c.title, "description": c.description,
        "data": c.data, "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _photo_summary(p: Photo, title: str | None = None, score: float | None = None) -> dict:
    return {
        "id": str(p.id), "filename": p.filename, "width": p.width, "height": p.height,
        "title": title, "score": score, "nudity_level": p.nudity_level, "media": _media(p.id),
    }


def _ensure_human_caption(session: Session, photo_id: uuid.UUID) -> Caption:
    cap = session.scalar(select(Caption).where(Caption.photo_id == photo_id, Caption.source == "human"))
    if cap is None:
        # On part de la meilleure legende machine pour garder les champs structures.
        base = delcampe.best_caption(session, photo_id)
        cap = Caption(
            photo_id=photo_id, source="human",
            title=base.title if base else None, description=base.description if base else None,
            data=dict(base.data) if base and base.data else {},
        )
        session.add(cap)
        session.flush()
    return cap


def _merge_data(base: dict | None, patch: dict) -> dict:
    """Fusion a un niveau : les sous-objets (epoque, lieu) sont fusionnes cle par cle."""
    out = dict(base or {})
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def _series_members(session: Session, sid: int, limit: int | None = None) -> list[dict]:
    sql = f"""
        SELECT p.*, {_best_title_sql()} AS title,
               (SELECT c.data->'epoque'->>'decennie' FROM captions c WHERE c.photo_id = p.id
                ORDER BY (c.source = 'human') DESC, c.updated_at DESC LIMIT 1) AS decade
        FROM photos p WHERE p.series_id = :sid ORDER BY p.rel_path
    """ + (f" LIMIT {int(limit)}" if limit else "")
    return [
        {"id": str(r["id"]), "filename": r["filename"], "title": r["title"], "decade": r["decade"],
         "nudity_level": r["nudity_level"], "media": _media(r["id"])}
        for r in session.execute(text(sql), {"sid": sid}).mappings().all()
    ]


def _series_summary(session: Session, sid: int) -> dict:
    decades = session.execute(
        text(
            """
            SELECT d, count(*) AS n FROM (
                SELECT (SELECT c.data->'epoque'->>'decennie' FROM captions c WHERE c.photo_id = p.id
                        ORDER BY (c.source = 'human') DESC, c.updated_at DESC LIMIT 1) AS d
                FROM photos p WHERE p.series_id = :sid) t
            WHERE d IS NOT NULL AND d <> 'inconnue' GROUP BY d ORDER BY n DESC
            """
        ), {"sid": sid},
    ).all()
    scenes = session.execute(
        text(
            """
            SELECT sc, count(*) AS n FROM (
                SELECT (SELECT c.data->>'scene' FROM captions c WHERE c.photo_id = p.id
                        ORDER BY (c.source = 'human') DESC, c.updated_at DESC LIMIT 1) AS sc
                FROM photos p WHERE p.series_id = :sid) t
            WHERE sc IS NOT NULL GROUP BY sc ORDER BY n DESC
            """
        ), {"sid": sid},
    ).all()
    persons = session.execute(
        text(
            "SELECT pe.name, count(DISTINCT f.photo_id) AS n FROM faces f JOIN persons pe ON pe.id = f.person_id "
            "JOIN photos p ON p.id = f.photo_id WHERE p.series_id = :sid GROUP BY pe.name ORDER BY n DESC"
        ), {"sid": sid},
    ).all()
    clusters = session.execute(
        text(
            "SELECT f.cluster_id, count(DISTINCT f.photo_id) AS n FROM faces f JOIN photos p ON p.id = f.photo_id "
            "WHERE p.series_id = :sid AND f.cluster_id >= 0 AND f.person_id IS NULL GROUP BY f.cluster_id ORDER BY n DESC LIMIT 10"
        ), {"sid": sid},
    ).all()
    nudity = session.scalar(
        text(
            "SELECT nudity_level FROM photos WHERE series_id = :sid AND nudity_level IS NOT NULL "
            "ORDER BY array_position(ARRAY['aucune','suggestive','partielle','integrale'], nudity_level) DESC LIMIT 1"
        ), {"sid": sid},
    )
    return {
        "decades": [{"value": r.d, "count": r.n} for r in decades],
        "scenes": [{"value": r.sc, "count": r.n} for r in scenes],
        "persons": [{"name": r.name, "photos": r.n} for r in persons],
        "unnamed_clusters": [{"cluster_id": r.cluster_id, "photos": r.n} for r in clusters],
        "nudity_max": nudity,
    }


def _best_title_sql() -> str:
    # Legende humaine en priorite, sinon la plus recente.
    return (
        "(SELECT c.title FROM captions c WHERE c.photo_id = p.id "
        "ORDER BY (c.source = 'human') DESC, c.updated_at DESC LIMIT 1)"
    )


# --------------------------------------------------------------------------- routes

@app.get("/api/stats")
def stats(session: Session = Depends(get_session)):
    photos = session.scalar(text("SELECT count(*) FROM photos"))
    captions = session.scalar(text("SELECT count(DISTINCT photo_id) FROM captions"))
    faces = session.scalar(text("SELECT count(*) FROM faces"))
    persons = session.scalar(text("SELECT count(*) FROM persons"))
    clusters = session.scalar(text("SELECT count(DISTINCT cluster_id) FROM faces WHERE cluster_id >= 0"))
    return {
        "photos": photos, "photos_with_caption": captions, "faces": faces, "persons": persons,
        "face_clusters": clusters, "jobs": job_stats(session),
        "config": {"embedding_model": settings.embedding_model, "vlm_backend": settings.vlm_backend},
    }


@app.get("/api/photos")
def list_photos(
    q: str | None = None,
    mode: Literal["text", "vector"] = "text",
    scene: str | None = None,
    decade: str | None = None,
    nudity: Literal["all", "exclude", "only"] = "all",
    type_objet: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(60, ge=1, le=500),
    session: Session = Depends(get_session),
):
    offset = (page - 1) * page_size
    params: dict = {"limit": page_size, "offset": offset}
    where = ["p.status = 'ready'"]
    if scene:
        where.append("EXISTS (SELECT 1 FROM captions c WHERE c.photo_id = p.id AND c.data->>'scene' = :scene)")
        params["scene"] = scene
    if decade:
        where.append("EXISTS (SELECT 1 FROM captions c WHERE c.photo_id = p.id AND c.data->'epoque'->>'decennie' = :decade)")
        params["decade"] = decade
    if type_objet:
        where.append("EXISTS (SELECT 1 FROM captions c WHERE c.photo_id = p.id AND c.data->>'type_objet' = :type_objet)")
        params["type_objet"] = type_objet
    if nudity == "exclude":
        where.append("coalesce(p.nudity_level, 'aucune') = 'aucune'")
    elif nudity == "only":
        where.append("p.nudity_level IN ('suggestive', 'partielle', 'integrale')")

    if q and mode == "vector":
        from ..extractors.embedding import SiglipEncoder

        vec = SiglipEncoder.get().encode_texts([q])[0].tolist()
        params["vec"] = str(vec)
        sql = f"""
            SELECT p.*, {_best_title_sql()} AS title, 1 - (e.embedding <=> CAST(:vec AS vector)) AS score
            FROM photos p JOIN image_embeddings e ON e.photo_id = p.id
            WHERE {' AND '.join(where)}
            ORDER BY e.embedding <=> CAST(:vec AS vector)
            LIMIT :limit OFFSET :offset
        """
        total = None
    elif q:
        where.append(
            "(EXISTS (SELECT 1 FROM captions c WHERE c.photo_id = p.id AND "
            "(c.tsv @@ websearch_to_tsquery('french', :q) OR c.title ILIKE :like OR c.description ILIKE :like)) "
            "OR p.filename ILIKE :like)"
        )
        params["q"], params["like"] = q, f"%{q}%"
        sql = f"""
            SELECT p.*, {_best_title_sql()} AS title, NULL::float AS score
            FROM photos p WHERE {' AND '.join(where)}
            ORDER BY p.ingested_at DESC LIMIT :limit OFFSET :offset
        """
        total = session.scalar(text(f"SELECT count(*) FROM photos p WHERE {' AND '.join(where)}"), params)
    else:
        sql = f"""
            SELECT p.*, {_best_title_sql()} AS title, NULL::float AS score
            FROM photos p WHERE {' AND '.join(where)}
            ORDER BY p.ingested_at DESC LIMIT :limit OFFSET :offset
        """
        total = session.scalar(text(f"SELECT count(*) FROM photos p WHERE {' AND '.join(where)}"), params)

    rows = session.execute(text(sql), params).mappings().all()
    items = [
        {
            "id": str(r["id"]), "filename": r["filename"], "width": r["width"], "height": r["height"],
            "title": r["title"], "score": r["score"], "nudity_level": r["nudity_level"], "media": _media(r["id"]),
        }
        for r in rows
    ]
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@app.get("/api/photos/{photo_id}")
def get_photo(photo_id: uuid.UUID, session: Session = Depends(get_session)):
    p = session.get(Photo, photo_id)
    if not p:
        raise HTTPException(404)

    similar = session.execute(
        text(
            f"""
            SELECT p.*, {_best_title_sql()} AS title, 1 - (e.embedding <=> me.embedding) AS score
            FROM image_embeddings me
            JOIN image_embeddings e ON e.photo_id <> me.photo_id
            JOIN photos p ON p.id = e.photo_id
            WHERE me.photo_id = :id
            ORDER BY e.embedding <=> me.embedding LIMIT 12
            """
        ),
        {"id": photo_id},
    ).mappings().all()

    duplicates = []
    if p.phash is not None:
        duplicates = session.execute(
            text(
                f"""
                SELECT p.*, {_best_title_sql()} AS title, bit_count((p.phash # :h)::bit(64)) AS dist
                FROM photos p WHERE p.id <> :id AND p.phash IS NOT NULL AND bit_count((p.phash # :h)::bit(64)) <= 10
                ORDER BY dist LIMIT 12
                """
            ),
            {"id": photo_id, "h": p.phash},
        ).mappings().all()

    faces = session.execute(
        select(Face, Person.name).outerjoin(Person, Person.id == Face.person_id).where(Face.photo_id == photo_id)
    ).all()

    return {
        **_photo_summary(p),
        "rel_path": p.rel_path, "format": p.format, "bytes": p.bytes, "phash": p.phash,
        "ingested_at": p.ingested_at.isoformat(),
        "nudity_source": p.nudity_source,
        "series": (
            {
                "id": p.series_id,
                "name": session.scalar(text("SELECT name FROM series WHERE id = :sid"), {"sid": p.series_id}),
                "size": session.scalar(text("SELECT count(*) FROM photos WHERE series_id = :sid"), {"sid": p.series_id}),
                "members": [m for m in _series_members(session, p.series_id, 24) if m["id"] != str(p.id)],
            }
            if p.series_id is not None else None
        ),
        "captions": [_caption_out(c) for c in sorted(p.captions, key=lambda c: (c.source != "human", c.source))],
        "extractions": [
            {"extractor": e.extractor, "version": e.version, "model": e.model, "data": e.data, "created_at": e.created_at.isoformat()}
            for e in p.extractions
        ],
        "faces": [
            {
                "id": str(f.id), "bbox": f.bbox, "det_score": f.det_score, "age": f.age, "gender": f.gender,
                "cluster_id": f.cluster_id, "person_id": str(f.person_id) if f.person_id else None, "person_name": name,
                "crop": f"/api/faces/{f.id}/crop.jpg",
            }
            for f, name in faces
        ],
        "similar": [
            {"id": str(r["id"]), "filename": r["filename"], "title": r["title"], "score": r["score"],
             "nudity_level": r["nudity_level"], "media": _media(r["id"])}
            for r in similar
        ],
        "duplicates": [
            {"id": str(r["id"]), "filename": r["filename"], "title": r["title"], "distance": r["dist"],
             "nudity_level": r["nudity_level"], "media": _media(r["id"])}
            for r in duplicates
        ],
        "jobs": [
            {"extractor": r.extractor, "status": r.status, "error": r.error}
            for r in session.execute(text("SELECT extractor, status, error FROM jobs WHERE photo_id = :id"), {"id": photo_id}).all()
        ],
    }


class CaptionIn(BaseModel):
    title: str | None = None
    description: str | None = None
    data: dict | None = None


@app.put("/api/photos/{photo_id}/caption")
def put_caption(photo_id: uuid.UUID, body: CaptionIn, session: Session = Depends(get_session)):
    p = session.get(Photo, photo_id)
    if not p:
        raise HTTPException(404)
    cap = _ensure_human_caption(session, photo_id)
    if body.title is not None:
        cap.title = body.title
    if body.description is not None:
        cap.description = body.description
    if body.data is not None:
        cap.data = _merge_data(cap.data, body.data)
    session.commit()
    session.refresh(cap)
    return _caption_out(cap)


class NudityIn(BaseModel):
    level: Literal["aucune", "suggestive", "partielle", "integrale"]


@app.put("/api/photos/{photo_id}/nudity")
def put_nudity(photo_id: uuid.UUID, body: NudityIn, session: Session = Depends(get_session)):
    """Niveau de nudite fixe par un humain : prime sur tous les extracteurs."""
    if not session.get(Photo, photo_id):
        raise HTTPException(404)
    apply_level(session, photo_id, body.level, "human")
    session.commit()
    return {"nudity_level": body.level, "nudity_source": "human", "levels": NUDITY_LEVELS}


@app.get("/api/photos/{photo_id}/fiche")
def get_fiche(photo_id: uuid.UUID, session: Session = Depends(get_session)):
    p = session.get(Photo, photo_id)
    if not p:
        raise HTTPException(404)
    return delcampe.fiche(session, p)


@app.get("/api/export/delcampe.csv")
def export_delcampe(ids: str, session: Session = Depends(get_session)):
    photo_ids = [uuid.UUID(x) for x in ids.split(",") if x]
    csv_text = delcampe.export_csv(session, photo_ids)
    return PlainTextResponse(csv_text, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=delcampe.csv"})


@app.get("/api/facets")
def facets(session: Session = Depends(get_session)):
    scenes = session.execute(
        text("SELECT data->>'scene' AS k, count(DISTINCT photo_id) AS n FROM captions WHERE data->>'scene' IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
    ).all()
    decades = session.execute(
        text("SELECT data->'epoque'->>'decennie' AS k, count(DISTINCT photo_id) AS n FROM captions WHERE data->'epoque'->>'decennie' IS NOT NULL GROUP BY 1 ORDER BY 1")
    ).all()
    nudity = session.execute(
        text("SELECT coalesce(nudity_level, 'non_analyse') AS k, count(*) AS n FROM photos GROUP BY 1 ORDER BY 1")
    ).all()
    types = session.execute(
        text("SELECT data->>'type_objet' AS k, count(DISTINCT photo_id) AS n FROM captions WHERE data->>'type_objet' IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
    ).all()
    return {
        "types": [{"value": r.k, "count": r.n} for r in types],
        "scenes": [{"value": r.k, "count": r.n} for r in scenes],
        "decades": [{"value": r.k, "count": r.n} for r in decades],
        "nudity": [{"value": r.k, "count": r.n} for r in nudity],
    }


# --------------------------------------------------------------------------- series

@app.get("/api/series")
def list_series(page: int = Query(1, ge=1), page_size: int = Query(40, ge=1, le=200), session: Session = Depends(get_session)):
    total = session.scalar(text("SELECT count(*) FROM series"))
    rows = session.execute(
        text("SELECT id, name, size FROM series ORDER BY size DESC, id LIMIT :l OFFSET :o"),
        {"l": page_size, "o": (page - 1) * page_size},
    ).all()
    items = []
    for r in rows:
        summary = _series_summary(session, r.id)
        items.append(
            {
                "id": r.id, "name": r.name, "size": r.size,
                "samples": _series_members(session, r.id, 6),
                "decade": summary["decades"][0]["value"] if summary["decades"] else None,
                "scene": summary["scenes"][0]["value"] if summary["scenes"] else None,
                "persons": [x["name"] for x in summary["persons"]],
                "nudity_max": summary["nudity_max"],
            }
        )
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@app.get("/api/series/{sid}")
def get_series(sid: int, session: Session = Depends(get_session)):
    s = session.get(Series, sid)
    if not s:
        raise HTTPException(404)
    edges = session.execute(
        text("SELECT photo_a, photo_b, score, reasons FROM series_edges WHERE series_id = :sid ORDER BY score DESC"), {"sid": sid}
    ).all()
    return {
        "id": s.id, "name": s.name, "notes": s.notes, "size": s.size,
        "summary": _series_summary(session, sid),
        "photos": _series_members(session, sid),
        "edges": [{"a": str(e.photo_a), "b": str(e.photo_b), "score": e.score, "reasons": e.reasons} for e in edges],
    }


class SeriesIn(BaseModel):
    name: str | None = None
    notes: str | None = None


@app.put("/api/series/{sid}")
def put_series(sid: int, body: SeriesIn, session: Session = Depends(get_session)):
    s = session.get(Series, sid)
    if not s:
        raise HTTPException(404)
    if body.name is not None:
        s.name = body.name or None
    if body.notes is not None:
        s.notes = body.notes or None
    session.commit()
    return {"id": s.id, "name": s.name, "notes": s.notes}


class PropagateIn(BaseModel):
    """Champs structures a appliquer a toutes les photos de la serie (legende humaine).

    Exemple : {"data": {"epoque": {"decennie": "1930s", "confiance": "forte"}, "lieu": {"ville": "Dinard"}}}
    """

    data: dict
    exclude: list[uuid.UUID] = []
    only_if_unknown: bool = False  # ne pas ecraser une decennie / un lieu deja renseignes par un humain


@app.post("/api/series/{sid}/propagate")
def propagate_series(sid: int, body: PropagateIn, session: Session = Depends(get_session)):
    if not session.get(Series, sid):
        raise HTTPException(404)
    ids = session.scalars(select(Photo.id).where(Photo.series_id == sid)).all()
    excluded = set(body.exclude)
    updated = 0
    for pid in ids:
        if pid in excluded:
            continue
        cap = _ensure_human_caption(session, pid)
        patch = body.data
        if body.only_if_unknown:
            patch = {
                k: v for k, v in body.data.items()
                if not (isinstance(v, dict) and isinstance((cap.data or {}).get(k), dict)
                        and any((cap.data or {})[k].get(sub) not in (None, "", "inconnue") for sub in v))
            }
            if not patch:
                continue
        cap.data = _merge_data(cap.data, patch)
        updated += 1
    session.commit()
    return {"series_id": sid, "photos": len(ids), "updated": updated}


# --------------------------------------------------------------------------- visages

@app.get("/api/faces/clusters")
def face_clusters(session: Session = Depends(get_session)):
    rows = session.execute(
        text(
            """
            SELECT f.cluster_id, count(*) AS n, count(DISTINCT f.photo_id) AS photos,
                   (array_agg(f.id ORDER BY f.det_score DESC))[1:6] AS sample_faces,
                   (SELECT p.name FROM persons p WHERE p.id = (
                        SELECT person_id FROM faces WHERE cluster_id = f.cluster_id AND person_id IS NOT NULL
                        GROUP BY person_id ORDER BY count(*) DESC LIMIT 1)) AS person_name
            FROM faces f WHERE f.cluster_id >= 0
            GROUP BY f.cluster_id ORDER BY n DESC
            """
        )
    ).all()
    return [
        {"cluster_id": r.cluster_id, "faces": r.n, "photos": r.photos, "person_name": r.person_name,
         "samples": [f"/api/faces/{fid}/crop.jpg" for fid in r.sample_faces]}
        for r in rows
    ]


@app.get("/api/faces/clusters/{cluster_id}")
def face_cluster(cluster_id: int, session: Session = Depends(get_session)):
    rows = session.execute(
        select(Face, Photo, Person.name)
        .join(Photo, Photo.id == Face.photo_id)
        .outerjoin(Person, Person.id == Face.person_id)
        .where(Face.cluster_id == cluster_id)
        .order_by(Face.det_score.desc())
    ).all()
    return {
        "cluster_id": cluster_id,
        "faces": [
            {"id": str(f.id), "photo": _photo_summary(p), "crop": f"/api/faces/{f.id}/crop.jpg",
             "det_score": f.det_score, "age": f.age, "gender": f.gender, "person_name": name}
            for f, p, name in rows
        ],
    }


class PersonIn(BaseModel):
    name: str


@app.put("/api/faces/clusters/{cluster_id}/person")
def name_cluster(cluster_id: int, body: PersonIn, session: Session = Depends(get_session)):
    person = session.scalar(select(Person).where(Person.name == body.name))
    if person is None:
        person = Person(name=body.name)
        session.add(person)
        session.flush()
    r = session.execute(text("UPDATE faces SET person_id = :pid WHERE cluster_id = :cid"), {"pid": person.id, "cid": cluster_id})
    session.commit()
    return {"person_id": str(person.id), "name": person.name, "faces_updated": r.rowcount}


@app.get("/api/persons")
def persons(session: Session = Depends(get_session)):
    rows = session.execute(
        text("SELECT p.id, p.name, count(f.id) AS faces, count(DISTINCT f.photo_id) AS photos FROM persons p LEFT JOIN faces f ON f.person_id = p.id GROUP BY p.id ORDER BY p.name")
    ).all()
    return [{"id": str(r.id), "name": r.name, "faces": r.faces, "photos": r.photos} for r in rows]


@app.get("/api/faces/{face_id}/crop.jpg")
def face_crop(face_id: uuid.UUID, session: Session = Depends(get_session)):
    f = session.get(Face, face_id)
    if not f:
        raise HTTPException(404)
    p = f.photo
    img = open_image(derived_paths(p.id)["web"])
    s = img.width / p.width
    x1, y1, x2, y2 = f.bbox
    w, h = x2 - x1, y2 - y1
    pad = 0.35
    box = (
        max(0, int((x1 - w * pad) * s)), max(0, int((y1 - h * pad) * s)),
        min(img.width, int((x2 + w * pad) * s)), min(img.height, int((y2 + h * pad) * s)),
    )
    crop = img.crop(box)
    crop.thumbnail((256, 256))
    buf = io.BytesIO()
    crop.save(buf, "JPEG", quality=85)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


# --------------------------------------------------------------------------- upload

MAX_UPLOAD_BYTES = 200 * 1024 * 1024


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...), session: Session = Depends(get_session)):
    """Televerse des photos. Le nom de fichier est conserve tel quel dans photos.filename (c'est l'identifiant
    de l'utilisateur) ; le fichier est range sous DATA_DIR/_uploads/<date>/. Un fichier deja en base (meme
    contenu) est signale comme doublon avec l'id de la photo existante."""
    out = []
    for f in files:
        name = ingest.safe_filename(f.filename or "")
        item: dict = {"filename": name}
        try:
            if Path(name).suffix.lower() not in SUPPORTED_EXT:
                raise ValueError(f"format non pris en charge ({Path(name).suffix or 'sans extension'})")
            data = await f.read()
            if len(data) > MAX_UPLOAD_BYTES:
                raise ValueError("fichier trop gros (max 200 Mo)")
            if not data:
                raise ValueError("fichier vide")
            path, rel = ingest.upload_target(name, data)
            written = not path.exists()
            if written:
                path.write_bytes(data)
            try:
                photo = ingest.ingest_file(session, path, rel, filename=name)
                session.commit()
            except ingest.Duplicate as d:
                session.rollback()
                if written:
                    path.unlink(missing_ok=True)  # deja en base ailleurs : pas de copie orpheline
                item.update(status="duplicate", existing_id=str(d.existing_id))
                out.append(item)
                continue
            except Exception:
                session.rollback()
                if written:
                    path.unlink(missing_ok=True)
                raise
            item.update(status="new", id=str(photo.id))
        except Exception as e:  # noqa: BLE001
            item.update(status="error", error=str(e)[:300])
        out.append(item)
    return {"items": out}


# --------------------------------------------------------------------------- media

@app.get("/media/{kind}/{photo_id}.jpg")
def media(kind: Literal["thumb", "web"], photo_id: uuid.UUID):
    path = derived_paths(photo_id)[kind]
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/media/original/{photo_id}")
def media_original(photo_id: uuid.UUID, session: Session = Depends(get_session)):
    p = session.get(Photo, photo_id)
    if not p:
        raise HTTPException(404)
    return FileResponse(original_path(p.rel_path), filename=p.filename)
