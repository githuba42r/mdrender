# server/tests/test_geoip.py
"""DB-IP Lite ASN/country lookups and country bans (Phase G / D9)."""
import os

from server.app import bans, geoip
from server.app.app import create_app
from server.app.db import Database


def test_country_from_headers():
    assert geoip.country_from_headers({"CF-IPCountry": "au"}) == "AU"
    assert geoip.country_from_headers({"CF-IPCountry": "XX"}) is None
    assert geoip.country_from_headers({}) is None


def test_lookups_return_none_without_a_database(config):
    config.GEOIP_ASN_DB = ""
    config.GEOIP_COUNTRY_DB = ""
    assert geoip.asn_for_ip(config, "1.2.3.4") is None
    assert geoip.country_for_ip(config, "1.2.3.4") is None


def test_lookups_use_the_mmdb_reader(config, monkeypatch):
    class FakeReader:
        def get(self, ip):
            return {"autonomous_system_number": 64500, "country": {"iso_code": "AU"}}

    monkeypatch.setattr(geoip, "_open", lambda path: FakeReader())
    config.GEOIP_ASN_DB = "asn.mmdb"
    config.GEOIP_COUNTRY_DB = "country.mmdb"
    assert geoip.asn_for_ip(config, "1.2.3.4") == 64500
    assert geoip.country_for_ip(config, "1.2.3.4") == "AU"


def test_country_ban_matching(config, db_path):
    db = Database(db_path)
    conn = db.connect()
    db.init_schema(conn)
    bans.add_ban(conn, "country", "RU")
    assert bans.is_banned(conn, country="ru")
    assert not bans.is_banned(conn, country="AU")
    conn.close()


def test_asn_ban_enforced_at_the_edge(config, db_path, monkeypatch):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    with app.config["_db"].connect() as conn:
        bans.add_ban(conn, "asn", "64500")
    monkeypatch.setattr(geoip, "asn_for_ip", lambda config, ip: 64500)
    assert app.test_client().get("/api/health").status_code == 403
