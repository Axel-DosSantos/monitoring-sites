# Plan de déploiement — monitoring-sites + namecheap-backup

Ce document liste **tout ce qui reste à faire côté utilisateur** pour activer
les 7 améliorations (response time, backup, Core Web Vitals, historique SQLite,
détection stack, dashboard enrichi, GitHub Pages).

Le code est déjà écrit et en place. Il ne reste que de la configuration
(secrets, repos, activations) et les commits.

---

## Résumé des 4 étapes

1. **Créer un repo public pour `backup_status.json`** (source de vérité backups)
2. **Configurer `namecheap-backup`** pour publier ce fichier (PAT + .env)
3. **Configurer `monitoring-sites`** (secrets GitHub + Pages)
4. **Commit / push / test** des deux projets

Temps estimé : **30 à 45 minutes** (surtout du clic dans l'interface GitHub).

---

## Étape 1 — Créer le repo `backup-status`

Ce repo sert uniquement à héberger le fichier `backup_status.json`. Il doit
être **public** (pour que `monitoring-sites` puisse lire le fichier raw sans
authentification).

1. Aller sur https://github.com/new
2. Renseigner :
   - **Owner** : ton compte (ou l'organisation albys si tu en as une)
   - **Repository name** : `backup-status`
   - **Public** (important — sinon `BACKUP_STATUS_URL` ne marchera pas sans token)
   - Cocher **Add a README file** (pour initialiser la branche `main`)
3. Créer le repo.
4. Noter l'URL raw qui servira plus tard :

   ```
   https://raw.githubusercontent.com/<owner>/backup-status/main/backup_status.json
   ```

   Exemple si owner = `axeldossantos47` :
   `https://raw.githubusercontent.com/axeldossantos47/backup-status/main/backup_status.json`

---

## Étape 2 — Configurer `namecheap-backup`

### 2.1 Créer un Personal Access Token (PAT) GitHub

Le projet `namecheap-backup` a besoin d'un token pour écrire dans le repo
`backup-status`.

1. Aller sur https://github.com/settings/tokens?type=beta
   (Fine-grained token recommandé)
2. **Generate new token** :
   - **Token name** : `namecheap-backup-status`
   - **Expiration** : 1 an (ou plus si tu veux)
   - **Repository access** : *Only select repositories* → choisir
     `<owner>/backup-status`
   - **Repository permissions** :
     - **Contents** : *Read and write*
     - (tout le reste : No access)
3. **Generate token** et **copier la valeur** (elle commence par `github_pat_...`).
   Elle ne sera plus affichée ensuite.

### 2.2 Mettre à jour le `.env` du projet namecheap-backup

Ouvrir :

```
C:\Users\AxelDosSantos\OneDrive - ALBYS\Documents\Claude\Projects\Automatisation Sauvegarde site internet\namecheap-backup\.env
```

Ajouter ces 4 lignes à la fin (garder tout le reste intact) :

```
STATUS_GITHUB_TOKEN=github_pat_xxxxxxxxxxxxxxxx
STATUS_GITHUB_REPO=<owner>/backup-status
STATUS_GITHUB_PATH=backup_status.json
STATUS_GITHUB_BRANCH=main
```

Remplacer `<owner>` par ton nom d'utilisateur GitHub et coller le token réel.

### 2.3 Tester le push en local

Dans le dossier `namecheap-backup`, lancer un backup normal :

```powershell
node backup.js
```

À la fin tu dois voir dans les logs :

```
[publish] backup_status.json ecrit localement : ...
[publish] Push GitHub OK : https://github.com/<owner>/backup-status/...
[publish] URL raw (a mettre dans BACKUP_STATUS_URL) : https://raw.githubusercontent.com/<owner>/backup-status/main/backup_status.json
```

Vérifier ensuite dans le navigateur que le fichier est bien visible :
`https://github.com/<owner>/backup-status/blob/main/backup_status.json`

### 2.4 Commit + push de namecheap-backup

```powershell
cd "C:\Users\AxelDosSantos\OneDrive - ALBYS\Documents\Claude\Projects\Automatisation Sauvegarde site internet\namecheap-backup"
git add backup.js publishStatus.js .gitignore
git commit -m "feat: publication backup_status.json vers GitHub"
git push
```

> **Important** : `.env` et `backup_status.json` sont déjà dans `.gitignore`,
> donc le token ne sera pas commité. Vérifie avec `git status` qu'il n'apparaît
> pas avant de push.

---

## Étape 3 — Configurer `monitoring-sites`

### 3.1 Créer la clé PageSpeed Insights (optionnel mais recommandé)

Sans cette clé, les Core Web Vitals ne seront mesurés que si tu lances
`python monitor.py --psi` manuellement (l'endpoint public est rate-limité).

1. Aller sur https://console.cloud.google.com/
2. Créer un projet (ou en choisir un existant) : `monitoring-albys`
3. **APIs & Services** → **Library** → chercher **PageSpeed Insights API**
   → **Enable**
4. **Credentials** → **Create credentials** → **API key**
5. Copier la clé. Tu peux la restreindre par API (PageSpeed Insights uniquement)
   pour la sécurité.

### 3.2 Ajouter les secrets GitHub sur le repo monitoring-sites

Aller sur https://github.com/<owner>/monitoring-sites/settings/secrets/actions

Créer ces secrets (ceux qui existent déjà n'ont pas besoin d'être refaits) :

| Secret              | Valeur                                                       |
|---------------------|--------------------------------------------------------------|
| `SMTP_HOST`         | `smtp.office365.com` (déjà configuré sans doute)             |
| `SMTP_PORT`         | `587`                                                         |
| `SMTP_USER`         | `expediteur@albys.com`                                        |
| `SMTP_PASSWORD`     | mot de passe d'application                                    |
| `EMAIL_SUPPORT`     | `supportcsm@albys.com`                                        |
| `EMAIL_AXEL`        | `axel.dos-santos@albys.com`                                   |
| **`PSI_API_KEY`**   | **nouveau** — clé créée à l'étape 3.1                         |
| **`BACKUP_STATUS_URL`** | **nouveau** — URL raw de l'étape 1                        |

### 3.3 Activer GitHub Pages

1. Aller sur https://github.com/<owner>/monitoring-sites/settings/pages
2. **Source** : *GitHub Actions* (pas *Deploy from a branch*)
3. Sauvegarder.

> Le workflow `.github/workflows/monitor.yml` est déjà configuré pour déployer
> automatiquement sur Pages via `actions/deploy-pages@v4`.

### 3.4 Commit + push du projet monitoring-sites

Le dossier `monitoring-sites-main` n'est pas encore versionné. Initialiser le
repo localement si ce n'est pas déjà fait :

```powershell
cd "C:\Users\AxelDosSantos\Downloads\monitoring-sites-main\monitoring-sites-main"
git init
git remote add origin https://github.com/<owner>/monitoring-sites.git
git add .
git commit -m "feat: monitoring complet (response time, backup, CWV, history, stack, dashboard, pages)"
git branch -M main
git push -u origin main
```

Si le repo existe déjà avec du code, faire simplement :

```powershell
git add .
git commit -m "feat: monitoring complet (response time, backup, CWV, history, stack, dashboard, pages)"
git push
```

---

## Étape 4 — Premier run et vérifications

### 4.1 Déclencher manuellement le workflow

1. Aller sur https://github.com/<owner>/monitoring-sites/actions
2. Cliquer sur le workflow **Monitoring quotidien**
3. **Run workflow** → cocher `run_psi` pour le premier run (histoire d'avoir
   les Core Web Vitals dès la première fois) → **Run workflow**.

Le workflow fait :
- clone du repo
- pip install
- `python monitor.py` (checks + emails si alertes)
- `python build_static.py` (génère `public/index.html`)
- upload du dossier `public/` vers Pages
- commit de `results.json` + `history.db` + `inventaire_monitoring.xlsx`

Durée : ~3 à 5 minutes selon le nombre de sites.

### 4.2 Vérifier le dashboard public

Après le déploiement, le dashboard est accessible à :

```
https://<owner>.github.io/monitoring-sites/
```

Tu dois y voir :
- une carte par site client
- le score de santé (0-100)
- les indicateurs : uptime, response time, SSL, NDD, CMS, PHP, backup, CWV
- un bouton "Voir la tendance 30j" (Chart.js)

### 4.3 Vérifier les emails

Consulter la boîte `supportcsm@albys.com` et `axel.dos-santos@albys.com` pour
t'assurer qu'aucun email parasite n'a été envoyé (normal s'il n'y a pas
d'alerte réelle). Les alertes activées sont :

| Situation                    | Email ?                       |
|------------------------------|-------------------------------|
| Site DOWN                    | oui, support + axel           |
| SSL < 7 jours                | oui, priorité haute           |
| SSL 7-30 jours               | oui                           |
| NDD < 7 jours                | oui, priorité haute           |
| NDD 7-30 jours               | oui                           |
| Backup > 40 jours            | oui (si BACKUP_STATUS_URL OK) |
| PHP en fin de vie            | **non** (dashboard seulement) |

### 4.4 Vérifier l'intégration backup

Sur le dashboard, chaque carte doit afficher soit :
- "Backup : il y a Nj" (si domaine présent dans `backup_status.json`)
- "Backup : non configuré" (si domaine absent — normal pour les sites non
  sauvegardés par namecheap-backup)

Si tout affiche "non configuré" alors que le secret est bien défini, vérifier :
1. Le fichier `backup_status.json` est-il visible en raw sur GitHub ?
2. Le domaine dans l'Excel (colonne B) correspond-il exactement au domaine
   publié par `namecheap-backup` (pas de `www.`, pas de trailing slash, en
   minuscules) ?

---

## Maintenance

### Cron quotidien

Le workflow tourne automatiquement tous les jours à **08h Paris**
(`cron: "0 6 * * *"` en UTC). Pas besoin de relancer manuellement.

### PageSpeed Insights

Par défaut, PSI tourne à chaque run si `PSI_API_KEY` est définie. Si tu
préfères ne mesurer les CWV qu'une fois par semaine pour économiser le quota,
modifier dans `.github/workflows/monitor.yml` :

```yaml
- name: Run monitor
  run: |
    if [ "$(date +%u)" = "1" ]; then
      python monitor.py --psi
    else
      python monitor.py
    fi
```

### Ajout d'un site

Éditer `inventaire_monitoring.xlsx` → onglet **Inventaire**, ajouter une ligne
(colonne C = URL complète, obligatoire). Commit + push → le prochain cron
prendra en compte le nouveau site.

---

## Checklist finale

À cocher au fur et à mesure :

- [ ] Repo `backup-status` créé (public)
- [ ] PAT GitHub créé avec droits Contents:write sur `backup-status`
- [ ] `.env` de namecheap-backup mis à jour (4 variables STATUS_*)
- [ ] Test `node backup.js` en local → `backup_status.json` visible sur GitHub
- [ ] Commit + push de namecheap-backup (backup.js + publishStatus.js + .gitignore)
- [ ] Clé API Google PageSpeed Insights créée
- [ ] Secret `PSI_API_KEY` ajouté sur monitoring-sites
- [ ] Secret `BACKUP_STATUS_URL` ajouté sur monitoring-sites
- [ ] GitHub Pages activé (Source = GitHub Actions)
- [ ] Commit + push de monitoring-sites
- [ ] Workflow déclenché manuellement avec `run_psi` coché
- [ ] Dashboard accessible à `https://<owner>.github.io/monitoring-sites/`
- [ ] Aucun email parasite reçu
- [ ] Les cartes backup affichent bien des dates (et pas "non configuré" pour les sites sauvegardés)

---

## En cas de problème

| Symptôme                                           | Solution                                                    |
|----------------------------------------------------|-------------------------------------------------------------|
| `[publish] Echec push GitHub : Not Found`          | Le repo `backup-status` n'existe pas ou le PAT n'a pas accès|
| `[publish] Echec push GitHub : Bad credentials`    | Le token est invalide ou expiré                             |
| `BACKUP_STATUS_URL` dans les secrets → toujours "non configuré" | L'URL raw est fausse ou le fichier est vide       |
| Pages affiche 404                                  | Settings → Pages → vérifier Source = GitHub Actions          |
| PSI renvoie 429 malgré la clé                      | La clé n'est pas propagée au workflow — vérifier secret      |
| Cron ne tourne pas                                  | Actions désactivées sur le repo (Settings → Actions)         |
