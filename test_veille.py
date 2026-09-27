import sys
import types
import unittest
from unittest.mock import Mock, patch


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


if __name__ == "__main__":
    unittest.main()
