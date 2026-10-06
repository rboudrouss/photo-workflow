# Déploiement sur Coolify

Cible : `photoflow.rboud.com` sur le serveur Coolify (`coolify.rboud.com`, Traefik). Ce serveur est petit
et partagé : il coordonne, il ne calcule pas.

## Ce qui tourne sur le serveur

`docker-compose.coolify.yml` : Postgres + pgvector, l'API, le watcher et le site. Garanties :

- l'image backend est construite avec `ML=0` : ni torch, ni InsightFace, ni NudeNet. Un extracteur ne peut
  même pas s'importer, donc aucune analyse ne peut démarrer là, quelle que soit la commande lancée. Seul
  scikit-learn est présent, pour `faces cluster` et `series build` qui travaillent sur les vecteurs déjà en base ;
- pas de service worker ni llama.cpp dans le compose ;
- la recherche sémantique par texte, qui encoderait la requête avec SigLIP dans l'API, est désactivée
  (l'option disparaît de l'interface, l'API répond 400) ;
- limites mémoire par conteneur : 768 Mo Postgres, 512 Mo API, 256 Mo watcher, 256 Mo web.

Le calcul vient des machines perso (`docker-compose.worker.yml`), approuvées depuis la page Workers.

## Une seule origine, mot de passe dans l'application

Le site (Node) relaie `/api/*` et `/media/*` vers l'API interne (`web/src/hooks.server.ts`). Le navigateur ne
voit qu'un domaine, l'API n'a pas de domaine. Le même hook porte le mot de passe (Basic) avec `UI_USER` et
`UI_PASSWORD`, **sauf `/api/worker/*`**, réservé aux workers qui s'authentifient par jeton.

Pourquoi pas une authentification Traefik ? Elle couvrirait tout le domaine, workers compris : ils seraient
rejetés avant d'atteindre l'API. Si tu tiens à Traefik, il faut un second routeur sans middleware pour
`PathPrefix(\`/api/worker\`)` avec une priorité plus haute, par exemple dans les labels du service web :

```
traefik.http.routers.pfw-worker.rule=Host(`photoflow.rboud.com`) && PathPrefix(`/api/worker`)
traefik.http.routers.pfw-worker.entryPoints=https
traefik.http.routers.pfw-worker.tls=true
traefik.http.routers.pfw-worker.tls.certresolver=letsencrypt
traefik.http.routers.pfw-worker.priority=100
traefik.http.routers.pfw-worker.service=<service web genere par Coolify>
```

et laisser `UI_PASSWORD` vide. L'authentification dans l'application évite ça et donne le même résultat.

## Création dans Coolify

Pré-requis : le dépôt `rboudrouss/photo-workflow` est privé, Coolify doit y accéder via une GitHub App
(Sources) ou une clé de déploiement.

Dans l'interface : Projet → Nouvelle ressource → Docker Compose (GitHub App ou deploy key) → dépôt
`rboudrouss/photo-workflow`, branche `main`, fichier compose `/docker-compose.coolify.yml`. Puis :

1. domaine du service `web` : `https://photoflow.rboud.com` ;
2. variables : `PHOTOS_DIR` si le dossier surveillé n'est pas `/home/rboudrouss/photoflow/incoming`,
   `UI_USER` si autre que `photoflow`. `SERVICE_PASSWORD_UI` est généré par Coolify : c'est le mot de passe du
   site, à lire dans l'onglet variables ;
3. déployer. L'API crée ou met à jour le schéma à chaque démarrage, rien à lancer à la main.

Avec la CLI (`~/go/bin/coolify`, contexte à créer avec un jeton API de coolify.rboud.com) :

```bash
coolify context add rboud https://coolify.rboud.com <token> --default
coolify server list ; coolify project list ; coolify github list        # uuids
coolify app create github --server-uuid <srv> --project-uuid <proj> --environment-name production \
  --github-app-uuid <gh> --git-repository rboudrouss/photo-workflow --git-branch main \
  --build-pack dockercompose --ports-exposes 3000 --name photoflow \
  --compose-domain "web=https://photoflow.rboud.com"
# fichier compose : /docker-compose.coolify.yml (voir `coolify app update --help`, sinon dans l'interface)
coolify deploy ...
```

## Dossier surveillé

`PHOTOS_DIR` (défaut `/home/rboudrouss/photoflow/incoming`) est monté en lecture seule dans l'API et le
watcher. Tout fichier image qui y arrive (copyparty, rsync, scp) est ingéré sous une minute, nom conservé. Les
photos ajoutées depuis la page « Ajouter » vont dans le volume `photoflow-data`.

## Après le premier déploiement

- sur une machine perso : `docker compose -f docker-compose.worker.yml up -d --build`, puis « Approuver » sur
  la page Workers ;
- `faces cluster` et `series build` se lancent dans le terminal du conteneur `api` (Coolify → ressource →
  Terminal), ou en tâche planifiée Coolify sur ce conteneur : `photoflow faces cluster && photoflow series build`.
  Ordre de grandeur : quelques secondes pour mille photos, quelques minutes pour cent mille.
