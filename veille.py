import os
import json
import csv
import requests
import html
import feedparser
import re
from datetime import datetime

# --- CONFIGURATION ---
MISTRAL_KEY = os.getenv("MISTRAL_API_KEY")
DOSSIER_MD = "markdown"
FICHIER_SOURCES = "sources.csv"
AUJOURDHUI = datetime.now().strftime("%Y-%m-%d")

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

def main():
    sources = charger_sources()
    data_globale = {}
    contenu_pour_mistral = ""

    print("--- 📡 Récupération des flux ---")
    for s in sources:
        cat_nom = s.get('categorie', 'Général').strip()
        src_name = s.get('source', 'Inconnue')
        url = s.get('url')
        if not url: continue

        try:
            flux = feedparser.parse(url)
            articles = []
            for entry in flux.entries[:8]:
                t = html.unescape(entry.get('title', 'Sans titre')).strip()
                l = entry.get('link', url)
                
                # RÉCUPÉRATION DE LA DATE
                # feedparser normalise la date dans 'published' ou 'updated'
                d = entry.get('published') or entry.get('updated') or AUJOURDHUI
                
                articles.append({
                    "t": t, 
                    "l": l,
                    "d": d  # On ajoute la date ici
                })
                contenu_pour_mistral += f"[{cat_nom}] {src_name} : {t}\n"

            if cat_nom not in data_globale: data_globale[cat_nom] = []
            data_globale[cat_nom].append({"nom_site": src_name, "articles": articles})
        except: pass

    # Synthèse Mistral
    if MISTRAL_KEY and contenu_pour_mistral:
        print("--- 🤖 Synthèse IA ---")
        prompt = f"Expert en veille. Actus du {AUJOURDHUI}. Synthèse par catégories. Sources entre parenthèses.\n\nACTUS :\n{contenu_pour_mistral[:10000]}"
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