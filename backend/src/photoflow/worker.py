"""Worker : reclame des jobs, lance les extracteurs, persiste ou renvoie les resultats.

Deux sources de jobs, meme boucle :
- LocalSource  : connecte a Postgres (compose dev / grosse machine), SKIP LOCKED sur la file commune.
- RemoteSource : machine perso sans acces a la base. Uniquement des requetes sortantes vers l'API avec un
  jeton ; les images sont telechargees dans un dossier temporaire, les resultats renvoyes en JSON.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from sqlalchemy import select

from . import extractors, pairing, queue
from .config import settings
from .extractors.base import PhotoRef
from .images import derived_paths, original_path
from .models import Photo

log = logging.getLogger(__name__)


class Unauthorized(Exception):
    """Le serveur refuse le jeton : worker revoque, il faut se faire approuver a nouveau."""


@dataclass
class JobItem:
    job_id: uuid.UUID
    photo_id: uuid.UUID
    attempts: int
    ref: PhotoRef


class JobSource(Protocol):
    def start(self, loaded: dict[str, extractors.Extractor]) -> None: ...
    def claim(self, name: str, ex: extractors.Extractor) -> list[JobItem]: ...
    def finish(self, name: str, ex: extractors.Extractor, item: JobItem, result: dict | None, error: str | None) -> None: ...
    def stop(self) -> None: ...


# --------------------------------------------------------------------------- base locale

class LocalSource:
    def __init__(self, worker_id: str) -> None:
        self.worker_id = worker_id

    def start(self, loaded) -> None:
        from .db import session_scope

        with session_scope() as session:
            n = queue.reset_stale(session)
            if n:
                log.info("%d job(s) orphelin(s) remis en file", n)

    def claim(self, name, ex) -> list[JobItem]:
        from .db import session_scope

        with session_scope() as session:
            claimed = queue.claim(session, name, self.worker_id, ex.batch_size)
            if not claimed:
                return []
            photos = session.scalars(select(Photo).where(Photo.id.in_([pid for _, pid, _ in claimed]))).all()
            by_id = {p.id: p for p in photos}
        items = []
        for jid, pid, att in claimed:
            p = by_id.get(pid)
            if p is None:
                continue
            d = derived_paths(p.id)
            items.append(JobItem(jid, pid, att, PhotoRef(id=p.id, original=original_path(p.rel_path), web=d["web"], thumb=d["thumb"], width=p.width, height=p.height)))
        return items

    def finish(self, name, ex, item, result, error) -> None:
        from .db import session_scope

        with session_scope() as session:
            if error is None:
                try:
                    ex.persist(session, item.ref, result)
                except Exception as e:
                    session.rollback()
                    error = f"persist: {type(e).__name__}: {e}"[:2000]
                    log.exception("echec persist %s %s", name, item.ref.id)
            queue.finish(session, item.job_id, error, item.attempts, settings.worker_max_attempts)

    def stop(self) -> None:
        pass


# --------------------------------------------------------------------------- serveur distant

def task_kinds() -> list[str]:
    """Taches de maintenance que cette machine sait faire (faces_cluster si scikit-learn est installe)."""
    import importlib.util

    return ["faces_cluster"] if importlib.util.find_spec("sklearn") is not None else []


def run_task(kind: str, params: dict, data: bytes) -> dict:
    """Calcul pur d'une tache, a partir des donnees envoyees par le serveur."""
    import io

    import numpy as np

    if kind == "faces_cluster":
        from .faces_cluster import cluster_labels

        npz = np.load(io.BytesIO(data), allow_pickle=False)
        labels = cluster_labels(
            npz["X"], int(params.get("min_cluster_size", 3)), params.get("min_samples"), float(params.get("epsilon", 0.0))
        )
        return {"ids": [str(i) for i in npz["ids"]], "labels": [int(x) for x in labels]}
    raise ValueError(f"tache inconnue: {kind}")


class RemoteSource:
    def __init__(self, server_url: str, token: str, instance: str) -> None:
        self.base = server_url.rstrip("/")
        self.instance = instance
        self.http = httpx.Client(base_url=self.base, headers={"Authorization": f"Bearer {token}"}, timeout=120.0)
        self.tmp = Path(tempfile.mkdtemp(prefix="photoflow-worker-"))
        self._info: dict[str, Any] = {"instance": instance, "hostname": socket.gethostname(), "extractors": []}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.unauthorized = False

    def connect(self, names: list[str]) -> dict[str, Any]:
        """Premier battement : authentifie, et recupere la config du serveur a appliquer AVANT de charger les modeles."""
        self._info["extractors"] = names
        r = self.http.post("/api/worker/heartbeat", json=self._info)
        if r.status_code == 401:
            raise Unauthorized
        r.raise_for_status()
        cfg = r.json()["config"]
        if "embedding" in names and cfg["embedding_model"] != settings.embedding_model:
            log.info("modele d'embedding aligne sur le serveur : %s (%d dims)", cfg["embedding_model"], cfg["embedding_dim"])
            settings.embedding_model = cfg["embedding_model"]
            settings.embedding_dim = int(cfg["embedding_dim"])
        if cfg.get("vlm_max_side"):
            settings.vlm_max_side = int(cfg["vlm_max_side"])
        return cfg

    def start(self, loaded) -> None:
        from .modelrank import rank_of_model

        self._info.update(
            extractors=list(loaded),
            tasks=task_kinds(),
            models={n: ex.model_name for n, ex in loaded.items()},
            versions={n: ex.version for n, ex in loaded.items()},
            vlm_rank=(settings.vlm_rank if settings.vlm_rank is not None else rank_of_model(loaded["vlm"].model_name)) if "vlm" in loaded else None,
        )
        if "embedding" in loaded:
            # Le serveur n'a pas le modele : on encode pour lui les requetes de recherche semantique (long-poll).
            # Demarre AVANT le premier battement : des que le serveur nous voit avec "embedding", on ecoute deja.
            threading.Thread(target=self._queries_loop, args=(loaded["embedding"],), name="queries", daemon=True).start()
        self._beat()
        self._thread = threading.Thread(target=self._loop, name="heartbeat", daemon=True)
        self._thread.start()

    def _queries_loop(self, ex) -> None:
        while not self._stop.is_set():
            try:
                r = self.http.get("/api/worker/queries", params={"wait": 20}, timeout=40.0)
                if r.status_code == 401:
                    self.unauthorized = True
                    return
                r.raise_for_status()
                q = r.json().get("query")
                if not q:
                    continue
                vec = ex.encoder.encode_texts([q["text"]])[0].tolist()
                self.http.post(f"/api/worker/queries/{q['id']}/result", json={"vector": vec}, timeout=30.0)
            except Exception as e:  # noqa: BLE001
                log.warning("requetes de recherche: %s", e)
                self._stop.wait(5)

    def _beat(self) -> None:
        try:
            r = self.http.post("/api/worker/heartbeat", json=self._info)
            if r.status_code == 401:
                self.unauthorized = True
                return
            r.raise_for_status()
        except Exception as e:
            log.warning("battement impossible: %s", e)

    def _loop(self) -> None:
        while not self._stop.wait(settings.worker_heartbeat_seconds):
            self._beat()

    def claim(self, name, ex) -> list[JobItem]:
        try:
            r = self.http.post("/api/worker/claim", json={"instance": self.instance, "extractor": name, "n": ex.batch_size})
            if r.status_code == 401:
                raise Unauthorized
            r.raise_for_status()
        except Unauthorized:
            raise
        except Exception as e:
            log.warning("claim %s impossible: %s", name, e)
            return []
        items = []
        for j in r.json()["jobs"]:
            jid, pid = uuid.UUID(j["job_id"]), uuid.UUID(j["photo_id"])
            width, height = int(j["width"]), int(j["height"])
            probe = PhotoRef(id=pid, original=Path(), web=Path(), thumb=Path(), width=width, height=height)
            kind = "original" if ex.needs_original(probe) else "web"
            try:
                path = self._download(jid, kind)
            except Exception as e:
                log.warning("telechargement %s impossible: %s", pid, e)
                self._fail(jid, f"telechargement: {e}")
                continue
            items.append(JobItem(jid, pid, int(j["attempts"]), PhotoRef(id=pid, original=path, web=path, thumb=path, width=width, height=height)))
        return items

    def _download(self, job_id: uuid.UUID, kind: str) -> Path:
        path = self.tmp / f"{job_id}_{kind}.jpg"
        with self.http.stream("GET", f"/api/worker/jobs/{job_id}/image", params={"kind": kind}) as r:
            r.raise_for_status()
            with path.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        return path

    def _fail(self, job_id: uuid.UUID, error: str) -> None:
        try:
            self.http.post(f"/api/worker/jobs/{job_id}/fail", json={"error": error[:2000]}).raise_for_status()
        except Exception as e:
            log.warning("signalement d'echec impossible pour %s: %s", job_id, e)

    def finish(self, name, ex, item, result, error) -> None:
        for p in self.tmp.glob(f"{item.job_id}_*"):
            p.unlink(missing_ok=True)
        if error is not None:
            self._fail(item.job_id, error)
            return
        body = {"version": ex.version, "model": ex.model_name, "result": result}
        for attempt in range(3):
            try:
                r = self.http.post(f"/api/worker/jobs/{item.job_id}/result", json=body)
                if r.status_code == 400:
                    log.error("resultat %s refuse par le serveur: %s", name, r.text[:300])
                    return
                r.raise_for_status()
                return
            except Exception as e:
                log.warning("envoi du resultat %s (essai %d): %s", item.job_id, attempt + 1, e)
                time.sleep(5)
        log.error("resultat %s perdu ; le job sera remis en file par le serveur", item.job_id)

    def run_tasks(self) -> int:
        """Reclame et execute au plus une tache de maintenance. Renvoie 1 si une tache a ete traitee."""
        kinds = self._info.get("tasks") or []
        if not kinds:
            return 0
        try:
            r = self.http.post("/api/worker/claim-task", json={"instance": self.instance, "kinds": kinds})
            if r.status_code == 401:
                raise Unauthorized
            r.raise_for_status()
            task = r.json().get("task")
        except Unauthorized:
            raise
        except Exception as e:
            log.warning("claim-task impossible: %s", e)
            return 0
        if not task:
            return 0
        tid, kind = task["id"], task["kind"]
        t0 = time.time()
        try:
            data = self.http.get(f"/api/worker/tasks/{tid}/data", timeout=600.0)
            data.raise_for_status()
            result = run_task(kind, task.get("params") or {}, data.content)
            r = self.http.post(f"/api/worker/tasks/{tid}/result", json={"result": result}, timeout=600.0)
            if r.status_code == 400:
                log.error("tache %s refusee par le serveur: %s", kind, r.text[:300])
            else:
                r.raise_for_status()
                log.info("tache %s terminee en %.1fs : %s", kind, time.time() - t0, r.json().get("result"))
        except Exception as e:  # noqa: BLE001
            log.exception("echec tache %s", kind)
            try:
                self.http.post(f"/api/worker/tasks/{tid}/fail", json={"error": f"{type(e).__name__}: {e}"[:2000]})
            except Exception:
                pass
        return 1

    def stop(self) -> None:
        self._stop.set()
        try:
            self.http.post("/api/worker/release", json={"instance": self.instance})
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.http.close()


# --------------------------------------------------------------------------- boucle

def _run_batch(ex: extractors.Extractor, photos: list[PhotoRef]) -> tuple[list[dict | None], list[str | None]]:
    try:
        return list(ex.run(photos)), [None] * len(photos)
    except Exception as e:  # echec du lot entier : on retente photo par photo si lot > 1
        log.exception("echec lot %s", ex.name)
        if len(photos) == 1:
            return [None], [f"{type(e).__name__}: {e}"[:2000]]
        results: list[dict | None] = []
        errors: list[str | None] = []
        for ref in photos:
            try:
                results.append(ex.run([ref])[0])
                errors.append(None)
            except Exception as e2:
                results.append(None)
                errors.append(f"{type(e2).__name__}: {e2}"[:2000])
        return results, errors


def run_once(loaded: dict[str, extractors.Extractor], source: JobSource) -> int:
    """Traite au plus un lot par extracteur. Renvoie le nombre de jobs traites."""
    processed = 0
    for name, ex in loaded.items():
        items = source.claim(name, ex)
        if not items:
            continue
        t0 = time.time()
        results, errors = _run_batch(ex, [it.ref for it in items])
        for item, result, err in zip(items, results, errors):
            source.finish(name, ex, item, result, err)
        processed += len(items)
        log.info("%s: %d photo(s) en %.1fs", name, len(items), time.time() - t0)
    return processed


def _serve(loaded: dict[str, extractors.Extractor], source: JobSource, once: bool, remote: RemoteSource | None = None) -> None:
    try:
        while True:
            if remote is not None and remote.unauthorized:
                raise Unauthorized
            n = run_once(loaded, source)
            if remote is not None:
                n += remote.run_tasks()
            if once and n == 0:
                return
            if n == 0:
                time.sleep(settings.worker_poll_seconds)
    finally:
        source.stop()


def main(names: list[str], once: bool = False, server_url: str | None = None, token: str | None = None) -> None:
    instance = f"{socket.gethostname()}:{os.getpid()}"
    # docker stop envoie SIGTERM : on le transforme en sortie normale pour passer par les finally (release).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    server_url = server_url or settings.server_url

    if not server_url:
        loaded = {n: extractors.load(n) for n in names}
        source = LocalSource(instance)
        source.start(loaded)
        log.info("worker %s pret, extracteurs: %s", instance, ", ".join(loaded))
        _serve(loaded, source, once)
        return

    # Mode distant. Jeton : option/env (fixe), sinon fichier d'etat, sinon demande d'approbation sur le site.
    fixed = token or settings.worker_token
    loaded: dict[str, extractors.Extractor] | None = None
    while True:
        tok = fixed or pairing.load().get("token")
        if not tok:
            tok, _ = pairing.wait_for_approval(server_url, names)
        remote = RemoteSource(server_url, tok, instance)
        try:
            remote.connect(names)
        except Unauthorized:
            remote.http.close()
            if fixed:
                raise SystemExit("jeton de worker refuse par le serveur (WORKER_TOKEN)")
            log.warning("jeton refuse (worker revoque ?) : nouvelle demande d'approbation")
            pairing.clear_token()
            continue
        if loaded is None:
            loaded = {n: extractors.load(n) for n in names}
        remote.start(loaded)
        log.info("worker distant %s -> %s, extracteurs: %s", instance, server_url, ", ".join(loaded))
        try:
            _serve(loaded, remote, once, remote)
            return
        except Unauthorized:
            if fixed:
                raise SystemExit("jeton de worker refuse par le serveur (WORKER_TOKEN)")
            log.warning("jeton refuse (worker revoque ?) : nouvelle demande d'approbation")
            pairing.clear_token()
