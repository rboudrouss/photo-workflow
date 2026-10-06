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
| extracteurs | `physical`, `embedding`, `faces`, `nudity`, `vlm` | `backend/src/photoflow/extractors/` |
| backends VLM | OpenAI-compatible (llama.cpp, vLLM, MLX, Ollama), Anthropic (clé API), Claude Code (abonnement) | `extractors/vlm_backends/` |
| llama-server | sert le VLM local derrière une API HTTP | service `llm` du worker, ou natif sur l'hôte |
| web (SvelteKit) | grille, recherche, page photo, visages, séries, workers, ajout ; relais vers l'API et mot de passe | `web/` |
| watcher | ingère ce qui apparaît dans le dossier des photos | service `watcher` |

## File de jobs

Table `jobs` avec contrainte unique `(photo_id, extractor)`. Un worker réclame un lot avec
`SELECT ... FOR UPDATE SKIP LOCKED`, donc plusieurs workers sur plusieurs machines peuvent se partager la même
base sans coordination. Un job échoué est retenté jusqu'à `WORKER_MAX_ATTEMPTS`, puis marqué `failed`
(`photoflow jobs retry-failed` pour relancer).

Deux façons de consommer la file : les workers distants (cas normal, section suivante) ne voient que les jobs
que le serveur leur a réservés ; les workers branchés directement sur la base (compose de dev) prennent la file
commune (`reserved_for IS NULL`) et leurs jobs `running` abandonnés depuis plus d'une heure sont remis en file au
démarrage d'un worker. Chaque worker ne gère que les extracteurs qu'on lui donne, ce qui permet de couper le VLM
sans bloquer le reste.

## Ajout de photos

Trois entrées, même fonction `ingest_file` : `photoflow ingest <dossier>` pour un dossier déjà sur la machine
(sous `PHOTOS_ROOT`, monté en lecture seule), `POST /api/upload` depuis la page « Ajouter », et le service
`watcher` (`photoflow watch`) qui surveille `PHOTOS_ROOT` en continu. Le watcher compare les chemins relatifs
à ceux déjà en base ; un fichier nouveau n'est ingéré qu'au passage suivant, une fois sa taille et sa date
stables (copie terminée), donc sous une minute avec l'intervalle par défaut de 30 s. Un fichier remplacé sous
le même nom n'est pas revu ; un doublon de contenu est ignoré. Les fichiers
téléversés vont dans `UPLOADS_DIR/<date>/` (défaut `DATA_DIR/_uploads`, sur le serveur `PHOTOS_ROOT/_uploads`
pour n'avoir qu'un dossier à sauvegarder), `rel_path` commence par `_uploads/` et `original_path()` sait le
résoudre. Le nom de fichier d'origine est conservé tel quel dans
`photos.filename` (normalisé NFC, sans séparateurs) : c'est l'identifiant de l'utilisateur, il reste
recherchable même si deux fichiers portent le même nom. Sur le disque, un homonyme de contenu différent est
suffixé « (2) ». Un fichier dont le contenu (sha256) est déjà en base n'est pas réajouté, l'interface renvoie
vers la photo existante. Chaque ajout met en file les extracteurs par défaut.

## Workers distants (calcul décentralisé)

Le serveur (`docker-compose.coolify.yml`, voir `docs/deploy.md`) porte la base, l'API, l'interface et le
watcher, sans aucun modèle. Tout le calcul vient de machines qui lancent `docker-compose.worker.yml`, du portable
à la grosse machine (`docker-compose.worker.gpu.yml` pour un GPU NVIDIA, profil `vlm-host` pour un VLM natif
sur Mac).

```
machine perso (worker)                         serveur photoflow.example.com
 ┌───────────────────────────┐    HTTPS sortant  ┌──────────────────────────────┐
 │ worker-ml / worker-vlm    │ ───────────────►  │ web ─► API ─► Postgres        │
 │ (+ llama.cpp / Claude)    │  jeton porteur    │   /api/worker/* : jeton       │
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
- **Plusieurs processus, un jeton** : les services d'une même machine (worker-ml, worker-vlm et ses répliques,
  worker-claude) partagent le jeton ; le serveur fusionne leurs battements (`workers.instances`) et affiche
  tous les modèles VLM actifs. Le modèle d'embedding est imposé par le serveur au premier
  battement, pour que tous les vecteurs soient comparables.
- **Tâches de maintenance** (`tasks`, `workers.request_task`) : le regroupement des visages est trop lourd pour
  le serveur à partir de quelques dizaines de milliers de visages. Le bouton de la page Workers crée une tâche
  `faces_cluster` réservée à un worker ; il télécharge les vecteurs en `.npz`, calcule HDBSCAN, renvoie les
  étiquettes ; le serveur les applique en une requête, propage les personnes nommées et reconstruit les séries
  en arrière-plan (`series.build`, léger). Même bail et même reprise que les jobs.
- **Requêtes de recherche sémantique** : quand l'API n'a pas torch (serveur de coordination), elle dépose la
  requête texte dans une file en mémoire (`queries.py`) ; un worker en ligne avec le modèle d'embedding la prend
  en long-poll, l'encode et renvoie le vecteur. Latence de l'ordre de 100 ms, 503 après 8 s sans réponse.

Le mot de passe de l'interface est porté par le site (`web/src/hooks.server.ts`, Basic) : il couvre tout, y
compris les routes `/api/workers/*` (réserver, rendre), sauf `/api/worker/*` réservé aux jetons des workers.

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
| `pair_requests` | code | demandes d'appairage en attente d'approbation (15 min) |
| `tasks` | id | tâches de maintenance déléguées à un worker (`faces_cluster`) |
| `extractions` | (photo, extractor) | JSON brut de chaque extracteur, version, modèle |
| `captions` | (photo, source) | titre, description, analyse structurée, `model_rank` ; `tsv` généré pour le plein texte |
| `image_embeddings` | photo | vecteur SigLIP, index HNSW cosinus |
| `faces` | id | bbox, score, âge, genre, vecteur ArcFace, cluster, personne |
| `persons` | id | nom |
| `series` | id | nom, taille ; `photos.series_id` pointe dessus |
| `series_edges` | id | liens retenus entre deux photos d'une série, score et raisons |
| `claude_batches` | id Anthropic | correspondance custom_id → photo pour l'API Batches |

## Interface

Le front ne parle qu'à l'API, en JSON, depuis le navigateur. En production le site relaie `/api` et `/media`
vers l'API interne (une seule origine, pas de CORS) ; en dev le navigateur appelle l'API directement
(`PUBLIC_API_BASE`). Aucun rendu côté serveur ne dépend de la base.

La recherche et ses filtres (texte, objet, scène, époque, nudité, description par VLM, tags) vivent dans l'URL :
un lien se partage et le retour arrière retrouve la même page. Les tags sont comparés sans casse ni accents ;
les propositions (`/api/tags`) sont calculées sur les résultats courants. L'interrupteur « flouter » de la
barre du haut vaut pour toutes les pages.

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
