import os
import json
import csv
import requests
import html
import feedparser
import re
import smtplib
import markdown
from email.mime.text import MIMEText
from email.header import Header
from datetime import datetime, timedelta

hier = (datetime.now() - timedelta(days=1)).date().strftime('%Y-%m-%d')

# --- CONFIGURATION ---
MISTRAL_KEY = os.getenv("MISTRAL_API_KEY")
DOSSIER_MD = "markdown"
FICHIER_SOURCES = "sources.csv"
AUJOURDHUI = datetime.now().strftime("%Y-%m-%d")
HIER = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

FICHIER_LISTE_RSS = "liste_rss.json"
FICHIER_LISTE_MD = "liste_md.json"

if not os.path.exists(DOSSIER_MD):
    os.makedirs(DOSSIER_MD)

def charger_sources():
    sources = []
    if not os.path.exists(FICHIER_SOURCES):
        return sources
    # 'utf-8' ici est vital pour lire les noms de sites avec accents
    with open(FICHIER_SOURCES, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sources.append(row)
    return sources

def synchroniser_listes(data_rss):
    # 'utf-8' est OBLIGATOIRE ici car le RSS contient des caractères spéciaux
    with open(FICHIER_LISTE_RSS, "w", encoding='utf-8') as f:
        json.dump(data_rss, f, indent=4, ensure_ascii=False)

    fichiers_md = [f for f in os.listdir(DOSSIER_MD) if f.endswith(".md")]
    liste_md = []
    for f in fichiers_md:
        match = re.search(r"(\d{4}-\d{2}-\d{2})", f)
        date_f = match.group(1) if match else AUJOURDHUI
        liste_md.append({
            "date_affichage": date_f,
            "date_tri": date_f,
            "nom_fichier": f
        })
    
    liste_md.sort(key=lambda x: x['date_tri'], reverse=True)
    with open(FICHIER_LISTE_MD, "w", encoding='utf-8') as f:
        json.dump(liste_md, f, indent=4, ensure_ascii=False)

def envoyer_synthese_par_mail(texte_markdown):
    # 1. Récupération des réglages dans les Secrets GitHub
    host = os.getenv("EMAIL_SMTP_SERVER")
    expediteur = os.getenv("EMAIL_SENDER")
    mot_de_pass = os.getenv("EMAIL_PASSWORD")
    destinataire = os.getenv("EMAIL_RECEIVER")

    # 2. Conversion du Markdown en HTML (le même rendu que ton site)
    # On ajoute un petit style CSS pour que ce soit joli dans Outlook/Gmail
    corps_html_brut = markdown.markdown(texte_markdown)

    style_css = """
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: auto; }
        h1, h2 { color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; }
        a { color: #3498db; text-decoration: none; }
        code { background: #f4f4f4; padding: 2px 5px; border-radius: 3px; }
        .footer { margin-top: 30px; font-size: 0.8em; color: #888; border-top: 1px solid #eee; padding-top: 10px; }
    </style>
    """

    html_final = f"""
    <html>
        <head>{style_css}</head>
        <body>
            {corps_html_brut}
            <div class="footer">
                <p>🤖 Généré automatiquement par ton IA de veille.</p>
                <p>Retrouve l'historique sur <a href="https://Nymesias.github.io/ma-veille-ia/">ton site de veille</a>.</p>
            </div>
        </body>
    </html>
    """

    # 3. Création du mail
    msg = MIMEText(html_final, 'html', 'utf-8')
    msg['Subject'] = f"⚖️ Veille Juridique du {date_veille}"
    msg['From'] = os.environ["EMAIL_SENDER"]
    msg['To'] = os.environ["EMAIL_RECEIVER"]

    host = "smtp.bookmyname.com"

    try:
        print(f"Connexion à {host} (Port 587)...")
        with smtplib.SMTP(host, 587, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            print("Sécurisation TLS établie.")
            smtp.ehlo()
            # On utilise .strip() pour nettoyer les secrets
            user = os.environ["EMAIL_SENDER"].strip()
            password = os.environ["EMAIL_PASSWORD"].strip()
            smtp.login(user, password)
            print("Authentification réussie.")
            smtp.send_message(msg)
            print("Félicitations ! Mail envoyé avec succès.")
            
    except Exception as e:
        print(f"Erreur d'envoi : {e}")

def main():
    sources = charger_sources()
    data_globale = {}
    contenu_pour_mistral = ""

    print(f"--- 📡 Récupération (Aujourd'hui: {AUJOURDHUI} / Filtre Synthèse: {HIER}) ---")
    
    for s in sources:
        cat_nom = s.get('categorie', 'Général').strip()
        src_name = s.get('source', 'Inconnue')
        url = s.get('url')
        if not url: continue

        try:
            flux = feedparser.parse(url)
            articles_du_site = []

            for entry in flux.entries[:8]:
                t = html.unescape(entry.get('title', 'Sans titre')).strip()
                l = entry.get('link', url)
                
                # RÉCUPÉRATION DE LA DATE
                # feedparser normalise la date dans 'published' ou 'updated'
                dt_obj = entry.get('published') or entry.get('updated_parsed')
                date_art = datetime(*dt_obj[:3]).strftime('%Y-%m-%d') if dt_obj else AUJOURDHUI
                
                articles_du_site.append({
                    "t": t, 
                    "l": l,
                    "d": date_art  # On ajoute la date ici
                })

                if date_art == HIER:
                    contenu_pour_mistral += f"[{cat_nom}] {src_name} : {t}\n"

            if cat_nom not in data_globale: data_globale[cat_nom] = []
            data_globale[cat_nom].append({"nom_site": src_name, "articles": articles_du_site})
        except: Exception as e:
            print(f"Erreur flux {src_name}: {e}")

    # Synthèse Mistral
    if MISTRAL_KEY and contenu_pour_mistral:
        print("--- 🤖 Synthèse IA ---")
        prompt = f"Tu es un expert en veille. Voici les actus du {HIER}. Synthétise par catégories. URL + Sources entre parenthèses.\n\nACTUS :\n{contenu_pour_mistral[:10000]}"
        try:
            r = requests.post("https://api.mistral.ai/v1/chat/completions", 
                json={"model": "mistral-small-latest", "messages": [{"role": "user", "content": prompt}]},
                headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"})
            
            if r.status_code == 200:
                synthese_texte = r.json()['choices'][0]['message']['content']
                nom_md = f"synthese-{AUJOURDHUI}.md"
                # On écrit la synthèse en utf-8 pour accepter les emojis de Mistral
                with open(os.path.join(DOSSIER_MD, nom_md), "w", encoding='utf-8') as f:
                    f.write(synthese_texte)
            else:
                print(f"Erreur API Mistral : {r.status_code}")
        except Exception as e: 
            print(f"Erreur Mistral: {e}")

    synchroniser_listes(data_globale)
    print("✅ Fichiers JSON et Markdown mis à jour avec succès.")

if __name__ == "__main__":
    main()
