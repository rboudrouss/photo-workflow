"""Analyse par modele vision-langage : titre, description, support, epoque, lieu, tags.

Le schema `PhotoAnalysis` est LE contrat : il sert de JSON schema impose au modele
(llama.cpp via response_format, Claude via output_config) et de validation au retour.
Backends : OpenAI-compatible (llama.cpp, vLLM, MLX, Ollama), Anthropic (cle API) et Claude Code (abonnement).
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Str200 = Annotated[str, StringConstraints(max_length=200)]

from ..config import settings
from ..images import open_image, resize_for_vlm
from ..persist import persist_caption  # noqa: F401  (re-export historique, utilise par claude_batch)
from .base import Extractor, PhotoRef

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- schema

class TypeObjet(str, Enum):
    photographie = "photographie"
    carte_postale = "carte_postale"
    chromo_image = "chromo_image"  # chromo, image publicitaire, carte a collectionner
    piece_monnaie = "piece_monnaie"
    medaille_jeton = "medaille_jeton"
    billet = "billet"
    document = "document"  # lettre, facture, ticket, carte d'identite...
    autre = "autre"


class Face(str, Enum):
    recto = "recto"
    verso = "verso"
    les_deux = "les_deux"
    inconnu = "inconnu"


class FaceMonnaie(str, Enum):
    avers = "avers"
    revers = "revers"
    les_deux = "les_deux"
    inconnu = "inconnu"


class Support(str, Enum):
    tirage_photo = "tirage_photo"
    carte_postale = "carte_postale"
    photo_studio = "photo_studio"
    negatif = "negatif"
    diapositive = "diapositive"
    photo_de_presse = "photo_de_presse"
    inconnu = "inconnu"


class Couleur(str, Enum):
    noir_et_blanc = "noir_et_blanc"
    sepia = "sepia"
    couleur = "couleur"
    colorise = "colorise"


class Scene(str, Enum):
    portrait = "portrait"
    groupe = "groupe"
    scene_de_rue = "scene_de_rue"
    paysage = "paysage"
    monument = "monument"
    interieur = "interieur"
    vehicule = "vehicule"
    militaire = "militaire"
    travail = "travail"
    evenement = "evenement"
    sport = "sport"
    loisir = "loisir"
    animal = "animal"
    autre = "autre"


class Confiance(str, Enum):
    faible = "faible"
    moyenne = "moyenne"
    forte = "forte"


_DECENNIE = re.compile(r"(1[0-9]{2}|20[0-2])[0-9]")


class Epoque(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 1000s a 2020s : les pieces de monnaie remontent bien avant la photographie (1760s, 1790s...).
    decennie: str = Field(pattern=r"^(1[0-9]{2}0s|20[0-2]0s|inconnue)$", description="Decennie estimee, UNE seule, format strict '1930s' (chiffres + s). Pour une piece, decennie de frappe, meme avant 1800. 'inconnue' si impossible. Jamais d'intervalle.")
    confiance: Confiance
    indices: list[Str200] = Field(max_length=8, description="Indices visuels utilises : vetements, vehicules, support, bords, format.")

    @field_validator("decennie", mode="before")
    @classmethod
    def _une_decennie(cls, v: Any) -> Any:
        """Sans grammaire (Claude), le format n'est pas garanti : '1920s-1930s' ou '1925' deviennent '1920s',
        ce qui est hors de 1000-2029 devient 'inconnue'."""
        if not isinstance(v, str) or v == "inconnue" or re.fullmatch(r"(1[0-9]{2}0s|20[0-2]0s)", v):
            return v
        m = _DECENNIE.search(v)
        return f"{m.group(0)[:3]}0s" if m else "inconnue"


class Lieu(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pays: str | None = Field(description="Pays probable, ou null.")
    region: str | None = Field(description="Region ou departement probable, ou null.")
    ville: str | None = Field(description="Ville ou commune si identifiable, ou null.")
    lieu_precis: str | None = Field(description="Monument, rue, plage, gare... si identifiable, ou null.")
    confiance: Confiance
    indices: list[Str200] = Field(max_length=8, description="Ce qui permet de le dire : architecture, enseigne, monument, panneau, plaque.")


class NiveauNudite(str, Enum):
    aucune = "aucune"
    suggestive = "suggestive"
    partielle = "partielle"
    integrale = "integrale"


class ContexteNudite(str, Enum):
    aucun = "aucun"
    plage_bain = "plage_bain"
    naturisme = "naturisme"
    artistique = "artistique"
    erotique = "erotique"
    medical_ethnographique = "medical_ethnographique"
    autre = "autre"


class Nudite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    niveau: NiveauNudite = Field(description="aucune : rien, y compris plage, maillot de bain, torse nu masculin, sous-vetements ordinaires. suggestive : pose ou tenue erotisee (lingerie, deshabille, pin-up) sans nudite visible. partielle : poitrine feminine ou fesses decouvertes. integrale : sexe visible.")
    contexte: ContexteNudite = Field(description="Contexte : plage_bain pour maillots et baignade (niveau aucune), naturisme, artistique (nu academique, studio), erotique (photo de charme), medical_ethnographique, aucun, autre.")
    explication: str = Field(max_length=300, description="Une phrase factuelle justifiant le niveau. Vide si aucune.")


class ObjetItem(BaseModel):
    """Un objet parmi plusieurs sur le meme scan (ex. cinq pieces sous etuis, trois chromos)."""

    model_config = ConfigDict(extra="forbid")
    type_objet: TypeObjet
    resume: str = Field(max_length=200, description="Une ligne : ce que c'est, avec les precisions lisibles (annee, valeur, legende).")
    texte: list[Str200] = Field(max_length=20, description="Texte lisible propre a cet objet (annotations manuscrites, legende imprimee).")


class CartePostale(BaseModel):
    model_config = ConfigDict(extra="forbid")
    editeur: str | None = Field(description="Editeur ou photographe imprime (ND Phot, LL, CAP, Combier...), ou null.")
    numero: str | None = Field(description="Numero de la carte dans la serie de l'editeur, ou null.")
    legende_imprimee: str | None = Field(max_length=300, description="Legende imprimee sur la carte, transcrite telle quelle, ou null.")
    carte_photo: bool = Field(description="true si c'est une carte-photo (vrai tirage argentique au format carte postale) plutot qu'une carte imprimee.")
    voyagee: bool | None = Field(description="true si timbre, cachet ou correspondance visibles ; false si vierge ; null si on ne voit pas le verso.")
    cachet_date: str | None = Field(description="Date lisible sur le cachet postal, ou null.")
    cachet_lieu: str | None = Field(description="Lieu lisible sur le cachet postal, ou null.")
    correspondance: str | None = Field(max_length=400, description="Resume en une phrase de la correspondance manuscrite si lisible, ou null.")


class Piece(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pays: str | None = Field(description="Pays ou autorite emettrice, ou null.")
    valeur_faciale: str | None = Field(description="Ex. '10 francs', '1 franc', '50 centimes', ou null.")
    annee: str | None = Field(description="Millesime lisible, ou null.")
    type_ou_graveur: str | None = Field(description="Type monetaire ou graveur : Semeuse, Turin, Morlon, Napoleon III, Ceres, Hercule..., ou null.")
    metal: str | None = Field(description="Metal suppose ou indique : argent, bronze, aluminium, nickel, cupro-nickel..., ou null.")
    atelier: str | None = Field(description="Atelier ou differents visibles (A, B, corne d'abondance...), ou null.")
    face_visible: FaceMonnaie
    annotations: list[Str200] = Field(max_length=20, description="Annotations manuscrites ou imprimees sur l'etui : periode, titre, etat (TTB, SUP...), teneur en metal.")
    etat_estime: str | None = Field(description="Etat de conservation estime d'apres l'image (B, TB, TTB, SUP, SPL, FDC), ou null.")
    nombre: int = Field(description="Nombre de pieces visibles sur le scan.")


class CategorieDelcampe(str, Enum):
    """Rubriques de Delcampe > Photographie > Photographies (originaux). Le numero Delcampe correspondant est
    dans delcampe.py ; les nus y sont ranges d'office par epoque, et les autres objets se choisissent a l'export."""

    lieux_europe = "lieux_europe"
    lieux_afrique = "lieux_afrique"
    lieux_amerique = "lieux_amerique"
    lieux_asie = "lieux_asie"
    lieux_oceanie = "lieux_oceanie"
    lieux = "lieux"
    personnes_anonymes = "personnes_anonymes"
    personnes_identifiees = "personnes_identifiees"
    celebrites = "celebrites"
    metiers = "metiers"
    militaire = "militaire"
    automobiles = "automobiles"
    aviation = "aviation"
    bateaux = "bateaux"
    trains = "trains"
    cyclisme = "cyclisme"
    sports = "sports"
    objets = "objets"
    ethnographie = "ethnographie"
    pin_up = "pin_up"
    nus = "nus"
    avant_1900 = "avant_1900"
    stereoscopie = "stereoscopie"
    autre = "autre"


class Potentiel(BaseModel):
    """Deux notes independantes pour choisir quoi publier en premier, sur Delcampe et sur Instagram."""

    model_config = ConfigDict(extra="forbid")
    vente: int = Field(ge=0, le=10, description="Potentiel de vente sur Delcampe, 0 a 10. Demande des collectionneurs (militaria, metiers, vehicules, scenes de rue localisables, evenements, photographe identifie, nus anciens), rarete du sujet, qualite et etat du tirage. 0-2 : sans valeur (verso, flou, portrait anonyme banal abime) ; 3-4 : courant ; 5-6 : interessant ; 7-8 : recherche ; 9-10 : exceptionnel. Sois exigeant, la plupart des photos sont entre 2 et 5.")
    vente_raison: str = Field(max_length=200, description="Une phrase : ce qui fait monter ou baisser la note de vente.")
    instagram: int = Field(ge=0, le=10, description="Interet visuel pour Instagram, 0 a 10, independant de la valeur marchande. Composition, lumiere, emotion, humour, etrangete, charme d'epoque, lisible en petit format. 0-2 : terne ou illisible ; 5 : agreable ; 8-10 : image forte qui arrete le defilement. Sois exigeant. 0 si nudite partielle ou integrale (interdite sur Instagram).")
    instagram_raison: str = Field(max_length=200, description="Une phrase : ce qui rend l'image forte ou faible visuellement.")

    @field_validator("vente", "instagram", mode="before")
    @classmethod
    def _borne(cls, v: Any) -> Any:
        """Les sorties structurees Claude ne portent pas les bornes : on ramene dans 0-10 au lieu d'echouer."""
        return max(0, min(10, v)) if isinstance(v, int) else v


class PhotoAnalysis(BaseModel):
    """Resultat structure d'une analyse de photo ancienne."""

    model_config = ConfigDict(extra="forbid")

    type_objet: TypeObjet = Field(description="Nature de l'objet principal scanne. photographie pour un tirage ; carte_postale ; chromo_image ; piece_monnaie ; medaille_jeton ; billet ; document ; autre.")
    face: Face = Field(description="recto = image ; verso = dos (papier vierge, marque du papier, tampons, ecriture) ; les_deux si le scan montre les deux.")
    nombre_objets: int = Field(description="Nombre d'objets distincts sur le scan (1 en general ; 5 pour cinq pieces sous etuis ; 3 pour trois chromos).")
    lot: list[ObjetItem] = Field(max_length=20, description="Un element par objet quand nombre_objets > 1, dans l'ordre de lecture (gauche a droite, haut en bas). Liste vide si un seul objet.")
    carte_postale: CartePostale | None = Field(description="Rempli si type_objet = carte_postale, sinon null.")
    piece: Piece | None = Field(description="Rempli si type_objet = piece_monnaie ou medaille_jeton, sinon null.")
    titre: str = Field(max_length=140, description="Titre de vente, francais, 40 a 80 caracteres, factuel, sans guillemets. Ex: 'Groupe de pecheurs devant leur barque, port breton, annees 1920' ; 'Lot de 5 x 1 franc Semeuse argent 1915-1919' ; 'Verso de tirage Kodak, tampon septembre 1988'.")
    description: str = Field(max_length=1500, description="Description de vente en francais, 3 a 6 phrases : sujet, composition, details notables, epoque et lieu supposes avec les reserves d'usage, etat.")
    support: Support
    couleur: Couleur
    scene: Scene = Field(description="Categorie principale de la scene.")
    tags: list[Str200] = Field(max_length=15, description="8 a 15 mots-cles francais, minuscules, utiles pour la recherche et la vente : sujets, objets, metiers, lieux, ambiance.")
    personnes: int = Field(description="Nombre de personnes visibles (0 si aucune, approximatif si foule).")
    objets: list[Str200] = Field(max_length=20, description="Objets identifiables avec precision quand possible : modele de voiture, type d'uniforme, outil, enseigne, mobilier.")
    texte_visible: list[Str200] = Field(max_length=30, description="Tout texte lisible sur l'image, transcrit tel quel (enseignes, panneaux, legendes, tampons). Liste vide si aucun.")
    epoque: Epoque
    lieu: Lieu
    etat: list[Str200] = Field(max_length=10, description="Defauts physiques visibles : taches, pliures, dechirures, jaunissement, coins abimes, rayures. Liste vide si bon etat.")
    potentiel: Potentiel
    categorie_delcampe: CategorieDelcampe = Field(description="Rubrique Delcampe des photographies originales : le sujet principal (metiers, militaire, automobiles...), sinon lieux_<continent> pour une vue de lieu localisable, personnes_anonymes pour un portrait ou un groupe sans sujet particulier, avant_1900 pour un tirage du XIXe sans autre rubrique, nus pour toute nudite partielle ou integrale. autre si rien ne convient ou si ce n'est pas une photographie.")
    incertitudes: list[Str200] = Field(max_length=10, description="Ce dont le modele n'est pas sur et qu'un humain devrait verifier.")
    nudite: Nudite

    @model_validator(mode="after")
    def _coherence(self) -> "PhotoAnalysis":
        """Les petits modeles remplissent des sous-fiches hors sujet : on nettoie."""
        if self.nombre_objets <= 1:
            self.lot = []
        if self.type_objet != TypeObjet.carte_postale:
            self.carte_postale = None
        if self.type_objet not in (TypeObjet.piece_monnaie, TypeObjet.medaille_jeton):
            self.piece = None
        if self.nudite.niveau in (NiveauNudite.partielle, NiveauNudite.integrale):
            self.potentiel.instagram = 0
        return self


SYSTEM_PROMPT = """Tu es un expert en photographie ancienne francaise (1860-1980) et en vente de photos de collection sur Delcampe.
On te montre un scan. Le plus souvent un tirage noir et blanc amateur ou de studio, recto seul, sans legende.
Mais environ un scan sur vingt est autre chose : carte postale ou carte-photo, chromo ou image a collectionner,
piece de monnaie ou medaille (souvent sous etui cartonne avec annotations manuscrites : periode, annee, metal,
etat), billet, document, ou le VERSO d'un tirage (papier vierge, marque du papier comme Kodak ou Agfa, numero
de tirage, tampon de date, ecriture). Un scan peut aussi contenir plusieurs objets a la fois.
Ta tache : identifier ce que c'est, extraire le maximum d'informations fiables et rediger un titre et une
description de vente en francais.

Regles :
- Sois factuel. Decris ce qui est visible. Ne jamais inventer un lieu ou une date : quand tu estimes, dis-le et donne tes indices.
- Pour l'epoque, appuie-toi sur les vetements, coiffures, vehicules, mobilier urbain, et sur le support lui-meme (bords denteles, marges, format, tonalite).
- Pour le lieu, appuie-toi sur l'architecture, les monuments, les enseignes, plaques et panneaux, la vegetation, le relief. Si rien ne permet de localiser, laisse null et confiance faible.
- Transcris tout texte lisible exactement, y compris les annotations manuscrites sur les etuis de pieces.
- Pieces et medailles : lis la legende, le millesime, la valeur, identifie le type (Semeuse, Turin, Morlon, Ceres,
  Napoleon III, Hercule, Marianne...) et le metal probable ; estime l'etat avec les sigles usuels (B, TB, TTB, SUP,
  SPL, FDC) en restant prudent ; compte les pieces. categorie_delcampe = autre.
- Cartes postales : editeur, numero, legende imprimee, carte-photo ou imprimee, voyagee ou non ; si on voit un
  cachet, date et lieu. Le lieu de la legende imprimee est une information forte pour `lieu`.
- Chromos et images : editeur ou marque, serie, legende.
- Verso : type_objet = photographie, face = verso, titre du type 'Verso de tirage ...', potentiel de vente 0 a 1, et
  transcris tout (marque du papier, numeros, tampons). Ne decris pas une scene qui n'existe pas.
- Plusieurs objets : nombre_objets > 1 et un resume par objet dans `lot` ; le titre parle du lot.
- Le titre doit etre vendeur mais honnete : sujet precis, lieu si connu, epoque estimee.
- La description doit aider un acheteur : sujet, details interessants, ce qui rend la photo rare, les reserves sur ce qui est incertain, l'etat.
- Potentiel : deux notes independantes. Une photo banale peut etre tres belle (instagram haut, vente bas) et une
  photo recherchee peut etre visuellement terne. Note avec exigence : la note moyenne du fonds est autour de 3-4.
- Nudite : le fonds contient des photos de charme et des nus anciens, il faut les classer correctement, sans pudeur ni exces. Maillots de bain, plage, baignade, torse nu masculin, enfants en tenue de bain, sous-vetements ordinaires = niveau "aucune". "suggestive" seulement si la pose ou la tenue est clairement erotisee. "partielle" si poitrine feminine ou fesses sont decouvertes. "integrale" si le sexe est visible. Ne jamais deduire une nudite d'une zone floue ou sombre.
- Reponds uniquement avec le JSON demande."""

USER_PROMPT = "Analyse cette photo et remplis tous les champs du schema."


def fit_to_schema(data: Any) -> Any:
    """Tronque listes et textes aux bornes du schema. Les sorties structurees Claude ne portent pas maxItems ni
    maxLength (json_schema(strip_lengths=True)) : un 31e texte lu sur une carte ne doit pas faire perdre l'analyse."""
    schema = PhotoAnalysis.model_json_schema()
    defs = schema.get("$defs", {})

    def walk(v: Any, node: dict) -> Any:
        if "$ref" in node:
            node = defs[node["$ref"].rsplit("/", 1)[-1]]
        if "anyOf" in node:
            alts = [a for a in node["anyOf"] if a.get("type") != "null"]
            node = alts[0] if alts and v is not None else node
            if "$ref" in node:
                node = defs[node["$ref"].rsplit("/", 1)[-1]]
        if isinstance(v, dict):
            props = node.get("properties", {})
            return {k: walk(x, props[k]) if k in props else x for k, x in v.items()}
        if isinstance(v, list):
            if node.get("maxItems") is not None:
                v = v[: node["maxItems"]]
            return [walk(x, node.get("items", {})) for x in v]
        if isinstance(v, str) and node.get("maxLength") is not None:
            return v[: node["maxLength"]]
        return v

    return walk(data, schema)


def parse_output(data: Any) -> PhotoAnalysis:
    """Valide la sortie d'un modele, apres l'avoir ramenee dans les bornes du schema."""
    return PhotoAnalysis.model_validate(fit_to_schema(data))


def json_schema(strip_lengths: bool = False) -> dict:
    """JSON schema impose au modele. strip_lengths retire maxLength/maxItems et les bornes numeriques (utiles a
    la grammaire llama.cpp, mais hors du sous-ensemble accepte par les sorties structurees Claude)."""
    schema = PhotoAnalysis.model_json_schema()
    if strip_lengths:
        def strip(node):
            if isinstance(node, dict):
                for k in ("maxLength", "minLength", "maxItems", "minItems", "pattern", "minimum", "maximum"):
                    node.pop(k, None)
                for v in node.values():
                    strip(v)
            elif isinstance(node, list):
                for v in node:
                    strip(v)
        strip(schema)
    return schema


# --------------------------------------------------------------------------- backends

class VLMBackend:
    model_name: str = "?"
    backend_name: str = "?"

    def analyze(self, jpeg: bytes) -> PhotoAnalysis:
        raise NotImplementedError

    @property
    def source(self) -> str:
        return f"vlm:{self.backend_name}:{self.model_name}"


def get_backend(name: str | None = None) -> VLMBackend:
    name = name or settings.vlm_backend
    if name == "llama":
        from .vlm_backends.openai_compat import OpenAICompatBackend

        return OpenAICompatBackend()
    if name == "anthropic":
        from .vlm_backends.anthropic_backend import AnthropicBackend

        return AnthropicBackend()
    if name == "claude-code":
        from .vlm_backends.claude_code import ClaudeCodeBackend

        return ClaudeCodeBackend()
    if name == "claude":
        # Cle API Console si presente (sorties structurees natives), sinon abonnement via CLAUDE_CODE_OAUTH_TOKEN.
        import os

        if settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"):
            return get_backend("anthropic")
        if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            return get_backend("claude-code")
        raise RuntimeError("VLM_BACKEND=claude : definir ANTHROPIC_API_KEY ou CLAUDE_CODE_OAUTH_TOKEN")
    raise KeyError(f"backend VLM inconnu: {name} (llama | anthropic | claude-code | claude)")


# --------------------------------------------------------------------------- extracteur

class VLMExtractor(Extractor):
    name = "vlm"
    # 2 : potentiel (vente, instagram) et categorie Delcampe fermee. Un worker en v2 reprend les photos analysees
    # en v1 par un modele de rang inferieur ou egal au sien (workers.py).
    version = 2
    batch_size = 1

    def __init__(self, backend: VLMBackend | None = None) -> None:
        self.backend = backend or get_backend()
        self.model_name = f"{self.backend.backend_name}:{self.backend.model_name}"

    def run(self, photos: list[PhotoRef]) -> list[dict[str, Any]]:
        out = []
        for p in photos:
            jpeg = resize_for_vlm(open_image(p.web))
            analysis = self.backend.analyze(jpeg)
            out.append({"source": self.backend.source, **analysis.model_dump(mode="json")})
        return out
