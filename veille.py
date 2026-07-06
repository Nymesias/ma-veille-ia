import csv
import html
import json
import os
import re
import smtplib
from datetime import datetime, timedelta
from email.header import Header
from email.mime.text import MIMEText

import feedparser
import markdown
import requests
from bs4 import BeautifulSoup


MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-medium-3-5")

DOSSIER_MD = "markdown"
FICHIER_SOURCES = "sources.csv"
FICHIER_LISTE_RSS = "liste_rss.json"
FICHIER_LISTE_MD = "liste_md.json"
FICHIER_LETTRES = "lettres_cour_cassation.json"
FICHIER_DECISIONS = "decisions_judilibre.json"

JUDILIBRE_KEY_ID = os.getenv("JUDILIBRE_KEY_ID")
JUDILIBRE_API_URL = os.getenv(
    "JUDILIBRE_API_URL",
    "https://api.piste.gouv.fr/cassation/judilibre/v1.0",
)

COLLECTIONS_LETTRES = {
    "Lettre de la Cour": 2666,
    "Première chambre civile": 15,
    "Deuxième chambre civile": 170,
    "Troisième chambre civile": 171,
    "Chambre commerciale": 172,
    "Chambre sociale": 16,
    "Chambre criminelle": 173,
    "Lettre internationale": 3643,
}

MAINTENANT = datetime.now()
AUJOURDHUI = MAINTENANT.strftime("%Y-%m-%d")
HIER = (MAINTENANT - timedelta(days=1)).strftime("%Y-%m-%d")

os.makedirs(DOSSIER_MD, exist_ok=True)


def nettoyer_texte(valeur, limite=900):
    """Transforme le HTML d'un flux en texte court exploitable par le modèle."""
    texte = html.unescape(valeur or "")
    texte = re.sub(r"<[^>]+>", " ", texte)
    texte = re.sub(r"\s+", " ", texte).strip()
    return texte[:limite]


def charger_sources():
    if not os.path.exists(FICHIER_SOURCES):
        return []
    with open(FICHIER_SOURCES, newline="", encoding="utf-8-sig") as fichier:
        return list(csv.DictReader(fichier))


def date_entree(entry):
    date_structuree = entry.get("published_parsed") or entry.get("updated_parsed")
    if not date_structuree:
        return AUJOURDHUI
    return datetime(*date_structuree[:3]).strftime("%Y-%m-%d")


def collecter_articles(sources):
    data_rss = {}
    articles_hier = []

    for source in sources:
        categorie = source.get("categorie", "general").strip().lower()
        nom_source = source.get("source", "Source inconnue").strip()
        url = source.get("url", "").strip()
        if not url:
            continue

        articles_source = []
        try:
            flux = feedparser.parse(url)
            if getattr(flux, "bozo", False):
                print(f"Avertissement flux {nom_source}: {flux.bozo_exception}")

            for entry in flux.entries[:30]:
                article = {
                    "categorie": categorie,
                    "source": nom_source,
                    "titre": nettoyer_texte(entry.get("title", "Sans titre"), 300),
                    "lien": entry.get("link", url),
                    "date": date_entree(entry),
                    "resume": nettoyer_texte(
                        entry.get("summary") or entry.get("description") or ""
                    ),
                }
                articles_source.append(
                    {"t": article["titre"], "l": article["lien"], "d": article["date"]}
                )
                if article["date"] == HIER:
                    articles_hier.append(article)
        except Exception as exc:
            print(f"Erreur flux {nom_source}: {exc}")

        data_rss.setdefault(categorie, []).append(
            {"nom_site": nom_source, "articles": articles_source}
        )

    return data_rss, articles_hier


def synchroniser_listes(data_rss):
    with open(FICHIER_LISTE_RSS, "w", encoding="utf-8") as fichier:
        json.dump(data_rss, fichier, indent=2, ensure_ascii=False)

    fichiers = []
    for nom in os.listdir(DOSSIER_MD):
        if not nom.endswith(".md"):
            continue
        correspondance = re.search(r"(\d{4}-\d{2}-\d{2})", nom)
        date_fichier = correspondance.group(1) if correspondance else AUJOURDHUI
        fichiers.append(
            {
                "date_affichage": date_fichier,
                "date_tri": date_fichier,
                "nom_fichier": nom,
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
    for nom_mois, numero_mois in MOIS_FRANCAIS.items():
        correspondance = re.search(rf"\b{nom_mois}\s+(\d{{4}})\b", texte_titre)
        if correspondance:
            return f"{correspondance.group(1)}-{numero_mois:02d}-01"
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
            for article in page.select("main article")[:5]:
                lien = article.select_one("h2 a[href], h3 a[href]")
                if not lien:
                    continue
                url = requests.compat.urljoin(base, lien.get("href"))
                if url in vus:
                    continue
                vus.add(url)
                paragraphes = [
                    nettoyer_texte(p.get_text(" ", strip=True), 500)
                    for p in article.select("p")
                ]
                titre = nettoyer_texte(lien.get_text(" ", strip=True), 250)
                lettres.append(
                    {
                        "collection": collection,
                        "titre": titre,
                        "resume": next((p for p in paragraphes if p and p != collection), ""),
                        "url": url,
                        "date": date_lettre(article, titre),
                    }
                )
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


def collecter_decisions_judilibre():
    """Publie un cache sans secret des dernières décisions de la Cour de cassation."""
    if not JUDILIBRE_KEY_ID:
        print("JUDILIBRE_KEY_ID absente: collecte des décisions ignorée.")
        return []

    date_debut = (MAINTENANT - timedelta(days=45)).strftime("%Y-%m-%d")
    date_fin = MAINTENANT.strftime("%Y-%m-%d")
    try:
        reponse = requests.get(
            f"{JUDILIBRE_API_URL}/export",
            headers={"accept": "application/json", "KeyId": JUDILIBRE_KEY_ID},
            params={
                "jurisdiction": "cc",
                "date_start": date_debut,
                "date_end": date_fin,
                "date_type": "creation",
                "batch": 0,
                "batch_size": 50,
                "abridged": "true",
                "resolve_references": "true",
            },
            timeout=45,
        )
        reponse.raise_for_status()
        donnees = reponse.json()
    except requests.HTTPError as exc:
        corps = exc.response.text[:1000] if exc.response is not None else ""
        print(f"Erreur API Judilibre: {exc} — {corps}")
        return []
    except Exception as exc:
        print(f"Erreur API Judilibre: {exc}")
        return []

    decisions = []
    for resultat in donnees.get("results", []):
        identifiant = resultat.get("id") or resultat.get("_id")
        juridiction = str(resultat.get("jurisdiction", "")).lower()
        if juridiction and juridiction not in {"cc", "cour de cassation"}:
            continue
        decisions.append(
            {
                "id": identifiant,
                "date": resultat.get("decision_date") or resultat.get("date_decision") or "",
                "chambre": valeur_liste(resultat.get("chamber")),
                "formation": valeur_liste(resultat.get("formation")),
                "numero": resultat.get("number") or resultat.get("numero") or "",
                "solution": valeur_liste(resultat.get("solution")),
                "publication": valeur_liste(resultat.get("publication")),
                "sommaire": nettoyer_texte(
                    resultat.get("summary")
                    or resultat.get("sommaire")
                    or "",
                    700,
                ),
                "url": (
                    f"https://www.courdecassation.fr/decision/{identifiant}"
                    if identifiant
                    else "https://www.courdecassation.fr/recherche-judilibre"
                ),
            }
        )

    decisions.sort(key=lambda item: item["date"], reverse=True)
    with open(FICHIER_DECISIONS, "w", encoding="utf-8") as fichier:
        json.dump(
            {
                "mis_a_jour": MAINTENANT.isoformat(timespec="seconds"),
                "total_api": donnees.get("total"),
                "decisions": decisions,
            },
            fichier,
            indent=2,
            ensure_ascii=False,
        )
    print(f"{len(decisions)} décisions Judilibre collectées.")
    return decisions


def articles_pour(cible, articles):
    if cible == "news":
        return [article for article in articles if article["categorie"] == "news"]
    if cible == "finance":
        return [article for article in articles if article["categorie"] == "finance"]
    if cible == "cour-de-cassation":
        return [
            article
            for article in articles
            if "cour de cassation" in article["source"].lower()
            or "cour de cassation" in article["titre"].lower()
        ]
    return articles


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


def prompt_pour(cible, articles):
    titres = {
        "synthese": f"Synthèse de veille du {HIER}",
        "news": f"Actualités générales — {HIER}",
        "finance": f"Finance et économie — {HIER}",
        "cour-de-cassation": f"Cour de cassation — {HIER}",
    }
    specificites = {
        "synthese": (
            "Organise le mail en trois rubriques lorsque les données le permettent : News, "
            "Finance et Cour de cassation. Pour chaque sujet retenu, écris un petit bloc "
            "éditorial composé d'un intitulé de thème en gras, d'un résumé succinct de deux "
            "ou trois phrases, puis d'un lien sur une ligne séparée sous la forme "
            "[Lire l'article](URL). N'utilise ni numérotation, ni puces, ni libellés répétitifs "
            "comme « Item », « Thème », « Résumé » ou « Source ». Retiens seulement 3 à 6 "
            "sujets majeurs au total et relie les informations qui traitent du même thème."
        ),
        "news": "Retiens les faits d'actualité générale réellement significatifs.",
        "finance": "Distingue faits, chiffres et conséquences possibles. N'invente aucune cotation.",
        "cour-de-cassation": (
            "Croise les décisions Judilibre avec les sélections éditoriales des Lettres lorsqu'un lien "
            "thématique est explicitement établi par les données. Distingue clairement : décisions "
            "notables, tendances par chambre et nouvelles parutions. Pour chaque décision, indique la "
            "chambre, la date et le numéro uniquement s'ils figurent dans les données. Explique "
            "sobrement la portée juridique sans inventer de solution."
        ),
    }
    regle_format = (
        "- Utilise des titres Markdown ##, puis des puces factuelles.\n"
        "- Place le lien de la source au bout de chaque puce sous la forme [Source](URL)."
        if cible != "synthese"
        else "- Adopte un ton éditorial fluide et respecte strictement le format de blocs demandé."
    )
    return f"""Tu rédiges un briefing professionnel en français à partir des seules données ci-dessous.

Titre exact à utiliser : # {titres[cible]}

Règles impératives :
- N'ajoute aucun fait, chiffre, date, citation, décision ou contexte absent des données.
- Si les données sont insuffisantes pour affirmer un point, omets-le.
- Déduplique les sujets repris par plusieurs sources.
- Sois synthétique : 350 à 700 mots, phrases courtes, aucun remplissage.
{regle_format}
- Ne crée ni bibliographie séparée, ni note de méthode, ni prévision.
- {specificites[cible]}

DONNÉES DU {HIER} :
{donnees_prompt(articles)}
"""


def appeler_mistral(cible, articles):
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
                {"role": "user", "content": prompt_pour(cible, articles)},
            ],
            "temperature": 0.1,
            "max_tokens": 2500,
        },
        timeout=90,
    )
    reponse.raise_for_status()
    contenu = reponse.json()["choices"][0]["message"]["content"].strip()
    if not contenu:
        raise ValueError("Mistral a renvoyé une réponse vide")
    return contenu


def ecrire_markdown(cible, contenu):
    nom = f"synthese-{AUJOURDHUI}.md" if cible == "synthese" else f"{AUJOURDHUI}-{cible}.md"
    chemin = os.path.join(DOSSIER_MD, nom)
    with open(chemin, "w", encoding="utf-8") as fichier:
        fichier.write(contenu + "\n")
    print(f"Fichier généré: {chemin}")


def envoyer_synthese_par_mail(texte_markdown):
    expediteur = os.getenv("EMAIL_SENDER")
    mot_de_passe = os.getenv("EMAIL_PASSWORD")
    destinataire = os.getenv("EMAIL_RECEIVER")
    if not all([expediteur, mot_de_passe, destinataire]):
        print("Variables d'email manquantes: envoi ignoré.")
        return

    corps = markdown.markdown(texte_markdown)
    html_final = f"""<html><body style="font-family:Segoe UI,Arial,sans-serif;background:#f4f7f6">
    <main style="max-width:800px;margin:auto;background:white;padding:30px;border-left:6px solid #3498db">
    {corps}</main>
    <p style="text-align:center;color:#777">Généré automatiquement par Mistral AI —
    <a href="https://nymesias.github.io/ma-veille-ia/">Accéder aux archives</a></p>
    </body></html>"""
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

    lettres = collecter_lettres()
    decisions = collecter_decisions_judilibre()
    sources_cassation = sources_analyse_cassation(lettres, decisions)

    # Les flux et les archives doivent rester à jour, même sans article ou sans clé API.
    synchroniser_listes(data_rss)

    if not MISTRAL_KEY:
        print("MISTRAL_API_KEY absente: génération IA ignorée.")
        return
    if not articles and not sources_cassation:
        print(f"Aucun article daté du {HIER} et aucune source Cour: aucun Markdown généré.")
        return

    synthese = None
    # Les trois comptes rendus sont produits en premier. La synthèse mail est ensuite
    # générée à partir de toutes leurs sources, y compris celles de la Cour de cassation.
    for cible in ("news", "finance", "cour-de-cassation", "synthese"):
        if cible == "cour-de-cassation":
            selection = sources_cassation
        elif cible == "synthese":
            selection = articles + sources_cassation
        else:
            selection = articles_pour(cible, articles)
        if not selection:
            print(f"Aucune donnée pour {cible}: fichier non généré.")
            continue
        try:
            contenu = appeler_mistral(cible, selection)
            ecrire_markdown(cible, contenu)
            if cible == "synthese":
                synthese = contenu
        except Exception as exc:
            print(f"Erreur de génération {cible}: {exc}")

    synchroniser_listes(data_rss)
    if synthese:
        try:
            envoyer_synthese_par_mail(synthese)
        except Exception as exc:
            print(f"Erreur d'envoi du mail: {exc}")


if __name__ == "__main__":
    main()
