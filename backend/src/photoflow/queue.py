"""File de jobs adossee a Postgres (SELECT ... FOR UPDATE SKIP LOCKED).

Suffisant pour quelques workers et quelques centaines de milliers de jobs.
"""

from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .config import settings
from .models import Job


def enqueue(session: Session, photo_ids: Iterable[uuid.UUID], extractors: Iterable[str], force: bool = False) -> int:
    """Cree les jobs manquants. Avec force=True, remet aussi en pending les jobs deja faits/echoues."""
    rows = [{"photo_id": pid, "extractor": ex} for pid in photo_ids for ex in extractors]
    if not rows:
        return 0
    stmt = insert(Job).values(rows)
    if force:
        stmt = stmt.on_conflict_do_update(
            constraint="jobs_photo_extractor_uq",
            set_={"status": "pending", "attempts": 0, "error": None, "locked_by": None, "finished_at": None},
        )
    else:
        stmt = stmt.on_conflict_do_nothing(constraint="jobs_photo_extractor_uq")
    result = session.execute(stmt.returning(Job.id))
    return len(result.all())


def _claim_sql(reserved: bool) -> text:
    # File commune (reserved_for IS NULL) pour les workers connectes a la base ; jobs reserves pour un
    # worker distant identifie (reserved_for = :reserved_for).
    cond = "reserved_for = :reserved_for" if reserved else "reserved_for IS NULL"
    return text(
        f"""
        WITH c AS (
            SELECT id FROM jobs
            WHERE status = 'pending' AND extractor = :extractor AND {cond}
            ORDER BY created_at
            LIMIT :n
            FOR UPDATE SKIP LOCKED
        )
        UPDATE jobs j
        SET status = 'running', locked_by = :worker, started_at = now(), attempts = attempts + 1
        FROM c WHERE j.id = c.id
        RETURNING j.id, j.photo_id, j.attempts
        """
    )


CLAIM_POOL_SQL = _claim_sql(reserved=False)
CLAIM_RESERVED_SQL = _claim_sql(reserved=True)


def claim(
    session: Session, extractor: str, worker: str, n: int, reserved_for: uuid.UUID | None = None
) -> list[tuple[uuid.UUID, uuid.UUID, int]]:
    """Reclame jusqu'a n jobs. Sans reserved_for : file commune. Avec : seulement les jobs reserves a ce worker."""
    params = {"extractor": extractor, "worker": worker, "n": n}
    if reserved_for is None:
        rows = session.execute(CLAIM_POOL_SQL, params).all()
    else:
        rows = session.execute(CLAIM_RESERVED_SQL, {**params, "reserved_for": reserved_for}).all()
    session.commit()
    return [(r.id, r.photo_id, r.attempts) for r in rows]


def finish(session: Session, job_id: uuid.UUID, error: str | None, attempts: int, max_attempts: int) -> None:
    if error is None:
        status = "done"
    elif attempts >= max_attempts:
        status = "failed"
    else:
        status = "pending"
    session.execute(
        text("UPDATE jobs SET status=:s, error=:e, finished_at=now(), locked_by=NULL WHERE id=:id"),
        {"s": status, "e": error, "id": job_id},
    )


def reset_stale(session: Session, older_than_minutes: int = 60, remote_minutes: int | None = None, absent_worker_hours: int | None = None) -> int:
    """Remet en pending les jobs 'running' orphelins (worker tue).

    Un worker distant rafraichit `started_at` de ses jobs en cours a chaque battement (30 s) : 5 min sans
    battement = worker mort, le job repart (toujours reserve au meme worker, un autre ne le prendra pas).
    Un worker connecte a la base ne rafraichit rien, d'ou un delai plus long pour ses jobs (VLM lent).
    Les jobs reserves a un worker distant absent depuis longtemps (ou revoque) retournent dans la file commune.
    """
    remote_minutes = settings.worker_dead_minutes if remote_minutes is None else remote_minutes
    absent_worker_hours = settings.worker_absent_hours if absent_worker_hours is None else absent_worker_hours
    r = session.execute(
        text(
            "UPDATE jobs SET status='pending', locked_by=NULL WHERE status='running' AND ("
            "(reserved_for IS NULL AND started_at < now() - make_interval(mins => :m)) OR "
            "(reserved_for IS NOT NULL AND started_at < now() - make_interval(mins => :r)))"
        ),
        {"m": older_than_minutes, "r": remote_minutes},
    )
    session.execute(
        text(
            "UPDATE jobs SET reserved_for = NULL WHERE status = 'pending' AND reserved_for IN ("
            "SELECT id FROM workers WHERE revoked_at IS NOT NULL "
            "OR last_seen_at IS NULL OR last_seen_at < now() - make_interval(hours => :h))"
        ),
        {"h": absent_worker_hours},
    )
    return r.rowcount or 0


def stats(session: Session) -> list[dict]:
    rows = session.execute(
        text("SELECT extractor, status, count(*) AS n FROM jobs GROUP BY 1, 2 ORDER BY 1, 2")
    ).all()
    return [{"extractor": r.extractor, "status": r.status, "count": r.n} for r in rows]
