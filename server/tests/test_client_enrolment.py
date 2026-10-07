# server/tests/test_client_enrolment.py
"""Account-bound CLI enrolment, revoked clients, and account-scoped targets."""
import base64
import io
import os

from server.app import accounts, auth, push_store, store
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _account(app, email):
    c = app.test_client()
    c.post("/signup", data={"email": email, "password": "longenough1"})
    c.post("/account/login", data={"email": email, "password": "longenough1"})
    with app.config["_db"].connect() as conn:
        account_id = accounts.get_account_by_email(conn, email)["account_id"]
    return c, account_id


def _device(app, name="Dev", account_id=None, content_pubkey=None):
    push_key = base64.b64encode(b"\x00" * 32).decode()
    with app.config["_db"].connect() as conn:
        conn.execute(
            "INSERT INTO devices (device_secret, device_auth, device_name, fcm_token,"
            " public_key, push_key, registered_at, last_seen, account_id, content_pubkey)"
            " VALUES (?, 'auth', ?, 'tok', ?, ?, 1, 1, ?, ?)",
            (f"sec-{name}", name, base64.b64encode(b"\x01" * 294).decode(),
             push_key, account_id, content_pubkey))
        conn.commit()


def _push(app, token, target="Dev"):
    return app.test_client().post(
        "/api/push",
        headers={"Authorization": f"Bearer {token}"},
        data={"target_device": target, "file": (io.BytesIO(b"x"), "x.txt")},
        content_type="multipart/form-data")


def _client_token(config, app, name, account_id=None):
    with app.config["_db"].connect() as conn:
        client_id = store.create_client(conn, name, auth.hash_secret("secret"),
                                        account_id=account_id)
    return auth.issue_access_token(config, client_id)


def test_account_approved_enrolment_binds_the_client(config, db_path):
    app = _app(config)
    c = app.test_client()
    c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})
    c.post("/account/login", data={"email": "user@example.com", "password": "longenough1"})

    start = app.test_client().post("/api/enrol/start", json={"name": "cli"})
    eid = start.get_json()["enrolment_id"]
    assert c.get(f"/enrol/{eid}").status_code == 200
    assert c.post(f"/enrol/{eid}/approve").status_code == 200

    with app.config["_db"].connect() as conn:
        account_id = accounts.get_account_by_email(conn, "user@example.com")["account_id"]
        rows = store.list_clients(conn, account_id)
    assert [r["name"] for r in rows] == ["cli"]


def test_revoked_client_cannot_push_even_with_a_live_token(config, db_path):
    app = _app(config)
    _device(app)
    with app.config["_db"].connect() as conn:
        client_id = store.create_client(conn, "cli", "hash")

    token = auth.issue_access_token(config, client_id)
    assert _push(app, token).status_code == 200

    # Revoke the row but leave the token in the in-memory map, i.e. the case the
    # revoked_at check exists to catch.
    with app.config["_db"].connect() as conn:
        store.revoke_client(conn, client_id)
    assert _push(app, token).status_code == 401


def test_revoked_client_cannot_fetch_a_token(config, db_path):
    app = _app(config)
    with app.config["_db"].connect() as conn:
        client_id = store.create_client(conn, "cli", auth.hash_secret("secret"))
        store.revoke_client(conn, client_id)
    resp = app.test_client().post("/oauth/token", data={
        "grant_type": "client_credentials", "client_id": client_id,
        "client_secret": "secret"})
    assert resp.status_code == 401


def test_account_client_pushes_only_to_its_own_accounts_devices(config, db_path):
    """The core scope: another account's device — and an admin-paired device
    that belongs to no account — answer exactly like a device that doesn't
    exist, so a client token learns nothing from probing names."""
    app = _app(config)
    _, acc_a = _account(app, "a@example.com")
    _, acc_b = _account(app, "b@example.com")
    _device(app, "PhoneA", account_id=acc_a)
    _device(app, "PhoneB", account_id=acc_b)
    _device(app, "AdminPhone")
    token = _client_token(config, app, "cli", account_id=acc_a)

    assert _push(app, token, "PhoneA").status_code == 200
    for foreign in ("PhoneB", "AdminPhone", "DoesNotExist"):
        r = _push(app, token, foreign)
        assert r.status_code == 400
        assert r.get_json() == {"error": "device not found"}


def test_admin_client_may_target_any_account_device(config, db_path):
    """Operator tooling — an admin-approved, account-unbound client — keeps
    the every-device scope it has always had."""
    app = _app(config)
    _, acc = _account(app, "a@example.com")
    _device(app, "PhoneA", account_id=acc)
    token = _client_token(config, app, "ops")
    assert _push(app, token, "PhoneA").status_code == 200


def test_api_devices_is_scoped_to_the_clients_account(config, db_path):
    app = _app(config)
    _, acc_a = _account(app, "a@example.com")
    _, acc_b = _account(app, "b@example.com")
    _device(app, "PhoneA", account_id=acc_a)
    _device(app, "PhoneB", account_id=acc_b)
    _device(app, "AdminPhone")
    a_token = _client_token(config, app, "a", account_id=acc_a)
    ops_token = _client_token(config, app, "ops")

    def names(token):
        r = app.test_client().get(
            "/api/devices", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        return sorted(d["name"] for d in r.get_json()["devices"])

    assert names(a_token) == ["PhoneA"]
    assert names(ops_token) == ["AdminPhone", "PhoneA", "PhoneB"]


def test_content_key_is_scoped_to_the_clients_account(config, db_path):
    """The scope check runs before the key material: a foreign device is
    "not found", never a key-shaped response or a keyless 404."""
    app = _app(config)
    _, acc_a = _account(app, "a@example.com")
    _, acc_b = _account(app, "b@example.com")
    _device(app, "PhoneA", account_id=acc_a, content_pubkey="pk-A")
    _device(app, "PhoneB", account_id=acc_b, content_pubkey="pk-B")
    a_token = _client_token(config, app, "a", account_id=acc_a)
    b_token = _client_token(config, app, "b", account_id=acc_b)

    def key(token, device):
        return app.test_client().get(
            f"/api/push/content-key?device={device}",
            headers={"Authorization": f"Bearer {token}"})

    assert key(a_token, "PhoneA").status_code == 200
    r = key(b_token, "PhoneA")
    assert r.status_code == 404
    assert r.get_json() == {"error": "device not found"}


def test_short_code_enrolment_is_bound_to_the_account_that_read_the_code(config,
                                                                        db_path):
    """The headless path: an account opens /enrol/<eid> to read the code, the
    CLI exchanges it at /api/enrol, and the minted client inherits that
    account's scope — same as the browser-approve path."""
    app = _app(config)
    c, acc_a = _account(app, "a@example.com")
    _, acc_b = _account(app, "b@example.com")
    start = app.test_client().post("/api/enrol/start", json={"name": "cli"})
    eid = start.get_json()["enrolment_id"]

    assert c.get(f"/enrol/{eid}").status_code == 200
    code = app.config["_enrol_keys"][eid]["code"]

    # The CLI side: signed out, short code in hand.
    r = app.test_client().post("/api/enrol",
                               json={"enrolment_id": eid, "code": code})
    assert r.status_code == 200
    with app.config["_db"].connect() as conn:
        assert [row["name"] for row in store.list_clients(conn, acc_a)] == ["cli"]
        assert store.list_clients(conn, acc_b) == []

    token = app.test_client().post("/oauth/token", data={
        "grant_type": "client_credentials",
        "client_id": r.get_json()["client_id"],
        "client_secret": r.get_json()["client_secret"]}).get_json()["access_token"]
    _device(app, "Mine", account_id=acc_a)
    _device(app, "Theirs", account_id=acc_b)
    assert _push(app, token, "Mine").status_code == 200
    assert _push(app, token, "Theirs").status_code == 400


def test_push_status_hides_other_accounts_pushes(config, db_path):
    """Push status carries file names: to anyone but the owning account (and
    admin tooling) it answers like an unknown push — empty files."""
    app = _app(config)
    _, acc_a = _account(app, "a@example.com")
    _, acc_b = _account(app, "b@example.com")
    _device(app, "PhoneB", account_id=acc_b)
    with app.config["_db"].connect() as conn:
        push_store.create_push(conn, "push-1", "PhoneB", challenge_key="ck")
        push_store.add_file(conn, file_id="f1", push_id="push-1",
                            file_name="secret-plan.md", file_path="", size=1,
                            retrieval_key="k", stored_path=None, created_at=1000)
        a_client = store.create_client(conn, "a", auth.hash_secret("s"),
                                       account_id=acc_a)
        b_client = store.create_client(conn, "b", auth.hash_secret("s"),
                                       account_id=acc_b)
        ops_client = store.create_client(conn, "ops", auth.hash_secret("s"))

    def status(client_id):
        token = auth.issue_access_token(config, client_id)
        r = app.test_client().get(
            "/api/push/push-1/status", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        return r.get_json()

    assert status(a_client) == {"push_id": "push-1", "files": []}
    assert [f["name"] for f in status(b_client)["files"]] == ["secret-plan.md"]
    assert [f["name"] for f in status(ops_client)["files"]] == ["secret-plan.md"]
    # An unknown push answers the same way as a foreign one.
    assert status(a_client) == {"push_id": "push-1", "files": []}
