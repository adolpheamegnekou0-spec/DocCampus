# DocCampus

## Démarrer le site avec son API

Le site est servi par FastAPI pour que l'interface, l'authentification et l'API partagent la même origine.

1. Installe Python 3.10 ou plus récent.
2. Dans PowerShell, à la racine du projet, exécute :

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   Copy-Item .env.example .env
   ```

3. Modifie `.env` et remplace `DOCCAMPUS_SECRET_KEY` par une clé aléatoire et privée.
4. Crée le compte administrateur de façon interactive. L'adresse est saisie dans PowerShell et le mot de passe n'est pas affiché :

   ```powershell
   py create_admin.py
   ```

5. Lance le serveur :

   ```powershell
   uvicorn main:app --reload
   ```

6. Ouvre `http://127.0.0.1:8000`. Connecte-toi via le lien « Admin » avec le compte créé.

La base SQLite et les fichiers PDF sont conservés sous `data/`. Ne place pas de secrets dans le code et ne publie pas le fichier `.env`. Pour un déploiement Docker, monte un volume persistant sur `/data`.

Pour Docker, après avoir renseigné `.env` :

```powershell
docker build -t doccampus .
docker run --rm -p 8000:8000 --env-file .env -v doccampus-data:/data doccampus
```

En production, utilise HTTPS et conserve un volume persistant pour `/data`.

## Mettre le site en ligne sur Render

Le fichier `render.yaml` configure le service Docker, le contrôle de santé et un
disque persistant de 1 Go pour la base SQLite et les PDF. Ce disque nécessite
une instance Render payante. Vérifie les tarifs Render avant de créer le service.

1. Publie le projet dans un dépôt GitHub privé. Vérifie que `.env` n'est pas
   inclus : il contient des secrets et est ignoré par Git.
2. Dans Render, choisis **New > Blueprint**, connecte le dépôt, puis applique
   le Blueprint défini par `render.yaml`.
3. Lors de la configuration, renseigne `ADMIN_EMAIL` et un
   `ADMIN_PASSWORD` d'au moins 12 caractères. Render génère
   `DOCCAMPUS_SECRET_KEY` automatiquement. Ne partage pas ces valeurs.
4. Attends la fin du déploiement, puis ouvre l'URL `onrender.com` affichée par
   Render. Vérifie que `/api/health` répond `{"status":"ok"}`, puis connecte-toi
   avec l'adresse et le mot de passe admin configurés.

Les changements poussés sur la branche reliée déclencheront un nouveau
déploiement. Les données SQLite et les PDF restent sur le disque `/data` lors
des redéploiements. Configure des sauvegardes régulières du disque depuis Render.

## Comptes, catégories et documents

Les visiteurs peuvent créer un compte depuis « Se connecter ». `py create_admin.py` crée le compte admin dans la base SQLite configurée par `DOCCAMPUS_DATABASE`. Si l'adresse existe déjà, le script demande confirmation avant de la promouvoir en administrateur et de remplacer son mot de passe. Alternative pour un déploiement automatisé : définir `ADMIN_EMAIL` et `ADMIN_PASSWORD` ensemble dans l'environnement serveur (mot de passe d'au moins 12 caractères). Depuis l'administration, un administrateur peut gérer les comptes, les catégories et les documents PDF. Les PDF doivent faire 30 Mo maximum.

Les fichiers PDF déjà référencés dans la page statique doivent encore être ajoutés depuis l'administration ou copiés dans le projet avec leurs chemins configurés. La version source ne contient pas ces fichiers.

## Paiement Mixx by Yas

Le bouton d'abonnement indique que le paiement est indisponible. L'API et le compte marchand Mixx by Yas n'étant pas encore disponibles, aucun faux paiement ni confirmation d'abonnement n'est simulé. L'intégration de paiement pourra être activée après réception de la documentation officielle et des accès marchands; ne partage pas les clés secrètes dans le chat.
