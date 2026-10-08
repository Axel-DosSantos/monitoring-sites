"""Liste des domaines ignores par le monitoring (projets arretes, sites archives...).

Source : exclusions.txt a la racine du repo (editable directement sur GitHub).

Un domaine exclu :
  - continue d'etre verifie et affiche sur le dashboard (grise, badge "Ignore")
  - n'envoie AUCUN email (SSL, NDD, DOWN, backup, Gandi)
  - n'est pas compte dans les stats ni dans les "Prochaines echeances"

Format du fichier (une entree par ligne) :
    lead-portage.com      # projet arrete en 2025
    # ligne de commentaire ignoree

Le match se fait sur le nom de domaine exact (sans www.), compare a la fois a
la colonne "Nom de domaine" de l'Excel, a l'hote de l'URL testee et au FQDN Gandi.
"""
from __future__ import annotations

import re
from pathlib import Path

EXCLUSIONS_FILE = Path(__file__).parent / "exclusions.txt"


def _norm(value: str | None) -> str:
    if not value:
        return ""
    v = str(value).strip().lower()
    v = re.sub(r"^https?://", "", v).split("/")[0].split("?")[0].split(":")[0]
    if v.startswith("www."):
        v = v[4:]
    return v


def load_exclusions(path: Path = EXCLUSIONS_FILE) -> dict[str, str]:
    """Retourne {domaine_normalise: raison}."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        domain, _, reason = line.partition("#")
        d = _norm(domain)
        if d:
            out[d] = reason.strip()
    return out


def match(exclusions: dict[str, str], *names: str | None) -> tuple[bool, str]:
    """(True, raison) si l'un des noms (domaine, URL, fqdn) est exclu."""
    for n in names:
        d = _norm(n)
        if d and d in exclusions:
            return True, exclusions[d] or "Ignore"
    return False, ""


def host_of(url: str | None) -> str:
    return _norm(url)
