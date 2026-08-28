# tools/localsend-send/test_localsend_send.py
"""Tests for the cloud-push enrolment path of localsend-send.py (Task C2).

Spins up a tiny stdlib HTTP server that mimics the cloud-push server's three
endpoints (/api/enrol/start, /api/enrol, /oauth/token) on a free port, and
exercises the pure functions against it. Credentials always go to tmp_path;
$HOME/.config is never touched.
"""
import importlib.util
import json
import os
import threading
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

ENROL_ID = "eid123"
ENROL_KEY = "key123"
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
            self._json({
                "enrolment_id": ENROL_ID,
                "verification_uri":
                    f"http://{self.headers.get('Host', 'localhost')}/enrol/{ENROL_ID}",
            })
        elif self.path == "/api/enrol":
            assert self.headers.get("Content-Type", "").startswith("application/json")
            payload = json.loads(body)
            if payload.get("enrolment_id") == ENROL_ID and payload.get("key") == ENROL_KEY:
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
    state = {"oauth_hits": 0}
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


def test_enrol_flow_writes_credentials(tmp_path, cloud_server):
    server_url, _ = cloud_server
    creds_path = tmp_path / "push-credentials.json"

    creds = ls._enrol_flow(server_url, ENROL_KEY, str(creds_path))

    assert creds == {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "server_url": server_url,
    }
    assert json.loads(creds_path.read_text()) == creds
    assert creds_path.stat().st_mode & 0o777 == 0o600


def test_enrol_flow_wrong_key_rejected(tmp_path, cloud_server):
    server_url, _ = cloud_server
    creds_path = tmp_path / "creds.json"
    with pytest.raises(urllib.error.HTTPError):
        ls._enrol_flow(server_url, "not-the-key", str(creds_path))
    assert not creds_path.exists()


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
