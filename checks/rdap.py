"""Date d'expiration d'un nom de domaine via RDAP (le successeur officiel du WHOIS).

Pourquoi RDAP plutot que WHOIS :
- passe en HTTPS (port 443) -> fonctionne depuis GitHub Actions, la ou le WHOIS
  port 43 echoue souvent ("Whois command returned no output" sur .legal)
- reponse JSON normalisee, pas de parsing de texte propre a chaque registre

Le serveur RDAP de chaque extension est donne par le fichier de bootstrap IANA.
Extensions sans RDAP public (ex. .eu) : retourne None, le WHOIS prend le relais.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import requests

log = logging.getLogger(__name__)

IANA_BOOTSTRAP = "https://data.iana.org/rdap/dns.json"

# Secours si le bootstrap IANA est injoignable (valeurs IANA au 08/10/2026)
FALLBACK_SERVERS = {
    "com": "https://rdap.verisign.com/com/v1/",
    "net": "https://rdap.verisign.com/net/v1/",
    "fr": "https://rdap.nic.fr/",
    "org": "https://rdap.publicinterestregistry.org/rdap/",
    "legal": "https://rdap.identitydigital.services/rdap/",
}

_servers: Optional[dict[str, str]] = None


def _load_servers() -> dict[str, str]:
    global _servers
    if _servers is not None:
        return _servers
    servers = dict(FALLBACK_SERVERS)
    try:
        resp = requests.get(IANA_BOOTSTRAP, timeout=15)
        resp.raise_for_status()
        for tlds, urls in resp.json().get("services", []):
            https = [u for u in urls if u.startswith("https://")] or urls
            if not https:
                continue
            for tld in tlds:
                servers[tld.lower()] = https[0]
    except Exception as e:  # pragma: no cover - reseau
        log.warning(f"RDAP : bootstrap IANA indisponible ({e}) — serveurs par defaut")
    _servers = servers
    return servers


def rdap_expiration(domain: str, timeout: int = 15) -> Optional[datetime]:
    """Retourne la date d'expiration (UTC) ou None si indisponible."""
    domain = domain.strip().lower().rstrip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    tld = domain.rsplit(".", 1)[-1]
    base = _load_servers().get(tld)
    if not base:
        return None
    url = base.rstrip("/") + "/domain/" + domain
    try:
        resp = requests.get(url, timeout=timeout, headers={"Accept": "application/rdap+json"})
        if resp.status_code != 200:
            log.info(f"RDAP {domain} : HTTP {resp.status_code}")
            return None
        return parse_expiration(resp.json())
    except Exception as e:  # pragma: no cover - reseau
        log.info(f"RDAP {domain} : erreur {e}")
        return None


def parse_expiration(data: dict) -> Optional[datetime]:
    for ev in data.get("events", []) or []:
        if ev.get("eventAction") == "expiration" and ev.get("eventDate"):
            raw = ev["eventDate"].replace("Z", "+00:00")
            # Python < 3.11 n'accepte pas plus de 6 decimales
            if "." in raw:
                head, _, tail = raw.partition(".")
                frac = "".join(c for c in tail if c.isdigit())
                tz = tail[len(frac):]
                raw = f"{head}.{frac[:6]}{tz}"
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None
