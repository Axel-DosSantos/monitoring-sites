"""Detection de la stack technique : WordPress, PHP, serveur web.

- WP detecte via <meta name="generator"> + /readme.html + /wp-login.php
- PHP detecte via les headers X-Powered-By / Server
- Serveur web detecte via header Server
"""
from __future__ import annotations

import re
import requests

USER_AGENT = "MonitorBot/2.0 (+https://albys.com)"
TIMEOUT = 10


def _fetch(url: str, timeout: int = TIMEOUT):
    try:
        return requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            verify=False,
        )
    except Exception:
        return None


def _detect_wp_version_from_html(html: str):
    """Essaye d'extraire la version WP depuis le meta generator."""
    m = re.search(
        r'<meta\s+name=["\']generator["\']\s+content=["\']WordPress\s+([\d.]+)',
        html or "",
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    # Variante : fragment wp-content dans le html
    if html and re.search(r"wp-(content|includes|json)", html, re.IGNORECASE):
        return "unknown"  # WP detecte mais version masquee
    return None


def _detect_wp_version_from_readme(base_url: str):
    """Teste /readme.html (souvent laisse accessible avec la version)."""
    for path in ["/readme.html", "/license.txt"]:
        r = _fetch(base_url.rstrip("/") + path, timeout=5)
        if r and r.status_code == 200:
            m = re.search(r"[Vv]ersion\s+([\d.]+)", r.text[:2000])
            if m:
                return m.group(1)
    return None


def check_stack(url: str):
    """Detecte la stack technique d'un site.

    Returns:
        dict: {
            "cms": "WordPress"|None,
            "cms_version": str|None,
            "cms_outdated": bool|None,
            "php_version": str|None,
            "server": str|None,
            "generator": str|None,
        }
    """
    result = {
        "cms": None,
        "cms_version": None,
        "cms_outdated": None,
        "php_version": None,
        "server": None,
        "generator": None,
    }

    r = _fetch(url)
    if r is None:
        return result

    # Headers : Server (nginx/apache) + X-Powered-By (PHP/7.4)
    server = r.headers.get("Server", "")
    powered = r.headers.get("X-Powered-By", "")
    result["server"] = server or None

    m_php = re.search(r"PHP/?\s*([\d.]+)", powered, re.IGNORECASE)
    if m_php:
        result["php_version"] = m_php.group(1)

    # WordPress : meta generator, puis readme.html en fallback
    wp_version = _detect_wp_version_from_html(r.text)
    if wp_version:
        result["cms"] = "WordPress"
        if wp_version != "unknown":
            result["cms_version"] = wp_version
            result["cms_outdated"] = _is_wp_outdated(wp_version)
        else:
            # Tente /readme.html
            rv = _detect_wp_version_from_readme(url)
            if rv:
                result["cms_version"] = rv
                result["cms_outdated"] = _is_wp_outdated(rv)

    # Autre generator meta (Joomla, Drupal, ...)
    m_gen = re.search(
        r'<meta\s+name=["\']generator["\']\s+content=["\']([^"\']+)',
        r.text or "",
        re.IGNORECASE,
    )
    if m_gen:
        result["generator"] = m_gen.group(1).strip()
        if not result["cms"] and "wordpress" not in result["generator"].lower():
            # Joomla/Drupal/...
            low = result["generator"].lower()
            if "joomla" in low:
                result["cms"] = "Joomla"
            elif "drupal" in low:
                result["cms"] = "Drupal"
            elif "prestashop" in low:
                result["cms"] = "PrestaShop"

    return result


# Seuil "WP outdated" : WP 6.5+ recent (2024-2026). Ajuster au fil du temps.
WP_LATEST_MAJOR = "6.5"


def _is_wp_outdated(version: str) -> bool:
    try:
        parts = [int(p) for p in version.split(".")[:2]]
        latest = [int(p) for p in WP_LATEST_MAJOR.split(".")]
        return parts < latest
    except Exception:
        return False


# Versions PHP considerees obsoletes (fin de vie + pas de security support)
PHP_END_OF_LIFE = {"5.6", "7.0", "7.1", "7.2", "7.3", "7.4", "8.0"}


def is_php_outdated(version: str | None) -> bool:
    if not version:
        return False
    major_minor = ".".join(version.split(".")[:2])
    return major_minor in PHP_END_OF_LIFE
