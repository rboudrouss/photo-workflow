# Informations extraites : quoi, comment, où

Chaque photo ingérée passe par une série d'extracteurs indépendants. Ce document liste tout ce qui est
produit, par quel moyen, dans quelle table, et avec quelles limites.

## Vue d'ensemble

| Étape | Déclenchement | Modèle / méthode | Tables écrites | Coût indicatif (CPU dev) |
|---|---|---|---|---|
| Ingestion | page « Ajouter », watcher, `photoflow ingest` | Pillow, SHA-256, pHash | `photos` | < 0,5 s / photo |
| `physical` | job | calcul direct, sans modèle | `extractions` | < 0,05 s / photo |
| `embedding` | job | SigLIP 2 | `image_embeddings`, `extractions` | 0,1 à 0,3 s / photo |
| `faces` | job | InsightFace buffalo_l | `faces`, `extractions` | 0,3 à 1 s / photo |
| `nudity` | job | NudeNet (détecteur de parties du corps) | `photos.nudity_level`, `extractions` | 0,1 à 0,3 s / photo |
| `vlm` | job | Qwen3-VL (local) ou Claude (API ou abonnement) | `captions`, `extractions`, `photos.nudity_level` | 20 à 60 s / photo en CPU dev, 2 à 8 s sur grosse machine |
| Clustering visages | bouton de la page Workers (tâche `faces_cluster` sur un worker) | HDBSCAN (scikit-learn) | `faces.cluster_id`, `faces.person_id` | quelques secondes pour 100k visages |
| Séries | à la suite du clustering, sur le serveur (`series build`) | graphe kNN pgvector + visages + physique + pHash, composantes connexes | `series`, `series_edges`, `photos.series_id` | quelques minutes pour 100k photos |
| Correction humaine | interface | toi | `captions` (source `human`) | |

Tout résultat brut est aussi conservé en JSON dans `extractions` (clé `photo_id` + `extractor`, avec `version`
et `model`). Relancer un extracteur écrase sa ligne, les autres ne bougent pas.

## 1. Ingestion (`photos`)

| Champ | Comment | Usage |
|---|---|---|
| `sha256` | hash du fichier | identité, déduplication exacte, idempotence de l'ingestion |
| `rel_path`, `filename` | chemin relatif à `PHOTOS_ROOT` | les originaux ne sont jamais déplacés ni modifiés |
| `width`, `height`, `bytes`, `format` | Pillow, après correction d'orientation EXIF | affichage, choix original/web pour les visages |
| `phash` | pHash 64 bits (DCT 8×8) stocké en bigint signé | doublons proches : distance de Hamming via `bit_count(a # b)`, seuil 10 dans l'API |
| `exif` | tags EXIF scalaires | rarement présent sur des scans, conservé au cas où (date de scan, scanner) |
| dérivés | `DATA_DIR/derived/xx/<id>_thumb.jpg` (320 px) et `_web.jpg` (1600 px) | interface, entrée des modèles |

Les jobs des extracteurs listés dans `DEFAULT_EXTRACTORS` sont créés à ce moment.

## 1 bis. Signal physique du tirage (`extractions.physical`)

Calculé sans modèle sur le dérivé web. Quasi constant au sein d'une pellicule, et indice d'époque.

| Champ | Comment | Usage |
|---|---|---|
| `ratio`, `orientation` | grand côté / petit côté ; paysage, portrait, carré | compatibilité de format entre photos d'une série |
| `tonality` | `neutre` (saturation < 0,07 ou presque aucun pixel teinté), `sepia` (une seule teinte dominante entre 15° et 95°, écart-type circulaire des teintes < 25°), `monochrome_teinte` (une seule teinte, autre), `couleur` (plusieurs teintes). Un tirage N&B scanné en couleur est presque toujours jauni, donc la saturation seule ne suffit pas : c'est la dispersion des teintes (`hue_std`) qui sépare le monochrome teinté de la vraie couleur | série, époque (le sépia et le virage chaud sont plus anciens) ; sur l'échantillon réel : 77 sépia, 23 couleur, 10 neutre, 4 monochrome teinté |
| `margins`, `has_border` | largeur de marge claire sur chaque bord, en fraction du côté | marges blanches = tirage amateur d'une certaine époque ; série |
| `luminance`, `contrast`, `sharpness` | statistiques simples | qualité, tri, futur signal d'état |

Les bords dentelés ne sont pas détectés pour l'instant : trop dépendant du fond de scan.

## 2. Embeddings image (`image_embeddings`)

- **Modèle** : SigLIP 2 (`google/siglip2-base-patch16-256` par défaut, `so400m-patch16-384` possible, choisi sur le serveur). Encodeur
  image et encodeur texte dans le même espace, multilingue, donc une requête en français fonctionne.
- **Entrée** : le dérivé web (1600 px), redimensionné par le processeur du modèle.
- **Sortie** : un vecteur normalisé (768 ou 1152 dims) par photo. Index HNSW cosinus dans Postgres (pgvector).
- **Usages** :
  - recherche sémantique : `GET /api/photos?q=plage&mode=vector` encode la requête texte et fait un kNN ;
  - photos proches : `GET /api/photos/{id}` renvoie les 12 voisines (`similar`) ;
  - séries : les voisines très proches (score > 0,9) sont presque toujours la même pellicule ou la même séance.
- **Limites** : entraîné surtout sur des images couleur modernes. Fonctionne bien sur du noir et blanc pour les
  scènes et objets, moins pour les nuances d'époque. Changer de modèle impose de changer `EMBEDDING_DIM`,
  de recréer la table et de relancer `photoflow jobs enqueue embedding --force`.

## 3. Visages (`faces`)

- **Modèle** : InsightFace `buffalo_l` = détecteur RetinaFace + reconnaissance ArcFace (512 dims) + estimation
  âge et genre. Tourne sur onnxruntime (CPU, CUDA ou CoreML).
- **Entrée** : l'original si son grand côté fait au plus 2400 px, sinon le dérivé web. Les bbox sont toujours
  ramenées aux coordonnées de l'original.
- **Filtres** : score de détection ≥ `FACE_MIN_SCORE` (0,5), côté minimum `FACE_MIN_PX` (24 px). À ajuster :
  les photos de groupe anciennes ont des visages minuscules, mais en dessous de 24 px l'embedding ne vaut rien.
- **Champs** : `bbox` [x1, y1, x2, y2], `det_score`, `age`, `gender` (M/F, peu fiable sur photos anciennes),
  `embedding`, `cluster_id`, `person_id`.
- **Regroupement** : `photoflow faces cluster` lance HDBSCAN sur tous les embeddings (distance euclidienne sur
  vecteurs normalisés, équivalente au cosinus). `cluster_id = -1` : visage non regroupé. Les clusters qui
  contiennent déjà des visages nommés propagent le nom aux autres.
- **Nommage** : `PUT /api/faces/clusters/{id}/person` crée une `Person` et l'attache à tout le cluster.
- **Limites** : noir et blanc, flou, grain, visages de profil : attends-toi à des clusters incomplets plutôt qu'à
  des mélanges. Monter `min_cluster_size` réduit les faux regroupements. Relancer le clustering écrase les
  `cluster_id` mais pas les `person_id`.

## 4. Analyse VLM (`captions`, schéma `PhotoAnalysis`)

Le modèle reçoit l'image (JPEG, grand côté 1024 px) et un prompt système d'expert en photo ancienne française.
Il doit répondre dans un JSON imposé (grammaire côté llama.cpp, sorties structurées côté Claude). Schéma
complet : `photoflow vlm schema`. Code : `backend/src/photoflow/extractors/vlm.py`.

| Champ | Contenu | Comment c'est obtenu | Fiabilité attendue |
|---|---|---|---|
| `type_objet` | photographie, carte_postale, chromo_image, piece_monnaie, medaille_jeton, billet, document, autre | nature de l'objet scanné | bonne ; facette « tous objets » dans la grille |
| `face` | recto, verso, les_deux, inconnu | un verso = papier vierge, marque Kodak/Agfa, tampons, écriture | bonne |
| `nombre_objets`, `lot[]` | nombre d'objets sur le scan et un résumé par objet | scans à plusieurs pièces ou chromos | bonne |
| `carte_postale` | éditeur, numéro, légende imprimée, carte-photo ou imprimée, voyagée, cachet date et lieu, correspondance | lecture du recto et du verso quand visibles | bonne sur imprimé ; la légende imprimée alimente `lieu` |
| `piece` | pays, valeur faciale, année, type ou graveur (Semeuse, Turin…), métal, atelier, face visible, annotations de l'étui, état estimé, nombre | lecture de la pièce et des annotations manuscrites de l'étui | bonne sur valeur et millésime ; **l'état estimé d'après un scan reste indicatif** |
| `titre` | 40 à 80 caractères, français, factuel | rédaction par le modèle | bonne, à relire avant vente |
| `description` | 3 à 6 phrases de vente | idem | bonne |
| `support` | tirage_photo, carte_postale, photo_studio, négatif, diapositive, photo_de_presse, inconnu | aspect du support, bords, marges | moyenne |
| `couleur` | noir_et_blanc, sépia, couleur, colorisé | | très bonne |
| `scene` | portrait, groupe, scène_de_rue, paysage, monument, intérieur, véhicule, militaire, travail, événement, sport, loisir, animal, autre | | bonne ; sert de facette dans l'interface |
| `tags` | 8 à 15 mots-clés français | | bonne ; indexés en plein texte |
| `personnes` | nombre de personnes visibles | | bonne sauf foules |
| `objets` | objets identifiés avec précision (modèle de voiture, uniforme, outil) | | variable, précieux quand juste |
| `texte_visible` | transcription littérale de tout texte | OCR implicite du VLM | bonne sur imprimé, moyenne sur manuscrit |
| `epoque.decennie` | « 1930s » ou « inconnue » | vêtements, véhicules, support, bords dentelés | ± une décennie dans la majorité des cas |
| `epoque.confiance`, `epoque.indices` | justification | | lire les indices, c'est là que la valeur est |
| `lieu.*` | pays, région, ville, lieu précis, confiance, indices | architecture, monuments, enseignes, plaques | **faible** en général : sans texte ni monument, une région au mieux. Jamais une certitude |
| `etat` | défauts physiques visibles | | moyenne |
| `potentiel.vente` | 0 à 10 + une phrase de raison | demande des collectionneurs, rareté, état du tirage | indicatif, à calibrer avec tes ventes ; sert au tri « potentiel de vente » |
| `potentiel.instagram` | 0 à 10 + une phrase de raison | composition, lumière, émotion, charme d'époque ; 0 si nudité partielle ou intégrale | jugement esthétique, utile pour trier, pas pour décider seul ; tri « potentiel Instagram » |
| `categorie_delcampe` | rubrique de Delcampe > Photographies (originaux) : lieux par continent, personnes, métiers, militaire, véhicules, sports, pin-up, nus, avant 1900… | | bonne ; convertie en numéro Delcampe à l'export (`delcampe.py`), les nus rangés d'office par époque |
| `incertitudes` | ce qu'un humain doit vérifier | | utile pour prioriser la relecture |

Ces deux notes et la rubrique datent de la version 2 de l'extracteur (avant : `interet_vente` faible / moyenne /
forte et une catégorie en texte libre). Un worker en v2 reprend les photos analysées en v1 par un modèle de rang
inférieur ou égal au sien ; les notes affichées sont celles du meilleur modèle.

Une ligne `captions` par source : `vlm:llama:<modèle>`, `vlm:anthropic:<modèle>`, `human`. Toutes sont
conservées, ce qui permet de comparer deux modèles sur les mêmes photos dans l'interface. La légende `human`
prime partout (fiche, titre dans la grille, export).

## 4 bis. Nudité (`photos.nudity_level`)

Objectif : repérer les nus et photos de charme (catégorie à part sur Delcampe, à relire avant publication) sans
faux positifs sur la plage, les maillots de bain ou les torses nus masculins.

Quatre niveaux : `aucune`, `suggestive` (lingerie, pose érotisée, sans nudité visible), `partielle` (poitrine
féminine ou fesses découvertes), `integrale` (sexe visible). `NULL` = pas encore analysé.

Deux signaux indépendants, fusionnés par `nudity.py` :

| Signal | Comment | Ce qu'il voit bien | Ce qu'il rate |
|---|---|---|---|
| `nudity` (NudeNet) | détecteur ONNX de 18 classes de parties du corps, couvertes ou découvertes. On ne retient que sexe, anus, poitrine féminine et fesses **découverts**, avec un seuil par classe (plus haut pour le sexe). Ventre, aisselles, pieds, torse masculin, et toutes les classes « couvert » sont ignorés : c'est exactement une plage | nudité visible, même petite dans l'image | le suggestif (lingerie, pose), les nus de dos très flous ; faux positifs possibles sur des zones sombres de vieux tirages, d'où les seuils |
| `vlm` (champ `nudite`) | le VLM remplit `niveau`, `contexte` (plage_bain, naturisme, artistique, erotique, medical_ethnographique…) et `explication`, avec des consignes explicites : maillot, plage, baignade, torse masculin = `aucune` | le contexte et le suggestif, la distinction nu artistique / photo de charme | moins fiable sur petit modèle ; un modèle cloud peut refuser l'image, auquel cas seul NudeNet répond |

Règles de fusion : un niveau fixé par un humain (sélecteur sur la page photo) n'est jamais écrasé. Entre
extracteurs, on garde le niveau le plus haut ; un extracteur relancé peut corriger sa propre valeur.

Dans l'interface : filtre « sans nudité / nudité seulement », floutage des vignettes `partielle` et
`integrale` activé par défaut, désactivable. Le JSON complet des détections (classe, score, boîte ramenée aux
coordonnées de l'original) est dans `extractions.nudity`.

Pour calibrer : chercher `nudity=only`, corriger à la main, puis comparer `extractions.nudity` et la valeur humaine
pour ajuster les seuils dans `nudity.py`. Si trop de faux positifs persistent sur un type de photo, passer
`NUDITY_RESOLUTION=640` (modèle plus précis, téléchargé au premier usage).

## 5. Doublons et séries

- **Doublons quasi exacts** : pHash, distance de Hamming ≤ 10. Rescan du même tirage, même image à deux
  résolutions. Calcul en SQL, pas de table d'arêtes.
- **Même scène, prise différente** : voisins par embedding, score ≥ 0,9 environ. C'est aussi la base de la
  détection de séries (même pellicule).
- **Même personne** : visages partageant un `cluster_id` ou un `person_id`.

## 5 bis. Séries (`series`, `series_edges`, `photos.series_id`)

Une série = photos de la même pellicule ou de la même séance. Construction par `photoflow series build`,
code dans `series.py` :

1. **Candidates** : pour chaque photo, ses 10 voisines par embedding avec similarité ≥ 0,75 (une requête
   pgvector en jointure latérale, index HNSW).
2. **Score** = similarité, + 0,10 si les deux photos partagent un visage (même cluster ou même personne),
   + 0,05 si le signal physique concorde (même orientation, ratio à 3 % près, même tonalité, même présence de
   marge), − 0,05 par critère physique en désaccord (orientation, ratio à plus de 8 %, tonalité, donc jusqu'à
   − 0,15). Un doublon pHash (distance ≤ 10) vaut 1.
3. **Arête retenue** si score ≥ 0,85, puis composantes connexes. Une composante de plus de 150 photos est
   rescindée avec un seuil plus strict, pour éviter l'effet de chaîne où tout le fonds finit dans une seule série.
4. **Report** : une nouvelle composante reprend l'id et le nom de l'ancienne série si la moitié au moins de ses
   photos en venaient. Les noms survivent donc à une reconstruction.

Les raisons de chaque lien sont conservées dans `series_edges.reasons` et visibles en bas de la page d'une
série : c'est l'outil pour régler les seuils (`--candidate-sim`, `--edge-threshold`, `--max-size`).

**Propagation** : depuis la page d'une série, une décennie ou un lieu s'appliquent à la légende humaine de toutes
les photos, avec l'option « seulement si inconnu » qui respecte les valeurs déjà saisies à la main. Nommer un
groupe de visages se propage déjà par construction.

Limites : une série ne contient que des photos reliées par le graphe, donc deux moitiés d'une même pellicule aux
sujets très différents peuvent former deux séries. À l'inverse, deux photos très ressemblantes (similarité
≥ 0,95) restent reliées même avec deux critères physiques en désaccord : c'est voulu, à ce niveau il s'agit
presque toujours de la même scène. Les seuils par défaut sont conservateurs ; à calibrer sur un millier de
photos réelles en regardant les liens de score proche de 0,85, et `--max-size` est le garde-fou si une série
absorbe trop de photos.

## 6. Ce qui n'est pas encore extrait, par ordre d'intérêt

0. **Appariement recto / verso** : les versos existent dans le fonds (papier Kodak, tampons de date). Le verso d'un
   tirage suit en général son recto dans l'ordre de numérisation. Un appariement par ordre de fichier plus
   confirmation visuelle (dimensions identiques) permettrait de rattacher la date du tampon au recto.
0 bis. **Découpage des scans multi-objets** en fiches séparées (cinq pièces = cinq ventes possibles). Nécessite une
   détection des objets et un recadrage ; aujourd'hui le lot est décrit globalement.

1. **Géolocalisation par modèle dédié** (GeoCLIP ou équivalent) : une estimation pays/région indépendante du VLM,
   à fusionner avec `lieu`. Faible précision attendue sur du noir et blanc.
2. **Datation par modèle dédié** : un classifieur d'époque entraîné sur tes propres corrections. Possible dès
   qu'il y a quelques milliers de légendes `human` avec décennie.
3. **Estimation de prix** à partir de ventes comparables Delcampe. Nécessite un scraping de ventes réalisées.
4. **Détection de bords, format physique, orientation** : signal pour le support et l'époque, calculable sans
   modèle.
5. **OCR dédié** si des versos ou des légendes apparaissent un jour (PaddleOCR, ou le VLM suffit souvent).
