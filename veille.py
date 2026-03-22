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
    # 1. Mise à jour du JSON RSS (On remplace les flux, car ils sont frais)
    with open(FICHIER_LISTE_RSS, "w", encoding='utf-8') as f:
        json.dump(data_rss, f, indent=4, ensure_ascii=False)

    # 2. Mise à jour intelligente du JSON MD (Archives)
    liste_existante = []
    if os.path.exists(FICHIER_LISTE_MD):
        try:
            with open(FICHIER_LISTE_MD, "r", encoding='utf-8') as f:
                liste_existante = json.load(f)
        except:
            liste_existante = []

    # On récupère les fichiers MD actuels sur le disque
    fichiers_sur_disque = [f for f in os.listdir(DOSSIER_MD) if f.endswith(".md")]
    
    # On reconstruit la liste proprement pour être sûr de ne rien oublier
    # (C'est plus sûr que de "append" car cela gère les fichiers supprimés à la main)
    nouvelle_liste = []
    for f in fichiers_sur_disque:
        match = re.search(r"(\d{4}-\d{2}-\d{2})", f)
        date_f = match.group(1) if match else AUJOURDHUI
        nouvelle_liste.append({
            "date_affichage": date_f,
            "date_tri": date_f,
            "nom_fichier": f
        })

    # Tri par date décroissante (plus récent en haut)
    nouvelle_liste.sort(key=lambda x: x['date_tri'], reverse=True)

    # 3. Écriture finale (Mise à jour du fichier)
    with open(FICHIER_LISTE_MD, "w", encoding='utf-8') as f:
        json.dump(nouvelle_liste, f, indent=4, ensure_ascii=False)

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
        body { margin: 0; padding: 0; background-color: #f4f7f6; font-family: 'Segoe UI', Helvetica, Arial, sans-serif; }
        .wrapper { width: 100%; table-layout: fixed; background-color: #f4f7f6; padding-bottom: 40px; }
        .main { background-color: #ffffff; margin: 0 auto; width: 100%; max-width: 600px; border-spacing: 0; color: #333333; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 10px rgba(0,0,0,0.05); }
        .header { background-color: #2c3e50; padding: 30px; text-align: center; color: #ffffff; }
        .header h1 { margin: 0; font-size: 24px; font-weight: 300; letter-spacing: 1px; }
        .content { padding: 30px; line-height: 1.6; font-size: 16px; }
        .content h2 { color: #2c3e50; border-bottom: 2px solid #ecf0f1; padding-bottom: 10px; margin-top: 25px; }
        .content a { color: #3498db; text-decoration: none; font-weight: bold; }
        .button-container { text-align: center; padding: 20px 0; }
        .button { background-color: #3498db; color: #ffffff !important; padding: 12px 25px; border-radius: 5px; text-decoration: none; font-weight: bold; display: inline-block; }
        .footer { text-align: center; padding: 20px; font-size: 12px; color: #7f8c8d; }
    </style>
    """

    html_final = f"""
    <html>
    <head>{style_css}</head>
    <body>
        <div class="wrapper">
            <table class="main">
                <tr>
                    <td class="header">
                        <h1>⚖️ Ma Veille Quotidienne</h1>
                    </td>
                </tr>
                <tr>
                    <td class="content">
                        <p>Bonjour,</p>
                        <p>Voici l'essentiel de l'actualité IA pour la journée du <strong>{HIER}</strong> :</p>
                        {corps_html_brut}
                        <div class="button-container">
                            <a href="{url_site}" class="button">Consulter l'archive sur le site</a>
                        </div>
                    </td>
                </tr>
                <tr>
                    <td class="footer">
                        Généré automatiquement par Mistral IA • {AUJOURDHUI}<br>
                        Vous recevez ce mail car vous êtes abonné à votre propre veille.
                    </td>
                </tr>
            </table>
        </div>
    </body>
    </html>
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
        prompt = f"Tu es un expert en veille. Voici les actus du {HIER}. Fais un résumé de ces actualités par thématiques pour être exhaustif mais concis à chaque fois. Cite la source, ainsi que l'URL. \n\nACTUS :\n{contenu_pour_mistral[:10000]}"
        try:
            r = requests.post("https://api.mistral.ai/v1/chat/completions", 
                json={"model": "mistral-small-latest", "messages": [{"role": "user", "content": prompt}]},
                headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"})
            
            if r.status_code == 200:
                synthese_texte = r.json()['choices'][0]['message']['content']
                nom_md = f"synthese-{AUJOURDHUI}.md" # On nomme le fichier par la date de hier
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

