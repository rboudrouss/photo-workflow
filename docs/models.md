# Choix des modèles

Les modèles spécialisés (embeddings, visages, nudité) sont petits et tournent partout. Le modèle
vision-langage (VLM) est le poste lourd et le vrai choix. Toute machine qui calcule est un worker
(`docker-compose.worker.yml`) : seuls son VLM et son GPU changent.

## Contextes

| Worker | Matériel | VLM | Objectif |
|---|---|---|---|
| machine perso (profil `vlm`) | CPU, ~16 Go RAM | Qwen3-VL 4B Q4 | embeddings, visages, nudité sur tout le fonds ; quelques légendes |
| grosse machine, GPU NVIDIA (`docker-compose.worker.gpu.yml`, profil `vlm`) | DGX Spark, ≥ 128 Go unifiés | Qwen3-VL 32B ou 30B-A3B, Q8 | passe complète des légendes sur 100k photos |
| Mac Studio (profil `vlm-host`) | ≥ 128 Go unifiés, Metal | idem, llama-server natif | idem |
| Claude (profil `claude`) | aucun | Claude Opus / Sonnet | référence qualité, ou passe complète si le local déçoit |

Le compose de dev (`docker-compose.dev.yml`) fait tout en CPU avec le 4B pour valider la tuyauterie, le schéma,
le prompt et l'interface.

La GTX 970 (Maxwell) de la machine de dev n'est plus supportée par les builds CUDA récents de PyTorch et
onnxruntime : rester en `DEVICE=cpu`.

## VLM : titre, description, époque, lieu, tags

### Modèles locaux (llama.cpp, GGUF)

Tous via `llama-server`, qui expose une API OpenAI-compatible et impose le JSON schema par grammaire.
Fichiers GGUF officiels Qwen (modèle + projecteur vision `mmproj`), téléchargés automatiquement avec `-hf`.

| Modèle | RAM (Q4 / Q8) | Vitesse relative | Qualité | Pour |
|---|---|---|---|---|
| Qwen3-VL-2B-Instruct | 1,5 / 2,5 Go | très rapide | faible : descriptions génériques, époque souvent fausse | tests de tuyauterie uniquement |
| **Qwen3-VL-4B-Instruct** | 3 / 5 Go | rapide | correcte sur le sujet et la scène, faible sur époque et lieu | **dev sur ta machine** |
| Qwen3-VL-8B-Instruct | 5 / 9 Go | moyenne | nettement meilleure, lit bien le texte | dev si tu as la patience en CPU |
| **Qwen3-VL-30B-A3B-Instruct** (MoE) | 18 / 32 Go | rapide (3 Go actifs par token) | proche du 32B sur description, un peu en dessous en raisonnement | **grosse machine si le débit prime** : 100k photos en quelques jours |
| **Qwen3-VL-32B-Instruct** (dense) | 19 / 35 Go | lente (tout le modèle par token) | la meilleure qualité qui tient confortablement en 128 Go | **grosse machine si la qualité prime** |
| Qwen3-VL-235B-A22B-Instruct (MoE) | 130 / 250 Go | lente | la meilleure, mais ne tient qu'en 256 Go et en Q4 | seulement si la machine est en 256 Go |

Pros et cons de la famille Qwen3-VL : excellente lecture de texte, bon français, support natif du JSON contraint,
poids ouverts. Contre : tendance à surinterpréter le lieu quand on le pousse (le prompt demande explicitement
de laisser `null`), et datation moins fine qu'un modèle cloud.

Mesuré sur la machine de dev (CPU 16 cœurs, 4B Q4_K_M, image 896 px) : 40 s d'encodage image, puis 5 tokens
par seconde en génération pour 600 à 900 tokens de JSON, soit 3 à 4 minutes par scan. Le petit modèle part
parfois en boucle à l'intérieur d'une chaîne (légende de pièce répétée) jusqu'à `max_tokens` : le schéma borne
donc chaque chaîne et chaque liste (`maxLength`, `maxItems`, appliqués par la grammaire llama.cpp, retirés pour
Claude), avec une pénalité de répétition, et une sortie tronquée est une erreur explicite plutôt qu'un JSON
invalide retenté trois fois. La grammaire coûte environ 15 % de vitesse.

Alternatives à considérer si Qwen déçoit sur un point précis : Gemma 3 27B (bon français, moins bon en OCR),
InternVL3, Mistral Small 3.x vision. Tous passent par la même interface OpenAI-compatible, donc sans code.

### Recommandation grosse machine

Comparer les deux candidats sur les mêmes 300 photos avant la passe complète. `photoflow vlm run` prend toujours
les photos les plus anciennes du fonds : lancé depuis le compose de dev avec `LLM_BASE_URL` pointé sur le
llama-server de la grosse machine, il produit les deux légendes côte à côte dans l'interface.

```
LLM_HF_REPO=Qwen/Qwen3-VL-30B-A3B-Instruct-GGUF:Q8_0   # sur la grosse machine, puis
LLM_HF_REPO=Qwen/Qwen3-VL-32B-Instruct-GGUF:Q8_0
photoflow vlm run --backend llama --limit 300           # en dev, LLM_BASE_URL=http://<grosse-machine>:8080/v1
```

Comme les sources sont nommées d'après le modèle, rien ne s'écrase. Ensuite, la passe complète se lance depuis
la page Workers. Le rang se déduit du nom (30B-A3B → 30, 32B → 32) : un worker reprend les photos jamais
décrites, puis celles décrites par un modèle de rang inférieur, jamais l'inverse.

### Claude

Deux accès, au choix du worker `claude` (`.env.worker.example`) :

- **clé API Console** (`ANTHROPIC_API_KEY`), facturée à l'usage : appel direct, sorties structurées, API Batches
  possible. Prioritaire si les deux sont définis ;
- **abonnement Claude** (`CLAUDE_CODE_OAUTH_TOKEN`, créé avec `claude setup-token`) : chaque photo passe par le
  CLI Claude Code en mode non interactif (`claude -p`, sans outils, JSON imposé par `--json-schema`). Compte dans
  les limites d'usage de l'abonnement : bien pour quelques centaines de photos, pas pour 100k. Mesuré : 11 à
  14 s par photo avec Opus.

Coût à l'API :

| Modèle | Entrée / sortie ($ par M tokens) | 100k photos, API Batches | Pour |
|---|---|---|---|
| `claude-opus-5-5` | 4 / 20 | ~500 $ | meilleure datation et lecture d'indices, par défaut dans le code |
| `claude-sonnet-5-5` | 2 / 10 | ~250 $ | bon compromis, qualité proche sur des photos sans texte |
| `claude-haiku-4-5` | 1 / 5 | ~125 $ | correct, parfois approximatif |

Estimation à 1500 tokens d'entrée et 200 de sortie par photo, mode batch à moitié prix. À recaler sur un
échantillon réel : le log du worker affiche les tokens consommés à chaque appel.

Autres usages, depuis une machine reliée à la base :

```
photoflow vlm run --backend anthropic --limit 50                 # synchrone, pour comparer (dev)
photoflow claude-batch submit --limit 5000                       # asynchrone, moitié prix
photoflow claude-batch status
photoflow claude-batch collect msgbatch_xxx
```

Le code active par défaut le repli serveur en cas de refus par un classifieur de sécurité
(`ANTHROPIC_FALLBACKS=true`). Sur des photos anciennes le cas ne devrait jamais se présenter ; mettre à `false`
si l'API renvoie une erreur sur ce paramètre.

## Embeddings image et texte

| Modèle | Dims | Pour | Pros | Cons |
|---|---|---|---|---|
| `google/siglip2-base-patch16-256` | 768 | défaut du serveur | rapide en CPU, multilingue | recherche un peu moins fine |
| `google/siglip2-so400m-patch16-384` | 1152 | meilleure qualité | meilleure qualité de recherche texte→image, toujours rapide | 2 à 3 fois plus lent que base |
| `jinaai/jina-clip-v2` | 1024 | alternative | très multilingue, bon sur le texte long | nécessite `trust_remote_code` |

Le modèle est fixé sur le serveur (`EMBEDDING_MODEL` / `EMBEDDING_DIM` du service `api`, base 768 par défaut)
et imposé à chaque worker à sa connexion, pour que tous les vecteurs soient comparables. En changer = changer
les deux variables, recréer la table `image_embeddings` et son index, puis relancer `embedding` sur tout le
fonds depuis la page Workers.

## Visages

InsightFace `buffalo_l` est le choix standard : détecteur robuste, embeddings ArcFace bien séparés, âge et genre
en bonus. Pas de raison d'en changer avant d'avoir mesuré la qualité des clusters sur ton corpus. Si les
visages sont trop petits, monter `FACE_DET_SIZE` à 1024 (plus lent) et baisser `FACE_MIN_PX`.

## Nudité

NudeNet v3 (`nudenet`, ONNX, CPU suffisant). Choisi parce qu'il détecte des parties du corps et distingue
couvert / découvert, ce qui est le seul moyen de ne pas confondre maillot de bain et nu. Un classifieur global
« NSFW / safe » (ViT Falcons.ai, CLIP zero-shot) se trompe systématiquement sur la plage. Deux modèles :
320 (embarqué, par défaut) et 640 (`NUDITY_RESOLUTION=640`, plus précis, un peu plus lent). Entraîné sur
des images couleur modernes : sur du noir et blanc ancien il faut s'attendre à quelques faux négatifs sur les
nus flous et à des faux positifs sur des zones sombres, d'où les seuils par classe dans `nudity.py` et le
second signal donné par le VLM.

Côté cloud : un modèle Claude peut refuser une image explicite (`stop_reason: refusal`). Le job VLM échoue alors
avec un message clair, NudeNet a déjà classé la photo, et la légende peut être écrite à la main ou par le
modèle local, qui ne refuse pas grâce au JSON contraint.

## Spark ou Mac Studio : ce qui change

| | DGX Spark | Mac Studio |
|---|---|---|
| Architecture | arm64 + CUDA | arm64 + Metal |
| Worker | `-f docker-compose.worker.gpu.yml --profile vlm` | `--profile vlm-host`, `llama-server` natif sur l'hôte |
| llama.cpp | si l'image `server-cuda` n'existe pas en arm64 : la construire (`docker build -f .devops/cuda.Dockerfile --target server -t llama-server-cuda .` dans le dépôt llama.cpp) et `LLM_IMAGE=llama-server-cuda`, ou lancer `llama-server` en natif avec `--profile vlm-host` | Docker n'a pas accès au GPU : `brew install llama.cpp` puis `llama-server` natif |
| Alternative plus rapide pour 100k photos | vLLM (meilleur débit par lots, même API) | MLX-VLM via `mlx_vlm.server` (même API) |
| Workers SigLIP / InsightFace | `DEVICE=cuda` (surcouche GPU) | CPU dans Docker, suffisant |
| Parallélisme VLM | `VLM_WORKERS=2` : deux workers VLM et `-np 2` côté llama-server | `VLM_WORKERS=2` et `-np 2` sur le llama-server natif |

Commande native équivalente au service `llm` de la surcouche GPU :

```
llama-server -hf Qwen/Qwen3-VL-32B-Instruct-GGUF:Q8_0 --host 0.0.0.0 --port 8080 -c 16384 -np 2 --jinja -ngl 999 --flash-attn on
```

## Débit attendu, pour planifier

| Étape | machine perso (CPU) | grosse machine |
|---|---|---|
| ingestion | 100k photos en ~8 h (dérivés JPEG) | 2 à 3 h |
| embeddings | ~6 h | < 1 h |
| visages | ~15 h | 1 à 2 h |
| VLM | impraticable au-delà de quelques centaines | 32B : 5 à 10 s/photo soit 6 à 12 jours ; 30B-A3B : 2 à 4 s/photo soit 2 à 5 jours ; parallélisme `-np` à ajuster |
