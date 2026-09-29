# server/tests/test_foundations.py
"""Storage/quotas (F), bans (G), billing (H) and content keys (J) data layers."""
from server.app import bans, billing, encryption, storage
from server.app.db import Database


def _conn(db_path):
    db = Database(db_path)
    conn = db.connect()
    db.init_schema(conn)
    return conn


# ---- F: storage + quotas ----------------------------------------------------

def test_quota_defaults_overrides_and_usage(config, db_path):
    conn = _conn(db_path)
    assert storage.effective_quota(conn, "a", config)["max_files"] == config.ACCOUNT_MAX_FILES
    storage.set_quota(conn, "a", max_files=2, max_age_hours=1)
    q = storage.effective_quota(conn, "a", config)
    assert q["max_files"] == 2 and q["max_age_hours"] == 1
    assert q["max_bytes"] == config.ACCOUNT_MAX_BYTES  # falls back to default
    storage.add_file(conn, file_id="f1", account_id="a", size=10, stored_path="/tmp/x")
    assert storage.usage(conn, "a") == {"bytes": 10, "files": 1}
    storage.add_file(conn, file_id="f2", account_id="a", size=1, stored_path="/tmp/y")
    assert storage.can_store(conn, "a", 1, config) is False  # max_files=2 reached
    conn.close()


def test_purge_expired_removes_old_files(config, db_path, tmp_path):
    conn = _conn(db_path)
    p = tmp_path / "old.bin"
    p.write_bytes(b"x")
    storage.set_quota(conn, "a", max_age_hours=1)
    storage.add_file(conn, file_id="old", account_id="a", size=1,
                     stored_path=str(p), created_at=1)
    storage.add_file(conn, file_id="new", account_id="a", size=1, stored_path=str(p))
    removed = storage.purge_expired(conn, config)
    assert removed == ["old"]
    assert not p.exists()
    assert storage.usage(conn, "a")["files"] == 1
    conn.close()


# ---- G: bans ----------------------------------------------------------------

def test_bans_match_ip_cidr_asn_hostname_domain(config, db_path):
    conn = _conn(db_path)
    bans.add_ban(conn, "ip", "1.2.3.4")
    bans.add_ban(conn, "cidr", "10.0.0.0/8")
    bans.add_ban(conn, "asn", "64500")
    bans.add_ban(conn, "domain", "spam.example")

    assert bans.is_banned(conn, ip="1.2.3.4")
    assert bans.is_banned(conn, ip="10.5.6.7")           # inside the CIDR
    assert not bans.is_banned(conn, ip="192.168.1.1")
    assert bans.is_banned(conn, asn="AS64500")           # AS prefix tolerated
    assert bans.is_banned(conn, hostname="mail.spam.example")
    assert bans.is_banned(conn, domain="spam.example")
    assert not bans.is_banned(conn, hostname="good.example")

    bans.add_ban(conn, "ip", "9.9.9.9", expires_at=1)
    assert not bans.is_banned(conn, ip="9.9.9.9", now=1000)  # expired
    conn.close()


# ---- H: billing -------------------------------------------------------------

def test_effective_plan_precedence_and_credit(config, db_path):
    conn = _conn(db_path)
    default = billing.ensure_default_group(conn)
    base = billing.create_plan(conn, "Base", billing.SCOPE_ACCOUNT, price_cents=200)
    group_plan = billing.create_plan(conn, "Group", billing.SCOPE_ACCOUNT)
    acct_plan = billing.create_plan(conn, "Acct", billing.SCOPE_ACCOUNT)
    billing.set_group_plan(conn, default, base)

    # No group -> the default group's plan.
    assert billing.effective_plan(conn, "account", "a1")["plan_id"] == base
    # A group overrides the default.
    grp = billing.create_group(conn, "G", plan_id=group_plan)
    billing.assign_account_group(conn, "account", "a1", grp)
    assert billing.effective_plan(conn, "account", "a1")["plan_id"] == group_plan
    # An account plan overrides its group.
    billing.set_account_plan(conn, "account", "a1", acct_plan)
    assert billing.effective_plan(conn, "account", "a1")["plan_id"] == acct_plan

    assert billing.add_credit(conn, "account", "a1", 500, reason="manual") == 500
    assert billing.add_credit(conn, "account", "a1", -100, reason="usage") == 400
    assert billing.balance(conn, "account", "a1") == 400
    conn.close()


# ---- J: content keys --------------------------------------------------------

def test_account_public_key_and_sealed_cek(config, db_path):
    conn = _conn(db_path)
    encryption.set_account_public_key(conn, "a1", "PUBKEY")
    assert encryption.get_account_public_key(conn, "a1") == "PUBKEY"
    encryption.set_sealed_cek(conn, "dev1", "SEALED1")
    row = encryption.get_sealed_cek(conn, "dev1")
    assert row["sealed_cek"] == "SEALED1" and row["alg"] == "rsa-oaep-sha256"
    encryption.set_sealed_cek(conn, "dev1", "SEALED2")  # rotation upserts
    assert encryption.get_sealed_cek(conn, "dev1")["sealed_cek"] == "SEALED2"
    conn.close()
