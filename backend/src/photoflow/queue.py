"""File de jobs adossee a Postgres (SELECT ... FOR UPDATE SKIP LOCKED).

Suffisant pour quelques workers et quelques centaines de milliers de jobs.
"""

from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

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


CLAIM_SQL = text(
    """
    WITH c AS (
        SELECT id FROM jobs
        WHERE status = 'pending' AND extractor = :extractor
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


def claim(session: Session, extractor: str, worker: str, n: int) -> list[tuple[uuid.UUID, uuid.UUID, int]]:
    rows = session.execute(CLAIM_SQL, {"extractor": extractor, "worker": worker, "n": n}).all()
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


def reset_stale(session: Session, older_than_minutes: int = 60) -> int:
    """Remet en pending les jobs 'running' orphelins (worker tue)."""
    r = session.execute(
        text(
            "UPDATE jobs SET status='pending', locked_by=NULL WHERE status='running' "
            "AND started_at < now() - make_interval(mins => :m)"
        ),
        {"m": older_than_minutes},
    )
    return r.rowcount or 0


def stats(session: Session) -> list[dict]:
    rows = session.execute(
        text("SELECT extractor, status, count(*) AS n FROM jobs GROUP BY 1, 2 ORDER BY 1, 2")
    ).all()
    return [{"extractor": r.extractor, "status": r.status, "count": r.n} for r in rows]
