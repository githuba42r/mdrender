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


def test_slave_tick_never_enrols_by_itself(config, db_path, monkeypatch):
    """A configured-but-unregistered slave stays untouched (design §5).

    Connecting is an operator action from /federation - the worker must not
    turn a clean first boot into an enrolment on its own.
    """
    config.ROLE = "slave"
    config.MASTER_URL = "https://master.example"
    config.PUSH_PUBLIC_URL = "https://slave.example"
    called = []
    monkeypatch.setattr(federation_client, "enrol",
                        lambda *a, **k: called.append("enrol"))
    monkeypatch.setattr(federation_client, "send_heartbeat",
                        lambda *a, **k: called.append("hb"))
    db = _db(db_path)
    federation_worker.tick(config, db)
    assert called == []
    with db.connect() as conn:
        assert federation_client.get_state(conn) is None


def test_slave_tick_heartbeats_once_registered(config, db_path, monkeypatch):
    """Once consented and enrolled, the worker only heartbeats."""
    config.ROLE = "slave"
    config.MASTER_URL = "https://master.example"
    config.PUSH_PUBLIC_URL = "https://slave.example"
    db = _db(db_path)
    with db.connect() as conn:
        federation_client.save_registration(conn, "https://master.example",
                                            "secret")
    events = []
    monkeypatch.setattr(federation_client, "enrol",
                        lambda *a, **k: events.append("enrol"))
    monkeypatch.setattr(federation_client, "send_heartbeat",
                        lambda *a, **k: events.append("hb") or {})
    federation_worker.tick(config, db)
    assert events == ["hb"]


def test_slave_tick_without_master_url_is_noop(config, db_path, monkeypatch):
    config.ROLE = "slave"
    config.MASTER_URL = ""
    called = []
    monkeypatch.setattr(federation_client, "enrol", lambda *a, **k: called.append(1))
    federation_worker.tick(config, _db(db_path))
    assert called == []
