"""Encodage des requetes texte (recherche semantique) par un worker en ligne.

Le serveur de coordination n'a pas torch : il ne peut pas encoder "plage" dans l'espace SigLIP. Un worker qui a
le modele d'embedding charge attend les requetes en long-poll (GET /api/worker/queries?wait=20), encode en
quelques dizaines de millisecondes et renvoie le vecteur. L'API attend la reponse quelques secondes.
Etat en memoire du processus API (un seul processus uvicorn) : rien a persister, une requete perdue est juste
une recherche a relancer.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field


@dataclass
class Query:
    id: str
    text: str
    done: threading.Event = field(default_factory=threading.Event)
    vector: list[float] | None = None
    taken: bool = False


class QueryBroker:
    def __init__(self) -> None:
        self._lock = threading.Condition()
        self._pending: deque[Query] = deque()
        self._all: dict[str, Query] = {}

    def ask(self, text: str, timeout: float) -> list[float] | None:
        """Cote API : depose la requete, attend le vecteur. None si aucun worker n'a repondu a temps."""
        q = Query(id=uuid.uuid4().hex, text=text)
        with self._lock:
            self._pending.append(q)
            self._all[q.id] = q
            self._lock.notify()
        try:
            q.done.wait(timeout)
            return q.vector
        finally:
            with self._lock:
                self._all.pop(q.id, None)
                if not q.taken and q in self._pending:
                    self._pending.remove(q)

    def take(self, wait: float) -> Query | None:
        """Cote worker : prend la prochaine requete, ou attend jusqu'a `wait` secondes."""
        deadline = time.monotonic() + wait
        with self._lock:
            while not self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._lock.wait(remaining)
            q = self._pending.popleft()
            q.taken = True
            return q

    def answer(self, query_id: str, vector: list[float]) -> bool:
        with self._lock:
            q = self._all.get(query_id)
        if q is None:
            return False
        q.vector = vector
        q.done.set()
        return True


broker = QueryBroker()
