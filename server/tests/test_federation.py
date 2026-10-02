# server/tests/test_federation.py
"""Federation enrolment, signed requests, nonces, and outbox (Phase C),
plus the operator consent handshake a slave must pass first (design §5)."""
import base64
import os
import time
import unittest.mock as mock
import urllib.parse

import pytest

from server.app import (billing, crypto, federation, federation_client,
                        settings)
from server.app.app import create_app
from conftest import consent_code


def _slave_keypair():
    priv, pub = crypto.generate_rsa_keypair()
    return priv, base64.b64encode(crypto.public_to_spki_der(pub)).decode()


@pytest.fixture()
def fed_app(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    config.ROLE = "master"  # this module exercises the master's view
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _enrol(app):
    """Enrol a slave using a fake callback signed by its own key; return
    (private_pem, bearer_token). Carries the consent code an operator would
    have been handed on /federation/connect (design §5)."""
    priv, pub_b64 = _slave_keypair()
    priv_pem = crypto.private_to_pem(priv).decode()

    def fake_callback(base_url, challenge, timeout=10):
        return federation.sign_bytes(priv_pem, challenge.encode())

    code = consent_code(app, server_id="srv-1", hostname="slave.example",
                        base_url="https://slave.example", public_key=pub_b64)
    with mock.patch.object(federation, "verify_slave_callback", fake_callback):
        resp = app.test_client().post("/api/federation/enrol", json={
            "server_id": "srv-1", "hostname": "slave.example",
            "base_url": "https://slave.example", "public_key": pub_b64,
            "code": code})
    assert resp.status_code == 200, resp.data
    secret = resp.get_json()["server_secret"]
    return priv_pem, f"srv-1.{secret}"


def test_slave_enrolment_via_signed_callback(fed_app, monkeypatch):
    priv, pub_b64 = _slave_keypair()
    priv_pem = crypto.private_to_pem(priv).decode()
    monkeypatch.setattr(federation, "verify_slave_callback",
                        lambda base_url, challenge, timeout=10:
                        federation.sign_bytes(priv_pem, challenge.encode()))

    c = fed_app.test_client()
    resp = c.post("/api/federation/enrol", json={
        "server_id": "srv-1", "hostname": "slave.example",
        "base_url": "https://slave.example", "public_key": pub_b64,
        "code": consent_code(fed_app, server_id="srv-1",
                             hostname="slave.example",
                             base_url="https://slave.example",
                             public_key=pub_b64)})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "active"

    token = f"srv-1.{body['server_secret']}"
    # Bearer alone is not enough any more: whoami is a signed request.
    who = c.get("/api/federation/whoami", headers={"Authorization": f"Bearer {token}"})
    assert who.status_code == 401
    headers = federation.sign_request(priv_pem, "GET", "/api/federation/whoami", b"")
    headers["Authorization"] = f"Bearer {token}"
    who = c.get("/api/federation/whoami", headers=headers)
    assert who.status_code == 200
    assert who.get_json()["server_id"] == "srv-1"


def test_enrol_rejects_a_bad_callback_signature(fed_app, monkeypatch):
    priv, pub_b64 = _slave_keypair()
    other, _ = _slave_keypair()
    other_pem = crypto.private_to_pem(other).decode()
    monkeypatch.setattr(federation, "verify_slave_callback",
                        lambda base_url, challenge, timeout=10:
                        federation.sign_bytes(other_pem, challenge.encode()))

    resp = fed_app.test_client().post("/api/federation/enrol", json={
        "server_id": "srv-x", "hostname": "h", "base_url": "https://h",
        "public_key": pub_b64,
        "code": consent_code(fed_app, server_id="srv-x", hostname="h",
                             base_url="https://h", public_key=pub_b64)})
    assert resp.status_code == 400


def test_heartbeat_requires_signature_and_flushes_outbox(fed_app):
    priv_pem, token = _enrol(fed_app)
    db = fed_app.config["_db"]
    with db.connect() as conn:
        federation.enqueue(conn, "srv-1", {"type": "notice", "text": "hello"})

    c = fed_app.test_client()
    # Bearer alone (unsigned) is rejected.
    assert c.post("/api/federation/heartbeat",
                  headers={"Authorization": f"Bearer {token}"}).status_code == 401

    headers = federation.sign_request(priv_pem, "POST", "/api/federation/heartbeat", b"")
    headers["Authorization"] = f"Bearer {token}"
    ok = c.post("/api/federation/heartbeat", headers=headers)
    assert ok.status_code == 200
    assert ok.get_json()["queued"] == [{"type": "notice", "text": "hello"}]

    with db.connect() as conn:
        assert federation.pending_outbox(conn, "srv-1") == []  # drained


def test_replayed_request_is_rejected(fed_app):
    priv_pem, token = _enrol(fed_app)
    c = fed_app.test_client()
    headers = federation.sign_request(priv_pem, "POST", "/api/federation/ping", b"")
    headers["Authorization"] = f"Bearer {token}"
    assert c.post("/api/federation/ping", headers=headers).status_code == 200
    # Same nonce again -> replay rejected.
    assert c.post("/api/federation/ping", headers=headers).status_code == 401


def test_sweep_marks_down_then_recovers(fed_app):
    _enrol(fed_app)
    db = fed_app.config["_db"]
    with db.connect() as conn:
        # First failure is below threshold; the second marks it down.
        assert federation.sweep_liveness(conn, probe=lambda row: False, threshold=2) == []
        assert federation.sweep_liveness(conn, probe=lambda row: False, threshold=2) == ["srv-1"]
        assert federation.get_server(conn, "srv-1")["status"] == "down"
        # A successful probe brings it back.
        assert federation.sweep_liveness(conn, probe=lambda row: True) == []
        assert federation.get_server(conn, "srv-1")["status"] == "active"


def test_federation_admin_lists_and_manages_servers(fed_app):
    _enrol(fed_app)
    c = fed_app.test_client()
    assert c.get("/federation").status_code == 303  # session-gated
    c.post("/admin-login", data={"username": "admin", "password": "testpass"})

    body = c.get("/federation").data
    assert b"srv-1" in body and b"slave.example" in body

    db = fed_app.config["_db"]
    c.post("/federation/srv-1/deactivate")
    with db.connect() as conn:
        assert federation.get_server(conn, "srv-1")["status"] == "deactivated"
    c.post("/federation/srv-1/activate")
    with db.connect() as conn:
        assert federation.get_server(conn, "srv-1")["status"] == "active"
    c.post("/federation/srv-1/revoke")
    with db.connect() as conn:
        assert federation.get_server(conn, "srv-1")["status"] == "revoked"
    c.post("/federation/srv-1/delete")
    with db.connect() as conn:
        assert federation.get_server(conn, "srv-1") is None


def test_slave_verify_responder_signs_the_challenge(fed_app):
    body = fed_app.test_client().post("/api/federation/verify",
                                      json={"challenge": "abc123"}).get_json()
    ident = fed_app.config["_server_identity"]
    assert body["server_id"] == ident["server_id"]
    pub = crypto.public_from_spki_der(base64.b64decode(ident["public_key"]))
    assert crypto.verify(pub, b"abc123", base64.b64decode(body["signature"]))


def test_probe_responder_reports_status(fed_app):
    body = fed_app.test_client().post("/api/federation/probe",
                                      json={"challenge": "c"}).get_json()
    assert body["status"] == fed_app.config["_server_role"]


def test_master_to_slave_calls_send_a_user_agent(monkeypatch):
    """Cloudflare answers error 1010 to urllib's default Python-urllib UA, so
    the master's verify callback and liveness probe need a real one too."""
    seen = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"signature": "c2ln"}'

    def _fake_urlopen(req, timeout=None):
        seen.append(req.get_header("User-agent"))
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert federation.verify_slave_callback("https://slave.example", "c1") == "c2ln"
    assert federation.probe_server({"base_url": "https://slave.example",
                                    "public_key": "bogus"}) is False
    assert seen == [federation.USER_AGENT, federation.USER_AGENT]


# ---- Operator consent handshake (design §5) --------------------------------

def _signed_consent_query(*, server_id="srv-1", hostname="slave.example",
                          base_url="https://slave.example", state="st",
                          ts=None, priv_pem=None, pub_b64=None):
    """The query a slave's browser is redirected with: its signed request."""
    if priv_pem is None:
        priv, pub_b64 = _slave_keypair()
        priv_pem = crypto.private_to_pem(priv).decode()
    ts = int(time.time()) if ts is None else int(ts)
    sig = federation.sign_bytes(priv_pem, federation.connect_canonical(
        server_id, hostname, base_url, pub_b64, state, ts))
    query = {"server_id": server_id, "hostname": hostname,
             "base_url": base_url, "public_key": pub_b64, "state": state,
             "ts": str(ts), "sig": sig}
    return query, priv_pem, pub_b64


def _consent_page(fed_app, **kwargs):
    query, priv_pem, pub_b64 = _signed_consent_query(**kwargs)
    resp = fed_app.test_client().get(
        "/federation/connect?" + urllib.parse.urlencode(query))
    return resp, priv_pem, pub_b64


def _connect_row(fed_app):
    with fed_app.config["_db"].connect() as conn:
        row = conn.execute(
            "SELECT * FROM federation_connects ORDER BY created_at DESC, rowid"
            " DESC").fetchone()
    return dict(row) if row else None


def _enrol_signed(fed_app, priv_pem, pub_b64, *, server_id="srv-1",
                  hostname="slave.example", base_url="https://slave.example",
                  code=None):
    payload = {"server_id": server_id, "hostname": hostname,
               "base_url": base_url, "public_key": pub_b64}
    if code is not None:
        payload["code"] = code
    with mock.patch.object(federation, "verify_slave_callback",
                           lambda base_url, challenge, timeout=10:
                           federation.sign_bytes(priv_pem, challenge.encode())):
        return fed_app.test_client().post("/api/federation/enrol",
                                          json=payload)


def test_consent_page_shows_the_requester_and_the_terms(fed_app):
    """The approval page is public: it only reads what approval would grant."""
    resp, _, _ = _consent_page(fed_app)
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "slave.example" in body
    assert "https://slave.example" in body
    assert "Approving connects this server to the master" in body
    assert 'action="/federation/connect/' in body
    assert "/approve" in body and "/decline" in body
    assert _connect_row(fed_app) is not None


def test_consent_page_rejects_a_bad_signature(fed_app):
    query, _, _ = _signed_consent_query()
    query["sig"] = query["sig"][:-4] + ("AAAA" if not query["sig"].endswith("AAAA")
                                        else "BBBB")
    resp = fed_app.test_client().get(
        "/federation/connect?" + urllib.parse.urlencode(query))
    assert resp.status_code == 403


def test_consent_page_rejects_a_stale_timestamp(fed_app):
    resp, _, _ = _consent_page(fed_app, ts=int(time.time()) - 3600)
    assert resp.status_code == 410


def test_consent_page_refuses_a_self_connection(fed_app):
    resp, _, _ = _consent_page(fed_app,
                               base_url="https://push.example.com")
    assert resp.status_code == 400
    assert _connect_row(fed_app) is None


def test_consent_page_is_off_when_the_master_stops_accepting(fed_app):
    with fed_app.config["_db"].connect() as conn:
        settings.set_value(conn, "accept_new_slaves", "off")
    resp, _, _ = _consent_page(fed_app)
    assert resp.status_code == 403


def test_approve_sends_the_slave_back_with_a_single_use_code(fed_app):
    _, _, _ = _consent_page(fed_app, state="st-9")
    row = _connect_row(fed_app)
    resp = fed_app.test_client().post(
        f"/federation/connect/{row['id']}/approve")
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert location.startswith("https://slave.example/federation/callback?")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    assert query["state"] == ["st-9"]
    assert query["code"][0]


def test_decline_leaves_nothing_to_enrol_with(fed_app):
    _consent_page(fed_app)
    row = _connect_row(fed_app)
    resp = fed_app.test_client().post(f"/federation/connect/{row['id']}/decline")
    assert resp.status_code == 200
    assert fed_app.test_client().post(
        f"/federation/connect/{row['id']}/approve").status_code == 410
    pub = row["public_key"]
    with fed_app.config["_db"].connect() as conn:
        assert federation.find_connect_by_code(conn, "anything") is None
    assert pub  # the declined row itself is retained for the audit trail


def test_enrol_without_consent_is_rejected(fed_app):
    priv, pub_b64 = _slave_keypair()
    priv_pem = crypto.private_to_pem(priv).decode()
    resp = _enrol_signed(fed_app, priv_pem, pub_b64)
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "consent required"


def test_a_consent_code_is_bound_to_the_slave_it_was_issued_for(fed_app):
    priv, pub_b64 = _slave_keypair()
    priv_pem = crypto.private_to_pem(priv).decode()
    code = consent_code(fed_app, server_id="srv-1",
                        hostname="slave.example",
                        base_url="https://slave.example", public_key=pub_b64)
    # Same code, different slave -> refused.
    other, other_pub = _slave_keypair()
    other_pem = crypto.private_to_pem(other).decode()
    resp = _enrol_signed(fed_app, other_pem, other_pub,
                         server_id="srv-2", base_url="https://other",
                         code=code)
    assert resp.status_code == 403


def test_a_consent_code_can_only_be_used_once(fed_app):
    priv, pub_b64 = _slave_keypair()
    priv_pem = crypto.private_to_pem(priv).decode()
    code = consent_code(fed_app, server_id="srv-1",
                        hostname="slave.example",
                        base_url="https://slave.example", public_key=pub_b64)
    assert _enrol_signed(fed_app, priv_pem, pub_b64,
                         code=code).status_code == 200
    # Revoking forces a fresh approval, and the old code is burned.
    with fed_app.config["_db"].connect() as conn:
        federation.revoke_server(conn, "srv-1")
    assert _enrol_signed(fed_app, priv_pem, pub_b64,
                         code=code).status_code == 403


def test_an_expired_consent_code_is_refused(fed_app):
    priv, pub_b64 = _slave_keypair()
    priv_pem = crypto.private_to_pem(priv).decode()
    code = consent_code(fed_app, server_id="srv-1",
                        hostname="slave.example",
                        base_url="https://slave.example", public_key=pub_b64)
    with fed_app.config["_db"].connect() as conn:
        conn.execute("UPDATE federation_connects SET expires_at = 1"
                     " WHERE code = ?", (code,))
        conn.commit()
    assert _enrol_signed(fed_app, priv_pem, pub_b64,
                         code=code).status_code == 403


def test_a_known_slave_may_re_enrol_without_a_code(fed_app):
    """Heartbeat/restart recovery: consent already given for this exact
    identity (server_id + base URL + key) does not need a second approval."""
    priv, pub_b64 = _slave_keypair()
    priv_pem = crypto.private_to_pem(priv).decode()
    code = consent_code(fed_app, server_id="srv-1",
                        hostname="slave.example",
                        base_url="https://slave.example", public_key=pub_b64)
    assert _enrol_signed(fed_app, priv_pem, pub_b64,
                         code=code).status_code == 200
    assert _enrol_signed(fed_app, priv_pem, pub_b64).status_code == 200

    # A changed identity (same id, new URL) is a new slave as far as consent
    # is concerned.
    assert _enrol_signed(fed_app, priv_pem, pub_b64,
                         base_url="https://moved.example").status_code == 403


def test_federation_list_shows_the_plan_column_and_edits_it(fed_app):
    _enrol(fed_app)
    c = fed_app.test_client()
    c.post("/admin-login", data={"username": "admin", "password": "testpass"})
    db = fed_app.config["_db"]
    with db.connect() as conn:
        host_plan = billing.create_plan(conn, "Host", billing.SCOPE_SLAVE,
                                        price_cents=2000)
        billing.create_plan(conn, "Starter", billing.SCOPE_ACCOUNT, price_cents=500)

    # The list carries a Plan column and the edit dialog (server plans only).
    body = c.get("/federation").data
    assert b"<th>Plan</th>" in body
    assert b"server-plan-dialog" in body and b"data-edit-server-plan" in body
    assert b"Host" in body and b"Starter" not in body

    # Assign it; the row then shows the plan.
    resp = c.post("/federation/srv-1/plan", data={"plan_id": host_plan})
    assert resp.status_code == 303 and resp.headers["Location"] == "/federation"
    with db.connect() as conn:
        assert federation.get_server(conn, "srv-1")["plan_id"] == host_plan
    assert b"Host" in c.get("/federation").data

    # An account plan is refused and changes nothing.
    with db.connect() as conn:
        starter = billing.list_plans(conn)
        starter = next(p for p in starter if p["scope"] == "account")
    resp = c.post("/federation/srv-1/plan", data={"plan_id": starter["plan_id"]})
    assert "error=" in resp.headers["Location"]
    with db.connect() as conn:
        assert federation.get_server(conn, "srv-1")["plan_id"] == host_plan

    # Clearing the selection hands the server back to the default.
    c.post("/federation/srv-1/plan", data={"plan_id": ""})
    with db.connect() as conn:
        assert federation.get_server(conn, "srv-1")["plan_id"] is None


def test_federation_list_marks_the_default_plan(fed_app):
    """A server with no plan of its own is listed under the Settings default,
    with the info icon opening that plan's details."""
    _enrol(fed_app)
    c = fed_app.test_client()
    c.post("/admin-login", data={"username": "admin", "password": "testpass"})
    with fed_app.config["_db"].connect() as conn:
        plan_id = billing.create_plan(conn, "Host", billing.SCOPE_SLAVE)
        settings.set_value(conn, "default_server_plan_id", plan_id)

    body = c.get("/federation").data.decode()
    assert "Host" in body and "(default)" in body
    assert 'data-plan-details' in body
    assert 'data-plan-name="Host"' in body
    assert 'id="plan-details-dialog"' in body


def test_federation_list_uses_icon_action_buttons_with_tooltips(fed_app):
    """Deactivate/Activate/Revoke/Remove are icons with titles, not text."""
    _enrol(fed_app)
    c = fed_app.test_client()
    c.post("/admin-login", data={"username": "admin", "password": "testpass"})
    with fed_app.config["_db"].connect() as conn:
        plan_id = billing.create_plan(conn, "Host", billing.SCOPE_SLAVE)
        settings.set_value(conn, "default_server_plan_id", plan_id)
    body = c.get("/federation").data.decode()
    for text in (">Deactivate</button>", ">Revoke</button>", ">Remove</button>"):
        assert text not in body
    for tip in ('title="Deactivate"', 'title="Revoke"', 'title="Remove"',
                'title="Edit plan"', 'title="Plan details"'):
        assert tip in body
    # Storage/pending/expiry rows are tagged so the popup hides them for
    # server (host) plans (4 <dt> + 4 <dd>).
    assert body.count("pd-hide-for-server") == 8


def test_re_enrolment_keeps_the_assigned_plan(fed_app):
    """register_active is INSERT OR REPLACE - an assigned plan must survive
    a slave's re-enrolment (fresh consent, fresh key)."""
    _enrol(fed_app)
    c = fed_app.test_client()
    c.post("/admin-login", data={"username": "admin", "password": "testpass"})
    with fed_app.config["_db"].connect() as conn:
        plan_id = billing.create_plan(conn, "Host", billing.SCOPE_SLAVE)
    c.post("/federation/srv-1/plan", data={"plan_id": plan_id})

    assert _enrol(fed_app)[1].startswith("srv-1.")  # re-enrol, new key
    with fed_app.config["_db"].connect() as conn:
        assert federation.get_server(conn, "srv-1")["plan_id"] == plan_id


def test_whoami_reports_the_billing_plan(fed_app):
    """Signed whoami carries the plan the master has for this server."""
    priv_pem, token = _enrol(fed_app)
    c = fed_app.test_client()
    c.post("/admin-login", data={"username": "admin", "password": "testpass"})
    db = fed_app.config["_db"]

    def whoami():
        headers = federation.sign_request(priv_pem, "GET",
                                          "/api/federation/whoami", b"")
        headers["Authorization"] = f"Bearer {token}"
        return c.get("/api/federation/whoami", headers=headers)

    # No plan assigned and no default configured.
    assert whoami().get_json()["plan"] is None

    with db.connect() as conn:
        default_id = billing.create_plan(conn, "Default Host",
                                         billing.SCOPE_SLAVE,
                                         price_cents=1000, included_messages=1000)
        settings.set_value(conn, "default_server_plan_id", default_id)
    plan = whoami().get_json()["plan"]
    assert plan["name"] == "Default Host" and plan["is_default"] is True
    assert plan["price_cents"] == 1000 and plan["included_messages"] == 1000

    with db.connect() as conn:
        own_id = billing.create_plan(conn, "Big Host", billing.SCOPE_SLAVE,
                                     price_cents=5000, included_messages=50000)
    c.post("/federation/srv-1/plan", data={"plan_id": own_id})
    plan = whoami().get_json()["plan"]
    assert plan["name"] == "Big Host" and plan["is_default"] is False


def test_slave_may_disconnect_itself(fed_app):
    """Signed POST /api/federation/disconnect removes the row and its outbox."""
    priv_pem, token = _enrol(fed_app)
    db = fed_app.config["_db"]
    with db.connect() as conn:
        federation.enqueue(conn, "srv-1", {"type": "notice", "text": "queued"})
    c = fed_app.test_client()

    # Unsigned is rejected.
    assert c.post("/api/federation/disconnect",
                  headers={"Authorization": f"Bearer {token}"}).status_code == 401
    headers = federation.sign_request(priv_pem, "POST",
                                      "/api/federation/disconnect", b"")
    headers["Authorization"] = f"Bearer {token}"
    assert c.post("/api/federation/disconnect", headers=headers).status_code == 200
    with db.connect() as conn:
        assert federation.get_server(conn, "srv-1") is None
        assert conn.execute("SELECT count(*) FROM federated_outbox"
                            ).fetchone()[0] == 0
    # The old credentials died with the row.
    assert c.post("/api/federation/disconnect", headers=headers).status_code == 401


@pytest.fixture()
def slave_app(config, db_path):
    """A slave-role server already registered with an (unreachable) master."""
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    config.ROLE = "slave"
    config.MASTER_URL = "https://master.example"
    app = create_app(config)
    app.config["TESTING"] = True
    with app.config["_db"].connect() as conn:
        federation_client.save_registration(conn, "https://master.example",
                                            "s3cr3t")
    return app


def _login(app):
    c = app.test_client()
    c.post("/admin-login", data={"username": "admin", "password": "testpass"})
    return c


def _canned_plan(**over):
    plan = {"id": "pl-1", "name": "Host", "price_cents": 2000,
            "included_messages": 10000, "message_cents_per_1000": 1,
            "max_messages_per_month": 0, "active": True, "is_default": True}
    plan.update(over)
    return plan


def test_slave_page_shows_the_master_plan(slave_app, monkeypatch):
    monkeypatch.setattr(
        federation_client, "fetch_plan",
        lambda config, conn, identity, timeout=4:
            {"server_id": "srv-1", "plan": _canned_plan()})
    c = _login(slave_app)
    body = c.get("/federation").data.decode()
    assert "<th>Billing plan</th>" in body
    assert "Host" in body and "$20/mo" in body
    assert "10000 messages included" in body
    # 0 means unlimited, so the row says so rather than showing a zero cap.
    assert "unlimited messages" in body
    assert "(default)" in body
    assert "Disconnect from master" in body
    # Whitespace between the master table and the disconnect button.
    assert 'class="actions-end"' in body

    # A capped plan shows the cap instead.
    monkeypatch.setattr(
        federation_client, "fetch_plan",
        lambda config, conn, identity, timeout=4:
            {"server_id": "srv-1",
             "plan": _canned_plan(max_messages_per_month=100000)})
    body = c.get("/federation").data.decode()
    assert "100000 messages max" in body
    assert "unlimited messages" not in body


def test_slave_page_says_unavailable_when_the_master_is_down(slave_app,
                                                             monkeypatch):
    def _down(config, conn, identity, timeout=4):
        raise OSError("master unreachable")
    monkeypatch.setattr(federation_client, "fetch_plan", _down)
    body = _login(slave_app).get("/federation").data.decode()
    assert "unavailable (master unreachable)" in body


def test_slave_page_without_a_master_fetches_nothing(slave_app, monkeypatch):
    with slave_app.config["_db"].connect() as conn:
        federation_client.clear(conn)
    fetched = []
    monkeypatch.setattr(federation_client, "fetch_plan",
                        lambda *a, **k: fetched.append(1) or {})
    body = _login(slave_app).get("/federation").data.decode()
    assert fetched == []
    assert "Disconnect from master" not in body


def test_slave_disconnect_clears_the_registration(slave_app, monkeypatch):
    notified = []
    monkeypatch.setattr(federation_client, "_notify_disconnect",
                        lambda conn, identity: notified.append(1))
    c = _login(slave_app)

    resp = c.post("/federation/disconnect")
    assert resp.status_code == 303
    assert "disconnected=1" in resp.headers["Location"]
    assert "notified=1" in resp.headers["Location"]
    assert notified == [1]
    with slave_app.config["_db"].connect() as conn:
        assert federation_client.get_state(conn) is None

    page = c.get(resp.headers["Location"]).data.decode()
    assert "Disconnected from" in page
    assert "Connect to a master" in page


def test_slave_disconnect_clears_locally_without_the_master(slave_app,
                                                            monkeypatch):
    def _down(conn, identity):
        raise OSError("master unreachable")
    monkeypatch.setattr(federation_client, "_notify_disconnect", _down)
    c = _login(slave_app)

    resp = c.post("/federation/disconnect")
    assert resp.status_code == 303
    assert "notified=0" in resp.headers["Location"]
    with slave_app.config["_db"].connect() as conn:
        assert federation_client.get_state(conn) is None

    page = c.get(resp.headers["Location"]).data.decode()
    assert "could not be reached" in page
    assert "Connect to a master" in page
