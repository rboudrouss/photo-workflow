"""Workers distants : jetons, battements, reservation de jobs.

Modele de securite : le worker n'expose rien, il ne fait que des requetes sortantes vers l'API avec un jeton
porteur propre a lui (cree par `photoflow workers create`, stocke hache). L'API ne lui sert que les jobs qui
lui sont reserves, et ne persiste un resultat que pour un job qu'il detient. Le navigateur ne parle jamais
au worker : il demande au serveur de reserver N photos, le worker les recupere a son prochain passage.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .config import settings
from .models import Job, PairRequest, Task, Worker

INSTANCE_TTL = timedelta(minutes=5)  # une instance sans battement depuis 5 min est oubliee
ONLINE_WINDOW = timedelta(minutes=2)

TRACKED = ("physical", "embedding", "faces", "nudity", "vlm")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _unique_name(session: Session, base: str) -> str:
    base = (base or "worker").strip()[:60]
    taken = set(session.scalars(select(Worker.name).where(Worker.name.like(f"{base}%"))).all())
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def create(session: Session, name: str) -> tuple[Worker, str]:
    """Cree un worker et renvoie son jeton EN CLAIR, une seule fois (seul le hache est stocke)."""
    token = "pfw_" + secrets.token_urlsafe(32)
    w = Worker(name=_unique_name(session, name), token_hash=_hash(token), instances={})
    session.add(w)
    session.flush()
    return w, token


# --------------------------------------------------------------------------- appairage
#
# Un worker sans jeton se signale avec un code aleatoire (genere chez lui, conserve entre redemarrages).
# La page Workers liste ces demandes ; « Approuver » cree le worker et depose le jeton sur la demande ;
# le worker le recupere au passage suivant, apres quoi le jeton en clair est efface. Le point d'entree
# est sans authentification, donc borne : demandes expirees apres PENDING_TTL, au plus MAX_PENDING.

PENDING_TTL = timedelta(minutes=15)
MAX_PENDING = 50


def pair_request(session: Session, code: str, hostname: str | None, extractors: list[str]) -> dict[str, Any]:
    """Appele par le worker. Renvoie {'status': 'pending'} ou {'status': 'approved', 'token', 'name'}."""
    now = datetime.now(timezone.utc)
    session.execute(
        text("DELETE FROM pair_requests WHERE worker_id IS NULL AND last_seen_at < :t"), {"t": now - PENDING_TTL}
    )
    req = session.get(PairRequest, code)
    if req is None:
        pending = session.scalar(text("SELECT count(*) FROM pair_requests WHERE worker_id IS NULL")) or 0
        if pending >= MAX_PENDING:
            return {"status": "pending"}
        req = PairRequest(code=code)
        session.add(req)
    req.hostname = (hostname or "")[:100] or None
    req.extractors = [e for e in extractors if e in TRACKED]
    req.last_seen_at = now
    if req.worker_id is None:
        return {"status": "pending"}
    w = session.get(Worker, req.worker_id)
    if w is None or w.revoked_at is not None or req.token is None:
        session.delete(req)  # jeton deja remis ou worker revoque : le worker doit refaire une demande
        return {"status": "pending"}
    token, req.token = req.token, None  # remis une seule fois
    session.delete(req)
    return {"status": "approved", "token": token, "name": w.name}


def pending(session: Session) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows = session.scalars(
        select(PairRequest).where(PairRequest.worker_id.is_(None), PairRequest.last_seen_at > now - ONLINE_WINDOW)
        .order_by(PairRequest.created_at)
    ).all()
    return [
        {"code": r.code, "hostname": r.hostname, "extractors": r.extractors or [], "since": r.created_at.isoformat()}
        for r in rows
    ]


def approve(session: Session, code: str, name: str | None) -> Worker | None:
    req = session.get(PairRequest, code)
    if req is None or req.worker_id is not None:
        return None
    w, token = create(session, name or req.hostname or "worker")
    req.worker_id = w.id
    req.token = token
    return w


def reject(session: Session, code: str) -> bool:
    req = session.get(PairRequest, code)
    if req is None:
        return False
    session.delete(req)
    return True


def revoke(session: Session, name: str) -> bool:
    w = session.scalar(select(Worker).where(Worker.name == name))
    if not w:
        return False
    w.revoked_at = datetime.now(timezone.utc)
    session.execute(text("UPDATE jobs SET reserved_for = NULL WHERE reserved_for = :id AND status = 'pending'"), {"id": w.id})
    return True


def authenticate(session: Session, token: str | None) -> Worker | None:
    if not token or not token.startswith("pfw_"):
        return None
    w = session.scalar(select(Worker).where(Worker.token_hash == _hash(token)))
    if w is None or w.revoked_at is not None:
        return None
    return w


# --------------------------------------------------------------------------- battements

def heartbeat(session: Session, w: Worker, instance: str, info: dict[str, Any]) -> None:
    """Enregistre l'etat d'une instance (processus) du worker et prolonge le bail de ses jobs en cours."""
    now = datetime.now(timezone.utc)
    inst = {k: v for k, v in dict(w.instances or {}).items()
            if datetime.fromisoformat(v.get("last_seen", "1970-01-01T00:00:00+00:00")) > now - INSTANCE_TTL}
    inst[instance] = {
        "extractors": [e for e in info.get("extractors", []) if e in TRACKED],
        "tasks": [t for t in info.get("tasks", []) if t in TASK_KINDS],
        "models": info.get("models") or {},
        "versions": info.get("versions") or {},
        "vlm_rank": info.get("vlm_rank"),
        "hostname": info.get("hostname"),
        "last_seen": now.isoformat(),
    }
    w.instances = inst
    w.last_seen_at = now
    for table in ("jobs", "tasks"):
        session.execute(
            text(f"UPDATE {table} SET started_at = now() WHERE status = 'running' AND reserved_for = :id AND locked_by = :lb"),
            {"id": w.id, "lb": f"{w.name}@{instance}"},
        )


def merged(w: Worker) -> dict[str, Any]:
    """Union des instances vivantes : extracteurs, modeles, versions, rang VLM."""
    now = datetime.now(timezone.utc)
    extractors: list[str] = []
    tasks: list[str] = []
    models: dict[str, str] = {}
    versions: dict[str, int] = {}
    vlm_rank: float | None = None
    hosts: set[str] = set()
    last_seen: datetime | None = None
    for v in (w.instances or {}).values():
        seen = datetime.fromisoformat(v["last_seen"])
        last_seen = max(last_seen, seen) if last_seen else seen
        if seen < now - ONLINE_WINDOW:
            continue
        for e in v.get("extractors", []):
            if e not in extractors:
                extractors.append(e)
        for t in v.get("tasks", []):
            if t not in tasks:
                tasks.append(t)
        models.update(v.get("models") or {})
        versions.update(v.get("versions") or {})
        if v.get("vlm_rank") is not None:
            vlm_rank = max(vlm_rank or 0.0, float(v["vlm_rank"]))
        if v.get("hostname"):
            hosts.add(v["hostname"])
    return {
        "extractors": extractors, "tasks": tasks, "models": models, "versions": versions, "vlm_rank": vlm_rank,
        "hosts": sorted(hosts), "online": bool(extractors or tasks), "last_seen": last_seen.isoformat() if last_seen else None,
    }


# --------------------------------------------------------------------------- selection des photos

def _needing_sql(extractor: str, count: bool) -> str:
    """Photos qu'il reste a traiter pour cet extracteur, hors celles deja en cours ou reservees a un autre.

    - extracteurs classiques : pas d'extraction, ou extraction d'une version plus ancienne que celle du worker.
    - vlm : pas de legende machine, ou meilleure legende issue d'un modele de rang inferieur (modelrank.py).
      Les jamais-analysees passent en premier, puis les plus faiblement analysees.
    """
    exclude = (
        "AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.photo_id = p.id AND j.extractor = :ex "
        "AND (j.status = 'running' OR (j.status = 'pending' AND j.reserved_for IS NOT NULL)))"
    )
    if extractor == "vlm":
        select_ = "count(*)" if count else "p.id"
        order = "" if count else "ORDER BY (b.r IS NULL) DESC, b.r ASC, p.ingested_at LIMIT :n"
        return (
            "WITH best AS (SELECT photo_id, coalesce(max(model_rank), 0) AS r FROM captions "
            "WHERE source <> 'human' GROUP BY photo_id) "
            f"SELECT {select_} FROM photos p LEFT JOIN best b ON b.photo_id = p.id "
            f"WHERE p.status = 'ready' AND (b.r IS NULL OR b.r < :rank) {exclude} {order}"
        )
    select_ = "count(*)" if count else "p.id"
    order = "" if count else "ORDER BY p.ingested_at LIMIT :n"
    return (
        f"SELECT {select_} FROM photos p WHERE p.status = 'ready' "
        "AND NOT EXISTS (SELECT 1 FROM extractions e WHERE e.photo_id = p.id AND e.extractor = :ex AND e.version >= :ver) "
        f"{exclude} {order}"
    )


def backlog(session: Session, extractor: str, vlm_rank: float | None, version: int) -> int:
    params = {"ex": extractor, "ver": version, "rank": vlm_rank or 0.0}
    return int(session.scalar(text(_needing_sql(extractor, count=True)), params) or 0)


def assign(session: Session, w: Worker, extractors: list[str], n: int) -> dict[str, int]:
    """Reserve jusqu'a n photos par extracteur pour ce worker (cree ou recycle les jobs)."""
    info = merged(w)
    out: dict[str, int] = {}
    for ex in extractors:
        if ex not in info["extractors"]:
            continue  # le worker ne gere pas cet extracteur en ce moment
        params = {"ex": ex, "ver": int(info["versions"].get(ex, 1)), "rank": info["vlm_rank"] or 0.0, "n": n}
        ids = session.execute(text(_needing_sql(ex, count=False)), params).scalars().all()
        if not ids:
            out[ex] = 0
            continue
        stmt = insert(Job).values([{"photo_id": pid, "extractor": ex, "status": "pending", "attempts": 0, "reserved_for": w.id} for pid in ids])
        stmt = stmt.on_conflict_do_update(
            constraint="jobs_photo_extractor_uq",
            set_={"status": "pending", "attempts": 0, "error": None, "locked_by": None, "reserved_for": w.id,
                  "finished_at": None, "created_at": text("now()")},
            where=(Job.status != "running"),
        )
        out[ex] = len(session.execute(stmt.returning(Job.id)).all())
    return out


def unassign(session: Session, w: Worker) -> int:
    """Rend a la file commune les jobs reserves a ce worker et pas encore commences."""
    r = session.execute(text("UPDATE jobs SET reserved_for = NULL WHERE reserved_for = :id AND status = 'pending'"), {"id": w.id})
    return r.rowcount or 0


def release_running(session: Session, w: Worker, instance: str | None = None) -> int:
    """Arret propre : les jobs en cours de ce worker (ou d'une de ses instances) repassent en pending, toujours reserves."""
    sql = "UPDATE jobs SET status = 'pending', locked_by = NULL WHERE status = 'running' AND reserved_for = :id"
    params: dict[str, Any] = {"id": w.id}
    if instance:
        sql += " AND locked_by = :lb"
        params["lb"] = f"{w.name}@{instance}"
    return session.execute(text(sql), params).rowcount or 0


def describe(session: Session, w: Worker) -> dict[str, Any]:
    info = merged(w)
    rows = session.execute(
        text("SELECT extractor, status, count(*) AS n FROM jobs WHERE reserved_for = :id GROUP BY 1, 2"), {"id": w.id}
    ).all()
    counts: dict[str, dict[str, int]] = {}
    for r in rows:
        counts.setdefault(r.extractor, {})[r.status] = int(r.n)
    done_24h = session.scalar(
        text("SELECT count(*) FROM jobs WHERE reserved_for = :id AND status = 'done' AND finished_at > now() - interval '24 hours'"),
        {"id": w.id},
    )
    back = {ex: backlog(session, ex, info["vlm_rank"], int(info["versions"].get(ex, 1))) for ex in info["extractors"]}
    last_task = session.scalars(select(Task).where(Task.reserved_for == w.id).order_by(Task.created_at.desc()).limit(1)).first()
    return {
        "id": str(w.id), "name": w.name, "created_at": w.created_at.isoformat() if w.created_at else None,
        "revoked": w.revoked_at is not None, **info, "jobs": counts, "done_24h": int(done_24h or 0), "backlog": back,
        "last_task": _task_out(last_task) if last_task else None,
    }


# --------------------------------------------------------------------------- taches de maintenance

TASK_KINDS = ("faces_cluster",)


def _task_out(t: Task) -> dict[str, Any]:
    return {
        "id": str(t.id), "kind": t.kind, "status": t.status, "result": t.result, "error": t.error,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
    }


def request_task(session: Session, w: Worker, kind: str, params: dict | None = None) -> Task:
    """Cree une tache reservee a ce worker. Une seule tache du meme genre en attente ou en cours a la fois."""
    if kind not in TASK_KINDS:
        raise ValueError(f"tache inconnue: {kind}")
    if kind not in merged(w)["tasks"]:
        raise ValueError(f"le worker {w.name} ne sait pas faire {kind}")
    existing = session.scalars(select(Task).where(Task.kind == kind, Task.status.in_(("pending", "running")))).first()
    if existing is not None:
        if existing.status == "pending":
            existing.reserved_for = w.id  # pas commencee : c'est le worker demande qui la fera
        return existing
    t = Task(kind=kind, params=params or {}, reserved_for=w.id)
    session.add(t)
    session.flush()
    return t


def claim_task(session: Session, w: Worker, instance: str, kinds: list[str]) -> Task | None:
    if not kinds:
        return None
    row = session.execute(
        text(
            "WITH c AS (SELECT id FROM tasks WHERE status = 'pending' AND reserved_for = :id AND kind = ANY(:kinds) "
            "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "UPDATE tasks t SET status = 'running', locked_by = :lb, started_at = now() FROM c WHERE t.id = c.id RETURNING t.id"
        ),
        {"id": w.id, "kinds": list(kinds), "lb": f"{w.name}@{instance}"},
    ).first()
    session.commit()
    return session.get(Task, row.id) if row else None


def owned_task(session: Session, w: Worker, task_id: uuid.UUID) -> Task | None:
    t = session.get(Task, task_id)
    if t is None or t.reserved_for != w.id or t.status != "running":
        return None
    return t


def finish_task(session: Session, t: Task, result: dict | None, error: str | None) -> None:
    t.status = "failed" if error else "done"
    t.error = error
    t.result = result
    t.locked_by = None
    t.finished_at = datetime.now(timezone.utc)


def reset_stale_tasks(session: Session) -> None:
    """Meme regle que les jobs : une tache dont le worker ne bat plus depuis WORKER_DEAD_MINUTES repart."""
    session.execute(
        text(
            "UPDATE tasks SET status = 'pending', locked_by = NULL WHERE status = 'running' "
            "AND started_at < now() - make_interval(mins => :m)"
        ),
        {"m": settings.worker_dead_minutes},
    )


def server_config() -> dict[str, Any]:
    """Ce qu'un worker distant doit aligner sur le serveur."""
    return {"embedding_model": settings.embedding_model, "embedding_dim": settings.embedding_dim, "vlm_max_side": settings.vlm_max_side}
