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
    url_site = "https://nymesias.github.io/ma-veille-ia/"

    if not all([expediteur, mot_de_pass, destinataire]):
        print("⚠️ Variables d'email manquantes.")
        return

    corps_html_brut = markdown.markdown(texte_markdown)
    date_fr = datetime.now().strftime('%d/%m/%Y')

    style_css = """
    <style>
        /* Reset pour les clients mail */
        body { margin: 0; padding: 0; background-color: #f4f7f6; font-family: 'Segoe UI', Arial, sans-serif; }
        table { border-collapse: collapse; width: 100%; }
        
        /* Container principal */
        .email-container { max-width: 800px; margin: 0 auto; background-color: #f4f7f6; }
        
        /* Le bloc Article (style .post du site) */
        .post-veille { 
            background-color: #ffffff;
            border-left: 6px solid #3498db; 
            margin: 20px 0;
            padding: 30px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            /* Force la largeur totale de la colonne */
            width: 100%;
            box-sizing: border-box; 
        }

        /* Titres et textes */
        h1 { color: #2c3e50; font-size: 26px; text-align: center; padding: 20px; margin: 0; }
        h2 { color: #2c3e50; font-size: 22px; border-bottom: 2px solid #3498db; padding-bottom: 8px; margin-top: 40px; }
        h3 { color: #34495e; font-size: 18px; margin-top: 25px; } /* Pour les titres d'articles */
        
        p { line-height: 1.7; color: #444; font-size: 16px; margin: 15px 0; }
        a { color: #3498db; text-decoration: none; font-weight: bold; }
        
        .footer { text-align: center; padding: 30px; font-size: 13px; color: #999; }

        /* Responsive : sur mobile, on réduit un peu le padding */
        @media screen and (max-width: 600px) {
            .post-veille { padding: 20px; border-left-width: 4px; }
            h1 { font-size: 22px; }
        }
    </style>
    """

    html_final = f"""
    <html>
    <head>{style_css}</head>
    <body>
        <div class="email-container">
            <table>
                <tr>
                    <td>
                        <h1>✨ Bonjour Pauline !</h1>
                    </td>
                </tr>
                <tr>
                    <td style="padding: 0 10px;">
                        <div class="post-veille">
                            {corps_html_brut}
                        </div>
                    </td>
                </tr>
                <tr>
                    <td class="footer">
                        <p>📅 Publié le {date_fr}</p>
                        <p><a href="{url_site}">Accéder aux archives sur le site</a></p>
                        <hr style="border: 0; border-top: 1px solid #ddd; width: 50%;">
                        <p>Généré automatiquement par Mistral IA</p>
                    </td>
                </tr>
            </table>
        </div>
    </body>
    </html>
    """

    msg = MIMEText(html_final, 'html', 'utf-8')
    msg['Subject'] = Header(f"🤖 Veille du {HIER}", 'utf-8')
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
    articles_par_categorie = {} 
    # Initialisation ici pour éviter toute erreur plus tard
    contenu_pour_mistral = ""

    print(f"--- 📡 Récupération (Filtre: {HIER}) ---")
    
    for s in sources:
        cat_nom = s.get('categorie', 'Général').strip()
        src_name = s.get('source', 'Inconnue')
        url = s.get('url')
        if not url: continue

        # --- CORRECTION 1 : Initialiser ICI pour chaque source ---
        articles_du_site = []

        try:
            flux = feedparser.parse(url)
            for entry in flux.entries[:20]:
                t = html.unescape(entry.get('title', 'Sans titre')).strip()
                l = entry.get('link', url)
                
                dt_struct = entry.get('published_parsed') or entry.get('updated_parsed')
                date_art = datetime(*dt_struct[:3]).strftime('%Y-%m-%d') if dt_struct else AUJOURDHUI

                articles_du_site.append({"t": t, "l": l, "d": date_art})

                if date_art == HIER:
                    if cat_nom not in articles_par_categorie:
                        articles_par_categorie[cat_nom] = []
                    articles_par_categorie[cat_nom].append(f"{src_name} : {t} (Lien: {l})")
            
            # --- CORRECTION 2 : Déplacer l'enregistrement à l'intérieur du bloc source ---
            if cat_nom not in data_globale: 
                data_globale[cat_nom] = []
            data_globale[cat_nom].append({"nom_site": src_name, "articles": articles_du_site})

        except Exception as e:
            print(f"❌ Erreur flux {src_name}: {e}")
            # On assure que data_globale a quand même une entrée vide en cas d'erreur
            if cat_nom not in data_globale: data_globale[cat_nom] = []
            data_globale[cat_nom].append({"nom_site": src_name, "articles": []})

    # --- CONSTRUCTION DU TEXTE POUR MISTRAL ---
    for categorie, liste_articles in articles_par_categorie.items():
        contenu_pour_mistral += f"\n### CATÉGORIE : {categorie} ###\n"
        # On limite par exemple à 3 articles par catégorie pour l'IA
        for art in liste_articles[:3]: 
            contenu_pour_mistral += f"- {art}\n"

   # --- VÉRIFICATION AVANT ENVOI ---
    if not contenu_pour_mistral.strip():
        print("⚠️ Aucune actualité trouvée pour hier. Fin du script.")
        return # On arrête ici, pas besoin d'appeler l'IA ou d'envoyer un mail vide

    # --- SYNTHESE MISTRAL ---
    if MISTRAL_KEY and contenu_pour_mistral.strip():
        print(f"--- 🤖 Synthèse IA ({contenu_pour_mistral.count(' : ')} articles de hier) ---")
        
        # Préparation du prompt (on peut ajouter une consigne de brièveté ici)
        prompt = (
         f"Tu es un expert en veille. Voici les actus du {HIER} classées par catégories. Synthétise chaque catégorie séparément, sans en oublier une seule. Cite tes sources et inclue les liens [Lire l'article](URL).\n\nACTUS :\n{contenu_pour_mistral[:10000]}"
         f"DONNÉES :\n{contenu_pour_mistral[:12000]}"
       )

        try:
            # Appel API avec limitation des tokens et température
            r = requests.post(
                "https://api.mistral.ai/v1/chat/completions", 
                json={
                    "model": "mistral-small-latest", 
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,   # 0.2 pour la précision technique
                    "max_tokens": 1500    # Limite la longueur du mail
                },
                headers={
                    "Authorization": f"Bearer {MISTRAL_KEY}", 
                    "Content-Type": "application/json"
                },
                timeout=60 # Sécurité pour ne pas bloquer le script indéfiniment
            )
            
            if r.status_code == 200:
                reponse_json = r.json()
                synthese_texte = reponse_json['choices'][0]['message']['content']
                
                if synthese_texte:
                    nom_md = f"synthese-{AUJOURDHUI}.md" # Utilisation de AUJOURDH'HUI pour le nom du fichier sur les ACTUS de HIER
                    chemin_fichier = os.path.join(DOSSIER_MD, nom_md)
                    
                    with open(chemin_fichier, "w", encoding='utf-8') as f:
                        f.write(synthese_texte)
                    
                    # ENVOI DU MAIL (seulement si la synthèse a fonctionné)
                    envoyer_synthese_par_mail(synthese_texte)
                else:
                    print("⚠️ Mistral a renvoyé une réponse vide.")
            else:
                print(f"❌ Erreur API Mistral : {r.status_code} - {r.text}")
                
        except Exception as e: 
            print(f"❌ Erreur lors de l'appel Mistral: {e}")
    else:
        print("ℹ️ Aucun article trouvé pour hier (ou clé API manquante). Pas de synthèse.")

    # On synchronise les listes JSON même s'il n'y a pas eu de synthèse
    synchroniser_listes(data_globale)
    print("✅ Processus terminé et fichiers mis à jour.")

if __name__ == "__main__":
    main()

