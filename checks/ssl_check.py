"""Check du certificat SSL : validite, emetteur, jours restants, date d'expiration ISO."""
from __future__ import annotations

import re
import socket
import ssl
from datetime import datetime, timezone


def _extract_hostname(url: str) -> str:
    return re.sub(r"https?://", "", url).split("/")[0].split("?")[0]


def check_ssl(url: str, warn_days: int = 30):
    """Verifie le certificat SSL.

    Returns:
        tuple (status, message, days_left, issuer, expires_iso)
        - status        : str dans {ok, warning, critical, none, error}
        - message       : str affiche dans le dashboard
        - days_left     : int|None - jours restants avant expiration
        - issuer        : str|None - emetteur du certificat
        - expires_iso   : str|None - date d'expiration ISO (YYYY-MM-DD)
                          utilisee par history.py pour detecter un renouvellement
                          de certif et reset l'etat des alertes J-30/J-15.
    """
    try:
        hostname = _extract_hostname(url)
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=hostname) as s:
            s.settimeout(10)
            s.connect((hostname, 443))
            cert = s.getpeercert()

        exp = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc
        )
        days = (exp - datetime.now(timezone.utc)).days
        label = exp.strftime("%d/%m/%Y")
        expires_iso = exp.strftime("%Y-%m-%d")

        issuer_parts = cert.get("issuer", [])
        issuer = None
        for part in issuer_parts:
            for k, v in part:
                if k == "organizationName":
                    issuer = v
                    break
            if issuer:
                break

        if days <= 7:
            return "critical", f"Expire dans {days}j ({label})", days, issuer, expires_iso
        if days <= warn_days:
            return "warning", f"Expire dans {days}j ({label})", days, issuer, expires_iso
        return "ok", f"Valide {days}j ({label})", days, issuer, expires_iso

    except ssl.SSLError:
        return "none", "Pas de certificat SSL", None, None, None
    except (ConnectionRefusedError, OSError):
        return "none", "Port 443 inaccessible (pas de SSL)", None, None, None
    except Exception as e:
        return "error", str(e)[:60], None, None, None
