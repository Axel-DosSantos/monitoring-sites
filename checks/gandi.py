"""Récupération des domaines Gandi via l'API v5 et vérification de leur expiration."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests

log = logging.getLogger(__name__)

GANDI_API_BASE = "https://api.gandi.net/v5"


def _get_api_key() -> Optional[str]:
    return os.environ.get("GANDI_API_KEY") or os.environ.get("GANDI_PAT")


def _headers(api_key: str) -> dict:
    # Gandi supporte à la fois l'ancienne API Key et les Personal Access Tokens (PAT)
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def fetch_gandi_domains(api_key: str) -> list[dict]:
    """Retourne la liste brute des domaines depuis l'API Gandi (toutes les pages)."""
    domains = []
    page = 1
    per_page = 100

    while True:
        url = f"{GANDI_API_BASE}/domain/domains"
        params = {"page": page, "per_page": per_page}
        try:
            resp = requests.get(url, headers=_headers(api_key), params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 401:
                raise ValueError("Clé API Gandi invalide ou expirée (401 Unauthorized)") from e
            raise
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Impossible de joindre l'API Gandi : {e}") from e

        if not data:
            break
        domains.extend(data)
        if len(data) < per_page:
            break
        page += 1

    return domains


def check_gandi_domain(domain_data: dict, warn_days: int = 30) -> dict:
    """
    Analyse un domaine retourné par l'API Gandi.

    Retourne un dict avec :
        fqdn        : str  - nom de domaine complet
        expires_iso : str  - date d'expiration ISO (YYYY-MM-DD)
        days_left   : int  - jours avant expiration
        status      : str  - 'ok' | 'warning' | 'critical'
        message     : str  - message lisible
        autorenew   : bool - renouvellement automatique activé
        nameservers : list - serveurs de noms
        tld         : str  - extension (.fr, .com, …)
    """
    fqdn = domain_data.get("fqdn") or domain_data.get("id", "inconnu")
    autorenew = (domain_data.get("autorenew") or {}).get("enabled", False)

    # Date d'expiration (format ISO retourné par Gandi)
    expires_raw = domain_data.get("dates", {}).get("registry_ends_at") or \
                  domain_data.get("dates", {}).get("expires_at")

    if not expires_raw:
        return {
            "fqdn": fqdn,
            "expires_iso": None,
            "days_left": None,
            "status": "unknown",
            "message": "Date d'expiration introuvable dans l'API",
            "autorenew": autorenew,
            "nameservers": domain_data.get("nameservers", []),
            "tld": domain_data.get("tld", ""),
        }

    try:
        # Gandi renvoie des dates ISO 8601 : "2025-11-14T00:00:00Z"
        exp = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        days_left = (exp - datetime.now(timezone.utc)).days
        label = exp.strftime("%d/%m/%Y")
        expires_iso = exp.strftime("%Y-%m-%d")

        if days_left <= 7:
            status = "critical"
            msg = f"EXPIRE dans {days_left}j ({label})"
        elif days_left <= warn_days:
            status = "warning"
            msg = f"Expire dans {days_left}j ({label})"
        else:
            status = "ok"
            msg = f"Valide {days_left}j ({label})"

        # Info renouvellement auto
        if autorenew:
            msg += " — Renouvellement AUTO ✓"

    except (ValueError, TypeError) as e:
        return {
            "fqdn": fqdn,
            "expires_iso": None,
            "days_left": None,
            "status": "error",
            "message": f"Erreur parsing date : {e}",
            "autorenew": autorenew,
            "nameservers": domain_data.get("nameservers", []),
            "tld": domain_data.get("tld", ""),
        }

    return {
        "fqdn": fqdn,
        "expires_iso": expires_iso,
        "days_left": days_left,
        "status": status,
        "message": msg,
        "autorenew": autorenew,
        "nameservers": domain_data.get("nameservers", []),
        "tld": domain_data.get("tld", ""),
    }


def get_all_gandi_domains_status(warn_days: int = 30) -> tuple[list[dict], Optional[str]]:
    """
    Point d'entrée principal.
    Retourne (liste de résultats, message_erreur_ou_None).
    """
    api_key = _get_api_key()
    if not api_key:
        return [], "GANDI_API_KEY non définie — ajoutez-la dans vos variables d'environnement"

    try:
        raw_domains = fetch_gandi_domains(api_key)
    except (ValueError, ConnectionError) as e:
        return [], str(e)

    log.info(f"Gandi : {len(raw_domains)} domaines récupérés")

    results = []
    for d in raw_domains:
        result = check_gandi_domain(d, warn_days=warn_days)
        results.append(result)
        log.debug(f"  {result['fqdn']} → {result['status']} ({result['message']})")

    return results, None
