"""Tests de simulation de la politique d'alertes pour les 4 categories.

Verifie :
- SSL/NDD (countdown) : J-30 (1x), J-15 (1x), J-7..J-0 (quotidien), reset si renouvellement
- DOWN   (sticky)     : 1ere alerte + rappel quotidien tant que le site est down
- BACKUP (sticky)     : 1ere alerte + rappel tous les 7 jours, reset quand resolu

Usage : python -m tests.test_alerts
"""
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import history


def simulate(db, domain, category, today_iso, **kwargs):
    """Simule un run : decide, et marque si on envoie."""
    should, atype = history.should_send_alert(
        db, domain, category, today_iso=today_iso, **kwargs
    )
    if should:
        history.mark_alert_sent(
            db, domain, category, atype, today_iso=today_iso,
            expires_iso=kwargs.get("expires_iso"),
        )
    return should, atype


# --- Tests COUNTDOWN ---------------------------------------------------------

def test_ssl_full_sequence():
    """SSL: 35 jours -> 10 alertes (1 J-30 + 1 J-15 + 8 daily J-7..J-0)."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        cert_expires = "2026-02-01"
        start = date(2025, 12, 28)
        sent = []
        for offset in range(36):
            today = start + timedelta(days=offset)
            days = (date.fromisoformat(cert_expires) - today).days
            s, at = simulate(db, "example.com", "ssl",
                             today.isoformat(),
                             expires_iso=cert_expires, days_left=days)
            if s:
                sent.append((today.isoformat(), days, at))

        by_type = {}
        for _, _, at in sent:
            by_type[at] = by_type.get(at, 0) + 1
        print(f"SSL countdown : {len(sent)} alertes - {by_type}")
        assert by_type.get("j30") == 1
        assert by_type.get("j15") == 1
        assert by_type.get("daily") == 8
        assert len(sent) == 10
        print("  test_ssl_full_sequence OK")


def test_ndd_full_sequence():
    """NDD: meme logique que SSL."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        ndd_expires = "2026-03-15"
        start = date(2026, 2, 8)
        sent = []
        for offset in range(36):
            today = start + timedelta(days=offset)
            days = (date.fromisoformat(ndd_expires) - today).days
            s, at = simulate(db, "example.com", "ndd",
                             today.isoformat(),
                             expires_iso=ndd_expires, days_left=days)
            if s:
                sent.append((today.isoformat(), days, at))

        by_type = {}
        for _, _, at in sent:
            by_type[at] = by_type.get(at, 0) + 1
        print(f"NDD countdown : {len(sent)} alertes - {by_type}")
        assert by_type.get("j30") == 1
        assert by_type.get("j15") == 1
        assert by_type.get("daily") == 8
        assert len(sent) == 10
        print("  test_ndd_full_sequence OK")


def test_renouvellement_reset_etat():
    """Si la date d'expiration change, l'etat est reset (renouvellement)."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        simulate(db, "ex.com", "ssl", "2026-01-01",
                 expires_iso="2026-01-31", days_left=30)
        simulate(db, "ex.com", "ssl", "2026-01-16",
                 expires_iso="2026-01-31", days_left=15)
        s, at = simulate(db, "ex.com", "ssl", "2027-01-01",
                         expires_iso="2027-01-31", days_left=30)
        assert s and at == "j30", f"J-30 doit repartir, got ({s}, {at})"
        print("  test_renouvellement_reset_etat OK")


def test_dedup_meme_journee_countdown():
    """2 runs le meme jour = 1 seule alerte."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        s1, _ = simulate(db, "ex.com", "ssl", "2026-01-26",
                         expires_iso="2026-01-31", days_left=5)
        s2, _ = simulate(db, "ex.com", "ssl", "2026-01-26",
                         expires_iso="2026-01-31", days_left=5)
        assert s1 and not s2
        print("  test_dedup_meme_journee_countdown OK")


def test_decouverte_tardive_J5_ssl():
    """Si on decouvre le site juste a J-5, on envoie 6 daily (J-5..J-0)
    et PAS de J-30/J-15 retroactifs."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        sent = []
        for offset in range(6):
            today = date(2026, 1, 26) + timedelta(days=offset)
            days = 5 - offset
            s, at = simulate(db, "ex.com", "ssl", today.isoformat(),
                             expires_iso="2026-01-31", days_left=days)
            if s:
                sent.append(at)
        assert all(at == "daily" for at in sent)
        assert len(sent) == 6
        print("  test_decouverte_tardive_J5_ssl OK")


# --- Tests STICKY ------------------------------------------------------------

def test_down_first_puis_rappels_quotidiens():
    """Site DOWN pendant 30 jours :
    - J0   : alerte 'first'
    - J1..J29 : 1 rappel par jour
    Total : 30 alertes (1 first + 29 reminders)."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        sent = []
        for day in range(30):
            today = (date(2026, 6, 1) + timedelta(days=day)).isoformat()
            s, at = simulate(db, "ex.com", "down", today)
            if s:
                sent.append((today, at))
        print(f"DOWN sticky (quotidien) : {len(sent)} alertes")
        assert sent[0][1] == "first"
        assert all(at == "reminder" for _, at in sent[1:])
        assert len(sent) == 30, f"Attendu 30 alertes, recu {len(sent)}"
        print("  test_down_first_puis_rappels_quotidiens OK")


def test_down_dedup_meme_journee():
    """2 runs le meme jour pour un site DOWN = 1 seule alerte."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        s1, at1 = simulate(db, "ex.com", "down", "2026-06-01")
        s2, _ = simulate(db, "ex.com", "down", "2026-06-01")
        assert s1 and at1 == "first"
        assert not s2, "Deuxieme run le meme jour ne doit pas re-alerter"
        print("  test_down_dedup_meme_journee OK")


def test_down_resolution_puis_nouvelle_panne():
    """Apres resolution, la panne suivante est traitee comme 'first' (pas reminder)."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        s1, at1 = simulate(db, "ex.com", "down", "2026-06-01")
        s2, at2 = simulate(db, "ex.com", "down", "2026-06-02")
        history.mark_resolved(Path(tmp) / "t.db", "ex.com", "down")
        s3, at3 = simulate(db, "ex.com", "down", "2026-06-15")
        assert s1 and at1 == "first"
        assert s2 and at2 == "reminder", f"J+1 doit etre 'reminder', got {at2}"
        assert s3 and at3 == "first", f"Apres resolved, doit etre 'first' got {at3}"
        print("  test_down_resolution_puis_nouvelle_panne OK")


def test_backup_first_puis_resolu():
    """BACKUP : 1ere detection + rappel a J7, puis backup OK -> reset."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        s1, at1 = simulate(db, "ex.com", "backup", "2026-05-01")
        s2, at2 = simulate(db, "ex.com", "backup", "2026-05-08")
        history.mark_resolved(Path(tmp) / "t.db", "ex.com", "backup")
        s3, at3 = simulate(db, "ex.com", "backup", "2026-05-20")
        assert (s1, at1) == (True, "first")
        assert (s2, at2) == (True, "reminder")
        assert (s3, at3) == (True, "first")
        print("  test_backup_first_puis_resolu OK")


def test_backup_pas_de_rappel_avant_7j():
    """BACKUP : pas de rappel pendant les 6 jours qui suivent la 1ere alerte."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        s1, at1 = simulate(db, "ex.com", "backup", "2026-05-01")
        for offset in range(1, 7):
            today = (date(2026, 5, 1) + timedelta(days=offset)).isoformat()
            s, _ = simulate(db, "ex.com", "backup", today)
            assert not s, f"Pas de rappel attendu a J+{offset}, mais alerte envoyee"
        s8, at8 = simulate(db, "ex.com", "backup", "2026-05-08")
        assert (s1, at1) == (True, "first")
        assert (s8, at8) == (True, "reminder")
        print("  test_backup_pas_de_rappel_avant_7j OK")


# --- Test transverse ---------------------------------------------------------

def test_categories_independantes():
    """SSL et NDD pour le meme domaine sont gerees independamment."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        s_ssl, at_ssl = simulate(db, "ex.com", "ssl", "2026-01-01",
                                 expires_iso="2026-01-31", days_left=30)
        s_ndd, at_ndd = simulate(db, "ex.com", "ndd", "2026-01-01",
                                 expires_iso="2026-12-31", days_left=29)
        assert s_ssl and at_ssl == "j30"
        assert s_ndd and at_ndd == "j30"
        print("  test_categories_independantes OK")


if __name__ == "__main__":
    print("--- Countdown (SSL/NDD) ---")
    test_ssl_full_sequence()
    test_ndd_full_sequence()
    test_renouvellement_reset_etat()
    test_dedup_meme_journee_countdown()
    test_decouverte_tardive_J5_ssl()
    print("\n--- Sticky (DOWN/BACKUP) ---")
    test_down_first_puis_rappels_quotidiens()
    test_down_dedup_meme_journee()
    test_down_resolution_puis_nouvelle_panne()
    test_backup_first_puis_resolu()
    test_backup_pas_de_rappel_avant_7j()
    print("\n--- Transverse ---")
    test_categories_independantes()
    print("\n[OK] Tous les tests passent.")
