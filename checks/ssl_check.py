"""Check du certificat SSL : validite, emetteur, jours restants."""
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
        tuple (status: str, message: str, days_left: int|None, issuer: str|None)
        status dans {ok, warning, critical, none, error}
    """
    try:
        hostname = _extract_hostname(url)
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=hostname) as s:
            s.settimeout(10)
            s.connect((hostname, 443))
            cert = s.getpeercert()

        # Date d'expiration
        exp = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc
        )
        days = (exp - datetime.now(timezone.utc)).days
        label = exp.strftime("%d/%m/%Y")

        # Emetteur (pour info, type "Let's Encrypt" ou "Sectigo")
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
            return "critical", f"Expire dans {days}j ({label})", days, issuer
        if days <= warn_days:
            return "warning", f"Expire dans {days}j ({label})", days, issuer
        return "ok", f"Valide {days}j ({label})", days, issuer

    except ssl.SSLError:
        return "none", "Pas de certificat SSL", None, None
    except (ConnectionRefusedError, OSError):
        return "none", "Port 443 inaccessible (pas de SSL)", None, None
    except Exception as e:
        return "error", str(e)[:60], None, None
