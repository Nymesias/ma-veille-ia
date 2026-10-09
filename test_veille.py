import sys
import types
import unittest
from unittest.mock import Mock, mock_open, patch


def dependance_absente(*args, **kwargs):
    raise AssertionError("Cette dépendance ne doit pas être appelée par ce test unitaire")


feedparser = types.ModuleType("feedparser")
feedparser.parse = dependance_absente
requests = types.ModuleType("requests")
requests.get = dependance_absente
requests.post = dependance_absente
bs4 = types.ModuleType("bs4")
bs4.BeautifulSoup = dependance_absente
sys.modules.setdefault("feedparser", feedparser)
sys.modules.setdefault("requests", requests)
sys.modules.setdefault("bs4", bs4)

import veille


class VeilleTests(unittest.TestCase):
    def test_questions_senat_extraient_questions_et_reponses_du_dernier_jo(self):
        def bloc_senat(libelle, nature, numero, sujet, rubrique):
            entete = Mock()
            entete.get_text.return_value = libelle
            titre_rubrique = Mock()
            titre_rubrique.get_text.return_value = rubrique
            lien = Mock()
            lien.get.side_effect = lambda attribut, repli="": {
                "href": f"/questions/base/2026/q{numero}.html",
            }.get(attribut, repli)
            lien.get_text.return_value = numero
            ligne = Mock()
            ligne.select_one.return_value = lien
            ligne.get_text.return_value = f"{numero} de Mme Exemple : {sujet}"
            ligne.find_previous.return_value = titre_rubrique
            bloc = Mock()
            bloc.select_one.return_value = entete
            bloc.select.return_value = [ligne]
            return bloc

        blocs = [
            bloc_senat(
                "Questions publiées dans le JO du 8 octobre 2026",
                "Question", "10249", "Déploiement de la fibre", "Numérique",
            ),
            bloc_senat(
                "Réponses publiées dans le JO du 8 octobre 2026",
                "Réponse", "09370", "Retards de paiement", "Économie",
            ),
        ]
        with patch("veille.BeautifulSoup") as analyseur:
            analyseur.return_value.select.return_value = blocs
            entrees = veille.entrees_questions_senat(
                "<html>", "https://www.senat.fr/questions/base/index.html"
            )

        self.assertEqual([item["date_normalisee"] for item in entrees], [
            "2026-10-08", "2026-10-08",
        ])
        self.assertEqual([item["tags"][0]["term"] for item in entrees], [
            "Question", "Réponse",
        ])
        self.assertIn("Déploiement de la fibre", entrees[0]["title"])

    def test_questions_assemblee_distinguent_date_question_et_date_reponse(self):
        lien = Mock()
        lien.get.side_effect = lambda attribut, repli="": {
            "href": "/dyn/17/questions/QANR5L17QE17128",
        }.get(attribut, repli)
        lien.get_text.return_value = "Question écrite n° 17128 de M. Xavier Albertini"
        carte = Mock()
        carte.select_one.return_value = lien
        carte.get_text.return_value = (
            "21 juillet 2026 Voir la question Question écrite n° 17128 "
            "Rubrique : fonctionnaires et agents publics "
            "Titre : Rupture conventionnelle dans la fonction publique "
            "Réponse publiée le 6 octobre 2026"
        )

        with patch("veille.BeautifulSoup") as analyseur:
            analyseur.return_value.select.return_value = [carte]
            questions = veille.entrees_questions_assemblee(
                "<html>", "https://www.assemblee-nationale.fr/dyn/questions/recherche",
                "html-an-questions",
            )
            reponses = veille.entrees_questions_assemblee(
                "<html>", "https://www.assemblee-nationale.fr/dyn/questions/recherche",
                "html-an-reponses",
            )

        self.assertEqual(questions[0]["date_normalisee"], "2026-07-21")
        self.assertEqual(reponses[0]["date_normalisee"], "2026-10-06")
        self.assertEqual(reponses[0]["tags"][0]["term"], "Réponse")
        self.assertIn("Rupture conventionnelle", reponses[0]["title"])

    def test_page_officielle_extrait_titre_lien_et_date(self):
        bloc = Mock()
        bloc.get_text.return_value = "Publication institutionnelle 25 septembre 2026"
        bloc.parent = None
        lien = Mock()
        lien.get.side_effect = lambda attribut, repli="": {
            "href": "/fr/publications/communiques-presse/publication-test",
        }.get(attribut, repli)
        lien.get_text.return_value = "Publication institutionnelle"
        lien.parent = bloc

        with patch("veille.BeautifulSoup") as analyseur:
            analyseur.return_value.select.return_value = [lien]
            entrees = veille.entrees_page_officielle(
                "<html>", "https://www.aft.gouv.fr/fr/communiques-de-presse", "html-aft"
            )

        self.assertEqual(entrees, [{
            "title": "Publication institutionnelle",
            "link": (
                "https://www.aft.gouv.fr/fr/publications/communiques-presse/"
                "publication-test"
            ),
            "date_normalisee": "2026-09-25",
        }])

    def test_page_conseil_etat_extrait_les_cartes_editoriales(self):
        titre = Mock(name="titre")
        titre.name = "a"
        titre.get.return_value = "/actualites/decision-remarquable"
        titre.get_text.return_value = "Une décision remarquable"
        date = Mock()
        date.get.return_value = "2026-10-06"
        resume = Mock()
        resume.get_text.return_value = "Le résumé officiel."
        carte = Mock()
        carte.select_one.side_effect = lambda selecteur: {
            ".list__item__title a, a.list__item__body .list__item__title": titre,
            "time[datetime]": date,
            ".list__item__teasing": resume,
        }.get(selecteur)

        with patch("veille.BeautifulSoup") as analyseur:
            analyseur.return_value.select.return_value = [carte]
            entrees = veille.entrees_page_conseil_etat(
                "<html>", "https://www.conseil-etat.fr/decisions-de-justice/dernieres-decisions"
            )

        self.assertEqual(entrees, [{
            "title": "Une décision remarquable",
            "link": "https://www.conseil-etat.fr/actualites/decision-remarquable",
            "date_normalisee": "2026-10-06",
            "summary": "Le résumé officiel.",
        }])

    @patch("veille.requests.get")
    def test_decisions_administratives_filtrent_les_codes_sans_filtrer_la_juridiction(self, get):
        reponse = Mock()
        reponse.raise_for_status.return_value = None
        reponse.json.return_value = {"decisions": {"body": {"hits": {"hits": [
            {"_source": {
                "Identification": "DCE_1.xml", "Code_Juridiction": "CE",
                "Nom_Juridiction": "Conseil d'État", "Numero_Dossier": "1",
                "Type_Decision": "Décision", "Code_Publication": "A",
                "Date_Lecture": "2026-10-06",
            }},
            {"_source": {
                "Identification": "DCAA75_2.xml", "Code_Juridiction": "CAA75",
                "Nom_Juridiction": "Cour administrative d'appel de Paris",
                "Numero_Dossier": "2", "Type_Decision": "Décision",
                "Code_Publication": "C+", "Date_Lecture": "2026-10-05",
            }},
            {"_source": {
                "Identification": "DTA75_3.xml", "Code_Juridiction": "TA75",
                "Nom_Juridiction": "Tribunal administratif de Paris",
                "Numero_Dossier": "3", "Type_Decision": "Décision",
                "Code_Publication": "C", "Date_Lecture": "2026-10-06",
            }},
            {"_source": {
                "Identification": "DTA75_4.xml", "Code_Juridiction": "TA75",
                "Nom_Juridiction": "Tribunal administratif de Paris",
                "Numero_Dossier": "4", "Type_Decision": "Ordonnance",
                "Code_Publication": "B", "Date_Lecture": "2026-10-06",
            }},
        ]}}}}
        get.return_value = reponse

        with patch("builtins.open", mock_open()), patch("veille.json.dump"):
            decisions = veille.collecter_decisions_conseil_etat()

        self.assertEqual([item["numero"] for item in decisions], ["1", "2"])
        self.assertEqual(
            {item["code_juridiction"] for item in decisions}, {"CE", "CAA75"}
        )
        self.assertTrue(
            all(item["code_publication"] in {"A", "B", "R", "C+"} for item in decisions)
        )

    def test_publication_newsletter_reprend_le_titre_editorial(self):
        image = Mock()
        image.get.side_effect = lambda attribut, repli="": {
            "alt": "Rapport annuel sur les marchés financiers",
        }.get(attribut, repli)
        balise_lien = Mock()
        balise_lien.get_text.return_value = ""
        balise_lien.get.side_effect = lambda attribut, repli="": {
            "href": "https://example.com/publications/rapport",
        }.get(attribut, repli)
        balise_lien.select.return_value = [image]
        balise_lien.parent = None
        lien_accueil = Mock()
        lien_accueil.get_text.return_value = "Cour des comptes"
        lien_accueil.get.side_effect = lambda attribut, repli="": {
            "href": "https://example.com/",
        }.get(attribut, repli)
        lien_accueil.select.return_value = []
        lien_accueil.parent = None

        with patch("veille.BeautifulSoup") as analyseur:
            analyseur.return_value.select.return_value = [lien_accueil, balise_lien]
            titre, lien = veille.publication_principale_newsletter(
                "<html>", "La lettre d'information"
            )

        self.assertEqual(titre, "Rapport annuel sur les marchés financiers")
        self.assertEqual(lien, "https://example.com/publications/rapport")

    def test_publication_newsletter_ignore_un_bouton_generique(self):
        titre_section = Mock()
        titre_section.get_text.return_value = "Sanctions et décisions du mois"
        section = Mock()
        section.select_one.return_value = titre_section
        section.parent = None
        balise_lien = Mock()
        balise_lien.get_text.return_value = "Lire la suite"
        balise_lien.get.side_effect = lambda attribut, repli="": {
            "href": "https://example.com/publications/sanctions",
        }.get(attribut, repli)
        balise_lien.select.return_value = []
        balise_lien.parent = section

        with patch("veille.BeautifulSoup") as analyseur:
            analyseur.return_value.select.return_value = [balise_lien]
            titre, _ = veille.publication_principale_newsletter("<html>", "Newsletter")

        self.assertEqual(titre, "Sanctions et décisions du mois")

    def test_premier_resume_disponible_utilise_seulement_la_source(self):
        donnees = {"title": "Un titre", "description": "<p>Résumé officiel.</p>"}
        self.assertEqual(
            veille.premier_resume_disponible(donnees),
            "Résumé officiel.",
        )
        self.assertEqual(veille.premier_resume_disponible({"title": "Un titre"}), "")

    def test_nettoyer_texte_decode_les_entites_html_doublement_encodees(self):
        self.assertEqual(
            veille.nettoyer_texte(
                "Audience &amp;lt; 14&amp;nbsp;h &amp;#8239;: &amp;gt; suite"
            ),
            "Audience < 14 h : > suite",
        )

    def test_libelles_categories_conserve_les_categories_du_flux(self):
        donnees = {"tags": [
            {"term": "News article"},
            {"term": "carbon neutrality"},
            {"term": "News article"},
        ]}
        self.assertEqual(
            veille.libelles_categories(donnees),
            ["News article", "carbon neutrality"],
        )

    def test_categorie_cedh_traduit_la_nature_du_document(self):
        self.assertEqual(
            veille.categorie_cedh_depuis_resume("12345/20 - Committee Judgment"),
            "Arrêt de comité",
        )

    @patch("veille.BeautifulSoup")
    @patch("veille.contenu_page_officielle")
    def test_themes_pibd_lit_les_rubriques_du_sommaire(self, contenu, analyseur):
        contenu.return_value = "<html>Sommaire</html>"
        titres = []
        for libelle in ("III. Jurisprudence", "Brevets", "Marques"):
            titre = Mock()
            titre.get_text.return_value = libelle
            titres.append(titre)
        sommaire = Mock()
        sommaire.select.return_value = titres
        analyseur.return_value.select_one.return_value = sommaire

        self.assertEqual(
            veille.themes_pibd_depuis_page("https://example.com/pibd"),
            ["Jurisprudence", "Brevets", "Marques"],
        )

    def test_sommaire_reprend_les_titres_des_comptes_rendus(self):
        sommaire = veille.construire_sommaire({
            "news": (
                "# Actualités générales\n\n"
                "## International\n"
                "- **Accord européen** — Un résumé. [Source](https://example.com)"
            ),
        })
        self.assertIn("## News", sommaire)
        self.assertIn("[Accord européen]", sommaire)
        self.assertIn("https://nymesias.github.io/ma-veille-ia/news.html", sommaire)
        self.assertNotIn("Un résumé", sommaire)

    @patch("veille.consommer_quota_gemini")
    @patch("veille.time.sleep")
    @patch("veille.requests.post")
    def test_appel_gemini_reessaie_apres_429(self, post, sleep, consommer):
        limite = Mock(status_code=429, headers={"Retry-After": "1"})
        succes = Mock(status_code=200, headers={})
        succes.json.return_value = {
            "usageMetadata": {"totalTokenCount": 20},
            "candidates": [{"content": {"parts": [{"text": "# Résultat"}]}}],
        }
        post.side_effect = [limite, succes]

        resultat = veille.appeler_gemini("news", [{
            "categorie": "news",
            "source": "Source",
            "titre": "Titre",
            "lien": "https://example.com",
            "date": veille.HIER,
            "resume": "Résumé officiel",
        }], {"tokens": 0})

        self.assertEqual(resultat, "# Résultat")
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(veille.GEMINI_RETRY_BASE_SECONDS)
        consommer.assert_called_once()
        requete = post.call_args_list[-1]
        self.assertIn("gemini-3.8-flash:generateContent", requete.args[0])
        self.assertIn("x-goog-api-key", requete.kwargs["headers"])
        self.assertEqual(
            requete.kwargs["json"]["generationConfig"]["thinkingConfig"]["thinkingLevel"],
            "low",
        )

    @patch("veille.consommer_quota_gemini")
    @patch("veille.time.sleep")
    @patch("veille.requests.post")
    def test_appel_gemini_reessaie_apres_503(self, post, sleep, consommer):
        indisponible = Mock(status_code=503, headers={})
        succes = Mock(status_code=200, headers={})
        succes.json.return_value = {
            "usageMetadata": {"totalTokenCount": 20},
            "candidates": [{"content": {"parts": [{"text": "# Résultat"}]}}],
        }
        post.side_effect = [indisponible, succes]

        resultat = veille.appeler_gemini("news", [{
            "categorie": "news",
            "source": "Source",
            "titre": "Titre",
            "lien": "https://example.com",
            "date": veille.HIER,
            "resume": "Résumé officiel",
        }], {"tokens": 0})

        self.assertEqual(resultat, "# Résultat")
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(veille.GEMINI_RETRY_BASE_SECONDS)
        consommer.assert_called_once()

    @patch("veille.time.sleep")
    @patch("veille.requests.post")
    def test_appel_gemini_signale_une_indisponibilite_persistante(self, post, sleep):
        post.return_value = Mock(
            status_code=503,
            headers={},
            json=lambda: {"error": {"message": "Modèle temporairement surchargé"}},
        )

        with patch("veille.GEMINI_MAX_RETRIES", 2):
            with patch("veille.GEMINI_FALLBACK_MODELS", ()):
                with self.assertRaises(veille.ErreurGeminiTemporaire):
                    veille.appeler_gemini("finance", [{
                        "categorie": "finance",
                        "source": "Source",
                        "titre": "Titre",
                        "lien": "https://example.com",
                        "date": veille.HIER,
                        "resume": "Résumé officiel",
                    }], {"tokens": 0})

        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(veille.GEMINI_RETRY_BASE_SECONDS)

    @patch("veille.consommer_quota_gemini")
    @patch("veille.time.sleep")
    @patch("veille.requests.post")
    def test_appel_gemini_bascule_sur_un_modele_de_secours(self, post, sleep, consommer):
        indisponible = Mock(status_code=503, headers={})
        succes = Mock(status_code=200, headers={})
        succes.json.return_value = {
            "usageMetadata": {"totalTokenCount": 20},
            "candidates": [{"content": {"parts": [{"text": "# Secours"}]}}],
        }
        post.side_effect = [indisponible, succes]

        with patch("veille.GEMINI_MAX_RETRIES", 1):
            with patch("veille.GEMINI_FALLBACK_MODELS", ("gemini-3.7-flash",)):
                resultat = veille.appeler_gemini("news", [{
                    "categorie": "news",
                    "source": "Source",
                    "titre": "Titre",
                    "lien": "https://example.com",
                    "date": veille.HIER,
                    "resume": "Résumé officiel",
                }], {"tokens": 0})

        self.assertEqual(resultat, "# Secours")
        self.assertIn("gemini-3.7-flash:generateContent", post.call_args.args[0])
        sleep.assert_not_called()
        consommer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
