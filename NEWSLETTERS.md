# Ajouter une newsletter

Abonnez l'adresse du compte de veille à la newsletter, puis ajoutez une ligne dans `newsletters.csv` :

```csv
categorie,source,expediteur
finance,Nom de la lettre,newsletter@exemple.fr
régulation,Autre lettre,@exemple.org
```

Les catégories `conseil-etat` et `cour-comptes` alimentent les onglets dédiés à ces institutions. Les newsletters du Conseil d'État sont affichées avec ses autres flux dans le sous-onglet « Flux RSS » de la page Juridictions.

- `categorie` doit correspondre à un onglet du site : `finance`, `news`, `revues`, `blogs`, `institutions`, `juridictions`, `régulation`, `conseil-etat` ou `cour-comptes`.
- `expediteur` accepte une adresse exacte ou un domaine commençant par `@`.
- Aucun filtre de sujet ni site public n'est requis. Le premier lien éditorial sûr trouvé dans la newsletter est utilisé.
- Si plusieurs règles correspondent, la première ligne est utilisée.

Les messages dont l'objet évoque une confirmation, une activation, une création de compte, un mot de passe ou un code de sécurité sont ignorés automatiquement.

Le script lit la boîte sans marquer ni déplacer les messages. Par défaut, il réutilise `EMAIL_SENDER` et `EMAIL_PASSWORD`, se connecte à `imap.bookmyname.com:993`, lit `INBOX` et examine les 30 derniers jours.

Ces réglages peuvent être remplacés par `IMAP_USERNAME`, `IMAP_PASSWORD`, `IMAP_HOST`, `IMAP_PORT`, `IMAP_FOLDER` et `IMAP_LOOKBACK_DAYS`.
