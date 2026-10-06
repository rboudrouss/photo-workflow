"""Schema de la base. Une ligne par photo, un extracteur = ses propres lignes.

Voir docs/extracted-data.md pour la signification de chaque champ.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import settings


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSONB, list: JSONB}


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Photo(Base):
    __tablename__ = "photos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    rel_path: Mapped[str] = mapped_column(Text, nullable=False)  # relatif a PHOTOS_ROOT
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(BigInteger)
    format: Mapped[str | None] = mapped_column(String(16))
    phash: Mapped[int | None] = mapped_column(BigInteger)  # hash perceptuel 64 bits (signe)
    exif: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="new")  # new | ready | hidden
    # Nudite : aucune | suggestive | partielle | integrale (voir nudity.py). NULL = pas encore analyse.
    nudity_level: Mapped[str | None] = mapped_column(String(16), index=True)
    nudity_source: Mapped[str | None] = mapped_column(Text)  # "human", "nudity:<modele>", "vlm:<backend>:<modele>"
    series_id: Mapped[int | None] = mapped_column(ForeignKey("series.id", ondelete="SET NULL"), index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    captions: Mapped[list["Caption"]] = relationship(back_populates="photo", cascade="all, delete-orphan")
    extractions: Mapped[list["Extraction"]] = relationship(back_populates="photo", cascade="all, delete-orphan")
    faces: Mapped[list["Face"]] = relationship(back_populates="photo", cascade="all, delete-orphan")


class Caption(Base):
    """Titre + description + analyse structuree. Une ligne par source (modele ou humain)."""

    __tablename__ = "captions"
    __table_args__ = (UniqueConstraint("photo_id", "source", name="captions_photo_source_uq"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    photo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("photos.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # "human" ou "vlm:<backend>:<model>"
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSONB)  # PhotoAnalysis complet (voir extractors/vlm.py)
    # Force estimee du modele qui a produit la legende (modelrank.py). Sert a decider si un worker avec un
    # VLM plus fort doit re-analyser la photo. NULL pour 'human'.
    model_rank: Mapped[float | None] = mapped_column(Float)
    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('french', coalesce(title,'') || ' ' || coalesce(description,'') "
            "|| ' ' || coalesce(data->>'tags',''))",
            persisted=True,
        ),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    photo: Mapped[Photo] = relationship(back_populates="captions")


class Extraction(Base):
    """Resultat brut d'un extracteur pour une photo (JSON), versionne."""

    __tablename__ = "extractions"
    __table_args__ = (UniqueConstraint("photo_id", "extractor", name="extractions_photo_extractor_uq"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    photo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("photos.id", ondelete="CASCADE"), index=True)
    extractor: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    photo: Mapped[Photo] = relationship(back_populates="extractions")


class ImageEmbedding(Base):
    __tablename__ = "image_embeddings"

    photo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(settings.embedding_dim), nullable=False)


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Face(Base):
    __tablename__ = "faces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    photo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("photos.id", ondelete="CASCADE"), index=True)
    # bbox en pixels sur l'image ORIGINALE : [x1, y1, x2, y2]
    bbox: Mapped[list] = mapped_column(JSONB, nullable=False)
    det_score: Mapped[float] = mapped_column(Float)
    age: Mapped[int | None] = mapped_column(Integer)
    gender: Mapped[str | None] = mapped_column(String(1))
    embedding = mapped_column(Vector(512), nullable=False)
    cluster_id: Mapped[int | None] = mapped_column(Integer, index=True)  # -1 = bruit, NULL = pas clusterise
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("persons.id", ondelete="SET NULL"), index=True)

    photo: Mapped[Photo] = relationship(back_populates="faces")
    person: Mapped[Person | None] = relationship()


class Series(Base):
    """Groupe de photos de la meme pellicule ou seance (series.py). Reconstruit par `photoflow series build`."""

    __tablename__ = "series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SeriesEdge(Base):
    """Arete retenue entre deux photos d'une serie, avec les raisons (similarite, visages, physique, phash)."""

    __tablename__ = "series_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True)
    photo_a: Mapped[uuid.UUID] = mapped_column(ForeignKey("photos.id", ondelete="CASCADE"), index=True)
    photo_b: Mapped[uuid.UUID] = mapped_column(ForeignKey("photos.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float)
    reasons: Mapped[dict] = mapped_column(JSONB)


class Worker(Base):
    """Worker distant (machine perso) qui tire ses jobs par HTTP avec un jeton. Voir workers.py."""

    __tablename__ = "workers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # sha256 du jeton
    # Un jeton peut etre partage par plusieurs processus (worker-ml + worker-vlm) : un etat par instance,
    # {instance: {extractors, models, versions, vlm_rank, last_seen}}. Fusionne par workers.describe().
    instances: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PairRequest(Base):
    """Worker qui demande a rejoindre le serveur (workers.py). Approuve en un clic sur la page Workers."""

    __tablename__ = "pair_requests"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)  # genere par le worker, le prouve porteur
    hostname: Mapped[str | None] = mapped_column(Text)
    extractors: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    worker_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workers.id", ondelete="CASCADE"))
    token: Mapped[str | None] = mapped_column(Text)  # jeton en clair, le temps que le worker vienne le chercher


class Job(Base):
    """File de travail. Un job = (photo, extracteur). Reclame avec SKIP LOCKED.

    `reserved_for` : NULL = file commune (workers connectes a la base) ; sinon le job n'est servi qu'au
    worker distant designe, qui l'a obtenu via le bouton « analyser N photos ».
    """

    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("photo_id", "extractor", name="jobs_photo_extractor_uq"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    photo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("photos.id", ondelete="CASCADE"), index=True)
    extractor: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | running | done | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[str | None] = mapped_column(Text)
    reserved_for: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workers.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClaudeBatch(Base):
    """Suivi des lots envoyes a l'API Batches d'Anthropic."""

    __tablename__ = "claude_batches"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # id Anthropic (msgbatch_...)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="submitted")  # submitted | collected
    count: Mapped[int] = mapped_column(Integer)
    mapping: Mapped[dict] = mapped_column(JSONB, nullable=False)  # custom_id -> photo_id
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
