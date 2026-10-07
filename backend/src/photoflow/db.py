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
    # Workers distants et reservation de jobs.
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS reserved_for UUID REFERENCES workers(id) ON DELETE SET NULL",
    "CREATE INDEX IF NOT EXISTS jobs_reserved_idx ON jobs (reserved_for, extractor, status)",
    "ALTER TABLE captions ADD COLUMN IF NOT EXISTS model_rank REAL",
    # Liens publics des images (export Delcampe).
    "ALTER TABLE photos ADD COLUMN IF NOT EXISTS public_token TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS photos_public_token_uq ON photos (public_token)",
    # Statut de publication Delcampe.
    "ALTER TABLE photos ADD COLUMN IF NOT EXISTS delcampe_status VARCHAR(16)",
    "ALTER TABLE photos ADD COLUMN IF NOT EXISTS delcampe_status_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS photos_delcampe_status_idx ON photos (delcampe_status)",
]


def init_db() -> None:
    from . import models  # noqa: F401  (enregistre les tables)

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    models.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in EXTRA_SQL:
            conn.execute(text(stmt))
    backfill_model_rank()


def backfill_model_rank() -> None:
    """Renseigne captions.model_rank pour les legendes creees avant l'ajout de la colonne."""
    from .modelrank import rank_of_source

    with engine.begin() as conn:
        sources = conn.execute(text("SELECT DISTINCT source FROM captions WHERE model_rank IS NULL")).scalars().all()
        for src in sources:
            conn.execute(
                text("UPDATE captions SET model_rank = :r WHERE source = :s AND model_rank IS NULL"),
                {"r": rank_of_source(src), "s": src},
            )
