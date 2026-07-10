let rssData = null;

// --- NOUVELLE FONCTION DE FORMATAGE ---
function formaterDateEnFrancais(dateBrute) {
    if (!dateBrute) return "";
    const d = /^\d{4}-\d{2}-\d{2}$/.test(dateBrute)
        ? new Date(`${dateBrute}T12:00:00`)
        : new Date(dateBrute);
    if (isNaN(d.getTime())) return dateBrute; // Retourne brute si format inconnu

    return d.toLocaleDateString('fr-FR', {
        day: 'numeric',
        month: 'long',
        year: 'numeric'
    });
}

async function initialiserProjet() {
    try {
        const response = await fetch('liste_rss.json');
        if (!response.ok) throw new Error("Fichier liste_rss.json introuvable");
        rssData = await response.json();
        console.log("Données JSON chargées :", Object.keys(rssData));
    } catch (e) {
        console.error("Erreur initialisation :", e);
    }
}

function normaliserLien(lien) {
    try {
        const url = new URL(lien, window.location.href);
        url.hash = "";
        return url.href.replace(/\/$/, "");
    } catch (_) {
        return (lien || "").trim().replace(/#.*$/, "").replace(/\/$/, "");
    }
}

async function chargerLiensDesComptesRendus(categorie) {
    const categorieNormalisee = categorie.trim().toLowerCase();
    if (!['finance', 'news'].includes(categorieNormalisee)) return new Set();

    try {
        const res = await fetch('liste_md.json');
        if (!res.ok) return new Set();
        const liste = await res.json();
        const fichiers = liste.filter(item =>
            item.nom_fichier.toLowerCase().includes(categorieNormalisee + '/')
        );
        const contenus = await Promise.all(fichiers.map(async item => {
            const mdRes = await fetch('markdown/' + item.nom_fichier);
            return mdRes.ok ? mdRes.text() : '';
        }));
        const liens = new Set();
        const motifLien = /https?:\/\/[^\s)\]>]+/g;
        contenus.forEach(contenu => {
            (contenu.match(motifLien) || []).forEach(lien =>
                liens.add(normaliserLien(lien))
            );
        });
        return liens;
    } catch (e) {
        console.warn(`Impossible de dédoublonner les comptes-rendus ${categorieNormalisee} :`, e);
        return new Set();
    }
}

// On ajoute le paramètre "modeTri" avec une valeur par défaut
async function chargerFluxRSS(nomCategorie, idContainer, modeTri = 'date') {
    const container = document.getElementById(idContainer);
    if (!container) return;

    container.innerHTML = "<p style='text-align:center;'>Chargement des flux...</p>";

    if (!rssData) {
        await initialiserProjet();
    }

    // Recherche de la catégorie dans le JSON
    const cleReelle = Object.keys(rssData).find(
        k => k.trim().toLowerCase() === nomCategorie.trim().toLowerCase()
    );

    const sources = cleReelle ? rssData[cleReelle] : [];
    const liensDejaRepris = await chargerLiensDesComptesRendus(nomCategorie);

    if (sources.length === 0) {
        container.innerHTML = `<p>Aucune donnée pour "${nomCategorie}".</p>`;
        return;
    }

    // --- ÉTAPE CLÉ : ON REGROUPE TOUT POUR TRIER ---
    let tousLesArticles = [];
    sources.forEach(source => {
        source.articles.forEach(art => {
            if (liensDejaRepris.has(normaliserLien(art.l))) return;
            tousLesArticles.push({
                ...art,
                nom_site: source.nom_site // On attache le nom du site à l'article
            });
        });
    });

    // --- LOGIQUE DE TRI ---
    if (modeTri === 'date') {
        // Tri par date (plus récent en haut)
        tousLesArticles.sort((a, b) => new Date(b.d) - new Date(a.d));
    } else {
        // Tri par Source (A-Z) puis par date
        tousLesArticles.sort((a, b) => a.nom_site.localeCompare(b.nom_site) || new Date(b.d) - new Date(a.d));
    }

    // --- AFFICHAGE FINAL ---
    if (tousLesArticles.length === 0) {
        container.innerHTML = `<p>Aucune publication récente pour "${nomCategorie}".</p>`;
        return;
    }

    let htmlContenu = "";
    tousLesArticles.forEach(art => {
        const dateAffichage = formaterDateEnFrancais(art.d);
        htmlContenu += `
            <article class="post-veille">
                <span class="badge-site">${art.nom_site}</span>
                <h3><a href="${art.l}" target="_blank">${art.t}</a></h3>
                <p class="date-rss">📅 ${dateAffichage}</p> 
            </article>`;
    });

    container.innerHTML = htmlContenu;
}

// --- Tes autres fonctions (chargerRecapDuJour, etc.) restent inchangées en dessous ---
async function chargerRecapDuJour(idContainer) {
    const container = document.getElementById(idContainer);
    if (!container) return;
    try {
        const res = await fetch('liste_md.json');
        const liste = await res.json();
        const syntheseInfo = liste.find(f => f.nom_fichier.split('/').pop().startsWith('synthese-'));
        if (syntheseInfo) {
            const mdRes = await fetch('markdown/' + syntheseInfo.nom_fichier);
            const text = await mdRes.text();
            container.innerHTML = `
                <div class="post-md-index">
                    <h2 style="color: #2c3e50;">Analyse IA du ${syntheseInfo.date_affichage}</h2>
                    <div class="markdown-body">${marked.parse(text)}</div>
                </div>`;
        } else {
            container.innerHTML = "<p>Aucune synthèse disponible.</p>";
        }
    } catch (e) {
        container.innerHTML = "<p>Erreur lors du chargement de la synthèse.</p>";
    }
}

async function chargerMarkdown(motCle, idContainer) {
    const container = document.getElementById(idContainer);
    if (!container) return;
    try {
        const res = await fetch('liste_md.json');
        const liste = await res.json();
        container.innerHTML = "";
        for (const item of liste) {
            const nomSeul = item.nom_fichier.split('/').pop();
            if (item.nom_fichier.toLowerCase().includes(motCle.toLowerCase()) && !nomSeul.startsWith('synthese-')) {
                const mdRes = await fetch('markdown/' + item.nom_fichier);
                const text = await mdRes.text();
                container.innerHTML += `
                    <article class="post-md">
                        <small>Publiée le ${item.date_affichage}</small>
                        <div>${marked.parse(text)}</div>
                    </article>`;
            }
        }
    } catch (e) {
        container.innerHTML = "<p>Erreur analyses.</p>";
    }
}
