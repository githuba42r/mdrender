# server/tests/test_slave_registration.py
"""Slave onboarding: login redirect, Connect form, consent callback."""
import io
import os
import urllib.error
import urllib.parse

from server.app import federation_client, federation_worker, settings
from server.app.app import create_app


def _app(config, *, role="slave", master_url="https://master.example"):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    config.ROLE = role
    config.MASTER_URL = master_url
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _login(app):
    c = app.test_client()
    r = c.post("/admin-login", data={"username": "admin", "password": "testpass"})
    assert r.status_code == 302
    return c


def _register(app, master_url="https://master.example"):
    with app.config["_db"].connect() as conn:
        federation_client.save_registration(conn, master_url, "s3cr3t")


def _state(app):
    with app.config["_db"].connect() as conn:
        return federation_client.get_state(conn)


def _http_error(code, body):
    return urllib.error.HTTPError("https://master.example", code, "err", {},
                                  io.BytesIO(body))


# ---- where an admin lands after signing in ----

def test_unregistered_slave_login_goes_to_federation(config):
    app = _app(config)
    r = app.test_client().post("/admin-login",
                               data={"username": "admin", "password": "testpass"})
    assert r.status_code == 302
    assert r.headers["Location"] == "/federation"


def test_admin_login_route_also_goes_to_federation(config):
    app = _app(config)
    r = app.test_client().post("/admin-login",
                               data={"username": "admin", "password": "testpass"})
    assert r.status_code == 302
    assert r.headers["Location"] == "/federation"


def test_registered_slave_login_goes_to_pushes(config):
    app = _app(config)
    _register(app)
    r = app.test_client().post("/admin-login",
                               data={"username": "admin", "password": "testpass"})
    assert r.headers["Location"] == "/pushes"


def test_login_honours_an_explicit_next(config):
    """The CLI enrolment flow must bounce straight back to its own URL."""
    app = _app(config)
    r = app.test_client().post("/admin-login", data={"username": "admin",
                                               "password": "testpass",
                                               "next": "/enrol/abc"})
    assert r.headers["Location"] == "/enrol/abc"


def test_master_login_goes_to_pushes(config):
    app = _app(config, role="master", master_url="")
    r = app.test_client().post("/admin-login",
                               data={"username": "admin", "password": "testpass"})
    assert r.headers["Location"] == "/pushes"


def test_slave_without_master_url_goes_to_pushes(config):
    app = _app(config, master_url="")
    r = app.test_client().post("/admin-login",
                               data={"username": "admin", "password": "testpass"})
    assert r.headers["Location"] == "/pushes"


# ---- the Federation page on a slave ----

def test_slave_page_shows_master_and_connect_form(config):
    app = _app(config)
    c = _login(app)
    page = c.get("/federation")
    assert page.status_code == 200
    assert b"https://master.example" in page.data
    assert b"not connected" in page.data
    assert b' action="/federation/connect"' in page.data
    assert b'name="master_url"' in page.data
    assert b"Connect to a master" in page.data


def test_slave_page_hides_the_form_once_connected(config):
    app = _app(config)
    _register(app)
    c = _login(app)
    page = c.get("/federation")
    assert b"connected</span>" in page.data
    assert b'action="/federation/connect"' not in page.data


def test_master_page_still_lists_slaves(config):
    app = _app(config, role="master", master_url="")
    c = _login(app)
    page = c.get("/federation")
    assert page.status_code == 200
    assert b"No slave servers enrolled yet." in page.data
    assert b'action="/federation/connect"' not in page.data


# ---- POST /federation/connect: open the master's consent page ----

def _connect_location(response):
    assert response.status_code == 302, response.data
    location = response.headers["Location"]
    assert location.startswith(
        "https://master.example/federation/connect?"), location
    return urllib.parse.parse_qs(urllib.parse.urlparse(location).query)


def test_connect_redirects_to_the_masters_consent_page(config):
    app = _app(config)
    c = _login(app)
    query = _connect_location(
        c.post("/federation/connect", data={"master_url": "https://master.example"}))
    assert query["base_url"] == ["https://push.example.com"]
    assert query["hostname"] == [app.config["_server_identity"]["hostname"]]
    assert query["server_id"] == [app.config["_server_identity"]["server_id"]]
    assert query["state"] and query["ts"] and query["sig"]
    with app.config["_db"].connect() as conn:
        assert settings.get(conn, "federation_master_url") == \
            "https://master.example"
    assert query["state"][0] in app.config["_pending_connects"]


def test_connect_accepts_a_hostname_without_a_scheme(config):
    app = _app(config)
    c = _login(app)
    query = _connect_location(
        c.post("/federation/connect", data={"master_url": "master.example"}))
    with app.config["_db"].connect() as conn:
        assert settings.get(conn, "federation_master_url") == \
            "https://master.example"


def test_connect_rejects_a_bogus_master_url(config):
    app = _app(config)
    c = _login(app)
    r = c.post("/federation/connect", data={"master_url": "javascript:alert(1)"})
    assert r.status_code == 303
    assert r.headers["Location"].startswith("/federation?error=")
    assert _state(app) is None
    assert app.config["_pending_connects"] == {}


def test_connect_refuses_this_server_as_its_master(config):
    app = _app(config)
    c = _login(app)
    r = c.post("/federation/connect",
               data={"master_url": "https://push.example.com"})
    assert r.headers["Location"].startswith("/federation?error=")
    assert app.config["_pending_connects"] == {}


def test_connect_is_gated_on_an_admin_session(config):
    app = _app(config)
    r = app.test_client().post("/federation/connect",
                               data={"master_url": "https://master.example"})
    assert r.status_code == 303
    assert r.headers["Location"].startswith("/admin-login")
    assert app.config["_pending_connects"] == {}


def test_connect_on_a_master_is_a_noop(config):
    app = _app(config, role="master", master_url="https://master.example")
    c = _login(app)
    r = c.post("/federation/connect",
               data={"master_url": "https://master.example"})
    assert r.headers["Location"] == "/federation"
    assert app.config["_pending_connects"] == {}


# ---- GET /federation/callback: come back approved and enrol ----

def _connect(app, master_url="https://master.example"):
    """Run the first half of the handshake; return the state it minted."""
    client = _login(app)
    query = _connect_location(
        client.post("/federation/connect", data={"master_url": master_url}))
    return client, query["state"][0]


def test_callback_enrols_with_the_master(config, monkeypatch):
    app = _app(config)
    c, state = _connect(app)
    seen = {}

    def _enrol(cfg, conn, identity, *, master_url=None, code=None):
        seen.update(master_url=master_url, code=code)
        federation_client.save_registration(conn, master_url, "s3cr3t")

    monkeypatch.setattr(federation_client, "enrol", _enrol)
    r = c.get(f"/federation/callback?code=CODE123&state={state}")
    assert r.status_code == 303
    assert r.headers["Location"] == "/pushes"
    assert seen == {"master_url": "https://master.example", "code": "CODE123"}
    assert _state(app)["server_secret"] == "s3cr3t"


def test_callback_requires_an_admin_session(config):
    app = _app(config)
    _, state = _connect(app)
    r = app.test_client().get(f"/federation/callback?code=C&state={state}")
    assert r.status_code == 303
    assert r.headers["Location"].startswith("/admin-login?next=/federation"
                                             "/callback%3F")
    # The approval must survive the login round-trip: the state stays pending.
    assert state in app.config["_pending_connects"]
    assert _state(app) is None


def test_callback_rejects_an_unknown_state(config, monkeypatch):
    app = _app(config)
    c = _login(app)
    called = []
    monkeypatch.setattr(federation_client, "enrol",
                        lambda *a, **k: called.append(1))
    r = c.get("/federation/callback?code=C&state=bogus")
    assert r.status_code == 303
    assert r.headers["Location"].startswith("/federation?error=")
    assert called == []


def test_callback_is_single_use_even_for_the_same_state(config, monkeypatch):
    app = _app(config)
    c, state = _connect(app)
    called = []
    monkeypatch.setattr(federation_client, "enrol",
                        lambda *a, **k: called.append(1))
    c.get(f"/federation/callback?code=C&state={state}")
    again = c.get(f"/federation/callback?code=C&state={state}")
    assert again.headers["Location"].startswith("/federation?error=")
    assert len(called) == 1


def test_callback_without_a_code_reports_the_decline(config, monkeypatch):
    app = _app(config)
    c, state = _connect(app)
    called = []
    monkeypatch.setattr(federation_client, "enrol",
                        lambda *a, **k: called.append(1))
    r = c.get(f"/federation/callback?state={state}")
    assert r.headers["Location"].startswith("/federation?error=")
    assert called == []


def test_callback_surfaces_the_masters_reason(config, monkeypatch):
    app = _app(config)
    c, state = _connect(app)

    def _enrol(*a, **k):
        raise _http_error(403, b'{"error": "consent required"}')

    monkeypatch.setattr(federation_client, "enrol", _enrol)
    r = c.get(f"/federation/callback?code=C&state={state}")
    assert r.status_code == 303
    assert r.headers["Location"].startswith("/federation?error=")
    page = c.get(r.headers["Location"])
    assert b"consent required" in page.data
    assert _state(app) is None


def test_callback_reports_an_unreachable_master(config, monkeypatch):
    app = _app(config)
    c, state = _connect(app)

    def _enrol(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(federation_client, "enrol", _enrol)
    page = c.get(c.get(f"/federation/callback?code=C&state={state}"
                       ).headers["Location"])
    assert b"connection refused" in page.data


def test_callback_on_a_master_is_a_noop(config):
    app = _app(config, role="master", master_url="https://master.example")
    c = _login(app)
    r = c.get("/federation/callback?code=C&state=anything")
    assert r.headers["Location"] == "/federation"
    assert _state(app) is None


# ---- the worker re-enrols after the master forgets us ----

def _seeded(config, role="slave", master_url="https://master.example"):
    app = _app(config, role=role, master_url=master_url)
    _register(app, master_url)
    return app


def test_worker_clears_a_registration_the_master_rejects(config, monkeypatch):
    app = _seeded(config)
    config.ROLE = "slave"
    config.MASTER_URL = "https://master.example"

    def _hb(*a, **k):
        raise _http_error(401, b'{"error": "unauthorized"}')

    monkeypatch.setattr(federation_client, "send_heartbeat", _hb)
    federation_worker.tick(config, app.config["_db"])
    assert _state(app) is None


def test_worker_keeps_the_registration_when_the_master_is_down(config,
                                                                monkeypatch):
    app = _seeded(config)
    config.ROLE = "slave"
    config.MASTER_URL = "https://master.example"

    def _hb(*a, **k):
        raise urllib.error.URLError("master unreachable")

    monkeypatch.setattr(federation_client, "send_heartbeat", _hb)
    federation_worker.tick(config, app.config["_db"])
    assert _state(app) is not None
