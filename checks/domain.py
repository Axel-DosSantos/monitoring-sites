"""Check du nom de domaine via WHOIS (date d'expiration NDD)."""
from __future__ import annotations

import re
from datetime import datetime, timezone

try:
    import whois  # type: ignore
    WHOIS_AVAILABLE = True
except ImportError:
    WHOIS_AVAILABLE = False


def check_domain(url: str, warn_days: int = 30):
    """Verifie la date d'expiration du nom de domaine via WHOIS.

    Returns:
        tuple (status, message, days_left, expires_iso)
        - status      : str dans {ok, warning, critical, unknown, error, skip}
        - message     : str affiche dans le dashboard
        - days_left   : int|None - jours restants avant expiration
        - expires_iso : str|None - date d'expiration ISO (YYYY-MM-DD)
                        utilisee pour detecter un renouvellement de domaine
                        et reset l'etat des alertes J-30/J-15.
    """
    if not WHOIS_AVAILABLE:
        return "skip", "python-whois non installe", None, None
    try:
        domain = re.sub(r"https?://", "", url).split("/")[0].split("?")[0]
        w = whois.whois(domain)
        exp = w.expiration_date
        if isinstance(exp, list):
            exp = exp[0]
        if not exp:
            return "unknown", "Date inconnue", None, None
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        days = (exp - datetime.now(timezone.utc)).days
        label = exp.strftime("%d/%m/%Y")
        expires_iso = exp.strftime("%Y-%m-%d")
        if days <= 7:
            return "critical", f"Expire dans {days}j ({label})", days, expires_iso
        if days <= warn_days:
            return "warning", f"Expire dans {days}j ({label})", days, expires_iso
        return "ok", f"Valide {days}j ({label})", days, expires_iso
    except Exception as e:
        return "error", str(e)[:60], None, None
