# Ajouter une newsletter

Abonnez l'adresse du compte de veille à la newsletter, puis ajoutez une ligne dans `newsletters.csv` :

```csv
categorie,source,expediteur,sujet,url_publique
finance,Nom de la lettre,newsletter@exemple.fr,*,https://exemple.fr/actualites
régulation,Autre lettre,@exemple.org,*hebdo*,https://exemple.org/publications
```

- `categorie` doit correspondre à un onglet du site : `finance`, `news`, `revues`, `blogs`, `institutions`, `juridictions` ou `régulation`.
- `expediteur` accepte une adresse exacte, un domaine commençant par `@`, ou un motif avec `*`.
- Mettez `*` dans `sujet` pour accepter tous les objets, ou un motif tel que `*lettre mensuelle*`.
- `url_publique` doit être une page publique sans jeton de connexion ni paramètre personnel. Les liens présents dans les courriels ne sont jamais publiés.
- Si plusieurs règles correspondent, la première ligne est utilisée.

Les messages dont l'objet évoque une confirmation, une activation, une création de compte, un mot de passe ou un code de sécurité sont ignorés automatiquement.

Le script lit la boîte sans marquer ni déplacer les messages. Par défaut, il réutilise `EMAIL_SENDER` et `EMAIL_PASSWORD`, se connecte à `imap.bookmyname.com:993`, lit `INBOX` et examine les 30 derniers jours.

Ces réglages peuvent être remplacés par `IMAP_USERNAME`, `IMAP_PASSWORD`, `IMAP_HOST`, `IMAP_PORT`, `IMAP_FOLDER` et `IMAP_LOOKBACK_DAYS`.
