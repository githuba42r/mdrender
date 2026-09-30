# server/tests/test_account_portal_pages.py
"""Account portal pages: devices, clients, profile, pushes, pending."""
import os

from server.app import accounts, push_store, store
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _account(app, email="user@example.com"):
    c = app.test_client()
    c.post("/signup", data={"email": email, "password": "longenough1"})
    c.post("/account/login", data={"email": email, "password": "longenough1"})
    with app.config["_db"].connect() as conn:
        return c, accounts.get_account_by_email(conn, email)["account_id"]


def _seed_device(app, account_id, secret="dev-1", name="Clever Juniper", approved=1,
                 model=None):
    with app.config["_db"].connect() as conn:
        conn.execute(
            "INSERT INTO devices (device_secret, device_auth, device_name, device_model,"
            " fcm_token, public_key, push_key, registered_at, last_seen, account_id,"
            " approved_at) VALUES (?, 'auth', ?, ?, 'tok', 'PUB', 'PUSH', 1, 1, ?, ?)",
            (secret, name, model, account_id, approved))
        conn.commit()


def _seed_push(app, account_id, push_id="push-1"):
    with app.config["_db"].connect() as conn:
        push_store.create_push(conn, push_id, "dev-1", account_id=account_id)
        push_store.add_file(conn, file_id=f"{push_id}-f1", push_id=push_id,
                            file_name="a.pdf", file_path="", size=3, retrieval_key="rk",
                            stored_path=None, created_at=1)


def test_devices_page_and_revoke(config, db_path):
    app = _app(config)
    c, account_id = _account(app)
    _seed_device(app, account_id)
    page = c.get("/account/devices")
    assert page.status_code == 200 and b"Clever Juniper" in page.data

    assert c.post("/account/devices/dev-1/revoke").status_code == 303
    with app.config["_db"].connect() as conn:
        assert conn.execute("SELECT 1 FROM devices WHERE device_secret='dev-1'").fetchone() is None


def test_clients_page_and_revoke(config, db_path):
    app = _app(config)
    c, account_id = _account(app)
    with app.config["_db"].connect() as conn:
        cid = store.create_client(conn, "laptop", "hash", account_id=account_id)
    page = c.get("/account/clients")
    assert page.status_code == 200 and b"laptop" in page.data

    assert c.post(f"/account/clients/{cid}/revoke").status_code == 303
    with app.config["_db"].connect() as conn:
        assert store.get_client(conn, cid)["revoked_at"] is not None


def test_profile_updates_name_email_phone(config, db_path):
    app = _app(config)
    c, account_id = _account(app)
    assert b"Account info" in c.get("/account/profile").data
    resp = c.post("/account/profile", data={
        "name": "Sam", "email": "sam@example.com", "phone": "+61400000000"})
    assert resp.status_code == 303
    with app.config["_db"].connect() as conn:
        acc = accounts.get_account(conn, account_id)
    assert acc["name"] == "Sam" and acc["email"] == "sam@example.com"
    assert acc["phone"] == "+61400000000"


def test_profile_rejects_an_admin_email(config, db_path):
    from server.app import identity

    app = _app(config)
    c, _ = _account(app)
    with app.config["_db"].connect() as conn:
        identity.create_admin(conn, "boss", "longenough1", email="boss@example.com")
    resp = c.post("/account/profile", data={
        "name": "x", "email": "boss@example.com", "phone": ""})
    assert resp.status_code == 400


def test_pushes_and_pending_are_account_scoped(config, db_path):
    app = _app(config)
    c, account_id = _account(app)
    _seed_push(app, account_id)

    # Another account's push is invisible.
    c2, other_id = _account(app, "other@example.com")
    _seed_push(app, other_id, push_id="other-push")

    pushes = c.get("/account/pushes")
    assert b"push-1" in pushes.data and b"other-push" not in pushes.data
    pending = c.get("/account/pending")
    assert b"push-1" in pending.data and b"other-push" not in pending.data

    # A different account cannot delete someone else's push.
    assert c2.post("/account/pending/push-1/delete").status_code == 303
    with app.config["_db"].connect() as conn:
        assert push_store.get_push_by_id(conn, "push-1") is not None

    # The owner can.
    assert c.post("/account/pending/push-1/delete").status_code == 303
    with app.config["_db"].connect() as conn:
        assert push_store.get_push_by_id(conn, "push-1") is None


class _FakeFcm:
    def __init__(self):
        self.sent = []

    def send(self, data, token, **kwargs):
        self.sent.append((data, token, kwargs))


def test_removing_a_device_notifies_the_phone(config, db_path):
    app = _app(config)
    c, account_id = _account(app)
    _seed_device(app, account_id)  # fcm_token = 'tok'
    fake = _FakeFcm()
    app.config["_fcm"] = fake

    assert c.post("/account/devices/dev-1/revoke").status_code == 303
    assert fake.sent, "expected an FCM unpaired nudge"
    data, token, kwargs = fake.sent[0]
    assert data == {"type": "unpaired"} and token == "tok" and kwargs["high_priority"]


def test_paired_success_banner_shows_name_and_model(config, db_path):
    app = _app(config)
    c, account_id = _account(app)
    _seed_device(app, account_id, name="Clever Juniper", model="SM-S931B")
    page = c.get("/account/devices?paired=1").data
    assert b"paired successfully" in page
    assert b"Clever Juniper" in page and b"SM-S931B" in page


def test_dashboard_shows_stats(config, db_path):
    app = _app(config)
    c, account_id = _account(app)
    _seed_push(app, account_id)
    page = c.get("/account")
    assert page.status_code == 200
    # Last login recorded at sign-in, and the seeded push counts as a message/file.
    assert b"Last login" in page.data
    assert b"Messages this week" in page.data and b"Pending files" in page.data
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["last_login_at"] is not None
    # The dashboard is stats only — no in-page storage/device tables.
    assert b"Files sent this month" in page.data
    assert b"Storage quota" in page.data


def test_account_menu_is_rendered(config, db_path):
    app = _app(config)
    c, _ = _account(app)
    body = c.get("/account").data
    for link in (b"/account/pair", b"/account/devices", b"/account/clients",
                 b"/account/profile", b"/account/pushes", b"/account/pending"):
        assert link in body


def test_account_purge_all_is_scoped_to_the_account(config, db_path):
    app = _app(config)
    c, account_id = _account(app)
    _seed_push(app, account_id)
    _c2, other_id = _account(app, "other@example.com")
    _seed_push(app, other_id, push_id="other-push")
    # A push directory on disk, to prove the bytes are cleaned up too.
    os.makedirs(os.path.join(config.PUSH_STORAGE_DIR, "push-1"), exist_ok=True)

    assert c.post("/account/pushes/purge").status_code == 303
    with app.config["_db"].connect() as conn:
        assert push_store.get_push_by_id(conn, "push-1") is None
        assert push_store.get_push_by_id(conn, "other-push") is not None
    assert not os.path.exists(os.path.join(config.PUSH_STORAGE_DIR, "push-1"))


def test_account_can_purge_pending(config, db_path):
    app = _app(config)
    c, account_id = _account(app)
    _seed_push(app, account_id)
    assert c.post("/account/pending/purge").status_code == 303
    with app.config["_db"].connect() as conn:
        assert push_store.get_push_by_id(conn, "push-1") is None


def test_admin_can_purge_all_pushes(config, db_path):
    app = _app(config)
    with app.config["_db"].connect() as conn:
        push_store.create_push(conn, "p-admin", "dev")
        push_store.add_file(conn, file_id="fa", push_id="p-admin", file_name="a.pdf",
                            file_path="", size=1, retrieval_key="rk", stored_path=None,
                            created_at=1)
    admin = app.test_client()
    admin.post("/login", data={"username": "admin", "password": "testpass"})
    assert admin.post("/pushes/purge").status_code == 303
    with app.config["_db"].connect() as conn:
        assert push_store.get_push_by_id(conn, "p-admin") is None


def test_portal_pages_are_gated(config, db_path):
    app = _app(config)
    for path in ("/account/devices", "/account/clients", "/account/profile",
                 "/account/pushes", "/account/pending"):
        resp = app.test_client().get(path)
        assert resp.status_code == 303 and resp.headers["Location"] == "/account/login"
