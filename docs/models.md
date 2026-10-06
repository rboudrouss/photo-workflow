# Choix des modèles

Trois familles de modèles, trois contextes d'exécution. Les modèles spécialisés (embeddings, visages) sont
petits et tournent partout. Le modèle vision-langage (VLM) est le poste lourd et le vrai choix.

## Contextes

| Contexte | Matériel | Ce qui tourne | Objectif |
|---|---|---|---|
| dev (`docker-compose.dev.yml`) | ta machine : GTX 970 4 Go, 23 Go RAM, 16 cœurs | tout en CPU, VLM 4B | valider la tuyauterie, le schéma, le prompt, l'interface |
| prod (`docker-compose.yml`) | ≥ 128 Go unifiés, Spark ou Mac Studio | VLM 32B, SigLIP so400m | passe complète sur 100k photos |
| cloud (optionnel) | API Anthropic, clé Console | Claude | référence qualité sur un échantillon, ou passe complète si le local déçoit |

La GTX 970 (Maxwell) n'est plus supportée par les builds CUDA récents de PyTorch et onnxruntime. Le compose dev
n'essaie pas de l'utiliser. Si tu veux tenter : installer `onnxruntime-gpu` et un torch cu126, et `DEVICE=cuda`.

## VLM : titre, description, époque, lieu, tags

### Modèles locaux (llama.cpp, GGUF)

Tous via `llama-server`, qui expose une API OpenAI-compatible et impose le JSON schema par grammaire.
Fichiers GGUF officiels Qwen (modèle + projecteur vision `mmproj`), téléchargés automatiquement avec `-hf`.

| Modèle | RAM (Q4 / Q8) | Vitesse relative | Qualité | Pour |
|---|---|---|---|---|
| Qwen3-VL-2B-Instruct | 1,5 / 2,5 Go | très rapide | faible : descriptions génériques, époque souvent fausse | tests de tuyauterie uniquement |
| **Qwen3-VL-4B-Instruct** | 3 / 5 Go | rapide | correcte sur le sujet et la scène, faible sur époque et lieu | **dev sur ta machine** |
| Qwen3-VL-8B-Instruct | 5 / 9 Go | moyenne | nettement meilleure, lit bien le texte | dev si tu as la patience en CPU |
| **Qwen3-VL-30B-A3B-Instruct** (MoE) | 18 / 32 Go | rapide (3 Go actifs par token) | proche du 32B sur description, un peu en dessous en raisonnement | **prod si le débit prime** : 100k photos en quelques jours |
| **Qwen3-VL-32B-Instruct** (dense) | 19 / 35 Go | lente (tout le modèle par token) | la meilleure qualité qui tient confortablement en 128 Go | **prod si la qualité prime** |
| Qwen3-VL-235B-A22B-Instruct (MoE) | 130 / 250 Go | lente | la meilleure, mais ne tient qu'en 256 Go et en Q4 | seulement si la machine est en 256 Go |

Pros et cons de la famille Qwen3-VL : excellente lecture de texte, bon français, support natif du JSON contraint,
poids ouverts. Contre : tendance à surinterpréter le lieu quand on le pousse (le prompt demande explicitement
de laisser `null`), et datation moins fine qu'un modèle cloud.

Alternatives à considérer si Qwen déçoit sur un point précis : Gemma 3 27B (bon français, moins bon en OCR),
InternVL3, Mistral Small 3.x vision. Tous passent par la même interface OpenAI-compatible, donc sans code.

### Recommandation prod

Lancer les deux candidats sur les mêmes 300 photos et comparer dans l'interface (les deux légendes s'affichent
côte à côte) :

```
LLM_HF_REPO=Qwen/Qwen3-VL-30B-A3B-Instruct-GGUF:Q8_0   # puis
LLM_HF_REPO=Qwen/Qwen3-VL-32B-Instruct-GGUF:Q8_0
photoflow vlm run --backend llama --limit 300
```

Comme les sources sont nommées d'après le modèle, rien ne s'écrase.

### Claude (API Anthropic)

Via une clé API Console, facturée à l'usage. **Pas le token de l'abonnement claude.ai**, c'est contraire aux
conditions d'utilisation et les limites ne tiendraient pas.

| Modèle | Entrée / sortie ($ par M tokens) | 100k photos, API Batches | Pour |
|---|---|---|---|
| `claude-opus-5-5` | 4 / 20 | ~500 $ | meilleure datation et lecture d'indices, par défaut dans le code |
| `claude-sonnet-5-5` | 2 / 10 | ~250 $ | bon compromis, qualité proche sur des photos sans texte |
| `claude-haiku-4-5` | 1 / 5 | ~125 $ | correct, parfois approximatif |

Estimation à 1500 tokens d'entrée et 200 de sortie par photo, mode batch à moitié prix. À recaler sur un
échantillon réel : le log du worker affiche les tokens consommés à chaque appel.

Deux usages prévus :

```
photoflow vlm run --backend anthropic --limit 50                 # synchrone, pour comparer
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
| `google/siglip2-base-patch16-256` | 768 | dev | rapide en CPU, multilingue | recherche un peu moins fine |
| `google/siglip2-so400m-patch16-384` | 1152 | prod | meilleure qualité de recherche texte→image, toujours rapide | 2 à 3 fois plus lent que base |
| `jinaai/jina-clip-v2` | 1024 | alternative | très multilingue, bon sur le texte long | nécessite `trust_remote_code` |

Changer de modèle = changer `EMBEDDING_DIM`, recréer la base (ou la table `image_embeddings` et son index), puis
`photoflow jobs enqueue embedding --force`.

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
| llama.cpp | image Docker `server-cuda` publiée en amd64 seulement : construire l'image localement (`docker build -f .devops/cuda.Dockerfile --target server` dans le dépôt llama.cpp, base CUDA arm64) ou lancer `llama-server` en natif | Docker n'a pas accès au GPU : `brew install llama.cpp` puis `llama-server` natif, profil `mac` |
| Alternative plus rapide pour 100k photos | vLLM (meilleur débit par lots, même API) | MLX-VLM via `mlx_vlm.server` (même API) |
| Workers SigLIP / InsightFace | profil `cuda`, `DEVICE=cuda` | CPU dans Docker (suffisant), ou natif avec `DEVICE=mps` |

Commande native équivalente au service `llm` du compose :

```
llama-server -hf Qwen/Qwen3-VL-32B-Instruct-GGUF:Q8_0 --host 0.0.0.0 --port 8080 -c 16384 -np 2 --jinja -ngl 999 --flash-attn on
```

## Débit attendu, pour planifier

| Étape | dev (CPU) | prod |
|---|---|---|
| ingestion | 100k photos en ~8 h (dérivés JPEG) | 2 à 3 h |
| embeddings | ~6 h | < 1 h |
| visages | ~15 h | 1 à 2 h |
| VLM | impraticable au-delà de quelques centaines | 32B : 5 à 10 s/photo soit 6 à 12 jours ; 30B-A3B : 2 à 4 s/photo soit 2 à 5 jours ; parallélisme `-np` à ajuster |
