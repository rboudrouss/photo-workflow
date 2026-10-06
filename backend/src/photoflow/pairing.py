"""Cote worker : etat persistant (code d'appairage, jeton) et attente d'approbation.

Le fichier DATA_DIR/worker.json est partage par les processus d'une meme machine (worker-ml, worker-vlm)
via le volume de donnees : un seul code, un seul jeton, un seul worker vu du serveur.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import time

import httpx

from .config import settings

log = logging.getLogger(__name__)


def _path():
    return settings.data_dir / "worker.json"


def load() -> dict:
    try:
        return json.loads(_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(state: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, p)


def hostname() -> str:
    return settings.worker_name or socket.gethostname()


def ensure_code() -> str:
    """Code d'appairage stable entre redemarrages ; cree atomiquement si deux processus demarrent ensemble."""
    state = load()
    if state.get("code"):
        return state["code"]
    code = secrets.token_urlsafe(24)
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return load().get("code") or ensure_code()
    with os.fdopen(fd, "w") as f:
        json.dump({"code": code}, f)
    return code


def clear_token() -> None:
    state = load()
    state.pop("token", None)
    state.pop("name", None)
    save(state)


def wait_for_approval(server_url: str, extractors: list[str], poll: float = 10.0) -> tuple[str, str]:
    """Bloque jusqu'a ce que quelqu'un approuve ce worker sur la page Workers. Renvoie (jeton, nom)."""
    code = ensure_code()
    announced = False
    with httpx.Client(base_url=server_url.rstrip("/"), timeout=30.0) as http:
        while True:
            state = load()
            if state.get("token"):  # un autre processus de la machine a deja recu le jeton
                return state["token"], state.get("name", hostname())
            try:
                r = http.post("/api/worker/pair", json={"code": code, "hostname": hostname(), "extractors": extractors})
                r.raise_for_status()
                out = r.json()
            except Exception as e:
                log.warning("serveur %s injoignable: %s", server_url, e)
                time.sleep(poll)
                continue
            if out.get("status") == "approved":
                save({**load(), "token": out["token"], "name": out["name"]})
                log.info("worker approuve sous le nom %s", out["name"])
                return out["token"], out["name"]
            if not announced:
                log.info("en attente d'approbation : ouvre %s/workers et clique « Approuver » pour %s", server_url, hostname())
                announced = True
            time.sleep(poll)
