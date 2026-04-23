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
        tuple (status: str, message: str, days_left: int|None)
        status dans {ok, warning, critical, unknown, error, skip}
    """
    if not WHOIS_AVAILABLE:
        return "skip", "python-whois non installe", None
    try:
        domain = re.sub(r"https?://", "", url).split("/")[0].split("?")[0]
        w = whois.whois(domain)
        exp = w.expiration_date
        if isinstance(exp, list):
            exp = exp[0]
        if not exp:
            return "unknown", "Date inconnue", None
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        days = (exp - datetime.now(timezone.utc)).days
        label = exp.strftime("%d/%m/%Y")
        if days <= 7:
            return "critical", f"Expire dans {days}j ({label})", days
        if days <= warn_days:
            return "warning", f"Expire dans {days}j ({label})", days
        return "ok", f"Valide {days}j ({label})", days
    except Exception as e:
        return "error", str(e)[:60], None
