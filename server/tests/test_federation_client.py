# server/tests/test_federation_client.py
"""Slave-side federation client (outbound) against a fake master."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from server.app import crypto, federation, federation_client
from server.app.db import Database
from server.app.deployment import get_or_create_identity


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

    federation_client.sync_device(config, conn, identity, "acct-1", "dev-1", "",
                                  delete=True)
    assert state["synced"][-1][0] == "DELETE"
    conn.close()


def test_ring_via_master_posts_sealed_trigger(config, db_path, fake_master):
    master_url, state = fake_master
    conn, identity = _enrolled(config, db_path, master_url)

    federation_client.ring_via_master(config, conn, identity, "acct-1", "dev-1",
                                      {"c": "CIPHER", "i": "IV"})
    assert state["doorbell"] == {"account_id": "acct-1", "device_id": "dev-1",
                                 "sealed": {"c": "CIPHER", "i": "IV"}}
    conn.close()


def test_heartbeat_without_registration_raises(config, db_path):
    db = Database(db_path)
    conn = db.connect()
    db.init_schema(conn)
    identity = get_or_create_identity(conn)
    with pytest.raises(ValueError):
        federation_client.send_heartbeat(config, conn, identity)
    conn.close()
