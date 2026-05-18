"""Historique des mesures dans SQLite + politique de dedoublonnage des alertes.

Tables :
- `checks`      : toutes les mesures d'un run (uptime, ssl, ndd, psi, stack, backup)
- `alert_state` : etat de chaque alerte par (domain, category) pour eviter le spam

Politiques de dedoublonnage :

1) Countdown (categories 'ssl', 'ndd') :
   - J-30 : 1 alerte unique
   - J-15 : 1 alerte unique
   - J-7 a J-0 : 1 alerte par jour
   - Reset si expires_at change (renouvellement)

2) Sticky (categories 'down', 'backup') :
   - Premiere detection : 1 alerte immediate (type 'first')
   - Puis 1 rappel a intervalle regulier tant que l'incident persiste (type 'reminder')
   - Cadence configurable par categorie via STICKY_REMINDER_DAYS :
       * 'down'   -> 1 jour  (site indisponible = critique, on alerte chaque jour)
       * 'backup' -> 7 jours (rappel hebdomadaire, moins urgent)
   - Reset complet quand l'incident est resolu (call mark_resolved)
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Cadence de rappel par categorie sticky (en jours). Defaut = 7.
STICKY_REMINDER_DAYS = {
    "down": 1,    # quotidien : un site DOWN doit etre rappele chaque jour
    "backup": 7,  # hebdomadaire : moins critique
}
DEFAULT_REMINDER_INTERVAL_DAYS = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    domain          TEXT NOT NULL,
    url             TEXT,
    client          TEXT,
    up              INTEGER,
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

CREATE TABLE IF NOT EXISTS alert_state (
    domain              TEXT NOT NULL,
    category            TEXT NOT NULL,    -- 'ssl' | 'ndd' | 'down' | 'backup'
    -- Specifique countdown (SSL/NDD) :
    expires_at          TEXT,             -- 'YYYY-MM-DD' du certif ou du domaine
    j30_sent_at         TEXT,
    j15_sent_at         TEXT,
    last_daily_sent_at  TEXT,
    -- Specifique sticky (DOWN/BACKUP) :
    first_seen_at       TEXT,             -- date premiere detection de l'incident
    last_reminder_at    TEXT,             -- date du dernier rappel envoye
    PRIMARY KEY (domain, category)
);
"""

COUNTDOWN_CATEGORIES = {"ssl", "ndd"}
STICKY_CATEGORIES = {"down", "backup"}


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    return conn


# =============================================================================
# Sauvegarde des mesures historiques (inchange)
# =============================================================================

def save_run(db_path: Path, results: list[dict]) -> int:
    """Enregistre tous les resultats d'un run."""
    if not results:
        return 0
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for r in results:
        psi = r.get("psi") or {}
        stack = r.get("stack") or {}
        backup = r.get("backup") or {}
        rows.append((
            ts, r.get("domaine"), r.get("url"), r.get("client"),
            1 if r.get("up") else 0,
            r.get("http_status"), r.get("response_ms"),
            r.get("ssl_days"), r.get("ssl_st"),
            r.get("ndd_days"), r.get("ndd_st"),
            psi.get("score"), psi.get("lcp_ms"), psi.get("cls"),
            psi.get("fcp_ms"), psi.get("ttfb_ms"),
            stack.get("cms"), stack.get("cms_version"), stack.get("php_version"),
            backup.get("days_since"), backup.get("status"),
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
               WHERE domain = ? AND ts >= datetime('now', ?)
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
    if not db_path.exists():
        return {"total_checks": 0, "sites": []}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """SELECT domain, COUNT(*) AS n, AVG(up) AS uptime_ratio,
                      AVG(response_ms) AS avg_ms, MIN(ssl_days) AS min_ssl,
                      AVG(psi_score) AS avg_psi
               FROM checks WHERE ts >= datetime('now', ?)
               GROUP BY domain ORDER BY domain""",
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


# =============================================================================
# Dedoublonnage des alertes (4 categories)
# =============================================================================

def _get_state(conn: sqlite3.Connection, domain: str, category: str):
    """Recupere l'etat existant ou None."""
    return conn.execute(
        """SELECT expires_at, j30_sent_at, j15_sent_at, last_daily_sent_at,
                  first_seen_at, last_reminder_at
           FROM alert_state WHERE domain = ? AND category = ?""",
        (domain, category),
    ).fetchone()


def should_send_alert(
    db_path: Path,
    domain: str,
    category: str,
    *,
    expires_iso: str | None = None,
    days_left: int | None = None,
    today_iso: str | None = None,
) -> tuple[bool, str]:
    """Decide si on doit envoyer une alerte aujourd'hui pour ce (domain, category).

    Pour les categories COUNTDOWN (ssl, ndd) :
        - kwargs requis : expires_iso, days_left
        - Returns (bool, alert_type) ou alert_type ∈ {'j30','j15','daily','none'}

    Pour les categories STICKY (down, backup) :
        - aucun kwargs requis
        - Returns (bool, alert_type) ou alert_type ∈ {'first','reminder','none'}

    NE MARQUE PAS l'envoi : il faut appeler mark_alert_sent() apres l'envoi
    reussi de l'email. Si l'email echoue, ne pas marquer -> on retentera demain.
    """
    if today_iso is None:
        today_iso = date.today().isoformat()

    if category in COUNTDOWN_CATEGORIES:
        return _should_send_countdown(db_path, domain, category, expires_iso, days_left, today_iso)
    if category in STICKY_CATEGORIES:
        return _should_send_sticky(db_path, domain, category, today_iso)
    raise ValueError(f"Categorie inconnue : {category}")


def _should_send_countdown(db_path, domain, category, expires_iso, days_left, today_iso):
    if expires_iso is None or days_left is None:
        return False, "none"
    if days_left > 30:
        return False, "none"

    if days_left <= 7:
        tier = "daily"
    elif days_left <= 15:
        tier = "j15"
    else:
        tier = "j30"

    conn = _connect(db_path)
    try:
        row = _get_state(conn, domain, category)
        if row is None or row[0] != expires_iso:
            # Pas d'etat ou renouvellement detecte
            j30_sent, j15_sent, daily_sent = None, None, None
        else:
            _, j30_sent, j15_sent, daily_sent, _, _ = row

        if tier == "daily":
            if daily_sent == today_iso:
                return False, "none"
            return True, "daily"
        if tier == "j15":
            if j15_sent is not None:
                return False, "none"
            return True, "j15"
        # tier == "j30"
        if j30_sent is not None:
            return False, "none"
        return True, "j30"
    finally:
        conn.close()


def _should_send_sticky(db_path, domain, category, today_iso):
    conn = _connect(db_path)
    try:
        row = _get_state(conn, domain, category)
        if row is None or row[4] is None:  # first_seen_at IS NULL
            return True, "first"
        # Incident en cours, verifier l'intervalle de rappel propre a la categorie
        interval = STICKY_REMINDER_DAYS.get(category, DEFAULT_REMINDER_INTERVAL_DAYS)
        last_reminder = row[5] or row[4]  # fallback first_seen_at
        try:
            last_dt = date.fromisoformat(last_reminder)
        except ValueError:
            return True, "reminder"
        today_dt = date.fromisoformat(today_iso)
        if (today_dt - last_dt) >= timedelta(days=interval):
            return True, "reminder"
        return False, "none"
    finally:
        conn.close()


def mark_alert_sent(
    db_path: Path,
    domain: str,
    category: str,
    alert_type: str,
    *,
    expires_iso: str | None = None,
    today_iso: str | None = None,
) -> None:
    """Enregistre l'envoi reussi d'une alerte.

    alert_type :
        countdown : 'j30' | 'j15' | 'daily'
        sticky    : 'first' | 'reminder'
    """
    if today_iso is None:
        today_iso = date.today().isoformat()

    conn = _connect(db_path)
    try:
        row = _get_state(conn, domain, category)

        if category in COUNTDOWN_CATEGORIES:
            if row is None or row[0] != expires_iso:
                j30_sent, j15_sent, daily_sent = None, None, None
            else:
                _, j30_sent, j15_sent, daily_sent, _, _ = row

            # Marque le tier courant ET tous les tiers superieurs comme deja
            # envoyes (pour eviter les alertes retroactives).
            if alert_type == "j30":
                j30_sent = j30_sent or today_iso
            elif alert_type == "j15":
                j30_sent = j30_sent or today_iso
                j15_sent = j15_sent or today_iso
            elif alert_type == "daily":
                j30_sent = j30_sent or today_iso
                j15_sent = j15_sent or today_iso
                daily_sent = today_iso

            with conn:
                conn.execute(
                    """INSERT INTO alert_state
                       (domain, category, expires_at, j30_sent_at, j15_sent_at, last_daily_sent_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(domain, category) DO UPDATE SET
                         expires_at = excluded.expires_at,
                         j30_sent_at = excluded.j30_sent_at,
                         j15_sent_at = excluded.j15_sent_at,
                         last_daily_sent_at = excluded.last_daily_sent_at,
                         first_seen_at = NULL,
                         last_reminder_at = NULL""",
                    (domain, category, expires_iso, j30_sent, j15_sent, daily_sent),
                )
            return

        if category in STICKY_CATEGORIES:
            first_seen = row[4] if row else None
            if alert_type == "first" or first_seen is None:
                first_seen = today_iso
            with conn:
                conn.execute(
                    """INSERT INTO alert_state
                       (domain, category, first_seen_at, last_reminder_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(domain, category) DO UPDATE SET
                         first_seen_at = COALESCE(alert_state.first_seen_at, excluded.first_seen_at),
                         last_reminder_at = excluded.last_reminder_at,
                         expires_at = NULL,
                         j30_sent_at = NULL,
                         j15_sent_at = NULL,
                         last_daily_sent_at = NULL""",
                    (domain, category, first_seen, today_iso),
                )
            return
    finally:
        conn.close()


def mark_resolved(db_path: Path, domain: str, category: str) -> None:
    """Reset l'etat d'une alerte sticky (ou countdown) quand l'incident est resolu.

    Pour les sticky : appele systematiquement quand l'incident n'est plus actif
    (site UP, backup OK). Permet a la prochaine occurence d'etre traitee comme
    une 'premiere detection'.

    Pour les countdown : peu utile (le reset se fait automatiquement quand
    expires_at change), mais sans effet de bord.
    """
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "DELETE FROM alert_state WHERE domain = ? AND category = ?",
                (domain, category),
            )
    finally:
        conn.close()
