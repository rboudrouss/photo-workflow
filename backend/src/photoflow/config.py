"""Configuration centralisee, lue depuis l'environnement ou un fichier .env."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True)

    database_url: str = "postgresql+psycopg://photoflow:photoflow@localhost:5432/photoflow"

    # Dossier des originaux (lecture seule) et dossier des derives (miniatures, exports, batches).
    photos_root: Path = Path("/photos")
    data_dir: Path = Path("/data")

    # Tailles des derives generes a l'ingestion.
    thumb_size: int = 320
    web_size: int = 1600

    # Extracteurs mis en file automatiquement a l'ingestion.
    default_extractors: list[str] = Field(default_factory=lambda: ["physical", "embedding", "faces", "nudity", "vlm"])

    # Embeddings image/texte (SigLIP 2). La dimension doit correspondre au modele.
    embedding_model: str = "google/siglip2-base-patch16-256"
    embedding_dim: int = 768
    embedding_batch_size: int = 16

    # Visages (InsightFace). buffalo_l = detection RetinaFace + ArcFace 512d.
    face_model: str = "buffalo_l"
    face_det_size: int = 640
    face_min_score: float = 0.5
    face_min_px: int = 24

    # Nudite (NudeNet). 320 = modele embarque, rapide ; 640 = plus precis, telecharge au premier usage.
    nudity_resolution: int = 320

    # Device pour torch / onnxruntime : cpu, cuda, mps.
    device: str = "cpu"

    # VLM : "llama" (serveur OpenAI-compatible, llama.cpp / vLLM / MLX) ou "anthropic".
    vlm_backend: str = "llama"
    vlm_max_side: int = 1024
    vlm_timeout: float = 600.0

    llm_base_url: str = "http://localhost:8080/v1"
    llm_model: str = "local"
    llm_api_key: str = "none"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "low"
    anthropic_fallbacks: bool = True

    # Worker
    worker_poll_seconds: float = 3.0
    worker_max_attempts: int = 3

    # Worker distant (machine perso) : s'il a SERVER_URL + WORKER_TOKEN, il ne touche pas a la base et tire ses
    # jobs par HTTP (workers.py). Jeton cree sur le serveur avec `photoflow workers create <nom>`.
    server_url: str | None = None
    worker_token: str | None = None  # facultatif : sans jeton, le worker demande a etre approuve sur la page Workers
    worker_name: str | None = None   # nom propose a l'approbation (defaut : hostname)
    worker_heartbeat_seconds: float = 30.0
    # Cote serveur : apres WORKER_DEAD_MINUTES sans battement, les jobs en cours d'un worker distant repassent en
    # attente (toujours reserves a lui) ; apres WORKER_ABSENT_HOURS, ses photos reservees retournent a la file commune.
    worker_dead_minutes: int = 5
    worker_absent_hours: int = 6
    # Rang declare du VLM local (modelrank.py). Deduit du nom du modele si absent.
    vlm_rank: float | None = None

    # API : attente max d'un worker pour encoder une requete de recherche semantique (serveur sans modeles).
    query_timeout_seconds: float = 8.0
    # Toujours faire encoder les requetes par un worker, meme si torch est installe ici (machine faible).
    query_via_workers: bool = False
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"])

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def batches_dir(self) -> Path:
        return self.data_dir / "batches"


settings = Settings()
