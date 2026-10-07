# photo-workflow

Pipeline d'extraction d'informations sur un fonds de photos anciennes (titre et description de vente,
recherche sémantique, visages, séries, époque et lieu estimés, détection de nudité), avec une interface web de
consultation et de correction, et un export vers Delcampe.

- `docs/architecture.md` : comment c'est organisé, workers, file de jobs, comment ajouter un extracteur.
- `docs/deploy.md` : le serveur sur Coolify, et comment brancher un worker.
- `docs/extracted-data.md` : tout ce qui est extrait, par quel moyen, avec quelles limites.
- `docs/models.md` : quel modèle pour quelle machine, pros et cons, coûts.
- `docs/delcampe.md` : publier sur Delcampe (Easy Uploader, sélection, page Export, liens publics des images).

## Organisation

```
                     photoflow.rboud.com (Coolify, petit serveur)
                     base, API, interface, watcher : ne calcule rien
                                  ▲
               HTTPS sortant, jeton par machine, aucun port ouvert
          ┌───────────────────────┼────────────────────────┐
     portable / PC           DGX Spark (GPU)          Mac Studio
   worker-ml (+ vlm 4B)   worker-ml + vlm 32B      worker-ml + vlm natif
```

Toute machine qui calcule est un **worker** : du portable à la grosse machine, c'est le même
`docker-compose.worker.yml`, seuls le modèle VLM et le GPU changent. Le serveur ne fait que coordonner.

## Serveur

Déployé par Coolify depuis `docker-compose.coolify.yml` (détails dans `docs/deploy.md`). Les photos s'ajoutent
depuis la page « Ajouter » (glisser-déposer, noms de fichiers conservés) ou en les déposant dans le dossier des
photos du serveur : le watcher les ingère sous une minute. Pour vendre : trier par potentiel de vente, cocher
des photos, puis page « Export » (fichier Easy Uploader pour Delcampe).

## Worker (n'importe quelle machine)

```bash
docker compose -f docker-compose.worker.yml up -d --build                     # physique, embeddings, visages, nudité
docker compose -f docker-compose.worker.yml --profile vlm up -d --build       # + VLM local (llama.cpp, Qwen3-VL 4B)
docker compose -f docker-compose.worker.yml --profile claude up -d --build    # + VLM Claude (clé API ou abonnement)
```

Puis page « Workers » du site : la machine apparaît en attente, « Approuver », puis « Analyser N photos ».
Options (serveur, nom, modèle, Claude…) dans un `.env` à côté, voir `.env.worker.example`.

Grosse machine :

```bash
# DGX Spark / Linux NVIDIA : VLM 32B sur GPU
docker compose -f docker-compose.worker.yml -f docker-compose.worker.gpu.yml --profile vlm up -d --build

# Mac Studio : llama-server natif (Metal), le worker Docker s'y connecte
brew install llama.cpp
llama-server -hf Qwen/Qwen3-VL-32B-Instruct-GGUF:Q8_0 --host 0.0.0.0 --port 8080 -c 16384 -np 2 --jinja
VLM_WORKERS=2 docker compose -f docker-compose.worker.yml --profile vlm-host up -d --build
```

Arrêter un VLM : `docker compose -f docker-compose.worker.yml stop worker-vlm llm` (ou `worker-claude`). Un
`up` sans le profil ne coupe pas les conteneurs déjà lancés.

## Développement (tout en local)

`docker-compose.dev.yml` lance une base, l'API, l'interface et des workers branchés directement sur la base.

```bash
cp .env.example .env            # PHOTOS_DIR = dossier de photos de test (défaut ./samples)
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml run --rm api photoflow ingest /photos
```

Puis http://localhost:3000 (pas de mot de passe en dev). La base est créée au démarrage de l'API. Au premier
lancement, `llm` télécharge Qwen3-VL 4B (~3 Go) et les workers SigLIP 2 et InsightFace (~1 Go).

Sans Docker :

```bash
cd backend && uv venv && uv pip install -e ".[ml]"
export DATABASE_URL=postgresql+psycopg://photoflow:photoflow@localhost:5432/photoflow
export PHOTOS_ROOT=/chemin/photos DATA_DIR=/chemin/data
photoflow serve --reload

cd web && bun install && bun run dev          # http://localhost:5173, API sur :8000
```

## Commandes utiles

À lancer dans un conteneur relié à la base (`docker compose -f docker-compose.dev.yml run --rm api …` en dev,
terminal du service `api` dans Coolify sur le serveur).

```bash
photoflow ingest /photos/dossier            # ingérer un dossier (sous PHOTOS_ROOT)
photoflow watch --interval 30               # surveiller PHOTOS_ROOT et ingérer ce qui apparaît
photoflow jobs stats                        # état de la file
photoflow jobs enqueue vlm --force          # remettre un extracteur en file sur tout le fonds
photoflow jobs retry-failed
photoflow workers list / create pc-remi / revoke pc-remi   # create = jeton fixe, sans approbation
photoflow faces cluster --min-cluster-size 3               # en dev ; sur le serveur, bouton de la page Workers
photoflow series build
photoflow vlm run --backend llama --limit 20               # tester un VLM sur 20 photos (dev)
photoflow claude-batch submit --limit 1000                 # API Batches Anthropic, moitié prix
photoflow claude-batch status
photoflow claude-batch collect msgbatch_xxx
photoflow vlm schema                                       # le JSON imposé au modèle
```
