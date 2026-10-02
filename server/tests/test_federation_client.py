# server/tests/test_federation_client.py
"""Slave-side federation client (outbound) against a fake master."""
import base64
import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from server.app import accounts as accounts_mod
from server.app import crypto, federation, federation_client, pairing, push_store
from server.app.db import Database
from server.app.deployment import get_or_create_identity
from server.app.retry import RetryWorker


class _FakeMaster(BaseHTTPRequestHandler):
    state = {}

    def log_message(self, *a):  # silence
        pass

    def _json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.state["user_agent"] = self.headers.get("User-Agent")
        if self.path == "/api/federation/enrol":
            payload = json.loads(body)
            self.state["public_key"] = payload["public_key"]
            self.state["enrolled"] = payload
            self._json({"server_id": payload["server_id"],
                        "server_secret": "s3cr3t", "status": "active"})
        elif self.path == "/api/federation/heartbeat":
            auth = self.headers.get("Authorization", "")
            pub = self.state.get("public_key")
            ts = self.headers.get("X-Federation-Timestamp")
            nonce = self.headers.get("X-Federation-Nonce")
            sig = self.headers.get("X-Federation-Signature")
            ok = False
            if auth.startswith("Bearer ") and pub and ts and nonce and sig:
                key = crypto.public_from_spki_der(base64.b64decode(pub))
                canon = federation.canonical_request("POST", self.path, ts, nonce, body)
                ok = crypto.verify(key, canon, base64.b64decode(sig))
            self.state["heartbeat_ok"] = ok
            if ok:
                self._json({"ok": True, "queued": [{"type": "notice"}]})
            else:
                self._json({"error": "bad signature"}, status=401)
        elif self.path.startswith("/api/federation/accounts/"):
            if not self._signed_ok(body):
                self._json({"error": "bad signature"}, status=401)
                return
            self.state.setdefault("synced", []).append((self.command, self.path, body))
            self._json({"ok": True})
        elif self.path == "/api/federation/doorbell":
            if not self._signed_ok(body):
                self._json({"error": "bad signature"}, status=401)
                return
            self.state["doorbell"] = json.loads(body)
            self._json({"ok": True})
        elif self.path == "/api/federation/disconnect":
            if not self._signed_ok(body):
                self._json({"error": "bad signature"}, status=401)
                return
            self.state["disconnected"] = True
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, status=404)

    def do_GET(self):
        if self.path == "/api/federation/whoami":
            if not self._signed_ok(b""):
                self._json({"error": "bad signature"}, status=401)
                return
            self.state["whoami_ok"] = True
            self._json({"server_id": "srv-fake", "hostname": "fake.example",
                        "status": "active",
                        "plan": {"id": "pl-1", "name": "Host",
                                 "price_cents": 2000, "included_messages": 10000,
                                 "message_cents_per_1000": 1,
                                 "max_messages_per_month": 0,
                                 "active": True, "is_default": False}})
        else:
            self._json({"error": "not found"}, status=404)

    do_PUT = do_POST
    do_DELETE = do_POST

    def _signed_ok(self, body):
        auth = self.headers.get("Authorization", "")
        pub = self.state.get("public_key")
        ts = self.headers.get("X-Federation-Timestamp")
        nonce = self.headers.get("X-Federation-Nonce")
        sig = self.headers.get("X-Federation-Signature")
        if not (auth.startswith("Bearer ") and pub and ts and nonce and sig):
            return False
        key = crypto.public_from_spki_der(base64.b64decode(pub))
        canon = federation.canonical_request(self.command, self.path, ts, nonce, body)
        return crypto.verify(key, canon, base64.b64decode(sig))


@pytest.fixture()
def fake_master():
    _FakeMaster.state = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeMaster)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://{host}:{port}", _FakeMaster.state
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_slave_enrols_and_heartbeats(config, db_path, fake_master):
    master_url, state = fake_master
    config.MASTER_URL = master_url
    config.PUSH_PUBLIC_URL = "https://slave.example"

    db = Database(db_path)
    conn = db.connect()
    db.init_schema(conn)
    identity = get_or_create_identity(conn)

    reply = federation_client.enrol(config, conn, identity)
    assert reply["server_secret"] == "s3cr3t"
    assert state["enrolled"]["server_id"] == identity["server_id"]
    assert state["enrolled"]["base_url"] == "https://slave.example"
    assert federation_client.get_state(conn)["server_secret"] == "s3cr3t"
    # Cloudflare answers error 1010 to urllib's default Python-urllib UA, so
    # every federation call must present a real one.
    assert state["user_agent"] == federation.USER_AGENT

    hb = federation_client.send_heartbeat(config, conn, identity)
    assert state["heartbeat_ok"] is True
    assert hb == {"ok": True, "queued": [{"type": "notice"}]}
    assert federation_client.get_state(conn)["last_heartbeat"] is not None
    conn.close()


def _enrolled(config, db_path, master_url):
    config.MASTER_URL = master_url
    config.PUSH_PUBLIC_URL = "https://slave.example"
    db = Database(db_path)
    conn = db.connect()
    db.init_schema(conn)
    identity = get_or_create_identity(conn)
    federation_client.enrol(config, conn, identity)
    return conn, identity


def test_sync_device_signs_and_sends(config, db_path, fake_master):
    master_url, state = fake_master
    conn, identity = _enrolled(config, db_path, master_url)

    federation_client.sync_device(config, conn, identity, "acct-1", "dev-1", "tok-1")
    method, path, body = state["synced"][-1]
    assert method == "PUT"
    assert path == "/api/federation/accounts/acct-1/devices/dev-1"
    assert json.loads(body) == {"fcm_token": "tok-1"}
    assert state["user_agent"] == federation.USER_AGENT

    federation_client.sync_device(config, conn, identity, "acct-1", "dev-1", "",
                                  delete=True)
    assert state["synced"][-1][0] == "DELETE"
    conn.close()


def test_ring_via_master_syncs_the_tuple_then_posts_the_trigger(config, db_path,
                                                                fake_master):
    master_url, state = fake_master
    conn, identity = _enrolled(config, db_path, master_url)

    federation_client.ring_via_master(
        config, conn, identity,
        {"account_id": "acct-1", "device_secret": "dev-1", "fcm_token": "tok-1"},
        {"c": "CIPHER", "i": "IV"})
    # The routing tuple is republished first: registration-time sync can fail,
    # and a device paired before it existed has no row on the master at all.
    assert state["synced"][-1][:2] == ("PUT", "/api/federation/accounts/"
                                                 "acct-1/devices/dev-1")
    assert json.loads(state["synced"][-1][2]) == {"fcm_token": "tok-1"}
    assert state["doorbell"] == {"account_id": "acct-1", "device_id": "dev-1",
                                 "sealed": {"c": "CIPHER", "i": "IV"}}
    conn.close()


def test_ring_via_master_needs_an_account_bound_device(config, db_path, fake_master):
    master_url, _ = fake_master
    conn, identity = _enrolled(config, db_path, master_url)
    with pytest.raises(ValueError):
        federation_client.ring_via_master(config, conn, identity,
                                          {"device_secret": "dev-1"},
                                          {"c": "C", "i": "I"})
    conn.close()


def test_retry_worker_relays_a_slave_doorbell(config, db_path, fake_master):
    """A slave owns no FCM, so its retry worker must re-ring via the master.

    Without this branch a slave never re-rung, so a push whose first ring failed
    stayed pending until someone noticed (design §7B).
    """
    master_url, state = fake_master
    config.MASTER_URL = master_url
    config.PUSH_PUBLIC_URL = "https://slave.example"
    config.ROLE = "slave"

    db = Database(db_path)
    conn = db.connect()
    db.init_schema(conn)
    identity = get_or_create_identity(conn)
    federation_client.enrol(config, conn, identity)

    account_id = accounts_mod.create_account(conn, "user@example.com", "letmein99")
    _register_account_bound_device(conn, account_id)
    push_store.create_push(conn, "p1", "Sunny Falcon", challenge_key="ck-1")
    push_store.add_file(conn, file_id="f1", push_id="p1", file_name="a.md",
                        file_path="", size=3, retrieval_key="k1",
                        stored_path="p1/f1/a.md", created_at=time.time() - 3600)
    conn.close()

    touched = RetryWorker(config, db, fcm_client=None).tick(time.time())
    assert touched == ["f1"]
    assert state["doorbell"]["account_id"] == account_id
    assert state["doorbell"]["device_id"] == "sec-1"
    # The tuple reached the master too, so it could resolve the FCM token.
    assert state["synced"][-1][:2] == ("PUT", f"/api/federation/accounts/"
                                              f"{account_id}/devices/sec-1")


def _register_account_bound_device(conn, account_id, *, secret="sec-1",
                                   name="Sunny Falcon"):
    """Register through the real pairing path so the test cannot drift from it."""
    priv, pub = crypto.generate_rsa_keypair()
    pub_b64 = base64.b64encode(crypto.public_to_spki_der(pub)).decode()
    push_key_b64 = base64.b64encode(os.urandom(32)).decode()
    fcm_token = "tok-1"
    token = pairing.create_pairing_token(conn, 15, account_id=account_id)
    digest = hashlib.sha256(
        f"{secret}{name}{fcm_token}{pub_b64}{push_key_b64}".encode()).digest()
    sig = base64.b64encode(crypto.sign(priv, digest)).decode()
    device_auth, _ = pairing.register_device(
        conn, device_secret=secret, device_name=name, fcm_token=fcm_token,
        public_key_b64=pub_b64, push_key_b64=push_key_b64, pairing_token=token,
        sig_b64=sig)
    assert device_auth


def test_heartbeat_without_registration_raises(config, db_path):
    db = Database(db_path)
    conn = db.connect()
    db.init_schema(conn)
    identity = get_or_create_identity(conn)
    with pytest.raises(ValueError):
        federation_client.send_heartbeat(config, conn, identity)
    conn.close()


def test_fetch_plan_is_signed_and_returns_the_plan(config, db_path, fake_master):
    """The slave reads its billing plan from the master over the signed link."""
    master_url, state = fake_master
    conn, identity = _enrolled(config, db_path, master_url)

    reply = federation_client.fetch_plan(config, conn, identity)
    assert reply["plan"]["name"] == "Host"
    assert reply["plan"]["price_cents"] == 2000
    assert state["whoami_ok"] is True
    conn.close()


def test_disconnect_notifies_the_master_then_clears(config, db_path, fake_master):
    master_url, state = fake_master
    conn, identity = _enrolled(config, db_path, master_url)

    assert federation_client.disconnect(config, conn, identity) is True
    assert state.get("disconnected") is True
    assert federation_client.get_state(conn) is None
    # A second call has nothing to disconnect.
    assert federation_client.disconnect(config, conn, identity) is False
    conn.close()


def test_disconnect_clears_locally_even_when_the_master_is_down(config, db_path):
    """The operator button must never strand them on an unreachable master."""
    db = Database(db_path)
    conn = db.connect()
    db.init_schema(conn)
    identity = get_or_create_identity(conn)
    federation_client.save_registration(conn, "http://127.0.0.1:9", "s3cr3t")

    assert federation_client.disconnect(config, conn, identity) is False
    assert federation_client.get_state(conn) is None
    conn.close()
