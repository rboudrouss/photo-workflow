"""Connexion a la base et initialisation du schema."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """Dependance FastAPI."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


EXTRA_SQL = [
    # Colonnes ajoutees apres la v0 (create_all ne modifie pas les tables existantes).
    "ALTER TABLE photos ADD COLUMN IF NOT EXISTS nudity_level VARCHAR(16)",
    "ALTER TABLE photos ADD COLUMN IF NOT EXISTS nudity_source TEXT",
    "CREATE INDEX IF NOT EXISTS photos_nudity_idx ON photos (nudity_level)",
    "ALTER TABLE photos ADD COLUMN IF NOT EXISTS series_id INTEGER REFERENCES series(id) ON DELETE SET NULL",
    "CREATE INDEX IF NOT EXISTS photos_series_idx ON photos (series_id)",
    # Recherche vectorielle (cosine) sur les embeddings image et visages.
    "CREATE INDEX IF NOT EXISTS image_embeddings_hnsw ON image_embeddings "
    "USING hnsw (embedding vector_cosine_ops)",
    "CREATE INDEX IF NOT EXISTS faces_embedding_hnsw ON faces USING hnsw (embedding vector_cosine_ops)",
    # Recherche plein texte en francais sur les legendes.
    "CREATE INDEX IF NOT EXISTS captions_tsv_idx ON captions USING gin (tsv)",
    "CREATE INDEX IF NOT EXISTS jobs_claim_idx ON jobs (extractor, status, created_at)",
    "CREATE INDEX IF NOT EXISTS photos_phash_idx ON photos (phash)",
]


def init_db() -> None:
    from . import models  # noqa: F401  (enregistre les tables)

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    models.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in EXTRA_SQL:
            conn.execute(text(stmt))
