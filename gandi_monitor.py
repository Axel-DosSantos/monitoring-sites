#!/usr/bin/env python3
"""
Monitoring des noms de domaine Gandi.

Ce script récupère TOUS les domaines de votre compte Gandi via l'API v5,
vérifie leurs dates d'expiration, et envoie des alertes email selon la même
logique de déduplication que monitor.py (J-30 / J-15 / quotidien J-7→J-0).

Usage :
    python gandi_monitor.py              → check + alertes si nécessaire
    python gandi_monitor.py --test       → force l'envoi d'un rapport complet
    python gandi_monitor.py --list       → affiche tous les domaines sans envoyer d'email

Variables d'environnement requises :
    GANDI_API_KEY   ou   GANDI_PAT     → Clé API ou Personal Access Token Gandi
    SMTP_USER                          → Adresse email expéditeur
    SMTP_PASSWORD                      → Mot de passe SMTP
    SMTP_HOST       (optionnel)        → Défaut : smtp.office365.com
    SMTP_PORT       (optionnel)        → Défaut : 587
    EMAIL_SUPPORT   (optionnel)        → Destinataire alertes (défaut dans Config Excel)
    EMAIL_AXEL      (optionnel)        → Destinataire alertes (défaut dans Config Excel)
    GANDI_WARN_DAYS (optionnel)        → Jours avant expiration pour alerte (défaut : 30)
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# ── Chemins ───────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
RESULTS_GANDI_JSON = BASE_DIR / "results_gandi.json"
HISTORY_DB = BASE_DIR / "history.db"
LOG_PATH = BASE_DIR / "gandi_monitor.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# Import des modules du projet
from checks.gandi import get_all_gandi_domains_status
from monitor import load_config, send_support_email, _send_email
import history


# ── Envoi des alertes Gandi ───────────────────────────────────────────────────
def _build_domain_alert_body(domain_result: dict, alert_type: str, now: str) -> str:
    days = domain_result["days_left"]
    fqdn = domain_result["fqdn"]
    expires = domain_result["expires_iso"]
    autorenew = "✓ Activé" if domain_result["autorenew"] else "✗ DÉSACTIVÉ"
    ns = ", ".join(domain_result["nameservers"]) or "N/A"

    return (
        f"Alerte expiration domaine Gandi\n"
        f"{'=' * 50}\n\n"
        f"Domaine       : {fqdn}\n"
        f"Expiration    : {expires} ({days} jours restants)\n"
        f"Renouvellement auto : {autorenew}\n"
        f"Serveurs NS   : {ns}\n"
        f"Type d'alerte : {alert_type}\n"
        f"Détecté le    : {now}\n\n"
        f"{'=' * 50}\n"
        f"ACTION REQUISE : Renouvelez ce domaine sur https://admin.gandi.net/domain/{fqdn}\n"
        + ("⚠️  Le renouvellement automatique est DÉSACTIVÉ — risque de perte du domaine !\n"
           if not domain_result["autorenew"] else "")
    )


def send_gandi_alerts(results: list[dict], cfg: dict, now: str) -> int:
    """
    Envoie les alertes pour les domaines en warning/critical.
    Utilise la même logique de déduplication J-30/J-15/quotidien que monitor.py.
    Retourne le nombre d'alertes envoyées.
    """
    sent_count = 0

    for r in results:
        if r["status"] not in ("warning", "critical"):
            continue

        domain_key = f"gandi:{r['fqdn'].lower()}"
        days = r["days_left"]
        expires_iso = r["expires_iso"]

        if days is None or expires_iso is None:
            continue

        should, alert_type = history.should_send_alert(
            HISTORY_DB,
            domain_key,
            "ndd",
            expires_iso=expires_iso,
            days_left=days,
        )

        if not should:
            log.debug(f"  {r['fqdn']} : alerte déjà envoyée récemment, skip")
            continue

        # Calcul du préfixe du sujet selon le type d'alerte
        if alert_type == "daily":
            prefix = "NDD CRITIQUE" if days <= 7 else "NDD EXPIRATION"
            subject_suffix = f"J-{days}"
        elif alert_type == "j15":
            prefix = "NDD EXPIRATION"
            subject_suffix = "J-15"
        else:  # j30
            prefix = "NDD EXPIRATION"
            subject_suffix = "J-30"

        subject = f"[GANDI] {prefix} {subject_suffix} — {r['fqdn']}"
        body = _build_domain_alert_body(r, f"{prefix} {subject_suffix}", now)

        ok = send_support_email(cfg, subject, body)
        if ok:
            history.mark_alert_sent(
                HISTORY_DB,
                domain_key,
                "ndd",
                alert_type,
                expires_iso=expires_iso,
            )
            sent_count += 1
            log.warning(f"  ALERTE envoyée : {r['fqdn']} — {prefix} {subject_suffix}")
        else:
            log.error(f"  Échec envoi alerte pour {r['fqdn']}")

    return sent_count


def send_test_report(results: list[dict], cfg: dict, now: str):
    """Envoie un rapport complet de tous les domaines Gandi (mode --test)."""
    to_addr = os.environ.get("EMAIL_AXEL", cfg.get("Email axel", "axel.dos-santos@albys.com"))
    subject = f"[GANDI] Rapport complet des domaines — {now}"

    critiques = [r for r in results if r["status"] == "critical"]
    warnings  = [r for r in results if r["status"] == "warning"]
    ok_list   = [r for r in results if r["status"] == "ok"]
    autres    = [r for r in results if r["status"] not in ("ok", "warning", "critical")]

    lignes = [
        f"Rapport de monitoring Gandi — {now}",
        "=" * 60,
        "",
        f"Total domaines : {len(results)}",
        f"  🔴 Critiques  : {len(critiques)}",
        f"  🟡 Warnings   : {len(warnings)}",
        f"  🟢 OK         : {len(ok_list)}",
        f"  ⚪ Autres     : {len(autres)}",
        "",
    ]

    def _section(title, items):
        if not items:
            return
        lignes.append(title)
        lignes.append("-" * 40)
        for r in sorted(items, key=lambda x: x.get("days_left") or 9999):
            autorenew_flag = " [AUTO✓]" if r["autorenew"] else " [⚠️ SANS AUTO]"
            lignes.append(f"  {r['fqdn']:<40} {r['message']}{autorenew_flag}")
        lignes.append("")

    _section("🔴 CRITIQUES (≤ 7 jours)", critiques)
    _section("🟡 WARNINGS (≤ 30 jours)", warnings)
    _section("🟢 OK", ok_list)
    _section("⚪ STATUT INCONNU / ERREUR", autres)

    body = "\n".join(lignes)
    _send_email(to_addr, subject, body, cfg)
    log.info(f"Rapport complet envoyé à {to_addr}")


def display_list(results: list[dict]):
    """Affiche tous les domaines dans le terminal (mode --list)."""
    icons = {"ok": "🟢", "warning": "🟡", "critical": "🔴", "unknown": "⚪", "error": "❌"}
    print(f"\n{'DOMAINE':<45} {'STATUT':<12} {'JOURS':<8} {'AUTO':<6} {'MESSAGE'}")
    print("-" * 110)
    for r in sorted(results, key=lambda x: x.get("days_left") or 9999):
        icon = icons.get(r["status"], "❓")
        days = str(r["days_left"]) if r["days_left"] is not None else "?"
        auto = "✓" if r["autorenew"] else "✗"
        print(f"{icon} {r['fqdn']:<43} {r['status']:<12} {days:<8} {auto:<6} {r['message']}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────
def run(test_mode: bool = False, list_mode: bool = False):
    log.info("=== Démarrage monitoring Gandi ===")
    cfg = load_config()
    warn_days = int(os.environ.get("GANDI_WARN_DAYS", cfg.get("Alerte NDD (jours avant)", "30")))
    now = datetime.now().strftime("%d/%m/%Y %H:%M")

    # Récupération de tous les domaines Gandi
    results, error = get_all_gandi_domains_status(warn_days=warn_days)

    if error:
        log.error(f"Erreur API Gandi : {error}")
        # Envoi d'un email d'erreur si SMTP configuré
        send_support_email(cfg, "[GANDI] Erreur API — monitoring impossible", error)
        sys.exit(1)

    log.info(f"{len(results)} domaines récupérés depuis Gandi")

    # Affichage terminal (mode --list)
    if list_mode:
        display_list(results)
        return

    # Sauvegarde JSON
    payload = {
        "domains": results,
        "last_run": now,
        "total": len(results),
        "critiques": sum(1 for r in results if r["status"] == "critical"),
        "warnings": sum(1 for r in results if r["status"] == "warning"),
    }
    RESULTS_GANDI_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    log.info(f"Résultats sauvegardés dans {RESULTS_GANDI_JSON.name}")

    # Envoi du rapport complet (mode --test)
    if test_mode:
        log.info("Mode test : envoi du rapport complet...")
        send_test_report(results, cfg, now)
        return

    # Envoi des alertes selon la politique de déduplication
    nb_alertes = send_gandi_alerts(results, cfg, now)

    # Stats finales
    nb_warn = sum(1 for r in results if r["status"] in ("warning", "critical"))
    if nb_warn == 0:
        log.info(f"=== Terminé — {len(results)} domaines vérifiés — TOUT EST OK ===")
    else:
        log.info(
            f"=== Terminé — {len(results)} domaines vérifiés — "
            f"{nb_warn} en alerte, {nb_alertes} email(s) envoyé(s) ==="
        )


if __name__ == "__main__":
    test_mode = "--test" in sys.argv
    list_mode = "--list" in sys.argv
    run(test_mode=test_mode, list_mode=list_mode)
