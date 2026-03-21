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
    with open(FICHIER_SOURCES, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sources.append(row)
    return sources

def synchroniser_listes(data_rss):
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
        
def main():
    sources = charger_sources()
    data_globale = {}
    contenu_pour_mistral = ""

    print(f"--- 📡 Récupération (Aujourd'hui: {AUJOURDHUI} / Filtre: {HIER}) ---")
    
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
                
                dt_struct = entry.get('published_parsed') or entry.get('updated_parsed')
                date_art = datetime(*dt_struct[:3]).strftime('%Y-%m-%d') if dt_struct else AUJOURDHUI

                articles_du_site.append({"t": t, "l": l, "d": date_art})

                # FILTRAGE STRICT SUR HIER POUR L'IA
                if date_art == HIER:
                    contenu_pour_mistral += f"[{cat_nom}] {src_name} : {t}\n"

            if cat_nom not in data_globale: 
                data_globale[cat_nom] = []
            
            data_globale[cat_nom].append({"nom_site": src_name, "articles": articles_du_site})

        except Exception as e:
            print(f"Erreur flux {src_name}: {e}")

    # Synthèse Mistral
    if MISTRAL_KEY and contenu_pour_mistral:
        print(f"--- 🤖 Synthèse IA ({contenu_pour_mistral.count(' : ')} articles de hier) ---")
        prompt = f"Tu es un expert en veille. Voici les actus du {HIER}. Synthétise par catégories. Sources entre parenthèses.\n\nACTUS :\n{contenu_pour_mistral[:10000]}"
        try:
            r = requests.post("https://api.mistral.ai/v1/chat/completions", 
                json={"model": "mistral-small-latest", "messages": [{"role": "user", "content": prompt}]},
                headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"})
            
            if r.status_code == 200:
                synthese_texte = r.json()['choices'][0]['message']['content']
                nom_md = f"synthese-{HIER}.md" # On nomme le fichier par la date de hier
                with open(os.path.join(DOSSIER_MD, nom_md), "w", encoding='utf-8') as f:
                    f.write(synthese_texte)
                
                # ENVOI DU MAIL
                envoyer_synthese_par_mail(synthese_texte)
            else:
                print(f"Erreur API Mistral : {r.status_code}")
        except Exception as e: 
            print(f"Erreur Mistral: {e}")
    else:
        print("ℹ️ Aucun article trouvé pour hier. Pas de synthèse.")

    synchroniser_listes(data_globale)
    print("✅ Fichiers mis à jour.")

if __name__ == "__main__":
    main()

def envoyer_synthese_par_mail(texte_markdown):
    host = "smtp.bookmyname.com"
    expediteur = os.getenv("EMAIL_SENDER")
    mot_de_pass = os.getenv("EMAIL_PASSWORD")
    destinataire = os.getenv("EMAIL_RECEIVER")

    if not all([expediteur, mot_de_pass, destinataire]):
        print("⚠️ Variables d'email manquantes.")
        return

    corps_html_brut = markdown.markdown(texte_markdown)
    style_css = """
    <style>
        body { font-family: sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: auto; }
        h2 { color: #2c3e50; border-bottom: 2px solid #eee; }
        a { color: #3498db; }
    </style>
    """
    html_final = f"<html><head>{style_css}</head><body>{corps_html_brut}</body></html>"

    msg = MIMEText(html_final, 'html', 'utf-8')
    msg['Subject'] = Header(f"⚖️ Veille du {HIER}", 'utf-8')
    msg['From'] = expediteur
    msg['To'] = destinataire

    try:
        with smtplib.SMTP(host, 587, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(expediteur.strip(), mot_de_pass.strip())
            smtp.send_message(msg)
            print("✅ Mail envoyé avec succès.")
    except Exception as e:
        print(f"❌ Erreur mail : {e}")
