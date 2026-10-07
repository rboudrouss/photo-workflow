# Publication sur Delcampe

## Les voies possibles

| Voie | Prix | Conditions | Verdict |
|---|---|---|---|
| Saisie manuelle | gratuit | un objet à la fois, formulaire web | pour valider les fiches sur quelques ventes |
| **Easy Uploader** (import Excel / CSV) | option **Store Plus**, 6,45 €/mois | tout vendeur, particulier compris, sans engagement | **la voie retenue** |
| API Pass | 19,95 €/mois | compte **professionnel** et **site marchand** obligatoires | hors de portée, et inutile ici : elle sert à synchroniser une boutique en ligne avec Delcampe |

Store Plus a remplacé Club+ Gold : mêmes outils (Easy Uploader, modifications en masse, export CSV des ventes,
référence perso), sans l'API. On peut le prendre le mois où l'on publie en masse puis le résilier.

À côté, l'abonnement boutique : gratuit jusqu'à 1 000 objets en vente en même temps, puis 5,95 €/mois jusqu'à
10 000. Delcampe ne prend ni frais d'insertion ni commission au vendeur ; l'acheteur paie 10 % + 0,30 €.

Sources (relues le 7 oct. 2026) : [Easy Uploader](https://www.delcampe-support.com/hc/en-gb/articles/10423758141458),
[format du fichier](https://www.delcampe-support.com/hc/en-gb/articles/10423590237714),
[Store Plus](https://www.delcampe-support.com/hc/en-gb/articles/19264970006546),
[API Pass](https://www.delcampe-support.com/hc/en-us/articles/19266728992786),
[tarifs](https://www.delcampe.net/en_GB/rates-eur.html),
[numéros de catégories](https://www.delcampe.net/en_GB/collectables/category-id/photography/photographs/).

## Comment marche Easy Uploader

- Un fichier Excel ou CSV (virgule, guillemets doubles), dont les noms de colonnes doivent être **exactement**
  ceux du modèle. Deux modèles existent : « basique » et « complet » ; photoflow produit le complet.
- Colonnes : `category_id` (numéro), `title` (120 caractères, sans HTML), `personal_reference` (20 caractères),
  `description` (HTML accepté), `selling_type` (`bid` ou `fixed_price`), `price`, `minimum_bid_step` (enchères),
  `initial_quantity` (prix fixe), `images`, `renew_duration` (7, 10, 14, 21 ou 28 jours), `renew_total_count`
  (0 à 5, 10, ou 99 = jusqu'à la vente), `sale_end_time`, `sale_end_day`, `shipping_model` (nom exact d'un
  modèle de frais créé sur Delcampe, ou `Free`), `weight`.
- Images : des URL en HTTPS que Delcampe télécharge, ou des fichiers à sélectionner pendant l'import. Une image
  injoignable fait sauter l'objet : un e-mail donne le nombre d'objets non publiés et un fichier à réimporter.
- Aperçu des 25 premiers objets avant validation. Au plus 10 000 objets mis en vente par 24 h.
- Une catégorie supprimée par Delcampe reste acceptée 6 mois, puis il faut passer à sa remplaçante.

## Ce que fait photoflow

1. **Les notes du VLM.** Chaque analyse donne un potentiel de vente et un potentiel Instagram (0 à 10, avec une
   phrase de raison) et une rubrique Delcampe. Dans la grille : tri « potentiel de vente », badges V/I sur les
   vignettes.
2. **La sélection.** On coche des photos dans la grille (Maj+clic pour une plage, « cocher la page », ou
   « cocher les N résultats » d'un filtre et d'un tri). Elle est gardée dans le navigateur ; le lien « Export »
   de la barre du haut donne son nombre.
3. **La page Export.** Options de vente communes (prix fixe ou enchère, prix, durée, remises, heure et jour de
   fin, modèle de frais de port), puis une ligne par photo : titre tel qu'il partira, référence, avertissements
   (titre raccourci, pas de description, verso, lot), rubrique et prix modifiables. Le bouton télécharge le
   fichier Excel (par défaut, sans souci d'encodage) ou CSV. Il reste grisé tant qu'une photo n'a pas de
   rubrique.
4. **Les liens publics.** Les images partent en URL `https://photoflow.rboud.com/pub/<jeton>.jpg` : jeton
   aléatoire de 256 bits, créé à l'export, sans mot de passe, image en 2400 px (`PUBLIC_IMAGE_SIZE`), marquée
   `noindex`. Rien d'autre du site n'est accessible sans mot de passe. Delcampe garde sa propre copie : une fois
   l'import confirmé par e-mail, on peut désactiver les liens depuis la page Export (sélection ou tous).
5. **La fiche à coller** reste disponible sur la page d'une photo (« Copier la fiche Delcampe »), pour la saisie
   manuelle.

### Rubriques

Le VLM choisit parmi les rubriques de Photographies (originaux) ; `delcampe.py` les convertit en numéros. Les nus
(nudité partielle ou intégrale, ou rubrique « nus ») vont d'office dans la rubrique dédiée de leur époque :
…-1920, 1921-1940, 1941-1960, 1960-…, ou « non classés » si l'époque est inconnue. Pour les analyses plus
anciennes, la rubrique est déduite de la scène.

Hors photographies (cartes postales, pièces, billets, chromos, documents), l'arbre de Delcampe est trop fin
(département, règne, marque) : la rubrique se choisit sur la page Export (« autre numéro… », numéros sur la
[liste des catégories](https://www.delcampe.net/en_GB/collectables/category-id)).

### Référence perso

`personal_reference` vaut `pf-` suivi du début de l'id de la photo. Taper cette référence dans la recherche de
photoflow retrouve la photo, par exemple depuis une vente ou l'export CSV des ventes de Store Plus.

## Mode d'emploi

1. Valider les fiches à la main sur 10 à 20 ventes (titres, descriptions, prix).
2. Prendre Store Plus, télécharger le modèle « complet » depuis la page Easy Uploader et vérifier que ses
   colonnes sont toujours celles de `delcampe.COLUMNS`.
3. Créer sur Delcampe le modèle de frais de port et reporter son nom exact dans les options d'export.
4. Exporter une petite sélection, l'importer (« Hosted from a web server »), vérifier l'aperçu et les ventes.
5. Exporter le reste par lots, trié par potentiel de vente. Désactiver les liens publics une fois les imports
   confirmés.

## Nudité

Les nus et photos de charme ont leur rubrique sur Delcampe : l'export les y range d'office d'après
`nudity_level`. Avant publication, filtrer avec « nudité seulement », relire chaque niveau (corriger sur la
page photo si besoin) et vérifier les règles en vigueur côté Delcampe pour ces rubriques.

## Reste à faire

- Un statut de publication par photo (à relire, exportée, publiée, vendue) pour ne jamais publier deux fois.
- Une politique de prix (par rubrique et potentiel, ou d'après des ventes comparables) ; aujourd'hui un prix
  par défaut et des prix à la ligne.
