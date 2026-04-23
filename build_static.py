#!/usr/bin/env python3
"""
Genere une version statique du dashboard pour GitHub Pages.
- Lit results.json + history.db
- Genere public/index.html (standalone, embarque les donnees en JSON)
- Safe pour publication publique : aucune donnee sensible (pas de SMTP, tokens, etc.)
- Les tendances sont embarquees dans le HTML pour eviter les appels API
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import history

BASE_DIR = Path(__file__).parent
RESULTS_JSON = BASE_DIR / "results.json"
HISTORY_DB = BASE_DIR / "history.db"
PUBLIC_DIR = BASE_DIR / "public"
OUTPUT_FILE = PUBLIC_DIR / "index.html"


def health_score(site: dict) -> int:
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


def sanitize_site(site: dict) -> dict:
    """Retire les donnees sensibles avant publication publique."""
    return {
        "client": site.get("client"),
        "domaine": site.get("domaine"),
        "url": site.get("url"),
        "heb_dom": site.get("heb_dom"),
        "up": site.get("up"),
        "up_msg": site.get("up_msg"),
        "response_ms": site.get("response_ms"),
        "ssl_st": site.get("ssl_st"),
        "ssl_msg": site.get("ssl_msg"),
        "ssl_days": site.get("ssl_days"),
        "ndd_st": site.get("ndd_st"),
        "ndd_msg": site.get("ndd_msg"),
        "ndd_days": site.get("ndd_days"),
        "psi": site.get("psi") or {},
        "stack": {
            "cms": (site.get("stack") or {}).get("cms"),
            "cms_version": (site.get("stack") or {}).get("cms_version"),
            "cms_outdated": (site.get("stack") or {}).get("cms_outdated"),
            "php_version": (site.get("stack") or {}).get("php_version"),
            "server": (site.get("stack") or {}).get("server"),
        },
        "backup": {
            "status": (site.get("backup") or {}).get("status"),
            "days_since": (site.get("backup") or {}).get("days_since"),
            "last_backup": (site.get("backup") or {}).get("last_backup"),
        },
        "health_score": health_score(site),
    }


def load_trends_for_all(sites: list[dict], days: int = 30) -> dict:
    trends = {}
    for s in sites:
        dom = s.get("domaine")
        if not dom:
            continue
        rows = history.fetch_trends(HISTORY_DB, dom, days=days)
        trends[dom] = [
            {
                "ts": r["ts"],
                "up": r["up"],
                "response_ms": r["response_ms"],
                "psi_score": r["psi_score"],
                "ssl_days": r["ssl_days"],
            }
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
:root {
  --bg:#0f172a; --card:#1e293b; --card-hover:#273449; --border:#334155;
  --text:#e2e8f0; --muted:#94a3b8;
  --ok:#22c55e; --warn:#f59e0b; --crit:#ef4444; --none:#64748b; --accent:#3b82f6;
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
.footer{margin-top:24px;color:var(--muted);font-size:11px;text-align:center;
  padding-top:16px;border-top:1px solid var(--border)}
@media (max-width:500px){.metrics{grid-template-columns:1fr}.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<h1>Monitoring Albys</h1>
<div class="sub">Dernier scan : __LAST_RUN__ &middot; Genere le __GEN_DATE__</div>

<div class="stats">
  <div class="stat"><div class="stat-label">Total</div><div class="stat-value">__STAT_TOTAL__</div></div>
  <div class="stat ok"><div class="stat-label">En ligne</div><div class="stat-value">__STAT_UP__</div></div>
  <div class="stat crit"><div class="stat-label">Hors ligne</div><div class="stat-value">__STAT_DOWN__</div></div>
  <div class="stat crit"><div class="stat-label">Critiques</div><div class="stat-value">__STAT_CRIT__</div></div>
  <div class="stat warn"><div class="stat-label">Alertes</div><div class="stat-value">__STAT_WARN__</div></div>
  <div class="stat"><div class="stat-label">Sante moy.</div><div class="stat-value">__STAT_AVG__/100</div></div>
</div>

<div class="grid" id="grid"></div>

<div class="footer">
  Monitoring Albys &middot; Genere par GitHub Actions &middot; Pas d'indexation publique
</div>

<script>
const SITES = __SITES_JSON__;
const TRENDS = __TRENDS_JSON__;

function healthClass(s){
  if(s>=70) return 'h-high';
  if(s>=40) return 'h-mid';
  return 'h-low';
}
function cardClass(site){
  if(!site.up || site.ssl_st==='critical' || site.ndd_st==='critical') return 'critical';
  if(site.ssl_st==='warning' || site.ndd_st==='warning' || (site.backup && site.backup.status==='warning')) return 'warning';
  return 'ok';
}
function esc(s){
  return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function dotClass(st){return ['ok','warning','critical'].includes(st)?st:'none'}

const grid = document.getElementById('grid');
SITES.forEach((site, i) => {
  const cls = cardClass(site);
  const html = `
    <div class="card ${cls}">
      <div class="card-head">
        <div>
          <div class="title">${esc(site.client)}</div>
          <div class="url">${esc(site.domaine)}</div>
        </div>
        <div class="health ${healthClass(site.health_score)}">${site.health_score}</div>
      </div>
      <div class="metrics">
        <div class="metric">
          <div class="ml">Uptime</div>
          <div class="mv"><span class="dot ${site.up?'ok':'crit'}"></span>${esc(site.up_msg)}${site.response_ms?' &middot; '+site.response_ms+' ms':''}</div>
        </div>
        <div class="metric">
          <div class="ml">SSL</div>
          <div class="mv"><span class="dot ${dotClass(site.ssl_st).replace('warning','warn').replace('critical','crit')}"></span>${esc(site.ssl_msg)}</div>
        </div>
        <div class="metric">
          <div class="ml">Domaine</div>
          <div class="mv"><span class="dot ${dotClass(site.ndd_st).replace('warning','warn').replace('critical','crit')}"></span>${esc(site.ndd_msg)}</div>
        </div>
        <div class="metric">
          <div class="ml">PageSpeed</div>
          <div class="mv">${site.psi && site.psi.score!=null
            ? `<span class="dot ${site.psi.score>=90?'ok':(site.psi.score>=50?'warn':'crit')}"></span>${site.psi.score}/100${site.psi.lcp_ms?' &middot; LCP '+site.psi.lcp_ms+'ms':''}`
            : '<span class="dot none"></span>Non mesure'}</div>
        </div>
        <div class="metric">
          <div class="ml">Backup</div>
          <div class="mv">${site.backup && site.backup.last_backup
            ? `<span class="dot ${dotClass(site.backup.status).replace('warning','warn').replace('critical','crit')}"></span>il y a ${site.backup.days_since} j`
            : '<span class="dot none"></span>Non configure'}</div>
        </div>
        <div class="metric">
          <div class="ml">Hebergement</div>
          <div class="mv">${esc(site.heb_dom||'-')}</div>
        </div>
      </div>
      ${site.stack && (site.stack.cms||site.stack.php_version||site.stack.server)?`
      <div style="margin-top:10px">
        ${site.stack.cms?`<span class="badge ${site.stack.cms_outdated?'outdated':''}">${esc(site.stack.cms)}${site.stack.cms_version?' '+esc(site.stack.cms_version):''}</span>`:''}
        ${site.stack.php_version?`<span class="badge">PHP ${esc(site.stack.php_version)}</span>`:''}
        ${site.stack.server?`<span class="badge">${esc(site.stack.server.split('/')[0])}</span>`:''}
      </div>`:''}
      <button class="tbtn" data-domain="${esc(site.domaine)}" data-i="${i}">Voir la tendance 30j</button>
      <canvas class="tc" id="c${i}"></canvas>
    </div>`;
  grid.insertAdjacentHTML('beforeend', html);
});

document.querySelectorAll('.tbtn').forEach(b=>{
  b.addEventListener('click', () => {
    const dom = b.dataset.domain;
    const i = b.dataset.i;
    const canvas = document.getElementById('c'+i);
    if (canvas.style.display === 'block') {
      canvas.style.display='none';
      b.textContent='Voir la tendance 30j';
      return;
    }
    canvas.style.display='block';
    b.textContent='Masquer la tendance';
    const points = TRENDS[dom] || [];
    if (canvas._c) canvas._c.destroy();
    canvas._c = new Chart(canvas, {
      type:'line',
      data:{
        labels: points.map(p=>p.ts.slice(5,10)),
        datasets:[
          {label:'Response ms', data:points.map(p=>p.response_ms), borderColor:'#3b82f6', yAxisID:'y', backgroundColor:'transparent'},
          {label:'PSI', data:points.map(p=>p.psi_score), borderColor:'#22c55e', yAxisID:'y1', backgroundColor:'transparent', spanGaps:true}
        ]
      },
      options:{
        responsive:true, maintainAspectRatio:false,
        plugins:{legend:{labels:{color:'#94a3b8',font:{size:10}}}},
        scales:{
          x:{ticks:{color:'#94a3b8',font:{size:9}},grid:{color:'#334155'}},
          y:{ticks:{color:'#94a3b8'},grid:{color:'#334155'}},
          y1:{position:'right',min:0,max:100,ticks:{color:'#94a3b8'},grid:{display:false}}
        }
      }
    });
  });
});
</script>
</body>
</html>
"""


def main():
    if not RESULTS_JSON.exists():
        print(f"Erreur : {RESULTS_JSON} introuvable — lance d'abord monitor.py")
        return 1

    data = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    sites = [sanitize_site(s) for s in data.get("sites", [])]

    # Stats
    total = len(sites)
    up = sum(1 for s in sites if s["up"])
    crit = sum(
        1 for s in sites
        if not s["up"] or s["ssl_st"] == "critical" or s["ndd_st"] == "critical"
        or (s["backup"] or {}).get("status") == "critical"
    )
    warn = sum(
        1 for s in sites
        if s["ssl_st"] == "warning" or s["ndd_st"] == "warning"
        or (s["backup"] or {}).get("status") == "warning"
    )
    scores = [s["health_score"] for s in sites]
    avg = int(sum(scores) / len(scores)) if scores else 0

    trends = load_trends_for_all(sites, days=30)

    PUBLIC_DIR.mkdir(exist_ok=True)

    html = HTML_TEMPLATE
    html = html.replace("__LAST_RUN__", data.get("last_run") or "jamais")
    html = html.replace("__GEN_DATE__", datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC"))
    html = html.replace("__STAT_TOTAL__", str(total))
    html = html.replace("__STAT_UP__", str(up))
    html = html.replace("__STAT_DOWN__", str(total - up))
    html = html.replace("__STAT_CRIT__", str(crit))
    html = html.replace("__STAT_WARN__", str(warn))
    html = html.replace("__STAT_AVG__", str(avg))
    html = html.replace("__SITES_JSON__", json.dumps(sites, ensure_ascii=False))
    html = html.replace("__TRENDS_JSON__", json.dumps(trends, ensure_ascii=False))

    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"Dashboard statique genere : {OUTPUT_FILE} ({len(html)/1024:.1f} Ko)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
