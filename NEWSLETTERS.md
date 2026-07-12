# Ajouter une newsletter

Abonnez l'adresse du compte de veille à la newsletter, puis ajoutez une ligne dans `newsletters.csv` :

```csv
categorie,source,expediteur,sujet
finance,Nom de la lettre,newsletter@exemple.fr,*
régulation,Autre lettre,@exemple.org,*hebdo*
```

- `categorie` doit correspondre à un onglet du site : `finance`, `news`, `revues`, `blogs`, `institutions`, `juridictions` ou `régulation`.
- `expediteur` accepte une adresse exacte, un domaine commençant par `@`, ou un motif avec `*`.
- Mettez `*` dans `sujet` pour accepter tous les objets, ou un motif tel que `*lettre mensuelle*`.
- Si plusieurs règles correspondent, la première ligne est utilisée.

Le script lit la boîte sans marquer ni déplacer les messages. Par défaut, il réutilise `EMAIL_SENDER` et `EMAIL_PASSWORD`, se connecte à `imap.bookmyname.com:993`, lit `INBOX` et examine les 30 derniers jours.

Ces réglages peuvent être remplacés par `IMAP_USERNAME`, `IMAP_PASSWORD`, `IMAP_HOST`, `IMAP_PORT`, `IMAP_FOLDER` et `IMAP_LOOKBACK_DAYS`.
