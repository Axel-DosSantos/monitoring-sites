"""Check uptime + temps de reponse.

Retourne is_up, message, response_time_ms, http_status.
"""
from __future__ import annotations

import time
import requests

USER_AGENT = "MonitorBot/2.0 (+https://albys.com)"
DEFAULT_TIMEOUT = 10


def check_uptime(url: str, timeout: int = DEFAULT_TIMEOUT):
    """Verifie si un site repond en HTTP 2xx/3xx et mesure le temps de reponse.

    Returns:
        tuple (is_up: bool, msg: str, response_time_ms: int|None, http_status: int|None)
    """
    headers = {"User-Agent": USER_AGENT}
    start = time.perf_counter()
    try:
        r = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers=headers,
            verify=False,
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return r.status_code < 400, f"HTTP {r.status_code}", elapsed_ms, r.status_code

    except requests.exceptions.ConnectionError:
        # Si HTTPS echoue, on retente en HTTP
        if url.startswith("https://"):
            try:
                http_url = "http://" + url[8:]
                start = time.perf_counter()
                r = requests.get(
                    http_url,
                    timeout=timeout,
                    allow_redirects=True,
                    headers=headers,
                )
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                return (
                    r.status_code < 400,
                    f"HTTP {r.status_code} (http)",
                    elapsed_ms,
                    r.status_code,
                )
            except Exception:
                pass
        return False, "Connexion impossible", None, None

    except requests.exceptions.Timeout:
        return False, "Timeout", None, None
    except Exception as e:
        return False, str(e)[:80], None, None
