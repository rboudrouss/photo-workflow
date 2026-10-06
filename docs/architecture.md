# Architecture

## Principe

Une photo = une ligne dans `photos`. Chaque extracteur est un plugin versionné et idempotent qui écrit dans ses
propres tables. Les originaux ne sont jamais modifiés. Toute correction humaine est stockée à côté des résultats
machine, jamais à la place, pour servir plus tard de vérité terrain.

```
dossier photos ──► ingest ──► photos + jobs ──► worker(s) ──► extractions / embeddings / faces / captions
                                                                          │
                                        interface web ◄── API FastAPI ◄───┘
                                              │
                                   corrections (captions.source = human), noms de personnes
                                              │
                                   fiche / CSV Delcampe
```

## Composants

| Composant | Rôle | Où |
|---|---|---|
| Postgres + pgvector | seule base : métadonnées, JSON des extracteurs, vecteurs, plein texte français, file de jobs | service `db` |
| `photoflow` (Python) | CLI, ingestion, worker, API | `backend/` |
| extracteurs | `embedding`, `faces`, `vlm` | `backend/src/photoflow/extractors/` |
| backends VLM | OpenAI-compatible (llama.cpp, vLLM, MLX, Ollama) et Anthropic | `extractors/vlm_backends/` |
| llama-server | sert le VLM local derrière une API HTTP | service `llm` ou natif sur l'hôte |
| web (SvelteKit) | grille, recherche, page photo, visages, statut | `web/` |

## File de jobs

Table `jobs` avec contrainte unique `(photo_id, extractor)`. Un worker réclame un lot avec
`SELECT ... FOR UPDATE SKIP LOCKED`, donc plusieurs workers sur plusieurs machines peuvent se partager la même
base sans coordination. Un job échoué est retenté jusqu'à `WORKER_MAX_ATTEMPTS`, puis marqué `failed`
(`photoflow jobs retry-failed` pour relancer). Les jobs `running` abandonnés depuis plus d'une heure sont remis
en file au démarrage d'un worker.

Chaque worker ne gère que les extracteurs qu'on lui donne. Cela permet par exemple un worker GPU sur la grosse
machine pour le VLM et un worker CPU ailleurs pour les visages, ou de couper le VLM sans bloquer le reste.

## Ajouter un extracteur

1. Créer `extractors/monextracteur.py` avec une classe héritant de `Extractor` : `name`, `version`,
   `batch_size`, `run(photos) -> list[dict]`, et `persist()` si des tables dédiées sont nécessaires.
2. L'enregistrer dans `extractors/__init__.py`.
3. `photoflow jobs enqueue monextracteur` pour le lancer sur l'existant, et l'ajouter à `DEFAULT_EXTRACTORS`
   pour les prochaines ingestions.
4. Documenter ses champs dans `docs/extracted-data.md`.

Incrémenter `version` quand le résultat change de forme ou de qualité, puis `jobs enqueue --force`.

## Schéma des tables

| Table | Clé | Contenu |
|---|---|---|
| `photos` | id | fichier, dimensions, pHash, statut |
| `jobs` | (photo, extractor) | file de travail |
| `extractions` | (photo, extractor) | JSON brut de chaque extracteur, version, modèle |
| `captions` | (photo, source) | titre, description, analyse structurée ; `tsv` généré pour le plein texte |
| `image_embeddings` | photo | vecteur SigLIP, index HNSW cosinus |
| `faces` | id | bbox, score, âge, genre, vecteur ArcFace, cluster, personne |
| `persons` | id | nom |
| `series` | id | nom, taille ; `photos.series_id` pointe dessus |
| `series_edges` | id | liens retenus entre deux photos d'une série, score et raisons |
| `claude_batches` | id Anthropic | correspondance custom_id → photo pour l'API Batches |

## Interface

Le front ne parle qu'à l'API, en JSON, depuis le navigateur (`PUBLIC_API_BASE`). Il n'y a pas de rendu côté
serveur dépendant de la base, ce qui garde le déploiement simple : l'API et le front peuvent être sur des
machines différentes.

L'interface est aussi l'outil de correction : chaque sauvegarde crée ou met à jour la légende `human`, qui prime
ensuite partout. Nommer un groupe de visages crée une personne et l'attache à tout le groupe.

## Delcampe

Pas d'API ouverte, voir `docs/delcampe.md`. L'API expose la fiche prête à coller et un export CSV à remapper sur
le modèle Easy Uploader le jour où le compte Club+ Gold est actif.

## Évolutions prévues

- **Géo dédiée** : extracteur GeoCLIP, fusion avec `lieu` du VLM.
- **Évaluation** : script comparant les légendes machine aux légendes `human` (décennie, scène, tags) pour mesurer
  chaque modèle.
- **Publication** : file de relecture avec statut par photo (à relire, validée, publiée), lot CSV.
