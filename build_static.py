#!/usr/bin/env python3
"""
Genere une version statique du dashboard pour GitHub Pages.
- Lit results.json + results_gandi.json + history.db
- Genere public/index.html (standalone, embarque les donnees en JSON)
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import history
from exclusions import load_exclusions, match as match_exclusion, host_of

BASE_DIR        = Path(__file__).parent
RESULTS_JSON    = BASE_DIR / "results.json"
RESULTS_GANDI   = BASE_DIR / "results_gandi.json"
HISTORY_DB      = BASE_DIR / "history.db"
PUBLIC_DIR      = BASE_DIR / "public"
OUTPUT_FILE     = PUBLIC_DIR / "index.html"

# Horizon de l'onglet "Echeances" (jours). Au-dela, l'element est seulement compte.
HORIZON_DAYS    = 60


# ── Echeances ────────────────────────────────────────────────────────────────
def _iso_from_msg(msg: str | None) -> str | None:
    """Fallback pour les anciens results.json : extrait '(09/10/2026)' du message."""
    m = re.search(r"\((\d{2})/(\d{2})/(\d{4})\)", msg or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def _days_until(expires_iso: str | None, today: date) -> int | None:
    if not expires_iso:
        return None
    try:
        return (date.fromisoformat(expires_iso[:10]) - today).days
    except ValueError:
        return None


def _status_for(days: int | None, warn: int = 30) -> str:
    if days is None:
        return "unknown"
    if days <= 7:
        return "critical"
    if days <= warn:
        return "warning"
    return "ok"


def next_alert(expires_iso: str, days: int) -> tuple[str, str | None]:
    """Prochain email prevu selon la politique de history.py (J-30, J-15, quotidien J-7).

    Retourne (libelle, date_iso_ou_None).
    """
    exp = date.fromisoformat(expires_iso[:10])
    if days < 0:
        return "Expire", None
    if days <= 7:
        return "Email quotidien en cours", None
    if days <= 15:
        d = exp - timedelta(days=7)
        return "Emails quotidiens des J-7", d.isoformat()
    if days <= 30:
        d = exp - timedelta(days=15)
        return "Email J-15", d.isoformat()
    d = exp - timedelta(days=30)
    return "Email J-30", d.isoformat()


def build_echeances(sites: list[dict], gandi: list[dict], today: date) -> tuple[list[dict], int]:
    """Liste triee des prochaines expirations (SSL + NDD), dedoublonnee.

    - SSL : une entree par hote reellement teste
    - NDD : le resultat Gandi (plus fiable, a l'info auto-renew) remplace le WHOIS
    - les elements ignores (exclusions.txt) ne sont pas listes
    Retourne (items dans l'horizon, nb d'elements ignores ecartes).
    """
    items: dict[tuple[str, str], dict] = {}
    client_by_domain = {}
    for s in sites:
        for n in (s.get("domaine"), host_of(s.get("url"))):
            if n:
                client_by_domain.setdefault(n.lower(), s.get("client"))
    muted_count = 0

    for s in sites:
        host = host_of(s.get("url"))
        if s.get("muted"):
            muted_count += 1
            continue
        for kind, iso_key, msg_key in (("ssl", "ssl_expires_iso", "ssl_msg"),
                                       ("ndd", "ndd_expires_iso", "ndd_msg")):
            iso = s.get(iso_key) or _iso_from_msg(s.get(msg_key))
            days = _days_until(iso, today)
            if days is None or (kind, host) in items:
                continue
            items[(kind, host)] = {
                "kind": kind, "name": host, "client": s.get("client"),
                "expires_iso": iso, "days": days, "autorenew": None, "source": "site",
            }

    for d in gandi:
        fqdn = (d.get("fqdn") or "").lower()
        if d.get("muted"):
            muted_count += 1
            items.pop(("ndd", fqdn), None)
            continue
        days = _days_until(d.get("expires_iso"), today)
        if days is None:
            continue
        prev = items.get(("ndd", fqdn))
        if prev and prev["expires_iso"] > d["expires_iso"]:
            # Le WHOIS du jour voit une date plus lointaine : le domaine a ete
            # renouvele depuis le dernier scan Gandi -> on garde le WHOIS.
            continue
        items[("ndd", fqdn)] = {
            "kind": "ndd", "name": fqdn, "client": client_by_domain.get(fqdn),
            "expires_iso": d["expires_iso"], "days": days,
            "autorenew": d.get("autorenew"), "source": "gandi",
            "unverified": bool(d.get("unverified")),
        }

    out = []
    for it in items.values():
        if it["days"] > HORIZON_DAYS:
            continue
        label, when = next_alert(it["expires_iso"], it["days"])
        it["status"] = _status_for(it["days"])
        it["next_label"] = label
        it["next_date"] = when
        out.append(it)
    out.sort(key=lambda x: (x["days"], x["kind"], x["name"]))
    return out, muted_count


def health_score(site: dict) -> int:
    if not site.get("up"):
        return 0
    score = 100
    if site.get("ssl_st") == "critical":   score -= 40
    elif site.get("ssl_st") == "warning":  score -= 15
    elif site.get("ssl_st") in ("none", "error"): score -= 25
    if site.get("ndd_st") == "critical":   score -= 30
    elif site.get("ndd_st") == "warning":  score -= 10
    psi = (site.get("psi") or {}).get("score")
    if psi is not None:
        if psi < 50:   score -= 20
        elif psi < 70: score -= 10
    bstatus = (site.get("backup") or {}).get("status")
    if bstatus == "critical": score -= 20
    elif bstatus == "warning": score -= 5
    return max(0, min(100, score))


def sanitize_site(site: dict, today: date | None = None) -> dict:
    today = today or date.today()
    ssl_iso = site.get("ssl_expires_iso") or _iso_from_msg(site.get("ssl_msg"))
    ndd_iso = site.get("ndd_expires_iso") or _iso_from_msg(site.get("ndd_msg"))
    return {
        "client":       site.get("client"),
        "domaine":      site.get("domaine"),
        "url":          site.get("url"),
        "heb_dom":      site.get("heb_dom"),
        "up":           site.get("up"),
        "up_msg":       site.get("up_msg"),
        "response_ms":  site.get("response_ms"),
        "ssl_st":       site.get("ssl_st"),
        "ssl_msg":      site.get("ssl_msg"),
        "ssl_days":     site.get("ssl_days"),
        "ssl_expires_iso": ssl_iso,
        "ndd_st":       site.get("ndd_st"),
        "ndd_msg":      site.get("ndd_msg"),
        "ndd_days":     site.get("ndd_days"),
        "ndd_expires_iso": ndd_iso,
        "muted":        bool(site.get("muted")),
        "muted_reason": site.get("muted_reason") or "",
        "psi":          site.get("psi") or {},
        "stack": {
            "cms":         (site.get("stack") or {}).get("cms"),
            "cms_version": (site.get("stack") or {}).get("cms_version"),
            "cms_outdated":(site.get("stack") or {}).get("cms_outdated"),
            "php_version": (site.get("stack") or {}).get("php_version"),
            "server":      (site.get("stack") or {}).get("server"),
        },
        "backup": {
            "status":     (site.get("backup") or {}).get("status"),
            "days_since": (site.get("backup") or {}).get("days_since"),
            "last_backup":(site.get("backup") or {}).get("last_backup"),
        },
        "health_score": health_score(site),
    }


def sanitize_gandi(domain: dict, today: date | None = None) -> dict:
    """Recalcule jours/statut a la date du build : results_gandi.json peut dater
    de plusieurs semaines si gandi_monitor.py n'a pas tourne entre-temps."""
    today = today or date.today()
    iso = domain.get("expires_iso")
    days = _days_until(iso, today)
    status, message = domain.get("status"), domain.get("message")
    if days is not None:
        status = _status_for(days)
        label = date.fromisoformat(iso[:10]).strftime("%d/%m/%Y")
        if days < 0:
            message = f"EXPIRE depuis {-days}j ({label})"
        elif status == "ok":
            message = f"Valide {days}j ({label})"
        else:
            message = f"Expire dans {days}j ({label})"
    return {
        "fqdn":        domain.get("fqdn"),
        "status":      status,
        "days_left":   days if days is not None else domain.get("days_left"),
        "expires_iso": iso,
        "message":     message,
        "muted":       bool(domain.get("muted")),
        "muted_reason":domain.get("muted_reason") or "",
        "rdap_ok":     domain.get("rdap_ok"),
        "autorenew":   domain.get("autorenew", False),
        "tld":         domain.get("tld", ""),
        "nameservers": domain.get("nameservers", []),
    }


def load_trends_for_all(sites: list[dict], days: int = 30) -> dict:
    trends = {}
    for s in sites:
        dom = s.get("domaine")
        if not dom:
            continue
        rows = history.fetch_trends(HISTORY_DB, dom, days=days)
        trends[dom] = [
            {"ts": r["ts"], "up": r["up"], "response_ms": r["response_ms"],
             "psi_score": r["psi_score"], "ssl_days": r["ssl_days"]}
            for r in rows
        ]
    return trends


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow">
<title>Monitoring Albys</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{
  --bg:#0f172a;--card:#1e293b;--card-hover:#273449;--border:#334155;
  --text:#e2e8f0;--muted:#94a3b8;
  --ok:#22c55e;--warn:#f59e0b;--crit:#ef4444;--none:#64748b;--accent:#3b82f6;
  --gandi:#7c3aed;--gandi-light:rgba(124,58,237,.15);
}
*{box-sizing:border-box}
body{margin:0;padding:16px;background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;line-height:1.5}
h1{margin:0 0 4px;font-size:22px}
.sub{color:var(--muted);margin-bottom:20px;font-size:13px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:20px}
.stat{background:var(--card);border:1px solid var(--border);padding:10px 14px;border-radius:10px}
.stat-label{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.5px}
.stat-value{font-size:22px;font-weight:600;margin-top:2px}
.stat.ok .stat-value{color:var(--ok)}.stat.warn .stat-value{color:var(--warn)}.stat.crit .stat-value{color:var(--crit)}

/* Tabs */
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--border);margin-bottom:20px}
.tab-btn{background:transparent;border:none;color:var(--muted);font-size:14px;
  padding:10px 18px;cursor:pointer;border-bottom:2px solid transparent;
  margin-bottom:-1px;font-family:inherit;transition:color .15s,border-color .15s}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:var(--text);border-bottom-color:var(--accent);font-weight:600}
.tab-btn.gandi-tab.active{border-bottom-color:var(--gandi)}
.tab-count{display:inline-block;font-size:11px;background:rgba(255,255,255,.08);
  padding:1px 6px;border-radius:999px;margin-left:6px}
.tab-count.warn{background:rgba(245,158,11,.2);color:var(--warn)}
.tab-count.crit{background:rgba(239,68,68,.2);color:var(--crit)}
.tab-panel{display:none}.tab-panel.active{display:block}

/* Sites grid */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px}
.card.critical{border-left:4px solid var(--crit)}
.card.warning{border-left:4px solid var(--warn)}
.card.ok{border-left:4px solid var(--ok)}
.card-head{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}
.title{font-size:15px;font-weight:600}
.url{color:var(--muted);font-size:11px;word-break:break-all}
.health{font-size:18px;font-weight:700;padding:3px 10px;border-radius:999px;white-space:nowrap}
.h-high{background:rgba(34,197,94,.15);color:var(--ok)}
.h-mid{background:rgba(245,158,11,.15);color:var(--warn)}
.h-low{background:rgba(239,68,68,.15);color:var(--crit)}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.metric{background:rgba(0,0,0,.2);padding:8px;border-radius:6px}
.ml{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.5px}
.mv{font-size:12px;margin-top:2px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:middle}
.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}
.dot.crit{background:var(--crit)}.dot.none{background:var(--none)}
.badge{display:inline-block;background:rgba(59,130,246,.15);color:var(--accent);
  padding:2px 7px;border-radius:5px;font-size:10px;margin-right:3px;margin-top:3px}
.badge.outdated{background:rgba(239,68,68,.15);color:var(--crit)}
.tbtn{margin-top:10px;background:transparent;border:1px solid var(--border);color:var(--muted);
  padding:4px 10px;border-radius:6px;cursor:pointer;font-size:11px}
.tbtn:hover{color:var(--text);border-color:var(--accent)}
.tc{margin-top:10px;max-height:140px;display:none}

/* Gandi grid */
.gandi-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.gandi-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;transition:background .15s}
.gandi-card:hover{background:var(--card-hover)}
.gandi-card.critical{border-left:4px solid var(--crit)}
.gandi-card.warning{border-left:4px solid var(--warn)}
.gandi-card.ok{border-left:4px solid var(--ok)}
.gandi-card.unknown,.gandi-card.error{border-left:4px solid var(--none)}
.gandi-head{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.gandi-fqdn{font-size:14px;font-weight:600;word-break:break-all}
.gandi-fqdn a{color:var(--text);text-decoration:none}
.gandi-fqdn a:hover{color:#a78bfa}
.days-badge{flex-shrink:0;font-size:17px;font-weight:700;padding:3px 10px;border-radius:999px;white-space:nowrap}
.days-badge.ok{background:rgba(34,197,94,.15);color:var(--ok)}
.days-badge.warning{background:rgba(245,158,11,.15);color:var(--warn)}
.days-badge.critical{background:rgba(239,68,68,.15);color:var(--crit)}
.days-badge.unknown,.days-badge.error{background:rgba(100,116,139,.15);color:var(--none)}
.gandi-msg{margin-top:8px;font-size:12px;color:var(--muted)}
.gandi-meta{margin-top:8px;display:flex;flex-wrap:wrap;gap:5px}
.gtag{background:rgba(0,0,0,.2);padding:2px 8px;border-radius:5px;font-size:11px;color:var(--muted)}
.gtag.auto-on{color:var(--ok)}.gtag.auto-off{color:var(--crit);background:rgba(239,68,68,.08)}
.gtag.tld{color:var(--accent)}
.gandi-empty{grid-column:1/-1;text-align:center;color:var(--muted);padding:40px;
  background:var(--card);border:1px dashed var(--border);border-radius:10px;font-size:13px}

/* Echeances */
.ech-intro{color:var(--muted);font-size:12px;margin:-6px 0 14px}
.ech-group{margin-bottom:18px}
.ech-group h3{font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);
  margin:0 0 8px;display:flex;align-items:center;gap:8px}
.ech-group h3 .n{background:rgba(255,255,255,.08);border-radius:999px;padding:0 7px;font-size:11px}
.ech-group.g-crit h3{color:var(--crit)}.ech-group.g-warn h3{color:var(--warn)}
.ech-list{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden}
.ech-row{display:grid;grid-template-columns:64px 52px minmax(0,1fr) auto;align-items:center;
  gap:12px;padding:10px 14px;border-top:1px solid var(--border)}
.ech-row:first-child{border-top:none}
.jpill{font-weight:700;font-size:14px;text-align:center;padding:3px 0;border-radius:999px;
  font-variant-numeric:tabular-nums}
.jpill.critical{background:rgba(239,68,68,.15);color:var(--crit)}
.jpill.warning{background:rgba(245,158,11,.15);color:var(--warn)}
.jpill.ok{background:rgba(34,197,94,.12);color:var(--ok)}
.kind{font-size:10px;font-weight:600;text-align:center;padding:2px 0;border-radius:5px;letter-spacing:.4px}
.kind.ssl{background:rgba(59,130,246,.15);color:var(--accent)}
.kind.ndd{background:var(--gandi-light);color:#a78bfa}
.ech-name{font-weight:600;word-break:break-all}
.ech-sub{color:var(--muted);font-size:12px}
.ech-sub .warnflag{color:var(--crit)}
.ech-next{text-align:right;font-size:12px;color:var(--muted);white-space:nowrap}
.ech-next b{color:var(--text);font-weight:600}
.ech-empty{text-align:center;color:var(--muted);padding:40px;background:var(--card);
  border:1px dashed var(--border);border-radius:10px}
.ech-foot{color:var(--muted);font-size:11px;margin-top:6px}
.stale{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.35);color:var(--warn);
  padding:10px 14px;border-radius:10px;font-size:12px;margin-bottom:14px}
.stale code{color:var(--text)}
/* Elements ignores */
.card.muted,.gandi-card.muted{opacity:.45;border-left:4px solid var(--none)}
.card.muted:hover,.gandi-card.muted:hover{opacity:.8}
.mute-tag{display:inline-block;margin-top:4px;font-size:10px;color:var(--muted);
  background:rgba(100,116,139,.2);padding:1px 7px;border-radius:5px}
@media(max-width:600px){
  .ech-row{grid-template-columns:56px minmax(0,1fr);row-gap:4px}
  .ech-row .kind{grid-column:1;grid-row:2;align-self:start}
  .ech-row .ech-main{grid-column:2;grid-row:1/3}
  .ech-row .ech-next{grid-column:2;text-align:left;white-space:normal}
}
.footer{margin-top:24px;color:var(--muted);font-size:11px;text-align:center;
  padding-top:16px;border-top:1px solid var(--border)}
@media(max-width:500px){.metrics{grid-template-columns:1fr}.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<h1>Monitoring Albys</h1>
<div class="sub">Dernier scan : __LAST_RUN__ &middot; Genere le __GEN_DATE__ &middot; __STAT_SITES_ALL__ sites &middot; __GANDI_TOTAL__ domaines Gandi (dates verifiees : __DOMAINS_CHECKED__)</div>

<div class="stats">
  <div class="stat"><div class="stat-label">Total</div><div class="stat-value">__STAT_TOTAL__</div></div>
  <div class="stat ok"><div class="stat-label">En ligne</div><div class="stat-value">__STAT_UP__</div></div>
  <div class="stat crit"><div class="stat-label">Hors ligne</div><div class="stat-value">__STAT_DOWN__</div></div>
  <div class="stat crit"><div class="stat-label">Critiques</div><div class="stat-value">__STAT_CRIT__</div></div>
  <div class="stat warn"><div class="stat-label">Alertes</div><div class="stat-value">__STAT_WARN__</div></div>
  <div class="stat warn"><div class="stat-label">Echeances &le; 30j</div><div class="stat-value">__STAT_ECH30__</div></div>
  <div class="stat"><div class="stat-label">Sante moy.</div><div class="stat-value">__STAT_AVG__/100</div></div>
</div>

<div class="tabs">
  <button class="tab-btn active" onclick="switchTab('ech',this)">
    Echeances <span class="tab-count __ECH_TAB_CLS__">__ECH_COUNT__</span>
  </button>
  <button class="tab-btn" onclick="switchTab('sites',this)">
    Sites web <span class="tab-count __SITES_TAB_CLS__">__STAT_TOTAL__</span>
  </button>
  <button class="tab-btn gandi-tab" onclick="switchTab('gandi',this)">
    Domaines Gandi <span class="tab-count __GANDI_TAB_CLS__">__GANDI_TOTAL__</span>
  </button>
</div>

<!-- Panel Echeances -->
<div id="panel-ech" class="tab-panel active">
  <div class="ech-intro">Certificats SSL et noms de domaine qui expirent dans les __HORIZON__ prochains jours, avec le prochain email prevu (J-30, J-15, puis quotidien a partir de J-7).</div>
  __STALE_BANNER__
  <div id="ech"></div>
</div>

<!-- Panel Sites -->
<div id="panel-sites" class="tab-panel">
  <div class="grid" id="grid"></div>
</div>

<!-- Panel Gandi -->
<div id="panel-gandi" class="tab-panel">
  <div class="gandi-grid" id="gandi-grid"></div>
</div>

<div class="footer">
  Monitoring Albys &middot; Genere par GitHub Actions &middot; Pas d'indexation publique
</div>

<script>
const SITES  = __SITES_JSON__;
const TRENDS = __TRENDS_JSON__;
const GANDI  = __GANDI_JSON__;
const ECH    = __ECH_JSON__;
const ECH_MUTED = __ECH_MUTED__;

function switchTab(name, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('panel-'+name).classList.add('active');
  btn.classList.add('active');
}

function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function dotClass(st){ return ['ok','warning','critical'].includes(st)?st:'none'; }
function healthClass(s){ return s>=70?'h-high':s>=40?'h-mid':'h-low'; }
function frDate(iso){ if(!iso) return ''; const [y,m,d]=iso.slice(0,10).split('-'); return `${d}/${m}/${y}`; }
function frShort(iso){ if(!iso) return ''; const [y,m,d]=iso.slice(0,10).split('-');
  return `${d}/${m}`; }
function muteTag(o){ return o.muted?`<div class="mute-tag">Ignore${o.muted_reason&&o.muted_reason!=='Ignore'?' &middot; '+esc(o.muted_reason):''}</div>`:''; }
function cardClass(site){
  if(site.muted) return 'muted';
  if(!site.up||site.ssl_st==='critical'||site.ndd_st==='critical') return 'critical';
  if(site.ssl_st==='warning'||site.ndd_st==='warning'||(site.backup&&site.backup.status==='warning')) return 'warning';
  return 'ok';
}

/* ---- Echeances ---- */
(function(){
  const box = document.getElementById('ech');
  if(!ECH.length){
    box.innerHTML = '<div class="ech-empty">Rien n\'expire dans les __HORIZON__ prochains jours.</div>';
  } else {
    const groups = [
      {t:'Expires',             f:e=>e.days<0,                 c:'g-crit'},
      {t:'Cette semaine (J-7)', f:e=>e.days>=0&&e.days<=7,     c:'g-crit'},
      {t:'Sous 15 jours',       f:e=>e.days>7&&e.days<=15,     c:'g-warn'},
      {t:'Sous 30 jours',       f:e=>e.days>15&&e.days<=30,    c:'g-warn'},
      {t:'Sous __HORIZON__ jours', f:e=>e.days>30,             c:''},
    ];
    groups.forEach(g=>{
      const rows = ECH.filter(g.f);
      if(!rows.length) return;
      box.insertAdjacentHTML('beforeend', `
        <div class="ech-group ${g.c}">
          <h3>${g.t} <span class="n">${rows.length}</span></h3>
          <div class="ech-list">${rows.map(e=>{
            const sub = [e.client?esc(e.client):'', 'expire le '+frDate(e.expires_iso)];
            if(e.source==='gandi') sub.push(e.autorenew?'auto-renew Gandi':'<span class="warnflag">sans auto-renew</span>');
            if(e.unverified) sub.push('<span class="warnflag">date non verifiee</span>');
            const nxt = e.next_date
              ? `${esc(e.next_label)}<br><b>${frShort(e.next_date)}</b>`
              : `<b>${esc(e.next_label)}</b>`;
            return `<div class="ech-row">
              <div class="jpill ${e.status}">${e.days<0?'+'+(-e.days):'J-'+e.days}</div>
              <div class="kind ${e.kind}">${e.kind==='ssl'?'SSL':'DOMAINE'}</div>
              <div class="ech-main"><div class="ech-name">${esc(e.name)}</div>
                <div class="ech-sub">${sub.filter(Boolean).join(' &middot; ')}</div></div>
              <div class="ech-next">${nxt}</div>
            </div>`;}).join('')}</div>
        </div>`);
    });
  }
  if(ECH_MUTED) box.insertAdjacentHTML('beforeend',
    `<div class="ech-foot">${ECH_MUTED} element(s) ignore(s) via exclusions.txt ne sont pas listes.</div>`);
})();

/* ---- Sites ---- */
const grid = document.getElementById('grid');
const SITES_ORDER = SITES.map((s,i)=>i).sort((a,b)=>(SITES[a].muted-SITES[b].muted));
SITES_ORDER.forEach(i => { const site = SITES[i];
  const dc = s => dotClass(s).replace('warning','warn').replace('critical','crit');
  grid.insertAdjacentHTML('beforeend', `
    <div class="card ${cardClass(site)}">
      <div class="card-head">
        <div>
          <div class="title">${esc(site.client)}</div>
          <div class="url">${esc(site.domaine)}</div>
          ${muteTag(site)}
        </div>
        <div class="health ${healthClass(site.health_score)}">${site.health_score}</div>
      </div>
      <div class="metrics">
        <div class="metric"><div class="ml">Uptime</div>
          <div class="mv"><span class="dot ${site.up?'ok':'crit'}"></span>${esc(site.up_msg)}${site.response_ms?' &middot; '+site.response_ms+' ms':''}</div></div>
        <div class="metric"><div class="ml">SSL</div>
          <div class="mv"><span class="dot ${dc(site.ssl_st)}"></span>${esc(site.ssl_msg)}</div></div>
        <div class="metric"><div class="ml">Domaine</div>
          <div class="mv"><span class="dot ${dc(site.ndd_st)}"></span>${esc(site.ndd_msg)}</div></div>
        <div class="metric"><div class="ml">PageSpeed</div>
          <div class="mv">${site.psi&&site.psi.score!=null
            ?`<span class="dot ${site.psi.score>=90?'ok':site.psi.score>=50?'warn':'crit'}"></span>${site.psi.score}/100${site.psi.lcp_ms?' &middot; LCP '+site.psi.lcp_ms+'ms':''}`
            :'<span class="dot none"></span>Non mesure'}</div></div>
        <div class="metric"><div class="ml">Backup</div>
          <div class="mv">${site.backup&&site.backup.last_backup
            ?`<span class="dot ${dc(site.backup.status)}"></span>il y a ${site.backup.days_since} j`
            :'<span class="dot none"></span>Non configure'}</div></div>
        <div class="metric"><div class="ml">Hebergement</div>
          <div class="mv">${esc(site.heb_dom||'-')}</div></div>
      </div>
      ${site.stack&&(site.stack.cms||site.stack.php_version||site.stack.server)?`
      <div style="margin-top:10px">
        ${site.stack.cms?`<span class="badge ${site.stack.cms_outdated?'outdated':''}">${esc(site.stack.cms)}${site.stack.cms_version?' '+esc(site.stack.cms_version):''}</span>`:''}
        ${site.stack.php_version?`<span class="badge">PHP ${esc(site.stack.php_version)}</span>`:''}
        ${site.stack.server?`<span class="badge">${esc(site.stack.server.split('/')[0])}</span>`:''}
      </div>`:''}
      <button class="tbtn" data-domain="${esc(site.domaine)}" data-i="${i}">Voir la tendance 30j</button>
      <canvas class="tc" id="c${i}"></canvas>
    </div>`);
});

document.querySelectorAll('.tbtn').forEach(b=>{
  b.addEventListener('click',()=>{
    const dom=b.dataset.domain, i=b.dataset.i, canvas=document.getElementById('c'+i);
    if(canvas.style.display==='block'){canvas.style.display='none';b.textContent='Voir la tendance 30j';return;}
    canvas.style.display='block';b.textContent='Masquer la tendance';
    const points=TRENDS[dom]||[];
    if(canvas._c) canvas._c.destroy();
    canvas._c=new Chart(canvas,{type:'line',data:{
      labels:points.map(p=>p.ts.slice(5,10)),
      datasets:[
        {label:'Response ms',data:points.map(p=>p.response_ms),borderColor:'#3b82f6',yAxisID:'y',backgroundColor:'transparent'},
        {label:'PSI',data:points.map(p=>p.psi_score),borderColor:'#22c55e',yAxisID:'y1',backgroundColor:'transparent',spanGaps:true}
      ]},options:{responsive:true,maintainAspectRatio:false,
        plugins:{legend:{labels:{color:'#94a3b8',font:{size:10}}}},
        scales:{
          x:{ticks:{color:'#94a3b8',font:{size:9}},grid:{color:'#334155'}},
          y:{ticks:{color:'#94a3b8'},grid:{color:'#334155'}},
          y1:{position:'right',min:0,max:100,ticks:{color:'#94a3b8'},grid:{display:false}}
        }}});
  });
});

/* ---- Gandi ---- */
const gandiGrid = document.getElementById('gandi-grid');
if (GANDI.length === 0) {
  gandiGrid.innerHTML = '<div class="gandi-empty">Aucun domaine Gandi disponible.<br>Lancez <code>python gandi_monitor.py</code> pour commencer.</div>';
} else {
  const sorted = [...GANDI].sort((a,b)=>(a.muted-b.muted)||((a.days_left??9999)-(b.days_left??9999)));
  sorted.forEach(d => {
    const st0 = ['ok','warning','critical','unknown','error'].includes(d.status)?d.status:'unknown';
    const st = d.muted ? 'muted' : st0;
    const days = d.days_left!=null ? d.days_left+'j' : '?';
    const ns0 = d.nameservers&&d.nameservers.length ? d.nameservers[0].split('.')[0]+'…' : '';
    gandiGrid.insertAdjacentHTML('beforeend', `
      <div class="gandi-card ${st}">
        <div class="gandi-head">
          <div class="gandi-fqdn">
            <a href="https://admin.gandi.net/domain/${esc(d.fqdn)}" target="_blank" rel="noopener">${esc(d.fqdn)}</a>
          </div>
          <div class="days-badge ${st0}">${days}</div>
        </div>
        ${muteTag(d)}
        <div class="gandi-msg">${esc(d.message||'')}</div>
        <div class="gandi-meta">
          ${d.tld?`<span class="gtag tld">.${esc(d.tld)}</span>`:''}
          <span class="gtag ${d.autorenew?'auto-on':'auto-off'}">${d.autorenew?'✓ Auto-renew':'✗ Sans auto-renew'}</span>
          ${ns0?`<span class="gtag">NS: ${esc(ns0)}</span>`:''}
        </div>
      </div>`);
  });
}

if(location.hash==='#sites'){
  switchTab('sites', document.querySelectorAll('.tab-btn')[1]);
}
if(location.hash==='#gandi'){
  switchTab('gandi', document.querySelector('.gandi-tab'));
}
</script>
</body>
</html>
"""


def main():
    if not RESULTS_JSON.exists():
        print(f"Erreur : {RESULTS_JSON} introuvable — lance d'abord monitor.py")
        return 1

    today = date.today()
    exclusions = load_exclusions()

    data  = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    sites = [sanitize_site(s, today) for s in data.get("sites", [])]

    # Gandi
    gandi_domains = []
    gandi_last_run = None
    rdap_last_run = None
    if RESULTS_GANDI.exists():
        gandi_data    = json.loads(RESULTS_GANDI.read_text(encoding="utf-8"))
        gandi_domains = [sanitize_gandi(d, today) for d in gandi_data.get("domains", [])]
        gandi_last_run = gandi_data.get("last_run")
        rdap_last_run = gandi_data.get("rdap_last_run")

    def _age(stamp):
        try:
            return (today - datetime.strptime(stamp, "%d/%m/%Y %H:%M").date()).days
        except (TypeError, ValueError):
            return None

    # Fraicheur : API Gandi (gandi_monitor.py) ou registre public (refresh_domains.py)
    gandi_age = _age(gandi_last_run)
    rdap_age = _age(rdap_last_run)
    gandi_stale = gandi_age is None or gandi_age > 3
    rdap_fresh = rdap_age is not None and rdap_age <= 3
    for d_ in gandi_domains:
        d_["unverified"] = gandi_stale and not (rdap_fresh and d_.get("rdap_ok"))

    # Exclusions appliquees aussi au build : le dashboard reflete exclusions.txt
    # meme si le dernier scan date d'avant la modif du fichier.
    for s_ in sites:
        if not s_["muted"]:
            s_["muted"], s_["muted_reason"] = match_exclusion(exclusions, s_["domaine"], s_["url"])
    for d_ in gandi_domains:
        if not d_["muted"]:
            d_["muted"], d_["muted_reason"] = match_exclusion(exclusions, d_["fqdn"])

    echeances, ech_muted = build_echeances(sites, gandi_domains, today)
    ech_30 = sum(1 for e in echeances if e["days"] <= 30)

    stale_banner = ""
    unverified = sorted(d["fqdn"] for d in gandi_domains if d["unverified"] and not d["muted"])
    if gandi_domains and unverified:
        if rdap_fresh:
            stale_banner = (
                f'<div class="stale">Dates verifiees dans le registre public le {rdap_last_run}, '
                f'sauf {", ".join(unverified)} (pas de reponse du registre) : date du scan Gandi du '
                f'{gandi_last_run or "?"}, a verifier a la main.</div>'
            )
        else:
            stale_banner = (
                f'<div class="stale">Donnees Gandi du {gandi_last_run or "?"} '
                f'({gandi_age if gandi_age is not None else "?"} jours) : un domaine renouvele depuis '
                f'peut apparaitre a tort comme expire. Lancer <code>refresh_domains.py</code> '
                f'ou <code>gandi_monitor.py</code> pour rafraichir.</div>'
            )

    # Stats sites (hors sites ignores)
    actifs = [s for s in sites if not s["muted"]]
    total = len(actifs)
    up    = sum(1 for s in actifs if s["up"])
    crit  = sum(1 for s in actifs if not s["up"] or s["ssl_st"]=="critical" or s["ndd_st"]=="critical"
                or (s["backup"] or {}).get("status")=="critical")
    warn  = sum(1 for s in actifs if s["ssl_st"]=="warning" or s["ndd_st"]=="warning"
                or (s["backup"] or {}).get("status")=="warning")
    scores = [s["health_score"] for s in actifs]
    avg    = int(sum(scores)/len(scores)) if scores else 0

    # Stats gandi pour couleur du tab
    g_crit = sum(1 for d in gandi_domains if d["status"]=="critical" and not d["muted"])
    g_warn = sum(1 for d in gandi_domains if d["status"]=="warning" and not d["muted"])
    e_crit = sum(1 for e in echeances if e["days"] <= 7)
    ech_tab_cls = "crit" if e_crit else ("warn" if ech_30 else "")
    gandi_tab_cls  = "crit" if g_crit else ("warn" if g_warn else "")
    sites_tab_cls  = "crit" if crit   else ("warn" if warn   else "")

    trends = load_trends_for_all(sites, days=30)
    PUBLIC_DIR.mkdir(exist_ok=True)

    html = HTML_TEMPLATE
    html = html.replace("__LAST_RUN__",      data.get("last_run") or "jamais")
    html = html.replace("__GEN_DATE__",      datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC"))
    html = html.replace("__STAT_TOTAL__",    str(total))
    html = html.replace("__STAT_SITES_ALL__", str(len(sites)))
    html = html.replace("__STAT_ECH30__",    str(ech_30))
    html = html.replace("__ECH_COUNT__",     str(len(echeances)))
    html = html.replace("__ECH_TAB_CLS__",   ech_tab_cls)
    html = html.replace("__ECH_MUTED__",     str(ech_muted))
    html = html.replace("__HORIZON__",       str(HORIZON_DAYS))
    html = html.replace("__TODAY_ISO__",     today.isoformat())
    html = html.replace("__DOMAINS_CHECKED__", (rdap_last_run if rdap_fresh else gandi_last_run) or "jamais")
    html = html.replace("__STALE_BANNER__",  stale_banner)
    html = html.replace("__ECH_JSON__",      json.dumps(echeances,     ensure_ascii=False))
    html = html.replace("__STAT_UP__",       str(up))
    html = html.replace("__STAT_DOWN__",     str(total - up))
    html = html.replace("__STAT_CRIT__",     str(crit))
    html = html.replace("__STAT_WARN__",     str(warn))
    html = html.replace("__STAT_AVG__",      str(avg))
    html = html.replace("__GANDI_TOTAL__",   str(len(gandi_domains)))
    html = html.replace("__GANDI_TAB_CLS__", gandi_tab_cls)
    html = html.replace("__SITES_TAB_CLS__", sites_tab_cls)
    html = html.replace("__SITES_JSON__",    json.dumps(sites,         ensure_ascii=False))
    html = html.replace("__TRENDS_JSON__",   json.dumps(trends,        ensure_ascii=False))
    html = html.replace("__GANDI_JSON__",    json.dumps(gandi_domains, ensure_ascii=False))

    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"Dashboard statique genere : {OUTPUT_FILE} ({len(html)/1024:.1f} Ko)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
