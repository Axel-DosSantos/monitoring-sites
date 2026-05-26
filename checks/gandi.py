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
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _get_sharing_ids(api_key: str) -> list[Optional[str]]:
    """
    Retourne la liste des sharing_ids à interroger.
    Priorité :
      1. Variable d'env GANDI_SHARING_ID (valeur manuelle, la plus fiable)
      2. Auto-découverte via /organization/organizations (nécessite permission supplémentaire)
      3. Compte personnel uniquement (fallback)
    """
    # 1. Sharing ID défini manuellement
    manual = os.environ.get("GANDI_SHARING_ID", "").strip()
    if manual:
        log.info(f"Gandi : utilisation du sharing_id manuel → {manual}")
        # On interroge aussi le perso au cas où certains domaines y seraient
        return [None, manual]

    # 2. Auto-découverte via l'API organisations
    ids: list[Optional[str]] = [None]
    try:
        resp = requests.get(
            f"{GANDI_API_BASE}/organization/organizations",
            headers=_headers(api_key),
            timeout=15,
        )
        if resp.status_code == 200:
            for org in resp.json():
                org_id = org.get("id") or org.get("sharing_id")
                if org_id and org_id not in ids:
                    ids.append(org_id)
                    log.info(f"Gandi : organisation détectée → {org_id} ({org.get('name', '?')})")
        else:
            log.warning(f"Gandi organisations : HTTP {resp.status_code} — compte perso uniquement")
    except Exception as e:
        log.warning(f"Gandi organisations : erreur ({e}) — compte perso uniquement")

    return ids


def fetch_gandi_domains(api_key: str) -> list[dict]:
    """
    Retourne tous les domaines Gandi (compte perso + organisations).
    Déduplique par FQDN.
    """
    sharing_ids = _get_sharing_ids(api_key)
    log.info(f"Gandi : {len(sharing_ids)} compte(s) à interroger")

    seen: set[str] = set()
    domains: list[dict] = []

    for sharing_id in sharing_ids:
        page = 1
        per_page = 100
        label = f"org={sharing_id}" if sharing_id else "compte perso"

        while True:
            params: dict = {"page": page, "per_page": per_page}
            if sharing_id:
                params["sharing_id"] = sharing_id

            try:
                resp = requests.get(
                    f"{GANDI_API_BASE}/domain/domains",
                    headers=_headers(api_key),
                    params=params,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
            except requests.exceptions.HTTPError as e:
                if resp.status_code == 401:
                    raise ValueError("Clé API Gandi invalide ou expirée (401 Unauthorized)") from e
                log.warning(f"Gandi [{label}] page {page} : HTTP {resp.status_code} — on passe")
                break
            except requests.exceptions.RequestException as e:
                raise ConnectionError(f"Impossible de joindre l'API Gandi : {e}") from e

            if not data:
                break

            for d in data:
                fqdn = d.get("fqdn", "")
                if fqdn and fqdn not in seen:
                    seen.add(fqdn)
                    domains.append(d)

            log.info(f"Gandi [{label}] page {page} : {len(data)} domaine(s)")

            if len(data) < per_page:
                break
            page += 1

    return domains


def check_gandi_domain(domain_data: dict, warn_days: int = 30) -> dict:
    """Analyse un domaine retourné par l'API Gandi."""
    fqdn = domain_data.get("fqdn") or domain_data.get("id", "inconnu")
    autorenew = domain_data.get("autorenew", False)
    if isinstance(autorenew, dict):
        autorenew = autorenew.get("enabled", False)
    autorenew = bool(autorenew)

    expires_raw = (
        domain_data.get("dates", {}).get("registry_ends_at")
        or domain_data.get("dates", {}).get("expires_at")
    )

    if not expires_raw:
        return {
            "fqdn": fqdn, "expires_iso": None, "days_left": None,
            "status": "unknown", "message": "Date d'expiration introuvable",
            "autorenew": autorenew, "nameservers": domain_data.get("nameservers", []),
            "tld": domain_data.get("tld", ""),
        }

    try:
        exp = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        days_left = (exp - datetime.now(timezone.utc)).days
        label = exp.strftime("%d/%m/%Y")
        expires_iso = exp.strftime("%Y-%m-%d")

        if days_left <= 7:
            status, msg = "critical", f"EXPIRE dans {days_left}j ({label})"
        elif days_left <= warn_days:
            status, msg = "warning", f"Expire dans {days_left}j ({label})"
        else:
            status, msg = "ok", f"Valide {days_left}j ({label})"

        if autorenew:
            msg += " — Renouvellement AUTO ✓"

    except (ValueError, TypeError) as e:
        return {
            "fqdn": fqdn, "expires_iso": None, "days_left": None,
            "status": "error", "message": f"Erreur parsing date : {e}",
            "autorenew": autorenew, "nameservers": domain_data.get("nameservers", []),
            "tld": domain_data.get("tld", ""),
        }

    return {
        "fqdn": fqdn, "expires_iso": expires_iso, "days_left": days_left,
        "status": status, "message": msg, "autorenew": autorenew,
        "nameservers": domain_data.get("nameservers", []),
        "tld": domain_data.get("tld", ""),
    }


def get_all_gandi_domains_status(warn_days: int = 30) -> tuple[list[dict], Optional[str]]:
    """Point d'entrée principal."""
    api_key = _get_api_key()
    if not api_key:
        return [], "GANDI_API_KEY non définie — ajoutez-la dans vos variables d'environnement"

    try:
        raw_domains = fetch_gandi_domains(api_key)
    except (ValueError, ConnectionError) as e:
        return [], str(e)

    log.info(f"Gandi : {len(raw_domains)} domaines récupérés au total")
    return [check_gandi_domain(d, warn_days) for d in raw_domains], None
