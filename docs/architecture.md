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

## Ajout de photos

Trois entrées, même fonction `ingest_file` : `photoflow ingest <dossier>` pour un dossier déjà sur la machine
(sous `PHOTOS_ROOT`, monté en lecture seule), `POST /api/upload` depuis la page « Ajouter », et le service
`watcher` (`photoflow watch`) qui surveille `PHOTOS_ROOT` en continu. Le watcher compare les chemins relatifs
à ceux déjà en base ; un fichier nouveau n'est ingéré qu'au passage suivant, une fois sa taille et sa date
stables (copie terminée), donc sous une minute avec l'intervalle par défaut de 30 s. Un fichier remplacé sous
le même nom n'est pas revu ; un doublon de contenu est ignoré. Les fichiers
téléversés vont dans `DATA_DIR/_uploads/<date>/` (volume `photoflow-data`), `rel_path` commence par
`_uploads/` et `original_path()` sait le résoudre. Le nom de fichier d'origine est conservé tel quel dans
`photos.filename` (normalisé NFC, sans séparateurs) : c'est l'identifiant de l'utilisateur, il reste
recherchable même si deux fichiers portent le même nom. Sur le disque, un homonyme de contenu différent est
suffixé « (2) ». Un fichier dont le contenu (sha256) est déjà en base n'est pas réajouté, l'interface renvoie
vers la photo existante. Chaque ajout met en file les extracteurs par défaut.

## Workers distants (calcul décentralisé)

Déploiement visé : un petit serveur public (`docker-compose.server.yml`) porte la base, l'API, l'interface et un
reverse proxy ; le calcul vient de machines perso qui lancent `docker-compose.worker.yml`.

```
machine perso (worker)                         serveur photoflow.example.com
 ┌───────────────────────────┐    HTTPS sortant  ┌──────────────────────────────┐
 │ worker-ml / worker-vlm    │ ───────────────►  │ Caddy ─► API ─► Postgres      │
 │ (+ llama.cpp en option)   │  jeton porteur    │   /api/worker/* : jeton       │
 │ aucun port ouvert         │ ◄───────────────  │   le reste : mot de passe     │
 └───────────────────────────┘  images, jobs     └──────────────────────────────┘
                                                        ▲ navigateur : page « Workers »
```

Principes :

- **Le worker ne fait que des requêtes sortantes.** Il n'expose aucune API locale, donc aucun site ouvert dans
  le navigateur de la machine ne peut l'atteindre (pas de surface pour un DNS rebinding ou un `fetch` vers
  `localhost`). Le navigateur ne parle qu'au serveur. L'alternative « la page du site appelle
  `http://localhost` sur la machine » a été écartée : Safari bloque ce contenu mixte, Chrome demande une
  permission d'accès au réseau local, et il faudrait de toute façon transmettre un secret au worker.
- **Appairage en un clic.** Un worker sans jeton se signale au serveur avec un code aléatoire qu'il garde dans
  son volume (`pairing.py`, route `/api/worker/pair`, sans authentification mais bornée : 50 demandes au plus,
  expirées après 15 min). La page « Workers » le montre « en attente » ; « Approuver » crée le worker et dépose
  le jeton sur la demande, le worker le récupère à son passage suivant et le conserve. « Oublier » révoque le
  jeton : le worker repasse automatiquement en demande d'approbation. `photoflow workers create` reste
  disponible pour un jeton fixe (`WORKER_TOKEN`) sans passer par l'interface.
- **Un jeton par worker**, stocké haché, révocable. L'API ne sert à un worker que les jobs qui lui sont réservés
  et n'accepte un résultat que pour un job qu'il détient. Les résultats sont validés avant écriture (`persist.py` : dimension des vecteurs,
  schéma de l'analyse VLM, modèle d'embedding identique à celui de la base).
- **Rien ne tourne sans demande.** Le worker envoie un battement toutes les 30 s avec ses extracteurs, ses
  modèles et le rang de son VLM. Sur la page « Workers », le bouton « analyser N photos » réserve N jobs par
  extracteur coché (`jobs.reserved_for`). Le worker les réclame à son prochain passage, télécharge le dérivé
  web (ou l'original pour les visages des petits scans), calcule, renvoie le JSON **photo par photo, dès
  qu'elle est calculée**. Le worker interroge le serveur toutes les 3 s (`/api/worker/claim`) : cliquer
  « analyser » n'envoie rien au worker, ça réserve des jobs qu'il découvre à son passage suivant. Un
  `docker stop` rend les jobs en cours ; une machine qui meurt sans prévenir voit ses jobs repartir après
  `WORKER_DEAD_MINUTES` (5) sans battement, toujours réservés à elle. Les jobs réservés non commencés peuvent
  être rendus ; ils le sont d'office après `WORKER_ABSENT_HOURS` (6) sans battement, et redeviennent alors
  disponibles pour les autres workers. Ces délais sont vérifiés à chaque battement et à chaque ouverture de la
  page Workers.
- **Jamais deux workers sur la même photo** : la réservation est posée par le serveur dans une seule
  transaction, en excluant les photos déjà réservées ou en cours chez un autre worker, et la réclamation se
  fait avec `SKIP LOCKED` sur les seuls jobs réservés au demandeur. Deux workers qui demandent le même fonds au
  même moment se partagent le reste, sans recouvrement.
- **Choix des photos** (`workers.py`) : pour un extracteur classique, celles sans extraction ou avec une version
  plus ancienne que celle du worker. Pour le VLM, d'abord les photos sans légende machine, puis celles dont la
  meilleure légende vient d'un modèle de rang inférieur (`modelrank.py` : `4B` → 4, `32B` → 32, Claude → 1000,
  `VLM_RANK` pour forcer). Un worker au 4B ne retouche donc jamais une photo déjà vue par le 32B.
- **Plusieurs processus, un jeton** : worker-ml et worker-vlm d'une même machine partagent le jeton ; le serveur
  fusionne leurs battements (`workers.instances`). Le modèle d'embedding est imposé par le serveur au premier
  battement, pour que tous les vecteurs soient comparables.
- **Tâches de maintenance** (`tasks`, `workers.request_task`) : le regroupement des visages est trop lourd pour
  le serveur à partir de quelques dizaines de milliers de visages. Le bouton de la page Workers crée une tâche
  `faces_cluster` réservée à un worker ; il télécharge les vecteurs en `.npz`, calcule HDBSCAN, renvoie les
  étiquettes ; le serveur les applique en une requête, propage les personnes nommées et reconstruit les séries
  en arrière-plan (`series.build`, léger). Même bail et même reprise que les jobs.
- **Requêtes de recherche sémantique** : quand l'API n'a pas torch (serveur de coordination), elle dépose la
  requête texte dans une file en mémoire (`queries.py`) ; un worker en ligne avec le modèle d'embedding la prend
  en long-poll, l'encode et renvoie le vecteur. Latence de l'ordre de 100 ms, 503 après 8 s sans réponse.
- Les workers branchés directement sur la base (grosse machine) continuent de prendre la file commune
  (`reserved_for IS NULL`) ; les deux modes coexistent.

Le mot de passe de l'interface est géré par Caddy (`deploy/Caddyfile`), pas par l'application : les routes
`/api/workers/*` (réserver, rendre) sont donc protégées par le même mot de passe que le reste.

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
| `jobs` | (photo, extractor) | file de travail ; `reserved_for` = worker distant désigné |
| `workers` | id | worker distant : nom, jeton haché, état de ses instances |
| `extractions` | (photo, extractor) | JSON brut de chaque extracteur, version, modèle |
| `captions` | (photo, source) | titre, description, analyse structurée, `model_rank` ; `tsv` généré pour le plein texte |
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
