#!/usr/bin/env python3
"""
Interface web Flask pour le monitoring.
- Dashboard avec cartes par site + metriques (uptime, SSL, NDD, PSI, backup)
- Tendances (Chart.js) sur 30 jours via SQLite
- Lecture-seule : les checks sont lances par monitor.py (cron GitHub Actions)
- Endpoint /run permet de declencher un scan manuel en local
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template, request

import history

BASE_DIR = Path(__file__).parent
RESULTS_JSON = BASE_DIR / "results.json"
HISTORY_DB = BASE_DIR / "history.db"

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
logging.basicConfig(level=logging.INFO)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
log = app.logger


# ── Helpers ───────────────────────────────────────────────────────────────────
def load_results() -> dict:
    if not RESULTS_JSON.exists():
        return {"sites": [], "last_run": None, "running": False}
    try:
        return json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        log.error(f"Erreur lecture results.json : {e}")
        return {"sites": [], "last_run": None, "running": False}


def health_score(site: dict) -> int:
    """Score 0-100 compose de uptime / ssl / ndd / psi / backup."""
    if not site.get("up"):
        return 0
    score = 100
    if site.get("ssl_st") == "critical":
        score -= 40
    elif site.get("ssl_st") == "warning":
        score -= 15
    elif site.get("ssl_st") in ("none", "error"):
        score -= 25
    if site.get("ndd_st") == "critical":
        score -= 30
    elif site.get("ndd_st") == "warning":
        score -= 10
    psi = (site.get("psi") or {}).get("score")
    if psi is not None:
        if psi < 50:
            score -= 20
        elif psi < 70:
            score -= 10
    bstatus = (site.get("backup") or {}).get("status")
    if bstatus == "critical":
        score -= 20
    elif bstatus == "warning":
        score -= 5
    return max(0, min(100, score))


def overall_stats(sites: list[dict]) -> dict:
    total = len(sites)
    up = sum(1 for s in sites if s.get("up"))
    critical = sum(
        1
        for s in sites
        if not s.get("up")
        or s.get("ssl_st") == "critical"
        or s.get("ndd_st") == "critical"
        or (s.get("backup") or {}).get("status") == "critical"
    )
    warnings = sum(
        1
        for s in sites
        if s.get("ssl_st") == "warning"
        or s.get("ndd_st") == "warning"
        or (s.get("backup") or {}).get("status") == "warning"
    )
    scores = [health_score(s) for s in sites]
    avg = int(sum(scores) / len(scores)) if scores else 0
    return {
        "total": total,
        "up": up,
        "down": total - up,
        "critical": critical,
        "warnings": warnings,
        "avg_health": avg,
    }


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def dashboard():
    data = load_results()
    sites = data.get("sites", [])
    for s in sites:
        s["health_score"] = health_score(s)
    stats = overall_stats(sites)
    return render_template(
        "dashboard.html",
        sites=sites,
        stats=stats,
        last_run=data.get("last_run"),
        running=data.get("running", False),
    )


@app.route("/api/results")
def api_results():
    data = load_results()
    for s in data.get("sites", []):
        s["health_score"] = health_score(s)
    data["stats"] = overall_stats(data.get("sites", []))
    return jsonify(data)


@app.route("/api/trends/<path:domain>")
def api_trends(domain):
    days = int(request.args.get("days", 30))
    rows = history.fetch_trends(HISTORY_DB, domain, days=days)
    return jsonify({"domain": domain, "days": days, "points": rows})


@app.route("/api/summary")
def api_summary():
    days = int(request.args.get("days", 30))
    return jsonify(history.fetch_summary(HISTORY_DB, days=days))


@app.route("/run", methods=["POST"])
def run_scan():
    """Declenche un scan manuel (local uniquement)."""
    if os.environ.get("CI"):
        return jsonify({"error": "Scan manuel desactive en CI"}), 403

    def _run():
        subprocess.run(["python", str(BASE_DIR / "monitor.py")], cwd=str(BASE_DIR))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "scan lance"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
