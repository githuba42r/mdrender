# tools/localsend-send/test_localsend_send.py
"""Tests for the cloud-push enrolment path of localsend-send.py (Task C2).

Spins up a tiny stdlib HTTP server that mimics the cloud-push server's three
endpoints (/api/enrol/start, /api/enrol, /oauth/token) on a free port, and
exercises the pure functions against it. Credentials always go to tmp_path;
$HOME/.config is never touched.

The mock mirrors the real server's enrolment semantics: every /api/enrol/start
mints a FRESH enrolment_id + key and stores {eid: key} server-side, and
/api/enrol validates the submitted key against the submitted enrolment_id
(401 on mismatch). This is what keeps the "exactly one /api/enrol/start per
flow" invariant honest.
"""
import argparse
import importlib.util
import json
import os
import threading
import unittest.mock
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

# localsend-send.py contains a hyphen so it cannot be imported by name; load it
# by path instead. The module-level __main__ guard keeps main() from running.
_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "localsend-send.py")
_spec = importlib.util.spec_from_file_location("localsend_send", _SCRIPT)
ls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ls)

CLIENT_ID = "client123"
CLIENT_SECRET = "secret123"
ACCESS_TOKEN = "tok123"


class _Handler(BaseHTTPRequestHandler):
    server_version = "CloudPushTest"

    def __init__(self, *args, state=None, **kwargs):
        self.state = state if state is not None else {}
        super().__init__(*args, **kwargs)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if self.path == "/api/enrol/start":
            payload = json.loads(body)
            assert payload.get("info"), "enrol/start should carry client info"
            # Mint a FRESH id + key on EVERY call, exactly like the real
            # server (server/app/app.py), and store {eid: key} server-side.
            self.state["enrol_start_hits"] = self.state.get("enrol_start_hits", 0) + 1
            self.state["enrol_seq"] = self.state.get("enrol_seq", 0) + 1
            eid = f"eid{self.state['enrol_seq']}"
            key = f"key{self.state['enrol_seq']}"
            self.state.setdefault("enrol_keys", {})[eid] = key
            self.state["last_enrol_id"] = eid
            self._json({
                "enrolment_id": eid,
                "verification_uri":
                    f"http://{self.headers.get('Host', 'localhost')}/enrol/{eid}",
            })
        elif self.path == "/api/enrol":
            assert self.headers.get("Content-Type", "").startswith("application/json")
            payload = json.loads(body)
            eid = payload.get("enrolment_id")
            expected = self.state.get("enrol_keys", {}).get(eid)
            if expected is not None and payload.get("key") == expected:
                self._json({"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
            else:
                self._json({"error": "invalid enrolment key"}, status=401)
        elif self.path == "/oauth/token":
            self.state["oauth_hits"] = self.state.get("oauth_hits", 0) + 1
            assert self.headers.get("Content-Type", "").startswith(
                "application/x-www-form-urlencoded")
            form = urllib.parse.parse_qs(body.decode())
            if (form.get("grant_type", [""])[0] == "client_credentials"
                    and form.get("client_id", [""])[0] == CLIENT_ID
                    and form.get("client_secret", [""])[0] == CLIENT_SECRET):
                self._json({"access_token": ACCESS_TOKEN})
            else:
                self._json({"error": "invalid_grant"}, status=401)
        else:
            self._json({"error": "not found"}, status=404)

    def _json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # silence per-request logging
        pass


@pytest.fixture()
def cloud_server():
    """Yield (server_url, state) for a mock cloud-push server on a free port."""
    state = {"enrol_start_hits": 0, "enrol_seq": 0, "enrol_keys": {}, "oauth_hits": 0}
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        lambda *args, **kwargs: _Handler(*args, state=state, **kwargs))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_start_enrolment_mints_fresh_eid(cloud_server):
    server_url, state = cloud_server
    eid, uri = ls._start_enrolment(server_url)
    # The eid is exactly the one the mock issued and stored server-side.
    assert eid in state["enrol_keys"]
    assert eid in uri
    assert state["enrol_start_hits"] == 1

    # A second start mints a DIFFERENT id + key (the real server never reuses).
    eid2, uri2 = ls._start_enrolment(server_url)
    assert eid2 != eid
    assert state["enrol_keys"][eid2] != state["enrol_keys"][eid]
    assert uri2 != uri
    assert state["enrol_start_hits"] == 2


def test_enrol_flow_writes_credentials(tmp_path, cloud_server):
    server_url, state = cloud_server
    creds_path = tmp_path / "push-credentials.json"

    # Use an eid + key from a REAL prior start so the key matches.
    eid, _ = ls._start_enrolment(server_url)
    key = state["enrol_keys"][eid]

    creds = ls._enrol_flow(server_url, eid, key, str(creds_path))

    assert creds == {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "server_url": server_url,
    }
    assert json.loads(creds_path.read_text()) == creds
    assert creds_path.stat().st_mode & 0o777 == 0o600
    # _enrol_flow must NOT call /api/enrol/start itself.
    assert state["enrol_start_hits"] == 1


def test_enrol_flow_wrong_key_rejected(tmp_path, cloud_server):
    server_url, state = cloud_server
    creds_path = tmp_path / "creds.json"

    eid, _ = ls._start_enrolment(server_url)

    with pytest.raises(urllib.error.HTTPError) as exc:
        ls._enrol_flow(server_url, eid, "not-the-key", str(creds_path))
    assert exc.value.code == 401
    assert not creds_path.exists()


def test_cmd_enrol_single_start(tmp_path, cloud_server, monkeypatch):
    """cmd_enrol performs exactly ONE /api/enrol/start and enrols successfully.

    Regression test for the double-start defect: cmd_enrol used to call
    /api/enrol/start itself and _enrol_flow started AGAIN, minting a second
    enrolment_id whose key never matched the operator's — a guaranteed 401 in
    production. The mock mints a fresh id+key per start and validates key-vs-id
    strictly, so a double start fails this test.
    """
    server_url, state = cloud_server
    creds_path = tmp_path / "push-credentials.json"
    hits_before = state["enrol_start_hits"]

    # The key is minted server-side by the single start cmd_enrol performs, so
    # it cannot be known up front; patch input() with a function that reads back
    # the key the mock just issued for that id.
    def fake_input(_prompt):
        return state["enrol_keys"][state["last_enrol_id"]]

    monkeypatch.delenv("DISPLAY", raising=False)  # keep xdg-open out of the test
    with unittest.mock.patch("builtins.input", side_effect=fake_input):
        rc = ls.cmd_enrol(
            argparse.Namespace(server=server_url, creds=str(creds_path)))

    assert rc == 0
    assert state["enrol_start_hits"] == hits_before + 1  # exactly one start
    assert json.loads(creds_path.read_text()) == {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "server_url": server_url,
    }
    assert creds_path.stat().st_mode & 0o777 == 0o600


def test_get_token_hits_oauth_endpoint(cloud_server):
    server_url, state = cloud_server
    tok = ls._get_token({"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
                        server_url)
    assert tok == ACCESS_TOKEN
    assert state["oauth_hits"] == 1


def test_get_access_token_from_file(tmp_path, cloud_server):
    server_url, _ = cloud_server
    creds_path = tmp_path / "creds.json"
    creds_path.write_text(json.dumps({
        "server_url": server_url,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }))
    # server_url passed explicitly
    assert ls.get_access_token(str(creds_path), server_url) == ACCESS_TOKEN
    # server_url read from the credentials file when not passed
    assert ls.get_access_token(str(creds_path)) == ACCESS_TOKEN
