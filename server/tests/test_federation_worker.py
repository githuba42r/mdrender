# server/tests/test_federation_worker.py
from server.app import federation, federation_client, federation_worker
from server.app.db import Database


def _db(db_path):
    db = Database(db_path)
    conn = db.connect()
    db.init_schema(conn)
    conn.close()
    return db


def test_master_tick_runs_liveness_sweep(config, db_path, monkeypatch):
    config.ROLE = "master"
    calls = []
    monkeypatch.setattr(federation, "sweep_liveness",
                        lambda conn, **k: calls.append(1) or [])
    federation_worker.tick(config, _db(db_path))
    assert calls == [1]


def test_slave_tick_enrols_then_heartbeats(config, db_path, monkeypatch):
    config.ROLE = "slave"
    config.MASTER_URL = "https://master.example"
    config.PUSH_PUBLIC_URL = "https://slave.example"
    events = []
    monkeypatch.setattr(federation_client, "enrol",
                        lambda config, conn, identity: events.append("enrol") or {"server_secret": "s"})
    monkeypatch.setattr(federation_client, "send_heartbeat",
                        lambda config, conn, identity, **k: events.append("hb") or {})
    federation_worker.tick(config, _db(db_path))
    assert events == ["enrol", "hb"]


def test_slave_tick_without_master_url_is_noop(config, db_path, monkeypatch):
    config.ROLE = "slave"
    config.MASTER_URL = ""
    called = []
    monkeypatch.setattr(federation_client, "enrol", lambda *a, **k: called.append(1))
    federation_worker.tick(config, _db(db_path))
    assert called == []
