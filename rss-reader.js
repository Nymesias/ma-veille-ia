let rssData = null;

// --- NOUVELLE FONCTION DE FORMATAGE ---
function formaterDateEnFrancais(dateBrute) {
    if (!dateBrute) return "";
    const d = new Date(dateBrute);
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

async function chargerFluxRSS(nomCategorie, idContainer) {
    const container = document.getElementById(idContainer);
    if (!container) return;

    container.innerHTML = "<p>Chargement des flux...</p>";

    if (!rssData) {
        await initialiserProjet();
    }

    const cleReelle = Object.keys(rssData).find(
        k => k.trim().toLowerCase() === nomCategorie.trim().toLowerCase()
    );

    const sources = cleReelle ? rssData[cleReelle] : [];

    if (sources.length === 0) {
        container.innerHTML = `<p>Aucune donnée pour "${nomCategorie}".</p>`;
        return;
    }

    let htmlContenu = "";
    sources.forEach(source => {
        source.articles.forEach(art => {
            // --- ON UTILISE LA NOUVELLE FONCTION ICI ---
            const dateAffichage = formaterDateEnFrancais(art.d);

            htmlContenu += `
                <article class="post-veille">
                    <span class="badge-site">${source.nom_site}</span>
                    <h3><a href="${art.l}" target="_blank">${art.t}</a></h3>
                    <p class="date-rss">📅 ${dateAffichage}</p> 
                </article>`;
        });
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
        const syntheseInfo = liste.find(f => f.nom_fichier.startsWith('synthese-'));
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
            if (item.nom_fichier.toLowerCase().includes(motCle.toLowerCase()) && !item.nom_fichier.startsWith('synthese-')) {
                const mdRes = await fetch('markdown/' + item.nom_fichier);
                const text = await mdRes.text();
                container.innerHTML += `
                    <article class="post-md">
                        <small>Analyse du ${item.date_affichage}</small>
                        <div>${marked.parse(text)}</div>
                    </article>`;
            }
        }
    } catch (e) {
        container.innerHTML = "<p>Erreur analyses.</p>";
    }
}