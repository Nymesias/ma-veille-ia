import csv
import fnmatch
import html
import imaplib
import json
import os
import re
import smtplib
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email import message_from_bytes, policy
from email.header import Header, decode_header, make_header
from email.mime.text import MIMEText
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import urljoin

import feedparser
import markdown
import requests
from bs4 import BeautifulSoup


MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
MISTRAL_DAILY_TOKEN_BUDGET = int(os.getenv("MISTRAL_DAILY_TOKEN_BUDGET", "50000"))
MISTRAL_MAX_INPUT_TOKENS = int(os.getenv("MISTRAL_MAX_INPUT_TOKENS", "6000"))
MISTRAL_MAX_OUTPUT_TOKENS = int(os.getenv("MISTRAL_MAX_OUTPUT_TOKENS", "900"))
MISTRAL_MAX_ARTICLES = int(os.getenv("MISTRAL_MAX_ARTICLES", "24"))
MISTRAL_MAX_ARTICLES_PAR_SOURCE = int(os.getenv("MISTRAL_MAX_ARTICLES_PAR_SOURCE", "4"))
MISTRAL_TOKEN_CHARS = int(os.getenv("MISTRAL_TOKEN_CHARS", "4"))
MISTRAL_ANALYSE_JURIDIQUE_MAX_ARTICLES = int(
    os.getenv("MISTRAL_ANALYSE_JURIDIQUE_MAX_ARTICLES", str(MISTRAL_MAX_ARTICLES))
)

DOSSIER_MD = "markdown"
FICHIER_SOURCES = "sources.csv"
FICHIER_NEWSLETTERS = "newsletters.csv"
FICHIER_LISTE_RSS = "liste_rss.json"
FICHIER_LISTE_MD = "liste_md.json"
FICHIER_LETTRES = "lettres_cour_cassation.json"
FICHIER_DECISIONS = "decisions_judilibre.json"
FICHIER_QUOTA_MISTRAL = ".mistral_quota.json"

JUDILIBRE_PUBLIC_URL = "https://www.courdecassation.fr/recherche-judilibre"
JURIDIQUE_ANALYSE_CATEGORIES = {
    "revues",
    "blogs",
    "institutions",
    "juridictions",
    "régulation",
}

COLLECTIONS_LETTRES = {
    "Lettre de la Cour": 2666,
    "Lettre de la première chambre civile": 15,
    "Lettre de la deuxième chambre civile": 170,
    "Lettre de la troisième chambre civile": 171,
    "Lettre de la chambre commerciale, financière et économique": 172,
    "Lettre de la chambre sociale": 16,
    "Lettre de la chambre criminelle": 173,
    "Lettre internationale": 3643,
}

MAINTENANT = datetime.now()
AUJOURDHUI = MAINTENANT.strftime("%Y-%m-%d")
HIER = (MAINTENANT - timedelta(days=1)).strftime("%Y-%m-%d")

os.makedirs(DOSSIER_MD, exist_ok=True)

SOUS_DOSSIERS_MD = {
    "synthese": "syntheses",
    "news": "news",
    "finance": "finance",
    "juridique-analyse": "juridique",
    "cour-de-cassation": "cour-cassation",
}


def estimer_tokens(texte):
    """Approximation prudente: un token vaut souvent 3 a 4 caracteres."""
    if not texte:
        return 0
    return max(1, (len(texte) + MISTRAL_TOKEN_CHARS - 1) // MISTRAL_TOKEN_CHARS)


def charger_quota_mistral():
    if not os.path.exists(FICHIER_QUOTA_MISTRAL):
        return {"date": AUJOURDHUI, "tokens": 0}
    try:
        with open(FICHIER_QUOTA_MISTRAL, encoding="utf-8") as fichier:
            quota = json.load(fichier)
    except (json.JSONDecodeError, OSError):
        return {"date": AUJOURDHUI, "tokens": 0}
    if quota.get("date") != AUJOURDHUI:
        return {"date": AUJOURDHUI, "tokens": 0}
    return {"date": AUJOURDHUI, "tokens": int(quota.get("tokens", 0))}


def enregistrer_quota_mistral(quota):
    with open(FICHIER_QUOTA_MISTRAL, "w", encoding="utf-8") as fichier:
        json.dump(quota, fichier, indent=2, ensure_ascii=False)


def consommer_quota_mistral(quota, tokens):
    quota["tokens"] = int(quota.get("tokens", 0)) + max(0, int(tokens))
    enregistrer_quota_mistral(quota)


def nettoyer_texte(valeur, limite=900):
    """Transforme le HTML d'un flux en texte court exploitable par le modèle."""
    texte = html.unescape(valeur or "")
    texte = re.sub(r"<[^>]+>", " ", texte)
    texte = re.sub(r"\s+", " ", texte).strip()
    # Certains serveurs annoncent un mauvais charset et produisent par exemple
    # "FÃ©vrier" ou "NÂ°". Réparer uniquement lorsque ces marqueurs sont présents.
    if any(marqueur in texte for marqueur in ("Ã", "Â", "â€")):
        for _ in range(2):
            try:
                corrige = texte.encode("cp1252").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                break
            if corrige == texte:
                break
            texte = corrige
    return texte[:limite]


def charger_sources():
    if not os.path.exists(FICHIER_SOURCES):
        return []
    with open(FICHIER_SOURCES, newline="", encoding="utf-8-sig") as fichier:
        sources = []
        urls_vues = {}
        for numero, source in enumerate(csv.DictReader(fichier), start=2):
            if source.get(None):
                raise ValueError(
                    f"Ligne {numero} invalide dans {FICHIER_SOURCES}: "
                    "une URL contenant une virgule doit être entourée de guillemets."
                )
            url = source.get("url", "").strip()
            if not url:
                continue
            cle_url = url.lower()
            if cle_url in urls_vues:
                print(
                    "Source ignoree car URL deja presente: "
                    f"ligne {numero} ({source.get('source', 'Source inconnue')}) "
                    f"duplique la ligne {urls_vues[cle_url]}."
                )
                continue
            urls_vues[cle_url] = numero
            sources.append(source)
        return sources


def charger_newsletters():
    """Charge les règles expéditeur/sujet utilisées pour classer les newsletters."""
    if not os.path.exists(FICHIER_NEWSLETTERS):
        return []
    with open(FICHIER_NEWSLETTERS, newline="", encoding="utf-8-sig") as fichier:
        regles = []
        for numero, regle in enumerate(csv.DictReader(fichier), start=2):
            if regle.get(None):
                raise ValueError(f"Ligne {numero} invalide dans {FICHIER_NEWSLETTERS}.")
            categorie = (regle.get("categorie") or "").strip().lower()
            source = (regle.get("source") or "").strip()
            expediteur = (regle.get("expediteur") or "").strip().lower()
            sujet = (regle.get("sujet") or "*").strip().lower()
            url_publique = (regle.get("url_publique") or "").strip()
            if url_publique and not url_publique.startswith(("https://", "http://")):
                raise ValueError(
                    f"Ligne {numero} invalide dans {FICHIER_NEWSLETTERS}: URL publique non sûre."
                )
            if categorie and source and expediteur:
                regles.append({"categorie": categorie, "source": source,
                               "expediteur": expediteur, "sujet": sujet or "*",
                               "url_publique": url_publique})
        return regles


def decoder_entete(valeur):
    try:
        return str(make_header(decode_header(valeur or ""))).strip()
    except (LookupError, UnicodeDecodeError):
        return str(valeur or "").strip()


def extraire_html_newsletter(message):
    morceaux = []
    parties = message.walk() if message.is_multipart() else (message,)
    for partie in parties:
        if partie.get_content_type() != "text/html":
            continue
        try:
            morceaux.append(partie.get_content())
        except (LookupError, UnicodeDecodeError):
            contenu = partie.get_payload(decode=True) or b""
            morceaux.append(contenu.decode(partie.get_content_charset() or "utf-8", "replace"))
    return "\n".join(morceaux)


MOTS_LIENS_TECHNIQUES = (
    "désabonner", "desabonner", "unsubscribe", "préférences", "preferences",
    "politique de confidentialité", "privacy", "voir dans le navigateur",
    "view in browser", "facebook", "instagram", "linkedin", "twitter",
)


def lien_principal_newsletter(contenu_html):
    """Choisit un lien éditorial et écarte les liens de gestion et réseaux sociaux."""
    page = BeautifulSoup(contenu_html or "", "html.parser")
    for lien in page.select("a[href]"):
        url = html.unescape(lien.get("href", "")).strip()
        libelle = nettoyer_texte(lien.get_text(" ", strip=True), 300).lower()
        comparaison = f"{libelle} {url.lower()}"
        if (url.startswith(("https://", "http://"))
                and not any(mot in comparaison for mot in MOTS_LIENS_TECHNIQUES)):
            return url
    return ""


def regle_pour_message(regles, adresse, sujet):
    adresse, sujet = adresse.lower(), sujet.lower()
    for regle in regles:
        motif = regle["expediteur"]
        correspond = adresse.endswith(motif) if motif.startswith("@") else fnmatch.fnmatch(adresse, motif)
        if correspond and fnmatch.fnmatch(sujet, regle["sujet"]):
            return regle
    return None


SUJETS_MESSAGES_PERSONNELS = (
    "confirmation", "confirmez", "confirm your", "activation", "activez",
    "création de compte", "creation de compte", "vérifiez votre", "verifiez votre",
    "verify your", "mot de passe", "password", "code de sécurité", "security code",
)


def est_message_personnel(sujet):
    sujet = sujet.lower()
    return any(motif in sujet for motif in SUJETS_MESSAGES_PERSONNELS)


def resume_public_newsletter(contenu_html):
    """Extrait uniquement des libellés éditoriaux, sans corps de mail personnalisé."""
    page = BeautifulSoup(contenu_html or "", "html.parser")
    elements = []
    for balise in page.select("h1, h2, h3, a[href]"):
        texte = nettoyer_texte(balise.get_text(" ", strip=True), 300)
        comparaison = texte.lower()
        if len(texte) < 8 or "@" in texte:
            continue
        if any(mot in comparaison for mot in MOTS_LIENS_TECHNIQUES):
            continue
        if any(mot in comparaison for mot in SUJETS_MESSAGES_PERSONNELS):
            continue
        if texte not in elements:
            elements.append(texte)
    return nettoyer_texte(" — ".join(elements), 900)


def collecter_newsletters(regles, cartes_precedentes):
    """Lit les messages récents sans les modifier et les convertit en cartes."""
    if not regles:
        return {}, []
    utilisateur = os.getenv("IMAP_USERNAME") or os.getenv("EMAIL_SENDER")
    mot_de_passe = os.getenv("IMAP_PASSWORD") or os.getenv("EMAIL_PASSWORD")
    if not utilisateur or not mot_de_passe:
        print("Variables IMAP/EMAIL manquantes: newsletters ignorées.")
        return {}, []
    hote = os.getenv("IMAP_HOST", "imap.bookmyname.com")
    port = int(os.getenv("IMAP_PORT", "993"))
    dossier = os.getenv("IMAP_FOLDER", "INBOX")
    jours = max(1, int(os.getenv("IMAP_LOOKBACK_DAYS", "30")))
    depuis = (MAINTENANT - timedelta(days=jours)).strftime("%d-%b-%Y")
    par_source, articles_hier = {}, []
    try:
        with imaplib.IMAP4_SSL(hote, port) as boite:
            boite.login(utilisateur.strip(), mot_de_passe.strip())
            statut, _ = boite.select(dossier, readonly=True)
            if statut != "OK":
                raise RuntimeError(f"dossier IMAP inaccessible: {dossier}")
            statut, resultat = boite.uid("search", None, "SINCE", depuis)
            if statut != "OK":
                raise RuntimeError("recherche IMAP impossible")
            for uid in reversed(resultat[0].split()):
                statut, donnees = boite.uid("fetch", uid, "(BODY.PEEK[])")
                if statut != "OK" or not donnees or not isinstance(donnees[0], tuple):
                    continue
                message = message_from_bytes(donnees[0][1], policy=policy.default)
                sujet = decoder_entete(message.get("Subject")) or "Newsletter sans titre"
                adresse = parseaddr(decoder_entete(message.get("From")))[1].lower()
                regle = regle_pour_message(regles, adresse, sujet)
                if not regle or est_message_personnel(sujet):
                    continue
                date = normaliser_date_publication(message.get("Date"))
                if not date:
                    continue
                contenu_html = extraire_html_newsletter(message)
                # Ne jamais publier les redirections contenues dans le courriel : elles
                # peuvent embarquer un identifiant de suivi ou ouvrir un espace personnel.
                lien = regle["url_publique"]
                cle = (regle["categorie"], regle["source"])
                par_source.setdefault(cle, []).append(
                    {"t": sujet, "l": lien, "d": date, "type": "newsletter"}
                )
                if date == HIER:
                    articles_hier.append({
                        "categorie": regle["categorie"], "source": regle["source"],
                        "titre": sujet, "lien": lien, "date": date,
                        "resume": resume_public_newsletter(contenu_html),
                        "type": "newsletter",
                    })
    except Exception as exc:
        print(f"Erreur de collecte IMAP: {exc}")

    data = {}
    for regle in regles:
        cle = (regle["categorie"], regle["source"])
        articles = par_source.get(cle, [])
        if articles:
            date_recente = max(article["d"] for article in articles)
            articles = [article for article in articles if article["d"] == date_recente]
        else:
            articles = cartes_precedentes.get(cle, [])
            articles = [
                {
                    **article,
                    "l": regle["url_publique"],
                    "type": "newsletter",
                }
                for article in articles
                if article.get("type") == "newsletter"
            ]
        sources = data.setdefault(regle["categorie"], [])
        if not any(source["nom_site"] == regle["source"] for source in sources):
            sources.append({"nom_site": regle["source"], "articles": articles})
    return data, articles_hier


def fusionner_newsletters(data_rss, data_newsletters):
    for categorie, sources in data_newsletters.items():
        existantes = data_rss.setdefault(categorie, [])
        par_nom = {source["nom_site"]: source for source in existantes}
        for source in sources:
            if source["nom_site"] in par_nom:
                par_nom[source["nom_site"]]["articles"].extend(source["articles"])
            else:
                existantes.append(source)


MOIS_FRANCAIS = {
    "janvier": 1,
    "fevrier": 2,
    "février": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "août": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
    "décembre": 12,
}


def normaliser_date_publication(valeur):
    """Convertit une date ISO, RFC 2822 ou française en AAAA-MM-JJ."""
    if not valeur:
        return ""
    texte = str(valeur).strip()
    correspondance = re.search(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)", texte)
    if correspondance:
        return correspondance.group(1)
    correspondance = re.search(
        r"(?<!\d)(\d{1,2})/(\d{1,2})/(20\d{2})(?!\d)", texte
    )
    if correspondance:
        try:
            return datetime(
                int(correspondance.group(3)),
                int(correspondance.group(2)),
                int(correspondance.group(1)),
            ).date().isoformat()
        except ValueError:
            return ""
    try:
        return parsedate_to_datetime(texte).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    correspondance = re.search(
        r"\b(\d{1,2}|1er)\s+([a-zéû]+)\s+(20\d{2})\b", texte.lower()
    )
    if not correspondance:
        return ""
    jour = 1 if correspondance.group(1) == "1er" else int(correspondance.group(1))
    mois = MOIS_FRANCAIS.get(correspondance.group(2))
    if not mois:
        return ""
    try:
        return datetime(int(correspondance.group(3)), mois, jour).date().isoformat()
    except ValueError:
        return ""


def requete_avec_reessais(url, entetes=None, timeout=30, tentatives=3):
    """Télécharge une page en réessayant les erreurs réseau et limitations temporaires."""
    derniere_erreur = None
    for tentative in range(tentatives):
        try:
            reponse = requests.get(url, headers=entetes or {}, timeout=timeout)
            reponse.raise_for_status()
            return reponse
        except requests.RequestException as exc:
            derniere_erreur = exc
            if tentative + 1 < tentatives:
                time.sleep(1.5 * (tentative + 1))
    raise derniere_erreur


def date_depuis_page(url, entetes=None, stricte=False):
    """Lit la date de publication declaree par la page officielle liee au flux."""
    if not url:
        return ""
    try:
        reponse = requete_avec_reessais(url, entetes, timeout=20)
        page = BeautifulSoup(reponse.text, "html.parser")
        selecteurs_publication = (
            'meta[property="article:published_time"]',
            'meta[itemprop="datePublished"]',
            'meta[name="DC.date"]',
            'time[pubdate]',
        )
        selecteurs_generiques = (
            'meta[name="date"]',
            'time[datetime]',
            'time[date]',
            '.date-enregistrement',
            '.datetime',
        )
        for selecteur in selecteurs_publication + (() if stricte else selecteurs_generiques):
            element = page.select_one(selecteur)
            if not element:
                continue
            valeur = (
                element.get("content")
                or element.get("datetime")
                or element.get("date")
                or element.get_text(" ", strip=True)
            )
            date = normaliser_date_publication(valeur)
            if date:
                return date
        # Les donnees structurees Schema.org sont frequentes sur les sites publics.
        correspondance = re.search(
            r'["\u2019]datePublished["\u2019]\s*:\s*["\u2019](\d{4}-\d{2}-\d{2})',
            reponse.text,
            re.I,
        )
        return correspondance.group(1) if correspondance else ""
    except Exception as exc:
        print(f"Date de page indisponible pour {url}: {exc}")
        return ""


def date_entree(entry, flux=None, entetes=None, consulter_page=True):
    """Retourne la date de publication de l'entrée ou celle de sa page.

    Ne jamais utiliser la date d'execution comme repli : cela ferait paraitre
    aujourd'hui des publications anciennes dont le flux omet la date par item.
    La date globale de mise à jour du flux est également exclue : elle ne
    représente pas la date de publication de chaque article.
    """
    for champ in ("published", "updated"):
        date = normaliser_date_publication(entry.get(champ))
        if date:
            return date

    for champ in ("published_parsed", "updated_parsed"):
        date_structuree = entry.get(champ)
        if date_structuree:
            return datetime(*date_structuree[:3]).strftime("%Y-%m-%d")

    if consulter_page:
        date_page = date_depuis_page(entry.get("link", ""), entetes)
        if date_page:
            return date_page

    return ""


def normaliser_entrees_google_aft(entrees):
    """Nettoie le flux Google Actualités limité au domaine officiel de l'AFT."""
    communiques = []
    for entree in entrees:
        titre = nettoyer_texte(entree.get("title", ""), 500)
        correspondance = re.match(
            r"(\d{1,2}/\d{1,2}/20\d{2})\s*:\s*(.+?)(?:\s+-\s+Agence France Trésor)?$",
            titre,
        )
        if not correspondance:
            continue
        communiques.append(
            {
                "title": correspondance.group(2).strip(),
                "link": entree.get("link", ""),
                "date_normalisee": normaliser_date_publication(
                    correspondance.group(1)
                ),
                "summary": entree.get("summary", ""),
            }
        )
    return communiques[:30]


def normaliser_entrees_google_acpr(entrees):
    """Nettoie le flux Google Actualités limité au domaine officiel de l'ACPR."""
    actualites = []
    for entree in entrees:
        actualite = dict(entree)
        actualite["title"] = re.sub(
            r"\s+-\s+Banque de France$",
            "",
            nettoyer_texte(entree.get("title", ""), 500),
        )
        actualites.append(actualite)
    return actualites[:30]


def normaliser_entrees_google_officiel(entrees):
    """Nettoie les flux Google limités à un domaine institutionnel officiel."""
    actualites = []
    for entree in entrees:
        actualite = dict(entree)
        titre = nettoyer_texte(entree.get("title", ""), 500)
        if titre.lower().startswith(("toutes nos actualités", "toutes les actualités")):
            continue
        actualite["title"] = re.sub(
            r"\s+-\s+(?:Arcom|Cour des comptes|INPI PIBD|Autorité des marchés financiers \(AMF\))$",
            "",
            titre,
        )
        actualites.append(actualite)
    return actualites[:30]


def charger_cartes_precedentes():
    """Charge les dernières cartes publiées pour résister aux pannes temporaires."""
    if not os.path.exists(FICHIER_LISTE_RSS):
        return {}
    try:
        with open(FICHIER_LISTE_RSS, encoding="utf-8") as fichier:
            data = json.load(fichier)
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        (categorie, source.get("nom_site", "")): source.get("articles", [])
        for categorie, sources in data.items()
        for source in sources
    }


def collecter_articles(sources):
    data_rss = {}
    articles_hier = []
    cartes_precedentes = charger_cartes_precedentes()

    for source in sources:
        categorie = source.get("categorie", "general").strip().lower()
        nom_source = source.get("source", "Source inconnue").strip()
        url = source.get("url", "").strip()
        type_source = (source.get("type") or "rss").strip().lower()
        if not url:
            continue

        articles_source = []
        try:
            entetes = {"User-Agent": "MaVeilleIA/1.0 (veille personnelle)"}
            reponse_flux = requete_avec_reessais(url, entetes, timeout=30)
            contenu_source = reponse_flux.content
            contenu_flux = contenu_source
            # Certains flux Drupal ajoutent des commentaires de débogage avant
            # la déclaration XML, ce que les parseurs stricts refusent.
            debut_xml = contenu_flux.find(b"<?xml")
            if debut_xml > 0:
                contenu_flux = contenu_flux[debut_xml:]
            flux = feedparser.parse(contenu_flux)
            if getattr(flux, "bozo", False) and not flux.entries:
                print(f"Avertissement flux {nom_source}: {flux.bozo_exception}")
            entrees = flux.entries[:30]
            if type_source == "rss-google-aft":
                entrees = normaliser_entrees_google_aft(entrees)
            elif type_source == "rss-google-acpr":
                entrees = normaliser_entrees_google_acpr(entrees)
            elif type_source == "rss-google-officiel":
                entrees = normaliser_entrees_google_officiel(entrees)
            if not entrees:
                print(f"Avertissement flux {nom_source}: aucune entrée exploitable.")

            articles_collectes = []
            for entry in entrees:
                article = {
                    "categorie": categorie,
                    "source": nom_source,
                    "titre": nettoyer_texte(entry.get("title", "Sans titre"), 300),
                    "lien": urljoin(url, entry.get("link", url)),
                    "date": entry.get("date_normalisee")
                    or date_entree(entry, flux, entetes, consulter_page=False),
                    "resume": nettoyer_texte(
                        entry.get("summary") or entry.get("description") or ""
                    ),
                }
                articles_collectes.append(article)

            articles_a_verifier = (
                [
                    article
                    for article in articles_collectes
                    if article["lien"]
                    and (
                        not article["date"]
                        or nom_source == "Me PICOVSCHI|Affaires"
                    )
                ]
                if not type_source.startswith(("html-", "rss-google-"))
                else []
            )
            if articles_a_verifier:
                with ThreadPoolExecutor(max_workers=min(4, len(articles_a_verifier))) as pool:
                    dates_pages = pool.map(
                        lambda article: date_depuis_page(
                            article["lien"], entetes, stricte=bool(article["date"])
                        ),
                        articles_a_verifier,
                    )
                    for article, date_page in zip(articles_a_verifier, dates_pages):
                        if date_page:
                            article["date"] = date_page

            articles_hier.extend(
                article for article in articles_collectes if article["date"] == HIER
            )

            dates_connues = [
                article["date"]
                for article in articles_collectes
                if re.match(r"\d{4}-\d{2}-\d{2}$", article.get("date", ""))
                and article["date"] <= HIER
            ]
            date_reference = max(dates_connues) if dates_connues else ""

            liens_vus = set()
            for article in articles_collectes:
                if article["date"] == date_reference:
                    cle_lien = article["lien"].split("#", 1)[0].rstrip("/").lower()
                    if cle_lien in liens_vus:
                        continue
                    liens_vus.add(cle_lien)
                    articles_source.append(
                        {"t": article["titre"], "l": article["lien"], "d": article["date"]}
                    )
        except Exception as exc:
            print(f"Erreur flux {nom_source}: {exc}")

        if not articles_source:
            articles_source = cartes_precedentes.get((categorie, nom_source), [])
            if articles_source:
                print(
                    f"Dernières cartes conservées pour {nom_source}: "
                    f"{len(articles_source)} article(s)."
                )

        data_rss.setdefault(categorie, []).append(
            {"nom_site": nom_source, "articles": articles_source}
        )

    return data_rss, articles_hier


def synchroniser_listes(data_rss):
    with open(FICHIER_LISTE_RSS, "w", encoding="utf-8") as fichier:
        json.dump(data_rss, fichier, indent=2, ensure_ascii=False)

    fichiers = []
    for dossier, _, noms in os.walk(DOSSIER_MD):
        for nom in noms:
            if not nom.endswith(".md"):
                continue
            chemin = os.path.join(dossier, nom)
            chemin_relatif = os.path.relpath(chemin, DOSSIER_MD).replace(os.sep, "/")
            correspondance = re.search(r"(\d{4}-\d{2}-\d{2})", nom)
            date_fichier = correspondance.group(1) if correspondance else AUJOURDHUI
            fichiers.append(
                {
                    "date_affichage": date_fichier,
                    "date_tri": date_fichier,
                    "nom_fichier": chemin_relatif,
                }
            )

    fichiers.sort(key=lambda item: (item["date_tri"], item["nom_fichier"]), reverse=True)
    with open(FICHIER_LISTE_MD, "w", encoding="utf-8") as fichier:
        json.dump(fichiers, fichier, indent=2, ensure_ascii=False)


MOIS_FRANCAIS = {
    "janvier": 1,
    "février": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
}


def date_lettre(article, titre):
    """Extrait une date ISO de la page, avec le mois du titre comme repli."""
    balise_temps = article.select_one("time[datetime]")
    if balise_temps:
        valeur = balise_temps.get("datetime", "")
        correspondance = re.search(r"\d{4}-\d{2}-\d{2}", valeur)
        if correspondance:
            return correspondance.group(0)

    texte = nettoyer_texte(article.get_text(" ", strip=True), 1000).lower()
    correspondance = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", texte)
    if correspondance:
        jour, mois, annee = map(int, correspondance.groups())
        try:
            return datetime(annee, mois, jour).strftime("%Y-%m-%d")
        except ValueError:
            pass

    texte_titre = titre.lower()
    mois_trouves = []
    for nom_mois, numero_mois in MOIS_FRANCAIS.items():
        for correspondance in re.finditer(rf"\b{nom_mois}\s+(\d{{4}})\b", texte_titre):
            mois_trouves.append((correspondance.start(), correspondance.group(1), numero_mois))
    if mois_trouves:
        # Pour "Janvier/Février 2026", la fin de période est la meilleure clé de tri.
        _, annee, numero_mois = max(mois_trouves, key=lambda item: item[0])
        return f"{annee}-{numero_mois:02d}-01"
    return ""


def resume_lettre(url, titre, collection, entetes):
    """Extrait un vrai resume de la page de la Lettre, sans reprendre son titre."""
    try:
        reponse = requests.get(url, headers=entetes, timeout=30)
        reponse.raise_for_status()
        page = BeautifulSoup(reponse.text, "html.parser")
    except Exception as exc:
        print(f"Erreur resume Lettre {url}: {exc}")
        return ""

    interdits = {nettoyer_texte(titre).casefold(), nettoyer_texte(collection).casefold()}
    candidats = []
    description = page.select_one('meta[name="description"], meta[property="og:description"]')
    if description:
        candidats.append(nettoyer_texte(description.get("content", ""), 700))
    candidats.extend(
        nettoyer_texte(element.get_text(" ", strip=True), 700)
        for element in page.select("main p, main .field--name-body li")
    )
    for candidat in candidats:
        normalise = candidat.casefold().strip(" .:-")
        if len(candidat) >= 80 and normalise not in interdits and normalise != titre.casefold():
            return candidat
    return ""


def collecter_lettres():
    """Collecte les dernières parutions des huit collections officielles."""
    base = "https://www.courdecassation.fr"
    lettres = []
    vus = set()
    entetes = {"User-Agent": "MaVeilleIA/1.0 (veille personnelle)"}

    for collection, identifiant in COLLECTIONS_LETTRES.items():
        try:
            reponse = requests.get(
                f"{base}/publications",
                params={
                    "date_du": "",
                    "date_au": "",
                    "field_type[0]": identifiant,
                    "items_per_page": 10,
                    "sort_bef_combine": "created_DESC",
                },
                headers=entetes,
                timeout=30,
            )
            reponse.raise_for_status()
            page = BeautifulSoup(reponse.text, "html.parser")
            # La page est triee par creation decroissante : une seule parution,
            # la plus recente, doit etre conservee pour chaque collection.
            for article in page.select("main article"):
                lien = article.select_one("h2 a[href], h3 a[href]")
                if not lien:
                    continue
                url = requests.compat.urljoin(base, lien.get("href"))
                if url in vus:
                    continue
                vus.add(url)
                titre = nettoyer_texte(lien.get_text(" ", strip=True), 250)
                lettres.append(
                    {
                        "collection": collection,
                        "titre": titre,
                        "resume": resume_lettre(url, titre, collection, entetes),
                        "url": url,
                        "date": date_lettre(article, titre),
                    }
                )
                break
        except Exception as exc:
            print(f"Erreur collecte {collection}: {exc}")

    lettres.sort(key=lambda lettre: lettre.get("date", ""), reverse=True)
    if lettres:
        with open(FICHIER_LETTRES, "w", encoding="utf-8") as fichier:
            json.dump(
                {"mis_a_jour": MAINTENANT.isoformat(timespec="seconds"), "lettres": lettres},
                fichier,
                indent=2,
                ensure_ascii=False,
            )
        print(f"{len(lettres)} parutions de Lettres collectées.")
    return lettres


def valeur_liste(valeur):
    if isinstance(valeur, list):
        return ", ".join(str(item) for item in valeur if item)
    return valeur or ""


def sources_analyse_cassation(lettres, decisions):
    """Convertit Lettres et décisions en sources factuelles pour Mistral."""
    sources = []
    for lettre in lettres[:16]:
        sources.append(
            {
                "categorie": "lettres de la cour de cassation",
                "source": lettre.get("collection", "Cour de cassation"),
                "titre": lettre.get("titre", "Lettre de la Cour de cassation"),
                "lien": lettre.get("url", ""),
                "date": lettre.get("date") or AUJOURDHUI,
                "resume": lettre.get("resume", ""),
            }
        )
    for decision in decisions[:30]:
        details = []
        if decision.get("numero"):
            details.append(f"Pourvoi n° {decision['numero']}")
        if decision.get("solution"):
            details.append(f"Solution : {decision['solution']}")
        if decision.get("publication"):
            details.append(f"Publication : {decision['publication']}")
        if decision.get("sommaire"):
            details.append(decision["sommaire"])
        sources.append(
            {
                "categorie": "décisions judilibre",
                "source": decision.get("chambre") or "Cour de cassation",
                "titre": " — ".join(details[:2]) or "Décision Judilibre",
                "lien": decision.get("url", ""),
                "date": decision.get("date", ""),
                "resume": " ".join(details[2:]),
            }
        )
    return sources


def collecter_decisions_judilibre_pour_date(date_cible, base, entetes):
    """Collecte les décisions Judilibre publiées pour une date donnée."""
    decisions = []
    identifiants = set()

    def extraire_articles(contenu):
        soupe = BeautifulSoup(contenu, "html.parser")
        return soupe.select("article.decision-item-article")

    for page in range(10):
        params = {
            "date_du": date_cible,
            "date_au": date_cible,
            "judilibre_juridiction": "cc",
            "sort": "date-desc",
            "items_per_page": 30,
            "page": page,
        }
        reponse = requests.get(
            JUDILIBRE_PUBLIC_URL,
            params=params,
            headers=entetes,
            timeout=45,
        )
        reponse.raise_for_status()
        articles = extraire_articles(reponse.text)
        if not articles:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as moteur:
                navigateur = moteur.chromium.launch(headless=True)
                page_web = navigateur.new_page(user_agent=entetes["User-Agent"])
                page_web.goto(
                    reponse.url,
                    wait_until="domcontentloaded",
                    timeout=90000,
                )
                page_web.wait_for_function(
                    """() => document.querySelector('article.decision-item-article')
                    || document.body.innerText.includes('Aucun résultat')""",
                    timeout=90000,
                )
                articles = extraire_articles(page_web.content())
                navigateur.close()

        if not articles:
            break

        for article in articles:
            lien = article.select_one('a[href*="/decision/"]')
            entete = article.select_one(".decision-item--header h3")
            if not lien or not entete:
                continue
            url = urljoin(base, lien.get("href", "")).split("?", 1)[0]
            correspondance_id = re.search(r"/decision/([^/?]+)", url)
            identifiant = correspondance_id.group(1) if correspondance_id else url
            if identifiant in identifiants:
                continue
            identifiants.add(identifiant)

            texte_entete = nettoyer_texte(entete.get_text(" ", strip=True), 300)
            correspondance = re.search(
                r"(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4}).*?Pourvoi\s+n[°º]\s*([^\s]+)",
                texte_entete,
                re.IGNORECASE,
            )
            date_decision = ""
            numero = ""
            if correspondance:
                jour, mois, annee, numero = correspondance.groups()
                numero_mois = MOIS_FRANCAIS.get(mois.lower())
                if numero_mois:
                    date_decision = f"{annee}-{numero_mois:02d}-{int(jour):02d}"

            secondaires = article.select(".decision-item-header--secondary")
            chambre_formation = nettoyer_texte(
                next((p.get_text(" ", strip=True) for p in secondaires if "solution" not in p.get("class", [])), ""),
                250,
            )
            chambre, _, formation = chambre_formation.partition(" - ")
            decisions.append(
                {
                    "id": identifiant,
                    "date": date_decision,
                    "chambre": chambre,
                    "formation": formation,
                    "numero": numero,
                    "solution": nettoyer_texte(
                        article.select_one(".solution").get_text(" ", strip=True)
                        if article.select_one(".solution") else "",
                        100,
                    ),
                    "publication": nettoyer_texte(
                        article.select_one(".decision-item-header--large").get_text(" ", strip=True)
                        if article.select_one(".decision-item-header--large") else "",
                        200,
                    ),
                    "sommaire": nettoyer_texte(
                        article.select_one(".decision-summary").get_text(" ", strip=True)
                        if article.select_one(".decision-summary") else "",
                        700,
                    ),
                    "url": url,
                }
            )
        if len(articles) < 30:
            break

    decisions.sort(key=lambda item: item["date"], reverse=True)
    return decisions


def collecter_decisions_judilibre():
    """Collecte les dernières décisions depuis la page publique Judilibre."""
    base = "https://www.courdecassation.fr"
    entetes = {"User-Agent": "Mozilla/5.0 (veille-juridique; contact local)"}
    decisions = []
    date_retenue = HIER

    try:
        for recul in range(8):
            date_cible = (MAINTENANT - timedelta(days=1 + recul)).strftime("%Y-%m-%d")
            decisions = collecter_decisions_judilibre_pour_date(date_cible, base, entetes)
            date_retenue = date_cible
            if decisions:
                break
    except Exception as exc:
        raise RuntimeError(f"Erreur page publique Judilibre: {exc}") from exc

    with open(FICHIER_DECISIONS, "w", encoding="utf-8") as fichier:
        json.dump(
            {
                "mis_a_jour": MAINTENANT.isoformat(timespec="seconds"),
                "date_cible": date_retenue,
                "date_recherche_initiale": HIER,
                "total": len(decisions),
                "source": JUDILIBRE_PUBLIC_URL,
                "decisions": decisions,
            },
            fichier,
            indent=2,
            ensure_ascii=False,
        )
    if date_retenue == HIER:
        print(f"{len(decisions)} décisions Judilibre collectées pour le {HIER}.")
    else:
        print(
            f"{len(decisions)} décisions Judilibre collectées pour le {date_retenue} "
            f"(aucune pour le {HIER})."
        )
    return decisions


def articles_pour(cible, articles):
    if cible == "news":
        return [article for article in articles if article["categorie"] == "news"]
    if cible == "finance":
        mots_cles = (
            "marché", "bourse", "action", "obligation", "taux", "inflation",
            "dette", "déficit", "budget", "finances publiques", "comptes publics",
            "économie", "croissance", "récession", "emploi", "chômage", "pib",
            "banque", "crédit", "monétaire", "amf", "acpr", "bce", "régulation",
        )
        selection = []
        for article in articles:
            texte = " ".join(
                (article["source"], article["titre"], article["resume"])
            ).lower()
            if article["categorie"] == "finance" or any(mot in texte for mot in mots_cles):
                selection.append(article)
        return selection
    if cible == "cour-de-cassation":
        return [
            article
            for article in articles
            if "cour de cassation" in article["source"].lower()
            or "cour de cassation" in article["titre"].lower()
        ]
    return articles


def texte_article(article):
    return " ".join(
        str(article.get(champ, ""))
        for champ in ("categorie", "source", "titre", "resume")
    ).lower()


def selection_analyse_juridique(articles, sources_cassation, analyse_cassation=None):
    """Prepare une selection juridique compacte et equilibree pour Mistral."""
    articles_juridiques = [
        article
        for article in articles
        if article.get("categorie") in JURIDIQUE_ANALYSE_CATEGORIES
    ]
    if analyse_cassation:
        cassation = [{**analyse_cassation, "categorie": "cour de cassation"}]
    else:
        cassation = [
            {**source, "categorie": "cour de cassation"}
            for source in sources_cassation
        ]
    candidats = [
        article
        for article in articles_juridiques + cassation
        if article.get("date") == HIER
    ]

    def priorite(article):
        texte = texte_article(article)
        if "conseil d'etat" in texte or "conseil d’état" in texte:
            return 0
        if "cour de cassation" in texte:
            return 1
        ordre_categories = {
            "juridictions": 2,
            "régulation": 3,
            "institutions": 4,
            "revues": 5,
            "blogs": 6,
            "cour de cassation": 1,
        }
        return ordre_categories.get(article.get("categorie"), 9)

    candidats.sort(key=lambda article: (priorite(article), article.get("source", "")))

    selection = []
    liens_vus = set()
    sources_vues = set()

    def ajouter(article):
        if len(selection) >= MISTRAL_ANALYSE_JURIDIQUE_MAX_ARTICLES:
            return
        cle = article.get("lien") or (
            article.get("source", ""),
            article.get("titre", ""),
        )
        if cle in liens_vus:
            return
        liens_vus.add(cle)
        sources_vues.add(article.get("source", ""))
        selection.append(article)

    for categorie in (
        "institutions",
        "revues",
        "blogs",
        "juridictions",
        "régulation",
        "cour de cassation",
    ):
        for article in candidats:
            if article.get("categorie") == categorie:
                ajouter(article)
                break

    for article in candidats:
        if (
            article.get("source", "") in sources_vues
            and len(selection) < len(JURIDIQUE_ANALYSE_CATEGORIES)
        ):
            continue
        ajouter(article)

    return selection


def source_depuis_markdown(cible, contenu):
    titres = {
        "juridique-analyse": "Actualités Juridiques",
        "cour-de-cassation": "Analyse Cour de cassation",
    }
    return {
        "categorie": "analyse ia",
        "source": titres.get(cible, cible),
        "titre": titres.get(cible, cible),
        "lien": f"markdown/{SOUS_DOSSIERS_MD[cible]}/{AUJOURDHUI}-{cible}.md",
        "date": HIER,
        "resume": nettoyer_texte(contenu, 1800),
    }


def sources_synthese_generale(articles, analyse_juridique=None, analyse_cassation=None):
    """Utilise une synthese juridique compacte pour limiter le prompt global."""
    sources = [
        article
        for article in articles
        if article.get("categorie") not in JURIDIQUE_ANALYSE_CATEGORIES
    ]
    if analyse_juridique:
        sources.append(analyse_juridique)
    if analyse_cassation:
        sources.append(analyse_cassation)
    return sources


def donnees_prompt(articles):
    blocs = []
    for numero, article in enumerate(articles, start=1):
        bloc = (
            f"[{numero}] Categorie: {article['categorie']}\n"
            f"Source: {article['source']}\n"
            f"Titre: {article['titre']}\n"
            f"URL: {article['lien']}"
        )
        if article["resume"]:
            bloc += f"\nExtrait du flux: {article['resume']}"
        blocs.append(bloc)
    return "\n\n".join(blocs)


def diversifier_articles(articles):
    """Intercale les sources pour eviter qu'un seul flux occupe tout le prompt."""
    groupes = []
    positions = {}
    plafond = max(1, MISTRAL_MAX_ARTICLES_PAR_SOURCE)
    compteurs = {}

    for article in articles:
        source = article.get("source", "Source inconnue")
        if compteurs.get(source, 0) >= plafond:
            continue
        compteurs[source] = compteurs.get(source, 0) + 1
        if source not in positions:
            positions[source] = len(groupes)
            groupes.append([])
        groupes[positions[source]].append(article)

    selection = []
    profondeur = 0
    while len(selection) < MISTRAL_MAX_ARTICLES:
        progression = False
        for groupe in groupes:
            if profondeur < len(groupe):
                selection.append(groupe[profondeur])
                progression = True
                if len(selection) >= MISTRAL_MAX_ARTICLES:
                    break
        if not progression:
            break
        profondeur += 1
    return selection


def prompt_pour(cible, articles):
    titres = {
        "synthese": f"Synthèse de veille du {HIER}",
        "news": f"Actualités générales — {HIER}",
        "finance": f"Finance et économie — {HIER}",
        "cour-de-cassation": f"Cour de cassation — {HIER}",
        "juridique-analyse": f"Actualités Juridiques — {HIER}",
    }
    specificites = {
        "synthese": (
            "Organise obligatoirement le mail en quatre rubriques, dans cet ordre exact : "
            "## News, ## Finance, ## Juridique, ## Cour de cassation. Ces rubriques correspondent "
            "aux quatre pages du site. Dans chaque rubrique, retiens uniquement les sujets datés "
            "de la veille et les plus pertinents pour cette page; si une rubrique n'a aucun fait "
            "exploitable, indique en une phrase qu'aucune information significative datée de la "
            "veille n'a été retenue. Pour chaque sujet retenu, écris un petit bloc éditorial "
            "composé d'un intitulé de thème en gras, d'un résumé succinct de deux ou trois "
            "phrases, puis d'un lien sur une ligne séparée sous la forme [Lire l'article](URL). "
            "N'utilise ni numérotation, ni puces, ni libellés répétitifs comme « Item », "
            "« Thème », « Résumé » ou « Source ». Diversifie les sources et les thèmes."
        ),
        "news": (
            "Construis un panorama équilibré des actualités significatives de la veille. "
            "Couvre, lorsque les données le permettent, la géopolitique, l'environnement, "
            "la politique nationale, la tech, les questions de société et de pouvoir d'achat, "
            "la santé, puis toute autre actualité majeure. Ne force pas une rubrique sans fait "
            "pertinent et évite qu'un seul thème occupe toute la synthèse."
        ),
        "finance": (
            "Concentre-toi sur les marchés financiers, la dette souveraine, les comptes publics, "
            "l'économie, la politique monétaire et la régulation. Distingue faits, chiffres et "
            "conséquences possibles. N'invente aucune cotation."
        ),
        "cour-de-cassation": (
            "Croise les décisions Judilibre avec les sélections éditoriales des Lettres lorsqu'un lien "
            "thématique est explicitement établi par les données. Distingue clairement : décisions "
            "notables, tendances par chambre et nouvelles parutions. Pour chaque décision, indique la "
            "chambre, la date et le numéro uniquement s'ils figurent dans les données. Explique "
            "sobrement la portée juridique sans inventer de solution."
        ),
        "juridique-analyse": (
            "Structure le résumé en un item par onglet juridique, avec les intitulés exacts : "
            "Institutions, Revues, Blogs, Juridictions, Régulation. Ajoute Cour de cassation seulement "
            "si les données fournies contiennent une source exploitable datée de la veille. "
            "Chaque item doit citer au moins une source distincte lorsque c'est possible, et "
            "écarter les contenus anciens, redondants ou trop faibles. Mets un focus spécifique "
            "sur le Conseil d'État dans Juridictions si des données le concernent; sinon, signale "
            "sobrement qu'aucun fait exploitable daté de la veille ne le concerne. Termine par un "
            "court point de synthèse transversal en deux phrases maximum."
        ),
    }
    regle_format = (
        "- Utilise des titres Markdown ##, puis des puces factuelles.\n"
        "- Place le lien de la source au bout de chaque puce sous la forme [Source](URL).\n"
        "- N'ajoute jamais de section finale Sources, Bibliographie, Références ou Liens."
        if cible != "synthese"
        else "- Adopte un ton éditorial fluide et respecte strictement les quatre rubriques demandées."
    )
    return f"""Tu rédiges un briefing professionnel en français à partir des seules données ci-dessous.

Titre exact à utiliser : # {titres[cible]}

Règles impératives :
- N'ajoute aucun fait, chiffre, date, citation, décision ou contexte absent des données.
- Si les données sont insuffisantes pour affirmer un point, omets-le.
- Déduplique les sujets repris par plusieurs sources.
- Varie les sources citees lorsque plusieurs sources pertinentes traitent de sujets differents.
- Sois synthétique : 350 à 700 mots, phrases courtes, aucun remplissage.
{regle_format}
- Ne crée ni bibliographie séparée, ni note de méthode, ni prévision.
- Ignore tout article dont la date fournie ne correspond pas au {HIER}.
- {specificites[cible]}

DONNÉES DU {HIER} :
{donnees_prompt(articles)}
"""


def limiter_articles_pour_mistral(cible, articles):
    """Garde un panel diversifie tant que le prompt reste sous le plafond."""
    selection = []
    articles = diversifier_articles(articles)
    for article in articles:
        candidate = selection + [article]
        tokens_estimes = estimer_tokens(prompt_pour(cible, candidate))
        if tokens_estimes > MISTRAL_MAX_INPUT_TOKENS:
            if selection:
                break
            article_court = dict(article)
            article_court["resume"] = nettoyer_texte(article_court.get("resume", ""), 350)
            if estimer_tokens(prompt_pour(cible, [article_court])) <= MISTRAL_MAX_INPUT_TOKENS:
                selection.append(article_court)
            break
        selection = candidate
    if len(selection) < len(articles):
        print(
            f"{cible}: {len(selection)}/{len(articles)} sources retenues "
            f"pour rester sous {MISTRAL_MAX_INPUT_TOKENS} tokens d'entree estimes."
        )
    return selection


def appeler_mistral(cible, articles, quota):
    articles = limiter_articles_pour_mistral(cible, articles)
    if not articles:
        raise RuntimeError("aucune source ne tient dans le budget de tokens configure")
    prompt = prompt_pour(cible, articles)
    tokens_entree_estimes = estimer_tokens(prompt)
    tokens_appel_estimes = tokens_entree_estimes + MISTRAL_MAX_OUTPUT_TOKENS
    tokens_utilises = int(quota.get("tokens", 0))
    if tokens_utilises + tokens_appel_estimes > MISTRAL_DAILY_TOKEN_BUDGET:
        raise RuntimeError(
            "quota Mistral quotidien preserve: "
            f"{tokens_utilises} deja comptes, "
            f"{tokens_appel_estimes} requis, "
            f"budget {MISTRAL_DAILY_TOKEN_BUDGET}. "
            "Augmente MISTRAL_DAILY_TOKEN_BUDGET si ton tableau de bord Mistral "
            "autorise plus de tokens."
        )
    reponse = requests.post(
        MISTRAL_API_URL,
        headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
        json={
            "model": MISTRAL_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Tu es un analyste de veille rigoureux. Tu préfères omettre une information "
                        "plutôt que de la compléter par supposition."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": MISTRAL_MAX_OUTPUT_TOKENS,
        },
        timeout=90,
    )
    reponse.raise_for_status()
    payload = reponse.json()
    usage = payload.get("usage") or {}
    tokens_reels = usage.get("total_tokens") or (
        usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
    )
    consommer_quota_mistral(quota, tokens_reels or tokens_appel_estimes)
    contenu = payload["choices"][0]["message"]["content"].strip()
    if not contenu:
        raise ValueError("Mistral a renvoyé une réponse vide")
    return contenu


def nettoyer_sortie_mistral(contenu):
    """Supprime les bibliographies finales que le modele ajoute parfois."""
    lignes = contenu.strip().splitlines()
    titres_sources = re.compile(
        r"^\s{0,3}#{1,6}\s*(sources?|r[ée]f[ée]rences?|bibliographie|liens)\s*:?\s*$",
        re.I,
    )
    for index, ligne in enumerate(lignes):
        if titres_sources.match(ligne.strip()):
            return "\n".join(lignes[:index]).rstrip()
    return contenu.strip()


def ecrire_markdown(cible, contenu):
    nom = f"synthese-{AUJOURDHUI}.md" if cible == "synthese" else f"{AUJOURDHUI}-{cible}.md"
    dossier = os.path.join(DOSSIER_MD, SOUS_DOSSIERS_MD[cible])
    os.makedirs(dossier, exist_ok=True)
    chemin = os.path.join(dossier, nom)
    with open(chemin, "w", encoding="utf-8") as fichier:
        fichier.write(nettoyer_sortie_mistral(contenu) + "\n")
    print(f"Fichier généré: {chemin}")


def compte_rendu_sans_donnees(cible):
    """Publie tout de meme le compte rendu quotidien d'une rubrique vide."""
    titres = {
        "news": f"Actualités générales — {HIER}",
        "finance": f"Finance et économie — {HIER}",
        "juridique-analyse": f"Actualités Juridiques — {HIER}",
        "synthese": f"Synthèse de veille du {HIER}",
    }
    return (
        f"# {titres[cible]}\n\n"
        f"Aucune actualité datée du {HIER} n'a été publiée dans les flux "
        "de cette rubrique."
    )


def generer_html_mail(texte_markdown):
    """Construit un email autonome, fidèle à la charte graphique du site."""
    corps = markdown.markdown(texte_markdown)
    soupe = BeautifulSoup(corps, "html.parser")

    # Le titre principal est déjà repris dans le bandeau du mail.
    titre_markdown = soupe.find("h1")
    if titre_markdown:
        titre_markdown.decompose()

    styles = {
        "h2": (
            "margin:32px 0 14px;color:#2c3e50;font-size:20px;line-height:1.3;"
            "font-weight:700;border-bottom:1px solid #e6ecef;padding-bottom:9px;"
        ),
        "h3": "margin:24px 0 10px;color:#2c3e50;font-size:17px;line-height:1.4;font-weight:700;",
        "p": "margin:0 0 18px;color:#333333;font-size:15px;line-height:1.7;",
        "ul": "margin:0 0 20px;padding-left:22px;color:#333333;",
        "ol": "margin:0 0 20px;padding-left:22px;color:#333333;",
        "li": "margin:0 0 9px;font-size:15px;line-height:1.65;",
        "strong": "color:#2c3e50;font-weight:700;",
        "blockquote": (
            "margin:20px 0;padding:14px 18px;background:#eef6fc;border-left:4px solid #3498db;"
            "color:#44515d;"
        ),
    }
    for balise, style in styles.items():
        for element in soupe.find_all(balise):
            element["style"] = style

    for lien in soupe.find_all("a"):
        lien["style"] = "color:#2479b5;text-decoration:underline;font-weight:600;"
        lien["target"] = "_blank"

        # Les liens « Lire l'article » deviennent des appels à l'action homogènes.
        if lien.get_text(" ", strip=True).lower().startswith("lire"):
            lien["style"] = (
                "display:inline-block;background:#3498db;color:#ffffff;text-decoration:none;"
                "font-size:14px;line-height:20px;font-weight:700;padding:9px 16px;border-radius:5px;"
            )

    contenu = str(soupe)
    archives_url = "https://nymesias.github.io/ma-veille-ia/"
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ma Veille Personnalisée — {HIER}</title>
</head>
<body style="margin:0;padding:0;background:#f4f7f6;font-family:'Segoe UI',Tahoma,Arial,sans-serif;color:#333333;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;">Votre synthèse de veille du {HIER}.</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f4f7f6;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:760px;">
          <tr>
            <td style="background:#2c3e50;padding:28px 34px;text-align:center;border-radius:8px 8px 0 0;">
              <div style="color:#ffffff;font-size:25px;line-height:1.25;font-weight:700;">Ma Veille Personnalisée</div>
              <div style="margin-top:8px;color:#b9d9ee;font-size:13px;line-height:1.4;text-transform:uppercase;letter-spacing:1px;">Synthèse du {HIER}</div>
            </td>
          </tr>
          <tr>
            <td style="background:#ffffff;border-left:6px solid #3498db;padding:30px 34px 18px;box-shadow:0 4px 15px rgba(0,0,0,0.08);">
              {contenu}
            </td>
          </tr>
          <tr>
            <td style="background:#ffffff;border-left:6px solid #3498db;padding:8px 34px 32px;text-align:center;border-radius:0 0 8px 0;">
              <a href="{archives_url}" target="_blank" style="display:inline-block;background:#2c3e50;color:#ffffff;text-decoration:none;font-size:14px;line-height:20px;font-weight:700;padding:11px 20px;border-radius:5px;">Consulter les archives</a>
            </td>
          </tr>
          <tr>
            <td style="padding:18px 20px 0;text-align:center;color:#7f8c8d;font-size:12px;line-height:1.5;">
              Généré automatiquement par Mistral AI<br>
              Ma Veille Personnalisée
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def envoyer_synthese_par_mail(texte_markdown):
    expediteur = os.getenv("EMAIL_SENDER")
    mot_de_passe = os.getenv("EMAIL_PASSWORD")
    destinataire = os.getenv("EMAIL_RECEIVER")
    if not all([expediteur, mot_de_passe, destinataire]):
        print("Variables d'email manquantes: envoi ignoré.")
        return

    html_final = generer_html_mail(texte_markdown)
    message = MIMEText(html_final, "html", "utf-8")
    message["Subject"] = Header(f"Veille du {HIER}", "utf-8")
    message["From"] = expediteur
    message["To"] = destinataire

    with smtplib.SMTP("smtp.bookmyname.com", 587, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(expediteur.strip(), mot_de_passe.strip())
        smtp.send_message(message)
    print("Mail envoyé.")


def main():
    print(f"Collecte des flux pour le {HIER}...")
    data_rss, articles = collecter_articles(charger_sources())
    data_newsletters, articles_newsletters = collecter_newsletters(
        charger_newsletters(), charger_cartes_precedentes()
    )
    fusionner_newsletters(data_rss, data_newsletters)
    articles.extend(articles_newsletters)

    lettres = collecter_lettres()
    decisions = collecter_decisions_judilibre()
    sources_cassation = sources_analyse_cassation(lettres, decisions)
    sources_cassation_hier = [
        source for source in sources_cassation if source.get("date") == HIER
    ]

    # Les flux et les archives doivent rester à jour, même sans article ou sans clé API.
    synchroniser_listes(data_rss)

    if not MISTRAL_KEY:
        raise RuntimeError("MISTRAL_API_KEY absente: génération IA impossible.")

    quota_mistral = charger_quota_mistral()
    print(
        "Budget Mistral local: "
        f"{quota_mistral['tokens']}/{MISTRAL_DAILY_TOKEN_BUDGET} tokens "
        f"(modele {MISTRAL_MODEL})."
    )

    synthese = None
    analyse_cassation = None
    analyse_juridique = None
    sorties_generees = set()
    # Les comptes rendus detailles sont produits en premier. La synthese mail reutilise
    # ensuite l'analyse juridique compacte pour rester dans le budget d'entree Mistral.
    for cible in ("news", "finance", "cour-de-cassation", "juridique-analyse", "synthese"):
        if cible == "cour-de-cassation":
            selection = sources_cassation_hier
        elif cible == "juridique-analyse":
            selection = selection_analyse_juridique(
                articles,
                sources_cassation,
                analyse_cassation,
            )
        elif cible == "synthese":
            selection = sources_synthese_generale(
                articles,
                analyse_juridique,
                analyse_cassation,
            )
        else:
            selection = articles_pour(cible, articles)
        if not selection:
            if cible in ("news", "finance", "juridique-analyse", "synthese"):
                contenu = compte_rendu_sans_donnees(cible)
                ecrire_markdown(cible, contenu)
                sorties_generees.add(cible)
                if cible == "juridique-analyse":
                    analyse_juridique = source_depuis_markdown(cible, contenu)
            else:
                print(f"Aucune donnée pour {cible}: fichier non généré.")
            continue
        try:
            contenu = appeler_mistral(cible, selection, quota_mistral)
            ecrire_markdown(cible, contenu)
            sorties_generees.add(cible)
            if cible == "cour-de-cassation":
                analyse_cassation = source_depuis_markdown(cible, contenu)
            if cible == "juridique-analyse":
                analyse_juridique = source_depuis_markdown(cible, contenu)
            if cible == "synthese":
                synthese = contenu
        except Exception as exc:
            print(f"Erreur de génération {cible}: {exc}")

    sorties_attendues = {"news", "finance", "juridique-analyse", "synthese"}
    if sources_cassation_hier:
        sorties_attendues.add("cour-de-cassation")
    sorties_manquantes = sorties_attendues - sorties_generees
    if sorties_manquantes:
        raise RuntimeError(
            "Générations quotidiennes manquantes: "
            + ", ".join(sorted(sorties_manquantes))
        )

    synchroniser_listes(data_rss)
    if synthese:
        try:
            envoyer_synthese_par_mail(synthese)
        except Exception as exc:
            print(f"Erreur d'envoi du mail: {exc}")


if __name__ == "__main__":
    main()
