"""Check Core Web Vitals via Google PageSpeed Insights API.

Doc : https://developers.google.com/speed/docs/insights/v5/get-started

La cle API est facultative : sans cle, l'API est accessible mais rate-limitee.
Avec cle gratuite (console Cloud > PageSpeed Insights API), on monte a 25k/jour.

On ne fait le check que si PSI_API_KEY est defini OU force=True, pour eviter
de saturer le quota gratuit lors de chaque cron quotidien.
"""
from __future__ import annotations

import logging
import os
import requests

log = logging.getLogger(__name__)

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def check_pagespeed(url: str, strategy: str = "mobile", timeout: int = 60):
    """Lance une analyse PageSpeed et extrait les Core Web Vitals.

    Args:
        url: URL a analyser
        strategy: "mobile" ou "desktop"
        timeout: timeout de la requete PSI (longue, >30s)

    Returns:
        dict: {
            "status": "ok"|"error"|"skip",
            "message": str,
            "score": int|None,  # 0-100 (performance)
            "lcp_ms": int|None,
            "cls": float|None,  # sans dimension
            "fcp_ms": int|None,
            "ttfb_ms": int|None,
            "strategy": str,
        }
    """
    api_key = os.environ.get("PSI_API_KEY")
    params = {
        "url": url,
        "strategy": strategy,
        "category": "performance",
    }
    if api_key:
        params["key"] = api_key

    try:
        r = requests.get(PSI_ENDPOINT, params=params, timeout=timeout)
        if r.status_code == 429:
            return {
                "status": "error",
                "message": "Rate limit PSI — definir PSI_API_KEY",
                "score": None, "lcp_ms": None, "cls": None,
                "fcp_ms": None, "ttfb_ms": None, "strategy": strategy,
            }
        if r.status_code != 200:
            return {
                "status": "error",
                "message": f"PSI HTTP {r.status_code}",
                "score": None, "lcp_ms": None, "cls": None,
                "fcp_ms": None, "ttfb_ms": None, "strategy": strategy,
            }

        data = r.json()
        lh = data.get("lighthouseResult", {})
        categories = lh.get("categories", {})
        audits = lh.get("audits", {})

        perf_cat = categories.get("performance", {}) or {}
        score = perf_cat.get("score")
        score = int(round(score * 100)) if score is not None else None

        def _ms(audit_id):
            v = audits.get(audit_id, {}).get("numericValue")
            return int(round(v)) if v is not None else None

        def _float(audit_id):
            v = audits.get(audit_id, {}).get("numericValue")
            return round(v, 3) if v is not None else None

        return {
            "status": "ok",
            "message": f"Score {score}/100" if score is not None else "Analyse OK",
            "score": score,
            "lcp_ms": _ms("largest-contentful-paint"),
            "cls": _float("cumulative-layout-shift"),
            "fcp_ms": _ms("first-contentful-paint"),
            "ttfb_ms": _ms("server-response-time"),
            "strategy": strategy,
        }

    except requests.exceptions.Timeout:
        return {
            "status": "error",
            "message": "Timeout PSI",
            "score": None, "lcp_ms": None, "cls": None,
            "fcp_ms": None, "ttfb_ms": None, "strategy": strategy,
        }
    except Exception as e:
        log.warning(f"Erreur PSI {url} : {e}")
        return {
            "status": "error",
            "message": str(e)[:80],
            "score": None, "lcp_ms": None, "cls": None,
            "fcp_ms": None, "ttfb_ms": None, "strategy": strategy,
        }


def classify_score(score):
    """Retourne un statut colore selon le score PSI."""
    if score is None:
        return "unknown"
    if score >= 90:
        return "ok"
    if score >= 50:
        return "warning"
    return "critical"
