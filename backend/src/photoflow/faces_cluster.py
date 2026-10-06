"""Regroupement des visages par personne (HDBSCAN sur les embeddings ArcFace).

Les embeddings sont normalises : la distance euclidienne est monotone avec la distance cosinus.
Un cluster = probablement la meme personne. L'interface permet ensuite de nommer un cluster,
ce qui cree une Person et l'attache a tous ses visages.

Trois morceaux, pour que le calcul (lourd : minutes de CPU a 20 000 visages) puisse se faire sur un worker
distant pendant que le serveur ne fait que lire et ecrire :
  - load_faces(session)            -> ids, matrice float32        (serveur)
  - cluster_labels(X, ...)         -> etiquettes HDBSCAN          (worker, ou CLI sur une machine avec la base)
  - apply_labels(session, ids, labels)                           (serveur) + propagation des personnes nommees
"""

from __future__ import annotations

import logging
import uuid

import numpy as np
from sqlalchemy import select, text

from .models import Face

log = logging.getLogger(__name__)

def load_faces(session, min_score: float = 0.6) -> tuple[list[uuid.UUID], np.ndarray]:
    rows = session.execute(select(Face.id, Face.embedding).where(Face.det_score >= min_score)).all()
    ids = [r.id for r in rows]
    X = np.array([np.asarray(r.embedding, dtype=np.float32) for r in rows], dtype=np.float32).reshape(len(ids), -1)
    return ids, X


def cluster_labels(X: np.ndarray, min_cluster_size: int = 3, min_samples: int | None = None, epsilon: float = 0.0) -> np.ndarray:
    """Etiquettes HDBSCAN (-1 = bruit). Pur calcul, aucune base."""
    from sklearn.cluster import HDBSCAN

    if len(X) < min_cluster_size:
        return np.full(len(X), -1, dtype=np.int64)
    model = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=min_samples, cluster_selection_epsilon=epsilon, metric="euclidean")
    return model.fit_predict(X).astype(np.int64)


def apply_labels(session, ids: list[uuid.UUID], labels) -> dict:
    """Ecrit les clusters en une requete, puis nomme les visages des clusters qui contiennent des visages deja nommes."""
    labels = [int(x) for x in labels]
    session.execute(text("UPDATE faces SET cluster_id = NULL"))
    if ids:
        session.execute(
            text(
                "UPDATE faces f SET cluster_id = v.c FROM (SELECT unnest(CAST(:ids AS uuid[])) AS id, unnest(CAST(:labels AS int[])) AS c) v "
                "WHERE f.id = v.id"
            ),
            {"ids": [str(i) for i in ids], "labels": labels},
        )
    session.execute(
        text(
            """
            UPDATE faces f SET person_id = s.person_id
            FROM (
                SELECT cluster_id, person_id, count(*) AS n,
                       row_number() OVER (PARTITION BY cluster_id ORDER BY count(*) DESC) AS rn
                FROM faces WHERE cluster_id >= 0 AND person_id IS NOT NULL
                GROUP BY cluster_id, person_id
            ) s
            WHERE s.rn = 1 AND f.cluster_id = s.cluster_id AND f.person_id IS NULL
            """
        )
    )
    n_clusters = len(set(labels) - {-1})
    n_noise = sum(1 for x in labels if x == -1)
    log.info("%d visages, %d clusters, %d non regroupes", len(ids), n_clusters, n_noise)
    return {"faces": len(ids), "clusters": n_clusters, "noise": n_noise}


def cluster(min_cluster_size: int = 3, min_samples: int | None = None, epsilon: float = 0.0, min_score: float = 0.6) -> dict:
    """Tout sur place (CLI, machine connectee a la base)."""
    from .db import session_scope

    with session_scope() as session:
        ids, X = load_faces(session, min_score)
    labels = cluster_labels(X, min_cluster_size, min_samples, epsilon)
    with session_scope() as session:
        return apply_labels(session, ids, labels)
