"""Detection de series : photos de la meme pellicule ou de la meme seance.

Principe : graphe de proximite entre photos, puis composantes connexes.
  - aretes candidates : k plus proches voisines par embedding (pgvector), similarite >= `candidate_sim`
  - score = similarite + bonus visages communs (meme cluster ou meme personne)
                       + bonus / malus signal physique (format, orientation, tonalite)
  - doublon pHash (distance <= 10) : arete acceptee d'office
  - arete acceptee si score >= `edge_threshold`
  - les composantes trop grosses (chainage) sont rescindees avec un seuil plus strict
Les ids et noms de series sont conserves d'une reconstruction a l'autre quand la majorite des photos
d'une nouvelle composante vient d'une ancienne serie.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict

from sqlalchemy import text

from .db import session_scope

log = logging.getLogger(__name__)

FACE_BONUS = 0.10
PHYSICAL_BONUS = 0.05
PHYSICAL_PENALTY = 0.05  # par critere en desaccord (orientation, ratio, tonalite), donc jusqu'a 0.15
PHASH_DUP = 10


class UnionFind:
    def __init__(self):
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _hamming(a: int | None, b: int | None) -> int | None:
    if a is None or b is None:
        return None
    return bin((a ^ b) & ((1 << 64) - 1)).count("1")


def _physical_match(pa: dict | None, pb: dict | None) -> tuple[str, int]:
    """('ok' | 'neutral' | 'mismatch' | 'unknown', nombre de criteres en desaccord)."""
    if not pa or not pb:
        return "unknown", 0
    mismatches = 0
    if pa["orientation"] != pb["orientation"]:
        mismatches += 1
    ratio_diff = abs(pa["ratio"] - pb["ratio"]) / max(pa["ratio"], pb["ratio"])
    if ratio_diff > 0.08:
        mismatches += 1
    if pa["tonality"] != pb["tonality"]:
        mismatches += 1
    if mismatches:
        return "mismatch", mismatches
    if ratio_diff <= 0.03 and pa.get("has_border") == pb.get("has_border"):
        return "ok", 0
    return "neutral", 0


def _load(session):
    photos = {
        r.id: {"phash": r.phash, "old": r.series_id}
        for r in session.execute(text("SELECT id, phash, series_id FROM photos WHERE status='ready'")).all()
    }
    physical = {
        r.photo_id: r.data
        for r in session.execute(text("SELECT photo_id, data FROM extractions WHERE extractor='physical'")).all()
    }
    clusters, persons = defaultdict(set), defaultdict(set)
    for r in session.execute(
        text("SELECT photo_id, cluster_id, person_id FROM faces WHERE cluster_id >= 0 OR person_id IS NOT NULL")
    ).all():
        if r.cluster_id is not None and r.cluster_id >= 0:
            clusters[r.photo_id].add(r.cluster_id)
        if r.person_id is not None:
            persons[r.photo_id].add(r.person_id)
    return photos, physical, clusters, persons


def _candidates(session, k: int, candidate_sim: float):
    session.execute(text("SET LOCAL hnsw.ef_search = 64"))
    rows = session.execute(
        text(
            """
            SELECT a.photo_id AS pa, b.photo_id AS pb, b.sim
            FROM image_embeddings a
            JOIN LATERAL (
                SELECT e.photo_id, 1 - (e.embedding <=> a.embedding) AS sim
                FROM image_embeddings e
                ORDER BY e.embedding <=> a.embedding
                LIMIT :k
            ) b ON b.photo_id <> a.photo_id
            WHERE b.sim >= :sim
            """
        ),
        {"k": k + 1, "sim": candidate_sim},
    ).all()
    seen, out = set(), []
    for r in rows:
        key = (min(r.pa, r.pb), max(r.pa, r.pb))
        if key in seen:
            continue
        seen.add(key)
        out.append((key[0], key[1], float(r.sim)))
    return out


def _score_edges(cands, photos, physical, clusters, persons, edge_threshold):
    edges = []
    for a, b, sim in cands:
        if a not in photos or b not in photos:
            continue
        reasons = {"sim": round(sim, 3)}
        score = sim
        d = _hamming(photos[a]["phash"], photos[b]["phash"])
        if d is not None and d <= PHASH_DUP:
            reasons["phash"] = d
            score = max(score, 1.0)
        shared = bool(clusters[a] & clusters[b]) or bool(persons[a] & persons[b])
        if shared:
            reasons["faces"] = True
            score += FACE_BONUS
        pm, n_mismatch = _physical_match(physical.get(a), physical.get(b))
        reasons["physical"] = pm if not n_mismatch else f"mismatch x{n_mismatch}"
        if pm == "ok":
            score += PHYSICAL_BONUS
        elif pm == "mismatch":
            score -= PHYSICAL_PENALTY * n_mismatch
        reasons["score"] = round(score, 3)
        if score >= edge_threshold:
            edges.append((a, b, score, reasons))
    return edges


def _components(edges, max_size, threshold, depth=0):
    """Composantes connexes ; les trop grosses sont rescindees avec un seuil plus strict."""
    uf = UnionFind()
    for a, b, _, _ in edges:
        uf.union(a, b)
    groups = defaultdict(list)
    for a, b, s, r in edges:
        groups[uf.find(a)].append((a, b, s, r))
    result = []
    for g_edges in groups.values():
        members = {x for a, b, _, _ in g_edges for x in (a, b)}
        if len(members) > max_size and depth < 30:
            # On coupe d'abord les maillons les plus faibles : seuil releve par petits pas
            # jusqu'a ce qu'au moins une arete tombe. Si toutes tombent d'un coup (poids egaux),
            # on garde la composante entiere plutot que de perdre les photos.
            step, stricter = threshold, g_edges
            while len(stricter) == len(g_edges) and step < 1.0:
                step = round(step + 0.01, 4)
                stricter = [e for e in g_edges if e[2] >= step]
            if stricter and len(stricter) < len(g_edges):
                result.extend(_components(stricter, max_size, step, depth + 1))
                continue
        result.append((members, g_edges))
    return result


def build(candidate_sim: float = 0.75, edge_threshold: float = 0.85, k: int = 10, max_size: int = 150, min_size: int = 2) -> dict:
    with session_scope() as session:
        photos, physical, clusters, persons = _load(session)
        if not photos:
            return {"photos": 0, "series": 0}
        cands = _candidates(session, k, candidate_sim)
        log.info("%d photos, %d paires candidates", len(photos), len(cands))
        edges = _score_edges(cands, photos, physical, clusters, persons, edge_threshold)
        log.info("%d aretes acceptees", len(edges))
        comps = [c for c in _components(edges, max_size, edge_threshold) if len(c[0]) >= min_size]

        old_names = {r.id: r.name for r in session.execute(text("SELECT id, name FROM series")).all()}
        used_old = set()
        session.execute(text("DELETE FROM series_edges"))
        session.execute(text("UPDATE photos SET series_id = NULL"))

        for members, g_edges in sorted(comps, key=lambda c: -len(c[0])):
            # Report de l'ancienne serie majoritaire.
            votes = defaultdict(int)
            for m in members:
                if photos[m]["old"] is not None:
                    votes[photos[m]["old"]] += 1
            sid = None
            if votes:
                best, n = max(votes.items(), key=lambda kv: kv[1])
                if n * 2 >= len(members) and best not in used_old and best in old_names:
                    sid = best
            if sid is None:
                sid = session.execute(
                    text("INSERT INTO series (size) VALUES (:n) RETURNING id"), {"n": len(members)}
                ).scalar_one()
            else:
                session.execute(text("UPDATE series SET size = :n WHERE id = :id"), {"n": len(members), "id": sid})
            used_old.add(sid)
            session.execute(
                text("UPDATE photos SET series_id = :sid WHERE id = ANY(:ids)"),
                {"sid": sid, "ids": list(members)},
            )
            for a, b, s, r in g_edges:
                session.execute(
                    text(
                        "INSERT INTO series_edges (series_id, photo_a, photo_b, score, reasons) "
                        "VALUES (:sid, :a, :b, :s, CAST(:r AS jsonb))"
                    ),
                    {"sid": sid, "a": a, "b": b, "s": s, "r": json.dumps(r)},
                )
        session.execute(text("DELETE FROM series WHERE id NOT IN (SELECT DISTINCT series_id FROM photos WHERE series_id IS NOT NULL)"))
        grouped = sum(len(c[0]) for c in comps)
    return {"photos": len(photos), "candidates": len(cands), "edges": len(edges), "series": len(comps), "grouped_photos": grouped}
