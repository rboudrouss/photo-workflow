# photo-workflow

Pipeline d'extraction d'informations sur un fonds de photos anciennes (titre et description de vente,
recherche sémantique, visages, doublons, époque et lieu estimés, détection de nudité), avec une interface web de consultation et de
correction, et un export vers Delcampe.

- `docs/architecture.md` : comment c'est organisé, comment ajouter un extracteur.
- `docs/extracted-data.md` : tout ce qui est extrait, par quel moyen, avec quelles limites.
- `docs/models.md` : quel modèle pour quelle machine, pros et cons, coûts.
- `docs/delcampe.md` : ce qui est possible côté publication.

## Démarrage rapide (dev, ta machine)

```bash
cp .env.example .env            # PHOTOS_DIR = dossier de tes photos (ou ./samples)
mkdir -p samples                # y déposer une centaine de photos de test
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml run --rm api photoflow db init
docker compose -f docker-compose.dev.yml run --rm api photoflow ingest /photos
```

Puis ouvrir http://localhost:3000. Les workers traitent la file en continu ; la page « Statut » montre
l'avancement. Au premier démarrage, le service `llm` télécharge Qwen3-VL 4B (~3 Go) et les workers téléchargent
SigLIP 2 et InsightFace (~1 Go au total).

Quand les visages sont extraits :

```bash
docker compose -f docker-compose.dev.yml run --rm api photoflow faces cluster
docker compose -f docker-compose.dev.yml run --rm api photoflow series build
```

## Commandes utiles

```bash
photoflow ingest /photos/dossier            # ingérer un dossier (sous PHOTOS_ROOT)
photoflow jobs stats                        # état de la file
photoflow jobs enqueue vlm --force          # relancer un extracteur sur tout le fonds
photoflow jobs retry-failed
photoflow worker --extractors embedding,faces
photoflow worker --server https://photoflow.example.com            # worker distant, sans base (approbation sur le site)
photoflow workers list / create pc-remi / revoke pc-remi            # workers distants (create = jeton fixe)
photoflow faces cluster --min-cluster-size 3
photoflow series build                                # regrouper par pellicule / séance
photoflow vlm run --backend llama --limit 20          # tester le VLM local sur 20 photos
photoflow vlm run --backend anthropic --limit 20      # idem avec Claude (clé API Console dans .env)
photoflow claude-batch submit --limit 1000            # envoi en lot à moitié prix
photoflow claude-batch status
photoflow claude-batch collect msgbatch_xxx
photoflow vlm schema                                  # le JSON imposé au modèle
```

Dans Docker, préfixer par `docker compose -f docker-compose.dev.yml run --rm api`.

## Serveur + workers distants

Le serveur (petite machine publique) coordonne ; des machines perso calculent. Voir « Workers distants » dans
`docs/architecture.md`.

```bash
# serveur (DOMAIN, UI_PASSWORD_HASH, POSTGRES_PASSWORD, PHOTOS_DIR dans .env)
docker compose -f docker-compose.server.yml up -d --build
docker compose -f docker-compose.server.yml run --rm api photoflow db init
docker compose -f docker-compose.server.yml run --rm api photoflow ingest /photos

# machine perso : rien à configurer (le serveur par défaut est dans le compose, options dans .env.worker.example)
docker compose -f docker-compose.worker.yml up -d --build                  # visages, embeddings, nudité, physique
docker compose -f docker-compose.worker.yml --profile vlm up -d --build    # + VLM local
```

Puis page « Workers » du site : la machine apparaît en attente, « Approuver », puis « Analyser N photos ».

## Grosse machine (Spark ou Mac Studio)

Lire `docs/models.md` puis :

```bash
docker compose --profile cuda up -d --build     # Spark / Linux NVIDIA
docker compose --profile mac  up -d --build     # Mac Studio, avec llama-server natif sur l'hôte
```

La base, les photos et les dérivés sont les mêmes quel que soit le profil. Les workers peuvent tourner sur
plusieurs machines contre la même base.

## Développement sans Docker

```bash
cd backend && uv venv && uv pip install -e ".[ml]"
export DATABASE_URL=postgresql+psycopg://photoflow:photoflow@localhost:5432/photoflow
export PHOTOS_ROOT=/chemin/photos DATA_DIR=/chemin/data
photoflow db init && photoflow serve --reload

cd web && bun install && bun run dev          # http://localhost:5173, API sur :8000
```
