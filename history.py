"""Historique des mesures dans SQLite : permet les tendances (graphiques).

Une seule table `checks` avec toutes les mesures d'un run.
Les indexes sur (domain, ts) rendent les queries de tendance rapides.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,            -- ISO UTC
    domain          TEXT NOT NULL,
    url             TEXT,
    client          TEXT,
    up              INTEGER,                   -- 0/1
    http_status     INTEGER,
    response_ms     INTEGER,
    ssl_days        INTEGER,
    ssl_status      TEXT,
    ndd_days        INTEGER,
    ndd_status      TEXT,
    psi_score       INTEGER,
    lcp_ms          INTEGER,
    cls             REAL,
    fcp_ms          INTEGER,
    ttfb_ms         INTEGER,
    cms             TEXT,
    cms_version     TEXT,
    php_version     TEXT,
    backup_days     INTEGER,
    backup_status   TEXT
);
CREATE INDEX IF NOT EXISTS idx_checks_domain_ts ON checks(domain, ts);
CREATE INDEX IF NOT EXISTS idx_checks_ts ON checks(ts);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    return conn


def save_run(db_path: Path, results: list[dict]) -> int:
    """Enregistre tous les resultats d'un run. Retourne le nombre de lignes inserees."""
    if not results:
        return 0
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for r in results:
        psi = r.get("psi") or {}
        stack = r.get("stack") or {}
        backup = r.get("backup") or {}
        rows.append((
            ts,
            r.get("domaine"),
            r.get("url"),
            r.get("client"),
            1 if r.get("up") else 0,
            r.get("http_status"),
            r.get("response_ms"),
            r.get("ssl_days"),
            r.get("ssl_st"),
            r.get("ndd_days"),
            r.get("ndd_st"),
            psi.get("score"),
            psi.get("lcp_ms"),
            psi.get("cls"),
            psi.get("fcp_ms"),
            psi.get("ttfb_ms"),
            stack.get("cms"),
            stack.get("cms_version"),
            stack.get("php_version"),
            backup.get("days_since"),
            backup.get("status"),
        ))

    try:
        conn = _connect(db_path)
        with conn:
            conn.executemany(
                """INSERT INTO checks (
                    ts, domain, url, client, up, http_status, response_ms,
                    ssl_days, ssl_status, ndd_days, ndd_status,
                    psi_score, lcp_ms, cls, fcp_ms, ttfb_ms,
                    cms, cms_version, php_version,
                    backup_days, backup_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        conn.close()
        return len(rows)
    except Exception as e:
        log.error(f"Erreur ecriture history.db : {e}")
        return 0


def fetch_trends(db_path: Path, domain: str, days: int = 30) -> list[dict]:
    """Retourne l'historique d'un domaine sur N jours."""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """SELECT ts, up, response_ms, ssl_days, ndd_days,
                      psi_score, lcp_ms, cls, fcp_ms, ttfb_ms,
                      backup_days, backup_status
               FROM checks
               WHERE domain = ?
                 AND ts >= datetime('now', ?)
               ORDER BY ts ASC""",
            (domain, f"-{days} days"),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        log.error(f"Erreur lecture trends : {e}")
        return []


def fetch_summary(db_path: Path, days: int = 30) -> dict:
    """Retourne des stats aggregees pour le dashboard."""
    if not db_path.exists():
        return {"total_checks": 0, "sites": []}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """SELECT domain,
                      COUNT(*) AS n,
                      AVG(up) AS uptime_ratio,
                      AVG(response_ms) AS avg_ms,
                      MIN(ssl_days) AS min_ssl,
                      AVG(psi_score) AS avg_psi
               FROM checks
               WHERE ts >= datetime('now', ?)
               GROUP BY domain
               ORDER BY domain""",
            (f"-{days} days",),
        )
        sites = [dict(r) for r in cur.fetchall()]
        total = conn.execute(
            "SELECT COUNT(*) FROM checks WHERE ts >= datetime('now', ?)",
            (f"-{days} days",),
        ).fetchone()[0]
        conn.close()
        return {"total_checks": total, "sites": sites}
    except Exception as e:
        log.error(f"Erreur lecture summary : {e}")
        return {"total_checks": 0, "sites": []}
