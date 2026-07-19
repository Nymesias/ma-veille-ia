let rssData = null;
const versionsRendu = new WeakMap();

function commencerRendu(container) {
    const version = (versionsRendu.get(container) || 0) + 1;
    versionsRendu.set(container, version);
    return version;
}

function renduToujoursActif(container, version) {
    return versionsRendu.get(container) === version;
}

function echapperHtml(valeur) {
    return String(valeur ?? '').replace(/[&<>'"]/g, caractere => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    })[caractere]);
}

function formaterNomSource(nom) {
    return String(nom ?? '').replaceAll('|', ' — ');
}

function inventaireSources(sources) {
    const badges = sources.map(source => {
        const estNewsletter = source.type === 'newsletter';
        return `<span class="source-inventaire-badge${estNewsletter ? ' source-inventaire-newsletter' : ''}">
            ${echapperHtml(formaterNomSource(source.nom_site))}
            ${estNewsletter ? '<span aria-label="Newsletter">✉</span>' : ''}
        </span>`;
    }).join('');

    return `<aside class="source-inventaire" aria-label="Sources suivies">
        <strong>Sources suivies (${sources.length})</strong>
        <div>${badges}</div>
    </aside>`;
}

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
        const response = await fetch('liste_rss.json', { cache: 'no-store' });
        if (!response.ok) throw new Error("Fichier liste_rss.json introuvable");
        rssData = await response.json();
        console.log("Données JSON chargées :", Object.keys(rssData));
    } catch (e) {
        console.error("Erreur initialisation :", e);
    }
}

function reclasserSourcesRSS(categorieCible, categoriesOrigine, prefixesSources) {
    if (!rssData) return;
    const cible = rssData[categorieCible] ||= [];
    const prefixes = prefixesSources.map(prefixe => prefixe.toLocaleLowerCase('fr-FR'));
    categoriesOrigine.forEach(categorie => {
        if (!Array.isArray(rssData[categorie])) return;
        const aDeplacer = rssData[categorie].filter(source =>
            prefixes.some(prefixe => source.nom_site.toLocaleLowerCase('fr-FR').startsWith(prefixe))
        );
        rssData[categorie] = rssData[categorie].filter(source => !aDeplacer.includes(source));
        aDeplacer.forEach(source => {
            const existante = cible.find(item => item.nom_site === source.nom_site);
            if (!existante) {
                cible.push(source);
                return;
            }
            const articlesConnus = new Set(existante.articles.map(article => `${article.l}|${article.t}`));
            source.articles.forEach(article => {
                if (!articlesConnus.has(`${article.l}|${article.t}`)) existante.articles.push(article);
            });
        });
    });
}

// On ajoute le paramètre "modeTri" avec une valeur par défaut
async function chargerFluxRSS(nomCategorie, idContainer, modeTri = 'date', sourcesExclues = []) {
    const container = document.getElementById(idContainer);
    if (!container) return;
    const versionRendu = commencerRendu(container);

    container.innerHTML = "<p style='text-align:center;'>Chargement des flux...</p>";

    if (!rssData) {
        await initialiserProjet();
    }
    if (!renduToujoursActif(container, versionRendu)) return;

    // Recherche de la catégorie dans le JSON
    const cleReelle = Object.keys(rssData).find(
        k => k.trim().toLowerCase() === nomCategorie.trim().toLowerCase()
    );

    const exclusionsNormalisees = sourcesExclues.map(source => source.toLocaleLowerCase('fr-FR'));
    const sources = cleReelle ? rssData[cleReelle].filter(source =>
        !exclusionsNormalisees.some(exclusion => source.nom_site.toLocaleLowerCase('fr-FR').startsWith(exclusion))
    ) : [];
    if (sources.length === 0) {
        container.innerHTML = `<p>Aucune donnée pour "${nomCategorie}".</p>`;
        return;
    }

    // --- ÉTAPE CLÉ : ON REGROUPE TOUT POUR TRIER ---
    let tousLesArticles = [];
    sources.forEach(source => {
        (source.articles || []).forEach(art => {
            tousLesArticles.push({
                ...art,
                nom_site: source.nom_site, // On attache le nom du site à l'article
                type: art.type || source.type
            });
        });
    });

    const sourcesAffichees = new Set(tousLesArticles.map(article => article.nom_site));
    const sourcesSansArticles = sources.filter(source => !sourcesAffichees.has(source.nom_site));

    // --- LOGIQUE DE TRI ---
    if (modeTri === 'date') {
        // Tri par date (plus récent en haut)
        tousLesArticles.sort((a, b) => new Date(b.d) - new Date(a.d));
    } else {
        // Tri par Source (A-Z) puis par date
        tousLesArticles.sort((a, b) => a.nom_site.localeCompare(b.nom_site) || new Date(b.d) - new Date(a.d));
    }

    // --- AFFICHAGE FINAL ---
    if (tousLesArticles.length === 0 && sourcesSansArticles.length === 0) {
        container.innerHTML = `<p>Aucune publication récente pour "${nomCategorie}".</p>`;
        return;
    }

    let htmlContenu = inventaireSources(sources);
    tousLesArticles.forEach(art => {
        const dateAffichage = formaterDateEnFrancais(art.d);
        const estNewsletter = art.type === 'newsletter';
        const titre = art.l
            ? `<a href="${echapperHtml(art.l)}" target="_blank" rel="noopener noreferrer">${echapperHtml(art.t)}</a>`
            : echapperHtml(art.t);
        htmlContenu += `
            <article class="post-veille${estNewsletter ? ' post-newsletter' : ''}">
                <span class="badge-site">${echapperHtml(formaterNomSource(art.nom_site))}</span>
                ${estNewsletter ? '<span class="badge-newsletter">Newsletter</span>' : ''}
                <h3>${titre}</h3>
                <p class="date-rss">📅 ${dateAffichage}</p> 
            </article>`;
    });
    sourcesSansArticles.forEach(source => {
        const estNewsletter = source.type === 'newsletter';
        htmlContenu += `
            <article class="post-veille${estNewsletter ? ' post-newsletter' : ''}">
                <span class="badge-site">${echapperHtml(formaterNomSource(source.nom_site))}</span>
                ${estNewsletter ? '<span class="badge-newsletter">Newsletter</span>' : ''}
                <h3>Aucune publication reçue récemment.</h3>
            </article>`;
    });

    if (renduToujoursActif(container, versionRendu)) {
        container.innerHTML = htmlContenu;
    }
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
    const versionRendu = commencerRendu(container);
    try {
        const res = await fetch('liste_md.json', { cache: 'no-store' });
        const liste = await res.json();
        const fichiers = liste.filter(item => {
            const nomSeul = item.nom_fichier.split('/').pop();
            return item.nom_fichier.toLowerCase().includes(motCle.toLowerCase())
                && !nomSeul.startsWith('synthese-');
        });
        const chargerArticle = async item => {
            const mdRes = await fetch('markdown/' + item.nom_fichier, { cache: 'no-store' });
            const text = await mdRes.text();
            return `
                    <article class="post-md">
                        <small>Publiée le ${item.date_affichage}</small>
                        <div>${marked.parse(text)}</div>
                    </article>`;
        };
        if (fichiers.length === 0) {
            if (renduToujoursActif(container, versionRendu)) container.innerHTML = "";
            return;
        }
        const articleRecent = await chargerArticle(fichiers[0]);
        if (!renduToujoursActif(container, versionRendu)) return;
        container.innerHTML = articleRecent;

        const articlesArchives = await Promise.all(fichiers.slice(1).map(chargerArticle));
        if (!renduToujoursActif(container, versionRendu)) return;
        container.innerHTML = articleRecent + articlesArchives.join('');
    } catch (e) {
        if (renduToujoursActif(container, versionRendu)) {
            container.innerHTML = "<p>Erreur analyses.</p>";
        }
    }
}
