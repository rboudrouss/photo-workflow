"""Regroupement des visages par personne (HDBSCAN sur les embeddings ArcFace).

Les embeddings sont normalises : la distance euclidienne est monotone avec la distance cosinus.
Un cluster = probablement la meme personne. L'interface permet ensuite de nommer un cluster,
ce qui cree une Person et l'attache a tous ses visages.
"""

from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import select, text

from .db import session_scope
from .models import Face

log = logging.getLogger(__name__)


def cluster(min_cluster_size: int = 3, min_samples: int | None = None, epsilon: float = 0.0, min_score: float = 0.6) -> dict:
    from sklearn.cluster import HDBSCAN

    with session_scope() as session:
        rows = session.execute(
            select(Face.id, Face.embedding, Face.person_id).where(Face.det_score >= min_score)
        ).all()
    if len(rows) < min_cluster_size:
        return {"faces": len(rows), "clusters": 0}

    ids = [r.id for r in rows]
    X = np.array([np.asarray(r.embedding, dtype=np.float32) for r in rows])
    model = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=min_samples, cluster_selection_epsilon=epsilon, metric="euclidean")
    labels = model.fit_predict(X)

    with session_scope() as session:
        session.execute(text("UPDATE faces SET cluster_id = NULL"))
        for fid, lab in zip(ids, labels):
            session.execute(text("UPDATE faces SET cluster_id = :c WHERE id = :id"), {"c": int(lab), "id": fid})
        # Propagation : si un cluster contient des visages deja nommes, on nomme les autres.
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
    n_clusters = int(len(set(labels)) - (1 if -1 in labels else 0))
    n_noise = int((labels == -1).sum())
    log.info("%d visages, %d clusters, %d non regroupes", len(ids), n_clusters, n_noise)
    return {"faces": len(ids), "clusters": n_clusters, "noise": n_noise}
