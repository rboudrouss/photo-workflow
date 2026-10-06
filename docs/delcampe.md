# Publication sur Delcampe

## Ce que Delcampe permet

- **Saisie manuelle** : formulaire web, un objet à la fois. Disponible à tous.
- **Easy Uploader** : import CSV ou Excel pour lister en masse. Réservé aux membres Club+ Gold.
- **API** : réservée aux vendeurs professionnels Club+ Gold ayant un site e-commerce synchronisé. Hors de portée
  pour l'instant.

Source : [Listing your items with Easy Uploader](https://www.delcampe.be/en_GB/help-center/article/10423758141458-listing-your-items-with-easy-uploader).

## Ce que fait photoflow aujourd'hui

1. **Fiche prête à coller** : sur la page d'une photo, « Copier la fiche Delcampe » met dans le presse-papier le
   titre, la description enrichie (époque et lieu estimés avec leur confiance, format, défauts), la catégorie
   suggérée et les tags. `GET /api/photos/{id}/fiche` pour la même chose en JSON.
2. **Export CSV** : `GET /api/export/delcampe.csv?ids=a,b,c` produit un CSV (séparateur `;`) avec titre,
   description, catégorie, tags et chemin de l'image. Les colonnes sont génériques : quand le compte Gold sera
   actif, télécharger le modèle Easy Uploader depuis l'espace vendeur et adapter `backend/src/photoflow/delcampe.py`
   pour produire exactement ses colonnes (et son arbre de catégories).

## Ordre de priorité

1. Valider la qualité des fiches à la main sur quelques dizaines de ventes.
2. Passer Club+ Gold quand le volume manuel devient pénible, brancher le CSV.
3. Une automatisation navigateur (Playwright) du formulaire est possible mais fragile et à vérifier contre les
   conditions d'utilisation. À ne considérer qu'en dernier recours.

## Nudité

Les nus et photos de charme relèvent d'une catégorie dédiée sur Delcampe et de règles spécifiques
(catégorie « adulte », visibilité restreinte). Avant publication, filtrer avec « nudité seulement » dans
l'interface, relire chaque niveau, et vérifier les règles en vigueur côté Delcampe pour cette catégorie.
Le niveau `nudity_level` est dans l'export CSV pour router vers la bonne catégorie.

## À faire avant la première vente en masse

- Mapper `categorie_delcampe` (texte libre du modèle) vers les catégories réelles de Delcampe.
- Décider d'une politique de prix : fixe par catégorie et intérêt, ou estimation à partir de ventes comparables.
- Ajouter un statut de publication par photo (à relire, validée, publiée, vendue) pour ne jamais publier deux fois.
