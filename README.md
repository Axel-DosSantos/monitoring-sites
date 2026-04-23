# Monitoring Sites Albys — Dashboard multi-sites

Dashboard de sante complet pour les sites clients. Tourne automatiquement
chaque jour sur GitHub Actions, alerte par email en cas de probleme, et publie
une vue statique sur GitHub Pages consultable depuis n'importe ou.

## Ce que le monitoring surveille

Pour chaque site de l'inventaire :

- **Uptime + temps de reponse** (HTTP status + ms)
- **Certificat SSL** (jours restants, emetteur, alerte < 30 jours)
- **Nom de domaine** (date d'expiration WHOIS)
- **Core Web Vitals** (PageSpeed Insights : LCP, CLS, FCP, TTFB, score 0-100)
- **Stack technique** (WordPress + version, PHP version, serveur web)
- **Backups** (date du dernier backup, taille, statut) — via integration avec
  le projet `namecheap-backup`
- **Score de sante global** (0-100, compose de tous les indicateurs)

## Architecture

```
monitoring-sites/
├── inventaire_monitoring.xlsx   <- Source de verite (sites + config SMTP)
├── monitor.py                   <- Orchestrateur (checks + alertes + history)
├── app.py                       <- Interface web Flask locale (localhost:5000)
├── build_static.py              <- Genere public/index.html pour GitHub Pages
├── history.py                   <- SQLite pour les tendances
├── checks/
│   ├── uptime.py                <- HTTP 2xx/3xx + response time
│   ├── ssl_check.py             <- Certificat SSL
│   ├── domain.py                <- WHOIS NDD
│   ├── pagespeed.py             <- Google PageSpeed Insights API
│   ├── stack.py                 <- WP/PHP/serveur detection
│   └── backup.py                <- Fetch backup_status depuis namecheap-backup
├── templates/
│   └── dashboard.html           <- UI Flask (cartes + tendances Chart.js)
├── public/                      <- Build statique (GitHub Pages, ignore par git)
├── results.json                 <- Dernier scan (commit auto par GH Actions)
├── history.db                   <- SQLite (commit auto par GH Actions)
├── requirements.txt
└── .github/workflows/monitor.yml
```

## Fonctionnement

### Cycle quotidien (GitHub Actions, 08h Paris)

1. `monitor.py` :
   - lit l'inventaire Excel (onglet **Inventaire**, colonne C = URL)
   - lance tous les checks pour chaque site
   - fetch `backup_status.json` depuis l'URL `BACKUP_STATUS_URL`
   - ecrit `results.json`, met a jour les colonnes K-P de l'Excel, ajoute
     une ligne dans `history.db`
   - envoie un email d'alerte si SSL/NDD/backup probleme ou site DOWN
2. `build_static.py` :
   - lit `results.json` + `history.db`
   - genere `public/index.html` (standalone, toutes donnees embarquees)
3. GitHub Pages deploie `public/` automatiquement

### Consultation

- **Statique (mobile / public)** : https://<user>.github.io/<repo>/
- **Local (scan manuel possible)** : `python app.py` puis http://localhost:5000

## Installation

### Prerequis Python (local)

```powershell
pip install -r requirements.txt
```

### GitHub Secrets a configurer

| Secret              | Role                                          | Obligatoire |
|---------------------|-----------------------------------------------|-------------|
| `SMTP_HOST`         | smtp.office365.com                            | Oui         |
| `SMTP_PORT`         | 587                                           | Oui         |
| `SMTP_USER`         | expediteur@albys.com                          | Oui         |
| `SMTP_PASSWORD`     | mot de passe d'application                    | Oui         |
| `EMAIL_FROM`        | expediteur (defaut = SMTP_USER)               | Non         |
| `EMAIL_SUPPORT`     | supportcsm@albys.com                          | Oui         |
| `EMAIL_AXEL`        | axel.dos-santos@albys.com                     | Oui         |
| `EMAIL_TEST`        | destinataire du rapport `--test`              | Non         |
| `PSI_API_KEY`       | cle API Google PageSpeed Insights             | Non *       |
| `BACKUP_STATUS_URL` | URL raw du `backup_status.json`               | Non **      |

\* Sans `PSI_API_KEY`, PSI n'est lance que si on passe `--psi` manuellement
(l'endpoint public est rate-limite, pas fiable pour un cron quotidien).
Creer la cle : https://console.cloud.google.com > APIs > PageSpeed Insights API.

\*\* Sans `BACKUP_STATUS_URL`, le dashboard affiche "Non configure" dans la
carte Backup. Voir section suivante.

## Integration avec `namecheap-backup` (Solution 1)

Le projet `namecheap-backup` sauvegarde les sites chaque mois. Apres chaque
run, il ecrit `backup_status.json` localement et le pousse sur un repo public
via la GitHub Contents API.

### Cote `namecheap-backup` (.env)

```
STATUS_GITHUB_TOKEN=ghp_xxxxx           # PAT avec droit "Contents: write"
STATUS_GITHUB_REPO=albys/backup-status  # repo cible (peut etre public)
STATUS_GITHUB_PATH=backup_status.json
STATUS_GITHUB_BRANCH=main
```

### Cote `monitoring-sites` (GitHub Secrets)

```
BACKUP_STATUS_URL=https://raw.githubusercontent.com/albys/backup-status/main/backup_status.json
```

Le dashboard affichera pour chaque site la date du dernier backup + la taille.
Alerte email si > 40 jours sans backup reussi.

## Usage local

### Scan normal (avec alertes email)
```powershell
python monitor.py
```

### Scan avec PageSpeed Insights
```powershell
python monitor.py --psi
```

### Mode test (envoie un rapport complet a EMAIL_TEST)
```powershell
python monitor.py --test
```

### Interface web
```powershell
python app.py
# Ouvrir http://localhost:5000
```

L'interface permet de declencher un scan manuellement via le bouton
"Lancer un scan" (uniquement en local, desactive en CI).

### Generer le dashboard statique
```powershell
python build_static.py
# Fichier genere dans public/index.html
```

## Seuils d'alerte

| Situation                    | Email envoye a                       |
|------------------------------|--------------------------------------|
| Site DOWN                    | support + axel                       |
| SSL expire < 7 jours         | support + axel (priorite haute)      |
| SSL expire 7-30 jours        | support + axel                       |
| NDD expire < 7 jours         | support + axel (priorite haute)      |
| NDD expire 7-30 jours        | support + axel                       |
| Backup > 40 jours            | support + axel                       |
| PHP en fin de vie (<= 8.0)   | support + axel                       |

## Ajouter un nouveau site

Editer `inventaire_monitoring.xlsx`, onglet **Inventaire** :

| Col | Contenu                                            |
|-----|----------------------------------------------------|
| A   | Nom du client                                      |
| B   | Nom de domaine (ex: `monsite.fr`)                  |
| C   | URL complete (ex: `https://monsite.fr`) — **obligatoire** |
| D   | Hebergeur du domaine                               |
| E   | Hebergeur du site                                  |
| G   | Responsable                                        |
| I   | Date d'expiration indicative                       |

Les colonnes K-P sont remplies automatiquement par `monitor.py`.

## Historique et tendances

`history.db` (SQLite) stocke une ligne par site par run, ce qui permet :
- les graphiques Chart.js dans le dashboard (bouton "Voir la tendance 30j")
- de voir l'evolution du score PageSpeed dans le temps
- de detecter les regressions (temps de reponse qui monte progressivement)
- des stats agregees via `/api/summary` (Flask)

## Depannage

| Probleme                              | Solution                                         |
|---------------------------------------|--------------------------------------------------|
| Excel verrouille                      | Fermer le fichier + OneDrive sync avant scan     |
| Echec envoi email                     | Verifier `SMTP_USER` / `SMTP_PASSWORD` secrets   |
| Site marque DOWN alors qu'il est UP   | Verifier la colonne C (https:// requis)          |
| PSI renvoie 429                       | Definir `PSI_API_KEY` dans les secrets           |
| Backup "Non configure"                | Definir `BACKUP_STATUS_URL` dans les secrets     |
| Pages ne se deploie pas               | Activer GitHub Pages dans Settings > Pages       |
