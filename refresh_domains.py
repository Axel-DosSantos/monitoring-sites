#!/usr/bin/env python3
"""Rafraichit les dates d'expiration de results_gandi.json via RDAP.

Sans cle API Gandi, results_gandi.json n'est jamais mis a jour : un domaine
renouvele reste affiche "expire". Ce script relit chaque domaine du fichier dans
le registre public (RDAP) et met a jour date, jours restants et statut.

- Ne touche pas a la liste des domaines (seul gandi_monitor.py la connait)
- Garde l'ancienne valeur si le registre ne repond pas (ex. .eu : pas de RDAP)
- N'envoie aucun email : les alertes NDD restent gerees par monitor.py /
  gandi_monitor.py
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from checks.rdap import rdap_expiration
from checks.gandi import check_gandi_domain

BASE_DIR = Path(__file__).parent
RESULTS_GANDI_JSON = BASE_DIR / "results_gandi.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def refresh(path: Path = RESULTS_GANDI_JSON, lookup=rdap_expiration) -> tuple[int, int]:
    if not path.exists():
        log.info("results_gandi.json absent : rien a rafraichir")
        return 0, 0
    data = json.loads(path.read_text(encoding="utf-8"))
    domains = data.get("domains", [])
    ok = failed = 0
    for i, d in enumerate(domains):
        fqdn = d.get("fqdn")
        if not fqdn:
            continue
        exp = lookup(fqdn)
        if exp is None:
            failed += 1
            d["rdap_ok"] = False
            log.info(f"  {fqdn:<35} RDAP indisponible — date conservee ({d.get('expires_iso')})")
            continue
        new = check_gandi_domain({
            "fqdn": fqdn,
            "autorenew": d.get("autorenew", False),
            "nameservers": d.get("nameservers", []),
            "tld": d.get("tld", ""),
            "dates": {"registry_ends_at": exp.isoformat()},
        })
        if new["expires_iso"] != d.get("expires_iso"):
            log.info(f"  {fqdn:<35} {d.get('expires_iso')} -> {new['expires_iso']}")
        new["rdap_ok"] = True
        domains[i] = {**d, **new}
        ok += 1
    data["domains"] = domains
    data["rdap_last_run"] = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M")
    data["critiques"] = sum(1 for r in domains if r.get("status") == "critical")
    data["warnings"] = sum(1 for r in domains if r.get("status") == "warning")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log.info(f"RDAP : {ok} domaine(s) mis a jour, {failed} sans reponse")
    return ok, failed


if __name__ == "__main__":
    refresh()
