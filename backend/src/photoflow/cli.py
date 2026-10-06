"""Ligne de commande `photoflow`."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from rich.table import Table

from .config import settings

app = typer.Typer(help="Pipeline d'extraction d'informations sur des photos anciennes.", no_args_is_help=True)
db_app = typer.Typer(help="Base de donnees.")
jobs_app = typer.Typer(help="File de jobs.")
faces_app = typer.Typer(help="Visages.")
vlm_app = typer.Typer(help="Modele vision-langage.")
batch_app = typer.Typer(help="API Batches Anthropic.")
series_app = typer.Typer(help="Series (meme pellicule / meme seance).")
workers_app = typer.Typer(help="Workers distants (jetons).")
app.add_typer(workers_app, name="workers")
app.add_typer(db_app, name="db")
app.add_typer(jobs_app, name="jobs")
app.add_typer(faces_app, name="faces")
app.add_typer(vlm_app, name="vlm")
app.add_typer(batch_app, name="claude-batch")
app.add_typer(series_app, name="series")


@app.callback()
def _setup(verbose: bool = typer.Option(False, "--verbose", "-v")):
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


@db_app.command("init")
def db_init():
    """Cree l'extension pgvector, les tables et les index."""
    from .db import init_db

    init_db()
    rprint("[green]base initialisee[/green]")


@app.command()
def ingest(
    directory: Path = typer.Argument(..., help="Dossier a parcourir (sous PHOTOS_ROOT)."),
    extractors: Optional[str] = typer.Option(None, help="Extracteurs a mettre en file, separes par des virgules. Defaut: config."),
    limit: Optional[int] = typer.Option(None, help="Nombre max de nouvelles photos."),
):
    """Enregistre les photos d'un dossier, genere les miniatures, met les jobs en file."""
    from .ingest import ingest_dir

    exs = extractors.split(",") if extractors else None
    counts = ingest_dir(directory, exs, limit)
    rprint(counts)


@app.command()
def watch(
    interval: float = typer.Option(30.0, help="Secondes entre deux passages."),
    once: bool = typer.Option(False, help="Un seul passage."),
):
    """Surveille PHOTOS_ROOT et ingere tout nouveau fichier image, comme un upload depuis l'interface."""
    from .watch import watch as run

    run(interval, once)


@app.command()
def worker(
    extractors: str = typer.Option(",".join(["physical", "embedding", "faces", "nudity", "vlm"]), help="Extracteurs geres par ce worker."),
    once: bool = typer.Option(False, help="S'arreter quand la file est vide."),
    server: Optional[str] = typer.Option(None, help="URL du serveur photoflow (mode distant, sinon SERVER_URL)."),
    token: Optional[str] = typer.Option(None, help="Jeton du worker (sinon WORKER_TOKEN)."),
):
    """Lance un worker. Avec --server/SERVER_URL : mode distant, sans acces a la base."""
    from .worker import main

    main([e.strip() for e in extractors.split(",") if e.strip()], once=once, server_url=server, token=token)


@workers_app.command("create")
def workers_create(name: str = typer.Argument(..., help="Nom du worker, ex. pc-remi.")):
    """Cree un worker distant et affiche son jeton (une seule fois : il n'est stocke que hache)."""
    from .db import session_scope
    from .workers import create

    with session_scope() as s:
        w, token = create(s, name)
    rprint(f"worker [bold]{name}[/bold] cree. A mettre dans le .env de la machine du worker :")
    rprint(f"  SERVER_URL=https://photoflow.example.com\n  WORKER_TOKEN={token}")


@workers_app.command("list")
def workers_list():
    from sqlalchemy import select

    from .db import session_scope
    from .models import Worker
    from .workers import describe

    t = Table("nom", "en ligne", "extracteurs", "VLM", "rang", "jobs", "vu")
    with session_scope() as s:
        for w in s.scalars(select(Worker).order_by(Worker.name)).all():
            d = describe(s, w)
            jobs = "; ".join(f"{ex}: " + ", ".join(f"{n} {st}" for st, n in c.items()) for ex, c in d["jobs"].items()) or "-"
            t.add_row(
                w.name + (" (revoque)" if d["revoked"] else ""), "oui" if d["online"] else "non", ",".join(d["extractors"]) or "-",
                d["models"].get("vlm") or "-", str(d["vlm_rank"] or "-"), jobs, (d["last_seen"] or "-")[:16],
            )
    rprint(t)


@workers_app.command("revoke")
def workers_revoke(name: str):
    """Invalide le jeton d'un worker et rend ses jobs en attente a la file commune."""
    from .db import session_scope
    from .workers import revoke

    with session_scope() as s:
        ok = revoke(s, name)
    rprint("[green]revoque[/green]" if ok else "[red]worker inconnu[/red]")


@jobs_app.command("stats")
def jobs_stats():
    from .db import session_scope
    from .queue import stats

    with session_scope() as s:
        rows = stats(s)
    t = Table("extracteur", "statut", "nombre")
    for r in rows:
        t.add_row(r["extractor"], r["status"], str(r["count"]))
    rprint(t)


@jobs_app.command("enqueue")
def jobs_enqueue(
    extractors: str = typer.Argument(..., help="Extracteurs, separes par des virgules."),
    force: bool = typer.Option(False, help="Relancer aussi les jobs deja faits ou echoues."),
    limit: Optional[int] = typer.Option(None),
):
    """Met en file un extracteur pour toutes les photos (ex. apres changement de modele)."""
    from sqlalchemy import select

    from .db import session_scope
    from .models import Photo
    from .queue import enqueue

    with session_scope() as s:
        q = select(Photo.id).where(Photo.status == "ready")
        if limit:
            q = q.limit(limit)
        ids = s.scalars(q).all()
        n = enqueue(s, ids, [e.strip() for e in extractors.split(",")], force=force)
    rprint(f"{n} job(s) crees/remis en file")


@jobs_app.command("retry-failed")
def jobs_retry_failed():
    from sqlalchemy import text

    from .db import session_scope

    with session_scope() as s:
        r = s.execute(text("UPDATE jobs SET status='pending', attempts=0, error=NULL WHERE status='failed'"))
    rprint(f"{r.rowcount} job(s) remis en file")


@faces_app.command("cluster")
def faces_cluster(
    min_cluster_size: int = typer.Option(3, help="Nombre min de visages pour former une personne."),
    min_samples: Optional[int] = typer.Option(None),
    epsilon: float = typer.Option(0.0, help="Fusionne les clusters plus proches que cette distance."),
):
    """Regroupe les visages par personne (HDBSCAN)."""
    from .faces_cluster import cluster

    rprint(cluster(min_cluster_size, min_samples, epsilon))


@series_app.command("build")
def series_build(
    candidate_sim: float = typer.Option(0.75, help="Similarite d'embedding minimale pour considerer une paire."),
    edge_threshold: float = typer.Option(0.85, help="Score minimal (similarite + bonus) pour relier deux photos."),
    k: int = typer.Option(10, help="Voisines examinees par photo."),
    max_size: int = typer.Option(150, help="Au-dela, la serie est rescindee avec un seuil plus strict."),
    min_size: int = typer.Option(2),
):
    """Reconstruit les series a partir des embeddings, visages, signal physique et pHash."""
    from .series import build

    rprint(build(candidate_sim, edge_threshold, k, max_size, min_size))


@vlm_app.command("run")
def vlm_run(
    backend: str = typer.Option(settings.vlm_backend, help="llama | anthropic"),
    limit: int = typer.Option(10),
    model: Optional[str] = typer.Option(None, help="Nom du modele (anthropic) ; ignore pour llama."),
    only_missing: bool = typer.Option(True, help="Ignorer les photos deja analysees par cette source."),
):
    """Analyse directement N photos avec un backend donne (utile pour comparer la qualite)."""
    from sqlalchemy import text

    from .db import session_scope
    from .extractors.base import PhotoRef
    from .extractors.vlm import VLMExtractor, get_backend
    from .images import derived_paths, original_path
    from .models import Photo

    if backend == "anthropic" and model:
        from .extractors.vlm_backends.anthropic_backend import AnthropicBackend

        ex = VLMExtractor(AnthropicBackend(model))
    else:
        ex = VLMExtractor(get_backend(backend))
    typer.echo(f"source: {ex.backend.source}")

    with session_scope() as s:
        q = "SELECT p.* FROM photos p WHERE p.status='ready'"
        if only_missing:
            q += " AND NOT EXISTS (SELECT 1 FROM captions c WHERE c.photo_id=p.id AND c.source=:src)"
        q += " ORDER BY p.ingested_at LIMIT :n"
        rows = s.execute(text(q), {"src": ex.backend.source, "n": limit}).mappings().all()
        photos = [s.get(Photo, r["id"]) for r in rows]
        for p in photos:
            d = derived_paths(p.id)
            ref = PhotoRef(id=p.id, original=original_path(p.rel_path), web=d["web"], thumb=d["thumb"], width=p.width, height=p.height)
            try:
                res = ex.run([ref])[0]
                ex.persist(s, ref, res)
                s.commit()
                rprint(f"[green]{p.filename}[/green]: {res['titre']}")
            except Exception as e:
                s.rollback()
                rprint(f"[red]{p.filename}[/red]: {e}")


@vlm_app.command("schema")
def vlm_schema():
    """Affiche le JSON schema impose au modele."""
    from .extractors.vlm import json_schema

    print(json.dumps(json_schema(), ensure_ascii=False, indent=2))


@batch_app.command("submit")
def batch_submit(limit: Optional[int] = typer.Option(None), model: Optional[str] = typer.Option(None)):
    """Envoie les photos sans legende Claude a l'API Batches (moitie prix)."""
    from .claude_batch import submit

    ids = submit(limit=limit, model=model)
    rprint(ids or "rien a envoyer")


@batch_app.command("status")
def batch_status():
    from .claude_batch import status

    for b in status():
        rprint(b)


@batch_app.command("collect")
def batch_collect(batch_id: str):
    """Recupere les resultats d'un lot termine et les enregistre en legendes."""
    from .claude_batch import collect

    rprint(collect(batch_id))


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000, reload: bool = False):
    """Lance l'API HTTP."""
    import uvicorn

    uvicorn.run("photoflow.api.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
