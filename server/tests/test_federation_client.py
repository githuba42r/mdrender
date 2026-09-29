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
        else:
            self._json({"error": "not found"}, status=404)


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


def test_heartbeat_without_registration_raises(config, db_path):
    db = Database(db_path)
    conn = db.connect()
    db.init_schema(conn)
    identity = get_or_create_identity(conn)
    with pytest.raises(ValueError):
        federation_client.send_heartbeat(config, conn, identity)
    conn.close()
