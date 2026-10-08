"""Tests : exclusions.txt + calcul des prochaines echeances du dashboard.

Usage : python -m tests.test_echeances
"""
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import exclusions
import build_static as bs

TODAY = date(2026, 10, 5)


def test_exclusions_match():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "ex.txt"
        f.write_text("# commentaire\nlead-portage.com  # projet arrete\n\nWWW.Old-Site.fr\n", encoding="utf-8")
        ex = exclusions.load_exclusions(f)
        assert ex == {"lead-portage.com": "projet arrete", "old-site.fr": ""}
        # domaine, URL avec slash final, www, casse
        assert exclusions.match(ex, "leadportage.com", "https://lead-portage.com/")[0]
        assert exclusions.match(ex, "https://www.LEAD-PORTAGE.com")[0]
        assert exclusions.match(ex, "old-site.fr") == (True, "Ignore")
        assert not exclusions.match(ex, "lead-portage.fr", "https://autre.fr")[0]
    print("  test_exclusions_match OK")


def test_next_alert_tiers():
    exp = "2026-11-30"
    assert bs.next_alert(exp, 56) == ("Email J-30", "2026-10-31")
    assert bs.next_alert(exp, 25) == ("Email J-15", "2026-11-15")
    assert bs.next_alert(exp, 12) == ("Emails quotidiens des J-7", "2026-11-23")
    assert bs.next_alert(exp, 7)[1] is None
    assert bs.next_alert(exp, -2) == ("Expire", None)
    print("  test_next_alert_tiers OK")


def test_build_echeances_dedup_and_mute():
    sites = [
        # deux lignes Excel vers la meme URL -> une seule echeance SSL
        {"client": "A", "domaine": "a.fr", "url": "https://a.fr", "muted": False,
         "ssl_expires_iso": "2026-10-15", "ndd_expires_iso": "2027-01-01"},
        {"client": "A", "domaine": "a-bis.fr", "url": "https://a.fr/", "muted": False,
         "ssl_expires_iso": "2026-10-15", "ndd_expires_iso": None},
        # site ignore
        {"client": "B", "domaine": "b.fr", "url": "https://b.fr", "muted": True,
         "ssl_expires_iso": "2026-10-08"},
        # ancien format sans *_expires_iso : fallback sur le message
        {"client": "C", "domaine": "c.fr", "url": "https://c.fr", "muted": False,
         "ssl_msg": "Expire dans 20j (25/10/2026)", "ndd_msg": "Date inconnue"},
    ]
    gandi = [
        {"fqdn": "c.fr", "expires_iso": "2026-10-20", "autorenew": False, "muted": False},
        # Gandi perime (avant renouvellement) : le WHOIS du jour l'emporte
        {"fqdn": "a.fr", "expires_iso": "2026-06-01", "autorenew": False, "muted": False},
        {"fqdn": "b.fr", "expires_iso": "2026-10-10", "autorenew": False, "muted": True},
    ]
    items, muted = bs.build_echeances(sites, gandi, TODAY)
    keys = [(i["kind"], i["name"], i["days"]) for i in items]
    assert keys == [("ssl", "a.fr", 10), ("ndd", "c.fr", 15), ("ssl", "c.fr", 20)], keys
    assert muted == 2
    assert items[1]["source"] == "gandi"
    print("  test_build_echeances_dedup_and_mute OK")


def test_sanitize_gandi_recomputes_stale_days():
    d = bs.sanitize_gandi({"fqdn": "x.fr", "expires_iso": "2026-10-25", "days_left": 138,
                           "status": "ok", "message": "Valide 138j (25/10/2026)"}, TODAY)
    assert d["days_left"] == 20 and d["status"] == "warning"
    assert d["message"].startswith("Expire dans 20j")
    print("  test_sanitize_gandi_recomputes_stale_days OK")


def test_rdap_parse_and_refresh():
    import json
    from datetime import datetime, timezone
    from checks.rdap import parse_expiration
    import refresh_domains

    # formats reels : 6 decimales (.fr), 3 decimales (.legal), sans decimales (Verisign)
    for raw, iso in (("2027-07-31T10:57:32.267318Z", "2027-07-31"),
                     ("2027-10-25T17:13:07.125Z", "2027-10-25"),
                     ("2027-07-22T11:12:45Z", "2027-07-22"),
                     ("2027-07-22T11:12:45.1234567Z", "2027-07-22")):
        dt = parse_expiration({"events": [{"eventAction": "registration", "eventDate": "2020-01-01T00:00:00Z"},
                                          {"eventAction": "expiration", "eventDate": raw}]})
        assert dt.strftime("%Y-%m-%d") == iso and dt.tzinfo is not None
    assert parse_expiration({"events": []}) is None

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "g.json"
        f.write_text(json.dumps({"last_run": "09/06/2026 10:40", "domains": [
            {"fqdn": "renouvele.fr", "expires_iso": "2026-07-31", "days_left": -60,
             "status": "critical", "autorenew": False, "tld": "fr"},
            {"fqdn": "sans-rdap.eu", "expires_iso": "2026-07-22", "status": "critical", "tld": "eu"},
        ]}), encoding="utf-8")
        fake = {"renouvele.fr": datetime(2027, 7, 31, tzinfo=timezone.utc)}
        ok, failed = refresh_domains.refresh(f, lookup=fake.get)
        assert (ok, failed) == (1, 1)
        out = json.loads(f.read_text(encoding="utf-8"))
        d0, d1 = out["domains"]
        assert d0["expires_iso"] == "2027-07-31" and d0["status"] == "ok" and d0["rdap_ok"]
        assert d1["expires_iso"] == "2026-07-22" and d1["rdap_ok"] is False
        assert out["last_run"] == "09/06/2026 10:40" and out["rdap_last_run"]
    print("  test_rdap_parse_and_refresh OK")


if __name__ == "__main__":
    test_exclusions_match()
    test_next_alert_tiers()
    test_build_echeances_dedup_and_mute()
    test_sanitize_gandi_recomputes_stale_days()
    test_rdap_parse_and_refresh()
    print("Tous les tests echeances OK")
