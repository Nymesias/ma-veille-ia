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


if __name__ == "__main__":
    unittest.main()
