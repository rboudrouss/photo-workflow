"""Routes des workers distants et de la page « Workers ».

  /api/worker/*   : appelees par le worker, jeton porteur obligatoire (Authorization: Bearer pfw_...).
  /api/workers/*  : appelees par l'interface (liste, reserver N photos, rendre les jobs).
"""

from __future__ import annotations

import io
import uuid
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .. import faces_cluster, persist as persistence, queue, workers
from ..queries import broker as query_broker
from ..config import settings
from ..db import get_session
from ..images import derived_paths, original_path
from ..models import Job, Photo, Worker

router = APIRouter()


def current_worker(authorization: str = Header(default=""), session: Session = Depends(get_session)) -> Worker:
    scheme, _, token = authorization.partition(" ")
    w = workers.authenticate(session, token) if scheme.lower() == "bearer" else None
    if w is None:
        raise HTTPException(401, "jeton de worker invalide")
    return w


def _owned_job(session: Session, w: Worker, job_id: uuid.UUID) -> Job:
    job = session.get(Job, job_id)
    if job is None or job.reserved_for != w.id or job.status != "running":
        raise HTTPException(404, "job inconnu ou non detenu par ce worker")
    return job


# --------------------------------------------------------------------------- appairage (sans jeton)

class PairIn(BaseModel):
    code: str = Field(min_length=20, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    hostname: str | None = Field(default=None, max_length=100)
    extractors: list[str] = Field(default_factory=list, max_length=10)


@router.post("/api/worker/pair")
def worker_pair(body: PairIn, session: Session = Depends(get_session)):
    out = workers.pair_request(session, body.code, body.hostname, body.extractors)
    session.commit()
    return out


# --------------------------------------------------------------------------- cote worker

class HeartbeatIn(BaseModel):
    instance: str = Field(max_length=200)
    hostname: str | None = Field(default=None, max_length=200)
    extractors: list[str] = Field(default_factory=list, max_length=10)
    tasks: list[str] = Field(default_factory=list, max_length=10)
    models: dict[str, str | None] = Field(default_factory=dict)
    versions: dict[str, int] = Field(default_factory=dict)
    vlm_rank: float | None = None


@router.post("/api/worker/heartbeat")
def worker_heartbeat(body: HeartbeatIn, w: Worker = Depends(current_worker), session: Session = Depends(get_session)):
    workers.heartbeat(session, w, body.instance, body.model_dump())
    queue.reset_stale(session)
    workers.reset_stale_tasks(session)
    rows = session.execute(
        text("SELECT extractor, count(*) FROM jobs WHERE reserved_for = :id AND status = 'pending' GROUP BY 1"), {"id": w.id}
    ).all()
    session.commit()
    return {"worker": w.name, "config": workers.server_config(), "assigned": {r[0]: int(r[1]) for r in rows}}


class ClaimIn(BaseModel):
    instance: str = Field(max_length=200)
    extractor: str = Field(max_length=64)
    n: int = Field(default=1, ge=1, le=64)


@router.post("/api/worker/claim")
def worker_claim(body: ClaimIn, w: Worker = Depends(current_worker), session: Session = Depends(get_session)):
    # locked_by = nom@instance : le bail (started_at) est prolonge par les battements de cette instance.
    claimed = queue.claim(session, body.extractor, f"{w.name}@{body.instance}", body.n, reserved_for=w.id)
    if not claimed:
        return {"jobs": []}
    photos = {p.id: p for p in session.scalars(select(Photo).where(Photo.id.in_([pid for _, pid, _ in claimed]))).all()}
    return {
        "jobs": [
            {"job_id": str(jid), "photo_id": str(pid), "attempts": att, "width": photos[pid].width, "height": photos[pid].height}
            for jid, pid, att in claimed if pid in photos
        ]
    }


@router.get("/api/worker/jobs/{job_id}/image")
def worker_job_image(
    job_id: uuid.UUID, kind: Literal["web", "original"] = Query("web"),
    w: Worker = Depends(current_worker), session: Session = Depends(get_session),
):
    job = _owned_job(session, w, job_id)
    p = session.get(Photo, job.photo_id)
    if p is None:
        raise HTTPException(404)
    path = original_path(p.rel_path) if kind == "original" else derived_paths(p.id)["web"]
    if not path.exists():
        raise HTTPException(404, "fichier absent sur le serveur")
    return FileResponse(path, media_type="image/jpeg")


class ResultIn(BaseModel):
    version: int = Field(ge=1)
    model: str | None = Field(default=None, max_length=300)
    result: dict[str, Any]


@router.post("/api/worker/jobs/{job_id}/result")
def worker_job_result(job_id: uuid.UUID, body: ResultIn, w: Worker = Depends(current_worker), session: Session = Depends(get_session)):
    job = _owned_job(session, w, job_id)
    try:
        persistence.persist(session, job.extractor, job.photo_id, body.version, body.model, body.result)
    except persistence.ResultError as e:
        session.rollback()
        queue.finish(session, job.id, f"resultat refuse: {e}", settings.worker_max_attempts, settings.worker_max_attempts)
        session.commit()
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        session.rollback()
        queue.finish(session, job.id, f"persist: {type(e).__name__}: {e}"[:2000], job.attempts, settings.worker_max_attempts)
        session.commit()
        raise HTTPException(500, "echec d'enregistrement")
    queue.finish(session, job.id, None, job.attempts, settings.worker_max_attempts)
    session.commit()
    return {"status": "done"}


class FailIn(BaseModel):
    error: str = Field(max_length=2000)


@router.post("/api/worker/jobs/{job_id}/fail")
def worker_job_fail(job_id: uuid.UUID, body: FailIn, w: Worker = Depends(current_worker), session: Session = Depends(get_session)):
    job = _owned_job(session, w, job_id)
    queue.finish(session, job.id, body.error, job.attempts, settings.worker_max_attempts)
    session.commit()
    return {"status": "failed" if job.attempts >= settings.worker_max_attempts else "pending"}


class ReleaseIn(BaseModel):
    instance: str | None = Field(default=None, max_length=200)


@router.post("/api/worker/release")
def worker_release(body: ReleaseIn, w: Worker = Depends(current_worker), session: Session = Depends(get_session)):
    n = workers.release_running(session, w, body.instance)
    session.commit()
    return {"released": n}


# --------------------------------------------------------------------------- requetes de recherche (cote worker)

@router.get("/api/worker/queries")
def worker_queries(wait: float = Query(20.0, ge=0, le=30), w: Worker = Depends(current_worker)):
    """Long-poll : la prochaine requete texte a encoder, ou {query: null} apres `wait` secondes."""
    q = query_broker.take(wait)
    return {"query": {"id": q.id, "text": q.text} if q else None}


class QueryResultIn(BaseModel):
    vector: list[float] = Field(max_length=8192)


@router.post("/api/worker/queries/{query_id}/result")
def worker_query_result(query_id: str, body: QueryResultIn, w: Worker = Depends(current_worker)):
    if len(body.vector) != settings.embedding_dim:
        raise HTTPException(400, f"{settings.embedding_dim} dimensions attendues")
    return {"delivered": query_broker.answer(query_id, body.vector)}


# --------------------------------------------------------------------------- taches de maintenance (cote worker)

class ClaimTaskIn(BaseModel):
    instance: str = Field(max_length=200)
    kinds: list[str] = Field(default_factory=list, max_length=10)


@router.post("/api/worker/claim-task")
def worker_claim_task(body: ClaimTaskIn, w: Worker = Depends(current_worker), session: Session = Depends(get_session)):
    t = workers.claim_task(session, w, body.instance, [k for k in body.kinds if k in workers.TASK_KINDS])
    if t is None:
        return {"task": None}
    return {"task": {"id": str(t.id), "kind": t.kind, "params": t.params}}


@router.get("/api/worker/tasks/{task_id}/data")
def worker_task_data(task_id: uuid.UUID, w: Worker = Depends(current_worker), session: Session = Depends(get_session)):
    """Donnees de la tache, en binaire numpy (.npz) : pour faces_cluster, ids des visages et matrice float32."""
    t = workers.owned_task(session, w, task_id)
    if t is None:
        raise HTTPException(404, "tache inconnue ou non detenue par ce worker")
    if t.kind == "faces_cluster":
        ids, X = faces_cluster.load_faces(session, float(t.params.get("min_score", 0.6)))
        buf = io.BytesIO()
        np.savez(buf, ids=np.array([str(i) for i in ids]), X=X)
        return Response(buf.getvalue(), media_type="application/octet-stream")
    raise HTTPException(400, "tache sans donnees")


class TaskResultIn(BaseModel):
    result: dict[str, Any]


@router.post("/api/worker/tasks/{task_id}/result")
def worker_task_result(
    task_id: uuid.UUID, body: TaskResultIn, background: BackgroundTasks,
    w: Worker = Depends(current_worker), session: Session = Depends(get_session),
):
    t = workers.owned_task(session, w, task_id)
    if t is None:
        raise HTTPException(404, "tache inconnue ou non detenue par ce worker")
    try:
        if t.kind == "faces_cluster":
            ids, labels = body.result.get("ids"), body.result.get("labels")
            if not isinstance(ids, list) or not isinstance(labels, list) or len(ids) != len(labels):
                raise ValueError("ids et labels de meme longueur attendus")
            summary = faces_cluster.apply_labels(session, [uuid.UUID(i) for i in ids], labels)
        else:
            raise ValueError("tache inconnue")
    except Exception as e:  # noqa: BLE001
        session.rollback()
        workers.finish_task(session, t, None, f"resultat refuse: {e}"[:2000])
        session.commit()
        raise HTTPException(400, str(e)[:300])
    workers.finish_task(session, t, summary, None)
    session.commit()
    # Les series dependent des clusters de visages : on les reconstruit dans la foulee (leger, reste sur le serveur).
    background.add_task(_rebuild_series, t.id)
    return {"status": "done", "result": summary}


def _rebuild_series(task_id: uuid.UUID) -> None:
    from .. import series
    from ..db import session_scope

    try:
        out = series.build()
    except Exception as e:  # noqa: BLE001
        out = {"error": str(e)[:300]}
    with session_scope() as s:
        s.execute(text("UPDATE tasks SET result = result || CAST(:r AS jsonb) WHERE id = :id"), {"r": __import__("json").dumps({"series": out}), "id": task_id})


class TaskFailIn(BaseModel):
    error: str = Field(max_length=2000)


@router.post("/api/worker/tasks/{task_id}/fail")
def worker_task_fail(task_id: uuid.UUID, body: TaskFailIn, w: Worker = Depends(current_worker), session: Session = Depends(get_session)):
    t = workers.owned_task(session, w, task_id)
    if t is None:
        raise HTTPException(404)
    workers.finish_task(session, t, None, body.error)
    session.commit()
    return {"status": "failed"}


# --------------------------------------------------------------------------- cote interface

@router.get("/api/workers")
def list_workers(session: Session = Depends(get_session)):
    queue.reset_stale(session)
    session.commit()
    rows = session.scalars(select(Worker).where(Worker.revoked_at.is_(None)).order_by(Worker.name)).all()
    return {"workers": [workers.describe(session, w) for w in rows], "pending": workers.pending(session)}


class ApproveIn(BaseModel):
    code: str = Field(max_length=64)
    name: str | None = Field(default=None, max_length=60)


@router.post("/api/workers/approve")
def approve_worker(body: ApproveIn, session: Session = Depends(get_session)):
    w = workers.approve(session, body.code, body.name)
    if w is None:
        raise HTTPException(404, "demande inconnue ou deja traitee")
    session.commit()
    return {"id": str(w.id), "name": w.name}


@router.delete("/api/workers/pending/{code}")
def reject_worker(code: str, session: Session = Depends(get_session)):
    ok = workers.reject(session, code)
    session.commit()
    if not ok:
        raise HTTPException(404)
    return {"ok": True}


@router.post("/api/workers/{worker_id}/revoke")
def revoke_worker(worker_id: uuid.UUID, session: Session = Depends(get_session)):
    w = session.get(Worker, worker_id)
    if w is None:
        raise HTTPException(404)
    workers.revoke(session, w.name)
    session.commit()
    return {"ok": True}


class AssignIn(BaseModel):
    n: int = Field(ge=1, le=10000)
    extractors: list[str] = Field(min_length=1, max_length=10)


@router.post("/api/workers/{worker_id}/assign")
def assign_jobs(worker_id: uuid.UUID, body: AssignIn, session: Session = Depends(get_session)):
    w = session.get(Worker, worker_id)
    if w is None or w.revoked_at is not None:
        raise HTTPException(404)
    if not workers.merged(w)["online"]:
        raise HTTPException(409, "worker hors ligne : lance-le d'abord, il apparaitra en ligne au premier battement")
    out = workers.assign(session, w, body.extractors, body.n)
    session.commit()
    return {"assigned": out}


class TaskIn(BaseModel):
    kind: str = Field(max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("/api/workers/{worker_id}/tasks")
def request_task(worker_id: uuid.UUID, body: TaskIn, session: Session = Depends(get_session)):
    w = session.get(Worker, worker_id)
    if w is None or w.revoked_at is not None:
        raise HTTPException(404)
    try:
        t = workers.request_task(session, w, body.kind, body.params)
    except ValueError as e:
        raise HTTPException(409, str(e))
    session.commit()
    return workers._task_out(t)


@router.post("/api/workers/{worker_id}/unassign")
def unassign_jobs(worker_id: uuid.UUID, session: Session = Depends(get_session)):
    w = session.get(Worker, worker_id)
    if w is None:
        raise HTTPException(404)
    n = workers.unassign(session, w)
    session.commit()
    return {"released": n}
