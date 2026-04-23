"""Lecture du statut de sauvegarde publie par le projet `namecheap-backup`.

Solution 1 : le script namecheap-backup ecrit un fichier `backup_status.json`
apres chaque run. Ce fichier est :
  - sauvegarde localement (OneDrive), ET
  - publie via GitHub Contents API sur un repo accessible.

Le monitoring lit ce JSON soit depuis une URL (env BACKUP_STATUS_URL, pour CI),
soit depuis un chemin local (env BACKUP_STATUS_PATH, pour execution locale).

Format attendu de backup_status.json :
{
  "last_run": "2026-04-21T10:23:00Z",
  "sites": [
    {
      "domain": "selnhelp.fr",
      "status": "ok",
      "last_backup": "2026-04-21T10:25:00Z",
      "size_mb": 412.5,
      "file": "2026-04-21.tar.gz",
      "error": null
    },
    ...
  ]
}
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)


def load_backup_status():
    """Charge le statut des backups (local ou distant).

    Returns:
        dict {domain -> {status, last_backup, days_since, size_mb, file, error}}
        ou {} si indisponible.
    """
    url = os.environ.get("BACKUP_STATUS_URL", "").strip()
    local = os.environ.get("BACKUP_STATUS_PATH", "").strip()

    data = None
    if url:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
            else:
                log.warning(f"BACKUP_STATUS_URL HTTP {r.status_code}")
        except Exception as e:
            log.warning(f"Impossible de recuperer backup_status distant : {e}")

    if data is None and local:
        try:
            p = Path(local)
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"Impossible de lire backup_status local : {e}")

    if not data or "sites" not in data:
        return {}

    now = datetime.now(timezone.utc)
    indexed = {}
    for site in data["sites"]:
        domain = (site.get("domain") or "").lower().strip()
        if not domain:
            continue

        last_backup = site.get("last_backup")
        days_since = None
        if last_backup:
            try:
                dt = datetime.fromisoformat(last_backup.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                days_since = (now - dt).days
            except Exception:
                pass

        # Statut derive : rouge si > 40j, orange si > 35j
        raw_status = site.get("status", "unknown")
        if raw_status != "ok":
            derived = "error"
        elif days_since is None:
            derived = "unknown"
        elif days_since > 40:
            derived = "critical"
        elif days_since > 35:
            derived = "warning"
        else:
            derived = "ok"

        indexed[domain] = {
            "status": derived,
            "raw_status": raw_status,
            "last_backup": last_backup,
            "days_since": days_since,
            "size_mb": site.get("size_mb"),
            "file": site.get("file"),
            "error": site.get("error"),
        }

    return indexed
